from pathlib import Path

import pytest

from listener.config import bootstrap
from listener.platform.common.hotkeys import parse_combo, to_pynput, validate_combo
from listener.platform.common.paths import synced_folder_name


def test_hotkey_conversion():
    assert to_pynput("ctrl+shift+r") == "<ctrl>+<shift>+r"
    assert to_pynput("Ctrl + Alt + F9") == "<ctrl>+<alt>+<f9>"
    assert to_pynput("win+m") == "<cmd>+m"
    assert to_pynput("ctrl+alt+space") == "<ctrl>+<alt>+<space>"


def test_common_grammar_is_the_same_everywhere():
    assert parse_combo("ctrl+alt+shift+r") == ({"ctrl", "alt", "shift"}, "r")
    assert parse_combo("f12") == (frozenset(), "f12")
    assert validate_combo("ctrl+alt+shift+m") is None
    for bad in ("", "ctrl+", "r", "ctrl+r+s", "ctrl+hiper", "ctrl+ç", "shift+f25"):
        assert validate_combo(bad)                       # mesma recusa no Windows e no pynput
    with pytest.raises(ValueError):
        to_pynput("ctrl+ç")


def test_synced_folder_detection():
    assert synced_folder_name(Path("C:/Users/x/OneDrive - Empresa/docs")) == "OneDrive"
    assert synced_folder_name(Path("/home/x/Dropbox/app")) == "Dropbox"
    assert synced_folder_name(Path("/home/x/.local/share/listener-meet")) is None


def test_bootstrap_pointer_roundtrip(tmp_path):
    cfg = tmp_path / "cfg"
    assert bootstrap.read_db_dir(cfg) is None
    p = bootstrap.write_db_dir(cfg, tmp_path / "db")
    assert p.exists() and bootstrap.read_db_dir(cfg) == tmp_path / "db"


def test_pick_device_prefers_stable_id_then_name_and_ignores_old_indexes():
    from listener.platform.base import AudioDevice, pick_device

    intel = "Grupo de microfones (Tecnologia Intel Smart Sound)"
    devs = [AudioDevice("{0.0.1.00000000}.{aaa}", "Headset USB", "WASAPI", 1, 48000, index=3),
            AudioDevice(f"name:{intel}", intel, "WASAPI", 2, 48000, index=9)]
    assert pick_device(devs, "{0.0.1.00000000}.{aaa}", "outro nome") is devs[0]
    assert pick_device(devs, "9", "Headset USB") is devs[0]                 # índice antigo é ignorado; o nome resolve
    assert pick_device(devs, None, "Grupo de microfones (Tecnologia Int") is devs[1]   # nome truncado
    assert pick_device(devs, "{0.0.1.00000000}.{zzz}", "Webcam") is None


@pytest.mark.parametrize(("n", "text"), [(0, "0 B"), (1023, "1023 B"), (1536, "1,5 KB"), (5 * 1024**3, "5,0 GB"), (2 * 1024**4, "2,0 TB")])
def test_human_size(n, text):
    from listener.ui.common import human_size

    assert human_size(n) == text


def test_clamp_to_screens():
    from listener.ui.common import clamp_to_screens

    screens = [(0, 0, 1920, 1040), (1920, 0, 2560, 1400)]
    assert clamp_to_screens(100, 100, 320, 96, screens) == (100, 100)
    assert clamp_to_screens(1900, 1000, 320, 96, screens[:1]) == (1600, 944)      # só um monitor: encosta na borda
    assert clamp_to_screens(1900, 1000, 320, 96, screens) == (1920, 1000)         # dois: fica no que mais sobrepõe
    assert clamp_to_screens(4300, 1350, 320, 96, screens) == (4160, 1304)
    assert clamp_to_screens(-900, -900, 320, 96, screens) is None                 # monitor desplugado
