"""U-041 (F5.2): a ficha da pessoa (nome obrigatório; cargo, empresa e e-mail opcionais, e-mail conferido) e o "Apagar
pessoa…" da edição, que devolve o código próprio para a revisão confirmar."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog

from listener.ui.person import DELETE, EMAIL_NOTE, PersonData, PersonDialog, validate


def test_validate_and_label():
    assert validate(PersonData("")) == "Informe o nome."
    assert validate(PersonData("Ana", email="ana@")) is not None and validate(PersonData("Ana", email="a b@x.com")) is not None
    assert validate(PersonData("Ana", "CFO", "Empresa X", "ana@empresa.com.br")) is None
    assert PersonData("Ana", "CFO", "Empresa X").label() == "Ana — CFO · Empresa X"
    assert PersonData("Ana", company="Empresa X").label() == "Ana · Empresa X" and PersonData("Ana").label() == "Ana"


def test_new_person_needs_a_name_and_a_valid_email(qtbot):
    dlg = PersonDialog()
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Nova pessoa" and dlg.btn_delete is None
    dlg.accept()                                                       # sem nome: fica aberta com o motivo
    assert dlg.result() != QDialog.DialogCode.Accepted and dlg.error.text() == "Informe o nome."
    dlg.name_edit.setText("  Bia  ")
    dlg.email_edit.setText("bia@empresa")
    dlg.accept()
    assert "E-mail inválido" in dlg.error.text() and dlg.result() != QDialog.DialogCode.Accepted
    dlg.email_edit.setText("bia@empresa.com")
    dlg.role_edit.setText(" Diretora ")
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert dlg.data() == PersonData("Bia", "Diretora", "", "bia@empresa.com")


def test_edit_starts_filled_and_offers_delete(qtbot):
    dlg = PersonDialog(data=PersonData("Ana", "CFO", "Empresa X", "ana@x.com"), can_delete=True)
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Editar pessoa" and dlg.data() == PersonData("Ana", "CFO", "Empresa X", "ana@x.com")
    assert dlg.btn_delete is not None and not dlg.btn_delete.autoDefault()          # Enter salva; apagar só no clique
    dlg.btn_delete.click()
    assert dlg.result() == DELETE
    assert any(w.text() == EMAIL_NOTE for w in dlg.findChildren(type(dlg.error)))
