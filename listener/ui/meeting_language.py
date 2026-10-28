"""Idioma da reunião aberta (F5.3 L1, U-046, A-05, pergunta 9): "Idioma: pt-BR" no cabeçalho e a troca pelo outro idioma.
Trocar reprocessa a reunião (refaz a separação de vozes e a transcrição) e refaz pela LLM a ata, o executivo, o pré-resumo,
o título, a identidade por contexto e a transcrição revisada; antes, a pergunta diz o tempo estimado, o custo estimado da
LLM (as estimativas da fila e do job) e que a ata editada e as ações viram versão anterior (CP3: nada se apaga).
Durante o reprocessamento, "pt-BR → en (reprocessando)"; com a reunião gravando, a troca fica desabilitada. O pedido passa
pelo helper único de reprocessar (`pipeline/reprocess.py`). R-08: com a troca ainda não aplicada, o botão oferece "Voltar
para" o idioma que a reunião tem, com uma pergunta própria (sem tempo nem custo: desfazer não reprocessa nem chama a LLM);
desfeita, ele volta a oferecer o outro idioma."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from ..config.language import label, meeting_language, other, output_code
from ..llm.costs import fmt_usd
from ..log import get_logger
from ..model import JobKind
from ..pipeline import reprocess
from ..pipeline.estimate import fmt_remaining, remaining_seconds

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.meeting")

TITLE = "Idioma da reunião"
RECORDING_TIP = "Desabilitado durante a gravação: troque depois de parar."
UNDO_TIP = "Desfaz a troca ainda não aplicada: a reunião fica como está, sem reprocessar e sem custo da LLM."
UNDO_BUTTON = "Desfazer a troca"

type Confirm = Callable[[QWidget, str, str], bool]          # (pai, texto, botão de confirmar) -> confirmou?


def confirm_change(parent: QWidget, text: str, yes: str) -> bool:
    """A pergunta da troca, com "Cancelar" como padrão (reprocessar custa tempo e LLM); separada para os testes trocarem."""
    box = QMessageBox(QMessageBox.Icon.Question, TITLE, text, parent=parent)
    accept = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is accept


def change_question(store: Store, meeting_id: int, target: str) -> str:
    """O texto da pergunta (pergunta 9): o tempo do reprocessamento pela velocidade medida da fila, o custo da LLM pela
    conta do job sobre a transcrição atual e o que vira versão anterior."""
    from ..pipeline.llm_steps import estimate_meeting_usd

    m = store.get_meeting(meeting_id) or {}
    duration = int(m.get("duration_ms") or 0)
    eta = fmt_remaining(remaining_seconds(None, 0.0, duration / 1000 if duration else None, store.get_setting("pipeline.rtf_json"),
                                          kind=str(JobKind.REPROCESS)))
    if store.providers.active_with_key("llm") is None:
        llm = "sem chave de LLM, a ata não é refeita agora (Configurações, Chaves de acesso)"
    else:
        cost = fmt_usd(estimate_meeting_usd(store, meeting_id))
        llm = f"ata, resumo executivo, pré-resumo, título, identidade e transcrição revisada de novo (custo estimado: {cost})"
    return (f"Reprocessar esta reunião em {label(target)}?\n\n"
            f"Tempo estimado: {eta} (refaz a separação de vozes e a transcrição).\n"
            f"LLM: {llm}.\n"
            "A ata editada e as ações atuais ficam como versão anterior; nada se apaga.")


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
        self.target = other(None)                        # o idioma que o botão oferece agora
        self.label = QLabel("")
        self.label.setObjectName("meetingInfo")
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
        """A pergunta com tempo e custo; confirmada, o reprocessamento no idioma novo. Voltar ao idioma que a reunião tem
        desfaz a troca em curso, com a pergunta dela (R-08). Devolve o job que fará trabalho (None quando nada roda)."""
        target = self.target
        undo = target == meeting_language(self.store, self.store.get_meeting(self.meeting_id))
        if undo:
            text, button = undo_question(self.store, self.meeting_id), UNDO_BUTTON
        else:
            text, button = change_question(self.store, self.meeting_id, target), f"Reprocessar em {label(target)}"
        if not self.confirm(self, text, button):
            log.info("reunião %s: %s cancelado na pergunta", self.meeting_id, "desfazer a troca de idioma" if undo else
                     f"trocar o idioma para {target}")
            return None
        result = reprocess.request_reprocess(self.store, self.meeting_id, target)
        if self.on_message is not None:
            self.on_message(result.message)
        if result.job_id is not None and self.on_launch is not None:
            self.on_launch(False)
        self.reload()
        return result.job_id


__all__ = ["MeetingLanguage", "change_question", "confirm_change", "undo_question"]
