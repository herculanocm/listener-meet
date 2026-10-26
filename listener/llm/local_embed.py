"""Embedder local da F5.1 (D-021, D-051; A-02 a A-04, A-09, A-10): `intfloat/multilingual-e5-small` int8 em ONNX, na CPU,
sem chave, sem rede e sem custo (CP2, CP14). Repositório, revisão, arquivos e sha256 fixados aqui; os arquivos ficam em
`<pasta de modelos>/multilingual-e5-small/` (a do `rag_probe e5`) com a marca `.ready` (JSON com repositório, revisão e
sha256), gravada pelo preparo (`--live --prepare --what rag`, `llm/local_prepare.py`) depois de conferir cada arquivo.

Pronto = o extra `local` instalado (`find_spec`, sem importar; uma vez por processo) e a marca desta revisão com os arquivos
no lugar. Prefixos `query:` e `passage:`, corte em 512 tokens (os cortes vão para o log), pooling médio com a máscara e L2
(o `e5_embed` do `rag_probe`), 384 dimensões, lotes de 8 (A-14: o de 32 da A-04 passou de 500 MB no worker leve; medido
no Windows), `input_tokens` pela máscara.

A sessão do ONNX e o tokenizer são um objeto só por processo (`LocalSession`, A-03), sob um lock: o cálculo roda na thread
de quem chama (sem `to_thread`); intra-op = `configure(threads)` (2 no worker leve, 1 no app), inter-op = 1, sem spinning,
só `CPUExecutionProvider`, `TOKENIZERS_PARALLELISM=false`. Fechar o provedor não solta a sessão; `release_if_idle` solta
(o Histórico, depois de 10 min sem uso, A-11). Erro de carga ou de execução vira `LocalEmbedError` (um
`ProviderUnavailableError`) com texto sem jargão (nunca "sem rede", A-10); o detalhe técnico fica no log, e o índice marca a
reunião para a varredura tentar de novo (R10-04).

`onnxruntime` e `tokenizers` só dentro de função (A-13): os testes trocam as fábricas da sessão e do tokenizer
(`LocalSession(session_factory=..., tokenizer_factory=...)`)."""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import numpy as np

from ..log import get_logger
from ..platform.common import paths
from .base import LlmStream, ProviderUnavailableError
from .types import Capability, Completion, ConnectionTest, Embeddings, EmbedTask, ModelInfo, ProviderKind, Request, Usage

log = get_logger("llm.local")

REPO = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"          # fixada (D-051): o preparo baixa sempre esta
MODEL = "multilingual-e5-small"                                  # o modelo da chave `local/multilingual-e5-small@384`
DIM = 384
ONNX_FILE = "onnx/model_qint8_avx512_vnni.onnx"                  # int8: cosseno 0,995 com o fp32 nesta CPU (D-051)
TOKENIZER_FILE = "onnx/tokenizer.json"
FILES: dict[str, str] = {ONNX_FILE: "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88",
                         TOKENIZER_FILE: "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"}
SIZES: dict[str, int] = {ONNX_FILE: 118_346_824, TOKENIZER_FILE: 17_082_730}      # o progresso pesa pelo tamanho [fato]
LABELS: dict[str, str] = {ONNX_FILE: "modelo", TOKENIZER_FILE: "vocabulário"}
SIZE_MB = 135
READY_MARK = ".ready"
MAX_TOKENS = 512
# A-14 (Windows, 26/10, 2 threads, trechos de ~320 tokens): pico de 895 MB com lotes de 32, 872 com 16 e 683 com 8 numa
# sonda só com o e5 (o tokenizer, ~260 MB, e a sessão, ~140 MB, já dão ~455 MB fixos); o worker leve de verdade com 8,
# 725 MB. Acima de ~500 MB a A-14 pede 8 a 16: 8, o que mais economiza (16 não muda o pico da arena)
BATCH = 8
OUTPUT = "last_hidden_state"
PREFIX: dict[EmbedTask, str] = {EmbedTask.DOCUMENT: "passage: ", EmbedTask.QUERY: "query: "}
EXTRA_MODULES = ("onnxruntime", "tokenizers")
NOT_DOWNLOADED = "busca local não baixada: prepare em Configurações (Busca)"
NO_EXTRA = "instale o extra local da busca (pip install -e .[local])"
LOAD_FAILED = "a busca local não abriu o modelo: prepare de novo em Configurações (Busca)"
RUN_FAILED = "a busca local falhou ao calcular os vetores (detalhes no log)"
TEXT_ONLY = "o provedor local só calcula os vetores da busca por sentido: escolha outro provedor para texto"
MODEL_INFO = ModelInfo(MODEL, "e5-small (local, sem custo)", MAX_TOKENS, 0, frozenset({Capability.EMBEDDINGS}))

type SessionFactory = Callable[[Path, int], Any]
type TokenizerFactory = Callable[[Path], Any]


class LocalEmbedError(ProviderUnavailableError):
    """A sessão não abriu (`LOAD_FAILED`) ou o cálculo falhou (`RUN_FAILED`, como memória): sem retentativa na hora (é
    permanente para o `RetryingProvider`), mas o índice marca a reunião e a varredura tenta de novo uma vez (R10-04)."""


# ---------------------------------------------------------------- pasta e estado
def model_dir(models_dir_setting: str = "") -> Path:
    """`<pasta de modelos>/multilingual-e5-small/`: a pasta da F2 (`pipeline.models_dir`; vazio = `<dados do sistema>/models`)."""
    root = Path(models_dir_setting).expanduser() if models_dir_setting else paths.data_dir_suggestion() / "models"
    return root / MODEL


@cache
def extra_installed() -> bool:
    """O extra `local` (`onnxruntime` e `tokenizers`) instalado, sem importar nada (`find_spec`); uma vez por processo."""
    try:
        return all(find_spec(name) is not None for name in EXTRA_MODULES)
    except (ImportError, ValueError):
        return False


def ready_payload() -> dict[str, Any]:
    return {"repo": REPO, "revision": REVISION, "files": dict(FILES)}


def is_ready(folder: Path) -> bool:
    """A marca `.ready` desta revisão e destes arquivos, com os arquivos no lugar. O sha256 foi conferido pelo preparo, uma
    vez; aqui só se lê a marca (o estado é lido a cada busca)."""
    try:
        data = json.loads((folder / READY_MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    want = ready_payload()
    return (isinstance(data, dict) and all(data.get(k) == v for k, v in want.items())
            and all((folder / name).is_file() for name in want["files"]))


def status(models_dir_setting: str = "") -> tuple[bool, str]:
    """(pronto, motivo sem jargão) do embedder local (F5.1 item 2): o extra instalado e a marca desta revisão."""
    if not extra_installed():
        return False, NO_EXTRA
    if not is_ready(model_dir(models_dir_setting)):
        return False, NOT_DOWNLOADED
    return True, ""


# ---------------------------------------------------------------- sessão (A-03)
def onnx_session(path: Path, threads: int) -> Any:
    """A sessão real: intra-op = `threads`, inter-op = 1, sequencial, sem spinning, só CPU."""
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.intra_op_num_threads = max(1, int(threads))
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    so.add_session_config_entry("session.inter_op.allow_spinning", "0")
    so.log_severity_level = 3
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


def hf_tokenizer(path: Path) -> Any:
    """O tokenizer real: corte em 512 tokens e enchimento até o maior texto do lote, sem as threads próprias dele."""
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(path))
    tok.enable_truncation(max_length=MAX_TOKENS)
    pad = tok.token_to_id("<pad>")
    tok.enable_padding(pad_id=1 if pad is None else pad, pad_token="<pad>")  # noqa: S106 (o token de enchimento, não senha)
    return tok


def mean_pool(hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Pooling médio pela máscara e L2: (n, tokens, d) com a máscara (n, tokens) -> (n, d) unitário."""
    m = np.asarray(mask, np.float32)
    summed = np.einsum("ntd,nt->nd", np.asarray(hidden, np.float32), m)
    vec = summed / np.maximum(m.sum(axis=1, keepdims=True), 1.0)
    norms = np.linalg.norm(vec, axis=1, keepdims=True)
    return np.asarray(vec / np.where(norms > 0, norms, 1.0), np.float32)


@dataclass(slots=True)
class _Loaded:
    folder: Path
    threads: int
    session: Any
    tokenizer: Any
    inputs: frozenset[str]
    output: str | None
    last_used: float


def _run(state: _Loaded, texts: list[str]) -> tuple[np.ndarray, int, int]:
    """Um lote: (vetores unitários, tokens pela máscara, textos cortados em 512 tokens)."""
    encs = state.tokenizer.encode_batch(texts)
    cut = sum(1 for e in encs if e.overflowing)
    ids = np.asarray([e.ids for e in encs], dtype=np.int64)
    mask = np.asarray([e.attention_mask for e in encs], dtype=np.int64)
    feed = {"input_ids": ids, "attention_mask": mask}
    if "token_type_ids" in state.inputs:
        feed["token_type_ids"] = np.zeros_like(ids)
    hidden = state.session.run([state.output] if state.output else None, feed)[0]
    return mean_pool(hidden, mask), int(mask.sum()), cut


class LocalSession:
    """A sessão do ONNX e o tokenizer do processo, abertos no primeiro uso (ou na pré-carga do Histórico, A-11) e reabertos
    quando a pasta ou as threads mudam. Tudo sob um lock: duas buscas ao mesmo tempo esperam a vez. `release_if_idle` não
    espera o lock (com um cálculo em curso, não está ociosa)."""

    def __init__(self, session_factory: SessionFactory | None = None, tokenizer_factory: TokenizerFactory | None = None, *,
                 clock: Callable[[], float] = time.monotonic, batch: int = BATCH):
        self.session_factory: SessionFactory = session_factory or onnx_session
        self.tokenizer_factory: TokenizerFactory = tokenizer_factory or hf_tokenizer
        self.clock = clock
        self.batch = max(1, int(batch))
        self.threads = 1
        self.loads = 0                       # instrumentação: sessões abertas neste processo
        self.cuts = 0                        # textos cortados em 512 tokens neste processo
        self.last_load_ms = 0.0
        self._state: _Loaded | None = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._state is not None

    def configure(self, threads: int) -> None:
        """Threads do intra-op (2 no worker leve, 1 no app); a sessão aberta com outro número reabre no próximo uso."""
        with self._lock:
            self.threads = max(1, int(threads))
            if self._state is not None and self._state.threads != self.threads:
                self._state = None

    def preload(self, folder: Path) -> float:
        """Abre a sessão já (A-11); devolve os ms da carga, ou 0 quando já estava aberta."""
        with self._lock:
            before = self.loads
            self._ensure(folder)
            return self.last_load_ms if self.loads != before else 0.0

    def release(self) -> bool:
        with self._lock:
            had, self._state = self._state is not None, None
        return had

    def release_if_idle(self, idle_s: float) -> bool:
        """Solta a sessão parada há `idle_s` ou mais (A-11); False quando não há sessão, está em uso ou ainda é cedo."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            state = self._state
            if state is None or self.clock() - state.last_used < idle_s:
                return False
            self._state = None
        finally:
            self._lock.release()
        log.info("busca local: sessão solta depois de %.0f min sem uso", idle_s / 60)
        return True

    def embed(self, folder: Path, texts: Sequence[str], task: EmbedTask) -> tuple[np.ndarray, int]:
        """Vetores unitários (n, 384) e os tokens de entrada (pela máscara), em lotes de `batch`, na thread de quem chama."""
        prefixed = [PREFIX[task] + str(t) for t in texts]
        parts: list[np.ndarray] = []
        tokens = cuts = 0
        with self._lock:
            state = self._ensure(folder)
            try:
                for i in range(0, len(prefixed), self.batch):
                    vecs, n_tokens, n_cut = _run(state, prefixed[i:i + self.batch])
                    parts.append(vecs)
                    tokens += n_tokens
                    cuts += n_cut
            except Exception as e:  # ONNX, tokenizer, memória: o detalhe técnico fica no log, o texto é sem jargão (A-10)
                log.warning("busca local: falhou ao calcular %d vetor(es): %s: %s", len(prefixed), type(e).__name__, e)
                raise LocalEmbedError(RUN_FAILED) from e
            state.last_used = self.clock()
            self.cuts += cuts
        if cuts:
            log.info("busca local: %d de %d texto(s) cortado(s) em %d tokens", cuts, len(prefixed), MAX_TOKENS)
        return (np.concatenate(parts) if parts else np.zeros((0, DIM), np.float32)), tokens

    def _ensure(self, folder: Path) -> _Loaded:
        """A sessão aberta para a pasta e as threads atuais (chamar com o lock)."""
        state = self._state
        if state is not None and state.folder == folder and state.threads == self.threads:
            return state
        self._state = None
        if not extra_installed():
            raise ProviderUnavailableError(NO_EXTRA)
        if not is_ready(folder):
            raise ProviderUnavailableError(NOT_DOWNLOADED)
        t0 = time.perf_counter()
        try:
            tokenizer = self.tokenizer_factory(folder / TOKENIZER_FILE)
            session = self.session_factory(folder / ONNX_FILE, self.threads)
            inputs = frozenset(str(i.name) for i in session.get_inputs())
            outputs = [str(o.name) for o in session.get_outputs()]
        except ImportError as e:
            log.warning("busca local: extra ausente: %s", e)
            raise ProviderUnavailableError(NO_EXTRA) from e
        except Exception as e:  # arquivo corrompido, DLL do ONNX, memória: o texto para o usuário não tem jargão (A-10)
            log.warning("busca local: não abri o modelo em %s: %s: %s", folder, type(e).__name__, e)
            raise LocalEmbedError(LOAD_FAILED) from e
        self.last_load_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.loads += 1
        state = self._state = _Loaded(folder, self.threads, session, tokenizer, inputs, OUTPUT if OUTPUT in outputs else None,
                                      self.clock())
        log.info("busca local: sessão aberta em %.0f ms (%d thread(s)) em %s", self.last_load_ms, self.threads, folder)
        return state


_SESSION = LocalSession()


def shared() -> LocalSession:
    """A sessão do processo (os testes trocam `_SESSION` por uma com fábricas falsas)."""
    return _SESSION


def configure(threads: int) -> None:
    shared().configure(threads)


def preload(folder: Path) -> float:
    return shared().preload(folder)


def release_if_idle(idle_s: float) -> bool:
    return shared().release_if_idle(idle_s)


def loaded() -> bool:
    return shared().loaded


# ---------------------------------------------------------------- provedor (A-02)
class LocalEmbedProvider:
    """O adaptador do provedor `local`: só `EMBEDDINGS`, sem chave nem SSL; a sessão é a do processo (A-03)."""

    kind = ProviderKind.LOCAL

    def __init__(self, *, provider_id: str = "local", models_dir: str = "", session: LocalSession | None = None):
        self.id = provider_id
        self.capabilities: frozenset[Capability] = frozenset({Capability.EMBEDDINGS})
        self.folder = model_dir(models_dir)
        self._session = session

    @property
    def session(self) -> LocalSession:
        return self._session or shared()

    async def complete(self, req: Request) -> Completion:
        del req
        raise ProviderUnavailableError(TEXT_ONLY)

    def stream(self, req: Request) -> LlmStream:
        del req
        raise ProviderUnavailableError(TEXT_ONLY)

    async def embed(self, texts: Sequence[str], *, model: str, dim: int | None, task: EmbedTask) -> Embeddings:
        del model                                                # um modelo só (sem `rag.local_model`)
        if dim not in (None, DIM):
            raise ProviderUnavailableError(f"a busca local tem {DIM} dimensões, não {dim}")
        vectors, tokens = self.session.embed(self.folder, list(texts), task)
        return Embeddings(MODEL, DIM, vectors, Usage(tokens, 0))

    async def count_tokens(self, req: Request) -> int | None:
        del req
        return None

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (MODEL_INFO,)

    async def test_connection(self, model: str) -> ConnectionTest:
        del model
        t0 = time.perf_counter()
        try:
            await self.embed(["teste"], model=MODEL, dim=DIM, task=EmbedTask.QUERY)
        except ProviderUnavailableError as e:
            return ConnectionTest(False, int((time.perf_counter() - t0) * 1000), MODEL, str(e))
        return ConnectionTest(True, int((time.perf_counter() - t0) * 1000), MODEL)

    async def aclose(self) -> None:
        """A sessão é do processo: fechar o provedor não a solta (A-03)."""


__all__ = ["BATCH", "DIM", "FILES", "MODEL", "NOT_DOWNLOADED", "NO_EXTRA", "REPO", "REVISION", "SIZE_MB", "LocalEmbedError",
           "LocalEmbedProvider", "LocalSession", "configure", "extra_installed", "is_ready", "loaded", "mean_pool", "model_dir", "preload",
           "ready_payload", "release_if_idle", "shared", "status"]
