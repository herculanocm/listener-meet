"""Reunião aberta (F5 item 9, Q6, F5A-10, F5A-26): uma janela não modal por reunião, no lugar da janela "Transcrição". Abas
"Ata", "Resumo" (executivo e pré-resumo), "Ações" (as três do `LlmTabsGroup`, o mesmo da revisão: editar, marcas de
reprocessamento e de alerta), "Transcrição" (revisada ou original), "Marcadores" (o clique toca), "Linha do tempo" e "Chat"
(o da F3, sobre a reunião inteira); a barra do player embaixo. Abre num ponto (`open_at`): vai para a transcrição,
seleciona a fala de início mais perto, rola até ela e, pedido, toca do começo dela (sem fala, do ponto menos 150 ms).
Fechar para o som desta reunião e guarda posição e tamanho (`ui.meeting_geometry`). Avisos em texto puro: um título ou
nome com "<b>" não vira HTML (R7-07).

F5.1: a "Linha do tempo" (U-008, A-15; `ui/timeline.py`), o destaque da fala que toca na Transcrição e o cursor da linha do
tempo pelo mesmo `PlaybackFollower` (U-025). F5.3 (U-046): o idioma, cuja troca reprocessa (`ui/meeting_language.py`); a
exportação sai no idioma da ata; (A-12) a resposta do chat com a janela fora de foco chama `on_chat_answer(ok)`. F5.4 L4: a
ata, o executivo e o pré-resumo formatados (U-079, `ui/minutes_view.py`); os marcadores e o "Resumo" em
`ui/meeting_parts.py`; a janela abre dentro da área livre pelo `fit_to_screen` (A-17).

F5.11 L2: o cabeçalho numa linha, com o "⋯" e a legenda (U-102, `ui/meeting_header.py`, A-35); Ata, Ações e Resumo com a
linha de pedido no lugar da barra da F5.6 (U-103, A-23); os `[mm:ss]` da ata e o "Quando" das Ações tocam a fala mais perto,
até 5 s, como o chat (U-104, A-24); Espaço, ← e → tocam, voltam e avançam com a reunião em foco (U-105, `ui/meeting_keys.py`);
o título mexido grava ao fechar e na saída do app (A-22). L5 (U-114): a reunião processada sem fala abre na Transcrição,
dizendo qual canal ficou sem som e qual fonte estava escolhida (`ui/sound_check.py`)."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeyEvent, QKeySequence, QMoveEvent, QResizeEvent, QShortcut, QShowEvent
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from ..llm import chat_task
from ..log import get_logger
from ..model import SummaryKind
from ..pipeline import review
from ..pipeline.llm_requests import Species
from ..rag.citations import SentLine, nearest
from .chat import ChatPanel
from .common import clamp_to_screens, fit_to_screen, keep_until_destroyed
from .llm_tabs import LlmTabsGroup
from .meeting_header import MeetingHeaderMixin
from .meeting_keys import bar_keys, player_key
from .meeting_parts import MarkersTab, SummaryTab
from .meeting_suggestions import SuggestionsTab
from .player_bar import PlayerBar
from .sound_check import no_speech_text
from .timeline import PlaybackFollower
from .timeline_view import TimelineTab
from .transcript import TranscriptTab
from .widget_parts import _screen_rects

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store
    from .player import Player

log = get_logger("ui.meeting")

TAB_KEYS = ("ata", "resumo", "acoes", "transcricao", "marcadores", "sugestoes", "linha", "chat")       # F5.20: + sugestões
TAB_TITLES = ("Ata", "Resumo", "Ações", "Transcrição", "Marcadores", "Sugestões", "Linha do tempo", "Chat")
DEFAULT_SIZE = (1040, 720)
MIN_SIZE = (640, 420)
SAVE_DELAY_MS = 600
CASCADE_PX = 28                  # cada janela de reunião a mais nasce um pouco abaixo e à direita da anterior


class MeetingWindow(MeetingHeaderMixin, QWidget):
    closed = Signal(int)

    def __init__(self, store: Store, meeting_id: int, *, player: Player | None = None, on_review: Callable[[int], Any] | None = None,
                 on_launch: Callable[[bool], Any] | None = None, on_edited: Callable[..., Any] | None = None, cascade: int = 0,
                 on_chat_answer: Callable[[bool], Any] | None = None, recording: Callable[[], bool] | None = None,
                 platform: Platform | None = None, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.store = store
        self.meeting_id = meeting_id
        self.player = player
        self.on_review = on_review
        self.recording = recording                       # F5.19 (A-25): o controlador diz se há gravação em curso
        self.on_edited = on_edited
        self.setMinimumSize(*MIN_SIZE)
        self.llm = LlmTabsGroup(store, meeting_id, on_launch=on_launch, on_edited=self._edited, on_message=self.show_message,
                                compact_title=True, parent=self)
        self.transcript = TranscriptTab(store, meeting_id, player=player, on_launch=on_launch, title=self.title_text)
        head = self._build_header(on_launch)             # U-102: a linha do título e a legenda (`ui/meeting_header.py`)

        # --- abas ---
        self.markers = MarkersTab(store, meeting_id, play=self._play_marker)
        self.suggestions = SuggestionsTab(store, meeting_id, play=self._play_point if player is not None else None,
                                          platform=platform)   # F5.20; F5.21: a cópia da tela fora da captura
        self.timeline = TimelineTab(store, meeting_id, rows=lambda: self.transcript.model.rows,
                                    play=self._play_point if player is not None else None)
        self.transcript.model.modelReset.connect(self.timeline.reload)        # as falas mudaram (recarga, revisada/original)
        self.chat = ChatPanel(store, meeting_id, player=player, title=self.title_text, on_answer=on_chat_answer)
        self.tabs = QTabWidget()
        pages = self._request_pages()
        for widget, title in zip((*pages, self.transcript, self.markers, self.suggestions, self.timeline, self.chat), TAB_TITLES,
                                 strict=True):
            self.tabs.addTab(widget, title)
        m = store.get_meeting(meeting_id) or {}
        self.bar = PlayerBar(player, meeting_id, title=self.title_text, start=self.transcript.current_play_from,
                             duration_ms=m.get("duration_ms"), keys=True) if player else None
        self.transcript.player_keys = bar_keys(self.bar) if self.bar is not None else {}    # P-06 e R-02: na lista
        self.follower = self._follow(player)

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(self.message_bar)
        lay.addWidget(self.media.bar)                    # F5.19 (A-15): o progresso da exportação de mídia, com "Cancelar"
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
        silent = "" if self.transcript.model.rowCount() else no_speech_text(store, meeting_id)   # F5.11 (U-114)
        if silent:
            self.transcript.set_empty_text(silent)
        self.tabs.setCurrentWidget(self.transcript if self.transcript.model.rowCount() or silent else pages[0])
        self.focus_content()
        keep_until_destroyed(self)                       # R-06: a coleta de ciclos não a apaga antes do Qt

    def _request_pages(self) -> tuple[QWidget, QWidget, QWidget]:
        """U-103 (A-23): Ata, Resumo e Ações com a linha de pedido da espécie em cima; o botão da linha é o pedido da aba. O
        `[mm:ss]` da ata e o "Quando" das Ações tocam (U-104)."""
        tabs = self.llm.text_tabs
        minutes = tabs[str(SummaryKind.MINUTES)]
        minutes.on_play = self.play_cited
        self.llm.actions_tab.play = self.play_cited
        summary = SummaryTab(tabs)
        return (self.llm.add_page(minutes, Species.MINUTES, (minutes,)),
                self.llm.add_page(summary, Species.SUMMARY, (tabs[str(SummaryKind.EXECUTIVE)], tabs[str(SummaryKind.PRE_SUMMARY)])),
                self.llm.add_page(self.llm.actions_tab, Species.MINUTES))

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
    @override
    def title_text(self) -> str:
        m = self.store.get_meeting(self.meeting_id) or {}
        return str(m.get("title") or m.get("title_llm") or f"Reunião {self.meeting_id}")

    @override
    def reload_header(self) -> None:
        super().reload_header()
        if self.bar is not None:
            self.bar.set_duration((self.store.get_meeting(self.meeting_id) or {}).get("duration_ms"))

    def reload(self) -> None:
        """Um job da reunião terminou (LLM ou índice): recarrega sem roubar o foco nem mexer na rolagem da transcrição."""
        try:
            self.llm.reload()
            if not self.transcript.reload():             # refez a lista: a linha do tempo já releu tudo pelo `modelReset`
                self.timeline.reload()                   # falas iguais: marcadores e pausas podem ter mudado (uma vez só)
            self.markers.reload()
            self.suggestions.reload()
            self.chat.reload()
            self.reload_header()
        except Exception as e:  # a reunião sumiu ou o banco fechou: a janela fica como está
            log.debug("reunião %s: recarga: %s", self.meeting_id, e)

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

    def play_cited(self, t_ms: int) -> None:
        """U-104 (A-24): o `[mm:ss]` da ata ou o "Quando" das Ações toca a fala mais perto do tempo, até 5 s (a regra do chat),
        da primeira palavra menos 150 ms; sem fala por perto, do próprio tempo menos 150 ms."""
        if self.player is None:
            self.show_message("Reprodução indisponível nesta janela.")
            return
        lines = [SentLine(r.t_start_ms, r.segment_id) for r in self.transcript.model.rows]
        best = nearest(t_ms, lines)
        rng = chat_task.play_range(self.store, best.segment_id) if best is not None and best.segment_id is not None else None
        start = rng[1] if rng is not None else max(0, t_ms - review.PLAY_LEAD_MS)
        self.player.play_from(self.meeting_id, start, title=self.title_text())

    def _play_marker(self, t_ms: int) -> None:
        if self.player is not None:
            self.player.play_from(self.meeting_id, max(0, t_ms - review.PLAY_LEAD_MS), title=self.title_text())

    def _play_point(self, t_ms: int) -> None:
        """O clique na linha do tempo: toca do ponto já calculado (o instante exato, ou o marcador menos 150 ms, A-19)."""
        if self.player is not None:
            self.player.play_from(self.meeting_id, t_ms, title=self.title_text())

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """U-105: Espaço, ← e → do player, com a tecla que nenhum widget usou e o foco fora de quem a usa (A-38)."""
        if player_key(self, self.bar, event):
            event.accept()
            return
        super().keyPressEvent(event)

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
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        fit_to_screen(self)                              # A-17: a janela inteira, com a moldura, na área livre

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

    def focus_content(self) -> None:
        """R-02 (F5.11): o foco nasce no conteúdo da aba (a lista de falas, a leitura da ata), não no título: o Espaço é do
        player, não de um campo de texto."""
        page = self.tabs.currentWidget()
        target = self.transcript.view if page is self.transcript else next(
            (w for w in page.findChildren(QWidget) if w.focusPolicy() & Qt.FocusPolicy.TabFocus and not w.isHidden()), page)
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _save_title(self) -> None:
        """A-22: o título mexido grava ao fechar e na saída; o banco que já não responde vai para o log e o resto segue."""
        try:
            self.llm.title_tab.save_if_changed()
        except Exception:                                # o fechar e a saída seguem; o motivo fica no log
            log.warning("reunião %s: o título mexido não gravou", self.meeting_id, exc_info=True)

    def shutdown(self) -> None:
        """Saída do app: o título mexido grava (A-22); o chat em voo é cancelado e esperado antes de o banco fechar (F5A-32);
        a exportação de áudio ou vídeo é cancelada e esperada antes do .docx, que termina (F5.19, A-20)."""
        self._save_title()
        self.chat.shutdown()
        self.media.shutdown()
        self.docx.shutdown()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.media.confirm_close():               # F5.19 (U-152): o áudio ou o vídeo em curso pergunta antes
            event.ignore()
            return
        self._save_title()                               # A-22: fechar com o título mexido grava
        self._save_timer.stop()
        self.save_geometry()
        self.chat.close_panel()
        if self.player is not None:
            self.player.stop(self.meeting_id)            # fechar a reunião para o som dela (comportamento 8)
        super().closeEvent(event)
        self.closed.emit(self.meeting_id)


__all__ = ["TAB_KEYS", "MarkersTab", "MeetingWindow"]
