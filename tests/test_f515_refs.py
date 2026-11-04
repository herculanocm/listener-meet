"""F5.15 L1: as referências de voz no app. A carga (`load_profile_refs`) com uma pessoa de duas condições e o limiar
calculado das amostras, não da coluna (A-03); o espelho em `voice_profiles` e o desfazer que tira a amostra também do
limiar (o Welford da F2 não tirava); o job ponta a ponta com o pipeline falso, em que a pessoa é reconhecida sozinha pela
segunda condição (B1) e, com `max_refs = 1`, volta a média única; o ao vivo e a nota da revisão pelas referências; as duas
configurações no `JobConfig` e no snapshot do worker."""
from __future__ import annotations

import io
import logging
from pathlib import Path

import numpy as np
import pytest

from listener.capture.clock import ManualClock
from listener.live import proc
from listener.model import ClusterStatus, EmbeddingSource, JobStatus
from listener.pipeline import review
from listener.pipeline.profiles import load_profile_refs
from listener.pipeline.review_suggest import similarity_to
from listener.pipeline.types import JobConfig
from listener.store import Store
from listener.store.profiles import MAX_REFS, REF_COS, Calibration, ProfileRow, calibrate, normalize
from listener.store.segments import ClusterInsert
from tests.fake_live import FakeEngines, tone
from tests.fake_pipeline import FakeDeps, ToneEmbedder
from tests.fake_platform import FakePlatform
from tests.test_live_names import _input
from tests.test_runner import CFG, SCRIPT, _record, _run

SR = 16000
MODEL = "fake-tone-256"


def _tone_vec(freq: float) -> np.ndarray:
    return ToneEmbedder().embed(tone(1.0, freq), SR)


def _two_conditions(store: Store) -> int:
    """A Ana com duas amostras numa condição (550 Hz) e uma na nova (330 Hz, o 2º tom do R na gravação falsa): a média única
    fica a 1/raiz de 5 (~0,45) da nova, abaixo do limiar de 0,60; a referência da nova, a ~1,0."""
    ana = store.people.create_person("Ana", role="CFO")
    for freq in (550, 550, 330):
        store.profiles.confirm(ana, MODEL, _tone_vec(freq), None)
    return ana


def test_load_profile_refs_two_conditions_threshold_from_the_samples_and_log(store: Store, caplog: pytest.LogCaptureFixture):
    ana = _two_conditions(store)
    bia = store.people.create_person("Bia")
    rng = np.random.default_rng(1)
    for _ in range(6):                                                                        # uma condição, 6 notas ~0,99
        store.profiles.confirm(bia, MODEL, normalize(_tone_vec(440) + rng.normal(scale=0.01, size=256).astype(np.float32)), None)
    store._x("UPDATE voice_profiles SET threshold = 0.99 WHERE model = ?", (MODEL,))           # a coluna é só espelho (A-03)
    caio = store.people.create_person("Caio")
    store.profiles.add_embedding(store.profiles.ensure_profile(caio, MODEL, 256), np.ones(8, np.float32), EmbeddingSource.CONFIRM)
    with caplog.at_level(logging.DEBUG, logger="listener.pipeline.profiles"):
        refs = load_profile_refs(store, MODEL, ref_cos=REF_COS, max_refs=MAX_REFS)
    by = {r.person_id: r for r in refs}
    assert list(by) == [ana, bia]                                                             # o Caio, só com outra dimensão, fora
    assert by[ana].refs.shape == (2, 256) and by[ana].sizes == (2, 1) and by[ana].threshold is None and by[ana].name == "Ana"
    assert float(by[ana].refs[1] @ _tone_vec(330)) > 0.99                                     # a condição nova tem referência
    assert by[bia].refs.shape == (1, 256) and by[bia].sizes == (6,) and by[bia].threshold == 0.75
    assert "2 pessoa(s), 3 referência(s), 1 amostra(s) ignorada(s)" in caplog.text and "Ana" not in caplog.text
    single = load_profile_refs(store, MODEL, ref_cos=REF_COS, max_refs=1, person_ids=[ana])
    assert [r.person_id for r in single] == [ana] and single[0].sizes == (3,)
    assert float(single[0].refs[0] @ _tone_vec(330)) == pytest.approx(1 / np.sqrt(5), abs=0.01)   # a média única
    assert load_profile_refs(store, MODEL, ref_cos=REF_COS, max_refs=MAX_REFS, person_ids=[caio]) == []


def test_refresh_mirrors_each_confirmation_and_undo_takes_the_sample_out_of_the_threshold(store: Store):
    """Com `max_refs = 1` (um grupo só), as 7 vozes confirmadas dão 7 notas; a mais longe puxa o limiar para dentro da faixa.
    Ignorar essa voz tira a amostra do perfil e também do limiar: o espelho é o cálculo das 6 que ficam."""
    store.set_setting("pipeline.max_refs", "1")                                                 # a revisão lê a cada ação
    ana = store.people.create_person("Ana")
    mid = store.create_meeting("r", "ms-teams", None, None)
    job = store.jobs.enqueue(mid)
    rng, base = np.random.default_rng(7), _tone_vec(220)
    vecs = [normalize(base + rng.normal(scale=0.03, size=256).astype(np.float32)) for _ in range(6)]
    vecs.append(normalize(base + 1.7 * _tone_vec(660)))                                        # cosseno ~0,5 com as outras
    ids = store.segments.insert_clusters([ClusterInsert(mid, job, "R", f"SPK_{i}", 20_000, v, MODEL, ClusterStatus.UNNAMED)
                                          for i, v in enumerate(vecs)])
    for i in range(7):
        review.confirm(store, ids[f"SPK_{i}"], ana)

    def mirror() -> tuple[ProfileRow | None, Calibration]:
        active = store.profiles.active_samples(MODEL)[0].vectors
        return store.profiles.profile_for(ana, MODEL), calibrate(active, np.zeros(len(active), np.intp))

    p, cal = mirror()
    assert (p["n_samples"], p["calib_n"]) == (7, 7) and p["threshold"] == cal.threshold and 0.45 < cal.threshold < 0.70
    assert p["sim_mean"] == pytest.approx(cal.mean) and p["sim_std"] == pytest.approx(cal.std)
    review.ignore(store, ids["SPK_6"])
    p, cal = mirror()
    assert (p["n_samples"], p["calib_n"]) == (6, 6) and p["threshold"] == cal.threshold == 0.75   # a nota dela saiu da conta
    assert np.allclose(p["centroid"], normalize(np.mean(np.stack(vecs[:6]), axis=0)), atol=1e-6)


def test_job_recognizes_the_second_condition_and_max_refs_one_goes_back_to_the_mean(store: Store, tmp_path: Path):
    """B1: na reunião seguinte, a voz de 330 Hz (11 s de fala) é da Ana sozinha (`auto`) pela referência da condição nova;
    com `max_refs = 1`, a média única fica a ~0,45, abaixo do limiar, e a voz fica sem nome com a Ana de sugestão."""
    store.upsert_me("Eu")
    ana = _two_conditions(store)
    one = JobConfig(**{**CFG.to_dict(), "max_refs": 1})
    for i, (config, status, similarity) in enumerate(((CFG, ClusterStatus.AUTO, 1.0), (one, ClusterStatus.UNNAMED, 1 / np.sqrt(5)))):
        (tmp_path / f"p{i}").mkdir()
        platform = FakePlatform(tmp_path / f"p{i}", clock=ManualClock(), r_format=(16000, 1), system_format=(16000, 1))
        mid = _record(platform, store, seconds_each=11.0)
        store.jobs.enqueue(mid)
        _, done = _run(store, FakeDeps(SCRIPT), config=config)
        assert done == JobStatus.DONE
        voice = next(c for c in store.segments.list_clusters(mid) if c["channel"] == "R" and float(c["centroid"] @ _tone_vec(330)) > 0.9)
        assert voice["speech_ms"] >= 10_000 and voice["status"] == status and voice["person_id"] == ana
        assert voice["similarity"] == pytest.approx(similarity, abs=0.01)


def test_live_names_the_second_condition_by_its_reference(tmp_path: Path):
    db = tmp_path / "db"
    with Store(db) as s:
        s.upsert_me("Eu", role="Gerente")
        ana = _two_conditions(s)
        meetings = [s.create_meeting(f"ao vivo {i}", None, None, None) for i in range(2)]
    data = _input([("R", tone(1.5, 330))])
    got = []
    for mid in meetings:
        with Store.open_worker(db) as w:
            assert proc.serve(io.BytesIO(data), io.BytesIO(), w, mid, FakeEngines(), clock=lambda: 1010.0, names_sync=True) == 0
            got.append([(c["person_id"], c["label"]) for c in w.captions.list_for_meeting(mid)])
        with Store(db) as s:
            s.set_setting("pipeline.max_refs", "1")                                             # vale na próxima gravação
    assert got == [[(ana, "Ana — CFO")], [(None, "Participante")]]                              # a média única não passa


def test_review_similarity_is_by_the_person_references(store: Store):
    ana = _two_conditions(store)
    mid = store.create_meeting("r", None, None, None)
    job = store.jobs.enqueue(mid)
    ids = store.segments.insert_clusters([ClusterInsert(mid, job, "R", "SPK_0", 20_000, _tone_vec(330), MODEL, ClusterStatus.UNNAMED),
                                          ClusterInsert(mid, job, "R", "SPK_1", 20_000, None, None, ClusterStatus.UNNAMED)])
    voice = store.segments.get_cluster(ids["SPK_0"])
    assert similarity_to(store, voice, ana) == pytest.approx(1.0, abs=1e-3)           # a referência da condição nova
    store.set_setting("pipeline.max_refs", "1")                                                 # a revisão lê a cada ação
    assert similarity_to(store, voice, ana) == pytest.approx(1 / np.sqrt(5), abs=0.01)
    assert similarity_to(store, voice, store.people.create_person("Bia")) is None       # sem perfil
    assert similarity_to(store, store.segments.get_cluster(ids["SPK_1"]), ana) is None  # voz sem vetor


def test_job_config_and_worker_snapshot_carry_the_two_settings(store: Store):
    from listener.pipeline.worker import config_snapshot

    cfg = JobConfig.from_settings({"pipeline.ref_cos": "0.7", "pipeline.max_refs": "3"})
    assert (cfg.ref_cos, cfg.max_refs) == (0.7, 3) and cfg.to_dict()["ref_cos"] == 0.7
    bad = JobConfig.from_settings({"pipeline.ref_cos": "0.5", "pipeline.max_refs": "0"})
    assert (bad.ref_cos, bad.max_refs) == (REF_COS, MAX_REFS) == (JobConfig().ref_cos, JobConfig().max_refs)
    store.set_setting("pipeline.ref_cos", "0.85")
    store.set_setting("pipeline.max_refs", "2")
    snap = JobConfig.from_json(config_snapshot(store))                                          # o worker lê no próximo job
    assert (snap.ref_cos, snap.max_refs) == (0.85, 2)
