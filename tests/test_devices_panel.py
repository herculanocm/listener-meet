"""F5.3 L4 (U-049 a U-051), telas e a vigia: o painel do botão 🎧 com as listas (a fonte de um app, "novo", Hands-Free,
"(comunicação)", "Outro app…", Esc fecha); o fone conectado na gravação aparece no widget como "novo: <nome>" com "Usar",
que troca o microfone e abre o painel com as saídas do mesmo aparelho primeiro (U-050) e some em 30 s; o mudo passa ao
microfone novo de uma vez e a primeira leitura do poller depois da troca é ignorada (A-18); a vigia segue o padrão (A-20)
e o escolhido que sai e volta (pergunta 10); o "Antes de gravar" com "acompanha" e uma linha por saída (U-051)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from listener.capture.recorder import RecordConfig, SourceSpec
from listener.model import EventType
from tests.conftest import make_config
from tests.fake_platform import BASE_ENDPOINTS, MIC, SPEAKERS
from tests.test_recording_devices import BT, BT_HFP, BT_MIC, BT_OUT, with_bt

pytest.importorskip("PySide6")
pytestqt = pytest.importorskip("pytestqt")


def _texts(rows) -> list[str]:
    return [r.text for r in rows]


def test_the_panel_lists_mics_and_outputs_with_an_app_source(qtbot, platform, store, recorder_factory):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from listener.ui.devices_panel import APP_NOTE, CURRENT_ROLE, DETAIL_ROLE, HANDS_FREE_NOTE, NAME_ROLE, NEW_ROLE, OTHER_APP
    from listener.ui.recording_devices import RecordingDevices

    rec = recorder_factory()                                           # a fonte é um app (meeting.exe)
    rec.start()
    other: list[int] = []
    devices = RecordingDevices(platform, rec, open_other_app=lambda: other.append(1))
    devices.start()
    platform.next_endpoints = with_bt(comm=BT_HFP.id)                  # o fone chegou durante a gravação
    devices.poll()
    panel = devices.open_panel()
    qtbot.addWidget(panel)
    view = panel.view
    assert view is not None
    assert _texts(view.mics) == ["Padrão do Windows (acompanha): agora Mic Fake", "Mic Fake", f"{BT.name} · novo"]
    assert view.mics[0].current and view.mics[2].tip == HANDS_FREE_NOTE
    assert _texts(view.outputs) == ["Reunião (a fonte atual)", "Sistema inteiro: saída padrão (acompanha): agora Alto-falantes Fake",
                                    "Sistema inteiro: Alto-falantes Fake", f"Sistema inteiro: {BT_OUT.name} · novo",
                                    f"Sistema inteiro: {BT_HFP.name} (comunicação) · novo", "Outro app…"]
    assert view.outputs[0].current and APP_NOTE in view.notes and HANDS_FREE_NOTE in view.notes
    assert panel.mics.item(0).data(CURRENT_ROLE) and not panel.mics.item(1).data(CURRENT_ROLE)          # F5.4 (U-064): a marca
    assert panel.mics.item(0).text().endswith("(em uso)") and panel.mics.item(2).data(NEW_ROLE)                # e o selo "Novo"
    assert (panel.mics.item(2).data(NAME_ROLE), panel.mics.item(2).data(DETAIL_ROLE)) == (BT.name, "Microfone · perfil de chamada (mono)")
    devices.choose_output(OTHER_APP)                                   # o "Trocar fonte" de antes
    assert other == [1]
    assert devices.choose_output(f"out:{BT_OUT.id}")                   # do app para o sistema inteiro: troca de fonte
    assert rec.source.output_id == BT_OUT.id and rec.output_open_id == BT_OUT.id
    assert [r.current for r in panel.view.outputs if r.key == f"out:{BT_OUT.id}"] == [True]   # type: ignore[union-attr]
    types = [e["type"] for e in store.list_events(rec.meeting_id)]
    assert EventType.SOURCE_CHANGE in types and EventType.DEVICE_CHANGE not in types
    QTest.keyClick(panel, Qt.Key.Key_Escape)                           # Esc fecha
    assert not panel.isVisible()
    devices.stop()
    rec.stop()


def _controller(qapp, platform, store, monkeypatch):
    from listener.ui import controller as ctl

    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: SimpleNamespace(exec=lambda: None))
    return c


def _shows_new(w) -> bool:
    """A linha de alerta mostra o "novo" (outra linha, como o banco lento de uma thread de trabalho atrasada, não conta)."""
    return w.alert_label.isVisibleTo(w) and w.alert_label.full_text.startswith("novo:")


def _system_cfg() -> RecordConfig:
    return RecordConfig(source=SourceSpec("system", label="Sistema inteiro"), chunk_seconds=0.5, stop_timeout_s=1.0,
                        retry_base_s=0.001)


def test_a_headset_connected_while_recording_shows_new_and_use(qtbot, qapp, platform, store, monkeypatch):
    """U-050: "novo: <nome>" com "Usar" na linha de alerta, por 30 s; "Usar" troca o microfone (relendo o PortAudio) e abre
    o painel com as saídas do mesmo aparelho primeiro, a de comunicação antes."""
    c = _controller(qapp, platform, store, monkeypatch)
    try:
        assert c.start_recording(_system_cfg(), {"title": "u050"}) and c.devices is not None and c.widget is not None
        now = {"t": 100.0}
        c.devices.monotonic = lambda: now["t"]
        w = c.widget
        platform.next_endpoints = with_bt(comm=BT_HFP.id)
        c.devices.poll()
        platform.pump(0.5)
        c._tick()
        assert (w.alert_label.full_text, w.alert_btn.text()) == (f"novo: {BT.name}", "Usar") and w.alert_btn.isVisibleTo(w)
        assert "Hands-Free" in w.alert_btn.toolTip()
        assert w.alert_icon.property("theme_icon") == "info:info:16"                 # F5.4 L2 (U-060): o tipo do alerta
        assert w.btn_pause.toolTip().endswith("(Ctrl+Alt+Shift+P)")                  # U-061: o atalho salvo, na dica
        signal = w.metaObject().method(w.metaObject().indexOfSignal("height_changed()"))
        assert w.isSignalConnected(signal)                                             # A-16: a pilha dos avisos acompanha
        now["t"] += 31                                                     # some em 30 s
        c._tick()
        assert not _shows_new(w)
        platform.next_endpoints = BASE_ENDPOINTS                           # saiu e voltou: avisa de novo
        c.devices.poll()
        platform.next_endpoints, platform.next_devices = with_bt(comm=BT_HFP.id), [MIC, BT]
        c.devices.poll()
        c._tick()
        assert w.alert_label.full_text == f"novo: {BT.name}"
        w.alert_btn.click()                                                # "Usar"
        rec = c.recorder
        assert rec is not None and rec.mic_open_id == BT.id and rec.mic_device is not None and rec.mic_device.id == BT.id
        assert platform.refreshes == 1 and platform._mute.switches[-1] == (BT.id, False)
        panel = c.devices.panel
        assert panel is not None and panel.isVisible() and panel.view is not None
        assert [r.key for r in panel.view.outputs][:3] == [f"out:{BT_HFP.id}", f"out:{BT_OUT.id}", "out:"]
        assert panel.status.text() == "Microfone trocado."
        c._tick()
        assert not _shows_new(w)                                           # o aviso saiu com o "Usar"
    finally:
        c.emergency_stop()


def test_the_mute_moves_with_the_microphone_and_the_first_poller_reading_is_ignored(qtbot, qapp, platform, store, monkeypatch):
    """A-18: gravando no mudo, a troca tira o mudo do antigo e põe no novo de uma vez; a leitura do poller que chega logo
    depois (do endpoint antigo) não desfaz o mudo da gravação; a seguinte vale."""
    from listener.ui.recording_devices import endpoint_device

    platform.devices = [MIC, BT]
    platform.endpoints = with_bt()
    platform.settle()
    c = _controller(qapp, platform, store, monkeypatch)
    try:
        assert c.start_recording(_system_cfg(), {"title": "mudo"}) and c.devices is not None
        c._stop_mute_poller()                                              # as leituras do poller vêm do teste
        c.toggle_mute()
        rec = c.recorder
        assert rec is not None and rec.muted and platform._mute.muted
        assert c.devices.switch_mic(endpoint_device(BT_MIC)).ok
        mute = platform._mute
        assert mute.switches[-1] == (BT.id, True) and mute.by_device[None] is False and mute.by_device[BT.id] is True
        c._on_os_mute(False)                                               # a leitura velha: ignorada
        assert rec.muted
        c._on_os_mute(False)                                               # a seguinte vale (o usuário desmutou no sistema)
        assert not rec.muted
        types = [e["type"] for e in store.list_events(rec.meeting_id)]
        assert types.count(EventType.UNMUTE) == 1 and EventType.DEVICE_CHANGE in types
    finally:
        c.emergency_stop()


def test_the_watch_follows_the_default_and_the_chosen_microphone_that_leaves(qtbot, platform, store, recorder_factory):
    """A-20: "acompanha" segue o padrão; pergunta 10 e A-16: o escolhido que sai cai no padrão (`missing`, com aviso) e
    volta sozinho (`back`); a troca que o player não deixou reler tenta de novo em 10 s, sem fechar nada antes."""
    from listener.ui.recording_devices import RecordingDevices

    now = {"t": 0.0}
    follow = recorder_factory()
    follow.start()
    watch = RecordingDevices(platform, follow, monotonic=lambda: now["t"])
    watch.start()
    platform.next_endpoints, platform.next_devices = with_bt(mic=BT.id), [MIC, BT]
    held = {"n": 1}
    platform.other_streams = lambda: held["n"]                             # o player não solta: a troca espera
    watch.poll()
    assert follow.mic_open_id == MIC.id and watch._retry_at == 10.0
    watch.poll()                                                           # mesma assinatura e antes do prazo: nada
    assert follow.mic_open_id == MIC.id
    held["n"], now["t"] = 0, 11.0
    watch.poll()
    assert follow.mic_open_id == BT.id and watch._retry_at is None
    watch.stop()
    follow.stop()
    platform.endpoints, platform.devices, platform.fail_ids = with_bt(), [MIC, BT], set()
    platform.settle()
    chosen = recorder_factory(make_config(mic_device=BT))
    chosen.start()
    watch = RecordingDevices(platform, chosen, monotonic=lambda: now["t"])
    watch.start()
    bt_stream = next(s for s in platform.mic_streams if s.device_id == BT.id and not s.stopped)
    platform.next_endpoints, platform.next_devices, platform.fail_ids = BASE_ENDPOINTS, [MIC], {BT.id}   # o fone saiu
    bt_stream.fail("dispositivo removido")
    platform.pump(0.2)
    chosen.check_devices()                                 # R-04: a reabertura de 200 ms cai no padrão antes da vigia
    assert chosen.mic_open_id == MIC.id and chosen.mic_device == BT
    watch.poll()
    notice = watch.notice()
    assert notice is not None and notice.text.startswith("microfone escolhido saiu")
    watch.poll()                                           # o mesmo episódio: não avisa de novo
    platform.next_endpoints, platform.next_devices, platform.fail_ids = with_bt(), [MIC, BT], set()   # voltou
    watch.poll()
    assert chosen.mic_open_id == BT.id and watch.notice() is not None and watch.notice().text == "microfone escolhido voltou"
    watch.stop()
    chosen.stop()
    changes = [e["data"] for e in store.list_events(chosen.meeting_id) if e["type"] == EventType.DEVICE_CHANGE]
    assert [(d["endpoint_id"], d["reason"], d.get("wanted_id")) for d in changes] == [(MIC.id, "missing", BT.id),
                                                                                      (BT.id, "back", None)]


def test_the_warmup_offers_follow_and_one_row_per_output(qtbot, store, platform):
    """U-045 e U-051: o microfone começa no "Padrão do Windows (acompanha)" (a gravação recebe None); a tabela de fontes tem
    "Sistema inteiro: <saída>" por saída ativa, com a de comunicação marcada; a escolha volta pelo `warmup.last_output_id` e
    a saída que sai devolve a seleção à padrão."""
    from listener.ui.sources import SYSTEM_FOLLOW, SourceKey
    from listener.ui.warmup import WarmupDialog

    platform.endpoints = with_bt(comm=BT_HFP.id)
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    assert dlg.mic_combo.itemText(0) == "Padrão do Windows (acompanha)" and dlg.mic_combo.currentData() == ""
    table = dlg.sources.table
    rows = [(table.item(r, 0).text(), table.item(r, 1).text()) for r in range(table.rowCount())]
    assert rows[:4] == [("Sistema inteiro", "saída padrão (acompanha) (agora: Alto-falantes Fake)"),
                        ("Sistema inteiro", "Alto-falantes Fake"), ("Sistema inteiro", BT_OUT.name),
                        ("Sistema inteiro (comunicação)", BT_HFP.name)]
    assert dlg.sources.select_key(SourceKey("system", output_id=BT_OUT.id))
    cfg, fields = dlg.result_config()
    assert cfg.mic_device is None and cfg.source.output_id == BT_OUT.id and fields["platform"] == "sistema"
    dlg.accept()
    assert store.get_setting("warmup.last_output_id") == BT_OUT.id and store.get_setting("warmup.last_mic_device_id") == ""
    again = WarmupDialog(platform, store)
    qtbot.addWidget(again)
    assert again.sources.selected_key() == SourceKey("system", output_id=BT_OUT.id) and again.mic_combo.currentIndex() == 0
    platform.endpoints = BASE_ENDPOINTS                                    # a saída escolhida saiu
    assert again.sources.refresh_outputs() and again.sources.selected_key() == SYSTEM_FOLLOW
    assert again.sources.selected_spec().output_id == "" and SPEAKERS.id not in again.sources.selected_spec().label
    again.reject()


def test_the_player_lets_go_of_the_output_before_the_mic_switch_rereads(qtbot, qapp, platform, store, monkeypatch):
    """A-17 e o Comportamento da spec: com a fonte de um app o player toca durante a gravação; a troca para o fone que
    chegou relê o PortAudio, então o player solta a saída antes ("pausado para trocar o microfone") e só então o
    PortAudio reinicia; retomar, com a fonte de um app, toca direto."""
    import numpy as np

    from listener.capture.audio import encode_flac
    from listener.ui import controller as ctl
    from listener.ui.player import PAUSED, PLAYING
    from tests.fake_player import RealtimeOutput

    mid = store.create_meeting("para tocar", None, None, None)
    for ch in ("L", "R"):
        x = np.full(16000 * 30, 0.2, np.float32)
        store.insert_chunk(mid, ch, 0, 0, len(x), 16000, 0, encode_flac(x, 16000))
    store.finish_meeting(mid, 30_000)
    c = _controller(qapp, platform, store, monkeypatch)
    outs: list[RealtimeOutput] = []

    def factory(_endpoint: str | None = None) -> RealtimeOutput:
        outs.append(RealtimeOutput())
        return outs[-1]

    c.player.engine.factory = factory
    platform.other_streams = lambda: sum(1 for o in outs if "close" not in o.calls)   # o player segura o PortAudio
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5,
                       stop_timeout_s=1.0, retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, {"title": "player"}) and c.devices is not None
        assert c.player.play_from(mid, 0)
        qtbot.waitUntil(lambda: bool(outs) and len(outs[0].chunks) >= 2, timeout=3000)
        platform.next_endpoints, platform.next_devices = with_bt(), [MIC, BT]
        c.devices.poll()
        c.devices.use_new()                                                 # "Usar": a troca relê o PortAudio
        rec = c.recorder
        assert rec is not None and rec.mic_open_id == BT.id and platform.refreshes == 1
        assert outs[0].calls[-1] == "close"
        assert (c.player.state().state, c.player.state().message) == (PAUSED, ctl.PLAYER_PAUSED_MIC)
        assert c.player.toggle()                                            # fonte de um app: retoma sem perguntar
        qtbot.waitUntil(lambda: len(outs) == 2 and len(outs[1].chunks) >= 2, timeout=3000)
        assert c.player.state().state == PLAYING
    finally:
        c.emergency_stop()


# ---------------------------------------------------------------- revisão do L4
def test_the_chosen_output_that_leaves_warns_even_when_the_reopening_got_there_first(qtbot, platform, store,
                                                                                       recorder_factory):
    """R-04, na saída: a escolhida some, a reabertura de 200 ms do gravador já abriu a padrão; a vigia de 2 s não tem o que
    trocar, mas o aviso vem do estado (a escolhida sumiu e outra grava)."""
    from listener.ui.recording_devices import RecordingDevices

    platform.endpoints = with_bt()
    rec = recorder_factory(make_config(source=SourceSpec("system", label="Sistema inteiro: AirPods", output_id=BT_OUT.id)))
    rec.start()
    watch = RecordingDevices(platform, rec)
    watch.start()
    platform.next_endpoints = BASE_ENDPOINTS                               # o fone saiu
    platform.audio_endpoints()
    platform.loopbacks[-1].fail("dispositivo removido")
    platform.pump(0.2)
    rec.check_devices()
    assert rec.output_open_id == SPEAKERS.id
    watch.poll()
    notice = watch.notice()
    assert notice is not None and notice.text == "saída escolhida saiu: gravando a saída padrão"
    watch.stop()
    rec.stop()


def test_a_new_device_the_follow_already_opened_offers_no_use(qtbot, platform, store, recorder_factory):
    """R-09: o fone chega e vira o padrão; o "acompanha" já trocou para ele: o aviso diz "gravando nele" e mostra as saídas,
    sem "Usar" (que trocaria de novo, com outro buraco, e tiraria o "acompanha" sem dizer)."""
    from listener.ui.recording_devices import ACTION_OUTPUTS, RecordingDevices

    rec = recorder_factory()
    rec.start()
    watch = RecordingDevices(platform, rec)
    watch.start()
    platform.next_endpoints, platform.next_devices = with_bt(mic=BT.id), [MIC, BT]
    watch.poll()
    assert rec.mic_open_id == BT.id and rec.mic_device is None
    notice = watch.notice()
    assert notice is not None and notice.action == ACTION_OUTPUTS and notice.button != "Usar"
    assert notice.text == f"novo: {BT.name} (gravando nele)"
    opened = len(platform.mic_streams)
    watch.use_new()                                                        # nem o "Usar" de um aviso velho troca de novo
    assert len(platform.mic_streams) == opened and rec.mic_device is None and watch.panel is not None
    watch.stop()
    rec.stop()


def test_the_panel_and_the_queue_without_an_owner_are_freed_when_dropped(qtbot, store):
    """R-06: sem dono, o painel e a fila somem quando a última referência some; um `partial`/lambda com `self` numa conexão
    de sinal de um filho os prendia do lado C++ (o coletor do Python não vê o ciclo) até a derrubada do PySide."""
    import gc
    import weakref

    from listener.ui.devices_panel import DevicesPanel
    from listener.ui.queue import QueueDialog

    for make in (DevicesPanel, lambda: QueueDialog(store)):
        window = make()
        ref = weakref.ref(window)
        del window
        gc.collect()
        assert ref() is None


def test_the_exit_deletes_the_windows_the_controller_created_without_an_owner(qtbot, qapp, platform, store, monkeypatch):
    """R-06: na saída, a fila, a reunião aberta e o widget de uma gravação em curso são apagados no laço do Qt; vivos até a
    derrubada do PySide, seriam destruídos fora de ordem (0xC0000409)."""
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    c = _controller(qapp, platform, store, monkeypatch)
    mid = store.create_meeting("saída", None, None, None)
    store.finish_meeting(mid, 1000)
    c.open_queue()
    queue, win = c._queue_dialog, c.open_meeting(mid)
    assert c.start_recording(_system_cfg(), {"title": "gravando na saída"}) and c.widget is not None
    widget = c.widget
    c.emergency_stop()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any(shiboken6.isValid(w) for w in (queue, win, widget))


def test_the_mute_notice_at_the_start_keeps_the_device_name_off_the_log(qtbot, qapp, platform, store, monkeypatch, caplog):
    """R-05 (A-22): o aviso "Não achei o endpoint de '<nome>'" mostra o nome na tela; o log e o texto curto (gravando sem a
    exclusão da captura) levam o id resumido."""
    import logging

    from listener.platform.base import AudioDevice

    named = AudioDevice("name:AirPods de Fulano", "AirPods de Fulano", "fake", 1, 16000, index=1)
    platform.devices = [MIC, named]
    monkeypatch.setattr(platform._mute, "set_device", lambda *_a, **_k: False)       # o mudo não achou o endpoint
    c = _controller(qapp, platform, store, monkeypatch)
    try:
        with caplog.at_level(logging.INFO, logger="listener"):
            cfg = RecordConfig(mic_device=named, source=SourceSpec("system", label="Sistema inteiro"), chunk_seconds=0.5,
                               stop_timeout_s=1.0, retry_base_s=0.001)
            assert c.start_recording(cfg, {"title": "mudo"})
        notice = c.notices[-1]
        assert "AirPods de Fulano" in notice.text and "Fulano" not in notice.short_text
        assert "Não achei o endpoint" in caplog.text and "Fulano" not in caplog.text
    finally:
        c.emergency_stop()
