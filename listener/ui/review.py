"""Revisão de falantes (F2 item 13, U-006; F3 item 5): um card por voz com a sugestão do pyannote e a da LLM (origem,
confiança, citação com minuto); Enter aceita o pyannote (motor principal, CP5/CP11), `L` aceita a LLM, setas navegam, O =
outra pessoa, N = nova pessoa, M = mesclar, I = ignorar, Espaço ouve 5 s (numa `Task`). F3 item 11: abas de título,
pré-resumo, ata, executivo e ações editáveis com "gerar de novo" por aba (job `llm`), selo de custo no cabeçalho. "Concluir"
marca a reunião como revisada. Só metadados no construtor (R-9). F5: cada decisão sobre uma voz e cada edição das abas
chama `on_edited(meeting_id, motivo, now=...)` (o controlador reindexa a reunião 30 s depois da última; "Concluir" na
hora). F5 L4: as abas da LLM vêm do `LlmTabsGroup` (o mesmo da reunião aberta, F5A-26) e os 5 s passam pelo player único
do app (um som por vez; o aviso de reprodução aparece no cabeçalho).
U-041 (F5.2): N abre a ficha (nome, cargo, empresa, e-mail; nome repetido pergunta "é ela?"); O lista "Nome — Cargo ·
Empresa" pelo id; E edita a ficha da pessoa da voz confirmada e tem "Apagar pessoa…" (LGPD; confirmação, CP3)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ..log import get_logger
from ..model import ClusterStatus, JobStep, SummaryKind
from ..pipeline import review
from .llm_tabs import ActionsTab, LlmTabsGroup, TextTab, TitleTab
from .person import DELETE, PersonData, PersonDialog
from .player import ERROR, PlayerState

if TYPE_CHECKING:
    from ..store import Store
    from .player import Player

log = get_logger("ui.review")
DELETE_TEXT = ("Apagar {name}?\n\nSaem a ficha, o perfil de voz e o vínculo com as falas, as legendas e as ações em todas as "
               "reuniões; as vozes dela voltam a \"sem nome\". O áudio e as falas ficam.\n\nAtas, resumos, respostas do chat e o "
               "histórico das chamadas à LLM já gerados não mudam: use \"Gerar de novo\" para refazê-los sem o nome.")
EDITABLE = (ClusterStatus.CONFIRMED, ClusterStatus.AUTO)         # U-041 (A-12): só voz com pessoa decidida tem ficha a editar


def person_data(row: Any) -> PersonData:
    return PersonData(str(row["name"]), str(row.get("role") or ""), str(row.get("company") or ""), str(row.get("email") or ""))


def person_labels(people: list[Any]) -> list[str]:
    """"Nome — Cargo · Empresa" de cada pessoa, na ordem; homônimos iguais até no cargo ganham o id (A-11: a escolha vai
    pelo id, nunca pelo nome)."""
    labels = [person_data(p).label() for p in people]
    return [f"{lab} (#{p['id']})" if labels.count(lab) > 1 else lab for lab, p in zip(labels, people, strict=True)]


class ReviewDialog(QDialog):
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
        self.btn_confirm = QPushButton("Confirmar pyannote (Enter)")
        self.btn_llm = QPushButton("Aceitar LLM (L)")
        self.btn_other = QPushButton("Outra pessoa (O)")
        self.btn_new = QPushButton("Nova pessoa (N)")
        self.btn_merge = QPushButton("É a mesma que… (M)")
        self.btn_ignore = QPushButton("Ignorar (I)")
        self.btn_play = QPushButton("Ouvir 5 s (Espaço)")
        self.btn_edit = QPushButton("Editar pessoa (E)")                    # U-041: a ficha da pessoa desta voz
        self.btn_edit.clicked.connect(self.edit_current)
        self.btn_transcript = QPushButton("Ver transcrição")
        self.btn_finish = QPushButton("Concluir")
        self.btn_identify = QPushButton("Identidade por contexto de novo")     # F3.1 R3-05: `identify` sob demanda
        self.btn_identify.clicked.connect(lambda: self.regenerate(JobStep.IDENTIFY))
        self.btn_confirm.clicked.connect(self._confirm_clicked)
        self.btn_llm.clicked.connect(self.accept_llm_current)
        self.btn_other.clicked.connect(self.ask_other_person)
        self.btn_new.clicked.connect(self.ask_new_person)
        self.btn_merge.clicked.connect(self.ask_merge)
        self.btn_ignore.clicked.connect(self.ignore_current)
        self.btn_play.clicked.connect(self.play_current)
        self.btn_transcript.clicked.connect(self._open_transcript)
        self.btn_finish.clicked.connect(self.finish)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for b in (self.btn_confirm, self.btn_llm, self.btn_other, self.btn_new, self.btn_merge, self.btn_ignore):
            row1.addWidget(b)
        row2.addWidget(self.btn_play)
        row2.addWidget(self.btn_edit)
        row2.addWidget(self.btn_identify)
        row2.addStretch(1)
        row2.addWidget(self.btn_transcript)
        row2.addWidget(self.btn_finish)
        voices = QWidget()
        vlay = QVBoxLayout(voices)
        vlay.addWidget(self.list, 1)
        vlay.addLayout(row1)
        vlay.addLayout(row2)
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
        self.list.itemDoubleClicked.connect(lambda _item: self.play_current())
        self.list.currentRowChanged.connect(lambda _row: self._update_edit_button())
        if player is not None:
            player.state_changed.connect(self._on_player)
        self.reload(focus=True)

    # ---------------------------------------------------------------- índice da busca (F5)
    def _edited(self, now: bool = False) -> None:
        """Uma decisão sobre as vozes mudou quem fala na transcrição: a busca reindexa a reunião."""
        if self.on_edited is not None:
            self.on_edited(self.meeting_id, "review", now=now)

    def _text_edited(self) -> None:
        if self.on_edited is not None:
            self.on_edited(self.meeting_id, "edit")

    # ---------------------------------------------------------------- abas da LLM (F3 item 11)
    def reload_llm(self) -> None:
        busy = self.llm.reload()
        self.btn_identify.setEnabled(not busy)
        self.btn_identify.setToolTip("há um job de LLM em andamento para esta reunião" if busy
                                     else "a LLM sugere de novo quem é cada voz pendente, pelo contexto da conversa")

    def regenerate(self, step: JobStep) -> int | None:
        """"Gerar de novo": job `llm` de uma tarefa pela fila (P6); a versão anterior vira `superada` quando a nova chegar."""
        return self.llm.regenerate(step)

    @property
    def regenerated(self) -> list[int]:
        """Ids dos jobs `llm` criados por "gerar de novo" (instrumentação dos testes)."""
        return self.llm.regenerated

    # ---------------------------------------------------------------- estado
    def reload(self, keep: int | None = None, focus: bool = False) -> None:
        """Relê os cards; `focus` só nas ações do próprio diálogo (o polling do app não rouba o foco, R2-26)."""
        self.reloads += 1
        current = keep if keep is not None else self.current_cluster_id()
        self.items = review.review_items(self.store, self.meeting_id)
        self.list.clear()
        for it in self.items:
            item = QListWidgetItem(f"{it.title} · {it.minutes_text} · {it.summary()}")
            item.setData(Qt.ItemDataRole.UserRole, it.cluster_id)
            if it.pending and it.merged_into is None:
                item.setForeground(Qt.GlobalColor.darkRed)
            self.list.addItem(item)
        waiting = review.pending(self.items)
        self.header.setText("Tudo confirmado: nenhuma voz pendente." if not waiting else
                            f"{len(waiting)} voz(es) para confirmar. Enter aceita o pyannote, L a LLM; setas navegam.")
        self.btn_transcript.setEnabled(self.store.segments.count_segments(self.meeting_id) > 0)
        self.reload_llm()
        target = current if current is not None else (waiting[0].cluster_id if waiting else None)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == target:
                self.list.setCurrentRow(i)
                break
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self._update_edit_button()
        if focus:
            self.list.setFocus()

    def current_cluster_id(self) -> int | None:
        item = self.list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def current_item(self) -> review.ReviewItem | None:
        cid = self.current_cluster_id()
        return next((i for i in self.items if i.cluster_id == cid), None)

    def _next_pending(self) -> None:
        waiting = review.pending(review.review_items(self.store, self.meeting_id))
        self.reload(waiting[0].cluster_id if waiting else self.current_cluster_id(), focus=True)

    # ---------------------------------------------------------------- ações
    def _confirm_clicked(self) -> None:
        self.confirm_current()

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
            QMessageBox.warning(self, "Revisão", str(e))
            return False
        self._edited()
        self._next_pending()
        return True

    def accept_llm_current(self) -> bool:
        """Tecla `L`: aceita a sugestão da LLM (pessoa cadastrada ou nome novo); `decision_source = usuario_llm`."""
        it = self.current_item()
        if it is None:
            return False
        if not it.has_llm:
            self.header.setText("Esta voz não tem sugestão da LLM.")
            return False
        try:
            review.accept_llm(self.store, it.cluster_id)
        except ValueError as e:
            QMessageBox.warning(self, "Revisão", str(e))
            return False
        self._edited()
        self._next_pending()
        return True

    def ask_other_person(self) -> None:
        """"Outra pessoa": a lista mostra "Nome — Cargo · Empresa" e a escolha vai pelo id (homônimos, A-11)."""
        people = self.store.people.list_people()
        if not people:
            self.ask_new_person()
            return
        labels = person_labels(people)
        choice, ok = QInputDialog.getItem(self, "Outra pessoa", "Quem é esta voz?", labels, 0, False)
        if ok and choice in labels:
            self.confirm_current(int(people[labels.index(choice)]["id"]))

    def ask_new_person(self) -> None:
        """U-041: a ficha da pessoa (nome obrigatório; cargo, empresa e e-mail opcionais)."""
        if self.current_item() is None:
            return
        dlg = PersonDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.data()
            self.create_person(data.name, data.role, data.company, data.email)

    def create_person(self, name: str, role: str = "", company: str = "", email: str = "") -> bool:
        """Confirma a voz com a pessoa da ficha. Nome que já existe pergunta "é ela?" (pergunta 2 da F5.2): sim confirma a
        existente; não cria outra pessoa com o mesmo nome; cancelar não faz nada."""
        it = self.current_item()
        if it is None:
            return False
        for twin in self.store.people.find_all_by_name(name):
            answer = QMessageBox.question(self, "Nova pessoa", f"Já existe {person_data(twin).label()}. É ela?",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                          | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
            if answer == QMessageBox.StandardButton.Yes:
                log.info("revisão da reunião %s: o nome da ficha já existia; o usuário escolheu a pessoa %s",
                         self.meeting_id, twin["id"])
                return self.confirm_current(int(twin["id"]))
            if answer != QMessageBox.StandardButton.No:
                return False
        try:
            review.create_person(self.store, it.cluster_id, name, role, company, email)
        except ValueError as e:
            QMessageBox.warning(self, "Revisão", str(e))
            return False
        self._edited()
        self._next_pending()
        return True

    # ---------------------------------------------------------------- ficha (U-041)
    def editable_person(self) -> int | None:
        """A pessoa da voz atual quando a voz já tem pessoa decidida (confirmada ou automática, A-12); senão None."""
        it = self.current_item()
        if it is None or it.person_id is None or it.status not in EDITABLE:
            return None
        return int(it.person_id)

    def _update_edit_button(self) -> None:
        pid = self.editable_person()
        self.btn_edit.setEnabled(pid is not None)
        self.btn_edit.setToolTip("editar a ficha (nome, cargo, empresa, e-mail) ou apagar a pessoa" if pid is not None
                                 else "confirme a voz antes: só voz com pessoa decidida tem ficha a editar")

    def edit_current(self) -> None:
        """E: a ficha da pessoa da voz atual; salvar muda a ficha (e o cargo do dia nesta reunião), apagar pede confirmação."""
        pid = self.editable_person()
        if pid is None:
            self.header.setText("Esta voz ainda não tem pessoa confirmada: confirme antes de editar a ficha.")
            return
        person = self.store.people.get_person(pid)
        if person is None:
            return
        dlg = PersonDialog(self, data=person_data(person), can_delete=not person.get("is_me"))
        code = dlg.exec()
        if code == DELETE:
            self.delete_person(pid, str(person["name"]))
        elif code == QDialog.DialogCode.Accepted:
            self.save_person(pid, dlg.data())

    def save_person(self, person_id: int, data: PersonData) -> bool:
        try:
            meetings = review.edit_person(self.store, person_id, self.meeting_id, data.name, data.role, data.company, data.email)
        except ValueError as e:
            QMessageBox.warning(self, "Revisão", str(e))
            return False
        self._reindex(meetings)
        self.reload(self.current_cluster_id(), focus=True)
        return True

    def delete_person(self, person_id: int, name: str) -> bool:
        answer = QMessageBox.question(self, "Apagar pessoa", DELETE_TEXT.format(name=name),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            log.info("revisão da reunião %s: apagar a pessoa %s cancelado", self.meeting_id, person_id)
            return False
        try:
            meetings = review.forget_person(self.store, person_id)
        except ValueError as e:
            QMessageBox.warning(self, "Revisão", str(e))
            return False
        self._reindex(meetings)
        self._next_pending()
        return True

    def _reindex(self, meetings: list[int]) -> None:
        """O falante entra no texto dos trechos da busca: as reuniões da pessoa reindexam (esta, pelo caminho de sempre)."""
        for mid in meetings:
            if mid == self.meeting_id:
                self._edited()
            elif self.on_edited is not None:
                self.on_edited(mid, "review", now=True)

    def ask_merge(self) -> None:
        it = self.current_item()
        others = [o for o in self.items if o.cluster_id != (it.cluster_id if it else None) and o.merged_into is None]
        if it is None or not others:
            return
        labels = [f"{o.title} · {o.summary()}" for o in others]
        choice, ok = QInputDialog.getItem(self, "É a mesma pessoa que…", "Voz:", labels, 0, False)
        if ok and choice in labels:
            self.merge_into(others[labels.index(choice)].cluster_id)

    def merge_into(self, cluster_id: int) -> bool:
        it = self.current_item()
        if it is None:
            return False
        try:
            review.merge(self.store, it.cluster_id, cluster_id)
        except ValueError as e:
            QMessageBox.warning(self, "Revisão", str(e))
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
        self.player.play_from(self.meeting_id, t0, end_ms=t1, channel=channel, title=f"{it.title} (5 s)")

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
        self.accept()

    # ---------------------------------------------------------------- teclado (U-006)
    def handle_key(self, key: int) -> bool:
        """True quando a tecla é um atalho da revisão (setas ficam com a lista)."""
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm_current()
        elif key == Qt.Key.Key_L:
            self.accept_llm_current()
        elif key == Qt.Key.Key_O:
            self.ask_other_person()
        elif key == Qt.Key.Key_N:
            self.ask_new_person()
        elif key == Qt.Key.Key_E:
            self.edit_current()
        elif key == Qt.Key.Key_M:
            self.ask_merge()
        elif key == Qt.Key.Key_I:
            self.ignore_current()
        elif key == Qt.Key.Key_Space:
            self.play_current()
        elif key == Qt.Key.Key_Escape:
            self.reject()
        else:
            return False
        return True

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.tabs.currentIndex() != 0 or not self.handle_key(event.key()):   # nas abas de texto as letras são texto
            super().keyPressEvent(event)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.list and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent) and self.handle_key(event.key()):
            return True
        return super().eventFilter(watched, event)
