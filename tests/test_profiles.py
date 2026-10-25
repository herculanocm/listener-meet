"""Perfis de voz, pessoas e segmentos (F2 L1): centroide só de cadastro e confirmação, Welford e limiar clipado,
segmentos em lote, troca atômica do reprocessamento e resolução de mesclas."""
from __future__ import annotations

import math

import numpy as np
import pytest

from listener.model import Attribution, ClusterStatus, EmbeddingSource, MeetingStatus, SegmentSource
from listener.store.profiles import calibrated_threshold, centroid_of, from_blob, normalize, to_blob, welford_update
from listener.store.segments import ClusterInsert, SegmentInsert, resolve_person


def _vec(i: int, dim: int = 8) -> np.ndarray:
    v = np.zeros(dim, np.float32)
    v[i % dim] = 1.0
    v[(i + 1) % dim] = 0.3
    return v


def test_welford_matches_numpy_and_threshold_is_clipped():
    xs = [0.71, 0.66, 0.80, 0.74, 0.69, 0.77]
    n, mean, std = 0, 0.0, 0.0
    for x in xs:
        n, mean, std = welford_update(n, mean, std, x)
    assert n == 6 and math.isclose(mean, float(np.mean(xs)), rel_tol=1e-9) and math.isclose(std, float(np.std(xs)), rel_tol=1e-9)
    assert calibrated_threshold(4, 0.7, 0.01) is None                                  # menos de 5 confirmações: global
    assert calibrated_threshold(5, 0.9, 0.01) == 0.75 and calibrated_threshold(5, 0.3, 0.0) == 0.45
    assert math.isclose(calibrated_threshold(6, 0.70, 0.04), 0.64)
    c = centroid_of([np.array([2.0, 0.0]), np.array([0.0, 2.0])])
    assert np.allclose(c, [math.sqrt(0.5), math.sqrt(0.5)]) and centroid_of([]) is None
    assert from_blob(to_blob(_vec(1))).dtype == np.float32 and np.array_equal(from_blob(to_blob(_vec(1))), _vec(1))
    assert from_blob(None).size == 0 and np.allclose(np.linalg.norm(normalize(_vec(2))), 1.0)


def test_profile_centroid_ignores_automatic_and_calibrates(store):
    ana = store.people.create_person("Ana", role="CFO")
    assert store.people.get_person(ana)["role"] == "CFO" and store.people.find_by_name("ana")["id"] == ana
    pid = store.profiles.confirm(ana, "wespeaker", _vec(0), similarity=None, meeting_id=None, source=EmbeddingSource.ENROLL)
    assert store.profiles.ensure_profile(ana, "wespeaker", 8) == pid                      # único por (pessoa, modelo)
    assert store.profiles.ensure_profile(ana, "outro-modelo", 8) != pid
    before = store.profiles.get_profile(pid)["centroid"].copy()
    store.profiles.add_embedding(pid, _vec(4), EmbeddingSource.AUTO, meeting_id=None, platform="teams")
    store.profiles.recompute_centroid(pid)
    p = store.profiles.get_profile(pid)
    assert np.allclose(p["centroid"], before) and p["n_samples"] == 1                    # automático não entra
    for i, sim in enumerate([0.70, 0.72, 0.68, 0.74]):
        store.profiles.confirm(ana, "wespeaker", _vec(i % 2), similarity=sim, meeting_id=None, platform="teams")
    p = store.profiles.get_profile(pid)
    assert p["n_samples"] == 5 and p["threshold"] is None and math.isclose(p["sim_mean"], 0.71)
    store.profiles.confirm(ana, "wespeaker", _vec(0), similarity=0.66, meeting_id=None)
    p = store.profiles.get_profile(pid)
    assert p["n_samples"] == 6 and p["threshold"] is not None and 0.45 <= p["threshold"] <= 0.75
    assert math.isclose(p["threshold"], max(0.45, min(0.75, p["sim_mean"] - 1.5 * p["sim_std"])))
    sources = [s for _, _, s in store.profiles.embeddings(pid)]
    assert sources.count("confirmacao") == 5 and sources.count("automatico") == 1 and sources.count("cadastro") == 1
    assert [x["id"] for x in store.profiles.list_profiles("wespeaker")] == [pid]
    assert p["calib_n"] == 5 and not hasattr(store.profiles, "merge_profiles")                   # R2-24: código que apagava saiu
    # R2-18: a média usa só as similaridades medidas (a 1ª confirmação, sem perfil, não entra como amostra)
    bia = store.people.create_person("Bia")
    store.profiles.confirm(bia, "wespeaker", _vec(3), similarity=None, meeting_id=None)
    store.profiles.confirm(bia, "wespeaker", _vec(3), similarity=0.9, meeting_id=None)
    store.profiles.confirm(bia, "wespeaker", _vec(3), similarity=0.5, meeting_id=None)
    pb = store.profiles.profile_for(bia, "wespeaker")
    assert pb["calib_n"] == 2 and math.isclose(pb["sim_mean"], 0.7) and pb["n_samples"] == 3
    assert [p["name"] for p in store.people.list_people()] == ["Ana", "Bia"]
    store.profiles.invalidate_cluster_embeddings(pb["id"], 999)                                   # nada com esse cluster: no-op
    assert store.profiles.profile_for(bia, "wespeaker")["n_samples"] == 3


def test_segments_in_batches_clusters_swap_and_merge_resolution(store):
    mid = store.create_meeting("r", "teams", None, None)
    store.finish_meeting(mid, 60_000)
    me = store.upsert_me("Eu", role="Gerente")
    old_job, new_job = store.jobs.enqueue(mid), store.jobs.enqueue(mid)
    ids_old = store.segments.insert_clusters([ClusterInsert(mid, old_job, "R", "SPK_0", 12_000, _vec(0), "wespeaker", ClusterStatus.AUTO,
                                                            person_id=None, similarity=0.7, margin=0.2)])
    n = store.segments.insert_segments(
        SegmentInsert(mid, old_job, "R", t * 1000, t * 1000 + 900, f"fala {t}", Attribution.PYANNOTE, SegmentSource.FINAL,
                      cluster="SPK_0", cluster_id=ids_old["SPK_0"]) for t in range(2500))
    assert n == 2500 and store.segments.count_segments(mid, old_job) == 2500                  # três lotes (1000, 1000, 500)
    seg_old = store.segments.list_segments(mid)[0]["id"]
    ana = store.people.create_person("Ana")
    pid = store.profiles.confirm(ana, "wespeaker", _vec(0), 0.7, mid, segment_id=seg_old)
    # job novo escreve os seus; os antigos continuam até a troca
    ids_new = store.segments.insert_clusters([
        ClusterInsert(mid, new_job, "R", "SPK_0", 11_000, _vec(0), "wespeaker", ClusterStatus.CONFIRMED, person_id=ana),
        ClusterInsert(mid, new_job, "R", "SPK_1", 2_000, _vec(5), "wespeaker", ClusterStatus.UNNAMED),
        ClusterInsert(mid, new_job, "L", "L", 20_000, None, None, ClusterStatus.CONFIRMED, person_id=me)])
    store.segments.insert_segments([
        SegmentInsert(mid, new_job, "R", 0, 900, "oi", Attribution.PYANNOTE, SegmentSource.FINAL, cluster="SPK_1",
                      cluster_id=ids_new["SPK_1"]),
        SegmentInsert(mid, new_job, "L", 1000, 1900, "olá", Attribution.CHANNEL, SegmentSource.FINAL, person_id=me,
                      role_at_time="Gerente")])
    store._x("INSERT INTO segments(meeting_id, channel, t_start_ms, t_end_ms, text, source) VALUES (?, 'L', 0, 10, 'ao vivo', 'live')",
             (mid,))
    assert store.segments.count_segments(mid) == 2503 and store.segments.latest_job_id(mid) == new_job
    result = store.segments.swap_job(mid, new_job)
    assert result == {"embeddings_freed": 1, "segments_deleted": 2500, "clusters_deleted": 1}        # job_id nulo fica (R2-22)
    assert store.get_meeting(mid)["status"] == MeetingStatus.PROCESSED
    assert [s["text"] for s in store.segments.list_segments(mid)] == ["oi", "ao vivo", "olá"]
    store._x("DELETE FROM segments WHERE job_id IS NULL")
    store.meetings.set_status(mid, MeetingStatus.REVIEWED)
    store.segments.swap_job(mid, new_job)
    assert store.get_meeting(mid)["status"] == MeetingStatus.REVIEWED                               # R2-11: não rebaixa
    store.segments.swap_job(mid, new_job, downgrade_reviewed=True)
    assert store.get_meeting(mid)["status"] == MeetingStatus.PROCESSED
    assert store.profiles.embeddings(pid)[0][0] and store._q("SELECT segment_id FROM voice_embeddings")[0][0] is None
    # mescla na revisão: SPK_1 é a mesma pessoa que SPK_0 -> resolve pela cadeia merged_into
    store.segments.update_cluster(ids_new["SPK_1"], merged_into=ids_new["SPK_0"], status=ClusterStatus.CONFIRMED)
    clusters = {c["id"]: c for c in store.segments.list_clusters(mid, new_job)}
    assert resolve_person(clusters[ids_new["SPK_1"]], clusters) == ana and resolve_person(clusters[ids_new["SPK_0"]], clusters) == ana
    store.segments.update_cluster(ids_new["SPK_0"], merged_into=ids_new["SPK_1"])                # ciclo acidental: não trava
    clusters = {c["id"]: c for c in store.segments.list_clusters(mid, new_job)}
    assert resolve_person(clusters[ids_new["SPK_1"]], clusters) in (ana, None)
    assert store.segments.set_segments_person(mid, ids_new["SPK_1"], ana, "CFO") == 1
    assert store.segments.list_segments(mid)[0]["role_at_time"] == "CFO"
    assert store.segments.get_cluster(ids_new["L"])["centroid"].size == 0 and store.segments.get_cluster(999) is None
    store.meetings.set_language(mid, "pt")
    store.meetings.set_language(mid, "en")
    assert store.get_meeting(mid)["language"] == "pt" and [m["id"] for m in store.meetings.list_by_status(MeetingStatus.PROCESSED)] == [mid]
    with pytest.raises(ValueError):
        store.segments.update_cluster(ids_new["L"], centroid=b"x")


def test_pipeline_settings_and_hf_provider(store):
    from listener.config.defaults import HF_PROVIDER_ID, PIPELINE_KEYS, SPECS

    assert "pipeline.paused_until" not in PIPELINE_KEYS and "pipeline.threshold" in PIPELINE_KEYS
    assert all(SPECS[k].phase == "F2" for k in PIPELINE_KEYS)
    assert store.get(SPECS["pipeline.auto_start"]) is True and store.get(SPECS["pipeline.paused_manual"]) is False
    store.set_setting("pipeline.paused_manual", True)
    assert store.get(SPECS["pipeline.paused_manual"]) is True and store.get(SPECS["pipeline.torch_threads"]) >= 1
    store.set_setting("pipeline.threshold", "0.9")
    assert store.get(SPECS["pipeline.threshold"]) == 0.60                                     # fora da faixa: padrão
    snap = store.settings.settings_with_prefix("pipeline.")
    assert snap["pipeline.threshold"] == "0.9" and snap["pipeline.whisper_model"] == "large-v3-turbo"
    assert store.settings.provider_key(HF_PROVIDER_ID) == ""
    store.upsert_provider(HF_PROVIDER_ID, "huggingface", api_key="hf_x")
    assert store.settings.provider_key(HF_PROVIDER_ID) == "hf_x" and store.get_provider(HF_PROVIDER_ID)["kind"] == "huggingface"
