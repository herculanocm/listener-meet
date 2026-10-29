"""F5.7 (L3, D-066): as correções da revisão do aviso de reunião sem gravação (pytest-qt, offscreen, com a plataforma falsa).
R-02: o "Antes de gravar" aberto à mão e cancelado no meio de uma chamada não a cobre (o sensor para e as regras só veem o
modal). R-05: o "Gravar" que não começou deixa a chamada pendente, com o lembrete único. R-06: a bandeja e o atalho escolhem
a chamada do cartão ou, sem cartão, a avisada por último. R-07: "Não avisar" diz o que desliga de fato. R-08: o modal bloqueia
o cartão, que some com o prazo parado e volta com o que restava, sem contar como aviso novo. R-09: a suspensão não vira
WARNING de thread presa. R-11: a exclusão da captura antes do primeiro `show` e o WARNING dela uma vez só."""
from __future__ import annotations

import logging
from typing import Any

import pytest

from listener.model import AppState
from listener.ui.call_rules import MODE_IDLE, MODE_STOPPED, Prompt, State
from tests.fake_calls import act, chrome, teams, zoom
from tests.fake_watch import feed, glue, prompted

pytest.importorskip("pytestqt")
pytestmark = pytest.mark.lento                              # D-050: telas

TEAMS = "ms-teams.exe"


@pytest.fixture
def g(qtbot, platform, store, monkeypatch):
    made = glue(platform, store, monkeypatch)
    yield made
    made.close()


@pytest.fixture
def warmup_app(qtbot, qapp, platform, store, monkeypatch):
    """O controlador de verdade com a cola sem a thread; `script` diz o que o "Antes de gravar" faz quando aparece."""
    from PySide6.QtCore import QTimer

    from listener.ui import controller as ctl
    from listener.ui import tasks
    from listener.ui.call_sensor import CallSensor
    from listener.ui.call_watch import CallWatch
    from listener.ui.warmup import WarmupDialog
    from tests.fake_watch import Clock

    script: list[Any] = []

    class ScriptedWarmup(WarmupDialog):
        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, **k)
            QTimer.singleShot(50, self, self._play)
            QTimer.singleShot(3000, self, self.reject)                                  # R-11: falha em vez de travar

        def _play(self) -> None:
            script[0](self)

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    monkeypatch.setattr(ctl, "WarmupDialog", ScriptedWarmup)
    monkeypatch.setattr(CallSensor, "start", lambda _self: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    clock = Clock()
    calls = CallWatch(c, platform, store, clock=clock)
    calls.start()
    yield c, calls, clock, script
    tasks.live_threads.discard(calls.sensor)
    calls.shutdown()
    c.emergency_stop()


# ---------------------------------------------------------------- R-02 e R-05: o "Antes de gravar"
def test_a_warmup_opened_by_hand_and_cancelled_mid_call_still_warns(warmup_app):
    """A reprodução do `manual_warmup.py`: o Teams há 5 s, o "Antes de gravar" aberto pela bandeja (o de sempre) e cancelado;
    a chamada segue contando e avisa, sem virar "coberta"."""
    c, calls, clock, script = warmup_app
    seen: list[tuple[str, bool]] = []

    def inside(dlg: Any) -> None:
        calls.tick()                                                                     # a cola vê o "Antes de gravar"
        clock.t += 5
        calls._on_reading(act(teams()))                                                  # uma leitura em voo
        seen.append((calls.sensor.mode, calls.rules.state(TEAMS) == State.COVERED))
        dlg.reject()

    script.append(inside)
    calls._on_reading(act(teams()))                                                      # 0 s: armando
    c.open_warmup()                                                                      # à mão, sem o app como fonte
    assert seen == [(MODE_STOPPED, False)] and calls.card is None                       # parado, sem cobrir e sem avisar
    calls.tick()
    assert calls.sensor.mode != MODE_STOPPED
    clock.t += 15
    calls._on_reading(act(teams()))
    assert calls.rules.state(TEAMS) == State.PROMPTED and calls.card is not None        # aviso, não coberto


def test_record_that_does_not_start_leaves_the_call_pending_with_one_reminder(warmup_app, monkeypatch):
    c, calls, clock, script = warmup_app
    monkeypatch.setattr(c, "start_recording", lambda *_a, **_k: False)                  # o "Gravar" que não começou
    script.append(lambda dlg: dlg.accept())
    calls._on_reading(act(teams()))
    clock.t += 8
    calls._on_reading(act(teams()))
    assert calls.rules.state(TEAMS) == State.PROMPTED
    calls.open_for(TEAMS, "aviso")
    assert c.state == AppState.IDLE and calls.rules.state(TEAMS) == State.PENDING      # não vira "Agora não"
    clock.t += 119
    calls._on_reading(act(teams()))
    assert calls.card is None
    clock.t += 2
    events = calls.rules.step(act(teams()), clock.t, False, False, calls.params)
    assert [e.reminder for e in events if isinstance(e, Prompt)] == [True]              # o lembrete único, 2 min depois


# ---------------------------------------------------------------- R-06 e R-07: a bandeja e o atalho
def test_tray_and_hotkey_pick_the_card_then_the_latest_pending(g):
    prompted(g)                                                                           # Teams: aviso às 8 s
    g.calls._on_expired()                                                                 # sumiu: pendente
    feed(g, teams(), zoom(), at=20)
    feed(g, teams(), zoom(), at=28)                                                       # Zoom: aviso às 28 s
    assert g.calls.card_exe == "zoom.exe" and g.host.tray.call == ("zoom.exe", "Zoom")   # o do cartão, não o alfabético
    g.calls._on_expired()
    assert [p.exe for p in g.calls.rules.pending()] == [TEAMS, "zoom.exe"]
    assert g.host.tray.call == ("zoom.exe", "Zoom")                                      # o avisado por último
    g.calls.start_from_hotkey()
    assert g.host.opened == ["Zoom.exe"]


def test_dont_warn_names_what_it_switches_off(g, store):
    from listener.ui.call_watch import OFF_NOTICE

    meet = chrome(mic=True, peak=0.2, service="Google Meet")
    prompted(g, meet)
    assert g.host.tray.call == ("chrome.exe", "Google Meet no Chrome") and g.host.tray.off_label == "Chrome"
    g.calls.silence("chrome.exe")
    assert g.host.notices == [OFF_NOTICE.format(label="Chrome")] and store.get_setting("detect.off") == "chrome.exe"


# ---------------------------------------------------------------- R-08: o diálogo modal com o cartão à vista
def test_a_modal_dialog_blocks_the_card(qtbot):
    """Confirmação: com um diálogo modal aberto, o clique no cartão (pelo sistema de janelas) não chega."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    from listener.ui.call_prompt import CallPrompt

    card = CallPrompt("Teams", 8)
    qtbot.addWidget(card)
    card.show()
    qtbot.waitExposed(card)
    got: list[int] = []
    card.dismissed.connect(lambda: got.append(1))
    dlg = QDialog()
    qtbot.addWidget(dlg)
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.show()
    qtbot.waitExposed(dlg)
    pos = card.later_button.mapTo(card, card.later_button.rect().center())
    qtbot.mouseClick(card.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert got == []
    dlg.close()
    qtbot.mouseClick(card.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert got == [1]


def test_the_card_hides_while_a_modal_is_open_and_comes_back_with_the_time_left(qtbot, g, monkeypatch):
    from listener.ui.call_watch import CallWatch

    card = prompted(g)
    shown = len(g.calls.rules._prompts)
    modal = [True]
    monkeypatch.setattr(CallWatch, "_modal_now", staticmethod(lambda: modal[0]))
    qtbot.wait(100)
    left = card.remaining_ms()
    g.calls.tick()
    assert not card.isVisible() and card.paused and card.remaining_ms() == -1 and g.calls.card is card
    qtbot.wait(400)                                                                       # mais que o prazo (300 ms): não some
    assert g.calls.card is card and g.calls.rules.state(TEAMS) == State.PROMPTED
    modal[0] = False
    g.calls.tick()
    assert card.isVisible() and 0 < card.remaining_ms() <= left and not card.paused
    assert len(g.calls.rules._prompts) == shown                                         # não é um aviso a mais na hora
    qtbot.waitUntil(lambda: g.calls.rules.state(TEAMS) == State.PENDING, timeout=2000)   # o prazo que restava venceu


# ---------------------------------------------------------------- R-09 e R-11
def test_a_late_tick_after_sleep_restarts_the_watchdog_instead_of_warning(g, caplog):
    feed(g, teams(), at=0)                                                                # armando: 0,02 s aqui
    with caplog.at_level(logging.WARNING, logger="listener.ui.call_watch"):
        g.clock.t += 0.01
        g.calls.tick()
        g.clock.t += 30                                                                   # a máquina dormiu
        g.calls.tick()
        assert "sem leitura" not in caplog.text
        g.clock.t += 0.07
        g.calls.tick()                                                                    # o vigia segue valendo
    assert caplog.text.count("sem leitura há") == 1


def test_the_card_leaves_the_capture_before_it_shows_and_the_warning_comes_once(g, platform, caplog):
    from PySide6.QtWidgets import QWidget

    visible_at_exclusion: list[bool] = []
    real = platform.exclude_from_capture

    def exclude(window_id: int) -> bool:
        visible_at_exclusion.append(QWidget.find(window_id).isVisible())
        return real(window_id)

    platform.exclude_from_capture = exclude
    prompted(g)
    assert visible_at_exclusion == [False] and g.calls.card.isVisible()                   # fora da captura antes do `show`
    g.calls._on_dismiss()
    platform.capture_exclusion = False
    with caplog.at_level(logging.WARNING, logger="listener.ui.call_watch"):
        prompted(g, zoom(), start=40)
        g.calls._on_expired()
        feed(g, zoom(), chrome(mic=True, peak=0.2), at=60)
        feed(g, zoom(), chrome(mic=True, peak=0.2), at=68)
    assert g.calls.card_exe == "chrome.exe" and caplog.text.count("não saiu da captura de tela") == 1


def test_the_sensor_is_idle_again_when_the_warmup_closes(g, monkeypatch):
    from listener.ui import call_watch

    open_now = [object()]
    monkeypatch.setattr(call_watch, "open_warmup_dialog", lambda: open_now[0])
    g.calls.tick()
    assert g.sensor.mode == MODE_STOPPED
    open_now[0] = None
    g.calls.tick()
    assert g.sensor.mode == MODE_IDLE
