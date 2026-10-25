"""Revisão e leitura da F3 (L5): recarga por assinatura (sugestões da LLM, resumos, título) numa só consulta; cards com as
duas sugestões, Enter (pyannote) e L (LLM); abas de título, pré-resumo, ata, executivo e ações editáveis com `revisions` e
"gerar de novo" como job (desabilitado com job `llm` ativo); marca de reprocessamento; selo de custo; alternância
revisada/original na leitura; "LLM: ata (2 de 4)" e custo na fila; notificação por kind."""
from __future__ import annotations

import json

import pytest

from listener.model import ActionStatus, DecisionSource, JobKind, JobStatus, SummaryKind
from listener.pipeline import review
from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm

pytest.importorskip("PySide6")


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _ready(platform, store) -> tuple[int, int]:
    store.upsert_me("Eu", role="Gerente")
    store.people.create_person("Ana", role="CFO")
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE
    return mid, parent


def test_controller_signature_reloads_review_when_llm_data_changes(qtbot, qapp, platform, store):
    from listener.store.summaries import SummaryInsert
    from listener.ui.controller import Controller

    store.upsert_me("Eu")
    mid = _record(platform, store)
    _pipeline(store, mid)
    c = Controller(qapp, platform, store)
    c.tray = None
    try:
        dlg = c.open_review(mid)
        c._poll_queue()                                                                               # o job concluído é "visto" aqui
        base = dlg.reloads
        c._poll_queue()
        assert dlg.reloads == base                                                                    # nada mudou: sem reload
        cid = review.review_items(store, mid)[0].cluster_id
        store.segments.set_llm_suggestion(cid, None, "Rodrigo", 0.7, "[]")
        c._poll_queue()
        assert dlg.reloads == base + 1 and "LLM: Rodrigo" in dlg.list.item(0).text()                  # sugestão da LLM recarrega
        store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "# Ata", "gemini", "m", "minutes/1.0", 1, None, "pt-BR"))
        c._poll_queue()
        assert dlg.reloads == base + 2 and dlg.text_tabs["minutes"].editor.toPlainText() == "# Ata"
        store.meetings.set_title_llm(mid, "Título da LLM")
        c._poll_queue()
        assert dlg.reloads == base + 3 and "Título da LLM" in dlg.title_tab.suggestion.text()
        c._poll_queue()
        assert dlg.reloads == base + 3
        dlg.reject()
        qtbot.waitUntil(lambda: mid not in c._reviews, timeout=2000)
    finally:
        c.emergency_stop()


def test_review_dialog_l_key_tabs_edits_regenerate_and_cost(qtbot, platform, store):
    from PySide6.QtCore import Qt

    from listener.ui.review import ReviewDialog

    mid, parent = _ready(platform, store)
    launched: list[bool] = []
    dlg = ReviewDialog(store, mid, on_launch=launched.append)
    qtbot.addWidget(dlg)
    dlg.show()
    assert "custo da LLM: US$" in dlg.cost.text() and dlg.tabs.count() == 6 and "L a LLM" in dlg.header.text()
    llm_item = next(i for i in dlg.items if i.has_llm)
    other = next(i for i in dlg.items if not i.has_llm)
    assert "LLM: Ana (0,80)" in dlg.list.item([i.cluster_id for i in dlg.items].index(llm_item.cluster_id)).text()
    dlg.list.setCurrentRow([i.cluster_id for i in dlg.items].index(other.cluster_id))
    assert not dlg.accept_llm_current() and "não tem sugestão" in dlg.header.text()
    dlg.list.setCurrentRow([i.cluster_id for i in dlg.items].index(llm_item.cluster_id))
    qtbot.keyClick(dlg.list, Qt.Key.Key_L)                                                            # L aceita a LLM
    c = store.segments.get_cluster(llm_item.cluster_id)
    assert c["status"] == "confirmado" and c["decision_source"] == DecisionSource.USER_LLM and c["person_id"] == llm_item.llm_person_id
    # abas: ata editável com revisions; título usa a sugestão; ações mudam de estado
    minutes_tab = dlg.text_tabs["minutes"]
    assert minutes_tab.editor.toPlainText().startswith("## Contexto") and "gemini" in minutes_tab.info.text()
    minutes_tab.editor.setPlainText(minutes_tab.editor.toPlainText() + "\n\nNota minha.")
    assert minutes_tab.save() and "editado por você" in minutes_tab.info.text()
    row = store.summaries.current(mid, SummaryKind.MINUTES)
    assert row["text_edited"].endswith("Nota minha.") and row["text_llm"].startswith("## Contexto")
    assert store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'summary' AND field = 'text_edited'")[0][0] == 1
    minutes_tab.editor.setPlainText(row["text_llm"])
    assert minutes_tab.save() and store.summaries.current(mid, SummaryKind.MINUTES)["text_edited"] is None   # igual ao original: limpa
    dlg.title_tab.use_suggestion()
    assert store.get_meeting(mid)["title"] == store.get_meeting(mid)["title_llm"]
    actions = store.actions.list_for_meeting(mid)
    assert dlg.actions_tab.table.rowCount() == len(actions) == 2
    from PySide6.QtWidgets import QComboBox

    combo = dlg.actions_tab.table.cellWidget(0, 3)
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("feita"))
    first_id = dlg.actions_tab.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    assert store.actions.get(first_id)["status"] == ActionStatus.DONE
    dlg.actions_tab.table.item(0, 0).setText("Texto editado da ação")
    assert store.actions.get(first_id)["text"] == "Texto editado da ação"
    assert store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'action_item'")[0][0] == 2
    # gerar de novo: job llm de uma tarefa; enquanto ativo, os botões ficam desabilitados
    assert minutes_tab.btn_regen.isEnabled()
    from listener.model import JobStep

    job_id = dlg.regenerate(JobStep.MINUTES)
    job = store.jobs.get(job_id)
    assert job["kind"] == JobKind.LLM and json.loads(job["params_json"])["tasks"] == ["minutes"] and launched == [True]
    assert json.loads(job["params_json"])["parent_job_id"] == parent and dlg.regenerated == [job_id]
    assert not minutes_tab.btn_regen.isEnabled() and not dlg.title_tab.btn_regen.isEnabled() and "LLM em andamento" in dlg.cost.text()
    assert dlg.regenerate(JobStep.TITLE) is None and "em andamento" in dlg.header.text()                      # já há job ativo
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE
    dlg.reload()
    assert minutes_tab.btn_regen.isEnabled() and store.summaries.current(mid, SummaryKind.MINUTES)["id"] != row["id"]
    assert store._q("SELECT COUNT(*) FROM summaries WHERE kind = 'minutes' AND status = 'superada'")[0][0] == 1
    assert {a["status"] for a in store.actions.list_for_meeting(mid, include_superseded=True)} >= {"superada"}
    # reprocessamento: marca "gerados antes do reprocessamento" nas abas; executivo pulado pelo alerta mostra o motivo
    _pipeline(store, mid, JobKind.REPROCESS)
    dlg.reload()
    assert "gerados antes do reprocessamento" in minutes_tab.notice.text()
    store.set_setting("llm.cost_alert_usd", 0.0000001)
    store.summaries.core.execute("UPDATE summaries SET status = 'superada' WHERE kind = 'executive'")
    from listener.pipeline import llm_steps

    llm_job = next(j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == "pendente")
    store.jobs.request_cancel(int(llm_job["id"]))
    llm_steps.enqueue_llm_job(store, mid, ["executive", "title"], auto=True)
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE
    dlg.reload()
    assert "não gerado" in dlg.text_tabs["executive"].notice.text() and "acima do alerta" in dlg.text_tabs["executive"].notice.text()
    dlg.reject()


def test_transcript_dialog_toggles_revised_and_original(qtbot, platform, store):
    from listener.ui.transcript import TranscriptDialog

    mid, _parent = _ready(platform, store)
    dlg = TranscriptDialog(store, mid)
    qtbot.addWidget(dlg)
    first = dlg.model.data(dlg.model.index(0), 0)
    assert first.endswith("Bom dia Ana tudo bem.") and "revisada(s) pela LLM; mostrando a revisada" in dlg.status.text()
    dlg.toggle.setChecked(False)
    assert dlg.model.data(dlg.model.index(0), 0).endswith(": bom dia Ana tudo bem") and "mostrando a original" in dlg.status.text()
    rows = review.transcript_rows(store, mid, prefer_llm=False)
    assert rows[0].text == "bom dia Ana tudo bem" and rows[0].text_llm == "Bom dia Ana tudo bem." and rows[0].shown_text == rows[0].text
    store._x("UPDATE segments SET text_llm = NULL WHERE meeting_id = ?", (mid,))
    dlg.toggle.setChecked(True)
    assert not dlg.toggle.isEnabled() and "0 revisada(s)" in dlg.status.text()


def test_queue_texts_and_notification_for_llm_jobs(store):
    from listener.ui.llm_tabs import llm_done_text
    from listener.ui.queue import job_row_texts

    mid = store.create_meeting("Diretoria", "teams", None, None)
    store.finish_meeting(mid, 1_800_000)
    jid = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["identify", "minutes", "executive", "title"], "parent_job_id": 1})
    store.jobs.claim(pid=1, kinds=("llm",))
    store.jobs.set_progress(jid, "minutes", 0.25, {"llm": {"cost_usd": 0.1234, "done": ["identify"], "calls": 1, "skipped": {}}})
    texts = job_row_texts(store.jobs.get(jid), store.get_meeting(mid), "{}")
    assert list(texts[1:4]) == ["LLM", "rodando", "LLM: ata (2 de 4)"] and texts[5] == "sem estimativa" and texts[6] == "custo US$ 0,12"
    store.jobs.finish(jid, JobStatus.DONE, timings={"llm": {"cost_usd": 0.32, "done": ["identify", "minutes", "executive", "title"]}})
    job = store.jobs.get(jid)
    assert job_row_texts(job, None, "{}")[3] == "LLM: ata (4 de 4)" and llm_done_text(dict(job)) == "ata pronta · US$ 0,32"
    assert llm_done_text({"timings_json": json.dumps({"llm": {"done": ["title"], "cost_usd": 0}})}) == "LLM concluída."
    assert llm_done_text({"timings_json": None}) == "LLM sem tarefas novas."
