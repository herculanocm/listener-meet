"""F5.6 L1b: o popup do pedido de ata, resumo e ações (U-090, A-02, A-16, A-17) e as caixas "Gerar ata e ações" e "Gerar
resumo" (U-089, A-19, A-49), no sistema de design da F5.4. Só a vista: a pergunta vem pronta de `ui/llm_ask.py` (`Question`)
e o diálogo devolve a escolha; quem pede é `llm_ask.ask`.

- `RequestDialog`: o título ("Gerar ata e ações", "Gerar resumo" ou os dois), as frases (vozes sem nome, a reunião já revisada,
  a recomendação, a ata junto, o custo) e os botões. Com voz sem nome (ou a identificação por rodar): "Gerar quando todos
  tiverem nome" (o primário: padrão, foco e Enter), "Gerar agora mesmo assim" e "Cancelar"; com todas as vozes com nome:
  "Gerar" (o primário) e "Cancelar". Esc e o X cancelam, sem pedido (`mode` None).
- `choose(pai, pergunta)`: abre o popup e devolve o `Mode` (None = cancelou); os testes e as telas trocam pelo atributo
  `choose` de quem pergunta.
- `RequestBoxes`: as duas caixas, com a última escolha (`llm.ask_last`; desmarcadas na primeira vez); sem chave, apagadas e
  com o aviso de configurar a chave (Comportamento 1). `remember()` grava a escolha (só no OK de quem as mostra).
Sinais ligados a métodos (R-06)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..pipeline.llm_requests import Mode, Species
from .icons import set_icon
from .llm_ask import NO_KEY_TIP, Question, has_key, last_choice, save_choice
from .theme import PRIMARY, Tone, note, role, set_kind

if TYPE_CHECKING:
    from ..store import Store

HEADER_ICON_PX = 32
MIN_WIDTH = 560                     # os três botões pedem 534 px no Windows a 100 % de texto; a folga cobre o arredondamento
NAMES, NOW, GO, CANCEL = "Gerar quando todos tiverem nome", "Gerar agora mesmo assim", "Gerar", "Cancelar"
BOX_TEXT = {Species.MINUTES: "Gerar ata e ações", Species.SUMMARY: "Gerar resumo"}
BOX_TIP = {Species.MINUTES: "Pede à LLM a ata e as ações desta reunião (pergunta antes, com o custo).",
           Species.SUMMARY: "Pede à LLM o resumo executivo e o pré-resumo, que saem da ata (pergunta antes, com o custo)."}

type Chooser = Callable[[QWidget | None, Question], Mode | None]


class RequestDialog(QDialog):
    """U-090: a pergunta antes de todo pedido de ata, resumo ou ações; `mode` é a escolha (None = cancelou)."""

    def __init__(self, question: Question, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.question = question
        self.mode: Mode | None = None
        self.setWindowTitle(question.title)
        self.setMinimumWidth(MIN_WIDTH)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addLayout(self._header(question.title))
        self.lines: list[QLabel] = []
        for i, text in enumerate(question.lines):
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            if i == 0:
                role(label, "body_strong")               # as vozes: o fato que decide a escolha
            self.lines.append(label)
            lay.addWidget(label)
        lay.addLayout(self._buttons(question.named))
        # [fato, 29/10] o mínimo explícito desliga o do layout: com texto maior que o padrão, os botões não caberiam em 560
        self.setMinimumWidth(max(MIN_WIDTH, lay.minimumSize().width()))

    @staticmethod
    def _header(title: str) -> QHBoxLayout:
        icon = QLabel()
        icon.setFixedSize(HEADER_ICON_PX, HEADER_ICON_PX)
        set_icon(icon, "document", Tone.INFO, HEADER_ICON_PX)
        head = QLabel(title)
        role(head, "subtitle")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(icon)
        row.addWidget(head, 1)
        return row

    def _buttons(self, named: bool) -> QHBoxLayout:
        """Com todas as vozes com nome, "Gerar" e "Cancelar"; senão os três, com o "quando todos tiverem nome" de padrão."""
        self.btn_cancel = QPushButton(CANCEL, self)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_now: QPushButton | None = None
        if named:
            self.btn_primary = QPushButton(GO, self)
            self.btn_primary.clicked.connect(self._now)
        else:
            self.btn_primary = QPushButton(NAMES, self)
            self.btn_primary.clicked.connect(self._names)
            self.btn_now = QPushButton(NOW, self)
            self.btn_now.clicked.connect(self._now)
        set_kind(self.btn_primary, PRIMARY)             # o padrão da janela: Enter
        row = QHBoxLayout()
        row.addStretch(1)
        for b in (self.btn_primary, self.btn_now, self.btn_cancel):
            if b is not None:
                row.addWidget(b)
        self.btn_primary.setFocus()
        return row

    def _names(self) -> None:
        self.mode = Mode.NAMES
        self.accept()

    def _now(self) -> None:
        self.mode = Mode.NOW
        self.accept()


def choose(parent: QWidget | None, question: Question) -> Mode | None:
    """O popup modal; devolve o modo escolhido (None = Cancelar, Esc ou o X)."""
    dlg = RequestDialog(question, parent)
    try:
        dlg.exec()
        return dlg.mode
    finally:
        dlg.deleteLater()


class RequestBoxes(QWidget):
    """U-089 e A-49: "Gerar ata e ações" e "Gerar resumo", com a última escolha; sem chave, apagadas e com o aviso."""

    def __init__(self, store: Store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.key = has_key(store)
        last = last_choice(store) if self.key else frozenset()
        self.boxes: dict[Species, QCheckBox] = {}
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        for sp, text in BOX_TEXT.items():
            box = QCheckBox(text)
            box.setChecked(sp in last)
            box.setEnabled(self.key)
            box.setToolTip(BOX_TIP[sp] if self.key else NO_KEY_TIP)
            self.boxes[sp] = box
            row.addWidget(box)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(row)
        self.note: QLabel | None = None
        if not self.key:                                 # Comportamento 1: o porquê das caixas apagadas
            self.note = QLabel(NO_KEY_TIP)
            self.note.setWordWrap(True)
            note(self.note)
            lay.addWidget(self.note)

    def chosen(self) -> frozenset[Species]:
        """As espécies marcadas (nenhuma sem chave)."""
        if not self.key:
            return frozenset()
        return frozenset(sp for sp, box in self.boxes.items() if box.isChecked())

    def remember(self) -> None:
        """A última escolha, para a próxima vez (sem chave, a de antes fica)."""
        if self.key:
            save_choice(self.store, self.chosen())


__all__ = ["BOX_TEXT", "CANCEL", "GO", "NAMES", "NOW", "Chooser", "RequestBoxes", "RequestDialog", "choose"]
