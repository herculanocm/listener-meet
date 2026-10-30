"""U-043 (F5.2): o fone conectado ou removido com o "Antes de gravar" ou o assistente abertos entra ou sai do combo sem
reiniciar o app; o medidor fecha só para o reinício e reabre; com outro stream aberto (o player) a lista espera."""
from __future__ import annotations

import pytest

from listener.platform.base import AudioDevice

pytest.importorskip("PySide6")

BT = AudioDevice("bt-1", "Fone BT (Hands-Free)", "fake", 1, 16000, index=1)
FOLLOW = "Padrão do Windows (acompanha): agora Mic Fake"        # F5.9 (item 8): com o padrão de console de agora


def _items(combo) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def test_warmup_picks_up_a_new_headset_and_keeps_the_choice(qtbot, store, platform):
    from listener.ui.warmup import WarmupDialog

    store.set_setting("warmup.last_mic_device_id", "fake-mic-0")       # um microfone escolhido (F5.3: sem escolha, "acompanha")
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    meter = platform.live_streams()
    assert len(meter) == 1
    dlg._refresh_devices()                                            # nada mudou: o medidor fica
    assert platform.refreshes == 0 and platform.live_streams() == meter
    platform.next_devices = [platform.devices[0], BT]                 # o fone chegou
    dlg._refresh_devices()
    assert _items(dlg.mic_combo) == [FOLLOW, "Mic Fake", "Fone BT (Hands-Free)"] and dlg.mic_combo.currentData() == "fake-mic-0"
    assert dlg.mic_note.text() == "novo: Fone BT (Hands-Free)" and platform.refreshes == 1
    assert meter[0].stopped and len(platform.live_streams()) == 1 and platform.live_streams() != meter   # reaberto
    dlg.mic_combo.setCurrentIndex(2)                                  # escolheu o fone; depois ele sai
    platform.next_devices = [platform.devices[0]]
    dlg._refresh_devices()
    assert _items(dlg.mic_combo) == [FOLLOW, "Mic Fake"] and dlg.mic_combo.currentData() == ""   # F5.3: cai no "acompanha"
    assert "o microfone escolhido saiu" in dlg.mic_note.text() and len(platform.live_streams()) == 1
    dlg.reject()


def test_warmup_waits_while_another_stream_holds_the_audio_library(qtbot, store, platform):
    from listener.ui.warmup import WarmupDialog

    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    other = platform.open_mic(None, 16000, lambda *_: None)          # o player tocando, por exemplo
    other.start()
    platform.next_devices = [platform.devices[0], BT]
    dlg._refresh_devices()
    assert dlg.mic_combo.count() == 2 and platform.refreshes == 0 and len(platform.live_streams()) == 2   # nada fechou
    other.stop()
    dlg._testing = True                                               # o teste de 3 s lê o medidor: também espera
    dlg._refresh_devices()
    assert platform.refreshes == 0
    dlg._testing = False
    dlg._refresh_devices()
    assert dlg.mic_combo.count() == 3 and platform.refreshes == 1                   # "acompanha" e os dois microfones
    dlg.reject()


def test_wizard_microphone_page_picks_up_a_new_headset(qtbot, platform):
    from listener.ui.wizard import MicPage

    page = MicPage(platform)
    qtbot.addWidget(page)
    page.initializePage()
    platform.next_devices = [platform.devices[0], BT]
    page._refresh_devices()
    assert _items(page.combo) == ["Mic Fake", "Fone BT (Hands-Free)"] and "novo: Fone BT" in page.status.text()
    assert len(platform.live_streams()) == 1
    page.validatePage()
    assert platform.live_streams() == []
