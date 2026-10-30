"""A barra do player da reunião aberta (F5 L4), saída do `ui/player.py`, que passava de 400 linhas. F5.4 L4 (U-083): Tocar e
Pausar, -10 s e +10 s com o ícone junto do texto (o texto fica: o leitor de tela e os testes leem); o tempo em algarismos
de largura fixa (`tnum` da fonte do tema) numa coluna de largura fixa, sem empurrar o resto enquanto toca."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ..capture.clock import fmt_mmss
from .icons import set_icon
from .player import ERROR, PAUSED, PLAYING, SEEK_MS, STATE_TEXT, PlayerState
from .theme import Tone, font_for, qcolor, surface_tokens, tone

if TYPE_CHECKING:
    from .player import Player

TIME_SAMPLE = "000:00 / 000:00"      # a coluna do tempo cabe reuniões de até 999 min
EMPTY_TIME = "--:-- / --:--"


def tabular(font: QFont) -> QFont:
    """A fonte com os algarismos de largura igual (o "1" da Segoe UI Variable é mais estreito sem o `tnum`)."""
    out = QFont(font)
    out.setFeature(QFont.Tag.fromString("tnum"), 1)
    return out


class PlayerBar(QWidget):
    """Barra do player na reunião aberta: Tocar/Pausar, -10 s, +10 s, "mm:ss / mm:ss" e o título do que toca; o aviso do
    player (sem dispositivo, sem áudio, pausado para a gravação) numa linha discreta. "Tocar" com outra reunião (ou nada)
    carregada começa esta reunião em `start()` (a fala selecionada). Título e aviso em texto puro: um título com "<b>" não
    vira HTML (R7-07)."""

    def __init__(self, player: Player, meeting_id: int | None = None, *, title: Callable[[], str] | None = None,
                 start: Callable[[], int] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.player = player
        self.meeting_id = meeting_id
        self.title_fn = title
        self.start_fn = start
        self.setObjectName("playerBar")
        self.btn_play = QPushButton("Tocar")
        self.btn_play.setToolTip("Toca a reunião a partir da fala selecionada (ou pausa e retoma)")
        self.btn_back = QPushButton("-10 s")
        self.btn_back.setToolTip("Volta 10 segundos")
        self.btn_fwd = QPushButton("+10 s")
        self.btn_fwd.setToolTip("Avança 10 segundos")
        set_icon(self.btn_play, "play")
        set_icon(self.btn_back, "back-10")
        set_icon(self.btn_fwd, "forward-10")
        self.time = QLabel(EMPTY_TIME)
        self.time.setObjectName("playerTime")
        self.time.setFont(tabular(font_for("body")))
        self.time.setFixedWidth(QFontMetrics(self.time.font()).horizontalAdvance(TIME_SAMPLE) + 8)
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.what = QLabel("")
        self.what.setTextFormat(Qt.TextFormat.PlainText)
        tone(self.what, Tone.SECONDARY)
        self.what.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.note = QLabel("")
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        tone(self.note, Tone.WARNING)
        self.note.setWordWrap(True)
        self.btn_play.clicked.connect(self.toggle)
        self.btn_back.clicked.connect(self.back)
        self.btn_fwd.clicked.connect(self.forward)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        for w in (self.btn_play, self.btn_back, self.btn_fwd, self.time):
            row.addWidget(w)
        row.addWidget(self.what, 1)
        row.addWidget(self.note, 1)
        player.state_changed.connect(self._on_state)
        player.position_changed.connect(self._on_position)
        self.show_state(player.state())

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """A linha de cima da barra no traço do tema (F5.4: no lugar da borda da folha de estilo)."""
        p = QPainter(self)
        p.setPen(qcolor(surface_tokens(self).stroke))
        p.drawLine(0, 0, self.width(), 0)
        p.end()

    def mine(self, st: PlayerState) -> bool:
        return self.meeting_id is None or st.meeting_id == self.meeting_id

    def toggle(self) -> None:
        st = self.player.state()
        if st.active and self.mine(st):
            self.player.toggle()
        elif self.meeting_id is not None:
            self.player.play_from(self.meeting_id, self.start_fn() if self.start_fn is not None else 0,
                                  title=self.title_fn() if self.title_fn is not None else "")
        elif st.state == PAUSED:
            self.player.toggle()

    def seek(self, delta_ms: int) -> None:
        if self.mine(self.player.state()):
            self.player.seek(delta_ms)

    def back(self) -> None:
        self.seek(-SEEK_MS)

    def forward(self) -> None:
        self.seek(SEEK_MS)

    def _on_state(self, st: object) -> None:
        if isinstance(st, PlayerState):
            self.show_state(st)

    def _on_position(self, st: object) -> None:
        if isinstance(st, PlayerState) and self.mine(st) and st.state != ERROR and st.meeting_id is not None:
            self.time.setText(f"{fmt_mmss(st.t_ms / 1000)} / {fmt_mmss((st.duration_ms or 0) / 1000)}")

    def show_state(self, st: PlayerState) -> None:
        mine = self.mine(st)
        playing = st.state == PLAYING and mine
        text = "Pausar" if playing else "Tocar"
        if self.btn_play.text() != text:                 # o ícone só troca com o estado (a posição chega a cada 100 ms)
            self.btn_play.setText(text)
            set_icon(self.btn_play, "pause" if playing else "play")
        self.btn_back.setEnabled(st.active and mine)
        self.btn_fwd.setEnabled(st.active and mine)
        if mine and st.meeting_id is not None and st.state != ERROR:
            self.time.setText(f"{fmt_mmss(st.t_ms / 1000)} / {fmt_mmss((st.duration_ms or 0) / 1000)}")
        else:
            self.time.setText(EMPTY_TIME)
        if st.active:
            prefix = "" if mine else "outra reunião: "
            self.what.setText(f"{prefix}{STATE_TEXT[st.state]} · {st.title}" if st.title else f"{prefix}{STATE_TEXT[st.state]}")
        else:
            self.what.setText("")
        # o motivo do erro e o aviso da pausa pela gravação (R7-01) na barra da reunião que tocava; D-067: a saída que não
        # acompanhou o padrão do Windows, tocando ou pausado
        text = st.message if st.state in (ERROR, PAUSED) and (mine or st.meeting_id is None) else ""
        self.note.setText(text or (st.note if mine and st.active else ""))


__all__ = ["EMPTY_TIME", "PlayerBar", "tabular"]
