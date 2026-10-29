"""F5.5 (A-16, A-09, P-02): a varredura única do índice para as reuniões de antes da F5.5, com "Voz SPK_1" nos trechos da
transcrição. Cada uma volta à fila uma vez (`enqueue_index(mid, "sweep")`, sem `force`) e o `index` regrava texto e
`rag_chunks.speakers` com "Voz 2"; só os trechos com voz sem pessoa mudam de hash, os outros ficam com os vetores. Roda só
sob o portão do ramo de vetores do `index.sweep` (vetores automáticos ligados, embedder pronto, modelo confirmado; o local
depois do "Reindexar agora"): com os automáticos desligados os trechos que mudam perderiam os vetores (A-23), e a busca por
palavra ainda acha o nome antigo. A marca é estado interno em `settings`, fora do SPECS e do prefixo `pipeline.` (que vai
ao `config_json` dos jobs), gravada só com a volta completa da fila (sem `should_stop`). Log só com contagens (A-26)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..log import get_logger
from ..model import ChunkKind
from ..pipeline.voices import VOICE_PREFIX

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("rag.voice_sweep")

MARK = "rag.voice_names_swept"          # "1" depois da volta completa; sem a linha, a varredura ainda falta


def pending(store: Store) -> set[int] | None:
    """As reuniões com trecho de transcrição de voz sem pessoa ("Voz " em `speakers`) enquanto a marca falta; None depois
    dela (nada a pôr na fila, nada a marcar)."""
    if store.get_setting(MARK, ""):
        return None
    rows = store._q("SELECT DISTINCT meeting_id FROM rag_chunks WHERE kind = ? AND speakers LIKE ?",
                    (str(ChunkKind.TRANSCRIPT), f"%{VOICE_PREFIX}%"))
    return {int(r[0]) for r in rows}


def done(store: Store, meetings: set[int] | None) -> None:
    """A volta completa da fila: grava a marca, só quando a varredura rodou sob o portão (`meetings` não nulo)."""
    if meetings is None:
        return
    store.set_setting(MARK, "1")
    log.info("varredura única das vozes numeradas (F5.5): %d reunião(ões) com voz sem pessoa nos trechos", len(meetings))


__all__ = ["MARK", "done", "pending"]
