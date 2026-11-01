"""Os botões da revisão de falantes pelo resultado (F5.11 L1, U-098 a U-101), fora do `ui/review.py` (A-35).

`action_labels(item, names)` é pura (A-19): o principal (Enter), a sugestão pelo contexto (L) e a linha do que a
confirmação faz (U-101), conforme o estado da voz selecionada, com o nome e sem gênero (P-01):

| Estado da voz | Principal (Enter) | L |
| --- | --- | --- |
| sugestão pela voz (a confirmar; sem nome "parecida com"; ignorada com candidato) | "É Ana" | "É Rodrigo, pelo contexto" |
| automática (acima do limiar, a LLM não sugere: CP11) | "Confirmar Ana" | apagado |
| sem candidato pela voz | "Escolher pessoa…" (a lista de "Outra pessoa") | "É Rodrigo, pelo contexto" |
| confirmada | "É Ana", apagado | apagado |
| mesclada | "É a mesma que a Voz 3", apagado | apagado |

Sem sugestão pelo contexto, o L fica apagado. As teclas não mudam; o Enter faz o que o principal diz e, apagado, nada.
`ReviewActionsMixin` monta a coluna de largura fixa (o nome comprido é cortado no meio), o "Mais" (U-100, A-21: a tecla
depois do Tab no texto do item; quem a atende é o `handle_key`, sem `QAction.setShortcut`) e a linha do U-101, e faz o
que cada ação da voz faz."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QLabel, QMenu, QMessageBox, QVBoxLayout, QWidget

from ..model import BY_CONTEXT, ClusterStatus, JobStep
from ..pipeline import review
from .meeting_parts import menu_button
from .review_cards import KeyButton
from .review_person import PersonActionsMixin
from .theme import note

if TYPE_CHECKING:
    from ..pipeline.voices import VoiceNames
    from .player import Player

CHOOSE_TEXT = "Escolher pessoa…"
NO_CONTEXT = f"Sem sugestão {BY_CONTEXT}"
UNDO = "Para desfazer, escolha outra pessoa, ignore ou mescle numa voz sem pessoa."     # P-03: os três tiram o reforço
MORE_TIP = f"Mesclar com outra voz, ignorar, editar a pessoa ou sugerir de novo {BY_CONTEXT}"
NAME_CHARS = 14                      # o nome que cabe inteiro em "É <nome>, pelo contexto"; maior é cortado no meio (A-19)


class Primary(StrEnum):
    CONFIRM = "confirmar"            # a pessoa sugerida pela voz (ou a da decisão automática)
    CHOOSE = "escolher"              # sem candidato: a lista de pessoas
    NONE = "nada"                    # confirmada, mesclada ou nenhuma voz: apagado


@dataclass(frozen=True, slots=True)
class ReviewActions:
    primary: Primary
    primary_text: str
    context_text: str | None         # "É Rodrigo, pelo contexto"; None = sem sugestão pelo contexto (o L apagado)
    line: str                        # U-101: o que a confirmação faz e como desfazer


def action_labels(item: review.ReviewItem | None, names: VoiceNames) -> ReviewActions:
    """A-19: os rótulos da voz `item` por estado (a tabela do módulo); `names` dá o "Voz 3" da mesclada."""
    if item is None:
        return ReviewActions(Primary.NONE, "Confirmar", None, "")
    if item.merged_into is not None:
        target = names.title(item.merged_into, f"voz #{item.merged_into}")
        return ReviewActions(Primary.NONE, f"É a mesma que a {target}", None, f"Mesclada com a {target}.")
    name = item.person_name
    if item.status == ClusterStatus.CONFIRMED:
        return ReviewActions(Primary.NONE, f"É {name}" if name else "Confirmada", None, _done_line(item, name))
    context = f"É {item.llm_name}, {BY_CONTEXT}" if item.has_llm else None
    if item.person_id is None or not name:
        return ReviewActions(Primary.CHOOSE, CHOOSE_TEXT, context, _choose_line(item))
    text = f"Confirmar {name}" if item.status == ClusterStatus.AUTO else f"É {name}"
    return ReviewActions(Primary.CONFIRM, text, context, _confirm_line(item, name))


def _confirm_line(item: review.ReviewItem, name: str) -> str:
    """U-101: sem vetor da voz, a confirmação só dá o nome às falas; a linha não promete aprendizado."""
    if item.has_centroid:
        return f"Ao confirmar, o perfil de voz de {name} aprende com esta fala. {UNDO}"
    return f"Ao confirmar, as falas desta voz ficam com o nome de {name}."


def _choose_line(item: review.ReviewItem) -> str:
    if item.has_centroid:
        return f"Ao escolher a pessoa, o perfil de voz dela aprende com esta fala. {UNDO}"
    return "Ao escolher a pessoa, as falas desta voz ficam com o nome dela."


def _done_line(item: review.ReviewItem, name: str) -> str:
    if not name:
        return ""
    if item.has_centroid:
        return f"O perfil de voz de {name} aprendeu com esta fala. {UNDO}"
    return f"As falas desta voz estão com o nome de {name}."


class ReviewActionsMixin(PersonActionsMixin):
    """As ações da voz do `ReviewDialog` (o diálogo é o pai das caixas de pergunta e do menu)."""
    items: list[review.ReviewItem]
    names: VoiceNames
    player: Player | None
    played: list[tuple[str, int, int]]

    def regenerate(self, step: JobStep) -> int | None:
        raise NotImplementedError

    # ---------------------------------------------------------------- a coluna (A-19, A-21)
    def build_actions(self) -> QWidget:
        """A coluna das ações, de largura fixa, com o "Mais"; a linha do U-101 (`self.learn`) o diálogo põe embaixo."""
        self.btn_confirm = KeyButton(CHOOSE_TEXT, "Enter")
        self.btn_llm = KeyButton(NO_CONTEXT, "L")
        self.btn_other = KeyButton("Outra pessoa", "O")
        self.btn_new = KeyButton("Nova pessoa", "N")
        self.btn_more = menu_button("Mais", MORE_TIP, self._more_menu())
        self.learn = QLabel("")
        note(self.learn)
        self.learn.setWordWrap(True)
        self.learn.setTextFormat(Qt.TextFormat.PlainText)                 # o nome é o que o usuário digitou (R7-07)
        self.learn.setMinimumHeight(2 * self.learn.fontMetrics().lineSpacing())   # uma ou duas linhas, sem pular a tela
        for button, slot in ((self.btn_confirm, self.press_primary), (self.btn_llm, self.accept_llm_current),
                             (self.btn_other, self.ask_other_person), (self.btn_new, self.ask_new_person)):
            button.clicked.connect(slot)
        column = QWidget()
        lay = QVBoxLayout(column)
        lay.setContentsMargins(0, 0, 0, 0)
        for b in (self.btn_confirm, self.btn_llm, self.btn_other, self.btn_new, self.btn_more):
            b.setAutoDefault(False)                        # U-086: nenhum vira o padrão do Enter (o Enter num botão: `keyPressEvent`)
            lay.addWidget(b)
        lay.addStretch(1)
        column.setFixedWidth(self._column_width())
        return column

    def _more_menu(self) -> QMenu:
        """U-100 (A-21): as ações raras, com a tecla depois do Tab (o `handle_key` a atende; nenhum atalho do Qt)."""
        menu = QMenu(self._parent())
        menu.setToolTipsVisible(True)
        self.act_merge = menu.addAction("É a mesma que…\tM")
        self.act_ignore = menu.addAction("Ignorar\tI")
        self.act_edit = menu.addAction("Editar pessoa\tE")                  # U-041: a ficha da pessoa desta voz
        menu.addSeparator()
        self.act_identify = menu.addAction(f"Sugerir de novo {BY_CONTEXT}")  # F3.1 R3-05: `identify` sob demanda
        for act, slot in ((self.act_merge, self.ask_merge), (self.act_ignore, self.ignore_current),
                          (self.act_edit, self.edit_current), (self.act_identify, self._identify_clicked)):
            act.triggered.connect(slot)
        return menu

    def _column_width(self) -> int:
        """A maior largura dos botões e a de "É <nome>, pelo contexto" com `NAME_CHARS` letras médias no nome."""
        b, fm = self.btn_llm, self.btn_llm.fontMetrics()
        frame = b.full_width - fm.horizontalAdvance(b.text())              # as bordas, o vão e o selo da tecla
        sample = frame + fm.horizontalAdvance(f"É , {BY_CONTEXT}") + NAME_CHARS * fm.averageCharWidth()
        buttons = (self.btn_confirm, self.btn_llm, self.btn_other, self.btn_new, self.btn_more)
        return max(sample, *(w.sizeHint().width() for w in buttons))

    def apply_actions(self) -> None:
        """Os rótulos da voz selecionada (A-19): o principal, o L, os itens do "Mais" e a linha do U-101."""
        it = self.current_item()
        acts = action_labels(it, self.names)
        self.btn_confirm.set_label(acts.primary_text, "Enter")
        self.btn_confirm.setEnabled(acts.primary is not Primary.NONE)
        self.btn_llm.set_label(acts.context_text or NO_CONTEXT, "L")
        self.btn_llm.setEnabled(acts.context_text is not None)
        self.learn.setText(acts.line)
        self.act_merge.setEnabled(it is not None and any(o.cluster_id != it.cluster_id and o.merged_into is None for o in self.items))
        self.act_ignore.setEnabled(it is not None)
        self._update_edit_action()

    # ---------------------------------------------------------------- ações
    def press_primary(self) -> None:
        """U-098: o Enter (e o clique no principal) faz o que o principal diz; apagado, nada."""
        primary = action_labels(self.current_item(), self.names).primary
        if primary is Primary.CONFIRM:
            self.confirm_current()
        elif primary is Primary.CHOOSE:
            self.ask_other_person()

    def confirm_current(self, person_id: int | None = None) -> bool:
        it = self.current_item()
        if it is None:
            return False
        if person_id is None and it.person_id is None:
            self.ask_other_person()
            return False
        try:
            review.confirm(self.store, it.cluster_id, person_id)
        except ValueError as e:
            QMessageBox.warning(self._parent(), "Revisão", str(e))
            return False
        self._edited()
        self._next_pending()
        return True

    def accept_llm_current(self) -> bool:
        """Tecla `L`: aceita a sugestão pelo contexto (pessoa cadastrada ou nome novo; `decision_source = usuario_llm`), só
        onde o botão a mostra (voz pendente e não mesclada, CP11)."""
        it = self.current_item()
        if it is None:
            return False
        if action_labels(it, self.names).context_text is None:
            self.header.setText(f"Esta voz não tem sugestão {BY_CONTEXT}.")
            return False
        try:
            review.accept_llm(self.store, it.cluster_id)
        except ValueError as e:
            QMessageBox.warning(self._parent(), "Revisão", str(e))
            return False
        self._edited()
        self._next_pending()
        return True

    def ask_merge(self) -> None:
        it = self.current_item()
        others = [o for o in self.items if o.cluster_id != (it.cluster_id if it else None) and o.merged_into is None]
        if it is None or not others:
            return
        labels = [f"{self.names.title(o.cluster_id, o.title)} · {o.summary()}" for o in others]
        choice, ok = QInputDialog.getItem(self._parent(), "É a mesma pessoa que…", "Voz:", labels, 0, False)
        if ok and choice in labels:
            self.merge_into(others[labels.index(choice)].cluster_id)

    def merge_into(self, cluster_id: int) -> bool:
        it = self.current_item()
        if it is None:
            return False
        try:
            review.merge(self.store, it.cluster_id, cluster_id)
        except ValueError as e:
            QMessageBox.warning(self._parent(), "Revisão", str(e))
            return False
        self._edited()
        self._next_pending()
        return True

    def ignore_current(self) -> None:
        it = self.current_item()
        if it is not None:
            review.ignore(self.store, it.cluster_id)
            self._edited()
            self._next_pending()

    def play_current(self) -> None:
        it = self.current_item()
        if it is None:
            return
        rng = review.clip_range(self.store, it.cluster_id)
        if rng is None:
            self.header.setText("Esta voz ainda não tem trecho para ouvir.")
            return
        channel, t0, t1 = rng
        self.played.append(rng)
        if self.player is None:
            self.header.setText("Reprodução indisponível nesta janela.")
            return
        self.player.play_from(self.meeting_id, t0, end_ms=t1, channel=channel,
                              title=f"{self.names.title(it.cluster_id, it.title)} (5 s)")

    def _identify_clicked(self) -> None:
        self.regenerate(JobStep.IDENTIFY)


__all__ = ["CHOOSE_TEXT", "NO_CONTEXT", "UNDO", "Primary", "ReviewActions", "ReviewActionsMixin", "action_labels"]
