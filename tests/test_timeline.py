"""F5.1 L7, linha do tempo (U-008; A-15, A-16, A-19) e destaque contínuo (U-025; A-17), com o player e os fluxos falsos
(nada de áudio real): as faixas por pessoa ("Você" primeiro, as outras por tempo de fala, a voz sem pessoa pelo rótulo,
o mesmo rótulo em dois canais separado), as funções puras do desenho (falas visíveis por `searchsorted`, barra sob o
mouse, barras de 2 px que se juntam, passo do eixo, a fala que toca por `bisect`), o seguidor da posição (extrapola só
quando o som anda, pausado fica, parado ou outra reunião some), o clique que toca do instante exato e o marcador de
150 ms antes, `Ctrl`+roda ancorado no mouse até 20 vezes, a rolagem que segue o cursor salvo nos 5 s depois do usuário, as
dicas em texto puro, a reunião sem falas e, na Transcrição, o destaque sem mexer na seleção, sem `dataChanged`, reaplicado
na recarga e que só rola quando o usuário não rolou nos últimos 5 s. Revisão R9: o cursor sem duração não estoura o `int`
(R9-02); o destaque chega à tela e a dica chega escapada, conferidos pelo que a plataforma mostra (R9-04, os dois falham
com o método mutado); a recarga da reunião relê a linha do tempo uma vez só (R9-06)."""
from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.model import EventType
from listener.pipeline.review import TranscriptRow
from listener.store.segments import SegmentInsert
from tests.fake_player import FakeOutputs
from tests.test_rag_index import _meeting, _people, _pipeline_job

pytest.importorskip("PySide6")


def _row(seg: int, t0: int, t1: int, name: str, person: int | None, channel: str = "R", role: str = "", text: str = "") -> TranscriptRow:
    return TranscriptRow(seg, channel, t0, t1, name, role, text or f"fala {seg}", max(0, t0 - 150), None, True, person)


def _audio(store, mid: int, seconds: int = 300) -> None:
    for ch, value in (("L", 0.2), ("R", 0.6)):
        x = np.full(16000 * seconds, value, np.float32)
        store.insert_chunk(mid, ch, 0, 0, len(x), 16000, 0, encode_flac(x, 16000))


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------- funções puras
def test_lanes_put_you_first_then_by_speech_time_and_keep_voices_apart():
    from listener.ui.timeline import YOU, build_lanes

    rows = [_row(1, 0, 4_000, "Eu", 9, "L", "Gerente"), _row(2, 1_000, 40_000, "Ana", 1, role="CFO"),
            _row(3, 41_000, 50_000, "Voz SPK_1", None), _row(4, 50_000, 51_000, "Voz SPK_1", None, "L"),
            _row(5, 52_000, 70_000, "Bia", 2), _row(6, 71_000, 90_000, "Ana", 1, "L", "CFO")]
    lanes = build_lanes(rows, me_id=9)
    assert [ln.label for ln in lanes] == [YOU, "Ana", "Bia", "Voz SPK_1 (canal R)", "Voz SPK_1 (canal L)"]
    you, ana = lanes[0], lanes[1]
    assert you.mine and you.title == "Eu — Gerente" and you.rows == (0,)
    assert ana.rows == (1, 5) and ana.speech_ms == 39_000 + 19_000 and ana.title == "Ana — CFO"     # os dois canais juntos
    assert [ln.label for ln in build_lanes(rows, me_id=None)][:2] == ["Ana", "Bia"]                     # sem "você": por tempo
    assert build_lanes([], me_id=9) == []
    long = build_lanes([_row(1, 0, 100_000, "Ana", 1), _row(2, 10_000, 12_000, "Ana", 1)], None)[0]
    assert long.reach.tolist() == [100_000, 100_000]                                    # o maior fim até cada fala


def test_visible_slice_bar_under_the_mouse_spans_and_axis_steps():
    from listener.ui.timeline import bar_at, build_lanes, merge_spans, opening, tick_step_ms, visible_slice

    lane = build_lanes([_row(1, 0, 100_000, "Ana", 1), _row(2, 10_000, 12_000, "Ana", 1), _row(3, 120_000, 130_000, "Ana", 1)], None)[0]
    assert visible_slice(lane, 50_000, 60_000) == (0, 2)       # a fala longa cruza a vista; a curta acabou antes, mas vem junto
    assert visible_slice(lane, 101_000, 110_000) == (2, 2)     # nada visível
    assert visible_slice(lane, 125_000, 200_000) == (2, 3)
    assert bar_at(lane, 11_000, 0) == 1 and bar_at(lane, 50_000, 0) == 0 and bar_at(lane, 110_000, 0) is None
    assert bar_at(lane, 119_000, 1_500) == 2                   # a folga acha a barra fina
    assert merge_spans([0.0, 0.5, 1.0, 10.0], [0.2, 0.6, 1.1, 30.0]) == [(0.0, 3.0), (10.0, 30.0)]    # 2 px no mínimo, juntas
    assert merge_spans([], []) == []
    assert tick_step_ms(1000.0) == 120_000 and tick_step_ms(10.0) == 1_000 and tick_step_ms(1e9) == 7_200_000
    assert opening("a " * 100, 20).endswith("…") and len(opening("a " * 100, 20)) <= 21 and opening(" curta  ") == "curta"


def test_the_playing_line_comes_from_bisect():
    from listener.ui.transcript import playing_row

    starts, ends = [0, 10_000, 12_000, 30_000], [40_000, 11_000, 20_000, 35_000]
    reach = [40_000, 40_000, 40_000, 40_000]
    assert playing_row(starts, ends, reach, -1) is None                                  # antes da primeira fala
    assert playing_row(starts, ends, reach, 10_500) == 1                                 # a de início mais tarde que segue
    assert playing_row(starts, ends, reach, 11_500) == 0                                 # a curta acabou: a longa por baixo
    assert playing_row(starts, ends, reach, 13_000) == 2
    assert playing_row([0, 10_000], [5_000, 12_000], [5_000, 12_000], 7_000) == 0         # entre falas: a última que começou
    assert playing_row([0, 10_000], [5_000, 12_000], [5_000, 12_000], 60_000) == 1
    assert playing_row([], [], [], 5) is None


# ---------------------------------------------------------------- o seguidor (U-025)
def test_the_follower_extrapolates_only_while_the_sound_advances(qtbot, store):
    from listener.ui.player import PAUSED, PLAYING, STOPPED, Player, PlayerState
    from listener.ui.timeline import EXTRAP_MAX_MS, PlaybackFollower, follow_position

    assert follow_position(1000, 10.0, 10.25, True) == 1250 and follow_position(1000, 10.0, 12.0, True) == 1000 + EXTRAP_MAX_MS
    assert follow_position(1000, 10.0, 12.0, False) == 1000
    player = Player(store, FakeOutputs())
    clock = Clock()
    seen: list[int | None] = []
    follower = PlaybackFollower(player, 7, clock=clock)
    follower.moved.connect(seen.append)
    assert follower.position is None                                                    # parado: nada
    st = lambda state, t, mid=7: PlayerState(state, mid, t, 60_000)                      # noqa: E731
    follower.on_state(st(PLAYING, 1_000))                                               # começo: ainda sem áudio saindo
    clock.now += 0.3
    follower._tick()
    assert follower.position == 1_000                                                   # não corre na frente do som
    clock.now += 0.2
    follower.on_state(st(PLAYING, 1_070))                                               # o primeiro bloco saiu
    clock.now += 0.1
    follower._tick()
    assert follower.position == 1_170                                                   # extrapolado pelo relógio
    clock.now += 2.0
    follower._tick()
    assert follower.position == 1_070 + EXTRAP_MAX_MS                                   # com teto
    follower.on_state(st(PLAYING, 11_070))                                              # +10 s: salto, não extrapola
    clock.now += 0.2
    follower._tick()
    assert follower.position == 11_070 and follower._timer.isActive()
    follower.on_state(st(PAUSED, 11_300))
    assert follower.position == 11_300 and not follower._timer.isActive()               # pausado: fica
    follower.on_state(st(PLAYING, 0, mid=8))
    assert follower.position is None                                                    # outra reunião: some
    follower.on_state(st(PLAYING, 2_000))
    follower.on_state(st(STOPPED, 2_000))
    assert follower.position is None and seen[-1] is None and 11_300 in seen
    player.shutdown()


# ---------------------------------------------------------------- a aba e a vista
def _window(qtbot, store, *, audio: bool = False, player: Any = "fake", markers: bool = True):
    from listener.ui.meeting import MeetingWindow
    from listener.ui.player import Player

    who = _people(store)
    mid, _job = _meeting(store, who)
    if markers:
        store.add_marker(mid, 70_000, "auditoria <b>sexta</b> & cia")
        store.add_event(mid, 262_000, EventType.PAUSE)
        store.add_event(mid, 280_000, EventType.RESUME)
    if audio:
        _audio(store, mid)
    pl = Player(store, FakeOutputs(permits=threading.Semaphore(0))) if player == "fake" else player
    win = MeetingWindow(store, mid, player=pl)
    qtbot.addWidget(win)
    win.resize(1040, 720)
    win.show()
    win.show_tab("linha")
    qtbot.waitExposed(win)
    return win, mid, pl, who


def test_timeline_tab_lanes_click_plays_the_exact_point_and_markers_150_ms_before(qtbot, store):
    from PySide6.QtCore import QPoint, Qt

    win, mid, player, _who = _window(qtbot, store)
    try:
        tab, view = win.timeline, win.timeline.view
        assert win.tabs.currentWidget() is tab and [ln.label for ln in view.lanes] == ["Bia", "Ana"]     # Bia fala mais
        assert view.duration_ms == 300_000 and view.pauses == [(262_000, 280_000)]
        assert view.markers == [(70_000, "auditoria <b>sexta</b> & cia")]
        assert tab.note.text().startswith("2 falante(s), 6 fala(s)")
        y = view._lane_top(1) + 5                                                   # a faixa da Ana
        x = view.x_of(45_000)
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(round(x), y))
        assert player.requests[-1].meeting_id == mid and player.requests[-1].t_ms == view.t_of(round(x))
        assert abs(player.requests[-1].t_ms - 45_000) <= 500                       # aceite: a ±0,5 s do ponto
        qtbot.waitUntil(lambda: "não tem áudio gravado" in win.bar.note.text(), timeout=3000)   # sem áudio: avisa como o player
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(round(view.x_of(70_000)) + 2, y))
        assert player.requests[-1].t_ms == 69_850                                  # o marcador, 150 ms antes
        n = len(player.requests)
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, y))     # a coluna dos rótulos não toca
        assert len(player.requests) == n and list(view.played)[-2:] == [player.requests[-2].t_ms, 69_850]
        # dicas em texto puro: falante, tempo e começo; marcador com a nota como está; pausa; o instante
        tip = view.tooltip_at(view.x_of(15_000), y)
        assert tip == "Ana — CFO · 00:00 a 00:30\nBom dia, vamos falar do orçamento de 2027."
        assert view.tooltip_at(view.x_of(70_000), y) == "Marcador [01:10]: auditoria <b>sexta</b> & cia\nclique para ouvir deste ponto"
        assert view.tooltip_at(view.x_of(270_000), y).startswith("Gravação pausada de 04:22 a 04:40")
        assert view.tooltip_at(10, y) == "Ana — CFO · 2 min de fala"
        assert view.tooltip_at(view.x_of(280_500), y).endswith("clique para ouvir daqui")
    finally:
        player.shutdown()


def test_ctrl_wheel_zooms_on_the_mouse_up_to_20_times_and_the_view_follows_the_cursor(qtbot, store):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    from listener.ui.timeline_view import FOLLOW_PAUSE_S, ZOOM_MAX

    win, _mid, player, _who = _window(qtbot, store)
    try:
        view = win.timeline.view
        clock = Clock()
        view.clock = clock
        x = view.x_of(100_000)

        def wheel(dy: int, mods: Qt.KeyboardModifier, at: float) -> None:
            pos = QPointF(at, 40)
            ev = QWheelEvent(pos, QPointF(view.viewport().mapToGlobal(QPoint(int(at), 40))), QPoint(0, 0), QPoint(0, dy),
                             Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(view.viewport(), ev)

        wheel(120 * 6, Qt.KeyboardModifier.ControlModifier, x)
        assert view.zoom == pytest.approx(1.25 ** 6) and view.horizontalScrollBar().maximum() > 0
        assert abs(view.x_of(100_000) - x) <= 1.0                                  # ancorado no mouse
        for _ in range(10):
            wheel(120 * 4, Qt.KeyboardModifier.ControlModifier, x)
        assert view.zoom == ZOOM_MAX and abs(view.x_of(100_000) - x) <= 1.0
        # ampliada e tocando: a vista vira a página atrás do cursor, salvo nos 5 s depois de o usuário rolar ou ampliar
        bar = view.horizontalScrollBar()
        before = bar.value()
        view.set_cursor(250_000)
        assert bar.value() == before                                              # acabou de ampliar: não segue
        clock.now += FOLLOW_PAUSE_S
        view.set_cursor(260_000)
        assert bar.value() != before and view._label_w <= view.x_of(260_000) <= view.viewport().width()
        wheel(-120, Qt.KeyboardModifier.NoModifier, x)                             # roda sem Ctrl: rola na horizontal
        assert view._user_scroll_at == clock.now
        moved = bar.value()
        view.set_cursor(10_000)
        assert bar.value() == moved                                               # o usuário rolou: 5 s sem seguir
        wheel(-120 * 30, Qt.KeyboardModifier.ControlModifier, x)
        assert view.zoom == 1.0 and bar.maximum() == 0
    finally:
        player.shutdown()


def test_a_meeting_without_speech_shows_the_notice_and_still_plays_a_point(qtbot, store):
    from PySide6.QtCore import QPoint, Qt

    from listener.ui.meeting import MeetingWindow
    from listener.ui.player import Player
    from listener.ui.timeline_view import CLICK_TEXT, EMPTY_TEXT, NO_PLAYER_TEXT, TimelineTab

    mid = store.create_meeting("Sem falas", None, None, None)
    store.finish_meeting(mid, 120_000)
    _pipeline_job(store, mid)
    player = Player(store, FakeOutputs())
    win = MeetingWindow(store, mid, player=player)
    qtbot.addWidget(win)
    try:
        win.resize(900, 600)
        win.show()
        win.show_tab("linha")
        qtbot.waitExposed(win)
        view = win.timeline.view
        # F5.4 L4 (U-034): o "sem falas" fica no meio da vista; com áudio, o rodapé ensina o clique, sem repetir o aviso
        assert view.lanes == [] and view.duration_ms == 120_000
        assert win.timeline.note.text() == CLICK_TEXT and win.timeline.note.text() != EMPTY_TEXT
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(round(view.x_of(60_000)), 80))
        assert abs(player.requests[-1].t_ms - 60_000) <= 500
        bare = TimelineTab(store, mid, rows=list)                                  # sem player: o clique avisa
        qtbot.addWidget(bare)
        bare.view.play(1000)
        assert bare.note.text() == NO_PLAYER_TEXT
    finally:
        player.shutdown()


# ---------------------------------------------------------------- destaque na Transcrição (U-025, A-17)
def test_the_playing_line_is_highlighted_without_touching_the_selection_and_follows_the_player(qtbot, store):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush

    from listener.ui.player import PAUSED, PLAYING, STOPPED, PlayerState

    win, mid, player, _who = _window(qtbot, store)
    try:
        tab, model = win.transcript, win.transcript.model
        win.show_tab("transcricao")
        tab.view.setCurrentIndex(model.index(0))
        changed: list[object] = []
        model.dataChanged.connect(lambda *a: changed.append(a))
        player.state_changed.emit(PlayerState(PLAYING, mid, 75_000, 300_000))
        assert model.highlight == 2 and isinstance(model.data(model.index(2), Qt.ItemDataRole.BackgroundRole), QBrush)
        assert model.data(model.index(1), Qt.ItemDataRole.BackgroundRole) is None and tab.current_row() == model.rows[0]
        assert win.timeline.view.cursor_ms is not None and abs(win.timeline.view.cursor_ms - 75_000) <= 500
        player.position_changed.emit(PlayerState(PLAYING, mid, 99_900, 300_000))  # 150 ms antes da próxima: já é ela
        assert model.highlight == 3
        player.state_changed.emit(PlayerState(PAUSED, mid, 105_000, 300_000))
        assert model.highlight == 3 and win.timeline.view.cursor_ms == 105_000      # pausado: fica
        tab.set_prefer_llm(False)                                                   # recarga: o destaque volta
        assert model.highlight == 3 and tab.current_row() == model.rows[0]
        player.state_changed.emit(PlayerState(PLAYING, mid + 1, 0, 1000))
        assert model.highlight is None and win.timeline.view.cursor_ms is None      # outra reunião tocando: some
        player.state_changed.emit(PlayerState(PLAYING, mid, 30_500, 300_000))
        player.state_changed.emit(PlayerState(STOPPED, mid, 300_000, 300_000))
        assert model.highlight is None                                              # parado: some
        assert changed == []                                                        # sem `dataChanged`: a lista não se refaz
    finally:
        player.shutdown()


def test_the_highlight_scrolls_into_view_except_5_s_after_the_user_scrolled(qtbot, store):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QAbstractSlider, QApplication

    from listener.ui.meeting import MeetingWindow
    from listener.ui.player import PLAYING, Player, PlayerState
    from listener.ui.transcript import FOLLOW_PAUSE_S

    who = _people(store)
    mid = store.create_meeting("Longa", None, None, None)
    store.finish_meeting(mid, 2_000_000)
    job = _pipeline_job(store, mid)
    store.segments.insert_segments([SegmentInsert(mid, job, "R", i * 10_000, i * 10_000 + 9_000, f"fala número {i} " * 4, "pyannote",
                                                  "final", person_id=who["ana" if i % 2 else "bia"]) for i in range(200)])
    player = Player(store, FakeOutputs())
    win = MeetingWindow(store, mid, player=player)
    qtbot.addWidget(win)
    try:
        win.resize(900, 500)
        win.show()
        win.show_tab("transcricao")
        qtbot.waitExposed(win)
        tab = win.transcript
        viewport = tab.view.viewport()

        def play(i: int) -> None:
            player.position_changed.emit(PlayerState(PLAYING, mid, i * 10_000 + 2_000, 2_000_000))

        play(150)
        assert tab.followed[-1] == 150 and viewport.rect().contains(tab.view.visualRect(tab.model.index(150)))
        tab.view.verticalScrollBar().triggerAction(QAbstractSlider.SliderAction.SliderToMinimum)    # o usuário rolou
        top = tab.view.verticalScrollBar().value()
        play(160)
        assert tab.model.highlight == 160 and tab.followed[-1] == 150 and tab.view.verticalScrollBar().value() == top
        tab._user_scroll_at -= FOLLOW_PAUSE_S                                       # passaram os 5 s
        play(170)
        assert tab.followed[-1] == 170 and viewport.rect().contains(tab.view.visualRect(tab.model.index(170)))
        tab._user_scroll_at = float("-inf")
        center = viewport.rect().center()
        QApplication.sendEvent(viewport, QWheelEvent(QPointF(center), QPointF(viewport.mapToGlobal(center)), QPoint(0, 0), QPoint(0, 120),
                                                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase,
                                                     False))
        assert tab._user_scroll_at > float("-inf")                                  # a roda conta como rolagem do usuário
        tab._user_scroll_at = float("-inf")
        qtbot.keyClick(tab.view, Qt.Key.Key_PageDown)
        assert tab._user_scroll_at > float("-inf")                                  # o teclado também
    finally:
        player.shutdown()


def test_a_click_on_the_timeline_plays_there_and_the_highlight_follows_the_real_engine(qtbot, store):
    from PySide6.QtCore import QPoint, Qt

    win, _mid, player, _who = _window(qtbot, store, audio=True, markers=False)
    permits = player.engine.factory.permits
    try:
        view = win.timeline.view
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(round(view.x_of(30_000)), view._lane_top(1) + 5))
        t0 = player.requests[-1].t_ms
        assert abs(t0 - 30_000) <= 500
        permits.release(8)                                                          # 2 s de áudio saem
        qtbot.waitUntil(lambda: win.follower is not None and (win.follower.position or 0) >= t0 + 1_500, timeout=5000)
        assert win.transcript.model.highlight == 1 and win.timeline.view.cursor_ms is not None   # a fala de 30 a 70 s
    finally:
        permits.release(1000)
        player.shutdown()


# ---------------------------------------------------------------- revisão R9
def test_the_cursor_without_a_duration_is_only_kept_and_never_overflows(qtbot):
    """R9-02: sem duração (gravando, sem falas nem marcadores) a escala era trilha·zoom px/ms e, passados ~40 min de som, o
    `QRect` em volta do cursor passava do limite de um `int` a cada 50 ms. Agora o cursor só é guardado; com duração, um
    cursor longe da vista não é repintado."""
    from listener.ui.timeline_view import TimelineView

    view = TimelineView(lambda _t: None)
    qtbot.addWidget(view)
    view.resize(1000, 300)
    view.show()
    view.set_data([], [], [], [], 0)
    for t in (60_000, 30 * 60_000, 45 * 60_000):
        view.set_cursor(t)
        assert view.cursor_ms == t
    view.set_data([], [], [], [], 1)                                                # 1 ms de duração: escala enorme
    view.set_cursor(None)
    view.set_cursor(45 * 60_000)
    assert view.cursor_ms == 45 * 60_000
    view.viewport().repaint()                                                      # a pintura também não estoura


def test_the_highlight_reaches_the_screen_and_leaves_it(qtbot, store):
    """R9-04: o destaque chega à tela sem `dataChanged` porque as duas linhas são repintadas. A cor é lida da imagem que a
    plataforma mostra (`QScreen.grabWindow`), não de um `grab()` do widget, que repinta tudo e esconderia a falta do
    `update` (conferido: com o `_highlight` mutado sem o `update`, este teste falha e um `grab()` passaria)."""
    from PySide6.QtCore import QPoint

    from listener.ui.player import PLAYING, STOPPED, PlayerState

    win, mid, player, _who = _window(qtbot, store)
    try:
        win.show_tab("transcricao")
        tab = win.transcript
        qtbot.waitExposed(tab.view)

        def on_screen(row: int) -> str:
            rect = tab.view.visualRect(tab.model.index(row))
            point = tab._viewport.mapTo(win, QPoint(rect.right() - 12, rect.center().y()))     # fundo, longe do texto
            image = win.screen().grabWindow(win.winId()).toImage()          # vazia até a primeira pintura chegar à tela
            x, y = round(point.x() * image.devicePixelRatio()), round(point.y() * image.devicePixelRatio())
            return image.pixelColor(x, y).name() if image.valid(x, y) else ""

        qtbot.waitUntil(lambda: on_screen(2) != "" and on_screen(2) == on_screen(0), timeout=3000)   # o mesmo fundo nas duas
        before = on_screen(2)
        player.state_changed.emit(PlayerState(PLAYING, mid, 75_000, 300_000))      # a fala 2 (70 a 100 s) toca
        qtbot.waitUntil(lambda: on_screen(2) != before, timeout=3000)
        assert on_screen(0) == before
        player.state_changed.emit(PlayerState(STOPPED, mid, 300_000, 300_000))
        qtbot.waitUntil(lambda: on_screen(2) == before, timeout=3000)               # parado: o destaque sai da tela
    finally:
        player.shutdown()


def test_the_timeline_tooltip_reaches_the_screen_as_plain_text(qtbot, store):
    """R9-04: a dica mostrada leva a nota do marcador com "<b>" e "&" como texto (`convertFromPlainText`): `QToolTip.text()`
    sai escapado e, lido como HTML, é o texto como está (conferido: sem o escape, o HTML cru chegava à dica e o teste
    falha)."""
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent, QTextDocument
    from PySide6.QtWidgets import QApplication, QToolTip

    win, _mid, player, _who = _window(qtbot, store)
    try:
        view = win.timeline.view
        pos = QPoint(round(view.x_of(70_000)), view._lane_top(0) + 5)
        QApplication.sendEvent(view.viewport(), QHelpEvent(QEvent.Type.ToolTip, pos, view.viewport().mapToGlobal(pos)))
        shown = QToolTip.text()
        assert "auditoria &lt;b&gt;sexta&lt;/b&gt; &amp; cia" in shown and "<b>sexta" not in shown
        doc = QTextDocument()
        doc.setHtml(shown)
        assert doc.toPlainText() == view.tooltip_at(pos.x(), pos.y()) == ("Marcador [01:10]: auditoria <b>sexta</b> & cia\n"
                                                                            "clique para ouvir deste ponto")
        QToolTip.hideText()
    finally:
        player.shutdown()


def test_the_meeting_reload_reloads_the_timeline_once(qtbot, store):
    """R9-06: a recarga da reunião relê a linha do tempo uma vez só: pelo `modelReset` quando as falas mudaram, pela chamada
    direta quando só os marcadores ou as pausas podem ter mudado."""
    win, mid, player, who = _window(qtbot, store)
    try:
        timeline = win.timeline
        n = timeline.reloads
        win.reload()                                                               # nada mudou
        assert timeline.reloads == n + 1
        store.add_marker(mid, 200_000, "novo")
        win.reload()                                                               # um marcador novo, as falas iguais
        assert timeline.reloads == n + 2 and (200_000, "novo") in timeline.view.markers
        job = store.segments.current_job_id(mid)
        store.segments.insert_segments([SegmentInsert(mid, job, "R", 280_000, 290_000, "Fala nova.", "pyannote", "final",
                                                      person_id=who["bia"])])
        win.reload()                                                               # as falas mudaram: só o `modelReset`
        assert timeline.reloads == n + 3 and len(timeline.view.rows) == 7
    finally:
        player.shutdown()
