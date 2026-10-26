""""Salvar .docx…" da reunião aberta (F5.1 item 7, U-011, A-18; comportamento 5): o item do menu Exportar e a tarefa. A
reunião é lida na thread da interface (como o .md) e o documento é gerado e gravado numa `Task` (o python-docx só é
importado nela; a escrita é a atômica do .md); um de cada vez. O arquivo aberto no Word dá o aviso do U-030 e o anterior
fica; outro erro mostra o motivo. Sem o extra `docx`, o item fica desabilitado com a dica. Fechar a janela deixa a gravação
em curso terminar; a saída do app a espera antes de fechar o banco."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox, QWidget

from ..log import get_logger
from ..rag import docx_export, export
from .tasks import Task, run_task, wait_before_close

if TYPE_CHECKING:
    from ..rag.export import ExportMeeting

log = get_logger("ui.docx")

DOCX_TIP = "Ata, resumos, ações, marcadores e transcrição num documento do Word"
DOCX_MISSING = "Indisponível: instale o extra docx (python-docx) para salvar em .docx."
DOCX_WAIT_MS = 5000              # a saída do app espera o .docx em curso até isto (depois avisa no log e espera o fim)


class DocxSaver(QObject):
    """O item "Salvar .docx…" (`action`) e a geração numa `Task` (`task`, None parada)."""

    def __init__(self, window: QWidget, meeting_id: int, *, gather: Callable[[], ExportMeeting | None],
                 ask_path: Callable[[ExportMeeting], str | None], saved: Callable[[Path], None], message: Callable[[str], None]):
        super().__init__(window)
        self.window = window
        self.meeting_id = meeting_id
        self.gather, self.ask_path, self.saved, self.message = gather, ask_path, saved, message
        self.ready = docx_export.docx_available()       # procura sem importar (A-18)
        self.action = QAction("Salvar .docx…", window)
        self.action.setEnabled(self.ready)
        self.action.setToolTip(DOCX_TIP if self.ready else DOCX_MISSING)
        self.action.triggered.connect(self._clicked)
        self.task: Task | None = None
        self.target = ""

    def _clicked(self) -> None:
        self.save()

    def save(self, path: str | None = None) -> Task | None:
        """Gera e grava o .docx em `path` (ou no destino que o usuário escolher); None sem destino ou com outro em curso."""
        data = self.gather()
        if data is None or self.task is not None:
            return None
        path = path or self.ask_path(data)
        if not path:
            return None
        target = path if path.lower().endswith(".docx") else path + ".docx"

        def build() -> Path | OSError:
            try:
                return export.write_atomic(target, docx_export.meeting_docx(data))
            except OSError as e:                         # o arquivo aberto no Word: o aviso do U-030, o anterior fica
                return e

        self.action.setEnabled(False)
        self.message("Gerando o .docx…")
        self.target = target
        self.task = run_task(build, None, self._done)
        return self.task

    def _done(self, result: object) -> None:
        with suppress(RuntimeError):                     # a janela já foi destruída: o arquivo foi gravado mesmo assim
            self.task = None
            self.action.setEnabled(self.ready)
            if isinstance(result, Path):
                self.saved(result)
                return
            self.message("")
            log.warning("reunião %s: exportar o .docx para %s falhou: %s", self.meeting_id, self.target, result)
            text = (export.save_error_text(self.target, result) if isinstance(result, OSError)
                    else f"Não consegui gerar o .docx:\n{self.target}\n\n{result}")
            QMessageBox.warning(self.window, "Exportar", text)

    def shutdown(self) -> None:
        if self.task is not None:
            wait_before_close(self.task, DOCX_WAIT_MS, f"o .docx da reunião {self.meeting_id}")


__all__ = ["DOCX_MISSING", "DocxSaver"]
