"""F5.11 L5: os casos-limite (U-114, U-115; A-32, P-04). O gravador guarda no evento `stop` quanto tempo cada canal teve
som; o resumo da gravação só diz "com falha" pela saída sem som ou por um canal que não abriu (o microfone mudo é aviso na
linha dele); a reunião processada sem fala abre na Transcrição dizendo por quê, e a de antes desta entrega diz o genérico."""
from __future__ import annotations

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.model import EventType, MeetingStatus
from listener.ui.sound_check import GENERIC, Sound, channel_states, failed, reason, source_text

RATE = 16000


def _silent_meeting(store, stop: dict | None, channels: tuple[str, ...] = ("L", "R")) -> int:
    mid = store.create_meeting("Sem fala", "ms-teams", None, None)
    for ch in channels:
        store.insert_chunk(mid, ch, 0, 0, RATE, RATE, 0, encode_flac(np.zeros(RATE, np.float32), RATE))
    store.add_event(mid, 0, EventType.START, {"source": "app", "exe": "ms-teams.exe"})
    store.add_event(mid, 1000, EventType.STOP, stop or {})
    store.finish_meeting(mid, 1000)
    return mid


# ---------------------------------------------------------------- A-32: o `signal_ms` no evento `stop`
def test_the_stop_event_keeps_how_long_each_channel_had_sound(platform, store, recorder_factory):
    rec = recorder_factory()
    rec.start()
    platform.streams[-1].amplitude = 0.0                               # a saída sem som: o driver entrega zeros
    platform.pump(1.3)
    stats = rec.stop()
    assert stats["signal_ms"]["R"] == 0 and 1100 <= stats["signal_ms"]["L"] <= 1300
    stop = next(e for e in reversed(store.list_events(rec.meeting_id)) if e["type"] == EventType.STOP)
    assert stop["data"]["signal_ms"] == stats["signal_ms"]
    muted = recorder_factory()
    muted.start()
    muted.set_muted(True)                                              # mudo não conta como som
    platform.pump(0.5)
    assert muted.stop()["signal_ms"]["L"] == 0


def test_who_had_sound_decides_the_failure_and_the_reason():
    both = {"L": {"n": 1}, "R": {"n": 1}}
    assert channel_states(None, both) is None                                          # de antes da F5.11
    assert channel_states({"L": 900, "R": 0}, both) == {"L": Sound.OK, "R": Sound.SILENT}
    assert channel_states({"L": 900, "R": 900}, {"L": {"n": 1}}) == {"L": Sound.OK, "R": Sound.CLOSED}
    assert failed({"L": Sound.OK, "R": Sound.SILENT}, None) and failed({"L": Sound.CLOSED, "R": Sound.OK}, None)
    assert not failed({"L": Sound.SILENT, "R": Sound.OK}, None)                        # P-04: o microfone mudo é aviso
    assert failed(None, ["L3"]) and not failed(None, [])                               # o pedaço fora do banco segue falha
    start = {"type": EventType.START, "data": {"source": "app", "exe": "ms-teams.exe"}}
    assert source_text([start]) == "ms-teams"
    assert source_text([start, {"type": EventType.SOURCE_CHANGE, "data": {"kind": "system", "exe": ""}}]) == "Sistema inteiro"
    assert reason({"L": Sound.OK, "R": Sound.SILENT}, "ms-teams") == ("Nenhuma fala nesta reunião: o microfone teve som; a "
                                                                      "saída escolhida (ms-teams) ficou sem som.")
    assert "música" in reason({"L": Sound.OK, "R": Sound.OK}, "Sistema inteiro")


# ---------------------------------------------------------------- U-115 (P-04): o resumo da gravação
@pytest.mark.parametrize(("signal", "channels", "failure", "notes"), [
    ({"L": 800, "R": 800}, ("L", "R"), False, ()),
    ({"L": 0, "R": 800}, ("L", "R"), False, ("L",)),                  # o microfone mudo: aviso na linha dele
    ({"L": 800, "R": 0}, ("L", "R"), True, ("R",)),                   # a saída sem som: falha
    ({"L": 800, "R": 800}, ("L",), True, ("R",)),                     # a saída não abriu: falha
])
def test_the_summary_fails_only_for_a_silent_output_or_a_closed_channel(qtbot, store, signal, channels, failure, notes):
    from listener.ui.summary import CHANNEL_NOTES, SAVED, SAVED_WITH_FAILURE, SummaryDialog

    mid = _silent_meeting(store, None, channels)
    dlg = SummaryDialog(store, mid, {"duration_s": 1.0, "warnings": [], "signal_ms": signal})
    qtbot.addWidget(dlg)
    assert dlg.failed is failure and dlg.windowTitle() == (SAVED_WITH_FAILURE if failure else SAVED)
    for ch in ("L", "R"):
        shown = any(note in dlg.channels[ch].text() for (c, _s), note in CHANNEL_NOTES.items() if c == ch)
        assert shown is (ch in notes)
    old = SummaryDialog(store, mid, {"duration_s": 1.0, "warnings": []})                # sem o `signal_ms`: como antes
    qtbot.addWidget(old)
    assert old.windowTitle() == SAVED


# ---------------------------------------------------------------- U-114: a reunião sem fala
def test_a_processed_meeting_without_speech_opens_on_the_transcript_saying_why(qtbot, store):
    from listener.ui.meeting import MeetingWindow

    mid = _silent_meeting(store, {"signal_ms": {"L": 800, "R": 0}})
    first = MeetingWindow(store, mid)
    qtbot.addWidget(first)
    assert first.tabs.currentWidget() is not first.transcript                          # ainda não processada: a de sempre
    first.close()
    store.meetings.set_status(mid, MeetingStatus.PROCESSED)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    assert win.tabs.currentWidget() is win.transcript
    assert win.transcript.empty.label.text() == reason({"L": Sound.OK, "R": Sound.SILENT}, "ms-teams")
    win.close()
    old = _silent_meeting(store, {"chunks": {"L": 1, "R": 1}})                         # o `stop` de antes da F5.11
    store.meetings.set_status(old, MeetingStatus.PROCESSED)
    generic = MeetingWindow(store, old)
    qtbot.addWidget(generic)
    assert generic.tabs.currentWidget() is generic.transcript and generic.transcript.empty.label.text() == GENERIC
    generic.close()
