from pathlib import Path

from listener.config import bootstrap
from listener.platform.common.hotkeys import to_pynput
from listener.platform.common.paths import synced_folder_name


def test_hotkey_conversion():
    assert to_pynput("ctrl+shift+r") == "<ctrl>+<shift>+r"
    assert to_pynput("Ctrl + Alt + F9") == "<ctrl>+<alt>+<f9>"
    assert to_pynput("win+m") == "<cmd>+m"


def test_synced_folder_detection():
    assert synced_folder_name(Path("C:/Users/x/OneDrive - Empresa/docs")) == "OneDrive"
    assert synced_folder_name(Path("/home/x/Dropbox/app")) == "Dropbox"
    assert synced_folder_name(Path("/home/x/.local/share/listener-meet")) is None


def test_bootstrap_pointer_roundtrip(tmp_path):
    cfg = tmp_path / "cfg"
    assert bootstrap.read_db_dir(cfg) is None
    p = bootstrap.write_db_dir(cfg, tmp_path / "db")
    assert p.exists() and bootstrap.read_db_dir(cfg) == tmp_path / "db"
