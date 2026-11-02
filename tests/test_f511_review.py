"""A rodada final da F5.11: os achados e as observações da revisão do diff e os ajustes das fotos.

O título que não grava ao fechar não pula o resto; o alerta "novo: <aparelho>" corta no meio, com o nome inteiro na dica; o
painel de dispositivos diz "você e a sala" com a sala presencial (A-41); o `classify` só marca o token inválido pelo 401 de
verdade; caixa apagada do "Antes de gravar" nunca aparece marcada; a sonda mede o botão pelo que ele mostra e só conta a
célula cortada sem o texto inteiro na dica."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from listener.pipeline import hf_check
from listener.pipeline.hf_check import Status

pytest.importorskip("PySide6")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LONG_NAME = "Fone de ouvido (Hands-Free AG Audio) do aparelho com um nome muito comprido mesmo"


def test_the_title_that_fails_to_save_on_close_goes_to_the_log_and_the_window_still_closes(qtbot, store, monkeypatch, caplog):
    from listener.ui.meeting import MeetingWindow

    mid = store.create_meeting("Reunião", "sistema", None, None)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)

    def broken() -> bool:
        raise RuntimeError("banco fechado")

    panels: list[int] = []
    closed: list[int] = []
    monkeypatch.setattr(win.llm.title_tab, "save_if_changed", broken)
    monkeypatch.setattr(win.chat, "close_panel", lambda: panels.append(1))
    win.closed.connect(closed.append)
    with caplog.at_level(logging.WARNING):
        win.close()
    assert panels == [1] and closed == [mid] and "o título mexido não gravou" in caplog.text


def test_the_new_device_alert_cuts_the_name_in_the_middle_with_the_whole_name_in_the_tip(qtbot):
    from listener.ui.widget import RecordingWidget

    w = RecordingWidget(live_line=False, shortcuts={})
    qtbot.addWidget(w)
    w.show()
    text = f"novo: {LONG_NAME}"
    w.show_alert(text, "Usar", "use", "Usar troca o microfone para ele.", "info", middle=True)
    head, sep, tail = " ".join(w.alert_label.text().split()).partition("…")
    assert sep and head.startswith("novo: Fone") and text.endswith(tail) and tail      # o começo e o fim do nome à vista
    assert w.alert_label.toolTip().startswith(text)                                     # o nome inteiro na dica
    w.show_alert(text, "Usar", "use", "", "info")
    assert w.alert_label.text().endswith("…")                                           # sem `middle`: o fim sai, como antes


def test_the_devices_panel_says_you_and_the_room_in_an_in_person_meeting(qtbot, store, platform, recorder_factory):
    from listener.model import MIC_ROOM, MIC_YOU
    from listener.ui.recording_devices import RecordingDevices

    rec = recorder_factory()
    rec.start()
    devices = RecordingDevices(platform, rec, rooms=store.meetings)
    panel = devices.open_panel()
    qtbot.addWidget(panel)
    assert panel.mic_box.title() == MIC_YOU
    store.meetings.set_in_room(rec.meeting_id, True)
    devices.open_panel()
    assert panel.mic_box.title() == MIC_ROOM                                           # A-41
    rec.stop()


def test_only_a_real_401_marks_the_token_invalid():
    http = type("HfHubHTTPError", (Exception,), {})
    assert hf_check.classify(http("401 Client Error: Unauthorized for url")) == (Status.INVALID, hf_check.INVALID_TEXT)
    assert hf_check.classify(http("Invalid response from the server")) is None         # conta tentativa no worker


def test_a_disabled_live_box_is_never_shown_checked(qtbot, store, platform, monkeypatch):
    from listener.ui.warmup import WarmupDialog

    monkeypatch.setenv("LISTENER_LIVE", "fake")                                        # os modelos e os nomes à mão
    store.set_setting("live.enabled", False)
    store.set_setting("live.names", True)
    store.set_setting("live.partial", True)
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    for box in (dlg.names_check, dlg.partial_check):                                   # sem a legenda: apagadas e desmarcadas
        assert not box.isEnabled() and not box.isChecked()
    assert dlg.live.names_wanted and dlg.live.partial_wanted                           # o pedido fica guardado
    dlg.live_check.click()                                                             # com a legenda, os nomes acendem
    assert dlg.names_check.isEnabled() and dlg.names_check.isChecked()
    assert not dlg.partial_check.isEnabled() and not dlg.partial_check.isChecked()     # o parcial segue sem a chave
    dlg.reject()


@pytest.fixture
def cuts() -> Any:
    sys.path.insert(0, str(SCRIPTS))
    try:
        import probe_cuts
        yield probe_cuts
    finally:
        sys.path.remove(str(SCRIPTS))


def test_the_probe_measures_buttons_by_what_they_show_and_cells_by_the_tip(qtbot, cuts):
    from PySide6 import QtGui
    from PySide6.QtWidgets import QPushButton, QTableWidget, QTableWidgetItem

    more = QPushButton("⋯")
    more.setFixedWidth(44)
    long = QPushButton("um texto de botão comprido demais")
    long.setFixedWidth(60)
    for button in (more, long):
        qtbot.addWidget(button)
    assert cuts._button_cut(more) is None and cuts._button_cut(long) is not None     # o mínimo de 80 px do estilo não conta
    table = QTableWidget(3, 1)
    qtbot.addWidget(table)
    table.setColumnWidth(0, 40)
    text = "uma reunião de título bem comprido"
    for row, tip in enumerate(("", text, QtGui.Qt.convertFromPlainText(text))):
        item = QTableWidgetItem(text)
        item.setToolTip(tip)
        table.setItem(row, 0, item)
    table.resize(200, 200)
    table.show()
    assert [c["what"].split()[1] for c in cuts._cells_cut(table)] == ["0,0"]            # só a sem o texto inteiro na dica
