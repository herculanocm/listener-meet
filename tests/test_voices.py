"""F5.5: a voz sem pessoa como "Voz 2" em todo lugar onde o usuário lê. A tabela do A-15 (`pipeline.voices.speaker_of`), sem
job vigente sem numeração (A-05); o mesmo "Voz N" na tela, no .md, no .docx, nos trechos da busca e no chat global (A-17,
inclusive a janela que só cobre a segunda voz) com as consultas contadas; a entrada da LLM com "Voz 2" e a identidade pelo
rótulo interno (A-14); o canal L do preset sala (A-04); a assinatura dos falantes versionada (A-13, P-01); a varredura única
do índice (A-16, A-09, P-02) e as camadas sem Qt (A-21)."""
from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from listener.llm.types import LlmTask
from listener.model import ChunkKind, ClusterStatus, EventType, JobStatus, SummaryKind
from listener.pipeline import llm_steps, review, summary_gate, voices
from listener.rag import chat_global, export, index, voice_sweep
from listener.rag.search import SearchHit
from listener.store.segments import ClusterInsert, SegmentInsert
from listener.store.summaries import SummaryInsert
from tests.test_rag_index import _meeting, _people, _pipeline_job, _ready

PEOPLE = {7: {"id": 7, "name": "Eu", "role": "Gerente"}, 8: {"id": 8, "name": "Ana", "role": "CFO"}}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _c(cid: int, channel: str, label: str, status: ClusterStatus = ClusterStatus.UNNAMED, person: int | None = None,
       merged: int | None = None) -> dict[str, Any]:
    return {"id": cid, "channel": channel, "cluster": label, "status": str(status), "person_id": person, "merged_into": merged}


# ---------------------------------------------------------------- A-15: o nome da fala, puro
def test_the_name_of_a_speech_follows_the_a15_table_matching_by_cluster_and_never_by_label():
    clusters: dict[int, Any] = {c["id"]: c for c in (
        _c(1, "L", "L", ClusterStatus.CONFIRMED, 7),                   # o L do usuário com "eu": fora da numeração
        _c(2, "R", "SPK_0"), _c(3, "L", "SPK_0"),                     # o mesmo rótulo em dois canais: duas vozes
        _c(4, "R", "SPK_1", merged=2),                                 # mesclada numa voz sem pessoa: o número do alvo
        _c(5, "R", "SPK_2", ClusterStatus.CONFIRMED, 8), _c(6, "R", "SPK_3", merged=5))}   # mesclada num alvo decidido
    names = voices.number_voices(clusters.values(), {2: 1_000, 3: 2_000, 4: 500, 5: 3_000, 6: 4_000})

    def name(**s: Any) -> str:
        return voices.speaker_of(s, clusters, PEOPLE, names)[1]

    assert name(channel="L", cluster="L", cluster_id=1) == "Eu"
    assert (name(cluster="SPK_0", cluster_id=2), name(cluster="SPK_1", cluster_id=4)) == ("Voz 1", "Voz 1")
    assert name(channel="L", cluster="SPK_0", cluster_id=3) == "Voz 2"                    # preset sala: L/SPK_n numerada
    assert (name(cluster="SPK_2", cluster_id=5), name(cluster="SPK_3", cluster_id=6)) == ("Ana", "Ana")
    assert name(cluster="SPK_0", cluster_id=2, person_id=8) == "Ana"                      # a pessoa gravada na fala vence
    assert name(cluster="SPK_0", cluster_id=99) == "Voz SPK_0"                            # cluster de outro job: o rótulo
    assert name(cluster="SPK_0", cluster_id=None) == "Voz SPK_0"                          # sem cluster: nunca o número do rótulo
    assert name(cluster=None, cluster_id=None) == "Desconhecido"
    alone = {9: _c(9, "L", "L")}                                                          # o L sem "eu" (sem pessoa)
    assert voices.speaker_of({"channel": "L", "cluster": "L", "cluster_id": 9}, alone, PEOPLE,
                             voices.number_voices(alone.values(), {9: 0}))[1] == "Voz L"
    assert voices.numbering(clusters.values(), None, [{"cluster_id": 2, "t_start_ms": 0}]) is voices.NO_NAMES   # A-05


def _voice_meeting(store: Any, ana: int, title: str = "Orçamento 2027") -> tuple[int, int]:
    """Reunião processada sem áudio: duas vozes sem pessoa no R (rótulos fora da ordem da fala) e a Ana pela fala gravada."""
    mid = store.create_meeting(title, None, None, None)
    store.finish_meeting(mid, 300_000)
    job = _pipeline_job(store, mid)
    ids = store.segments.insert_clusters([ClusterInsert(mid, job, "R", "SPEAKER_03", 60_000, None, None, ClusterStatus.UNNAMED),
                                          ClusterInsert(mid, job, "R", "SPEAKER_00", 30_000, None, None, ClusterStatus.UNNAMED)])
    lines = [(0, 30, "SPEAKER_03", "Bom dia, vamos falar do orçamento de 2027."), (30, 70, None, "O fluxo de caixa está apertado."),
             (70, 100, "SPEAKER_00", "Precisamos aprovar a auditoria até sexta."), (100, 140, "SPEAKER_03", "Concordo, e vence.")]
    store.segments.insert_segments([SegmentInsert(mid, job, "R", t0 * 1000, t1 * 1000, text, "pyannote", "final", label,
                                                  ids[label] if label else None, None if label else ana) for t0, t1, label, text in lines])
    return mid, job


def _hit(mid: int, t0_s: int, t1_s: int) -> SearchHit:
    return SearchHit(0, mid, "", "", str(ChunkKind.TRANSCRIPT), t0_s * 1000, t1_s * 1000, "", (), "x", "", 1, None, 1.0)


# ---------------------------------------------------------------- o mesmo "Voz N" em todo lugar (A-17)
def test_the_reading_export_docx_index_and_global_chat_show_the_same_voz_n(store, monkeypatch):
    ana = store.people.create_person("Ana", role="CFO")
    mid, _job = _voice_meeting(store, ana)
    other, _ = _voice_meeting(store, ana, "Outra")

    def no_speakers(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a leitura acha a primeira fala de cada voz nas falas que já leu (A-17)")

    with monkeypatch.context() as m:
        m.setattr(store.segments, "speakers", no_speakers)
        rows = review.transcript_rows(store, mid)
    assert [r.name for r in rows] == ["Voz 1", "Ana", "Voz 2", "Voz 1"] and rows[1].role == "CFO"
    data = export.gather(store, mid)
    md = export.transcript_markdown(data)
    assert "Voz 1" in md and "Voz 2" in md and "SPEAKER" not in md and data.participants == ("Ana — CFO",)
    index.sync_text(store, mid)
    speakers = [str(c["speakers"]) for c in store.rag.list_chunks(mid) if c["kind"] == ChunkKind.TRANSCRIPT]
    assert speakers == ["Voz 1, Ana — CFO", "Voz 2, Voz 1"]
    calls: dict[int, int] = {}
    real = store.segments.speakers

    def counted(meeting_id: int, job_id: int | None) -> list[dict[str, Any]]:
        calls[meeting_id] = calls.get(meeting_id, 0) + 1
        return real(meeting_id, job_id)

    monkeypatch.setattr(store.segments, "speakers", counted)
    blocks, _targets = chat_global.build_excerpts(store, [_hit(mid, 70, 100), _hit(other, 0, 30), _hit(other, 70, 100)])
    assert calls == {mid: 1, other: 1}                                                    # uma leitura por reunião
    assert [ln.speaker for ln in blocks[0].lines] == ["Voz 2"]                            # só a segunda voz na janela
    assert [ln.speaker for ln in blocks[1].lines] == ["Voz 1", "Voz 2"] and blocks[0].participants == "Ana — CFO"
    docx = pytest.importorskip("docx")
    from listener.rag import docx_export

    text = "\n".join(p.text for p in docx.Document(io.BytesIO(docx_export.meeting_docx(data))).paragraphs)
    assert "Voz 1" in text and "Voz 2" in text and "SPEAKER" not in text


# ---------------------------------------------------------------- A-21: as camadas
def test_pipeline_rag_llm_and_store_never_import_the_ui_or_qt():
    bad: list[str] = []
    for pkg in ("pipeline", "rag", "llm", "store"):
        for path in sorted((ROOT / "listener" / pkg).rglob("*.py")):
            parts = path.relative_to(ROOT).with_suffix("").parts
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = list(parts[:-1])[:len(parts) - node.level] if node.level else []
                    names = [".".join([*base, node.module or ""]).strip(".")]
                    names += [f"{names[0]}.{a.name}" for a in node.names]
                else:
                    continue
                bad += [f"{path.name}:{node.lineno} {n}" for n in names if n.startswith(("listener.ui", "PySide6"))]
    assert not bad


# ---------------------------------------------------------------- A-13, P-01: a assinatura dos falantes versionada
def _v1(seen: set[tuple[str, str, str, int]]) -> str:
    """A fórmula de antes da F5.5, escrita aqui: o conjunto (canal, nome interno, cargo, pessoa) da leitura."""
    return hashlib.sha256(json.dumps(sorted(seen), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _minutes_done(store: Any, mid: int, data: dict[str, Any]) -> int:
    job = store.segments.current_job_id(mid)
    sid = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "## Ata", "gemini", "m", "minutes/1.1", job, None, "pt-BR"))
    store.add_event(mid, None, EventType.JOB_STEP, {"step": "minutes_done", "summary_id": sid, **data})
    return sid


def test_the_v1_signature_is_the_old_value_and_an_old_minutes_does_not_light_up(platform, store):
    from tests.test_review import _processed

    mid, items = _processed(platform, store)
    ana = review.new_person(store, items[0].cluster_id, "Ana", role="CFO")
    eu = int(store.get_me()["id"])
    old = _v1({("L", "Eu", "Gerente", eu), ("R", "Ana", "CFO", ana), ("R", "Voz SPK_1", "", 0)})
    assert summary_gate.speakers_digest(store, mid, 1) == old != summary_gate.speakers_digest(store, mid)
    _minutes_done(store, mid, {"speakers": old})                                          # ata de antes: sem `speakers_v`
    assert not summary_gate.names_changed(store, mid)
    review.edit_person(store, ana, mid, "Ana Souza", "CFO")
    assert summary_gate.names_changed(store, mid)                                         # a v1 segue vendo o nome mudar


# ---------------------------------------------------------------- a entrada da LLM (A-02, A-04, A-11, A-14, A-20)
def test_the_llm_reads_voz_n_while_the_identity_keeps_the_internal_label(platform, store, monkeypatch):
    from listener.llm import chat_task
    from listener.llm.prompts.common import VOICE_LABELS
    from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm

    store.upsert_me("Eu", role="Gerente")
    ana = store.people.create_person("Ana", role="CFO")
    store.set_setting("llm.auto_tasks", False)
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    llm_steps.enqueue_llm_job(store, mid, ["title"], parent)                                # o título pelas primeiras falas
    _runner, status, fake = _run_llm(store)
    title = [m.text for m in fake.calls(LlmTask.TITLE)[0].messages]
    assert status == JobStatus.DONE and "] Voz 1: bom dia Ana" in title[1]
    llm_steps.enqueue_llm_job(store, mid, ["identify", "minutes", "revise"], parent)
    _runner, status, fake = _run_llm(store)
    assert status == JobStatus.DONE
    texts = [*title, *(m.text for task in (LlmTask.MINUTES, LlmTask.REVISE) for c in fake.calls(task) for m in c.messages)]
    minutes = fake.calls(LlmTask.MINUTES)[0].messages
    assert "] Voz 1: bom dia Ana" in minutes[1].text and "Voz 2: " in minutes[1].text and VOICE_LABELS in minutes[0].text
    assert all("SPK_" not in t for t in texts) and any("Voz 2: " in t for c in fake.calls(LlmTask.REVISE) for t in [c.messages[-1].text])
    ident = fake.calls(LlmTask.IDENTIFY)[0].messages[1].text
    assert "Unresolved speakers: SPK_0, SPK_1" in ident and "] SPK_0: bom dia Ana" in ident and "Voz 1" not in ident
    clusters = {c["cluster"]: c for c in store.segments.list_clusters(mid, parent)}
    assert clusters["SPK_0"]["llm_person_id"] == ana and clusters["SPK_1"]["llm_person_id"] is None
    owners = {a["owner_text"] for a in store.actions.list_for_meeting(mid) if a["segment_id"] is not None}
    assert owners == {"Eu"}                                                               # A-11: o dono do fake não é uma voz
    monkeypatch.setenv("LISTENER_LLM", "fake")
    answer = chat_task.run_chat(store, mid, "Quem falou do orçamento?")
    req, _ = store.llm_calls.payloads(answer.call_id)
    sent = "\n".join(str(m["text"]) for m in req)
    assert answer.error is None and "Voz 2: " in sent and "SPK_" not in sent and VOICE_LABELS in req[0]["text"]
    assert store.summaries.current(mid, SummaryKind.MINUTES)["prompt_version"] == "minutes/1.2"
    assert store._q("SELECT prompt_version FROM llm_calls WHERE id = ?", (answer.call_id,))[0][0] == "chat/1.2"


def test_every_prompt_that_writes_about_the_speakers_keeps_the_voice_labels():
    """A-20, P-03: a frase "copie os rótulos de voz como estão" no SYSTEM da ata (e da redução), do chat da reunião, do
    executivo, do pré-resumo e do chat global, cada um com a versão que sobe junto."""
    from listener.llm import prompts
    from listener.llm.prompts.common import VOICE_LABELS, Line, MeetingCtx

    meeting = MeetingCtx("Comitê", "2026-10-28", None, (), (), "en")
    lines = (Line(1, 0, "Voz 2", "hello"),)
    summary = prompts.executive.SummaryCtx(meeting, "{}")
    systems = {"ata": prompts.minutes.build(prompts.minutes.MinutesCtx(meeting, lines, ())),
               "redução": prompts.minutes.build_reduce(prompts.minutes.ReduceCtx(meeting, ["{}"], ())),
               "chat": prompts.chat.build(prompts.chat.ChatCtx(meeting, lines, (), "q")),
               "executivo": prompts.executive.build(summary), "pré-resumo": prompts.pre_summary.build(summary),
               "chat global": prompts.rag_chat.build(prompts.rag_chat.RagChatCtx([], [], "q", "en"))}
    assert {task: VOICE_LABELS in msgs[0].text for task, msgs in systems.items()} == dict.fromkeys(systems, True)
    tasks = (LlmTask.MINUTES, LlmTask.CHAT, LlmTask.EXECUTIVE, LlmTask.PRE_SUMMARY, LlmTask.RAG_CHAT)
    assert [prompts.prompt_version(t) for t in tasks] == ["minutes/1.2", "chat/1.2", "executive/1.2", "pre_summary/1.2", "rag_chat/1.1"]


def test_in_the_room_preset_an_ignored_voice_of_channel_l_is_voz_n_and_not_the_user(store):
    """A-04: o canal L sem pessoa é você só na fala sem cluster; a voz L/SPK_n ignorada do preset sala é "Voz N"."""
    store.upsert_me("Eu", role="Gerente")
    mid = store.create_meeting("Sala", None, None, None, is_in_room=True)
    store.finish_meeting(mid, 60_000)
    job = _pipeline_job(store, mid)
    r = store.segments.insert_clusters([ClusterInsert(mid, job, "R", "SPK_0", 5000, None, None, ClusterStatus.UNNAMED)])
    l = store.segments.insert_clusters([ClusterInsert(mid, job, "L", "SPK_0", 5000, None, None, ClusterStatus.IGNORED)])
    store.segments.insert_segments([SegmentInsert(mid, job, "R", 0, 1000, "bom dia", "pyannote", "final", "SPK_0", r["SPK_0"]),
                                    SegmentInsert(mid, job, "L", 2000, 3000, "oi", "pyannote", "final", "SPK_0", l["SPK_0"]),
                                    SegmentInsert(mid, job, "L", 4000, 5000, "eu aqui", "canal", "final")])
    ctx = llm_steps.build_context(store, mid, job)
    assert [ln.speaker for ln in ctx.lines] == ["Voz 1", "Voz 2", "Eu (Gerente)"]
    assert [ln.speaker for ln in ctx.identify_lines] == ["SPK_0", "Voz 2", "Eu (Gerente)"]


def test_a_merge_of_two_voices_lights_the_refresh_of_new_minutes(platform, store):
    """P-01: a ata nova grava a v2 (o nome mostrado); mesclar duas vozes sem pessoa renumera e acende o aviso."""
    from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm

    store.set_setting("llm.auto_tasks", False)
    mid = _record(platform, store)
    _provider_ready(store)
    parent = _pipeline(store, mid)
    llm_steps.enqueue_llm_job(store, mid, ["minutes"], parent)
    _runner, status, _fake = _run_llm(store)
    assert status == JobStatus.DONE and not summary_gate.names_changed(store, mid)
    done = next(e["data"] for e in store.list_events(mid) if (e.get("data") or {}).get("step") == "minutes_done")
    assert done["speakers_v"] == summary_gate.SPEAKERS_V == 2 and done["speakers"] == summary_gate.speakers_digest(store, mid)
    first, second = (c.cluster_id for c in review.pending(review.review_items(store, mid)))
    old = summary_gate.speakers_digest(store, mid, 1)
    review.merge(store, second, first)
    assert summary_gate.speakers_digest(store, mid, 1) == old and summary_gate.names_changed(store, mid)


# ---------------------------------------------------------------- A-16: a varredura única do índice
def _indexed(store: Any) -> tuple[int, int]:
    """Duas reuniões indexadas com vetores: uma com a última fala de uma voz sem pessoa e outra só com pessoas."""
    who = _people(store)
    voice, job = _meeting(store, who, "Com voz")
    ids = store.segments.insert_clusters([ClusterInsert(voice, job, "R", "SPK_7", 60_000, None, None, ClusterStatus.UNNAMED)])
    store._x("UPDATE segments SET person_id = NULL, cluster = 'SPK_7', cluster_id = ? WHERE meeting_id = ? AND t_start_ms = 200000",
             (ids["SPK_7"], voice))
    people, _ = _meeting(store, who, "Só pessoas")
    _ready(store)
    return voice, people


def _pending_index(store: Any) -> list[int]:
    return [int(j["meeting_id"]) for j in store.jobs.list_jobs(kinds=("index",), statuses=(JobStatus.PENDING,))]


def test_the_sweep_reindexes_the_old_voice_meetings_once_and_only_under_the_vectors_gate(store, monkeypatch):
    from listener.llm.fake import FakeProvider

    voice, people = _indexed(store)
    with monkeypatch.context() as m:                                                     # o índice de antes da F5.5
        m.setattr(review, "numbering", lambda *_a, **_k: voices.NO_NAMES)
        for mid in (voice, people):
            index.sync_text(store, mid)
            index.fill_vectors(store, mid, provider=FakeProvider())
    before = {int(c["id"]): str(c["speakers"]) for c in store.rag.list_chunks(voice)}
    assert any("Voz SPK_7" in s for s in before.values())
    store.set_setting("rag.auto_index", False)
    assert index.sweep(store) == 0 and not store.get_setting(voice_sweep.MARK, "")        # sem o portão, espera (P-02)
    store.set_setting("rag.auto_index", True)
    assert index.sweep(store, should_stop=iter([False, False, True]).__next__) == 0     # parada antes da volta: sem marca
    assert not store.get_setting(voice_sweep.MARK, "") and not _pending_index(store)
    assert index.sweep(store) == 1 and _pending_index(store) == [voice] and store.get_setting(voice_sweep.MARK, "") == "1"
    for job in store.jobs.list_jobs(kinds=("index",), statuses=(JobStatus.PENDING,)):
        store.jobs.request_cancel(int(job["id"]))                                         # a fila vazia: só a marca segura
    assert not _pending_index(store) and index.sweep(store) == 0                          # uma vez só
    store.set_setting(voice_sweep.MARK, "")                                               # contraprova: sem a marca, volta
    assert index.sweep(store) == 1 and _pending_index(store) == [voice]
    res = index.sync_text(store, voice)
    after = {int(c["id"]): str(c["speakers"]) for c in store.rag.list_chunks(voice)}
    assert res.added >= 1 and res.kept == len([s for s in before.values() if "Voz" not in s]) >= 2
    assert set(after.items()) >= {(cid, s) for cid, s in before.items() if "Voz" not in s} and "Voz 1" in ", ".join(after.values())
