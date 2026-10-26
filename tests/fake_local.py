"""Sessão do ONNX e tokenizer falsos do embedder local da F5.1 (A-13: nenhum teste importa o onnxruntime nem o tokenizers
reais no processo da suíte) e a pasta de modelos de mentira com a marca `.ready`.

`FakeTokenizer` faz o que o tokenizer real faz para o projeto: um id por palavra (pelo hash), os tokens especiais, corte em
`max_tokens` com `overflowing` e enchimento até o maior texto do lote com a máscara em zero. `FakeSession` devolve, para
cada id, uma linha fixa de uma tabela aleatória (o "último estado oculto"): o pooling médio pela máscara fica conferível à
mão e o enchimento, se vazasse, mudaria o vetor."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from listener.llm import local_embed

PAD, BOS, EOS = 1, 0, 2


@dataclass
class FakeEncoding:
    ids: list[int]
    attention_mask: list[int]
    overflowing: list[Any] = field(default_factory=list)


class FakeTokenizer:
    def __init__(self, max_tokens: int = local_embed.MAX_TOKENS, vocab: int = 997):
        self.max_tokens = max_tokens
        self.vocab = vocab
        self.batches: list[list[str]] = []

    def token(self, word: str) -> int:
        return 3 + int.from_bytes(hashlib.blake2b(word.encode("utf-8"), digest_size=4).digest(), "little") % (self.vocab - 3)

    def ids(self, text: str) -> tuple[list[int], bool]:
        ids = [BOS, *(self.token(w) for w in text.split()), EOS]
        if len(ids) > self.max_tokens:
            return [*ids[:self.max_tokens - 1], EOS], True
        return ids, False

    def encode_batch(self, texts: list[str]) -> list[FakeEncoding]:
        self.batches.append(list(texts))
        rows = [self.ids(t) for t in texts]
        width = max((len(ids) for ids, _cut in rows), default=0)
        return [FakeEncoding(ids + [PAD] * (width - len(ids)), [1] * len(ids) + [0] * (width - len(ids)), [object()] if cut else [])
                for ids, cut in rows]


@dataclass
class _Node:
    name: str


class FakeSession:
    def __init__(self, dim: int = local_embed.DIM, vocab: int = 997, inputs: tuple[str, ...] = ("input_ids", "attention_mask",
                                                                                               "token_type_ids"), seed: int = 5):
        self.table = np.random.default_rng(seed).standard_normal((vocab, dim)).astype(np.float32)
        self.inputs = inputs
        self.feeds: list[dict[str, Any]] = []
        self.output_names: list[Any] = []

    def get_inputs(self) -> list[_Node]:
        return [_Node(n) for n in self.inputs]

    def get_outputs(self) -> list[_Node]:
        return [_Node(local_embed.OUTPUT)]

    def run(self, names: Any, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.feeds.append(dict(feed))
        self.output_names.append(names)
        return [self.table[feed["input_ids"]]]


class Factories:
    """As fábricas que a `LocalSession` recebe: guardam o que pediram (caminho e threads) e a sessão e o tokenizer criados."""

    def __init__(self, fail_session: BaseException | None = None, fail_tokenizer: BaseException | None = None):
        self.sessions: list[tuple[Path, int]] = []
        self.tokenizers: list[Path] = []
        self.session: FakeSession | None = None
        self.tokenizer: FakeTokenizer | None = None
        self.fail_session = fail_session
        self.fail_tokenizer = fail_tokenizer

    def make_session(self, path: Path, threads: int) -> FakeSession:
        self.sessions.append((path, threads))
        if self.fail_session is not None:
            raise self.fail_session
        self.session = FakeSession()
        return self.session

    def make_tokenizer(self, path: Path) -> FakeTokenizer:
        self.tokenizers.append(path)
        if self.fail_tokenizer is not None:
            raise self.fail_tokenizer
        self.tokenizer = FakeTokenizer()
        return self.tokenizer

    def local_session(self, **kw: Any) -> local_embed.LocalSession:
        return local_embed.LocalSession(self.make_session, self.make_tokenizer, **kw)


def ready_folder(models_dir: Path) -> Path:
    """A pasta do e5 com os arquivos (vazios: a sessão é falsa) e a marca desta revisão."""
    folder = local_embed.model_dir(str(models_dir))
    for name in local_embed.FILES:
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"")
    local_embed.write_ready(folder)
    return folder


def expected_vectors(session: FakeSession, tokenizer: FakeTokenizer, texts: list[str]) -> np.ndarray:
    """O pooling médio e o L2 feitos à mão, texto a texto (sem enchimento nenhum)."""
    out = []
    for t in texts:
        ids, _cut = tokenizer.ids(t)
        v = session.table[ids].mean(axis=0)
        out.append(v / np.linalg.norm(v))
    return np.asarray(out, np.float32)
