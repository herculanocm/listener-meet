"""Ficha da pessoa (U-041, F5.2): nome (obrigatório), cargo, empresa e e-mail (opcionais), ao nomear uma voz ou ao editar
quem já tem nome. Enter salva e Esc cancela; a edição tem "Apagar pessoa…", que a revisão confirma antes (CP3). O e-mail
fica só no banco local: nunca vai para a LLM nem para a busca (D-057)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import override

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .theme import Tone, tone

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_LEN = {"name": 120, "role": 120, "company": 120, "email": 254}
EMAIL_NOTE = "O e-mail fica só neste computador: não vai para a LLM nem para a busca."
DELETE = 2                                         # código de `exec()` quando o usuário pede "Apagar pessoa…"


@dataclass(frozen=True, slots=True)
class PersonData:
    name: str
    role: str = ""
    company: str = ""
    email: str = ""

    def label(self) -> str:
        """"Ana — CFO · Empresa X" para as listas de escolha."""
        role = f" — {self.role}" if self.role else ""
        company = f" · {self.company}" if self.company else ""
        return f"{self.name}{role}{company}"


def validate(data: PersonData) -> str | None:
    """O motivo de a ficha não poder ser salva, ou None."""
    if not data.name:
        return "Informe o nome."
    if data.email and not EMAIL_RE.match(data.email):
        return "E-mail inválido (exemplo: nome@empresa.com.br)."
    return None


class PersonDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, data: PersonData | None = None, can_delete: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Editar pessoa" if data is not None else "Nova pessoa")
        self.setMinimumWidth(420)
        start = data or PersonData("")
        self.name_edit = self._field(start.name, "obrigatório", MAX_LEN["name"])
        self.role_edit = self._field(start.role, "opcional (ex.: Gerente de TI)", MAX_LEN["role"])
        self.company_edit = self._field(start.company, "opcional", MAX_LEN["company"])
        self.email_edit = self._field(start.email, "opcional", MAX_LEN["email"])
        form = QFormLayout()
        form.addRow("Nome", self.name_edit)
        form.addRow("Cargo", self.role_edit)
        form.addRow("Empresa", self.company_edit)
        form.addRow("E-mail", self.email_edit)
        note = QLabel(EMAIL_NOTE)
        note.setWordWrap(True)
        tone(note, Tone.SECONDARY)
        self.error = QLabel("")
        tone(self.error, Tone.DANGER)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Salvar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.btn_delete: QPushButton | None = None
        if can_delete:
            self.btn_delete = buttons.addButton("Apagar pessoa…", QDialogButtonBox.ButtonRole.DestructiveRole)
            self.btn_delete.setAutoDefault(False)
            self.btn_delete.clicked.connect(lambda: self.done(DELETE))
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(self.error)
        lay.addWidget(buttons)
        self.name_edit.setFocus()

    @staticmethod
    def _field(text: str, placeholder: str, max_len: int) -> QLineEdit:
        edit = QLineEdit(text)
        edit.setPlaceholderText(placeholder)
        edit.setMaxLength(max_len)
        return edit

    def data(self) -> PersonData:
        return PersonData(self.name_edit.text().strip(), self.role_edit.text().strip(), self.company_edit.text().strip(),
                          self.email_edit.text().strip())

    @override
    def accept(self) -> None:
        problem = validate(self.data())
        if problem:
            self.error.setText(problem)
            (self.name_edit if not self.data().name else self.email_edit).setFocus()
            return
        super().accept()
