"""Idioma da reunião aberta (F5.3 L1, U-046, A-05, pergunta 9): "Idioma: pt-BR" no cabeçalho e a troca pelo outro idioma.
Trocar reprocessa a reunião (refaz a separação de vozes e a transcrição) e refaz pela LLM o título, a identidade por
contexto e a transcrição revisada; ata, resumo e ações ficam "desatualizados" até serem gerados de novo (F5.6, R-06). Antes,
a pergunta diz o tempo e o custo estimados (as estimativas da fila e do job) e que nada se apaga (CP3).
Durante o reprocessamento, "pt-BR → en (reprocessando)"; com a reunião gravando, a troca fica desabilitada. O pedido passa
pelo helper único de reprocessar (`pipeline/reprocess.py`). R-08: com a troca ainda não aplicada, o botão oferece "Voltar
para" o idioma que a reunião tem, com uma pergunta própria (sem tempo nem custo: desfazer não reprocessa nem chama a LLM);
desfeita, ele volta a oferecer o outro idioma.

F5.6 L1b (item 12, A-49): a pergunta da troca ganha as caixas "Gerar ata e ações" e "Gerar resumo" (`RequestBoxes`, com a
mesma última escolha do resumo da gravação; sem chave, apagadas). Marcadas, depois de confirmar vem o popup do pedido, e o
pedido fica preso ao job do reprocessamento (`after_job_id`); o "Reprocessar" da fila não pergunta (P-03). Desfazer a troca
não tem caixas. Revisão do L1b (como o desvio 6 no resumo da gravação): a escolha das caixas só fica gravada com o pedido
feito, ou sem caixa marcada; o "Cancelar" do popup não a grava."""
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..config.language import label, meeting_language, other, output_code
from ..llm.costs import fmt_usd
from ..log import get_logger
from ..model import JobKind
from ..pipeline import reprocess
from ..pipeline.estimate import fmt_remaining, remaining_seconds
from ..pipeline.llm_requests import Species
from . import llm_ask
from .icons import set_icon
from .llm_request import Chooser, RequestBoxes, choose
from .theme import Tone, note

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.meeting")

TITLE = "Idioma da reunião"
RECORDING_TIP = "Desabilitado durante a gravação: troque depois de parar."
UNDO_TIP = "Desfaz a troca ainda não aplicada: a reunião fica como está, sem reprocessar e sem custo da LLM."
UNDO_BUTTON = "Desfazer a troca"
ICON_PX = 32

# (pai, texto, botão de confirmar, banco das caixas ou None sem caixas) -> None = cancelou; senão as espécies marcadas
type Confirm = Callable[[QWidget, str, str, Store | None], frozenset[Species] | None]


class ChangeConfirm(QDialog):
    """A pergunta da troca (ou do desfazer), com "Cancelar" como padrão (reprocessar custa tempo e LLM); com `store`, as
    caixas de ata e resumo embaixo do texto (A-49)."""

    def __init__(self, text: str, yes: str, store: Store | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(520)
        icon = QLabel()
        icon.setFixedSize(ICON_PX, ICON_PX)
        set_icon(icon, "help", Tone.INFO, ICON_PX)
        self.text = QLabel(text)
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setWordWrap(True)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self.text, 1)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addLayout(top)
        self.boxes = RequestBoxes(store) if store is not None else None
        if self.boxes is not None:
            lay.addWidget(self.boxes)
        self.btn_yes = QPushButton(yes, self)            # com o pai na criação: a largura já é a de um botão de diálogo
        self.btn_cancel = QPushButton("Cancelar", self)
        self.btn_yes.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_cancel.setDefault(True)                 # Enter e Esc cancelam; confirmar pede o clique ou o foco nele
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn_yes)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)
        self.btn_cancel.setFocus()

    def take_choice(self) -> frozenset[Species]:
        """As caixas marcadas (nenhuma sem caixas ou sem chave); quem pede grava a escolha depois do popup (A-19)."""
        return self.boxes.chosen() if self.boxes is not None else frozenset()


def confirm_change(parent: QWidget, text: str, yes: str, store: Store | None = None) -> frozenset[Species] | None:
    """A pergunta; separada para os testes trocarem. None = cancelou; senão as espécies marcadas nas caixas."""
    dlg = ChangeConfirm(text, yes, store, parent)
    try:
        return dlg.take_choice() if dlg.exec() == QDialog.DialogCode.Accepted else None
    finally:
        dlg.deleteLater()


def change_question(store: Store, meeting_id: int, target: str) -> str:
    """O texto da pergunta (pergunta 9): o tempo do reprocessamento pela velocidade medida da fila, o custo da LLM pela
    conta do job sobre a transcrição atual e o que fica desatualizado (F5.6: a ata e o resumo só a pedido, pelas caixas)."""
    from ..pipeline.llm_steps import estimate_meeting_usd

    m = store.get_meeting(meeting_id) or {}
    duration = int(m.get("duration_ms") or 0)
    eta = fmt_remaining(remaining_seconds(None, 0.0, duration / 1000 if duration else None, store.get_setting("pipeline.rtf_json"),
                                          kind=str(JobKind.REPROCESS)))
    if store.providers.active_with_key("llm") is None:
        llm = "sem chave de LLM, nada é refeito agora (Configurações, Chaves de acesso)"
    else:
        cost = fmt_usd(estimate_meeting_usd(store, meeting_id))
        llm = f"refaz o título, a identificação das vozes e a transcrição revisada (custo estimado: {cost})"
    return (f"Reprocessar esta reunião em {label(target)}?\n\n"
            f"Tempo estimado: {eta} (refaz a separação de vozes e a transcrição).\n"
            f"LLM: {llm}.\n"
            "A ata, o resumo e as ações ficam como estão, marcados como desatualizados, até serem gerados de novo: marque "
            "abaixo para pedir junto com o reprocessamento (o pedido pergunta antes, com o custo). Nada se apaga.")


def undo_question(store: Store, meeting_id: int) -> str:
    """R-08: voltar ao idioma que a reunião já tem desfaz a troca em curso; nada é reprocessado nem refeito pela LLM."""
    current = meeting_language(store, store.get_meeting(meeting_id))
    running = reprocess.pending_language(store, meeting_id)
    asked = f" para {label(running[1])}" if running is not None else ""
    return (f"Desfazer a troca{asked}?\n\n"
            f"A reunião continua em {label(current)}, como está: o reprocessamento pedido para a troca sai da fila (ou para "
            "entre as janelas, se já começou), um processamento que já estava na fila segue como antes e a LLM não refaz "
            "nada por causa dela.")


class MeetingLanguage(QWidget):
    """O idioma no cabeçalho da reunião e o botão que troca pelo outro (a troca em curso aparece com a seta)."""

    def __init__(self, store: Store, meeting_id: int, *, on_launch: Callable[[bool], Any] | None = None,
                 on_message: Callable[[str], Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_launch = on_launch                       # `on_launch(False)`: o controlador lança o worker pesado
        self.on_message = on_message
        self.confirm: Confirm = confirm_change
        self.choose: Chooser = choose                    # F5.6 L1b (U-090): o popup do pedido (os testes trocam)
        self.target = other(None)                        # o idioma que o botão oferece agora
        self.label = QLabel("")
        self.label.setObjectName("meetingInfo")
        note(self.label)                                 # F5.4 L4 (U-078): os fatos do cabeçalho em legenda
        self.button = QPushButton("")
        self.button.clicked.connect(self.ask_change)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        lay.addWidget(self.button)
        self.reload()

    def reload(self) -> None:
        m = self.store.get_meeting(self.meeting_id) or {}
        current = meeting_language(self.store, m)
        running = reprocess.pending_language(self.store, self.meeting_id)
        goal = output_code(running[1]) if running is not None else current
        if running is None:
            self.label.setText(f"Idioma: {current}")
        elif goal == current:
            self.label.setText(f"Idioma: {current} (reprocessando)")
        else:
            self.label.setText(f"Idioma: {current} → {goal} (reprocessando)")
        self.target = other(goal)
        undo = running is not None and self.target == current          # R-08: a troca em curso pode ser desfeita
        recording = str(m.get("status")) in reprocess.RECORDING
        self.button.setText(f"{'Voltar para' if undo else 'Trocar para'} {label(self.target)}…")
        self.button.setEnabled(bool(m) and not recording)
        if recording:
            self.button.setToolTip(RECORDING_TIP)
        elif undo:
            self.button.setToolTip(UNDO_TIP)
        else:
            self.button.setToolTip(f"Reprocessa a reunião em {label(self.target)}: transcrição, vozes e tudo o que a LLM escreveu")

    def ask_change(self) -> int | None:
        """A pergunta com tempo e custo; confirmada, o reprocessamento no idioma novo e, com caixa marcada, o popup e o
        pedido preso a ele (A-49). Voltar ao idioma que a reunião tem desfaz a troca em curso, com a pergunta dela, sem caixas
        (R-08). Devolve o job que fará trabalho (None quando nada roda)."""
        target = self.target
        undo = target == meeting_language(self.store, self.store.get_meeting(self.meeting_id))
        if undo:
            text, button = undo_question(self.store, self.meeting_id), UNDO_BUTTON
        else:
            text, button = change_question(self.store, self.meeting_id, target), f"Reprocessar em {label(target)}"
        picked = self.confirm(self, text, button, None if undo else self.store)
        if picked is None:
            log.info("reunião %s: %s cancelado na pergunta", self.meeting_id, "desfazer a troca de idioma" if undo else
                     f"trocar o idioma para {target}")
            return None
        result = reprocess.request_reprocess(self.store, self.meeting_id, target)
        made, asked = self._ask_llm(picked, result.job_id) if picked and not undo and result.job_id is not None else (not picked, "")
        if made and not undo and llm_ask.has_key(self.store):
            llm_ask.save_choice(self.store, picked)        # A-19: a última escolha, com o pedido feito (ou sem caixa)
        if self.on_message is not None:
            self.on_message(f"{result.message} {asked}".strip())
        if result.job_id is not None and self.on_launch is not None:
            self.on_launch(False)
        self.reload()
        return result.job_id

    def _ask_llm(self, species: frozenset[Species], job_id: int) -> tuple[bool, str]:
        """A-49: o popup e o pedido preso ao reprocessamento (`retry=False`: a transcrição vai mudar, sempre pergunta);
        devolve (pedido feito, a mensagem que vai junto da do reprocessamento; vazia quando cancelou no popup)."""
        try:
            sent = llm_ask.ask(self.store, self.meeting_id, llm_ask.tasks_of(species), "troca de idioma",
                               functools.partial(self.choose, self.window()), after_job_id=job_id, retry=False)
        except Exception:  # o pedido não desfaz a troca já pedida; o log diz o porquê e o aviso, o que fazer
            log.warning("reunião %s: o pedido de ata da troca de idioma falhou", self.meeting_id, exc_info=True)
            return False, llm_ask.ASK_FAILED
        if sent is None:
            return False, ""
        if sent.job_id is not None and self.on_launch is not None:
            self.on_launch(True)
        return True, sent.message


__all__ = ["ChangeConfirm", "MeetingLanguage", "change_question", "confirm_change", "undo_question"]
