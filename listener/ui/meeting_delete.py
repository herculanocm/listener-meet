"""Excluir reuniões pelo Histórico (F5.6 L3, U-093, A-36 a A-42), o lado do controlador: `MeetingDeleter` recebe o pedido da
página (`HistoryWindow(on_delete=...)`), recusa a reunião que grava, pausa ou salva (o banco ainda diz gravando enquanto
salva: a tela sabe pelo gravador, A-41), confirma uma vez (`history_delete.confirm_delete`), e, antes de apagar, na thread
da interface, para o player se ele toca a reunião, fecha a janela dela (`shutdown()`, que cancela o chat), a revisão e a
janela ao vivo (A-42). A exclusão roda numa `Task` (`pipeline.delete.delete_many`, uma por vez, com "Excluindo 2 de 5…"),
cancelável pela saída do app; no fim, a página recarrega e o aviso diz quantas saíram e por que alguma ficou.

Depois, o espaço volta em segundo plano (`SpaceTask`, A-38, A-39): a liberação dos arquivos marcados roda numa QThread com
conexão própria por arquivo; `cancel()` acende o pedido de parada e interrompe a conexão em curso (`interrupt()`, seguro de
outra thread): a saída do app (o `_stop_windows` cancela e espera toda `Task`) e o começo de uma gravação (antes do
`rec.start()`) a param. O que ficou pendente é tentado na próxima abertura ociosa (`resume_space`, no `start`).

Também aqui, vindo do controlador (F5.6: ele não cresce, A-44): abrir pelo Histórico a reunião que ainda existe; a que foi
excluída (uma citação antiga do "Perguntar às reuniões", P-02) diz "Esta reunião não existe mais".

Revisão do L3: a confirmação recebe todas as selecionadas, com as recusadas à parte (R-11); uma exclusão nova interrompe a
liberação de espaço em curso (R-02) e, no fim, a conexão da interface solta os arquivos de áudio antes de liberar o espaço
(um arquivo anexado nela não recebe o VACUUM inteiro); a reunião sendo excluída não reabre (R-07: `refuses`, que o
controlador consulta ao abrir a reunião e a revisão)."""
from __future__ import annotations

import functools
import sqlite3
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from ..log import get_logger
from ..pipeline import delete
from ..store import space
from .history_delete import DeleteView, confirm_delete, plural
from .notices import NoticeKind
from .tasks import Task, live_threads, release, run_task

if TYPE_CHECKING:
    from ..store import Store
    from .controller import Controller
    from .history_data import MeetingLine

log = get_logger("ui.delete")

type Confirm = Callable[[Any, Sequence[MeetingLine], dict[int, str]], bool]


class SpaceTask(Task):
    """A liberação de espaço numa QThread (A-39). `cancel()` acende o evento (lido entre os passos e os arquivos) e interrompe
    a conexão do arquivo em curso: o `VACUUM` inteiro para na hora e o SQLite desfaz a transação dele (nada corrompe)."""

    def __init__(self, store: Store, recording: Callable[[], bool]):
        super().__init__(self._work, store)
        self.recording = recording
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _hold(self, conn: sqlite3.Connection | None) -> None:
        with self._lock:
            self._conn = conn

    @override
    def cancel(self) -> None:
        super().cancel()
        with self._lock:
            if self._conn is not None:
                self._conn.interrupt()

    def _work(self) -> list[space.SpaceResult]:
        if self.store is None:
            return []
        return delete.free_pending(self.store, recording=self.recording, should_stop=self.cancel_event.is_set, on_conn=self._hold)


class MeetingDeleter(QObject):
    """A exclusão do Histórico e o espaço de volta, em nome do controlador (`controller.deleter`)."""
    _progress = Signal(int, int)             # (n, total), da thread da exclusão para a da interface

    def __init__(self, controller: Controller, confirm: Confirm = confirm_delete):
        super().__init__(controller)
        self.c = controller
        self.confirm = confirm                # os testes trocam
        self.task: Task | None = None
        self.space_task: SpaceTask | None = None
        self.view: DeleteView | None = None
        self.deleting: set[int] = set()       # R-07: as reuniões da exclusão em curso (não reabrem)
        self._progress.connect(self._on_progress)

    # ---------------------------------------------------------------- abrir (vindo do controlador)
    def open_from_history(self, meeting_id: int, tab: str | None = None, t_ms: int | None = None, play: bool = False) -> None:
        """O `on_open` do Histórico: a reunião que ainda existe abre (no ponto, tocando); a excluída avisa (P-02)."""
        if self.c.store.get_meeting(meeting_id) is None:
            self.c.notify("Esta reunião não existe mais.")
            return
        self.c.open_meeting(meeting_id, tab, t_ms, play)

    def refuses(self, meeting_id: int) -> bool:
        """R-07: a reunião está sendo excluída (o pedido já foi confirmado): não abre nem a reunião nem a revisão; o aviso diz."""
        if meeting_id not in self.deleting:
            return False
        self.c.notify("Esta reunião está sendo excluída.")
        return True

    # ---------------------------------------------------------------- excluir
    def refusals(self, lines: Sequence[MeetingLine]) -> dict[int, str]:
        """A-41: as que não podem sair agora, com o motivo (a da gravação em curso, até salvando; as que o banco diz gravando)."""
        rec = self.c.recorder
        out: dict[int, str] = {}
        for line in lines:
            mid = line.meeting_id
            reason = delete.RECORDING_TEXT if rec is not None and rec.meeting_id == mid else delete.refusal(self.c.store, mid)
            if reason is not None:
                out[mid] = reason
        return out

    def request(self, lines: list[MeetingLine], view: DeleteView) -> bool:
        """O pedido da página: recusa, confirma, fecha o que mostra a reunião e exclui numa `Task`. False sem exclusão."""
        if self.task is not None:
            QMessageBox.information(view.parent_widget(), "Excluir reuniões", "Uma exclusão já está em andamento.")
            return False
        refused = self.refusals(lines)
        ok = [line.meeting_id for line in lines if line.meeting_id not in refused]
        if not self.confirm(view.parent_widget(), lines, refused) or not ok:      # R-11: a tela mostra as recusadas à parte
            log.info("exclusão de %d reunião(ões) não confirmada (%d recusada(s))", len(ok), len(refused))
            return False
        ids = ok
        self.interrupt_space()                           # R-02: o VACUUM inteiro seguraria o arquivo da exclusão nova
        log.info("exclusão confirmada: reuniões %s", ", ".join(map(str, ids)))
        for mid in ids:
            self.close_windows(mid)
        self._start(ids, view)
        return True

    def close_windows(self, meeting_id: int) -> None:
        """A-42, na thread da interface: o player (se toca esta reunião), a janela da reunião (o chat em voo cancelado e
        esperado), a revisão e a janela ao vivo dela."""
        c = self.c
        c.player.stop(meeting_id)
        win = c._meetings.get(meeting_id)
        if win is not None:
            win.shutdown()
            win.close()                                  # `closed` -> `_meeting_closed` solta e apaga
        dlg = c._reviews.get(meeting_id)
        if dlg is not None:
            dlg.close()                                  # `finished` -> `_review_closed`
        live = c.live_window
        if live is not None and live.meeting_id == meeting_id:
            live.close()                                 # `closed` -> `_live_window_closed`

    def _start(self, ids: list[int], view: DeleteView) -> None:
        self.view = view
        self.deleting = set(ids)
        view.show_progress(f"Excluindo 1 de {len(ids)}…" if len(ids) > 1 else "Excluindo…")
        stop = threading.Event()
        store, progress = self.c.store, self._progress.emit

        def work() -> list[delete.DeleteOutcome]:
            return delete.delete_many(store, ids, progress=progress, should_stop=stop.is_set)

        self.task = run_task(work, store, self._on_done, cancel=stop)

    def _on_progress(self, n: int, total: int) -> None:
        if self.view is not None and total > 1:
            self.view.show_progress(f"Excluindo {n} de {total}…")

    def _on_done(self, result: Any) -> None:
        self.task = None
        self.deleting = set()
        view, self.view = self.view, None
        if self.c._quitting:
            return
        if view is not None:
            view.finished()
        if isinstance(result, BaseException):
            self.c._notify_as(NoticeKind.ERROR, f"A exclusão falhou: {result}")
            return
        outcomes: list[delete.DeleteOutcome] = result
        done = [o for o in outcomes if o.deleted]
        failed = [o for o in outcomes if not o.deleted]
        text = f"{plural(len(done), 'reunião excluída', 'reuniões excluídas')}."
        if failed:
            text += f" {plural(len(failed), 'ficou', 'ficaram')}: " + " ".join(sorted({o.message for o in failed}))
        self.c._notify_as(NoticeKind.ERROR if failed else NoticeKind.INFO, text)
        if done:
            with suppress(Exception):                    # R-02: a interface solta os arquivos; o próximo uso os anexa de novo
                self.c.store.core.release_audio()
            self.start_space()

    # ---------------------------------------------------------------- espaço (A-38, A-39)
    def recording(self) -> bool:
        """Gravando (ou abrindo a gravação, ou salvando): lido da thread do espaço; uma leitura de atributo."""
        return self.c.recorder is not None

    def start_space(self) -> bool:
        """O espaço dos arquivos marcados de volta, numa `SpaceTask`; False quando não há o que liberar ou já roda."""
        if self.space_task is not None or self.c._quitting or self.recording() or not space.pending(self.c.store.core):
            return False
        task = SpaceTask(self.c.store, self.recording)
        live_threads.add(task)                           # referência viva até `finished` (sem parent, M-12)
        task.done.connect(self._on_space_done)
        task.finished.connect(functools.partial(release, task))
        self.space_task = task
        task.start()
        return True

    def resume_space(self) -> bool:
        """Abertura ociosa (o `start` do controlador): o que ficou pendente de outra vez."""
        try:
            return self.start_space()
        except Exception as e:  # o espaço nunca impede a abertura; fica para a próxima
            log.warning("espaço pendente dos arquivos de áudio não conferido na abertura: %s", e)
            return False

    def interrupt_space(self) -> None:
        """Começar uma gravação (antes do `rec.start()`): a liberação para já; o resto fica pendente."""
        task = self.space_task
        if task is not None:
            log.info("liberação de espaço interrompida: a gravação vai começar")
            with suppress(RuntimeError):                 # já destruída pelo `release`
                task.cancel()

    def _on_space_done(self, result: Any) -> None:
        self.space_task = None
        if isinstance(result, BaseException):
            log.warning("liberação de espaço falhou: %s", result)


__all__ = ["MeetingDeleter", "SpaceTask"]
