"""U-042 (F5.2 L3) e F5.6 (A-03, A-11, A-12): a ata depois da identificação das vozes, só a pedido. O pedido é o evento
`llm_requested` (o id dele é o `request_id`), consumido uma vez por `llm_requests`: com o modo "quando todos tiverem nome" e
voz pendente, vira o adiamento `llm_deferred` (job pai, tarefas, `requested` e o `request_id`); senão, ou quando o adiamento
sai (a revisão sem voz pendente, o "Concluir"), um job `llm` e o `llm_released` com o `request_id`. Os adiamentos automáticos
da F5.2, sem a marca `requested`, nunca disparam (A-03). Reprocessar troca o job pai: o adiamento de um job velho não sai
sozinho; o fim do processamento novo o refaz sobre a transcrição nova. Os nomes que mudam depois da ata pronta acendem o
aviso "Atualizar a ata com os nomes" pela assinatura dos falantes gravada no `minutes_done` (A-15; um hash, sem nomes),
versionada (F5.5 A-13, P-01): a v1, sobre o nome interno ("Voz SPK_1"), das atas de antes; a v2, sobre o nome mostrado
("Voz 2"), das novas, que acende também numa mescla de vozes sem pessoa. Só eventos da reunião: sem esquema novo."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..log import get_logger
from ..model import EventType, JobStep, SummaryKind
from . import review
from .voices import NO_NAMES, numbering

if TYPE_CHECKING:
    from ..store import Store
    from ..store.segments import ClusterRow

log = get_logger("pipeline.summary_gate")

EARLY = frozenset({JobStep.IDENTIFY, JobStep.TITLE, JobStep.REVISE})   # o núcleo (F5.6 A-05): sai logo, sem pedido nem espera
SPEAKERS_V = 2                 # F5.5 (A-13): a versão da assinatura que as atas novas gravam (`speakers_v`; sem ela, a 1)
REFRESH = (JobStep.MINUTES, JobStep.EXECUTIVE, JobStep.PRE_SUMMARY)   # "Atualizar a ata com os nomes": as que já existem (A-02)
LEDGER_TYPES = (EventType.LLM_REQUESTED, EventType.LLM_DEFERRED, EventType.LLM_RELEASED, EventType.LLM_CHAINED)


@dataclass(frozen=True, slots=True)
class Deferral:
    parent_job_id: int
    tasks: tuple[str, ...]
    pending: int                  # vozes pendentes agora
    request_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Asked:
    """Um pedido (`llm_requested`, A-11): o id do evento é o `request_id`; `after_job_id` nulo = o próximo processamento."""
    request_id: int
    tasks: tuple[str, ...]
    mode: str
    origin: str
    after_job_id: int | None
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class Ledger:
    """Os eventos dos pedidos de uma reunião numa leitura só (A-15): os pedidos, o último adiamento pedido de cada um (o
    reprocessamento o refaz sobre outro job pai), os liberados (com o job, nulo sem fala) e os processamentos já
    encadeados (`llm_chained`). O adiamento sem `requested` (F5.2) fica de fora (A-03)."""
    asked: tuple[Asked, ...]
    deferrals: dict[int, dict[str, Any]]
    released: dict[int, int | None]
    chained: frozenset[int] = frozenset()

    def consumed(self, request_id: int) -> bool:
        return request_id in self.released or request_id in self.deferrals

    def open(self) -> list[tuple[int, dict[str, Any]]]:
        """Os adiamentos pedidos que ainda não saíram: (request_id, dados do último `llm_deferred`)."""
        return [(rid, d) for rid, d in self.deferrals.items() if rid not in self.released]


def pending_voices(store: Store, meeting_id: int) -> int:
    """Vozes a confirmar no job vigente, pela regra da própria revisão (A-13: um lugar só decide o que é pendente)."""
    return len(review.pending(review.review_items(store, meeting_id)))


def _asked(e: Mapping[str, Any], data: Mapping[str, Any]) -> Asked:
    after = data.get("after_job_id")
    return Asked(int(e["id"]), tuple(str(t) for t in data.get("tasks") or ()), str(data.get("mode") or ""), str(data.get("origin") or ""),
                 int(after) if isinstance(after, int) else None, str(e.get("created_at") or ""))


def ledger(store: Store, meeting_id: int) -> Ledger:
    asked: list[Asked] = []
    deferrals: dict[int, dict[str, Any]] = {}
    released: dict[int, int | None] = {}
    chained: set[int] = set()
    for e in store.meetings.list_events_of(meeting_id, LEDGER_TYPES):
        raw = e.get("data")
        data = raw if isinstance(raw, dict) else {}
        rid = data.get("request_id")
        if e["type"] == EventType.LLM_REQUESTED:
            asked.append(_asked(e, data))
        elif e["type"] == EventType.LLM_CHAINED:
            chained.add(int(data.get("parent_job_id") or 0))
        elif not isinstance(rid, int):
            continue                                     # A-03: adiamento e liberação automáticos da F5.2, sem pedido
        elif e["type"] == EventType.LLM_DEFERRED and data.get("requested"):
            deferrals[rid] = data
        elif e["type"] == EventType.LLM_RELEASED:
            released[rid] = data.get("job_id") if isinstance(data.get("job_id"), int) else None
    return Ledger(tuple(asked), deferrals, released, frozenset(chained))


def record(store: Store, meeting_id: int, parent_job_id: int, tasks: Iterable[str], pending: int, request_id: int) -> None:
    """O pedido "quando todos tiverem nome" espera as vozes (A-11): o adiamento leva a marca e o `request_id`."""
    names = [str(t) for t in tasks]
    store.add_event(meeting_id, None, EventType.LLM_DEFERRED, {"parent_job_id": parent_job_id, "tasks": names, "pending": pending,
                                                              "requested": True, "request_id": request_id})
    log.info("reunião %s: pedido %s adiado até a identificação de %d voz(es) (job pai %s; tarefas %s)", meeting_id, request_id,
             pending, parent_job_id, ", ".join(names))


def deferred(store: Store, meeting_id: int, led: Ledger | None = None) -> Deferral | None:
    """Os pedidos adiados do job pai atual, ainda não liberados, juntos; None quando não há (A-03: só os pedidos)."""
    parent = store.segments.current_job_id(meeting_id)
    if parent is None:
        return None
    opened = [(rid, d) for rid, d in (led or ledger(store, meeting_id)).open() if int(d.get("parent_job_id") or 0) == parent]
    if not opened:
        return None
    tasks = tuple(dict.fromkeys(str(t) for _rid, d in opened for t in d.get("tasks") or ()))
    return Deferral(parent, tasks, pending_voices(store, meeting_id), tuple(rid for rid, _d in opened))


# ---------------------------------------------------------------- nomes depois da ata (A-15)
def rows_digest(rows: Iterable[Mapping[str, Any]], people: dict[int, dict[str, Any]], clusters: Iterable[ClusterRow],
                job_id: int | None, version: int = SPEAKERS_V, key: str = "first") -> str:
    """Hash de (canal, nome, cargo do dia, pessoa) de quem fala nas falas `rows` (`segments.speakers()`, com a primeira fala
    em `first`, ou os próprios segmentos, com `key = t_start_ms`): muda quando um nome muda. A v1 dá à voz sem pessoa o
    nome interno ("Voz SPK_1", o valor de antes da F5.5); a v2, o nome mostrado ("Voz 2"), que muda também numa mescla.
    Um hash e não os nomes: o evento fica no banco e o esquecimento (U-041) não precisaria reescrevê-lo."""
    rows, by_id = list(rows), {int(c["id"]): c for c in clusters}
    names = NO_NAMES if version == 1 else numbering(by_id.values(), job_id, rows, key)
    seen: set[tuple[str, str, str, int]] = set()
    for s in rows:
        name, role, pid = review._speaker(s, people, by_id, names)
        seen.add((str(s.get("channel") or ""), name, role, pid or 0))
    return hashlib.sha256(json.dumps(sorted(seen), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def speakers_digest(store: Store, meeting_id: int, version: int = SPEAKERS_V) -> str:
    """A assinatura de quem fala na leitura de agora (o job vigente), sem ler as falas (A-13: `segments.speakers()`)."""
    people, job_id, clusters, speakers = review.reading(store, meeting_id)
    return rows_digest(speakers, people, clusters.values(), job_id, version)


def names_changed(store: Store, meeting_id: int) -> bool:
    """A ata atual foi gerada com outros nomes: só quando ela é do job vigente (D-043) e tem a assinatura gravada; ata
    antiga, sem assinatura, não acende o aviso. A comparação é na versão gravada (F5.5 A-13: sem `speakers_v`, a 1)."""
    row = store.summaries.current(meeting_id, SummaryKind.MINUTES)
    if row is None or row.get("job_id") != store.segments.current_job_id(meeting_id):
        return False
    digest, version = None, 1
    for e in store.meetings.list_events_of(meeting_id, (EventType.JOB_STEP,)):
        raw = e.get("data")
        data = raw if isinstance(raw, dict) else {}
        if data.get("step") == "minutes_done" and data.get("summary_id") == row["id"]:
            digest, version = data.get("speakers"), int(data.get("speakers_v") or 1)
    return bool(digest) and digest != speakers_digest(store, meeting_id, version)
