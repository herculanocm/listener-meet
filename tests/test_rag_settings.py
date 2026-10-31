"""F5 L5: a estimativa do "reindexar" (trechos sem vetor do modelo ativo, tokens pelos caracteres ÷ 4, custo pela tabela,
minutos pelos lotes de 64 e pela latência medida ou 2 s), o grupo "Busca" das Configurações ("Completar índice" e
"Reindexar tudo…" com a confirmação, o worker leve lançado), a troca do embedder oferecendo "Reindexar agora" ou "Depois"
(comportamento 4) e o `--check` do G5 só leitura: o bloco `rag` por reunião, o objeto geral no fim (cobertura, FTS,
buscas fixas, U-027, custo por `llm_calls`) e o banco em v7 ("esquema anterior à F5")."""
from __future__ import annotations

import hashlib
import json
import math

import pytest

from listener.__main__ import main
from listener.llm.fake import FakeProvider
from listener.llm.types import LlmTask
from listener.model import JobStatus, LlmCallStatus
from listener.rag import chat_global, chunker, index, search
from listener.store import Store
from listener.store.llm_calls import LlmCallInsert
from tests.test_main_check import _reports
from tests.test_rag_chat import QUESTION, _llm_ready
from tests.test_rag_index import MODEL, _meeting, _people, _pipeline_job, _ready
from tests.test_rag_search import _other_meeting

pytest.importorskip("PySide6")


@pytest.fixture(autouse=True)
def fresh_caches():
    search.clear_caches()
    yield
    search.clear_caches()


def _two(store: Store, vectors_for_first: bool = False) -> tuple[int, int]:
    who = _people(store) | {"carla": store.people.create_person("Carla", role="Gerente")}
    a, _job = _meeting(store, who)
    b = _other_meeting(store, who)
    _ready(store)
    for mid in (a, b):
        index.sync_text(store, mid)
    if vectors_for_first:
        index.fill_vectors(store, a, provider=FakeProvider())
    return a, b


def _call(store: Store, latency_ms: int, strategy: str) -> None:
    store.llm_calls.insert(LlmCallInsert(None, None, str(LlmTask.RAG_EMBED), "gemini", "gemini-embedding-001", None, None, 10, 0, 0, 0,
                                         0.0, latency_ms, LlmCallStatus.OK, None, strategy), store_payloads=False)


def test_reindex_estimate_counts_tokens_cost_and_minutes(store):
    a, b = _two(store)
    est = index.reindex_estimate(store)
    chars = 0
    for mid in (a, b):
        date = str(store.get_meeting(mid)["started_at"])[:10]
        chars += sum(len(chunker.embed_input(str(c["title"]), date, str(c["text"]))) for c in store.rag.list_chunks(mid))
    assert (est.model_key, est.ready, est.chunks, est.meetings, est.without_chunks) == (MODEL, True, store.rag.count_chunks(), 2, 0)
    assert est.tokens == math.ceil(chars / 4) and est.cost_usd == round(est.tokens * 0.15 / 1_000_000, 6)    # gemini-embedding-001
    assert (est.batches, est.seconds, est.measured) == (2, 4.0, False)                     # um lote por reunião, 2 s cada
    text = est.text()
    assert f"{est.chunks} trechos sem vetor em 2 reuniões" in text and "tokens" in text and "US$" in text and "2 s por lote" in text
    _call(store, 3000, "batch:5")
    _call(store, 100, "query")                                                            # o vetor da consulta não entra na média
    assert (index.reindex_estimate(store).seconds, index.reindex_estimate(store).measured) == (6.0, True)
    index.fill_vectors(store, a, provider=FakeProvider())
    after = index.reindex_estimate(store)
    assert (after.chunks, after.meetings) == (store.rag.count_chunks(b), 1)
    empty = store.create_meeting("sem trechos", None, None, None)
    store.finish_meeting(empty, 1000)
    _pipeline_job(store, empty)
    assert "1 reunião(ões) ainda sem trechos" in index.reindex_estimate(store).text()
    store.providers.update_provider("gemini", api_key="")
    blocked = index.reindex_estimate(store)
    assert not blocked.ready and "sem chave" in blocked.reason and blocked.model_key == MODEL


def test_settings_search_group_completes_and_reindexes_with_confirmation(qtbot, store, platform, monkeypatch):
    from listener.ui import settings_min
    from listener.ui.settings_min import SettingsDialog

    a, b = _two(store, vectors_for_first=True)
    launched: list[bool] = []
    dlg = SettingsDialog(store, platform, {}, on_launch=launched.append)
    qtbot.addWidget(dlg)
    assert {"rag.auto_index", "rag.top_k", "rag.max_per_meeting"} <= set(dlg._editors) and dlg.editor("rag.top_k").raw() == "12"
    status = dlg.index_status.text()
    assert "2 de 2 reuniões processadas no índice" in status and "faltam" in status
    assert "vetores de gemini-embedding-001, Gemini" in status and MODEL not in status            # U-036: o nome, não a chave
    assert "embeddings prontos" in status and "completar:" in status and dlg.complete_btn.isEnabled()
    asked: list[str] = []
    answer = {"value": False}

    def ask(_parent: object, text: str, yes: str, no: str) -> bool:
        asked.append(f"{text}|{yes}|{no}")
        return answer["value"]

    monkeypatch.setattr(settings_min, "ask_reindex", ask)
    dlg.complete_btn.click()                       # há vetores a pedir (com custo): a estimativa e a confirmação antes
    assert len(asked) == 1 and asked[0].startswith("Completar o índice da busca?") and "sem vetor em 1 reunião" in asked[0]
    assert asked[0].endswith("|Completar|Cancelar") and store.jobs.active(kinds=("index",)) == [] and launched == []   # Cancelar
    answer["value"] = True
    dlg.complete_btn.click()                                                              # só a reunião com trecho sem vetor
    pending = {int(j["meeting_id"]): json.loads(j["params_json"]) for j in store.jobs.active(kinds=("index",))}
    assert set(pending) == {b} and pending[b]["force"] is True and pending[b]["reason"] == "reindex" and launched == [True]
    assert "1 reunião(ões) na fila do índice" in dlg.index_status.text() and store.rag.get_model(MODEL)["backfill"] == 0
    answer["value"] = False
    assert dlg.reindex_all() == 0 and len(store.jobs.active(kinds=("index",))) == 1                 # Cancelar: nada
    assert "Refazer o índice de todas as reuniões processadas" in asked[2] and "|Reindexar|Cancelar" in asked[2]
    answer["value"] = True
    assert dlg.reindex_all() == 1 and {int(j["meeting_id"]) for j in store.jobs.active(kinds=("index",))} == {a, b}
    assert store.rag.get_model(MODEL)["backfill"] == 1 and launched == [True, True]           # o modelo fica confirmado
    store.providers.update_provider("gemini", api_key="")       # sem embeddings, completar é só trecho e FTS: sem custo, direto
    empty = store.create_meeting("sem trechos", None, None, None)
    store.finish_meeting(empty, 1000)
    _pipeline_job(store, empty)
    assert dlg.complete_index() == 1 and len(asked) == 4 and launched == [True, True, True]
    top_k = dlg.editor("rag.top_k")
    top_k.set_raw("3")                                    # A-14: o spin não aceita o 3; R-04: vale o padrão, gravado ao salvar
    assert "Trechos por pergunta" in (top_k.saved_error or "") and top_k.raw() == "12" and dlg.validation_error() is None


def test_completing_says_that_meetings_without_chunks_also_get_vectors(qtbot, store, platform, monkeypatch):
    """R8-05: com os embeddings prontos, as reuniões ainda sem trechos entram com `force` e também recebem vetores: a
    confirmação do "Completar índice" diz isso (o custo delas só se sabe depois do corte em trechos) em vez de "entram com o
    índice por palavra", que só vale sem embeddings."""
    from listener.ui import settings_min
    from listener.ui.settings_min import SettingsDialog

    a, b = _two(store)
    for mid in (a, b):
        index.fill_vectors(store, mid, provider=FakeProvider())                           # nenhum trecho sem vetor
    empty = store.create_meeting("sem trechos", None, None, None)
    store.finish_meeting(empty, 1000)
    _pipeline_job(store, empty)
    asked: list[str] = []

    def ask(_parent: object, text: str, _yes: str, _no: str) -> bool:
        asked.append(text)
        return True

    monkeypatch.setattr(settings_min, "ask_reindex", ask)
    dlg = SettingsDialog(store, platform, {}, on_launch=lambda _light: None)
    qtbot.addWidget(dlg)
    assert dlg.complete_index() == 1 and len(asked) == 1 and "nenhum trecho sem vetor do modelo ativo" in asked[0]
    assert "1 reunião(ões) ainda sem trechos, que também recebem vetores" in asked[0] and "por palavra" not in asked[0]
    job = store.jobs.active(kinds=("index",))[0]
    assert int(job["meeting_id"]) == empty and json.loads(job["params_json"])["force"] is True   # os vetores dela vêm
    store.providers.update_provider("gemini", api_key="")                                # sem embeddings: só o índice por palavra
    assert "1 reunião(ões) ainda sem trechos (entram com o índice por palavra)" in index.reindex_estimate(store).text()


def test_changing_the_embedder_offers_reindexing_now_or_later(qtbot, store, platform, monkeypatch):
    from listener.ui import settings_min
    from listener.ui.settings_min import SettingsDialog

    _two(store, vectors_for_first=True)
    asked: list[str] = []
    answer = {"value": False}

    def ask(_parent: object, text: str, yes: str, no: str) -> bool:
        asked.append(f"{text}|{yes}|{no}")
        return answer["value"]

    monkeypatch.setattr(settings_min, "ask_reindex", ask)
    launched: list[bool] = []
    dlg = SettingsDialog(store, platform, {}, on_launch=launched.append)
    qtbot.addWidget(dlg)
    dlg._save()                                                                           # nada mudou: nada a oferecer
    assert asked == [] and dlg.result() == dlg.DialogCode.Accepted
    dlg = SettingsDialog(store, platform, {}, on_launch=launched.append)
    qtbot.addWidget(dlg)
    tabs = dlg.provider_tabs
    tabs.tab_by_id["openai"].key.setText("sk-teste-1234567890")
    tabs.active_embed.setCurrentIndex(tabs.active_embed.findData("openai"))
    dlg._save()                                                                           # "Depois": os vetores do gemini ficam
    assert len(asked) == 1 and "openai/text-embedding-3-small@768" in asked[0] and asked[0].endswith("|Reindexar agora|Depois")
    assert "voltar para ele não custa" in asked[0] and store.jobs.active(kinds=("index",)) == [] and launched == []
    assert store.rag.count_vectors(MODEL) > 0
    dlg = SettingsDialog(store, platform, {}, on_launch=launched.append)
    qtbot.addWidget(dlg)
    tabs = dlg.provider_tabs
    tabs.active_embed.setCurrentIndex(tabs.active_embed.findData("gemini"))
    answer["value"] = True
    dlg._save()                                                                           # "Reindexar agora"
    assert len(asked) == 2 and len(store.jobs.active(kinds=("index",))) == 2 and launched == [True]


def _contents(db) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in db.iterdir()
            if p.name.endswith((".db", ".db-wal")) and p.stat().st_size}


def test_check_reports_the_g5_rag_blocks_readonly(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LISTENER_LLM", "fake")
    db = tmp_path / "db"
    with Store(db) as store:
        a, b = _two(store, vectors_for_first=True)
        _llm_ready(store)
        for mid, status, info in ((a, JobStatus.DONE, {"chunks": 5, "vectors": 5, "note": ""}),
                                  (b, JobStatus.DONE, {"chunks": 4, "vectors": 0, "note": "4 trechos sem vetor: sem rede (x)"})):
            job = store.jobs.enqueue_index(mid, "pipeline")
            store.jobs.claim(pid=1, kinds=("index",))
            store.jobs.finish(job, status, timings={"index": info})
        first = chat_global.run_global_chat(store, None, QUESTION)
        second = chat_global.run_global_chat(store, first.thread_id, "e o prazo?")
        chat_global.rate_answer(store, first.message_id, 1)
        chat_global.rate_answer(store, second.message_id, 0)
        n_b, total = store.rag.count_chunks(b), store.rag.count_chunks()
        cited = {c["meeting_id"] for ans in (first, second) for c in ans.citations if c.get("meeting_id") is not None}
    before = _contents(db)
    assert main(["--check", str(db)]) == 0
    out = _reports(capsys.readouterr().out)
    by_meeting = {r["meeting"]: r for r in out if "meeting" in r}
    ra, rb = by_meeting[a]["rag"], by_meeting[b]["rag"]
    assert ra["chunks_total"] == 5 and ra["chunks"]["transcricao"] == 4 and ra["vectors"] == {MODEL: 5} and ra["missing_active"] == 0
    assert ra["active_model"] == MODEL and ra["last_index"]["status"] == "concluído" and ra["last_index"]["reason"] == "pipeline"
    assert rb["missing_active"] == n_b and rb["vectors"] == {} and rb["last_index"]["note"].startswith("4 trechos sem vetor: sem rede")
    overall = out[-1]["rag"]
    assert overall["schema"] == 9 and overall["embedder"]["ready"] and overall["meetings"] == {"processed": 2, "indexed": 2, "pct": 100.0}
    assert overall["fts"] == {"docsize": total, "chunks": total, "ok": True} and overall["vectors"]["active"] == 5
    assert overall["vectors"]["missing_active"] == n_b and overall["vectors"]["by_model"] == {MODEL: 5}
    assert [p["query"] for p in overall["fts_probes"]] == ["reunião", "orçamento", "prazo", "decisão", "próximos passos"]
    assert all(p["ms"] >= 0 for p in overall["fts_probes"]) and overall["gate"]["fts_ms"]["ok"] is None   # menos de 50 reuniões
    u027 = overall["u027"]
    assert (u027["answers"], u027["rated"], u027["correct"], u027["pct"], u027["ok"]) == (2, 2, 1, 50.0, None)   # menos de 10
    assert u027["meetings_cited"] == len(cited) >= 1
    costs = overall["costs"]
    assert costs["chat"]["calls"] == 2 and costs["query"]["calls"] >= 1 and costs["total_usd"] == pytest.approx(
        costs["chat"]["usd"] + costs["query"]["usd"] + costs.get("index", {"usd": 0.0})["usd"])
    assert _contents(db) == before                                                        # --check não escreve nada


def test_check_on_a_v7_database_says_the_schema_is_before_f5(tmp_path, capsys):
    db = tmp_path / "db"
    with Store(db) as store:
        mid = store.create_meeting("antiga", None, None, None)
        store.finish_meeting(mid, 1000)
        with store.transaction() as c:
            c.execute("DELETE FROM schema_migrations WHERE version >= 8")             # F5.10: e a v9
            c.execute("PRAGMA user_version = 7")
    assert main(["--check", str(db)]) == 0
    out = _reports(capsys.readouterr().out)
    assert out[0]["meeting"] == mid and out[0]["rag"] == {"error": "esquema anterior à F5"}
    assert out[-1] == {"rag": {"error": "esquema anterior à F5"}}
