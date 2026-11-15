"""A escolha da pessoa na revisão de vozes (F5.25, U-171 a U-176): "Outra pessoa…" (O) e "Escolher pessoa…" (o Enter na voz
sem candidato) abrem esta janela no lugar da caixa de escolha de antes.

A parte pura, sem Qt: `fold` (sem acento e sem maiúscula), `top_by_voice` (as até 3 de "Pela voz", U-136), `PersonIndex.view`
(o filtro, a ordem e as seções); a nota da voz em percentual (U-173) é o `fmt.percent`. A busca olha o nome, o cargo e a empresa,
nunca o e-mail; cada palavra digitada aparece em qualquer posição de um deles. A escolha vai sempre pelo id da linha
(homônimos, A-11).

A janela `PersonPicker`: o campo com o foco, a lista (pessoa, "já na Voz N" e a nota) com os títulos de seção não
selecionáveis e o rodapé. As setas e o Page Up/Down chegam à lista pelo filtro de tecla do campo (sem `QShortcut`, como o
A-38); o Enter só escolhe uma linha selecionada (U-176) e, sem resultado nem nome parecido, abre "Nova pessoa" (U-172).
Todo widget nasce com o pai, sem folha de estilo. `run` abre a janela: é o ponto único que os testes trocam."""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import override

from PySide6.QtCore import QEvent, QModelIndex, QObject, Qt
from PySide6.QtGui import QBrush, QKeyEvent
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..fmt import percent, plural
from ..pipeline.review_suggest import FLOOR
from .person import MAX_LEN
from .theme import PRIMARY, font_for, note, qcolor, set_kind, surface_tokens

TOP_BY_VOICE = 3                 # U-136: "Pela voz" traz até 3, com nota de `FLOOR` (0,45, D-089) ou mais
SIMILAR_MIN_LETTERS = 3          # U-175: "Nomes parecidos" só com 3 letras ou mais
SIMILAR_CUTOFF = 0.75            # U-175: a semelhança mínima do `difflib`
SIMILAR_MAX = 3
BY_VOICE_TITLE, EXPECTED_TITLE, ALL_TITLE, SIMILAR_TITLE = "Pela voz", "Esperadas nesta reunião", "Todas", "Nomes parecidos"
_WORD = re.compile(r"\w+")


def fold(text: str) -> str:
    """O texto para comparar: `casefold` e NFKD sem os sinais combinantes ("Conceição" vira "conceicao")."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


@dataclass(frozen=True, slots=True)
class Candidate:
    """Uma pessoa na janela. `label` é a linha de `person_labels` ("Nome — Cargo · Empresa", com "(#id)" nos homônimos);
    `score`, a nota pela voz (None sem perfil no modelo da voz); `taken`, a voz onde ela já foi decidida ("Voz 2")."""
    person_id: int
    label: str
    name: str
    role: str = ""
    company: str = ""
    score: float | None = None
    expected: bool = False
    taken: str = ""


@dataclass(frozen=True, slots=True)
class Section:
    title: str                           # vazio: sem título (a lista com texto)
    rows: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class View:
    """O que a lista mostra para um texto: as seções, quantas pessoas casaram (com o campo vazio, todas), se a primeira
    linha vem selecionada (U-176) e se as linhas são "Nomes parecidos" (U-175)."""
    sections: tuple[Section, ...]
    matched: int
    select_first: bool
    similar: bool = False

    @property
    def rows(self) -> list[Candidate]:
        return [c for s in self.sections for c in s.rows]


def top_by_voice(ranked: Sequence[tuple[int, float]], suggested: int | None, known: set[int] | frozenset[int]) -> list[int]:
    """U-136: até 3 pessoas com nota de 0,45 ou mais (D-089), na ordem da nota, sem a já sugerida e só entre as `known`."""
    near = [pid for pid, s in ranked if s >= FLOOR and pid != suggested and pid in known]
    return near[:TOP_BY_VOICE]


@dataclass(frozen=True, slots=True)
class _Entry:
    order: int                           # a posição de `list_people`: você primeiro, depois a ordem alfabética
    person: Candidate
    fields: tuple[str, ...]              # nome, cargo e empresa, normalizados
    name_words: tuple[str, ...]
    full_name: str
    first_name: str


class PersonIndex:
    """As pessoas normalizadas uma vez, na abertura; `view` roda em memória a cada tecla (nenhuma consulta ao banco)."""

    def __init__(self, people: Sequence[Candidate], top: Sequence[int]) -> None:
        self.entries: list[_Entry] = []
        for i, p in enumerate(people):
            name = fold(p.name)
            words = tuple(_WORD.findall(name))
            self.entries.append(_Entry(i, p, (name, fold(p.role), fold(p.company)), words, " ".join(name.split()),
                                       words[0] if words else ""))
        known = {p.person_id for p in people}
        self.top = [pid for pid in top if pid in known]
        self._rank = {pid: i for i, pid in enumerate(self.top)}

    def __len__(self) -> int:
        return len(self.entries)

    def view(self, text: str) -> View:
        words = fold(text).split()
        if not words:
            return self._sections()
        hits = [e for e in self.entries if all(any(w in f for f in e.fields) for w in words)]
        if hits:
            hits.sort(key=lambda e: self._key(e, words))
            return View((Section("", tuple(e.person for e in hits)),), len(hits), True)
        similar = self._similar(" ".join(words))
        if similar:
            return View((Section(SIMILAR_TITLE, tuple(similar)),), 0, True, similar=True)
        return View((), 0, False)

    def _sections(self) -> View:
        """U-174: "Pela voz", "Esperadas nesta reunião" e "Todas", cada pessoa uma vez; a primeira de "Pela voz" vem
        selecionada e, sem ela, nenhuma (U-176)."""
        by_id = {e.person.person_id: e.person for e in self.entries}
        rest = [e.person for e in self.entries if e.person.person_id not in self._rank]
        sections = (Section(BY_VOICE_TITLE, tuple(by_id[pid] for pid in self.top)),
                    Section(EXPECTED_TITLE, tuple(p for p in rest if p.expected)),
                    Section(ALL_TITLE, tuple(p for p in rest if not p.expected)))
        return View(tuple(s for s in sections if s.rows), len(self.entries), bool(self.top))

    def _key(self, e: _Entry, words: list[str]) -> tuple[int, int, int]:
        """Comportamento 4: quem estaria em "Pela voz", na ordem da nota; depois quem tem todas as palavras no começo de
        uma palavra do nome; por fim os demais. Nos dois últimos, as esperadas antes, e então a ordem de `list_people`."""
        rank = self._rank.get(e.person.person_id)
        if rank is not None:
            return (0, rank, 0)
        prefix = all(any(n.startswith(w) for n in e.name_words) for w in words)
        return (1 if prefix else 2, 0 if e.person.expected else 1, e.order)

    def _similar(self, typed: str) -> list[Candidate]:
        """U-175: com 3 letras ou mais, até 3 pessoas cujo nome inteiro ou primeiro nome se pareça com o texto (`difflib`,
        0,75 ou mais), da mais parecida para a menos."""
        if sum(c.isalnum() for c in typed) < SIMILAR_MIN_LETTERS:
            return []
        scored: list[tuple[float, int, Candidate]] = []
        for e in self.entries:
            best = max(difflib.SequenceMatcher(None, typed, target).ratio() for target in (e.full_name, e.first_name))
            if best >= SIMILAR_CUTOFF:
                scored.append((-best, e.order, e.person))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [p for _, _, p in scored[:SIMILAR_MAX]]


# ---------------------------------------------------------------- a janela
PLACEHOLDER = "Buscar por nome, cargo ou empresa"
TIP = "O quanto esta voz se parece com o perfil de voz de {name}. A partir de {limit}, o app costuma reconhecer sozinho."
PERSON_ROLE = Qt.ItemDataRole.UserRole           # o id da pessoa na linha; os títulos de seção não têm
NAME_COL, TAKEN_COL, SCORE_COL = 0, 1, 2
COL_PAD = 24                                      # px lógicos em volta do texto das colunas da direita
SHORT = 24                                        # o texto digitado no botão e no rodapé: maior é cortado com "…"
NAV_KEYS = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)
ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


@dataclass(frozen=True, slots=True)
class Pick:
    """O que a janela decidiu: a pessoa pelo id, a ficha nova com o nome digitado, ou nada (Esc, Cancelar, fechar)."""
    person_id: int | None = None
    new_name: str | None = None


def _short(text: str) -> str:
    return text if len(text) <= SHORT else text[:SHORT - 1] + "…"


class PersonPicker(QDialog):
    """"Outra pessoa" (U-171): `question` é "Quem é a Voz 3?"; `threshold`, o `pipeline.threshold` de agora (a dica)."""

    def __init__(self, parent: QWidget | None, index: PersonIndex, *, question: str, threshold: float) -> None:
        super().__init__(parent)
        self.setWindowTitle("Outra pessoa")
        self.setMinimumSize(560, 460)
        self.index, self.threshold = index, threshold
        self.picked = Pick()
        self.view = View((), 0, False)
        self.rows: list[QTreeWidgetItem] = []                  # as linhas de pessoa, na ordem da tela (as setas andam nela)
        self.question = QLabel(question, self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(PLACEHOLDER)
        self.search.setClearButtonEnabled(True)
        self.search.setMaxLength(MAX_LEN["name"])
        self.search.installEventFilter(self)
        self.tree = self._build_tree()
        self.count = QLabel(self)
        note(self.count)
        self.btn_new = QPushButton("Nova pessoa…", self)
        self.btn_choose = QPushButton("Escolher", self)
        self.btn_cancel = QPushButton("Cancelar", self)
        foot = QHBoxLayout()
        foot.addWidget(self.count, 1)
        for b in (self.btn_new, self.btn_choose, self.btn_cancel):
            foot.addWidget(b)
        lay = QVBoxLayout(self)
        for w in (self.question, self.search):
            lay.addWidget(w)
        lay.addWidget(self.tree, 1)
        lay.addLayout(foot)
        self.search.textChanged.connect(self.refilter)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemSelectionChanged.connect(self._sync)
        self.btn_choose.clicked.connect(self.choose)
        self.btn_new.clicked.connect(self.new_person)
        self.btn_cancel.clicked.connect(self.reject)
        self.refilter("")
        self.search.setFocus()

    def _build_tree(self) -> QTreeWidget:
        """Três colunas: a pessoa (estica), "já na Voz N" (some sem ninguém marcado) e a nota, à direita; sem foco (o
        teclado fica no campo) e sem cabeçalho."""
        tree = QTreeWidget(self)
        tree.setColumnCount(3)
        tree.setHeaderHidden(True)
        tree.setRootIsDecorated(False)
        tree.setItemsExpandable(False)
        tree.setUniformRowHeights(True)
        tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        fm, head = tree.fontMetrics(), tree.header()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(NAME_COL, QHeaderView.ResizeMode.Stretch)
        taken = [c.taken for e in self.index.entries if (c := e.person).taken]
        widths = {TAKEN_COL: max((fm.horizontalAdvance(f"já na {t}") for t in taken), default=0),
                  SCORE_COL: fm.horizontalAdvance("100 %")}
        for col, width in widths.items():
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            head.resizeSection(col, width + COL_PAD)
        tree.setColumnHidden(TAKEN_COL, not taken)
        return tree

    # ---------------------------------------------------------------- a lista
    def refilter(self, text: str) -> None:
        """Refaz a lista para o texto (em memória, `PersonIndex.view`) e a seleção: a primeira linha quando a vista manda
        (U-176); senão, nenhuma."""
        self.view = self.index.view(text)
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            self.rows = []
            for section in self.view.sections:
                parent = self._section_item(section.title) if section.title else None
                for c in section.rows:
                    self.rows.append(self._person_item(c, parent))
            self.tree.expandAll()
        finally:
            self.tree.setUpdatesEnabled(True)
        self._select(0 if self.view.select_first and self.rows else None)
        self._footer(text)

    def _section_item(self, title: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.tree, [title])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)                # não selecionável: as setas e o clique pulam o título
        item.setFirstColumnSpanned(True)
        item.setFont(NAME_COL, font_for("body_strong"))
        item.setForeground(NAME_COL, QBrush(qcolor(surface_tokens(self).secondary)))
        return item

    def _person_item(self, c: Candidate, parent: QTreeWidgetItem | None) -> QTreeWidgetItem:
        cols = [c.label, f"já na {c.taken}" if c.taken else "", percent(c.score)]
        item = QTreeWidgetItem(parent, cols) if parent is not None else QTreeWidgetItem(self.tree, cols)
        item.setData(NAME_COL, PERSON_ROLE, c.person_id)
        item.setTextAlignment(SCORE_COL, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if c.taken:
            item.setForeground(TAKEN_COL, QBrush(qcolor(surface_tokens(self).secondary)))
        if cols[SCORE_COL]:
            tip = TIP.format(name=c.name, limit=percent(self.threshold))
            for col in (NAME_COL, SCORE_COL):
                item.setToolTip(col, tip)
        return item

    def _footer(self, text: str) -> None:
        """O rodapé ("63 pessoas", "3 de 63 pessoas" ou "Ninguém com …") e os botões: "Nova pessoa" leva o texto e vira o
        primário quando não há linha nenhuma (U-172)."""
        total, typed = plural(len(self.index), "pessoa", "pessoas"), text.strip()
        if not typed:
            self.count.setText(total)
        elif self.view.matched:
            self.count.setText(f"{self.view.matched} de {total}")
        else:
            self.count.setText(f'Ninguém com "{_short(typed)}".')
        self.btn_new.setText(f'Nova pessoa "{_short(typed)}"…' if typed else "Nova pessoa…")
        new_first = self._offers_new()
        set_kind(self.btn_new, PRIMARY if new_first else None)
        set_kind(self.btn_choose, None if new_first else PRIMARY)
        self._sync()

    def _offers_new(self) -> bool:
        return not self.rows and bool(self.search.text().strip())

    def _sync(self) -> None:
        self.btn_choose.setEnabled(self.selected_id() is not None)

    def _select(self, pos: int | None) -> None:
        if pos is None:
            self.tree.clearSelection()
            self.tree.setCurrentIndex(QModelIndex())
            return
        item = self.rows[pos]
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)

    def selected_id(self) -> int | None:
        chosen = [it for it in self.tree.selectedItems() if it.data(NAME_COL, PERSON_ROLE) is not None]
        return int(chosen[0].data(NAME_COL, PERSON_ROLE)) if chosen else None

    def step(self, key: int) -> None:
        """↑ e ↓ andam uma linha; Page Up e Page Down, uma página. Sem linha selecionada, a primeira."""
        if not self.rows:
            return
        sel = [it for it in self.tree.selectedItems() if it in self.rows]
        if not sel:
            self._select(0)
            return
        page = max(1, self.tree.viewport().height() // max(1, self.tree.visualItemRect(sel[0]).height()) - 1)
        moves: dict[int, int] = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1, Qt.Key.Key_PageUp: -page, Qt.Key.Key_PageDown: page}
        self._select(min(len(self.rows) - 1, max(0, self.rows.index(sel[0]) + moves[key])))

    # ---------------------------------------------------------------- as saídas
    def press_enter(self) -> None:
        """U-176: o Enter escolhe a linha selecionada; sem resultado nem nome parecido, "Nova pessoa"; senão, nada."""
        if self.selected_id() is not None:
            self.choose()
        elif self._offers_new():
            self.new_person()

    def choose(self) -> None:
        pid = self.selected_id()
        if pid is not None:
            self.picked = Pick(person_id=pid)
            self.accept()

    def new_person(self) -> None:
        """U-172: a ficha nova com o nome digitado (sem espaços nas pontas, até 120 caracteres)."""
        self.picked = Pick(new_name=self.search.text().strip()[:MAX_LEN["name"]])
        self.accept()

    def _double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.data(NAME_COL, PERSON_ROLE) is not None:
            self.tree.setCurrentItem(item)
            self.choose()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """As teclas do campo: setas e páginas movem a lista; Enter escolhe (U-176); Esc fecha, com ou sem texto."""
        if watched is self.__dict__.get("search") and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key in NAV_KEYS:
                self.step(key)
                return True
            if key in ENTER_KEYS:
                self.press_enter()
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(watched, event)


def run(picker: PersonPicker) -> Pick:
    """Abre a janela (modal) e devolve a escolha; os testes trocam esta função para dirigir a janela sem `exec()`."""
    picker.exec()
    return picker.picked


__all__ = ["ALL_TITLE", "BY_VOICE_TITLE", "EXPECTED_TITLE", "PERSON_ROLE", "PLACEHOLDER", "SIMILAR_TITLE", "TIP", "TOP_BY_VOICE",
           "Candidate", "PersonIndex", "PersonPicker", "Pick", "Section", "View", "fold", "run", "top_by_voice"]
