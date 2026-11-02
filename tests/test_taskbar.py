"""F5.13 (L2, U-131): o Jump List. As partes puras (as quatro tarefas na ordem da spec, com o `.exe` de agora e o `--open` de
cada uma; só o `.exe` publica; a plataforma que recusa não derruba nada; o caminho das chamadas com objetos falsos, também
o erro no meio, que abandona a lista) e, só no Windows, a lista de verdade: o título, os argumentos e o ícone lidos de
volta de uma tarefa e a lista montada inteira e abandonada (`BeginList` e `AbortList`). Nunca o `CommitList`: mexeria na
barra de tarefas de quem roda o teste. L3: a dica de fixar na última página do assistente, só no `.exe` (U-132), e os
atalhos que registraram na coluna de atalho do menu da bandeja, trocados com as Configurações (U-133)."""
from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from listener.platform.base import Platform, TaskbarTask
from listener.ui import taskbar
from tests.fake_platform import FakePlatform

ROOT = Path(__file__).resolve().parent.parent
EXE = r"C:\Programas\listener-meet\listener-meet.exe"
TITLES = ["Gravar…", "Histórico", "Fila de processamento", "Configurações"]
windows = pytest.mark.skipif(sys.platform != "win32", reason="o Jump List é do Windows")


def test_the_four_tasks_in_the_spec_order_open_the_current_exe(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", EXE)
    tasks = taskbar.taskbar_tasks()
    assert [t.title for t in tasks] == TITLES
    assert {t.program for t in tasks} == {EXE}                     # o caminho de agora: a pasta pode ter mudado
    assert [t.arguments for t in tasks] == [("--open", "record"), ("--open", "history"), ("--open", "queue"),
                                            ("--open", "settings")]


def test_only_the_exe_publishes_and_a_refusal_breaks_nothing(monkeypatch, tmp_path):
    platform = FakePlatform(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert taskbar.publish_tasks(platform) is False and platform.taskbar_tasks is None      # no venv abriria um console
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", EXE)
    assert taskbar.publish_tasks(platform) is True
    assert platform.taskbar_tasks is not None and [t.title for t in platform.taskbar_tasks] == TITLES
    platform.taskbar_ok = False
    assert taskbar.publish_tasks(platform) is False


def test_the_other_platforms_have_no_taskbar_menu_yet():
    assert Platform.set_taskbar_tasks(None, [TaskbarTask("Histórico", EXE)]) is False   # type: ignore[arg-type]


# ---------------------------------------------------------------- o caminho das chamadas, com objetos falsos (Windows)
class FakeList:
    def __init__(self, calls: list[Any]) -> None:
        self.calls = calls

    def SetAppID(self, app_id: str) -> None:  # noqa: N802 (o nome do método COM)
        self.calls.append(("SetAppID", app_id))

    def BeginList(self, _riid: Any) -> tuple[int, None]:  # noqa: N802 (o nome do método COM)
        self.calls.append("BeginList")
        return 10, None

    def AddUserTasks(self, _items: Any) -> None:  # noqa: N802 (o nome do método COM)
        self.calls.append("AddUserTasks")

    def CommitList(self) -> None:  # noqa: N802 (o nome do método COM)
        self.calls.append("CommitList")

    def AbortList(self) -> None:  # noqa: N802 (o nome do método COM)
        self.calls.append("AbortList")


class FakeItems:
    def __init__(self, calls: list[Any], fail_at: int | None) -> None:
        self.calls, self.fail_at = calls, fail_at

    def AddObject(self, link: Any) -> None:  # noqa: N802 (o nome do método COM)
        if len([c for c in self.calls if c == "AddObject"]) == self.fail_at:
            raise OSError(None, "falhou", None, 0x80004005 - 2**32)
        self.calls.append("AddObject")

    def QueryInterface(self, _interface: Any) -> FakeItems:  # noqa: N802 (o nome do método COM)
        return self


def _fake_com(monkeypatch: pytest.MonkeyPatch, fail_at: int | None = None) -> list[Any]:
    from listener.platform.win32 import jumplist

    calls: list[Any] = []
    made = {jumplist.ICustomDestinationList: FakeList(calls), jumplist.IObjectCollection: FakeItems(calls, fail_at)}
    monkeypatch.setattr(jumplist, "_create", lambda _clsid, interface: made[interface])
    monkeypatch.setattr(jumplist, "shell_link", lambda task: task.title)
    return calls


@windows
@pytest.mark.parametrize("commit", [True, False])
def test_the_calls_go_in_the_order_and_only_the_app_commits(monkeypatch, commit):
    from listener.platform.win32 import jumplist

    calls = _fake_com(monkeypatch)
    tasks = [TaskbarTask(t, EXE, ("--open", "x")) for t in TITLES]
    assert jumplist.publish("listener-meet", tasks, commit=commit) is True
    assert calls == [("SetAppID", "listener-meet"), "BeginList", *["AddObject"] * 4, "AddUserTasks",
                     "CommitList" if commit else "AbortList"]


@windows
def test_an_error_midway_abandons_the_list_and_logs_one_line_without_the_path(monkeypatch, caplog):
    from listener.platform.win32 import jumplist

    calls = _fake_com(monkeypatch, fail_at=2)
    with caplog.at_level(logging.INFO, logger="listener.platform.win32.jumplist"):
        assert jumplist.publish("listener-meet", [TaskbarTask(t, EXE) for t in TITLES]) is False
    assert calls == [("SetAppID", "listener-meet"), "BeginList", "AddObject", "AddObject", "AbortList"]
    assert "Jump List não atualizado (OSError 0x80004005)" in caplog.text and "Programas" not in caplog.text


# ---------------------------------------------------------------- a lista de verdade (Windows; nunca o `CommitList`)
@windows
def test_a_task_reads_back_the_exe_the_arguments_the_icon_and_the_title():
    from comtypes import GUID

    from listener.platform.win32 import jumplist

    task = TaskbarTask("Fila de processamento", r"C:\Programas\listener meet\listener-meet.exe", ("--open", "queue"))
    link = jumplist.shell_link(task)
    assert link.GetPath(0x4) == task.program                               # SLGP_RAWPATH: o caminho como foi dado
    assert link.GetArguments() == "--open queue" and link.GetDescription() == task.title
    assert link.GetIconLocation() == (task.program, 0)
    store = link.QueryInterface(jumplist.IPropertyStore)
    key = jumplist.PropertyKey(GUID(jumplist.PKEY_TITLE[0]), jumplist.PKEY_TITLE[1])
    value = store.GetValue(ctypes.byref(key))
    try:
        assert value.vt == jumplist.VT_LPWSTR and ctypes.wstring_at(value.value.pwszVal) == task.title
    finally:
        if sys.platform == "win32":                                    # o mypy das outras plataformas não tem o `windll`
            ctypes.windll.ole32.PropVariantClear(ctypes.byref(value))
    assert ctypes.sizeof(jumplist.PropVariant) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)


@windows
def test_the_real_list_is_built_whole_and_abandoned(caplog):
    from listener.platform.win32 import jumplist

    with caplog.at_level(logging.INFO, logger="listener.platform.win32.jumplist"):
        assert jumplist.publish("listener-meet.teste", taskbar.taskbar_tasks(), commit=False) is True
    assert "Jump List montado e abandonado: 4 tarefa(s)" in caplog.text


def test_the_log_of_the_jump_list_carries_no_path_nor_name():
    from tests.log_privacy import leaks

    for module in ("listener/platform/win32/jumplist.py", "listener/ui/taskbar.py"):
        assert leaks(ROOT / module) == [], module


# ---------------------------------------------------------------- L3: a dica de fixar (U-132) e os atalhos no menu (U-133)
PIN = ("Dica: para abrir o listener-meet com um clique, clique com o botão direito no ícone dele na barra de tarefas e "
       "escolha Fixar na barra de tarefas.")


def test_the_pin_hint_is_the_spec_line_on_windows_and_none_elsewhere(monkeypatch, tmp_path):
    from listener.platform.win32.taskbar import Win32Taskbar

    assert Win32Taskbar().pin_hint() == PIN
    assert Platform.pin_hint(None) is None                                        # type: ignore[arg-type]
    platform = FakePlatform(tmp_path)
    platform.pin_text = PIN
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert taskbar.pin_hint(platform) is None                                    # no venv, fixaria o `python.exe`
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert taskbar.pin_hint(platform) == PIN
    platform.pin_text = None
    assert taskbar.pin_hint(platform) is None


@pytest.mark.parametrize("packaged", [True, False])
def test_the_last_page_of_the_wizard_teaches_pinning_in_one_line(qtbot, tmp_path, monkeypatch, packaged):
    from PySide6.QtWidgets import QLabel

    from listener.ui.wizard import FirstRunWizard

    platform = FakePlatform(tmp_path)
    platform.pin_text = PIN
    if packaged:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)
    wiz = FirstRunWizard(platform, tmp_path / "db")
    qtbot.addWidget(wiz)
    assert wiz.page(wiz.pageIds()[-1]) is wiz.mic_page                             # a última página
    hints = [label for label in wiz.mic_page.findChildren(QLabel) if label.text() == PIN]
    assert len(hints) == (1 if packaged else 0)
    assert all(label.wordWrap() for label in hints)


def test_menu_shortcuts_are_the_registered_ones_by_name():
    from listener.ui.tray import menu_shortcuts

    settings = {"hotkeys.start": "ctrl+alt+shift+r", "hotkeys.mute": "ctrl+alt+shift+u", "hotkeys.mark": ""}
    read = settings.get
    assert menu_shortcuts(read, {}) == {"start": "ctrl+alt+shift+r", "mute": "ctrl+alt+shift+u"}
    assert menu_shortcuts(read, {"ctrl+alt+shift+u": "já usado por outro app"}) == {"start": "ctrl+alt+shift+r"}
    assert menu_shortcuts(read, RuntimeError("o registro falhou")) == {}


def test_the_tray_menu_shows_the_shortcuts_and_follows_the_settings(qtbot, qapp, platform, store, monkeypatch):
    from listener.ui import controller as ctl
    from listener.ui.tray import TrayIcon

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    tray = TrayIcon(c)
    c.tray = tray
    try:
        c._on_hotkeys_registered(c.register_hotkeys())
        assert [getattr(tray, item).text() for item in ("act_record", "act_pause", "act_mark", "act_mute", "act_stop",
                                                        "act_history")] == [
            "Gravar…\tCtrl+Alt+Shift+R", "Pausar / retomar\tCtrl+Alt+Shift+P", "Marcador\tCtrl+Alt+Shift+M",
            "Mudo\tCtrl+Alt+Shift+U", "Parar\tCtrl+Alt+Shift+S", "Histórico…\tCtrl+Alt+Shift+H"]
        assert tray.act_queue.text() == "Fila de processamento…" and tray.act_live.text() == "Ao vivo…"   # sem atalho
        store.set_setting("hotkeys.history", "ctrl+alt+shift+j")                 # trocado nas Configurações
        store.set_setting("hotkeys.mark", "")                                     # e um tirado
        c._on_hotkeys_registered(c.register_hotkeys())
        assert tray.act_history.text() == "Histórico…\tCtrl+Alt+Shift+J" and tray.act_mark.text() == "Marcador"
        c._on_hotkeys_registered({"ctrl+alt+shift+u": "já usado por outro app"})  # o que não registrou não aparece
        assert tray.act_mute.text() == "Mudo" and tray.act_record.text() == "Gravar…\tCtrl+Alt+Shift+R"
        c._on_hotkeys_registered(RuntimeError("o registro falhou"))
        assert not [a for a in tray.menu.actions() if "\t" in a.text()]
    finally:
        c.tray = None
        tray.deleteLater()
        c.emergency_stop()
