"""F5.6 L3 (U-093), excluir reunião no armazenamento: nenhuma linha da reunião em tabela nenhuma (o esquema varrido: toda tabela
com `meeting_id`, fora as chamadas de LLM desligadas e as amostras de voz sem vínculo), as revisões por entidade (A-33), o
áudio apagado, `integrity_check` e o FTS íntegros (A-32), perfis de voz iguais e amostras sem `cluster_id`/`segment_id`
(A-34), o piso de id da reunião e do job (A-34), o arquivo de áudio ausente não criado e a falha entre as duas transações
sem áudio órfão (A-35), e o arquivo de áudio novo com `auto_vacuum` incremental (A-38)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.llm.fake import FakeProvider
from listener.model import ActionStatus, ChatRole, ClusterStatus, EventType, LlmCallStatus, SummaryKind
from listener.pipeline import review
from listener.rag import chat_global, index
from listener.store import Store, space
from listener.store.actions import ActionInsert
from listener.store.captions import CaptionInsert
from listener.store.core import schema_for
from listener.store.llm_calls import LlmCallInsert
from listener.store.segments import ClusterInsert, SegmentInsert
from listener.store.summaries import SummaryInsert
from tests.test_rag_index import _pipeline_job, _ready

TOUCHED = ("voice_embeddings",)        # ficam (sem o vínculo de voz e fala): as amostras dos perfis
DETACHED = ("llm_calls",)              # ficam sem a reunião: o custo do mês


def _vec(i: int, dim: int = 8) -> np.ndarray:
    v = np.zeros(dim, np.float32)
    v[i % dim] = 1.0
    return v


def _chunk(store: Store, mid: int, idx: int, seconds: float = 1.0) -> None:
    x = (np.random.default_rng(idx).standard_normal(int(16000 * seconds)) * 0.1).astype(np.float32)
    store.insert_chunk(mid, "R", idx, int(idx * seconds * 1000), len(x), 16000, 0, encode_flac(x, 16000))


def full_meeting(store: Store, title: str = "Comitê") -> dict[str, Any]:
    """Uma reunião com linha em toda tabela que tem `meeting_id` (e nas filhas delas): áudio, marcador, evento, job com
    artefato, vozes confirmadas e corrigidas (amostras e revisões), falas, legenda, ata editada, ação feita, chamada de LLM,
    chat da reunião, trechos e vetores da busca e uma conversa global que a cita."""
    ana, bia = store.people.create_person("Ana"), store.people.create_person("Bia")
    mid = store.create_meeting(title, "teams", None, "Ana, Bia")
    _chunk(store, mid, 0)
    store.add_marker(mid, 500, "ponto")
    store.add_event(mid, 0, EventType.START, {"n": 1})
    store.finish_meeting(mid, 60_000)
    job = _pipeline_job(store, mid)
    store.jobs.put_artifact(job, "diarize", 0, b"janela")
    cids = store.segments.insert_clusters([ClusterInsert(mid, job, "R", "SPK_0", 5000, _vec(0), "wespeaker", ClusterStatus.UNNAMED),
                                           ClusterInsert(mid, job, "R", "SPK_1", 3000, _vec(3), "wespeaker", ClusterStatus.UNNAMED)])
    store.segments.insert_segments([SegmentInsert(mid, job, "R", 0, 4000, "bom dia, vamos ao orçamento", "pyannote", "final",
                                                  cluster="SPK_0", cluster_id=cids["SPK_0"]),
                                    SegmentInsert(mid, job, "R", 4000, 8000, "o caixa está apertado", "pyannote", "final",
                                                  cluster="SPK_1", cluster_id=cids["SPK_1"])])
    review.confirm(store, cids["SPK_0"], ana)
    review.confirm(store, cids["SPK_1"], ana)
    review.confirm(store, cids["SPK_1"], bia)                 # a correção: invalida a amostra e grava a revisão do perfil
    store.captions.insert(CaptionInsert(mid, "R", 0, 1000, "bom dia", label="Ana"))
    sid = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "## Ata", "fake", "m", "minutes/1.2", job, None, "pt-BR"))
    store.summaries.set_text_edited(sid, "## Ata editada")
    aid = store.actions.insert_many([ActionInsert(mid, sid, "mandar a planilha", None, ana, None, 1000, None, job)])[0]
    store.actions.set_status(aid, ActionStatus.DONE)
    store.update_title(mid, f"{title} (revisto)")
    call = store.llm_calls.insert(LlmCallInsert(mid, job, "minutes", "fake", "m", "minutes/1.2", None, 100, 20, 0, 0, 0.01, 5,
                                                LlmCallStatus.OK, request={"x": 1}, response={"y": 2}))
    store.chat.add(mid, ChatRole.USER, "quais decisões?")
    thread = store.chat.create_thread("orçamento")
    store.chat.add(None, ChatRole.USER, "e o caixa?", thread_id=thread)
    store.chat.add(None, ChatRole.ASSISTANT, "Apertado [R1 00:04].", citations=[{"alias": "R1", "meeting_id": mid, "t_ms": 4000}],
                   thread_id=thread)
    _ready(store)
    index.sync_text(store, mid)
    index.fill_vectors(store, mid, provider=FakeProvider())
    return {"mid": mid, "job": job, "clusters": cids, "summary": sid, "action": aid, "call": call, "thread": thread}


def meeting_tables(store: Store) -> list[str]:
    tables = [str(r[0]) for r in store._q("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return [t for t in tables if "meeting_id" in {str(r[1]) for r in store._q(f"PRAGMA table_info({t})")}]


def _count(store: Store, table: str, mid: int) -> int:
    return int(store._q(f"SELECT COUNT(*) FROM {table} WHERE meeting_id = ?", (mid,))[0][0])  # noqa: S608


def _profiles(store: Store) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in store._q("SELECT id, person_id, centroid, n_samples, threshold, sim_mean, sim_std, calib_n "
                                       "FROM voice_profiles ORDER BY id")]


def _samples(store: Store, mid: int) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in store._q("SELECT id, profile_id, meeting_id, vector, source FROM voice_embeddings WHERE meeting_id = ? "
                                       "ORDER BY id", (mid,))]


def test_delete_leaves_no_row_of_the_meeting_in_any_table_and_keeps_profiles(store):
    kept = full_meeting(store, "Outra")
    m = full_meeting(store)
    mid = m["mid"]
    tables = meeting_tables(store)
    assert {"segments", "clusters", "rag_chunks", "processing_jobs", "voice_embeddings", "llm_calls", "chat_messages"} <= set(tables)
    assert all(_count(store, t, mid) for t in tables), {t: _count(store, t, mid) for t in tables}   # a reunião cobre todas
    chunk_ids = [int(r[0]) for r in store._q("SELECT id FROM rag_chunks WHERE meeting_id = ?", (mid,))]
    profiles, samples = _profiles(store), _samples(store, mid)
    assert samples and any(s[4] == "invalidado" for s in samples) and any(s[4] == "confirmacao" for s in samples)
    store.delete_meeting(mid)
    assert store.get_meeting(mid) is None and store.list_chunks(mid) == [] and store.orphan_report() == {}
    assert {t: _count(store, t, mid) for t in tables if t not in TOUCHED} == dict.fromkeys(set(tables) - set(TOUCHED), 0)
    assert store._q("SELECT COUNT(*) FROM job_artifacts WHERE job_id = ?", (m["job"],))[0][0] == 0
    assert store._q(f"SELECT COUNT(*) FROM rag_vectors WHERE chunk_id IN ({','.join('?' * len(chunk_ids))})", tuple(chunk_ids))[0][0] == 0  # noqa: S608
    # A-34: os perfis iguais; as amostras ficam (vetor, fonte, perfil e reunião) sem o vínculo de voz e fala
    assert _profiles(store) == profiles and _samples(store, mid) == samples
    assert store._q("SELECT COUNT(*) FROM voice_embeddings WHERE meeting_id = ? AND (cluster_id IS NOT NULL OR segment_id IS NOT NULL)",
                    (mid,))[0][0] == 0
    # as chamadas pagas ficam sem a reunião e sem o conteúdo; a conversa global fica, com a citação sem link (P-02)
    call = store.llm_calls.get(m["call"])
    assert call is not None and call["meeting_id"] is None and call["cost_usd"] == 0.01
    assert store.llm_calls.payloads(m["call"]) == (None, None)
    html = chat_global.thread_html(store, m["thread"])
    assert "reunião excluída, 00:04" in html and "listener://open" not in html
    # A-32: o banco e o FTS íntegros; a outra reunião inteira
    assert store._q("PRAGMA integrity_check")[0][0] == "ok"
    store._x("INSERT INTO rag_fts(rag_fts) VALUES ('integrity-check')")
    assert all(_count(store, t, kept["mid"]) for t in tables) and store.list_chunks(kept["mid"])


def test_revisions_of_the_meeting_entities_go_and_the_profile_ones_stay(store):
    other = full_meeting(store, "Outra")
    m = full_meeting(store)
    entities = [("meeting", m["mid"]), ("summary", m["summary"]), ("action_item", m["action"]),
                *(("cluster", cid) for cid in m["clusters"].values())]

    def revs(entity: str, entity_id: int) -> int:
        return int(store._q("SELECT COUNT(*) FROM revisions WHERE entity = ? AND entity_id = ?", (entity, entity_id))[0][0])

    assert all(revs(e, i) for e, i in entities)
    profile_revs = store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'voice_profile'")[0][0]
    store.delete_meeting(m["mid"])
    assert [revs(e, i) for e, i in entities] == [0] * len(entities)
    assert store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'voice_profile'")[0][0] == profile_revs > 0      # A-33: ficam
    assert revs("meeting", other["mid"]) and revs("summary", other["summary"])


def test_ids_of_the_most_recent_meeting_and_its_jobs_are_never_reused(store):
    first = full_meeting(store, "Primeira")
    last = full_meeting(store, "Última")
    top_job = int(store._q("SELECT MAX(id) FROM processing_jobs")[0][0])
    assert last["mid"] > first["mid"] and store.jobs.for_meeting(last["mid"])[0]["id"] == top_job
    store.delete_meeting(last["mid"])
    again = store.create_meeting("Nova", None, None, None)
    job = store.jobs.enqueue(again)
    index_job = store.jobs.enqueue_index(again, "pipeline")
    assert again == last["mid"] + 1 and job == top_job + 1 and index_job == top_job + 2          # acima do piso, nunca o apagado
    assert store.get_setting("meetings.max_id") == str(last["mid"]) and store.get_setting("jobs.max_id") == str(top_job)
    store.delete_meeting(again)
    assert store.create_meeting("Depois", None, None, None) == again + 1


def test_missing_audio_file_is_not_created(store):
    mid = store.create_meeting("Antiga", None, None, None)
    store._x("UPDATE meetings SET audio_file = 'listener-audio-2020-01.db', started_at = '2020-01-10T10:00:00.000-03:00' WHERE id = ?",
             (mid,))
    store.core.forget_meeting(mid)
    assert store.meeting_audio_file(mid) is None
    store.delete_meeting(mid)
    assert store.get_meeting(mid) is None and not (store.base_dir / "listener-audio-2020-01.db").exists()
    assert space.pending(store.core) == []


def test_a_failure_between_the_two_transactions_leaves_no_orphan_audio_and_deleting_again_finishes(store, monkeypatch):
    m = full_meeting(store)
    mid = m["mid"]

    def boom(_mid: int) -> int:
        raise RuntimeError("banco caiu no meio")

    monkeypatch.setattr(store.llm_calls, "detach_meeting", boom)
    with pytest.raises(RuntimeError):
        store.delete_meeting(mid)
    assert store.get_meeting(mid) is not None and store.list_chunks(mid) == [] and store.orphan_report() == {}   # sem áudio, sem órfão
    assert store.segments.count_segments(mid) == 2 and store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'meeting'")[0][0]
    monkeypatch.undo()
    store.delete_meeting(mid)
    assert store.get_meeting(mid) is None and all(_count(store, t, mid) == 0 for t in meeting_tables(store) if t not in TOUCHED)
    assert space.pending(store.core) == [store.core.current_audio_file()]                       # o espaço a liberar ficou marcado


def test_new_audio_files_are_born_with_incremental_auto_vacuum(store):
    schema = schema_for(store.core.current_audio_file())
    assert store._q(f"PRAGMA {schema}.auto_vacuum")[0][0] == space.INCREMENTAL
    assert store._q(f"PRAGMA {schema}.page_size")[0][0] == 32768
    mid = store.create_meeting("x", None, None, None)
    assert store.meeting_audio_file(mid) == store.core.current_audio_file()


def test_reprocessing_drops_the_revisions_of_the_voices_it_replaces(store):
    """R-09: o reprocessamento apaga as vozes do job antigo (`swap_job`); as revisões delas saem junto (o id volta em outra
    voz), e as das vozes novas e as do perfil ficam."""
    ana, bia = store.people.create_person("Ana"), store.people.create_person("Bia")
    mid = store.create_meeting("r", None, None, None)
    store.finish_meeting(mid, 60_000)
    old_job, new_job = store.jobs.enqueue(mid), store.jobs.enqueue(mid)
    old = store.segments.insert_clusters([ClusterInsert(mid, old_job, "R", "SPK_0", 5000, _vec(0), "wespeaker", ClusterStatus.UNNAMED)])
    new = store.segments.insert_clusters([ClusterInsert(mid, new_job, "R", "SPK_0", 5000, _vec(1), "wespeaker", ClusterStatus.UNNAMED)])
    review.confirm(store, old["SPK_0"], ana)
    review.confirm(store, old["SPK_0"], bia)                     # a correção grava também a revisão do perfil de Ana
    review.confirm(store, new["SPK_0"], ana)

    def revs(entity: str, entity_id: int) -> int:
        return int(store._q("SELECT COUNT(*) FROM revisions WHERE entity = ? AND entity_id = ?", (entity, entity_id))[0][0])

    profile_revs = store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'voice_profile'")[0][0]
    assert revs("cluster", old["SPK_0"]) == 2 and revs("cluster", new["SPK_0"]) == 1 and profile_revs == 1
    store.segments.swap_job(mid, new_job)
    assert revs("cluster", old["SPK_0"]) == 0 and revs("cluster", new["SPK_0"]) == 1
    assert store._q("SELECT COUNT(*) FROM revisions WHERE entity = 'voice_profile'")[0][0] == profile_revs
