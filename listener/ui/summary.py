"""Resumo de fim de gravação (F1): duração, pedaços, tamanho, marcadores; título editável (vai para revisions);
pedaços não gravados, se houver (M-04). F5: o título salvo aqui chama `on_edited` (o índice da busca usa o título em todo
trecho; o pesado pode já ter indexado com o antigo, R5-07). F5.4 L2 (U-069): o cabeçalho com o ícone de sucesso e o título
de 20 px; a lista dos marcadores só quando há marcador; OK é o primário (Enter, U-056). Revisão da F5.4: com pedaços não
gravados, o título diz que houve falha ("Gravação salva com falha"), junto do ícone de aviso.

F5.6 L1b (U-089, A-19): as caixas "Gerar ata e ações" e "Gerar resumo" (`RequestBoxes`), desmarcadas na primeira vez e depois
com a última escolha; sem chave, apagadas com o aviso. O OK grava a escolha e, com alguma marcada, abre o popup do pedido;
a escolha vira o pedido preso ao processamento que o parar enfileirou (`after_job_id`, que `_enqueue_processing` devolve;
None = o próximo processamento, com o `pipeline.auto_start` desligado). O X e o Esc não pedem nada nem gravam a escolha;
revisão do L1b: o "Cancelar" do popup também não grava (só o pedido feito, ou o OK sem caixa marcada), e o resultado do
pedido, ou a falha dele, vai para o aviso do app (`on_message`, R-05)."""
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QWidget

from ..capture.clock import fmt_mmss
from ..log import get_logger
from ..pipeline.llm_requests import Species
from . import llm_ask
from .common import human_size
from .icons import set_icon
from .llm_request import Chooser, RequestBoxes, choose
from .theme import Tone, primary, role, tone

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.summary")

HEADER_ICON_PX = 32
SAVED, SAVED_WITH_FAILURE = "Gravação salva", "Gravação salva com falha"


class SummaryDialog(QDialog):
    def __init__(self, store: Store, meeting_id: int, stats: dict[str, Any], parent: QWidget | None = None,
                 on_edited: Callable[..., Any] | None = None, *, after_job_id: int | None = None,
                 on_launch: Callable[[bool], Any] | None = None, on_message: Callable[[str], Any] | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_edited = on_edited                     # F5: `on_edited(meeting_id, motivo)` reindexa a reunião
        self.after_job_id = after_job_id               # F5.6 (A-18): o processamento a que o pedido fica preso
        self.on_launch = on_launch                     # `on_launch(True)`: o worker leve, quando o pedido já virou job
        self.on_message = on_message                   # R-05: o resultado do pedido no aviso do app
        self.choose: Chooser = choose                  # U-090: o popup (os testes trocam)
        self.setWindowTitle(SAVED_WITH_FAILURE if stats.get("unsaved") else SAVED)
        self.setMinimumWidth(480)
        m = store.get_meeting(meeting_id) or {}
        cs = store.chunk_stats(meeting_id)
        self.title_edit = QLineEdit(m.get("title") or "")
        self.title_edit.setPlaceholderText("título da reunião")
        form = QFormLayout(self)
        form.addRow(self._header(stats.get("unsaved")))
        form.addRow("Título", self.title_edit)
        form.addRow("Duração", QLabel(fmt_mmss(stats.get("duration_s", 0))))
        for ch in ("L", "R"):
            c = cs.get(ch)
            gaps = fmt_mmss((c["gaps"] or 0) / (c["sr"] or 16000)) if c else ""
            txt = "sem áudio" if not c else f"{c['n']} pedaços · {human_size(c['bytes'] or 0)} · buracos preenchidos: {gaps}"
            form.addRow(f"Canal {ch}", QLabel(txt))
        markers = store.list_markers(meeting_id)
        self.markers: QListWidget | None = None
        if markers:                                    # U-069: a lista só quando há marcador
            self.markers = QListWidget()
            for mk in markers:
                self.markers.addItem(f"[{fmt_mmss(mk['t_ms'] / 1000)}] {mk['note'] or '(sem nota)'}")
            self.markers.setMaximumHeight(min(160, 8 + 36 * len(markers)))
            form.addRow(f"Marcadores ({len(markers)})", self.markers)
        self._warnings(form, stats)
        sizes = store.db_sizes()
        form.addRow("Banco", QLabel(f"texto {human_size(sizes['main'])} · áudio {human_size(sizes['audio'])} "
                                    f"em {len(sizes.get('audio_files', {}))} arquivo(s)"))
        self.boxes = RequestBoxes(store)               # U-089: o pedido ao parar
        form.addRow("Depois de processar", self.boxes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        form.addRow(buttons)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            primary(self, ok)

    @staticmethod
    def _warnings(form: QFormLayout, stats: dict[str, Any]) -> None:
        unsaved = stats.get("unsaved") or []
        if unsaved:
            lab = QLabel(f"{len(unsaved)} pedaço(s) não foram gravados no banco ({', '.join(unsaved)}); a reunião ficou "
                         "marcada como interrompida. Veja o log.")
            tone(lab, Tone.DANGER)
            lab.setWordWrap(True)
            form.addRow("Atenção", lab)
        warns = stats.get("warnings") or []
        if warns:
            w = QLabel("\n".join(warns))
            w.setWordWrap(True)
            form.addRow("Avisos", w)

    @staticmethod
    def _header(unsaved: object) -> QHBoxLayout:
        """U-069: o ícone de sucesso e "Gravação salva" em 20 px; com pedaços não gravados, o de aviso e a falha no título."""
        icon = QLabel()
        icon.setFixedSize(HEADER_ICON_PX, HEADER_ICON_PX)
        if unsaved:
            set_icon(icon, "warning", Tone.WARNING, HEADER_ICON_PX)
        else:
            set_icon(icon, "success", Tone.SUCCESS, HEADER_ICON_PX)
        title = QLabel(SAVED_WITH_FAILURE if unsaved else SAVED)
        role(title, "subtitle")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(icon)
        row.addWidget(title, 1)
        return row

    @override
    def accept(self) -> None:
        title = self.title_edit.text().strip()
        changed = bool(title) and title != str((self.store.get_meeting(self.meeting_id) or {}).get("title") or "")
        if changed:
            self.store.update_title(self.meeting_id, title, author="usuario")
            if self.on_edited is not None:
                self.on_edited(self.meeting_id, "edit")
        chosen = self.boxes.chosen()
        if not chosen or self.ask(chosen) is not None:
            self.boxes.remember()                      # A-19: a última escolha, só no OK com o pedido feito (ou sem caixa)
        super().accept()

    def ask(self, species: frozenset[Species]) -> llm_ask.Sent | None:
        """U-089: o popup e o pedido preso ao processamento do parar (`retry=False`: uma reunião nova sempre pergunta);
        None = cancelado no popup ou falhou. Falhar aqui nunca impede o resumo de fechar: o log leva o rastro e o aviso diz
        para pedir de novo pela reunião (R-05)."""
        try:
            sent = llm_ask.ask(self.store, self.meeting_id, llm_ask.tasks_of(species), "ao parar a gravação",
                               functools.partial(self.choose, self), after_job_id=self.after_job_id, retry=False)
        except Exception:  # o pedido não trava o fim da gravação; o log diz o porquê e o aviso, o que fazer
            log.warning("reunião %s: o pedido de ata ao parar falhou", self.meeting_id, exc_info=True)
            self._message(llm_ask.ASK_FAILED)
            return None
        if sent is not None:
            if sent.job_id is not None and self.on_launch is not None:
                self.on_launch(True)
            self._message(sent.message)
        return sent

    def _message(self, text: str) -> None:
        if self.on_message is not None and text:
            self.on_message(text)
