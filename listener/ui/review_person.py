"""A ficha da pessoa na revisão de falantes (U-041, F5.2), fora do `ui/review.py`, que passava de 400 linhas (F5.4 L4): "Outra
pessoa" (O, a escolha pelo id), "Nova pessoa" (N, a ficha; nome repetido pergunta "é ela?"), "Editar pessoa" (E, só voz com
pessoa decidida) e "Apagar pessoa…" (LGPD; confirmação, CP3). Um mixin do `ReviewDialog`: usa o estado e os métodos dele."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QDialog, QInputDialog, QLabel, QMessageBox, QPushButton, QWidget

from ..log import get_logger
from ..model import ClusterStatus
from ..pipeline import review
from .person import DELETE, PersonData, PersonDialog, person_data, person_labels

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.review")

DELETE_TEXT = ("Apagar {name}?\n\nSaem a ficha, o perfil de voz e o vínculo com as falas, as legendas e as ações em todas as "
               "reuniões; as vozes dela voltam a \"sem nome\". O áudio e as falas ficam.\n\nAtas, resumos, respostas do chat e o "
               "histórico das chamadas à LLM já gerados não mudam: use \"Gerar de novo\" para refazê-los sem o nome.")
EDITABLE = (ClusterStatus.CONFIRMED, ClusterStatus.AUTO)         # U-041 (A-12): só voz com pessoa decidida tem ficha a editar


class PersonActionsMixin:
    """As ações da ficha do `ReviewDialog` (o diálogo é o pai das caixas de pergunta)."""
    store: Store
    meeting_id: int
    header: QLabel
    btn_edit: QPushButton
    on_edited: Callable[..., Any] | None

    def current_item(self) -> review.ReviewItem | None:
        raise NotImplementedError

    def current_cluster_id(self) -> int | None:
        raise NotImplementedError

    def confirm_current(self, person_id: int | None = None) -> bool:
        raise NotImplementedError

    def reload(self, keep: int | None = None, focus: bool = False) -> None:
        raise NotImplementedError

    def _edited(self, now: bool = False) -> None:
        raise NotImplementedError

    def _next_pending(self) -> None:
        raise NotImplementedError

    def _parent(self) -> QWidget | None:
        return self if isinstance(self, QWidget) else None

    def ask_other_person(self) -> None:
        """"Outra pessoa": a lista mostra "Nome — Cargo · Empresa" e a escolha vai pelo id (homônimos, A-11)."""
        people = self.store.people.list_people()
        if not people:
            self.ask_new_person()
            return
        labels = person_labels(people)
        choice, ok = QInputDialog.getItem(self._parent(), "Outra pessoa", "Quem é esta voz?", labels, 0, False)
        if ok and choice in labels:
            self.confirm_current(int(people[labels.index(choice)]["id"]))

    def ask_new_person(self) -> None:
        """U-041: a ficha da pessoa (nome obrigatório; cargo, empresa e e-mail opcionais)."""
        if self.current_item() is None:
            return
        dlg = PersonDialog(self._parent())
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
            answer = QMessageBox.question(self._parent(), "Nova pessoa", f"Já existe {person_data(twin).label()}. É ela?",
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
            QMessageBox.warning(self._parent(), "Revisão", str(e))
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
        dlg = PersonDialog(self._parent(), data=person_data(person), can_delete=not person.get("is_me"))
        code = dlg.exec()
        if code == DELETE:
            self.delete_person(pid, str(person["name"]))
        elif code == QDialog.DialogCode.Accepted:
            self.save_person(pid, dlg.data())

    def save_person(self, person_id: int, data: PersonData) -> bool:
        try:
            meetings = review.edit_person(self.store, person_id, self.meeting_id, data.name, data.role, data.company, data.email)
        except ValueError as e:
            QMessageBox.warning(self._parent(), "Revisão", str(e))
            return False
        self._reindex(meetings)
        self.reload(self.current_cluster_id(), focus=True)
        return True

    def delete_person(self, person_id: int, name: str) -> bool:
        answer = QMessageBox.question(self._parent(), "Apagar pessoa", DELETE_TEXT.format(name=name),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            log.info("revisão da reunião %s: apagar a pessoa %s cancelado", self.meeting_id, person_id)
            return False
        try:
            meetings = review.forget_person(self.store, person_id)
        except ValueError as e:
            QMessageBox.warning(self._parent(), "Revisão", str(e))
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


__all__ = ["DELETE_TEXT", "EDITABLE", "PersonActionsMixin"]
