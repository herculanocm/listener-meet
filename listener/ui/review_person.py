"""A ficha da pessoa na revisão de falantes (U-041, F5.2), fora do `ui/review.py`, que passava de 400 linhas (F5.4 L4): "Outra
pessoa" (O, a escolha pelo id; F5.25: a janela com busca, `ui/person_picker.py`), "Nova pessoa" (N, a ficha; nome repetido
pergunta "é ela?"), "Editar pessoa" (E, só voz com pessoa decidida; item do "Mais" desde a F5.11, U-100) e "Apagar
pessoa…" (LGPD; confirmação, CP3). A base do mixin das ações (`ui/review_actions.py`), que o `ReviewDialog` herda: usa o
estado e os métodos dele."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QWidget

from ..config.defaults import SPECS
from ..log import get_logger
from ..model import ClusterStatus
from ..pipeline import review, review_suggest
from ..pipeline import review_person as sheet
from ..pipeline.profiles import expected_person_ids
from . import person_picker
from .person import DELETE, PersonData, PersonDialog, person_data, person_labels
from .person_picker import Candidate, PersonIndex, PersonPicker, top_by_voice
from .review_cards import decided_people

if TYPE_CHECKING:
    from ..pipeline.voices import VoiceNames
    from ..store import Store
    from ..store.people import PersonRow

log = get_logger("ui.review")

DELETE_TEXT = ("Apagar {name}?\n\nSaem a ficha, o perfil de voz e o vínculo com as falas, as legendas e as ações em todas as "
               "reuniões; as vozes dela voltam a \"sem nome\". O áudio e as falas ficam.\n\nAtas, resumos, respostas do chat e o "
               "histórico das chamadas à IA já gerados não mudam: use \"Gerar de novo\" para refazê-los sem o nome.")
EDITABLE = (ClusterStatus.CONFIRMED, ClusterStatus.AUTO)         # U-041 (A-12): só voz com pessoa decidida tem ficha a editar


class PersonActionsMixin:
    """As ações da ficha do `ReviewDialog` (o diálogo é o pai das caixas de pergunta)."""
    store: Store
    meeting_id: int
    items: list[review.ReviewItem]
    names: VoiceNames
    header: QLabel
    act_edit: QAction
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

    def focus_voices(self) -> None:
        raise NotImplementedError

    def _parent(self) -> QWidget | None:
        return self if isinstance(self, QWidget) else None

    def ask_other_person(self) -> None:
        """"Outra pessoa" (e o "Escolher pessoa…"): a janela com busca (F5.25). A escolha vai pelo id da linha, nunca pelo
        texto (homônimos, A-11); "Nova pessoa" abre a ficha com o nome digitado; sem escolha, o foco volta à lista."""
        people = self.store.people.list_people()
        if not people:
            self.ask_new_person()
            return
        it = self.current_item()
        if it is None:
            return
        picker = PersonPicker(self._parent(), self._person_index(it, people),
                              question=f"Quem é a {self.names.title(it.cluster_id, it.title)}?",
                              threshold=float(self.store.get(SPECS["pipeline.threshold"])))
        try:
            pick = person_picker.run(picker)
        finally:
            picker.deleteLater()
        if pick.person_id is not None:
            self.confirm_current(pick.person_id)
        elif pick.new_name is not None:
            self._new_person_named(pick.new_name)
        else:
            self.focus_voices()

    def _person_index(self, it: review.ReviewItem, people: list[PersonRow]) -> PersonIndex:
        """Tudo o que a janela mostra, lido uma vez antes de ela abrir (Comportamento 1): a nota pela voz de cada pessoa
        (U-173), "Pela voz" (U-136), as esperadas da reunião e "já na Voz N" (U-174, das vozes da tela, como o U-137)."""
        t0 = time.perf_counter()
        ranked = self._voice_ranking(it)
        scores = dict(ranked)
        expected = expected_person_ids((self.store.get_meeting(self.meeting_id) or {}).get("expected_people_json"))
        taken = {pid: self.names.title(cid, f"voz #{cid}") for pid, cid in decided_people(self.items).items() if cid != it.cluster_id}
        candidates = []
        for p, label in zip(people, person_labels(people), strict=True):
            pid = int(p["id"])
            candidates.append(Candidate(pid, label, str(p["name"]), str(p.get("role") or ""), str(p.get("company") or ""),
                                        scores.get(pid), pid in expected, taken.get(pid, "")))
        index = PersonIndex(candidates, top_by_voice(ranked, it.person_id, set(scores)))
        log.debug("revisão da reunião %s: a escolha da pessoa para a voz %s com %d pessoas (%d com nota, %d pela voz) em %.1f ms",
                  self.meeting_id, it.cluster_id, len(candidates), len(scores), len(index.top), (time.perf_counter() - t0) * 1000)
        return index

    def _voice_ranking(self, it: review.ReviewItem) -> list[tuple[int, float]]:
        """A nota da voz para cada pessoa com perfil, da maior para a menor; vazia na voz sem vetor. Uma falha vira aviso no
        log, sem nome, e a janela abre sem "Pela voz" e sem os números."""
        if not it.has_centroid:
            return []
        try:
            return review_suggest.voice_ranking(self.store, it.cluster_id)
        except Exception as e:
            log.warning("revisão da reunião %s: a ordem pela voz %s falhou: %s", self.meeting_id, it.cluster_id, e)
            return []

    def ask_new_person(self) -> None:
        """U-041: a ficha da pessoa (nome obrigatório; cargo, empresa e e-mail opcionais)."""
        self._new_person_named("")

    def _new_person_named(self, name: str) -> None:
        """A ficha nova, com o nome já preenchido quando vem da janela de busca (U-172); cancelar volta à revisão."""
        if self.current_item() is None:
            return
        dlg = PersonDialog(self._parent(), name=name)
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
            sheet.create_person(self.store, it.cluster_id, name, role, company, email)
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

    def _update_edit_action(self) -> None:
        pid = self.editable_person()
        self.act_edit.setEnabled(pid is not None)
        self.act_edit.setToolTip("editar a ficha (nome, cargo, empresa, e-mail) ou apagar a pessoa" if pid is not None
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
            meetings = sheet.edit_person(self.store, person_id, self.meeting_id, data.name, data.role, data.company, data.email)
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
            meetings = sheet.forget_person(self.store, person_id)
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
