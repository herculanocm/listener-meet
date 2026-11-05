"""O cabeçalho da reunião aberta (F5.11 L2, U-102), fora do `ui/meeting.py` (A-35). Uma linha só: o título, que grava ao sair
do campo e no Enter, com o Esc de volta ao gravado (A-22, `TitleTab` compacto); "Revisar falantes…" à vista só com voz
pendente (pergunta 7); "Exportar" e o "⋯", com "Revisar falantes…" (sem voz pendente), a sugestão de título e o "gerar de
novo" dele, "Revisar a transcrição com IA", a troca de idioma e "Reprocessar…", que perguntam antes com o tempo e o custo
(P-05). Embaixo, a legenda: data, duração, participantes, idioma e custo. A contagem das vozes pendentes é uma consulta
leve por recarga (A-36). Mora aqui também o "Exportar" (copiar a ata ou a transcrição com texto e HTML; salvar o .md com
escrita atômica ou o .docx, U-011, D-012). Um mixin da `MeetingWindow`: usa o estado e os métodos dela."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QPoint, QStandardPaths, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ..config.defaults import HF_PROVIDER_ID
from ..log import get_logger
from ..model import ClusterStatus
from ..pipeline import no_diarization, review
from ..pipeline.runner import USER_CLUSTER
from ..rag import export
from .common import copy_rich, markdown_html
from .components import InfoBar
from .docx_save import DocxSaver
from .meeting_language import MeetingLanguage
from .meeting_parts import menu_button
from .theme import METRICS, note, role

if TYPE_CHECKING:
    from ..store import Store
    from .llm_tabs import LlmTabsGroup
    from .transcript import TranscriptTab

log = get_logger("ui.meeting")
MORE_TIP = "Mais ações: revisar falantes, título sugerido, revisar a transcrição com IA, idioma e reprocessar"
EXPORT_TIP = "Copiar para a área de transferência ou salvar um arquivo .md ou .docx"


def pending_voices(store: Store, meeting_id: int) -> int:
    """As vozes pendentes do processamento vigente (a confirmar ou sem nome, sem mescla, fora o microfone do usuário), numa
    consulta só, sem os vetores (A-36: a abertura não fica mais lenta)."""
    job = store.segments.current_job_id(meeting_id)
    if job is None:
        return 0
    rows = store._q("SELECT COUNT(*) FROM clusters WHERE meeting_id = ? AND job_id = ? AND status IN (?, ?) AND merged_into IS NULL "
                    "AND NOT (channel = 'L' AND cluster = ?)", (meeting_id, job, ClusterStatus.AMBIGUOUS, ClusterStatus.UNNAMED,
                                                                  USER_CLUSTER))
    return int(rows[0][0]) if rows else 0


class MeetingHeaderMixin:
    """O cabeçalho e o "Exportar" da `MeetingWindow` (a janela é o pai das caixas e dos menus)."""
    store: Store
    meeting_id: int
    llm: LlmTabsGroup
    transcript: TranscriptTab
    on_review: Callable[[int], Any] | None

    def title_text(self) -> str:
        raise NotImplementedError

    def _host(self) -> QWidget:
        return cast(QWidget, self)                       # o mixin só vive na `MeetingWindow`, um `QWidget`

    def _build_header(self, on_launch: Callable[[bool], Any] | None) -> QVBoxLayout:
        """A linha do título e dos botões e a legenda embaixo; a faixa do aviso fica por conta da janela."""
        host = self._host()
        self.exported: deque[str] = deque(maxlen=100)    # instrumentação dos testes: o que foi copiado ou salvo
        self.btn_review = QPushButton("Revisar falantes…")
        self.btn_review.setToolTip("Confirmar quem é cada voz desta reunião")
        self.btn_review.clicked.connect(self._review_clicked)
        self.btn_review.setEnabled(self.on_review is not None)
        self.btn_export = menu_button("Exportar", EXPORT_TIP, self._export_menu())
        self.language_bar = MeetingLanguage(self.store, self.meeting_id, parent=host, on_launch=on_launch,
                                            on_message=self.show_message, after_change=self.reload_legend)
        self.btn_more = QPushButton("⋯")
        self.btn_more.setToolTip(MORE_TIP)
        self.btn_more.setAccessibleName("Mais ações")
        role(self.btn_more, "subtitle")                         # o "⋯" no corpo do título: o glifo pequeno sumia
        self.btn_more.setFixedWidth(METRICS.control_h + 12)
        self.btn_more.setMinimumHeight(METRICS.control_h)
        self.more_menu = self._more_menu()
        self.btn_more.clicked.connect(self._show_more)
        self.info = QLabel("")
        self.info.setObjectName("meetingInfo")
        note(self.info)                                         # U-078: os fatos em legenda
        self.info.setTextFormat(Qt.TextFormat.PlainText)        # participantes: nomes do usuário (R7-07)
        self.info.setWordWrap(True)
        self.message_bar = InfoBar("info")                      # U-057: o aviso numa faixa (o caminho salvo leva o título:
        self.message = self.message_bar.label                   # texto puro, como a faixa)
        self.message.setObjectName("meetingMessage")
        self.message_bar.setVisible(False)
        top = QHBoxLayout()
        top.addWidget(self.llm.title_tab, 1)
        for button in (self.btn_review, self.btn_export, self.btn_more):
            top.addWidget(button)
        facts = QHBoxLayout()
        facts.addWidget(self.info, 1)
        facts.addWidget(self.llm.cost)
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addLayout(top)
        head.addLayout(facts)
        return head

    def _export_menu(self) -> QMenu:
        host = self._host()
        self.act_copy_minutes = QAction("Copiar ata", host)
        self.act_copy_transcript = QAction("Copiar transcrição", host)
        self.act_save = QAction("Salvar ata e transcrição (.md)…", host)
        self.act_copy_minutes.triggered.connect(self.copy_minutes)
        self.act_copy_transcript.triggered.connect(self.copy_transcript)
        self.act_save.triggered.connect(self._save_clicked)
        self.docx = DocxSaver(host, self.meeting_id, gather=self._export_data, saved=self._saved, message=self.show_message,
                              ask_path=self._ask_docx)
        menu = QMenu(host)
        menu.setToolTipsVisible(True)                    # a dica do item .docx sem o extra (A-18)
        for act in (self.act_copy_minutes, self.act_copy_transcript):
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(self.act_save)
        menu.addAction(self.docx.action)
        return menu

    def _more_menu(self) -> QMenu:
        """U-102: o "⋯"; o "Revisar falantes…" daqui só aparece quando o botão some (sem voz pendente)."""
        host = self._host()
        menu = QMenu(host)
        menu.setToolTipsVisible(True)
        self.act_review = menu.addAction("Revisar falantes…")
        self.act_review.triggered.connect(self._review_clicked)
        self.act_review_sep = menu.addSeparator()
        menu.addAction(self.llm.title_tab.act_use)
        menu.addAction(self.llm.title_tab.act_regen)
        self.act_revise = menu.addAction("Revisar a transcrição com IA")
        self.act_revise.triggered.connect(self._revise_clicked)
        menu.addSeparator()
        menu.addAction(self.language_bar.action)
        menu.addAction(self.language_bar.act_reprocess)
        return menu

    def _show_more(self) -> None:
        self.more_menu.popup(self.btn_more.mapToGlobal(QPoint(0, self.btn_more.height())))

    # ---------------------------------------------------------------- leitura
    def reload_header(self) -> None:
        self._host().setWindowTitle(f"{self.title_text()} — reunião")
        self.language_bar.reload()
        self.reload_legend()
        self._diarization_notice()
        pending = pending_voices(self.store, self.meeting_id)
        self.btn_review.setVisible(pending > 0)                 # pergunta 7: à vista só com voz pendente; senão, no "⋯"
        self.act_review.setVisible(pending == 0)
        self.act_review.setEnabled(self.on_review is not None)
        self.act_review_sep.setVisible(pending == 0)
        self.act_revise.setEnabled(self.transcript.btn_revise.isEnabled())
        self.act_revise.setToolTip(self.transcript.btn_revise.toolTip())

    def reload_legend(self) -> None:
        """A legenda: data e hora, duração, participantes (quem fala, `review.participants`) e o idioma; o custo vem ao lado."""
        m = self.store.get_meeting(self.meeting_id) or {}
        people = review.participants(self.store, self.meeting_id)
        if not people and m.get("expected_participants"):
            people = [str(m["expected_participants"])]
        facts = [export.fmt_datetime(m.get("started_at")), export.fmt_duration(m.get("duration_ms")),
                 f"Participantes: {', '.join(people)}" if people else "", self.language_bar.text]
        self.info.setText(" · ".join(f for f in facts if f))

    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self.message_bar.setVisible(bool(text))

    def _diarization_notice(self) -> None:
        """F5.16 (U-139): a reunião transcrita sem diarização diz como resolver, no lugar de uma faixa vazia ou do próprio
        aviso (o resultado de outra ação fica); reprocessada com diarização, o aviso sai."""
        own = (no_diarization.NOTICE_MISSING, no_diarization.NOTICE_PRESENT)
        shown = self.message.text()
        if no_diarization.meeting_reason(self.store, self.meeting_id) is None:
            if shown in own:
                self.show_message("")
            return
        if not shown or shown in own:
            self.show_message(no_diarization.notice(bool(str(self.store.settings.provider_key(HF_PROVIDER_ID) or "").strip())))

    def _review_clicked(self) -> None:
        if self.on_review is not None:
            self.on_review(self.meeting_id)

    def _revise_clicked(self) -> None:
        self.transcript.request_revise()
        self.show_message(self.transcript.llm_note.text())

    # ---------------------------------------------------------------- exportar (U-011, D-012)
    def _export_data(self) -> export.ExportMeeting | None:
        data = export.gather(self.store, self.meeting_id, prefer_llm=self.transcript.prefer_llm)
        if data is None:
            self.show_message("Esta reunião não existe mais.")
        return data

    def copy_minutes(self) -> str:
        data = self._export_data()
        if data is None:
            return ""
        md = export.minutes_markdown(data)
        copy_rich(md, markdown_html(md))
        log.info("reunião %s: ata copiada para a área de transferência (%d caracteres)", self.meeting_id, len(md))
        self.exported.append("ata")
        self.show_message("Ata copiada (texto e formatação).")
        return md

    def copy_transcript(self) -> str:
        data = self._export_data()
        if data is None:
            return ""
        md = export.transcript_markdown(data)
        copy_rich(md, markdown_html(md))
        log.info("reunião %s: transcrição copiada para a área de transferência (%d caracteres)", self.meeting_id, len(md))
        self.exported.append("transcricao")
        self.show_message("Transcrição copiada (texto e formatação).")
        return md

    def _save_clicked(self) -> None:
        self.save_markdown()

    def _ask_docx(self, data: export.ExportMeeting) -> str | None:
        return self._ask_path("Salvar em .docx", data, ".docx", "Documento do Word")

    def _ask_path(self, title: str, data: export.ExportMeeting, ext: str, kind: str) -> str | None:
        """O destino escolhido pelo usuário (nome padrão "AAAA-MM-DD título" em Documentos), com a extensão garantida."""
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(self._host(), title, str(Path(folder) / export.default_filename(data, ext)),
                                              f"{kind} (*{ext})")
        return path or None

    def save_markdown(self, path: str | None = None) -> Path | None:
        """Salva a reunião inteira num .md (nome padrão "AAAA-MM-DD título.md"); escreve num temporário e troca no fim; erro
        de escrita vira aviso."""
        data = self._export_data()
        if data is None:
            return None
        path = path or self._ask_path("Salvar ata e transcrição", data, ".md", "Markdown")
        if not path:
            return None
        if not path.lower().endswith(".md"):
            path += ".md"
        try:
            out = export.write_atomic(path, export.meeting_markdown(data))
        except OSError as e:                               # o nome do arquivo traz o título: só a pasta e a extensão no log
            log.warning("reunião %s: exportar .md em %s falhou: %s", self.meeting_id, Path(path).parent, type(e).__name__)
            QMessageBox.warning(self._host(), "Exportar", export.save_error_text(path, e))
            return None
        self._saved(out)
        return out

    def _saved(self, out: Path) -> None:
        with suppress(OSError):
            log.info("reunião %s: exportada em %s (%s, %d bytes)", self.meeting_id, out.parent, out.suffix, out.stat().st_size)
        self.exported.append(str(out))
        self.show_message(f"Salvo em {out}.")


__all__ = ["EXPORT_TIP", "MORE_TIP", "MeetingHeaderMixin", "pending_voices"]
