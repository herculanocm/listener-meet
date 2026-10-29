"""U-041 (F5.2, L2) no banco e no contexto da LLM: a ficha cria a pessoa com todos os campos; editar o cargo guarda o cargo
do dia nas outras reuniões; apagar a pessoa (LGPD) tira o vínculo de todas as tabelas numa transação e o id nunca volta;
o vínculo órfão some; a LLM recebe os falantes com cargo e empresa; o e-mail não sai da máquina (pedidos, busca, log)."""
from __future__ import annotations

import json

import pytest

from listener.llm.types import LlmTask
from listener.model import ClusterStatus, JobStatus, SummaryKind
from listener.pipeline import llm_steps, review
from listener.rag.index import sync_text
from listener.store.actions import ActionInsert
from listener.store.captions import CaptionInsert
from listener.store.llm_calls import unpack_blob
from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm

CANARY = "canario.lgpd@exemplo.com.br"


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _voices(store, mid) -> dict[str, dict]:
    return {c["cluster"]: c for c in store.segments.list_clusters(mid, store.segments.current_job_id(mid))}


def test_forget_unlinks_every_table_and_the_id_never_comes_back(platform, store, caplog):
    me = store.upsert_me("Eu", role="Gerente")
    mid = _record(platform, store)
    _pipeline(store, mid)
    voices = _voices(store, mid)
    ana = review.create_person(store, voices["SPK_0"]["id"], "Ana", "CFO", "Empresa X", CANARY)
    bia = store.people.create_person("Bia")
    store.segments.set_llm_suggestion(voices["SPK_1"]["id"], ana, "Ana", 0.7, "[]")
    store.captions.insert(CaptionInsert(mid, "R", 0, 900, "oi", ana, "Ana — CFO", 0.9))
    summary = store._q("SELECT id FROM summaries LIMIT 1")
    store.actions.insert_many([ActionInsert(mid, int(summary[0]["id"]) if summary else 0, "fechar", "Ana", ana, None, None,
                                            None, None)])
    store._x("UPDATE meetings SET expected_people_json = ? WHERE id = ?", (json.dumps([ana, bia]), mid))
    store.add_revision("person", ana, "role", "", "CFO", "usuario")
    assert store._q("SELECT COUNT(*) FROM voice_profiles WHERE person_id = ?", (ana,))[0][0] == 1        # a confirmação criou
    with caplog.at_level("INFO", logger="listener.pipeline.review"):
        assert review.forget_person(store, ana) == [mid]
    assert store.people.get_person(ana) is None and "Ana" not in caplog.text and CANARY not in caplog.text
    assert store._q("SELECT COUNT(*) FROM voice_profiles WHERE person_id = ?", (ana,))[0][0] == 0
    assert store._q("SELECT COUNT(*) FROM voice_embeddings e LEFT JOIN voice_profiles p ON p.id = e.profile_id "
                    "WHERE p.id IS NULL")[0][0] == 0                                                      # nenhum vetor órfão
    spk0, spk1 = store.segments.get_cluster(voices["SPK_0"]["id"]), store.segments.get_cluster(voices["SPK_1"]["id"])
    assert (spk0["status"], spk0["person_id"], spk0["decision_source"]) == (ClusterStatus.UNNAMED, None, None)
    assert (spk1["llm_person_id"], spk1["llm_name"], spk1["llm_evidence_json"]) == (None, None, None)
    assert store._q("SELECT COUNT(*) FROM segments WHERE person_id = ? OR role_at_time = 'CFO'", (ana,))[0][0] == 0
    cap = store.captions.list_for_meeting(mid)[0]
    assert (cap["person_id"], cap["label"], cap["confidence"], cap["text"]) == (None, None, None, "oi")       # a fala fica
    action = store.actions.list_for_meeting(mid, include_superseded=True)[0]
    assert (action["owner_person_id"], action["owner_text"]) == (None, "Ana")                                # texto da ata fica
    assert json.loads(store.get_meeting(mid)["expected_people_json"]) == [bia]
    assert store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'person' AND entity_id = ?", (ana,))[0][0] == 0
    assert review.forget_person(store, bia) == [mid]                                                         # o maior id
    assert store.people.create_person("Carla") == bia + 1                                                   # A-07: não volta
    assert store.get_meeting(mid)["expected_people_json"] is None
    with pytest.raises(ValueError, match="canal do microfone"):
        review.forget_person(store, me)


def test_orphan_links_are_scrubbed(platform, store, monkeypatch):
    """A-08: o vínculo que um processo grava depois do apagar (o worker que leu a pessoa antes) some. R-12: sem ninguém
    apagado não há órfão possível, e a varredura do fim de cada job e da abertura nem escreve no banco."""
    mid = _record(platform, store)
    _pipeline(store, mid)
    voices = _voices(store, mid)
    writes: list[str] = []
    execute = store.people.core.execute

    def spy(sql: str, params: tuple = ()) -> int:
        writes.append(sql)
        return execute(sql, params)

    monkeypatch.setattr(store.people.core, "execute", spy)
    assert store.people.scrub_orphans() == 0 and writes == []                                                # R-12
    monkeypatch.undo()
    gone = store.people.create_person("Ana")
    store.people.forget(gone)
    store._x("UPDATE clusters SET person_id = ?, status = ? WHERE id = ?", (gone, ClusterStatus.CONFIRMED, voices["SPK_0"]["id"]))
    store._x("UPDATE segments SET person_id = ?, role_at_time = 'X' WHERE cluster_id = ?", (gone, voices["SPK_0"]["id"]))
    assert store.people.scrub_orphans() >= 2
    spk0 = store.segments.get_cluster(voices["SPK_0"]["id"])
    assert (spk0["person_id"], spk0["status"]) == (None, ClusterStatus.UNNAMED)
    assert store._q("SELECT COUNT(*) FROM segments WHERE person_id = ? OR role_at_time = 'X'", (gone,))[0][0] == 0
    assert store.people.scrub_orphans() == 0                                                                 # nada sobrou


def test_editing_the_role_keeps_the_role_of_the_day_in_other_meetings(store, caplog):
    from tests.test_rag_index import _meeting

    ana = store.people.create_person("Ana", role="CFO")
    bia = store.people.create_person("Bia")
    first, _ = _meeting(store, {"ana": ana, "bia": bia})                 # falas da Ana sem cargo do dia: seguiam a ficha
    second, _ = _meeting(store, {"ana": ana, "bia": bia}, title="Outra")
    with caplog.at_level("INFO", logger="listener.pipeline.review"):
        again = review.edit_person(store, ana, second, "Ana", "CEO", "Empresa X", CANARY)
    assert sorted(again) == [first, second] and "Ana" not in caplog.text and CANARY not in caplog.text
    rows = {mid: {r.role for r in review.transcript_rows(store, mid) if r.person_id == ana} for mid in (first, second)}
    assert rows == {first: {"CFO"}, second: {"CEO"}}                                                       # o cargo do dia
    assert review.edit_person(store, ana, second, "Ana", "CEO", "Outra", CANARY) == []                     # nada no texto
    assert store.people.get_person(ana)["company"] == "Outra"


def test_the_llm_sees_speakers_with_role_and_company_but_never_the_email(platform, store, caplog, no_summary_gate):
    store.upsert_me("Eu", role="Gerente", company="Empresa Y")
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    voices = _voices(store, mid)
    ana = review.create_person(store, voices["SPK_0"]["id"], "Ana", "CFO", "Empresa X", CANARY)
    review.create_person(store, voices["SPK_1"]["id"], "Bia", email=CANARY)
    ctx = llm_steps.build_context(store, mid, parent)
    speakers = {p.name: (p.role, p.company) for p in ctx.meeting_ctx.speakers}
    assert speakers == {"Eu": ("Gerente", "Empresa Y"), "Ana": ("CFO", "Empresa X"), "Bia": ("", "")}
    assert any(ln.speaker == "Ana (CFO)" for ln in ctx.lines) and any(ln.speaker == "Eu (Gerente)" for ln in ctx.lines)
    assert any(p.id == ana for p in ctx.meeting_ctx.speakers)
    with caplog.at_level("DEBUG"):
        _runner, status, fake = _run_llm(store)
        assert status == JobStatus.DONE
        sync_text(store, mid)
    minutes = fake.calls(LlmTask.MINUTES)[0].messages[1].text
    assert "Identified speakers in this meeting" in minutes and "(" + str(ana) + ") Ana — CFO, Empresa X" in minutes
    blobs = store._q("SELECT request_blob, response_blob FROM llm_calls")
    assert blobs and all(CANARY not in json.dumps(unpack_blob(b), ensure_ascii=False) for row in blobs for b in row if b)
    assert store._q("SELECT COUNT(*) FROM rag_chunks WHERE text LIKE ? OR speakers LIKE ?", (f"%{CANARY}%",) * 2)[0][0] == 0
    assert store._q("SELECT COUNT(*) FROM rag_fts WHERE rag_fts MATCH ?", ('"canario"',))[0][0] == 0
    assert CANARY not in caplog.text
    assert store.summaries.current(mid, SummaryKind.MINUTES)["prompt_version"] == "minutes/1.2"
