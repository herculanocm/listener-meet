"""O hash dos trechos do índice e o casamento dos trechos montados com os gravados (F5.18, PF-29). Puro: nada de banco.

`text_hash` cobre o que entra no FTS e no embedding: tipo, título, data, falantes e texto. Nos trechos `transcricao` o título
fica de fora (PF-29): renomear a reunião, ou a LLM sugerir um título, não refaz os trechos nem os vetores da transcrição
(~40 s de CPU por hora de reunião no embedder local; ~US$ 0,01 nos pagos). `legacy_text_hash` é a fórmula anterior, com o
título em todo tipo: o `sync_text` casa por ela os trechos gravados antes desta versão e regrava o hash no lugar, sem refazer
nem apagar vetor (`rag_vectors` não guarda o hash: só o confere ao inserir).

`match_rows` é o casamento por (tipo, hash, ocorrência): a n-ésima repetição de um texto casa a n-ésima linha gravada com ele
(uma fila por chave, na ordem dos ids). Primeiro pelo hash novo; nos `transcricao` que sobram, pelo legado com o título de agora
e com o título guardado em cada trecho (a reunião renomeada antes da migração). Cada linha gravada casa no máximo um trecho."""
from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, NamedTuple

from ..model import ChunkKind

if TYPE_CHECKING:
    from ..store.rag import RagChunkRow
    from .chunker import Chunk

SEP = "\x1f"


def _digest(parts: Iterable[str]) -> str:
    return hashlib.sha1(SEP.join(parts).encode("utf-8")).hexdigest()  # noqa: S324


def legacy_text_hash(kind: str, title: str, date: str, speakers: str, text: str) -> str:
    """A fórmula anterior à F5.18: o título em todo tipo de trecho."""
    return _digest((str(kind), title, date, speakers, text))


def text_hash(kind: str, title: str, date: str, speakers: str, text: str) -> str:
    """Tipo, título, data, falantes e texto; nos `transcricao`, o título vale vazio (PF-29). Nos outros tipos é a fórmula de
    sempre: igual à legada."""
    return _digest((str(kind), "" if str(kind) == ChunkKind.TRANSCRIPT else title, date, speakers, text))


class Match(NamedTuple):
    row: RagChunkRow
    chunk: Chunk
    legacy: bool                 # casou pelo hash legado: o `text_hash` gravado é o antigo e é regravado no lugar


def match_rows(old: Sequence[RagChunkRow], built: Sequence[Chunk], date: str) -> tuple[list[Match], list[Chunk]]:
    """(mantidos, novos). As linhas de `old` que não casaram são as que saem."""
    pool: dict[tuple[str, str], deque[RagChunkRow]] = defaultdict(deque)
    for r in old:
        pool[(str(r["kind"]), str(r["text_hash"]))].append(r)
    titles = list(dict.fromkeys([c.title for c in built[:1]] + [str(r.get("title") or "") for r in old
                                                                  if str(r["kind"]) == ChunkKind.TRANSCRIPT]))
    kept: list[Match] = []
    added: list[Chunk] = []
    for c in built:
        queue = pool.get((str(c.kind), c.text_hash))
        if queue:
            kept.append(Match(queue.popleft(), c, False))
            continue
        row = _legacy_match(pool, c, titles, date) if c.kind == ChunkKind.TRANSCRIPT else None
        if row is None:
            added.append(c)
        else:
            kept.append(Match(row, c, True))
    return kept, added


def _legacy_match(pool: dict[tuple[str, str], deque[RagChunkRow]], c: Chunk, titles: Sequence[str], date: str) -> RagChunkRow | None:
    for title in titles:
        queue = pool.get((str(c.kind), legacy_text_hash(c.kind, title, date, c.speakers, c.text)))
        if queue:
            return queue.popleft()
    return None
