import pytest

from listener.platform.win32.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, parse_combo


def test_parse_letters_and_modifiers():
    assert parse_combo("ctrl+alt+shift+r") == (MOD_CONTROL | MOD_ALT | MOD_SHIFT, ord("R"))
    assert parse_combo("Ctrl + Shift + M") == (MOD_CONTROL | MOD_SHIFT, ord("M"))
    assert parse_combo("win+5") == (MOD_WIN, ord("5"))


def test_parse_function_keys_and_named():
    assert parse_combo("ctrl+shift+f9") == (MOD_CONTROL | MOD_SHIFT, 0x78)
    assert parse_combo("f12") == (0, 0x7B)                   # tecla de função pode ir sem modificador
    assert parse_combo("ctrl+alt+space") == (MOD_CONTROL | MOD_ALT, 0x20)


@pytest.mark.parametrize("bad", ["", "ctrl+", "r", "ctrl+r+s", "ctrl+hiper", "ctrl+ç"])
def test_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_combo(bad)
