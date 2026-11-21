"""F5.27 (U-184, item 12): "Apagar a tela desta reunião (1,2 GB)…" no "⋯" da reunião aberta, para liberar disco sem
perder o áudio (CP3: só por ação do usuário, com confirmação, e o padrão é "Manter").

O item aparece só com a reunião parada (nem gravando nem pausada) e com pedaços da tela, e fica apagado com uma exportação
de mídia da mesma reunião em curso (a tarefa lê os pedaços). O tamanho é a soma dos MP4 (`store.screen.total_bytes`), lido
ao abrir o menu. Com o arquivo da tela do mês na lista do espaço a liberar (`space.pending`), o item some sem consultar o
arquivo: anexá-lo na conexão da interface interromperia o `VACUUM` que devolve o disco (`store/attached.py`).

Apagar roda numa `Task` (o DELETE de 1 GB de BLOBs lê cada página de transbordo): `delete_for_meeting` apaga os pedaços e
põe o arquivo na lista do espaço a liberar; o evento `screen_deleted` leva os pedaços e os bytes. No fim, como a exclusão
de reuniões (`meeting_delete`): a conexão da interface solta os arquivos e `on_free_space` (a liberação em segundo plano
do controlador) devolve o disco; sem ela, o espaço volta na próxima abertura ociosa do app. O áudio, a transcrição e o
`screen_file` ficam. O log leva a reunião, as contagens e os bytes."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox, QWidget

from ..log import get_logger
from ..model import EventType, MeetingStatus
from ..store import space
from .common import human_size
from .tasks import Task, run_task, wait_before_close

if TYPE_CHECKING:
    from ..store import Store
    from ..store.screen import ScreenDeleted

log = get_logger("ui.media")

TITLE = "Apagar a tela"
KEEP, ERASE = "Manter", "Apagar a tela"
ERASING = "A tela desta reunião está sendo apagada: exporte depois que terminar."
WAIT_MS = 30_000                 # a saída do app espera o DELETE em curso até isto (ele não para no meio)


def item_text(size: int) -> str:
    return f"Apagar a tela desta reunião ({human_size(size)})…"


def done_text(size: int) -> str:
    return f"Tela apagada: {human_size(size)} liberados."


def confirm_box(parent: QWidget | None, size: int) -> tuple[QMessageBox, Any]:
    """A confirmação, com "Manter" como padrão e no Esc (o Enter mantém); devolve a caixa e o botão de apagar."""
    text = (f"Apagar a tela gravada desta reunião ({human_size(size)})? O áudio, a transcrição e a ata ficam. Não dá para "
            "desfazer.")
    box = QMessageBox(QMessageBox.Icon.Warning, TITLE, text, parent=parent)
    keep = box.addButton(KEEP, QMessageBox.ButtonRole.RejectRole)
    erase = box.addButton(ERASE, QMessageBox.ButtonRole.DestructiveRole)
    box.setDefaultButton(keep)
    box.setEscapeButton(keep)
    return box, erase


def erase_screen(store: Store, meeting_id: int) -> ScreenDeleted:
    """Na tarefa: apaga os pedaços da tela da reunião e grava o evento (só quando havia o que apagar)."""
    res = store.screen.delete_for_meeting(meeting_id)
    if res.chunks:
        store.add_event(meeting_id, None, EventType.SCREEN_DELETED, {"chunks": res.chunks, "bytes": res.data_bytes})
    log.info("reunião %s: tela apagada pelo usuário (%d pedaço(s), %d byte(s))", meeting_id, res.chunks, res.data_bytes)
    return res


class ScreenEraser(QObject):
    """O item `action` do "⋯" e a tarefa (`task`, None parada)."""

    def __init__(self, window: QWidget, store: Store, meeting_id: int, *, message: Callable[[str], None],
                 exporting: Callable[[], bool], on_free_space: Callable[[], Any] | None = None):
        super().__init__(window)
        self.window, self.store, self.meeting_id = window, store, meeting_id
        self.message, self.exporting, self.on_free_space = message, exporting, on_free_space
        self.action = QAction(item_text(0), window)
        self.action.setToolTip("Apaga só a tela gravada, para liberar disco; o áudio, a transcrição e a ata ficam")
        self.action.triggered.connect(self._clicked)
        self.action.setVisible(False)
        self.task: Task | None = None
        self.size = 0

    def screen_bytes(self) -> int:
        """Os bytes da tela a apagar, ou 0 quando o item não cabe (gravando, sem tela, o espaço do arquivo a liberar)."""
        m = self.store.get_meeting(self.meeting_id)
        if m is None or m.get("status") in (MeetingStatus.RECORDING, MeetingStatus.PAUSED):
            return 0
        file = self.store.screen.file_for(self.meeting_id)
        if file is None or file in space.pending(self.store.core):
            return 0
        return self.store.screen.total_bytes(self.meeting_id)

    def refresh(self) -> None:
        """O item de acordo com o estado de agora (ao abrir o "⋯" e a cada recarga)."""
        if self.task is not None:
            self.action.setEnabled(False)
            return
        try:
            self.size = self.screen_bytes()
        except Exception as e:  # o banco fechou ou o arquivo não abre: o item some, o menu abre
            log.warning("reunião %s: tamanho da tela não lido: %s", self.meeting_id, type(e).__name__)
            self.size = 0
        self.action.setText(item_text(self.size))
        self.action.setVisible(self.size > 0)
        self.action.setEnabled(self.size > 0 and not self.exporting())

    def ask(self, size: int) -> bool:
        """True apaga; "Manter", o Esc e o Enter mantêm."""
        box, erase = confirm_box(self.window, size)
        box.exec()
        return box.clickedButton() is erase

    def _clicked(self) -> None:
        self.erase()

    def erase(self) -> Task | None:
        """Confere de novo, confirma e apaga numa tarefa; None sem apagar."""
        self.refresh()
        if not (self.action.isVisible() and self.action.isEnabled()) or not self.ask(self.size):
            return None
        if self.exporting() or self.task is not None:        # a exportação pode ter começado com a caixa aberta
            return None
        store, mid = self.store, self.meeting_id
        self.action.setEnabled(False)
        self.task = run_task(lambda: erase_screen(store, mid), store, self._done)
        return self.task

    def _done(self, result: object) -> None:
        with suppress(RuntimeError):                         # a janela já foi destruída: o log já foi escrito na tarefa
            self.task = None
            if isinstance(result, BaseException):
                log.warning("reunião %s: apagar a tela falhou: %s", self.meeting_id, type(result).__name__)
                self.message(f"Não consegui apagar a tela ({type(result).__name__}); tente de novo.")
                self.refresh()
                return
            freed = getattr(result, "data_bytes", 0)
            with suppress(Exception):                        # a interface solta os arquivos antes do espaço (R-02 da F5.6)
                self.store.core.release_audio()
            if self.on_free_space is not None:
                self.on_free_space()
            self.message(done_text(freed))
            self.refresh()

    def shutdown(self) -> None:
        """Saída do app: espera o DELETE em curso antes de o banco fechar (ele não para no meio)."""
        task, self.task = self.task, None
        if task is not None:
            wait_before_close(task, WAIT_MS, f"apagar a tela da reunião {self.meeting_id}")


__all__ = ["ERASE", "ERASING", "KEEP", "ScreenEraser", "confirm_box", "done_text", "erase_screen", "item_text"]
