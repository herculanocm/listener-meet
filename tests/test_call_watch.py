"""F5.7 (L3, U-094): a thread do sensor do aviso de reunião sem gravação e a cola com as regras (pytest-qt, offscreen, com a
plataforma falsa). A thread: os tempos de cada modo e o espaço das falhas (`delay_for`), o "parado" que só lê a pedido, o
"ocioso" que espera o indicador de microfone, as falhas que viram o modo só pelo indicador (um WARNING, um INFO), nenhuma
exceção saindo, o sistema de áudio solto na própria thread e a parada antes de o banco fechar. A cola (sem a thread: as
leituras vêm do teste, num relógio manual): o cartão fora da captura e a bandeja, as respostas, a gravação por outro caminho
(a leitura da borda e nenhum passo durante o `busy`), o "Antes de gravar" pelo atalho e pela bandeja, o "Não avisar", a
releitura das Configurações e o vigia da thread presa."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
import shiboken6

from listener.platform.base import AudioActivity
from listener.ui.call_rules import MODE_ARMING, MODE_IDLE, MODE_SLOW, MODE_STOPPED, State
from listener.ui.call_sensor import CallSensor, Intervals, backoff_s, delay_for
from tests.fake_calls import act, teams, whatsapp
from tests.fake_watch import FAST, Clock, FakeHost
from tests.fake_watch import feed as _feed
from tests.fake_watch import glue as make_glue
from tests.fake_watch import prompted as _prompted

pytest.importorskip("pytestqt")
pytestmark = pytest.mark.lento                              # D-050: threads e telas

def _sensor(platform: Any, mode: str = MODE_STOPPED, iv: Intervals = FAST) -> tuple[CallSensor, list[Any]]:
    got: list[Any] = []
    s = CallSensor(platform, iv)
    s.reading.connect(got.append)
    s.set_mode(mode)
    s.start()
    return s, got


def _halt(s: CallSensor) -> None:
    s.halt()
    assert s.wait(2000)


# ---------------------------------------------------------------- a thread
def test_each_mode_waits_its_time_and_failures_space_the_tries():
    iv = Intervals()
    assert delay_for(MODE_STOPPED, True, 0, iv) == (10.0, False)                      # só acorda para ver o modo
    assert delay_for(MODE_IDLE, True, 0, iv) == (20.0, True)                          # espera o indicador por até 20 s
    assert delay_for(MODE_IDLE, False, 0, iv) == (5.0, False)                         # sem o indicador, a cada 5 s
    assert delay_for(MODE_ARMING, True, 0, iv) == (2.0, False)
    assert delay_for(MODE_SLOW, True, 0, iv) == (5.0, False)
    assert delay_for(MODE_ARMING, True, 1, iv) == (2.0, False) and delay_for(MODE_IDLE, True, 2, iv) == (4.0, False)
    assert delay_for(MODE_IDLE, True, 3, iv) == (20.0, True)                          # só pelo indicador: no ritmo do modo
    assert delay_for(MODE_STOPPED, True, 2, iv) == (10.0, False)
    assert [backoff_s(n, iv) for n in range(1, 9)] == [2, 4, 8, 16, 32, 60, 60, 60]


def test_stopped_reads_only_when_asked_and_idle_waits_for_the_mic_indicator(qtbot, platform):
    s, got = _sensor(platform)
    try:
        qtbot.wait(150)
        assert got == [] and platform.activity_reads == []                            # parado: não lê
        s.set_titles(frozenset({"chrome.exe"}))
        s.request()                                                                    # a borda do `busy`: uma leitura já
        qtbot.waitUntil(lambda: len(got) == 1, timeout=2000)
        assert got[0] is platform.activity and platform.activity_reads == [(frozenset({"chrome.exe"}), False)]
        qtbot.wait(100)
        assert len(got) == 1
        s.set_mode(MODE_IDLE)                                                          # o modo novo lê na hora
        qtbot.waitUntil(lambda: len(got) == 2, timeout=2000)
        qtbot.waitUntil(lambda: platform.watch.waits > 3, timeout=2000)               # e espera o indicador (5 s aqui)
        assert len(got) == 2 and s.has_watch
        platform.watch.change()                                                        # o indicador mudou: lê bem antes dos 5 s
        qtbot.waitUntil(lambda: len(got) == 3, timeout=1500)
        s.set_mode(MODE_ARMING)
        qtbot.waitUntil(lambda: len(got) > 10, timeout=3000)                           # armando: a cada 2 s (aqui 0,02)
    finally:
        _halt(s)
    assert platform.watch.cancelled and platform.watch.closed


def test_without_the_mic_indicator_idle_polls(qtbot, platform):
    platform.watch = None
    s, got = _sensor(platform, MODE_IDLE)
    try:
        qtbot.waitUntil(lambda: len(got) > 5, timeout=3000)
        assert not s.has_watch
    finally:
        _halt(s)


def test_failures_degrade_with_one_warning_and_recover_with_one_info(qtbot, platform, caplog):
    platform.activity_error = OSError("o sistema de áudio caiu")
    with caplog.at_level(logging.DEBUG, logger="listener.ui.call_sensor"):
        s, got = _sensor(platform, MODE_ARMING)
        try:
            qtbot.waitUntil(lambda: sum(1 for a in got if isinstance(a, AudioActivity) and a.degraded) >= 6, timeout=3000)
            assert got[:2] == [None, None] and s.failures >= 3                       # duas leituras perdidas, depois o indicador
            full = sum(1 for _titles, degraded in platform.activity_reads if not degraded)
            assert full < sum(1 for _titles, degraded in platform.activity_reads if degraded)   # a leitura inteira espaçada
            platform.activity_error = None
            qtbot.waitUntil(lambda: got[-1] is platform.activity and s.failures == 0, timeout=3000)
        finally:
            _halt(s)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO and "voltaram" in r.getMessage()]
    assert len(warnings) == 1 and "3 leituras seguidas falharam" in warnings[0].getMessage() and len(infos) == 1
    assert "OSError" in caplog.text and "caiu" not in caplog.text                     # o tipo, nunca a mensagem


def test_no_exception_leaves_the_thread(qtbot, platform, monkeypatch):
    def broken(*_a: object, **_k: object) -> AudioActivity:
        raise RuntimeError("tudo falha")

    monkeypatch.setattr(platform, "audio_activity", broken)
    monkeypatch.setattr(platform, "mic_watch", broken)                                 # sem o indicador: consulta
    s, got = _sensor(platform, MODE_ARMING)
    try:
        qtbot.waitUntil(lambda: len(got) > 6, timeout=3000)
        assert set(map(type, got)) == {type(None)} and s.isRunning() and not s.has_watch
    finally:
        _halt(s)


def test_the_audio_system_is_released_in_the_sensor_thread_before_it_leaves(qtbot, platform, monkeypatch):
    seen: dict[str, int] = {}
    read, close = platform.audio_activity, platform.close_audio_activity

    def reading(*a: Any, **k: Any) -> Any:
        seen["read"] = threading.get_ident()
        return read(*a, **k)

    def closing() -> None:
        seen["close"] = threading.get_ident()
        close()

    monkeypatch.setattr(platform, "audio_activity", reading)
    monkeypatch.setattr(platform, "close_audio_activity", closing)
    s, got = _sensor(platform, MODE_IDLE)
    qtbot.waitUntil(lambda: len(got) == 1, timeout=2000)
    _halt(s)
    assert seen["close"] == seen["read"] != threading.get_ident() and platform.activity_closed == 1


# ---------------------------------------------------------------- a cola
@pytest.fixture
def glue(qtbot, platform, store, monkeypatch):
    g = make_glue(platform, store, monkeypatch)
    yield g
    g.close()


def test_a_call_shows_one_card_out_of_capture_and_the_tray_points_to_it(qtbot, glue, platform, caplog):
    from PySide6.QtCore import Qt

    with caplog.at_level(logging.INFO, logger="listener.ui.call_watch"):
        _feed(glue, teams(), at=0)
        assert glue.calls.card is None and glue.sensor.mode == MODE_ARMING
        card = _prompted(glue)
    assert card.isVisible() and int(card.winId()) in platform.excluded
    assert card.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus and card.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert card.question.text() == "Gravar a reunião do Teams?"
    assert card.detail.text() == "Áudio de chamada há 8 s. Nada está sendo gravado."
    assert card.record_button.toolTip() == 'Abre o "Antes de gravar" com o Teams como fonte (Ctrl+Alt+Shift+R)'
    assert glue.host.tray.call == ("ms-teams.exe", "Teams") and glue.host.toasts.avoid == glue.calls.card_rect
    g = card.frameGeometry()
    assert glue.calls.card_rect() == (g.x(), g.y(), g.width(), g.height()) and glue.host.toasts.relayouts >= 1
    assert "chamada sem gravação: ms-teams.exe (pid 100), regra R1, 8 s; aviso mostrado" in caplog.text
    _feed(glue, teams(), at=9)
    assert glue.calls.card is card                                                       # um aviso só


def test_without_the_capture_exclusion_the_notice_goes_only_to_the_tray(qtbot, glue, platform, caplog):
    platform.capture_exclusion = False
    with caplog.at_level(logging.INFO, logger="listener.ui.call_watch"):
        card = _prompted(glue)
        assert not card.isVisible() and glue.calls.card_rect() is None and glue.host.tray.call[0] == "ms-teams.exe"
        qtbot.waitUntil(lambda: glue.calls.rules.state("ms-teams.exe") == State.PENDING, timeout=2000)   # os 20 s valem igual
    assert "só na bandeja" in caplog.text and caplog.text.count("não saiu da captura de tela") == 2      # o WARNING, uma vez
    assert glue.host.tray.call == ("ms-teams.exe", "Teams")


@pytest.mark.parametrize("answer", ["later", "close"])
def test_now_not_and_the_x_dismiss_until_the_call_ends(qtbot, glue, caplog, answer):
    from PySide6.QtCore import QCoreApplication, QEvent

    from listener.ui import common

    key = id(_prompted(glue))                                                           # o teste não segura o cartão
    with caplog.at_level(logging.INFO, logger="listener.ui.call_watch"):
        getattr(glue.calls.card, f"{answer}_button").click()                            # fechado dentro do próprio clique
    assert glue.calls.card is None and glue.calls.rules.state("ms-teams.exe") == State.DISMISSED
    assert glue.host.tray.call == ("", "") and "(ms-teams.exe): Agora não" in caplog.text and glue.sensor.mode == MODE_SLOW
    assert key in common._ALIVE and not common._ALIVE[key].isVisible()                 # vivo até o `deleteLater`
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert key not in common._ALIVE


def test_a_card_that_vanishes_turns_pending_and_record_opens_the_warmup_after_the_click(qtbot, glue, caplog):
    card = _prompted(glue)
    with caplog.at_level(logging.INFO, logger="listener.ui.call_watch"):
        qtbot.waitUntil(lambda: glue.calls.card is None, timeout=2000)                  # sumiu sem clique
        assert glue.calls.rules.state("ms-teams.exe") == State.PENDING and glue.host.tray.call[0] == "ms-teams.exe"
        assert "sumiu sem clique" in caplog.text and not (shiboken6.isValid(card) and card.isVisible())
    _feed(glue, teams(), at=8 + 125)                                                     # o lembrete, 2 min depois
    reminder = glue.calls.card
    assert reminder is not None and reminder.question.text() == "Ainda sem gravar: a reunião do Teams segue com áudio."
    assert reminder.detail.text() == "Começou há 2 min."
    reminder.record_button.click()
    assert glue.host.opened == [] and glue.calls.card is None                          # depois do evento do clique
    qtbot.waitUntil(lambda: glue.host.opened == ["ms-teams.exe"], timeout=2000)
    assert glue.calls.rules.state("ms-teams.exe") == State.DISMISSED                   # fechou sem gravar: "Agora não"


def test_recording_by_another_path_closes_the_card_and_steps_once_with_a_real_reading(qtbot, glue, monkeypatch, caplog):
    card = _prompted(glue)
    steps: list[tuple[bool, bool]] = []
    step = glue.calls.rules.step

    def counted(a: Any, now: float, busy: bool, modal: bool, p: Any) -> Any:
        steps.append((a is None, busy))
        return step(a, now, busy, modal, p)

    monkeypatch.setattr(glue.calls.rules, "step", counted)
    asked: list[int] = []
    monkeypatch.setattr(glue.sensor, "request", lambda: asked.append(1))
    glue.host.busy = True
    with caplog.at_level(logging.INFO, logger="listener.ui.call_watch"):
        glue.calls.tick()
    assert glue.calls.card is None and not card.isVisible() and "fechado pela gravação" in caplog.text
    assert asked == [1] and glue.sensor.mode == MODE_STOPPED and glue.host.tray.call == ("", "")
    _feed(glue, teams(), at=9)                                                           # a leitura da borda
    _feed(glue, teams(), at=10)                                                          # durante o `busy`: nenhum passo
    assert steps == [(False, True)] and glue.calls.rules.state("ms-teams.exe") == State.COVERED
    glue.host.busy = False
    glue.calls.tick()
    glue.host.busy = True
    glue.clock.t += 1
    glue.calls.tick()                                                                    # a borda de novo, sem leitura
    glue.clock.t += 3
    glue.calls.tick()
    assert steps[-1] == (True, True)                                                     # cobre pela última leitura real


def test_the_hotkey_and_the_tray_open_the_warmup_filled_and_closing_it_dismisses(qtbot, glue):
    assert glue.host.tray.handlers == (glue.calls.open_for, glue.calls.silence)
    glue.calls.start_from_hotkey()
    assert glue.host.opened == [None]                                                    # sem chamada pendente: o de sempre
    card = _prompted(glue)
    glue.calls.start_from_hotkey()                                                       # pendente: o app como fonte
    assert glue.host.opened == [None, "ms-teams.exe"] and not card.isVisible()
    assert glue.calls.rules.state("ms-teams.exe") == State.DISMISSED                   # o Esc ou o Cancelar
    _feed(glue, whatsapp(), at=20)
    _feed(glue, whatsapp(), at=41)                                                       # R3: 20 s com microfone e som
    assert glue.calls.card is not None and glue.calls.card_exe == "whatsapp.root.exe"

    def recorded(host: FakeHost) -> None:
        host.busy = True                                                                 # o Gravar do "Antes de gravar"

    glue.host.on_open = recorded
    glue.calls.open_for("whatsapp.root.exe")
    assert glue.host.opened[-1] == "WhatsApp.Root.exe" and glue.calls.rules.state("whatsapp.root.exe") == State.PROMPTED
    glue.host.opened.clear()
    glue.calls.open_for("whatsapp.root.exe")                                             # gravando: não abre de novo
    assert glue.host.opened == []


def test_dont_warn_for_teams_writes_the_setting_dismisses_and_says_how_to_undo(qtbot, glue, store):
    from listener.ui.call_watch import OFF_NOTICE

    _prompted(glue)
    glue.calls.silence("ms-teams.exe")
    assert store.get_setting("detect.off") == "ms-teams.exe" and "ms-teams.exe" in glue.calls.params.off
    assert glue.host.notices == [OFF_NOTICE.format(label="Teams")] and glue.calls.card is None
    assert glue.host.notices[0] == "Aviso de reunião desligado para o Teams. Para religar: Configurações, Idioma e avisos."
    assert glue.calls.rules.state("ms-teams.exe") == State.DISMISSED and glue.host.tray.call == ("", "")
    for t in range(9, 300, 2):                                                           # a chamada acaba e volta: nada
        _feed(glue, teams(), at=t)
    assert glue.calls.card is None
    _feed(glue, whatsapp(), at=400)
    _feed(glue, whatsapp(), at=421)
    glue.calls.silence("whatsapp.root.exe")                                              # outro app: o nome como o sistema mostra
    assert store.get_setting("detect.off") == "ms-teams.exe,WhatsApp.Root.exe"


def test_settings_are_reread_every_10_s_and_when_a_dialog_closes(qtbot, glue, store, monkeypatch, caplog):
    from listener.ui.call_watch import CallWatch

    card = _prompted(glue)
    glue.calls.reload()                                                                  # a releitura conta daqui
    store.set_setting("detect.enabled", "False")
    glue.clock.t += 5
    glue.calls.tick()
    assert glue.calls.params.enabled and glue.calls.card is card
    glue.clock.t += 5
    with caplog.at_level(logging.INFO, logger="listener.ui.call_watch"):
        glue.calls.tick()                                                                # 10 s: vale (CP8)
    assert not glue.calls.params.enabled and glue.calls.card is None and glue.sensor.mode == MODE_STOPPED
    assert "(ms-teams.exe): desligado" in caplog.text and "aviso de reunião sem gravação desligado" in caplog.text
    store.set_setting("detect.enabled", "True")
    store.set_setting("detect.seconds", "20")
    modal = [True]
    monkeypatch.setattr(CallWatch, "_modal_now", staticmethod(lambda: modal[0]))
    glue.calls.tick()
    assert not glue.calls.params.enabled                                                 # com o diálogo aberto, ainda não
    modal[0] = False
    glue.calls.tick()                                                                    # o diálogo fechou: vale já
    assert glue.calls.params.enabled and glue.calls.params.seconds == 20 and glue.sensor.mode == MODE_IDLE
    _feed(glue, teams(), at=100)
    _feed(glue, teams(), at=110)
    assert glue.calls.card is None
    _feed(glue, teams(), at=120)
    assert glue.calls.card is not None


def test_the_watchdog_warns_once_when_readings_stop_and_logs_when_they_come_back(qtbot, glue, caplog):
    _feed(glue, teams(), at=0)                                                           # armando: a cada 0,02 s aqui
    with caplog.at_level(logging.INFO, logger="listener.ui.call_watch"):
        glue.clock.t += 0.05
        glue.calls.tick()
        assert "sem leitura" not in caplog.text
        glue.clock.t += 0.02
        glue.calls.tick()
        glue.clock.t += 5
        glue.calls.tick()
        assert caplog.text.count("sem leitura há") == 1                                 # uma vez
        _feed(glue, teams())
    assert "sensor de chamada voltou a ler" in caplog.text


# ---------------------------------------------------------------- a thread de verdade e a saída
def test_the_real_thread_drives_a_prompt_and_record_opens_the_warmup(qtbot, platform, store):
    from listener.ui.call_watch import CallWatch

    platform.activity = act(teams())
    clock, host = Clock(), FakeHost()
    calls = CallWatch(host, platform, store, clock=clock, intervals=FAST)
    calls.start()
    try:
        qtbot.waitUntil(lambda: calls.rules.state("ms-teams.exe") == State.ARMING, timeout=3000)
        clock.t += 9
        qtbot.waitUntil(lambda: calls.card is not None, timeout=3000)
        calls.card.record_button.click()
        qtbot.waitUntil(lambda: host.opened == ["ms-teams.exe"], timeout=3000)
    finally:
        sensor = calls.sensor
        calls.shutdown()
    assert sensor.isFinished() and platform.activity_closed == 1 and platform.watch.closed


@pytest.mark.parametrize("signal", ["aboutToQuit", "commitDataRequest"])
def test_the_exit_stops_the_sensor_before_the_emergency_stop_closes_the_store(qtbot, qapp, platform, store, monkeypatch, signal):
    """A ligação do `app.py` (`attach_calls`): no `aboutToQuit` e no `commitDataRequest`, a thread do sensor já parou (e soltou
    o sistema de áudio) quando a parada de emergência roda e fecha o banco."""
    from PySide6.QtCore import QObject, Signal

    from listener.ui import controller as ctl
    from listener.ui.app import attach_calls

    class App(QObject):
        aboutToQuit = Signal()                                                           # noqa: N815 (o nome do Qt)
        commitDataRequest = Signal(object)                                               # noqa: N815

        def quit(self) -> None:                                                          # D-072: o logoff sai depois
            pass

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    seen: list[tuple[bool, bool, int]] = []
    held: dict[str, Any] = {}
    real_stop = c.emergency_stop

    def emergency() -> None:
        seen.append((held["calls"].sensor is None, held["thread"].isFinished(), platform.activity_closed))
        real_stop()                                                                      # fecha o banco

    monkeypatch.setattr(c, "emergency_stop", emergency)
    app = App()
    calls = attach_calls(app, c, platform, store)
    calls.start()
    held.update(calls=calls, thread=calls.sensor)
    assert c.on_start == calls.start_from_hotkey and calls.parent() is c
    getattr(app, signal).emit(*(() if signal == "aboutToQuit" else (None,)))
    assert seen == [(True, True, 1)]                                                     # parado antes do fechamento do banco


def test_a_logoff_quits_the_app_after_the_stops(qtbot):
    """D-072: depois do `commitDataRequest` a parada de emergência já fechou o banco; se o Windows desistisse do logoff (um
    app segurou e o usuário cancelou), o processo seguia vivo sem banco, sem atalhos e sem aviso. Agora o app sai."""
    from PySide6.QtCore import QObject, Signal

    from listener.ui.app import connect_exit

    order: list[str] = []

    class App(QObject):
        aboutToQuit = Signal()                                                           # noqa: N815 (o nome do Qt)
        commitDataRequest = Signal(object)                                               # noqa: N815

        def quit(self) -> None:
            order.append("quit")

    app = App()
    connect_exit(app, lambda: order.append("sensor"), lambda: order.append("emergência"))
    app.aboutToQuit.emit()
    qtbot.wait(20)
    assert order == ["sensor", "emergência"]                                             # a saída normal já está saindo
    order.clear()
    app.commitDataRequest.emit(None)
    qtbot.waitUntil(lambda: "quit" in order, timeout=1000)
    assert order == ["sensor", "emergência", "quit"]


def test_a_mute_poller_stuck_in_the_audio_system_counts_at_exit(qtbot, qapp, platform, store, monkeypatch):
    """D-072 (dívida vista no L3 da F5.7): o `_MutePoller` preso no COM na saída caía no 0xC0000409 que o R-01 corrigiu no
    sensor. Parado, ele entra na conta do `leave_if_stuck` enquanto segue rodando."""
    from listener.ui import call_sensor
    from listener.ui import controller as ctl

    inside, free = threading.Event(), threading.Event()

    class StuckMute:
        def get(self) -> bool:
            inside.set()
            free.wait(10)
            return False

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    monkeypatch.setattr(platform, "mic_mute", StuckMute)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    c._start_mute_poller()
    poller = c._mute_poller
    try:
        assert inside.wait(2)
        c._stop_mute_poller()
        assert poller in call_sensor.ABANDONED and call_sensor.stuck_threads() >= 1
    finally:
        free.set()                                                                       # R-11: solta a thread mesmo com falha
    assert poller.wait(3000)
    call_sensor.ABANDONED.remove(poller)


def test_a_stuck_thread_is_left_behind_after_one_second(qtbot, platform, store, monkeypatch, caplog):
    from listener.ui import call_sensor
    from listener.ui.call_watch import CallWatch

    stuck = threading.Event()
    monkeypatch.setattr(platform, "audio_activity", lambda *_a, **_k: stuck.wait(10) and None)
    calls = CallWatch(FakeHost(), platform, store, intervals=FAST)
    calls.start()
    sensor = calls.sensor
    try:
        qtbot.waitUntil(sensor.isRunning, timeout=2000)
        qtbot.wait(100)                                                                   # preso na leitura
        with caplog.at_level(logging.WARNING, logger="listener.ui.call_watch"):
            calls.shutdown()
        assert "não parou em 1000 ms" in caplog.text and sensor in call_sensor.ABANDONED
        assert call_sensor.stuck_threads() >= 1                                          # o `app.py` sai já por isto (R-01)
    finally:
        stuck.set()                                                                      # R-11: solta a thread mesmo com falha
    assert sensor.wait(3000)
    call_sensor.ABANDONED.remove(sensor)


def test_the_app_exits_with_code_0_with_the_sensor_stuck_in_the_audio_system():
    """R-01: a derrubada do PySide destrói no `atexit` todo QObject do Python; com a QThread do sensor presa (uma troca de dock),
    o Qt abortava o processo (0xC0000409). O caminho de saída de verdade (`tests/call_exit_scenario.py`: `app.run`, a bandeja,
    a cola, o `aboutToQuit`) num subprocesso: o `app.py` sai já, com o código do laço e o log escrito. [fato, 29/10] sem a
    saída imediata (`--sem-saida-imediata`), o mesmo cenário termina com 0xC0000409."""
    root = Path(__file__).resolve().parent.parent
    env = {k: v for k, v in os.environ.items() if k != "LISTENER_LLM"} | {"PYTHONUTF8": "1", "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.run([sys.executable, str(root / "tests" / "call_exit_scenario.py")], cwd=str(root), env=env,  # noqa: S603
                          capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "não parou em 1000 ms" in proc.stderr and "saída imediata" in proc.stderr and "QThread: Destroyed" not in proc.stderr
