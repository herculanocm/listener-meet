"""Revisão de falantes (F2 item 13, U-006; F3 item 5): um card por voz com a sugestão pela voz e a pelo contexto (origem,
confiança, citação com minuto); setas navegam, Espaço ouve 5 s (numa `Task`). F3 item 11: abas de título, pré-resumo, ata,
executivo e ações editáveis com "gerar de novo" por aba (job `llm`), selo de custo no cabeçalho. "Concluir" marca a reunião
como revisada. Só metadados no construtor (R-9). F5: cada decisão sobre uma voz e cada edição das abas chama
`on_edited(meeting_id, motivo, now=...)` (o controlador reindexa a reunião 30 s depois da última; "Concluir" na hora). F5 L4:
as abas da LLM vêm do `LlmTabsGroup` (o mesmo da reunião aberta, F5A-26) e os 5 s passam pelo player único do app (um som
por vez; o aviso de reprodução aparece no cabeçalho).
U-041 (F5.2): a ficha da pessoa (`ui/review_person.py`). F5.4 L4 (U-086, U-035; `ui/review_cards.py`): cada voz num cartão,
as ações numa coluna com a tecla num selo e o primário seguindo a etapa (com voz pendente, o da voz; sem nenhuma, "Concluir").
F5.11 L1 (`ui/review_actions.py`, A-35): os botões dizem o resultado, com o nome (U-098), o "Ouvir 5 s" no cartão
selecionado (U-099), as ações raras no "Mais" (U-100) e a linha do que a confirmação ensina ao perfil (U-101). As teclas são
as mesmas (Enter, L, O, N, M, I, E, Espaço); o Enter faz o que o principal diz e, apagado, nada."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QPushButton, QTabWidget,
                               QVBoxLayout, QWidget)

from ..fmt import plural
from ..log import get_logger
from ..model import JobStep, SummaryKind
from ..pipeline import llm_requests, review
from ..pipeline.review_suggest import LearnHint, learn_hints
from ..pipeline.voices import VoiceNames, voice_names
from .common import click_focused_button, keep_until_destroyed
from .llm_tabs import ActionsTab, LlmTabsGroup, TextTab, TitleTab
from .player import ERROR, PlayerState
from .review_actions import ReviewActionsMixin
from .review_cards import VoiceCardDelegate, card_item, stage_primary, taken_voices

if TYPE_CHECKING:
    from ..store import Store
    from .player import Player

log = get_logger("ui.review")
ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


class ReviewDialog(ReviewActionsMixin, QDialog):
    def __init__(self, store: Store, meeting_id: int, parent: QWidget | None = None, on_transcript: Any = None,
                 on_launch: Callable[[bool], Any] | None = None, on_edited: Callable[..., Any] | None = None,
                 player: Player | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.on_transcript = on_transcript
        self.on_launch = on_launch
        self.on_edited = on_edited                         # F5: `on_edited(meeting_id, motivo, now=False)` reindexa a reunião
        self.player = player                               # F5: o player único do app (None = sem som nesta janela)
        self.items: list[review.ReviewItem] = []
        self.names = VoiceNames()                          # U-035: "Voz 2" no lugar do rótulo interno
        self.hints: dict[int, LearnHint] = {}               # F5.15 (U-138): a previsão da linha do U-101, por voz
        self.played: list[tuple[str, int, int]] = []       # instrumentação dos testes
        self.reloads = 0
        m = store.get_meeting(meeting_id) or {}
        self.setWindowTitle(f"Revisão — {m.get('title') or f'reunião {meeting_id}'}")
        self.setMinimumSize(760, 480)
        self.header = QLabel("")
        self.header.setWordWrap(True)
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list.installEventFilter(self)                 # Enter, Espaço e letras valem com o foco na lista
        self.cards = VoiceCardDelegate(self.list)          # U-086: cada voz num cartão; U-099: o "Ouvir 5 s" no selecionado
        self.list.setItemDelegate(self.cards)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setSpacing(2)
        column = self.build_actions()                      # F5.11 (A-19 a A-21): a coluna, o "Mais" e a linha do U-101
        self.btn_transcript = QPushButton("Ver transcrição")
        self.btn_finish = QPushButton("Concluir")
        for b in (self.btn_transcript, self.btn_finish):
            b.setAutoDefault(False)                        # U-086: nenhum vira o padrão do Enter (o Enter num botão: `keyPressEvent`)
        self.btn_transcript.clicked.connect(self._open_transcript)
        self.btn_finish.clicked.connect(self.finish)
        body = QHBoxLayout()
        body.addWidget(self.list, 1)
        body.addWidget(column)
        foot = QHBoxLayout()
        foot.addWidget(self.btn_transcript)
        foot.addStretch(1)
        foot.addWidget(self.btn_finish)
        voices = QWidget()
        vlay = QVBoxLayout(voices)
        vlay.addLayout(body, 1)
        vlay.addWidget(self.learn)
        vlay.addLayout(foot)
        self.tabs = QTabWidget()
        self.tabs.addTab(voices, "Vozes")
        self.llm = LlmTabsGroup(store, meeting_id, on_launch=on_launch, on_edited=self._text_edited, on_message=self.header.setText,
                                after_regenerate=self.reload_llm, parent=self)
        self.title_tab: TitleTab = self.llm.title_tab
        self.text_tabs: dict[str, TextTab] = self.llm.text_tabs
        self.actions_tab: ActionsTab = self.llm.actions_tab
        self.cost = self.llm.cost
        self.tabs.addTab(self.title_tab, "Título")
        self.tabs.addTab(self.text_tabs[SummaryKind.PRE_SUMMARY], "Pré-resumo")
        self.tabs.addTab(self.text_tabs[SummaryKind.MINUTES], "Ata")
        self.tabs.addTab(self.text_tabs[SummaryKind.EXECUTIVE], "Executivo")
        self.tabs.addTab(self.actions_tab, "Ações")
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.cost)
        lay.addWidget(self.tabs, 1)
        self.list.itemDoubleClicked.connect(self._double_clicked)          # R-06: métodos, não lambdas com `self`
        self.list.currentRowChanged.connect(self._row_changed)
        self.cards.play_requested.connect(self.card_play)
        if player is not None:
            player.state_changed.connect(self._on_player)
        self.reload(focus=True)
        keep_until_destroyed(self)                         # R-06: a coleta de ciclos não a apaga antes do Qt

    # ---------------------------------------------------------------- índice da busca (F5)
    def _edited(self, now: bool = False) -> None:
        """Uma decisão sobre as vozes mudou quem fala na transcrição: a busca reindexa a reunião, e a ata que esperava a
        identificação sai quando não resta voz pendente (U-042)."""
        if self.on_edited is not None:
            self.on_edited(self.meeting_id, "review", now=now)
        self.release_summary("revisão sem voz pendente")

    def release_summary(self, reason: str, force: bool = False) -> int | None:
        """U-042, F5.6 (P-01): os pedidos "quando todos tiverem nome" saem (uma vez); o worker leve é lançado. Falhar aqui
        nunca trava a revisão."""
        try:
            job_id = llm_requests.release(self.store, self.meeting_id, reason, force=force)
        except Exception as e:
            log.warning("reunião %s: a ata adiada não foi liberada (%s): %s", self.meeting_id, reason, e)
            return None
        if job_id is not None:
            self.header.setText("Vozes resolvidas: a ata está sendo gerada pela fila.")
            if self.on_launch is not None:
                self.on_launch(True)
        return job_id

    def _double_clicked(self, _item: QListWidgetItem) -> None:
        self.play_current()

    def card_play(self, row: int) -> None:
        """U-099: o "Ouvir 5 s" pintado no cartão (só o selecionado o mostra)."""
        if row == self.list.currentRow():
            self.play_current()

    def _row_changed(self, _row: int) -> None:
        self.apply_actions()

    def _text_edited(self) -> None:
        if self.on_edited is not None:
            self.on_edited(self.meeting_id, "edit")

    # ---------------------------------------------------------------- abas da LLM (F3 item 11)
    def reload_llm(self) -> None:
        busy = self.llm.reload()
        self.act_identify.setEnabled(not busy)
        self.act_identify.setToolTip("há um job de LLM em andamento para esta reunião" if busy
                                     else "a LLM sugere de novo quem é cada voz pendente, pelo contexto da conversa")

    @override
    def regenerate(self, step: JobStep) -> int | None:
        """"Gerar de novo": job `llm` de uma tarefa pela fila (P6); a versão anterior vira `superada` quando a nova chegar."""
        return self.llm.regenerate(step)

    @property
    def regenerated(self) -> list[int]:
        """Ids dos jobs `llm` criados por "gerar de novo" (instrumentação dos testes)."""
        return self.llm.regenerated

    # ---------------------------------------------------------------- estado
    @override
    def reload(self, keep: int | None = None, focus: bool = False) -> None:
        """Relê os cards; `focus` só nas ações do próprio diálogo (o polling do app não rouba o foco, R2-26)."""
        self.reloads += 1
        current = keep if keep is not None else self.current_cluster_id()
        self.items = review.review_items(self.store, self.meeting_id)
        self.names = voice_names(self.store, self.meeting_id)
        self.hints = self._learn_hints()
        taken = taken_voices(self.items)                   # U-137: "(já na Voz N)"
        self.list.clear()
        for it in self.items:
            self.list.addItem(card_item(it, self.names, taken.get(it.cluster_id)))   # U-086: o cartão com o estado em selo
        waiting = review.pending(self.items)
        stage_primary(self.btn_confirm if waiting else self.btn_finish, (self.btn_confirm, self.btn_finish))
        self.header.setText("Tudo confirmado: nenhuma voz pendente." if not waiting else
                            f"{plural(len(waiting), 'voz', 'vozes')} para confirmar. Setas trocam de voz; Espaço ouve 5 s.")
        self.btn_transcript.setEnabled(self.store.segments.count_segments(self.meeting_id) > 0)
        self.reload_llm()
        target = current if current is not None else (waiting[0].cluster_id if waiting else None)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == target:
                self.list.setCurrentRow(i)
                break
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.apply_actions()
        if focus:
            self.list.setFocus()

    def _learn_hints(self) -> dict[int, LearnHint]:
        """U-138, só para as pessoas dos cards; uma falha não trava a revisão: a linha fica a de antes."""
        try:
            return learn_hints(self.store, self.meeting_id, self.items)
        except Exception as e:
            log.warning("reunião %s: a previsão das referências de voz falhou: %s", self.meeting_id, e)
            return {}

    @override
    def current_cluster_id(self) -> int | None:
        item = self.list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    @override
    def current_item(self) -> review.ReviewItem | None:
        cid = self.current_cluster_id()
        return next((i for i in self.items if i.cluster_id == cid), None)

    @override
    def _next_pending(self) -> None:
        waiting = review.pending(review.review_items(self.store, self.meeting_id))
        self.reload(waiting[0].cluster_id if waiting else self.current_cluster_id(), focus=True)

    def _on_player(self, st: object) -> None:
        """Sem dispositivo de saída ou sem o arquivo de áudio: o motivo no cabeçalho, como antes."""
        if isinstance(st, PlayerState) and st.state == ERROR and st.meeting_id in (None, self.meeting_id) and st.message:
            self.header.setText(f"Reprodução indisponível: {st.message}")

    def _open_transcript(self) -> None:
        if self.on_transcript is not None:
            self.on_transcript(self.meeting_id)

    def finish(self) -> None:
        review.finish(self.store, self.meeting_id)
        self._edited(now=True)                             # "Concluir": a reindexação não espera os 30 s
        self.release_summary("concluir", force=True)       # U-042, P-01: concluir decide; o pedido sai mesmo com voz sem nome
        self.accept()

    # ---------------------------------------------------------------- teclado (U-006)
    def handle_key(self, key: int) -> bool:
        """True quando a tecla é um atalho da revisão (setas ficam com a lista). U-098: o Enter faz o que o principal diz;
        U-100 (A-21): M, I e E, do "Mais", seguem aqui."""
        actions: dict[int, Callable[[], object]] = {           # `Qt.Key` é `IntEnum`: o código da tecla acha o item
            Qt.Key.Key_Return: self.press_primary, Qt.Key.Key_Enter: self.press_primary, Qt.Key.Key_L: self.accept_llm_current,
            Qt.Key.Key_O: self.ask_other_person, Qt.Key.Key_N: self.ask_new_person, Qt.Key.Key_E: self.edit_current,
            Qt.Key.Key_M: self.ask_merge, Qt.Key.Key_I: self.ignore_current, Qt.Key.Key_Space: self.play_current,
            Qt.Key.Key_Escape: self.reject}
        action = actions.get(key)
        if action is None:
            return False
        action()
        return True

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Revisão da F5.4 (R-01): o Enter num botão com foco aciona esse botão (os botões da revisão não são `autoDefault`,
        para nenhum virar o padrão sozinho); na aba Vozes, as teclas da revisão (U-098: o Enter faz o que o principal diz);
        nas abas de texto as letras são texto e o Enter fora de um botão não aciona nada (um botão das abas que já teve o
        foco fica sendo o padrão no Qt: o Enter num campo não gera ata nem fecha a revisão)."""
        enter = event.key() in ENTER_KEYS
        if enter and click_focused_button(self):
            return
        if self.tabs.currentIndex() == 0 and self.handle_key(event.key()):
            return
        if enter:
            event.accept()
            return
        super().keyPressEvent(event)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Enter, Espaço e as letras com o foco na lista. Lê a lista pelo `__dict__`: a coleta de ciclos pode limpar os
        atributos da janela sem pai antes de o Qt apagá-la, e um filtro que falha ali derrubava o processo (F5.4 L4)."""
        if (watched is self.__dict__.get("list") and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent)
                and self.handle_key(event.key())):
            return True
        return super().eventFilter(watched, event)
