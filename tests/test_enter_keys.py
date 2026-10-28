"""Revisão da F5.4: o Enter como no Windows. Num botão com foco, aciona esse botão (Cancelar cancela); num campo, o primário
da janela (R-02, desvio do A-02, que tirava o `autoDefault` dos outros botões). Na revisão de falantes, o Enter na lista
segue confirmando o pyannote (U-006) e num campo de texto não aciona nada (R-01). Na fila, o Enter na tabela não aciona
botão nenhum (antes clicava "Pausar fila")."""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def platform(tmp_path, clock):
    """Tons a 16 kHz mono nos dois canais, como em `tests/test_review.py` (a gravação falsa custa menos)."""
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _enter(qtbot: Any, widget: Any) -> None:
    """O usuário chega a `widget` pelo Tab e aperta Enter."""
    from PySide6.QtCore import Qt

    widget.setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.keyClick(widget, Qt.Key.Key_Return)


def _count_clicks(buttons: Any) -> list[int]:
    """Cada clique de `buttons` vira um item da lista (a função só prende a lista, nenhum widget)."""
    clicked: list[int] = []

    def hit(*_args: object) -> None:
        clicked.append(1)

    for b in buttons:
        b.clicked.connect(hit)
    return clicked


def _calls(monkeypatch: Any, cls: Any, *names: str) -> list[str]:
    """Troca os métodos `names` da classe (antes de montar a janela: os sinais ligam o método da instância) por um registro."""
    seen: list[str] = []
    for name in names:
        monkeypatch.setattr(cls, name, lambda self, *a, _n=name, **k: seen.append(_n))
    return seen


# ---------------------------------------------------------------- R-01: a revisão de falantes
def test_review_enter_on_a_focused_button_runs_that_button(qtbot, platform, store, monkeypatch):
    from listener.ui.review import ReviewDialog
    from tests.test_review import _processed

    mid, _items = _processed(platform, store)
    seen = _calls(monkeypatch, ReviewDialog, "confirm_current", "finish", "ask_other_person", "_open_transcript", "regenerate")
    dlg = ReviewDialog(store, mid)
    qtbot.addWidget(dlg)
    dlg.show()
    for button, action in ((dlg.btn_finish, "finish"), (dlg.btn_other, "ask_other_person"),
                           (dlg.btn_transcript, "_open_transcript"), (dlg.btn_identify, "regenerate")):
        if not button.isEnabled():
            button.setEnabled(True)
        seen.clear()
        _enter(qtbot, button)
        assert seen == [action], button.text()
    seen.clear()
    _enter(qtbot, dlg.list)                                                          # U-006: na lista, confirma o pyannote
    assert seen == ["confirm_current"]


def test_review_enter_in_a_text_field_does_not_press_any_button(qtbot, platform, store, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLineEdit, QPushButton

    from listener.ui.review import ReviewDialog
    from tests.test_review import _processed

    mid, _items = _processed(platform, store)
    seen = _calls(monkeypatch, ReviewDialog, "confirm_current", "finish")
    dlg = ReviewDialog(store, mid)
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.tabs.setCurrentWidget(dlg.title_tab)
    field = next(w for w in dlg.title_tab.findChildren(QLineEdit) if w.isVisible())
    clicked = _count_clicks(dlg.findChildren(QPushButton))
    save = next(b for b in dlg.title_tab.findChildren(QPushButton) if b.isVisible() and b.isEnabled())
    save.setFocus()                                  # um botão da aba que já teve o foco vira o padrão no Qt
    field.setFocus()
    qtbot.keyClick(field, Qt.Key.Key_Return)
    assert clicked == [] and seen == [] and dlg.isVisible()


# ---------------------------------------------------------------- R-02: o primário não toma o Enter dos outros botões
def test_warmup_enter_on_cancel_cancels_and_in_a_field_records(qtbot, platform, store, monkeypatch):
    from PySide6.QtWidgets import QDialog, QDialogButtonBox

    from listener.ui.warmup import WarmupDialog

    store.seed_default_presets()
    seen = _calls(monkeypatch, WarmupDialog, "_test_mic", "_save_preset")
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    dlg.show()
    for button, action in ((dlg.test_btn, "_test_mic"), (dlg.save_preset_btn, "_save_preset")):
        _enter(qtbot, button)
        assert seen == [action] and dlg.isVisible()
        seen.clear()
    cancel = next(b for b in dlg.buttons.buttons() if dlg.buttons.buttonRole(b) == QDialogButtonBox.ButtonRole.RejectRole)
    _enter(qtbot, cancel)
    assert dlg.result() == QDialog.DialogCode.Rejected and not dlg.isVisible()
    again = WarmupDialog(platform, store)
    qtbot.addWidget(again)
    again.show()
    _enter(qtbot, again.title_edit)                                                  # num campo, o primário (Gravar)
    assert again.result() == QDialog.DialogCode.Accepted


def test_settings_enter_on_cancel_or_a_page_button_does_not_save(qtbot, platform, store, monkeypatch):
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPushButton

    from listener.ui import settings_general
    from listener.ui.settings_editors import ChoiceEditor
    from listener.ui.settings_min import SettingsDialog

    store.seed_default_presets()
    seen = _calls(monkeypatch, settings_general.GeneralPagesMixin, "_delete_preset")
    dlg = SettingsDialog(store, platform)
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.nav.setCurrentRow(list(dlg.pages).index("Captura"))
    delete = next(b for b in dlg.findChildren(QPushButton) if b.text() == "Apagar preset selecionado")
    _enter(qtbot, delete)
    assert seen == ["_delete_preset"] and dlg.isVisible()
    box = dlg.findChildren(QDialogButtonBox)[0]
    store.set_setting("log.level", "WARNING")
    level = dlg.editor("log.level")
    assert isinstance(level, ChoiceEditor)
    level.set_raw("WARNING")
    level.combo.setCurrentIndex(level.combo.findData("DEBUG"))                       # mexeu e cancela: nada salvo
    _enter(qtbot, box.button(QDialogButtonBox.StandardButton.Cancel))
    assert dlg.result() == QDialog.DialogCode.Rejected and store.get_setting("log.level") == "WARNING"     # nada salvo
    again = SettingsDialog(store, platform)
    qtbot.addWidget(again)
    again.show()
    again.nav.setCurrentRow(list(again.pages).index("Atalhos"))
    _enter(qtbot, again.editor("hotkeys.mark").widget)                               # num campo, o primário (Salvar)
    assert again.result() == QDialog.DialogCode.Accepted


def test_person_enter_on_cancel_cancels_and_in_the_name_saves(qtbot):
    from PySide6.QtWidgets import QDialog, QDialogButtonBox

    from listener.ui.person import PersonData, PersonDialog

    dlg = PersonDialog(data=PersonData("Ana", "CFO"), can_delete=True)
    qtbot.addWidget(dlg)
    dlg.show()
    box = dlg.findChildren(QDialogButtonBox)[0]
    _enter(qtbot, box.button(QDialogButtonBox.StandardButton.Cancel))
    assert dlg.result() == QDialog.DialogCode.Rejected
    again = PersonDialog(data=PersonData("Ana", "CFO"))
    qtbot.addWidget(again)
    again.show()
    _enter(qtbot, again.name_edit)
    assert again.result() == QDialog.DialogCode.Accepted


# ---------------------------------------------------------------- a fila: o Enter na tabela não clica nada
def test_queue_enter_in_the_table_presses_nothing_and_on_a_button_presses_it(qtbot, store):
    from PySide6.QtWidgets import QPushButton

    from listener.config.defaults import SPECS
    from listener.ui.queue import QueueDialog

    mid = store.create_meeting("Comitê", None, None, None)
    store.jobs.enqueue(mid)
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    clicked = _count_clicks(dlg.findChildren(QPushButton))
    dlg.show()
    dlg.table.selectRow(0)
    _enter(qtbot, dlg.table)
    assert clicked == [] and dlg.isVisible() and not store.get(SPECS["pipeline.paused_manual"])
    _enter(qtbot, dlg.btn_close)
    assert len(clicked) == 1 and not dlg.isVisible()
