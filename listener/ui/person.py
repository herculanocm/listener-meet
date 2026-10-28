"""Ficha da pessoa (U-041, F5.2): nome (obrigatório), cargo, empresa e e-mail (opcionais), ao nomear uma voz ou ao editar
quem já tem nome. Enter salva e Esc cancela; a edição tem "Apagar pessoa…", que a revisão confirma antes (CP3). O e-mail
fica só no banco local: nunca vai para a LLM nem para a busca (D-057). F5.4 L4 (U-087): "Apagar pessoa…" em vermelho à
esquerda, longe de Salvar (o primário), que fica à direita com o Cancelar. `person_data` e `person_labels` vieram do
`ui/review.py`."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, override

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .theme import DANGER, Tone, primary, set_kind, tone

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_LEN = {"name": 120, "role": 120, "company": 120, "email": 254}
EMAIL_NOTE = "O e-mail fica só neste computador: não vai para a LLM nem para a busca."
DELETE = 2                                         # código de `exec()` quando o usuário pede "Apagar pessoa…"
DELETE_GAP = 48                                    # U-087: px lógicos, no mínimo, entre o apagar e o Salvar


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


def person_data(row: Any) -> PersonData:
    return PersonData(str(row["name"]), str(row.get("role") or ""), str(row.get("company") or ""), str(row.get("email") or ""))


def person_labels(people: list[Any]) -> list[str]:
    """"Nome — Cargo · Empresa" de cada pessoa, na ordem; homônimos iguais até no cargo ganham o id (A-11: a escolha vai
    pelo id, nunca pelo nome)."""
    labels = [person_data(p).label() for p in people]
    return [f"{lab} (#{p['id']})" if labels.count(lab) > 1 else lab for lab, p in zip(labels, people, strict=True)]


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
        self.setMinimumWidth(480)
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
        self.btn_save = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.btn_save.setText("Salvar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()                              # U-087: o apagar à esquerda, longe do Salvar
        self.btn_delete: QPushButton | None = None
        if can_delete:
            self.btn_delete = QPushButton("Apagar pessoa…")
            self.btn_delete.setToolTip("Apaga a ficha, o perfil de voz e o vínculo com as falas (pede confirmação)")
            self.btn_delete.clicked.connect(self._delete_clicked)
            row.addWidget(self.btn_delete)
            row.addSpacing(DELETE_GAP)
        row.addStretch(1)
        row.addWidget(buttons)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(self.error)
        lay.addLayout(row)
        primary(self, self.btn_save)                     # Enter num campo salva; num botão com foco, aciona o botão (R-02)
        if self.btn_delete is not None:
            set_kind(self.btn_delete, DANGER)
        self.name_edit.setFocus()

    def _delete_clicked(self) -> None:
        self.done(DELETE)

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
