"""Reunião aberta (F5 item 9, Q6, F5A-10, F5A-26): uma janela não modal por reunião, no lugar da janela "Transcrição".
Cabeçalho com o título editável (Enter ou "Salvar" grava com `revisions` e reindexa a busca; a sugestão da LLM fica num
menu), data e hora, duração, participantes (quem fala, `review.participants`) e o selo de custo; abas "Ata", "Resumo"
(executivo e pré-resumo), "Ações" (as três do `LlmTabsGroup`, o mesmo da revisão: editar, "gerar de novo", marcas de
reprocessamento e de alerta), "Transcrição" (revisada ou original), "Marcadores" (o clique toca) e "Chat" (o da F3, sobre
a reunião inteira); "Revisar falantes…" e o menu "Exportar" (copiar a ata ou a transcrição com texto e HTML, salvar o .md com
escrita atômica); a barra do player embaixo. Abre num ponto (`open_at`): vai para a transcrição, seleciona a fala de início
mais perto, rola até ela e, pedido, toca do começo dela (sem fala, do ponto menos 150 ms). Fechar para o som desta reunião
e guarda posição e tamanho (`ui.meeting_geometry`). Cabeçalho e avisos em texto puro: um título ou nome com "<b>" não vira
HTML (R7-07).

F5.1: a aba "Linha do tempo" entre Marcadores e Chat (U-008, A-15; `ui/timeline.py`), o destaque da fala que toca na
Transcrição e o cursor da linha do tempo pelo mesmo `PlaybackFollower` (U-025) e "Salvar .docx…" no Exportar (U-011,
A-18; `ui/docx_save.py`).

F5.3 (U-046): "Idioma: pt-BR" no cabeçalho, com a troca pelo outro idioma que reprocessa a reunião (`ui/meeting_language.py`);
a exportação sai no idioma da ata. (A-12) A resposta do chat com a janela fora de foco chama `on_chat_answer(ok)` (o aviso do
controlador, cujo clique volta à aba Chat)."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence, QMoveEvent, QResizeEvent, QShortcut
from PySide6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from ..llm.prompts.common import stamp
from ..log import get_logger
from ..model import SummaryKind
from ..pipeline import review
from ..rag import export
from .chat import ChatPanel
from .common import clamp_to_screens, copy_rich, markdown_html
from .docx_save import DocxSaver
from .llm_tabs import LlmTabsGroup
from .meeting_language import MeetingLanguage
from .player import PlayerBar
from .timeline import PlaybackFollower
from .timeline_view import TimelineTab
from .transcript import TranscriptTab
from .widget import _screen_rects

if TYPE_CHECKING:
    from ..store import Store
    from .player import Player

log = get_logger("ui.meeting")

TAB_KEYS = ("ata", "resumo", "acoes", "transcricao", "marcadores", "linha", "chat")
TAB_TITLES = ("Ata", "Resumo", "Ações", "Transcrição", "Marcadores", "Linha do tempo", "Chat")
DEFAULT_SIZE = (1040, 720)
MIN_SIZE = (640, 420)
SAVE_DELAY_MS = 600
CASCADE_PX = 28                  # cada janela de reunião a mais nasce um pouco abaixo e à direita da anterior
STYLE = ("QLineEdit#meetingTitle { font-size: 16px; font-weight: 600; padding: 3px 6px; } "
         "QLabel#meetingInfo { color: palette(placeholder-text); } QLabel#meetingMessage { color: #e67e22; }")


class MarkersTab(QWidget):
    """Os marcadores da gravação ("[mm:ss] nota"); o clique ou Enter toca daquele ponto."""

    def __init__(self, store: Store, meeting_id: int, *, play: Callable[[int], Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.play = play
        self.list = QListWidget()
        self.list.itemClicked.connect(self._activated)
        self.list.itemActivated.connect(self._activated)
        self.note = QLabel("")
        self.note.setStyleSheet("color: #888; font-size: 11px;")
        lay = QVBoxLayout(self)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.note)
        self.reload()

    def reload(self) -> None:
        markers = self.store.list_markers(self.meeting_id)
        current = self.list.currentRow()
        self.list.clear()
        for mk in markers:
            item = QListWidgetItem(f"[{stamp(int(mk['t_ms']))}] {mk.get('note') or '(sem nota)'}")
            item.setData(Qt.ItemDataRole.UserRole, int(mk["t_ms"]))
            item.setToolTip("clique para ouvir deste ponto")
            self.list.addItem(item)
        if 0 <= current < self.list.count():
            self.list.setCurrentRow(current)
        self.note.setText(f"{len(markers)} marcador(es); clique num para ouvir deste ponto." if markers
                          else "Nenhum marcador nesta reunião.")

    def _activated(self, item: QListWidgetItem) -> None:
        t = item.data(Qt.ItemDataRole.UserRole)
        if t is not None and self.play is not None:
            self.play(int(t))


class MeetingWindow(QWidget):
    closed = Signal(int)

    def __init__(self, store: Store, meeting_id: int, *, player: Player | None = None, on_review: Callable[[int], Any] | None = None,
                 on_launch: Callable[[bool], Any] | None = None, on_edited: Callable[..., Any] | None = None, cascade: int = 0,
                 on_chat_answer: Callable[[bool], Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.store = store
        self.meeting_id = meeting_id
        self.player = player
        self.on_review = on_review
        self.on_edited = on_edited
        self.exported: deque[str] = deque(maxlen=100)        # instrumentação dos testes: o que foi copiado ou salvo
        self.setMinimumSize(*MIN_SIZE)
        self.setStyleSheet(STYLE)
        self.llm = LlmTabsGroup(store, meeting_id, on_launch=on_launch, on_edited=self._edited, on_message=self.show_message,
                                compact_title=True, parent=self)

        # --- cabeçalho ---
        self.btn_review = QPushButton("Revisar falantes…")
        self.btn_review.setToolTip("Confirmar quem é cada voz desta reunião")
        self.btn_review.clicked.connect(lambda: self.on_review(self.meeting_id) if self.on_review else None)
        self.btn_review.setEnabled(on_review is not None)
        self.act_copy_minutes = QAction("Copiar ata", self)
        self.act_copy_transcript = QAction("Copiar transcrição", self)
        self.act_save = QAction("Salvar ata e transcrição (.md)…", self)
        self.act_copy_minutes.triggered.connect(self.copy_minutes)
        self.act_copy_transcript.triggered.connect(self.copy_transcript)
        self.act_save.triggered.connect(self._save_clicked)
        self.docx = DocxSaver(self, meeting_id, gather=self._export_data, saved=self._saved, message=self.show_message,
                              ask_path=lambda data: self._ask_path("Salvar em .docx", data, ".docx", "Documento do Word"))
        menu = QMenu(self)
        menu.setToolTipsVisible(True)                    # a dica do item .docx sem o extra (A-18)
        for act in (self.act_copy_minutes, self.act_copy_transcript):
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(self.act_save)
        menu.addAction(self.docx.action)
        self.btn_export = QToolButton()
        self.btn_export.setText("Exportar")              # a seta do menu é do próprio Qt (com "▾" no texto saíam duas)
        self.btn_export.setToolTip("Copiar para a área de transferência ou salvar um arquivo .md ou .docx")
        self.btn_export.setMenu(menu)
        self.btn_export.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.info = QLabel("")
        self.info.setObjectName("meetingInfo")
        self.info.setTextFormat(Qt.TextFormat.PlainText)        # participantes: nomes do usuário (R7-07)
        self.info.setWordWrap(True)
        self.message = QLabel("")
        self.message.setObjectName("meetingMessage")
        self.message.setTextFormat(Qt.TextFormat.PlainText)     # o caminho salvo leva o título
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        self.language_bar = MeetingLanguage(store, meeting_id, on_launch=on_launch, on_message=self.show_message, parent=self)
        top = QHBoxLayout()
        top.addWidget(self.llm.title_tab, 1)
        top.addWidget(self.btn_review)
        top.addWidget(self.btn_export)
        facts = QHBoxLayout()
        facts.addWidget(self.info, 1)
        facts.addWidget(self.language_bar)
        facts.addWidget(self.llm.cost)

        # --- abas ---
        self.transcript = TranscriptTab(store, meeting_id, player=player, on_launch=on_launch, title=self.title_text)
        self.markers = MarkersTab(store, meeting_id, play=self._play_marker)
        self.timeline = TimelineTab(store, meeting_id, rows=lambda: self.transcript.model.rows,
                                    play=self._play_point if player is not None else None)
        self.transcript.model.modelReset.connect(self.timeline.reload)        # as falas mudaram (recarga, revisada/original)
        self.chat = ChatPanel(store, meeting_id, player=player, title=self.title_text, on_answer=on_chat_answer)
        summary = QSplitter(Qt.Orientation.Vertical)
        for kind, label in ((SummaryKind.EXECUTIVE, "Resumo executivo"), (SummaryKind.PRE_SUMMARY, "Pré-resumo")):
            box = QGroupBox(label)
            inner = QVBoxLayout(box)
            inner.setContentsMargins(4, 4, 4, 4)
            inner.addWidget(self.llm.text_tabs[str(kind)])
            summary.addWidget(box)
        self.tabs = QTabWidget()
        for widget, title in zip((self.llm.text_tabs[str(SummaryKind.MINUTES)], summary, self.llm.actions_tab, self.transcript,
                                  self.markers, self.timeline, self.chat), TAB_TITLES, strict=True):
            self.tabs.addTab(widget, title)
        self.bar = PlayerBar(player, meeting_id, title=self.title_text, start=self.transcript.current_play_from) if player else None
        self.follower = self._follow(player)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(facts)
        lay.addWidget(self.message)
        lay.addWidget(self.tabs, 1)
        if self.bar is not None:
            lay.addWidget(self.bar)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Close), self, self.close)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save_geometry)
        self._restoring = True
        self._restore_geometry(cascade)
        self._restoring = False
        self.llm.reload()
        self.reload_header()
        self.tabs.setCurrentWidget(self.transcript if self.transcript.model.rowCount() else self.llm.text_tabs[str(SummaryKind.MINUTES)])

    def _follow(self, player: Player | None) -> PlaybackFollower | None:
        """U-025: o destaque da Transcrição e o cursor da linha do tempo seguem o som desta reunião (o mesmo sinal)."""
        if player is None:
            return None
        follower = PlaybackFollower(player, self.meeting_id, self)
        for slot in (self.transcript.show_playing, self.timeline.set_cursor):
            follower.moved.connect(slot)
            slot(follower.position)
        return follower

    # ---------------------------------------------------------------- leitura
    def title_text(self) -> str:
        m = self.store.get_meeting(self.meeting_id) or {}
        return str(m.get("title") or m.get("title_llm") or f"Reunião {self.meeting_id}")

    def reload_header(self) -> None:
        m = self.store.get_meeting(self.meeting_id) or {}
        title = self.title_text()
        self.setWindowTitle(f"{title} — reunião")
        people = review.participants(self.store, self.meeting_id)
        if not people and m.get("expected_participants"):
            people = [str(m["expected_participants"])]
        facts = [export.fmt_datetime(m.get("started_at")), export.fmt_duration(m.get("duration_ms")),
                 f"Participantes: {', '.join(people)}" if people else ""]
        self.info.setText(" · ".join(f for f in facts if f))
        self.language_bar.reload()

    def reload(self) -> None:
        """Um job da reunião terminou (LLM ou índice): recarrega sem roubar o foco nem mexer na rolagem da transcrição."""
        try:
            self.llm.reload()
            if not self.transcript.reload():             # refez a lista: a linha do tempo já releu tudo pelo `modelReset`
                self.timeline.reload()                   # falas iguais: marcadores e pausas podem ter mudado (uma vez só)
            self.markers.reload()
            self.chat.reload()
            self.reload_header()
        except Exception as e:  # a reunião sumiu ou o banco fechou: a janela fica como está
            log.debug("reunião %s: recarga: %s", self.meeting_id, e)

    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _edited(self) -> None:
        if self.on_edited is not None:
            self.on_edited(self.meeting_id, "edit")
        self.reload_header()

    # ---------------------------------------------------------------- navegação
    def show_tab(self, key: str | None) -> None:
        if key in TAB_KEYS:
            self.tabs.setCurrentIndex(TAB_KEYS.index(key))

    def open_at(self, t_ms: int, play: bool = False) -> review.TranscriptRow | None:
        """"Abrir neste ponto": a transcrição na fala de início mais perto de `t_ms`, selecionada e no meio da tela; com
        `play`, toca do começo dela (primeira palavra menos 150 ms) ou, sem fala, de `t_ms` menos 150 ms."""
        self.show_tab("transcricao")
        row = self.transcript.select_time(t_ms)
        if play and self.player is not None:
            start = row.play_from_ms if row is not None else max(0, int(t_ms) - review.PLAY_LEAD_MS)
            self.player.play_from(self.meeting_id, start, title=self.title_text())
        return row

    def _play_marker(self, t_ms: int) -> None:
        if self.player is not None:
            self.player.play_from(self.meeting_id, max(0, t_ms - review.PLAY_LEAD_MS), title=self.title_text())

    def _play_point(self, t_ms: int) -> None:
        """O clique na linha do tempo: toca do ponto já calculado (o instante exato, ou o marcador menos 150 ms, A-19)."""
        if self.player is not None:
            self.player.play_from(self.meeting_id, t_ms, title=self.title_text())

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

    def _ask_path(self, title: str, data: export.ExportMeeting, ext: str, kind: str) -> str | None:
        """O destino escolhido pelo usuário (nome padrão "AAAA-MM-DD título" em Documentos), com a extensão garantida."""
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(self, title, str(Path(folder) / export.default_filename(data, ext)), f"{kind} (*{ext})")
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
            QMessageBox.warning(self, "Exportar", export.save_error_text(path, e))
            return None
        self._saved(out)
        return out

    def _saved(self, out: Path) -> None:
        with suppress(OSError):
            log.info("reunião %s: exportada em %s (%s, %d bytes)", self.meeting_id, out.parent, out.suffix, out.stat().st_size)
        self.exported.append(str(out))
        self.show_message(f"Salvo em {out}.")

    # ---------------------------------------------------------------- janela
    def _restore_geometry(self, cascade: int) -> None:
        raw = self.store.get_setting("ui.meeting_geometry")
        pos: tuple[int, int] | None = None
        try:
            x, y, w, h = (int(v) for v in raw.split(","))
            pos = (x + cascade * CASCADE_PX, y + cascade * CASCADE_PX)
        except ValueError:
            w, h = DEFAULT_SIZE
        w, h = max(MIN_SIZE[0], w), max(MIN_SIZE[1], h)
        self.resize(w, h)
        if pos is not None:
            clamped = clamp_to_screens(pos[0], pos[1], w, h, _screen_rects())
            if clamped is not None:
                self.move(*clamped)
                return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.left() + max(0, (screen.width() - w) // 2) + cascade * CASCADE_PX,
                  screen.top() + max(0, (screen.height() - h) // 3) + cascade * CASCADE_PX)

    def save_geometry(self) -> None:
        g = self.geometry()
        with suppress(Exception):                        # banco já fechado na saída do app: a posição anterior fica
            self.store.set_setting("ui.meeting_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")

    @override
    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        if not self._restoring and self.isVisible():
            self._save_timer.start()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if not self._restoring and self.isVisible():
            self._save_timer.start()

    def shutdown(self) -> None:
        """Saída do app: o chat em voo é cancelado e esperado antes de o banco fechar (F5A-32); o .docx em curso termina."""
        self.chat.shutdown()
        self.docx.shutdown()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_timer.stop()
        self.save_geometry()
        self.chat.close_panel()
        if self.player is not None:
            self.player.stop(self.meeting_id)            # fechar a reunião para o som dela (comportamento 8)
        super().closeEvent(event)
        self.closed.emit(self.meeting_id)


__all__ = ["TAB_KEYS", "MarkersTab", "MeetingWindow"]
