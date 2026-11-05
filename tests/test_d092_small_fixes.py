"""D-092, as correções pequenas do backlog (04/11): o Enter na pergunta da pasta sincronizada do assistente e na de
apagar preset fica com o "Não", como no descarte (D-076); a pasta do banco e o caminho do log saem da linha de abertura e
dos avisos do log do app, do worker e do ao vivo (D-053)."""
from __future__ import annotations

import ast
from typing import Any, ClassVar

import pytest

from tests.log_privacy import ROOT, leaks

OPENING_MODULES = tuple(ROOT / p for p in ("listener/ui/app.py", "listener/pipeline/worker.py", "listener/live/proc.py"))


def _box(answer_name: str) -> Any:
    """Um `QMessageBox` de mentira para o módulo testado: guarda título, botões e padrão de cada pergunta e responde."""
    from PySide6.QtWidgets import QMessageBox

    class Box:
        StandardButton = QMessageBox.StandardButton
        calls: ClassVar[list[tuple[str, Any, Any]]] = []

        @classmethod
        def question(cls, parent: Any, title: str, text: str, buttons: Any = None, default: Any = None) -> Any:
            del parent, text
            cls.calls.append((title, buttons, default))
            return getattr(QMessageBox.StandardButton, answer_name)

        @classmethod
        def warning(cls, *args: Any) -> None:
            raise AssertionError(f"aviso inesperado: {args[1:]}")

    return Box


@pytest.mark.parametrize(("answer", "accepted"), [("No", False), ("Yes", True)])
def test_the_synced_folder_question_keeps_no_on_enter(qtbot, platform, tmp_path, monkeypatch, answer, accepted):
    from PySide6.QtWidgets import QMessageBox

    from listener.ui import wizard

    box = _box(answer)
    monkeypatch.setattr(wizard, "QMessageBox", box)
    page = wizard.DbPage(platform, None)
    qtbot.addWidget(page)
    page.path.setText(str(tmp_path / "OneDrive - Empresa" / "listener"))
    assert page.validatePage() is accepted
    yes_no = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    assert box.calls == [("Pasta sincronizada", yes_no, QMessageBox.StandardButton.No)]


@pytest.mark.parametrize(("answer", "deleted"), [("No", False), ("Yes", True)])
def test_deleting_a_preset_asks_with_no_on_enter(qtbot, store, platform, monkeypatch, answer, deleted):
    from PySide6.QtWidgets import QMessageBox

    from listener.ui import settings_general
    from listener.ui.settings_min import SettingsDialog

    store.seed_default_presets()
    before = [int(p["id"]) for p in store.list_presets()]
    box = _box(answer)
    monkeypatch.setattr(settings_general, "QMessageBox", box)
    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    dlg.presets.setCurrentRow(0)
    dlg._delete_preset()
    after = [int(p["id"]) for p in store.list_presets()]
    assert (after == before[1:]) is deleted and (after == before) is (not deleted)
    assert dlg.presets.count() == len(after)
    yes_no = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    assert box.calls == [("Apagar preset", yes_no, QMessageBox.StandardButton.No)]


def test_the_opening_lines_never_carry_the_database_folder():
    """A linha de abertura do log do app, do worker e do ao vivo não leva a pasta do banco (a pasta do usuário, D-053)."""
    for module in OPENING_MODULES:
        calls = [n for n in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "log_versions"]
        assert calls, module
        for call in calls:
            keys = {k.value for d in ast.walk(call) if isinstance(d, ast.Dict) for k in d.keys if isinstance(k, ast.Constant)}
            attrs = {a.attr for a in ast.walk(call) if isinstance(a, ast.Attribute)}
            assert "banco" not in keys and "base_dir" not in attrs, module


def test_the_log_file_failure_shows_the_path_to_the_user_but_not_in_the_log(monkeypatch):
    from listener.ui import app

    assert leaks(ROOT / "listener/ui/app.py") == []
    user_path = "C:/Users/fulano/AppData/Local/listener-meet/listener.log"
    seen: list[tuple[Any, ...]] = []

    class Recorder:
        def warning(self, *args: Any) -> None:
            seen.append(args)

    def refuse(path: str, level: str | None) -> None:
        raise PermissionError(13, "Acesso negado", path)

    class Store:
        def get_setting(self, key: str) -> str | None:
            return user_path if key == "log.path" else None

        def default_log_path(self) -> str:
            return user_path

    monkeypatch.setattr(app, "log", Recorder())
    monkeypatch.setattr(app, "attach_file", refuse)
    shown = app.attach_log(Store())                       # type: ignore[arg-type]
    assert shown is not None and user_path in shown          # a tela do usuário mostra o caminho
    assert seen and all(user_path not in str(a) and "fulano" not in str(a) for args in seen for a in args)
    assert seen[0][1:] == ("PermissionError", 13)
