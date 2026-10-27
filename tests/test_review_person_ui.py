"""U-041 (F5.2, L2) na revisão: N abre a ficha e salva todos os campos; nome repetido pergunta "é ela?"; O lista "Nome —
Cargo · Empresa" pelo id; E só em voz confirmada, edita a ficha e o cargo do dia, e apaga a pessoa com confirmação."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from listener.model import ClusterStatus
from listener.pipeline import review
from listener.ui import review as review_ui
from listener.ui.person import DELETE, PersonData, PersonDialog
from tests.test_review import _processed

pytest.importorskip("PySide6")


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _dialog(qtbot, store, mid, edited=None):
    dlg = review_ui.ReviewDialog(store, mid, on_edited=(lambda *a, **k: edited.append(a)) if edited is not None else None)
    qtbot.addWidget(dlg)
    return dlg


def _fill(monkeypatch, data: PersonData, code: int = QDialog.DialogCode.Accepted) -> None:
    """A ficha preenchida pelo "usuário": `exec` devolve o código e `data` o que foi digitado."""
    monkeypatch.setattr(PersonDialog, "exec", lambda _self: code)
    monkeypatch.setattr(PersonDialog, "data", lambda _self: data)


def test_new_person_saves_every_field_and_a_repeated_name_asks(qtbot, platform, store, monkeypatch):
    mid, items = _processed(platform, store)
    dlg = _dialog(qtbot, store, mid)
    assert not dlg.btn_edit.isEnabled()                                                   # voz pendente: sem ficha a editar
    _fill(monkeypatch, PersonData("Ana", "CFO", "Empresa X", "ana@x.com"))
    dlg.ask_new_person()
    ana = store.people.find_by_name("Ana")
    assert (ana["role"], ana["company"], ana["email"]) == ("CFO", "Empresa X", "ana@x.com")
    assert store.segments.get_cluster(items[0].cluster_id)["person_id"] == ana["id"]
    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **_k: asked.append(a[2]) or QMessageBox.StandardButton.No))
    dlg.list.setCurrentRow([i.cluster_id for i in dlg.items].index(items[1].cluster_id))
    _fill(monkeypatch, PersonData("Ana", "Analista"))                                      # outra Ana: "não" cria a segunda
    dlg.ask_new_person()
    assert asked == ["Já existe Ana — CFO · Empresa X. É ela?"] and len(store.people.find_all_by_name("Ana")) == 2
    other = store.segments.get_cluster(items[1].cluster_id)
    assert other["person_id"] != ana["id"] and store.people.get_person(other["person_id"])["role"] == "Analista"


def test_other_person_lists_role_and_company_and_picks_by_id(qtbot, platform, store, monkeypatch):
    mid, items = _processed(platform, store)
    first = store.people.create_person("Ana", role="CFO")
    second = store.people.create_person("Ana", role="CFO")                               # homônima até no cargo
    dlg = _dialog(qtbot, store, mid)
    shown: list[list[str]] = []

    def pick(_parent, _title, _label, labels, *_a):
        shown.append(list(labels))
        return f"Ana — CFO (#{second})", True

    monkeypatch.setattr(review_ui.QInputDialog, "getItem", staticmethod(pick))
    dlg.ask_other_person()
    assert f"Ana — CFO (#{first})" in shown[0] and f"Ana — CFO (#{second})" in shown[0]
    assert store.segments.get_cluster(items[0].cluster_id)["person_id"] == second


def test_edit_changes_the_record_and_delete_asks_first(qtbot, platform, store, monkeypatch):
    mid, items = _processed(platform, store)
    ana = review.create_person(store, items[0].cluster_id, "Ana", "CFO")
    edited: list[tuple] = []
    dlg = _dialog(qtbot, store, mid, edited)
    dlg.list.setCurrentRow([i.cluster_id for i in dlg.items].index(items[0].cluster_id))
    assert dlg.btn_edit.isEnabled() and dlg.editable_person() == ana
    _fill(monkeypatch, PersonData("Ana", "CEO", "Empresa X", "ana@x.com"))
    dlg.edit_current()
    assert (store.people.get_person(ana)["role"], store.people.get_person(ana)["company"]) == ("CEO", "Empresa X")
    assert {r.role for r in review.transcript_rows(store, mid) if r.person_id == ana} == {"CEO"} and edited
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **_k: QMessageBox.StandardButton.Cancel))
    _fill(monkeypatch, PersonData("Ana"), code=DELETE)
    dlg.edit_current()
    assert store.people.get_person(ana) is not None                                         # cancelou: nada saiu
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **_k: QMessageBox.StandardButton.Yes))
    dlg.list.setCurrentRow([i.cluster_id for i in dlg.items].index(items[0].cluster_id))
    dlg.edit_current()
    assert store.people.get_person(ana) is None
    assert store.segments.get_cluster(items[0].cluster_id)["status"] == ClusterStatus.UNNAMED
