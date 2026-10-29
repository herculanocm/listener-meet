"""Excluir reuniões (F5.6 L3, U-093, A-36, A-39), sem interface: o Histórico chama numa thread de trabalho.

Antes de apagar: recusa a reunião gravando ou pausada (`reprocess.RECORDING`; a tela recusa também a que está salvando, que
o banco ainda diz gravando, A-41); pede o cancelamento de todos os jobs ativos dela numa transação (o pendente cancela na
hora, `JobsRepo.request_cancel`) e espera até `WAIT_RUNNING_S` pelo que um worker vivo roda; passado o prazo, apaga mesmo
assim (o runner que ainda roda acha a linha do job sumida e para, sem derrubar o worker: F5A-17, A-37). A exclusão em si é
`Store.delete_meeting` (duas transações, A-35). Várias reuniões, uma por vez, com o progresso e a falha de uma sem parar as
outras. Depois, `free_pending` devolve o espaço dos arquivos de áudio marcados (`store/space.py`, A-38), parando com a
gravação ou o pedido de parada. O log leva só ids e contagens, nunca título nem nome (A-46)."""
from __future__ import annotations

import functools
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..log import get_logger
from ..model import MeetingStatus
from ..store import space
from ..store.jobs import ACTIVE
from .reprocess import RECORDING

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("pipeline.delete")

WAIT_RUNNING_S = 15.0                 # A-36: a espera pelo job que roda antes de apagar mesmo assim
POLL_S = 0.25
GONE_TEXT = "Esta reunião não existe mais."
RECORDING_TEXT = "A reunião está sendo gravada: pare a gravação antes de excluir."
STOPPED_TEXT = "A exclusão parou antes desta reunião (o app está saindo); ela continua no Histórico."
CANCELLED_TEXT = ("A exclusão parou no meio (o app está saindo): a reunião continua no Histórico, mas o processamento dela foi "
                  "cancelado; para refazê-lo, use Reprocessar na fila.")             # R-06

type Check = Callable[[], bool]
type Progress = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class DeleteOutcome:
    meeting_id: int
    deleted: bool
    message: str = ""                 # o motivo da recusa ou da falha, para a tela
    cancelled: int = 0                # jobs com o cancelamento pedido
    still_running: int = 0            # os que não pararam no prazo (a exclusão seguiu)


def _never() -> bool:
    return False


def refusal(store: Store, meeting_id: int) -> str | None:
    """Por que a reunião não pode ser excluída agora (A-41), ou None."""
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        return GONE_TEXT
    return RECORDING_TEXT if str(meeting.get("status")) in RECORDING else None


def cancel_jobs(store: Store, meeting_id: int) -> tuple[int, list[int]]:
    """Pede o cancelamento de todos os jobs ativos da reunião ainda sem o pedido, numa transação; devolve (quantos, os que um
    worker vivo roda)."""
    with store.transaction():
        jobs = [int(j["id"]) for j in store.jobs.for_meeting(meeting_id) if str(j["status"]) in ACTIVE and not j.get("cancel_requested")]
        for job_id in jobs:
            store.jobs.request_cancel(job_id)
    return len(jobs), held_jobs(store, meeting_id)


def held_jobs(store: Store, meeting_id: int) -> list[int]:
    """Os jobs da reunião que um worker vivo ainda segura (heartbeat fresco): os que vale a pena esperar."""
    return [int(j["id"]) for j in store.jobs.held_fresh() if int(j["meeting_id"]) == meeting_id]


def wait_jobs(store: Store, meeting_id: int, timeout_s: float, *, should_stop: Check = _never,
              sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> tuple[list[int], int]:
    """Espera os jobs da reunião pararem (o worker atende o cancelamento entre janelas ou tarefas) até `timeout_s`. A cada
    volta pede de novo (R-03): o job que nasceu na espera (o pesado conclui e enfileira o `llm` e o `index`; a varredura do
    app) também para, antes das chamadas pagas. Devolve (os que ainda rodam, os cancelamentos novos)."""
    deadline = clock() + timeout_s
    left, more = held_jobs(store, meeting_id), 0
    while left and clock() < deadline and not should_stop():
        sleep(POLL_S)
        n, left = cancel_jobs(store, meeting_id)
        more += n
    return left, more


def delete_meeting(store: Store, meeting_id: int, *, wait_s: float = WAIT_RUNNING_S, should_stop: Check = _never,
                   sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> DeleteOutcome:
    """Uma reunião: recusa, cancela os jobs, espera o que roda e apaga (definitivo, sem lixeira: CP3 pelo pedido do usuário)."""
    reason = refusal(store, meeting_id)
    if reason is not None:
        log.info("reunião %s: exclusão recusada (%s)", meeting_id, "não existe" if reason == GONE_TEXT else "gravando")
        return DeleteOutcome(meeting_id, False, reason)
    if should_stop():                                    # R-06: a saída antes de tocar nos jobs: nada mudou
        return DeleteOutcome(meeting_id, False, STOPPED_TEXT)
    cancelled, running = cancel_jobs(store, meeting_id)
    left, more = wait_jobs(store, meeting_id, wait_s, should_stop=should_stop, sleep=sleep, clock=clock) if running else ([], 0)
    cancelled += more
    if should_stop():                                    # R-06: os jobs já foram cancelados: a mensagem diz
        return DeleteOutcome(meeting_id, False, CANCELLED_TEXT if cancelled else STOPPED_TEXT, cancelled, len(left))
    if left:
        log.warning("reunião %s: %d job(s) não pararam em %.0f s (%s); excluindo assim mesmo", meeting_id, len(left), wait_s,
                    ", ".join(map(str, left)))
    store.delete_meeting(meeting_id)
    log.info("reunião %s excluída pelo usuário (%d job(s) cancelado(s))", meeting_id, cancelled)
    return DeleteOutcome(meeting_id, True, "", cancelled, len(left))


def delete_many(store: Store, meeting_ids: Sequence[int], *, progress: Progress | None = None, should_stop: Check = _never,
                wait_s: float = WAIT_RUNNING_S, sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic) -> list[DeleteOutcome]:
    """Várias, uma por vez ("excluindo 2 de 5"): a falha de uma fica no resultado dela (a reunião segue listada; excluir de
    novo termina, A-35) e as outras seguem; o pedido de parada para entre reuniões."""
    out: list[DeleteOutcome] = []
    total = len(meeting_ids)
    for n, mid in enumerate(meeting_ids, 1):
        if should_stop():
            out.append(DeleteOutcome(int(mid), False, STOPPED_TEXT))
            continue
        if progress is not None:
            progress(n, total)
        try:
            out.append(delete_meeting(store, int(mid), wait_s=wait_s, should_stop=should_stop, sleep=sleep, clock=clock))
        except Exception as e:  # uma reunião que falha não para as outras; o motivo vai para a tela e o log
            log.exception("reunião %s: a exclusão falhou", mid)
            out.append(DeleteOutcome(int(mid), False, f"A exclusão falhou ({type(e).__name__}); tente de novo."))
    return out


def free_pending(store: Store, *, recording: Check, should_stop: Check = _never, on_conn: space.OnConn | None = None,
                 step_bytes: int = space.STEP_BYTES, disk_free: Callable[[Path], int] = space.disk_free) -> list[space.SpaceResult]:
    """O espaço dos arquivos marcados de volta (A-38, A-39), um por vez; o que ficou pendente segue na lista para a próxima
    abertura ociosa. `recording` é a gravação do app (a tela sabe da que está salvando); o banco diz das outras."""
    current = store.core.current_audio_file()

    def busy() -> bool:
        return recording() or bool(store.meetings.list_by_status(MeetingStatus.RECORDING, MeetingStatus.PAUSED))

    def worker_alive() -> bool:
        return bool(store.jobs.held_fresh())

    out: list[space.SpaceResult] = []
    for file in space.pending(store.core):
        if should_stop() or busy():
            break
        res = space.free_file(store.base_dir / file, current=file == current, stop=should_stop, busy=busy, worker_alive=worker_alive,
                              on_conn=on_conn, step_bytes=step_bytes, disk_free=disk_free,
                              exclusive=functools.partial(store.core.files.exclusive, file))     # R-02: sem anexo no app
        if res.outcome != space.SpaceOutcome.PENDING:
            space.drop_pending(store.core, file)
        log.info("espaço de %s: %s%s, %d byte(s) liberado(s)%s", file, res.outcome, f" ({res.mode})" if res.mode else "",
                 res.freed_bytes, f"; motivo: {res.reason}" if res.reason else "")
        out.append(res)
    return out


__all__ = ["CANCELLED_TEXT", "GONE_TEXT", "RECORDING_TEXT", "STOPPED_TEXT", "WAIT_RUNNING_S", "DeleteOutcome", "cancel_jobs", "delete_many",
           "delete_meeting", "free_pending", "held_jobs", "refusal", "wait_jobs"]
