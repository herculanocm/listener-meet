"""`JobRunner` de ponta a ponta sobre uma gravação da `FakePlatform` (F2 L3): dois tons no R viram dois clusters, L é o
usuário; retomada após queda pula as janelas já gravadas; cancel e lease entre janelas; reprocessar transporta decisões e
falha mantém o resultado antigo; erro permanente não conta tentativa; nada do app importa torch."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

import numpy as np
import pytest

from listener.capture.recorder import Recorder, SourceSpec
from listener.model import ClusterStatus, JobKind, JobStatus, MeetingStatus
from listener.pipeline.base import PermanentError
from listener.pipeline.runner import JobRunner, build_prompt, update_rtf
from listener.pipeline.types import JobConfig, Word
from listener.store import Store
from tests.conftest import make_config
from tests.fake_platform import FakePlatform
from tests.fake_pipeline import FakeDeps

CFG = JobConfig(diar_window_s=2, whisper_window_s=1, whisper_overlap_s=0, diar_overlap_s=1, silence_rms=0.01, threshold=0.6, margin=0.05)
SCRIPT = {"R": [(0.2, 1.4, "bom dia pessoal"), (1.6, 2.9, "tudo certo então")], "L": [(0.3, 1.2, "oi gente"), (2.0, 2.8, "sim claro")]}
T0 = datetime.fromisoformat("2026-10-24T10:00:00.000-03:00")


@pytest.fixture
def platform(tmp_path, clock):
    """Tons a 16 kHz mono nos dois canais: sem reamostragem bloco a bloco, a gravação falsa custa 10x menos (a reamostragem
    é coberta em test_pipeline_core e test_recorder)."""
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _record(platform: FakePlatform, store: Store, seconds_each: float = 1.5) -> int:
    """Reunião de 3 s: R com 220 Hz (app) e depois 330 Hz (sistema); L com 440 Hz o tempo todo."""
    mid = store.create_meeting("Planejamento", "meeting", None, "Ana, Bia")
    rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
    rec.start()
    platform.pump(seconds_each)
    rec.switch_source(SourceSpec("system", label="Sistema"))
    platform.pump(seconds_each)
    rec.stop()
    return mid


def _run(store: Store, deps: FakeDeps, job_id: int | None = None, **kw) -> tuple[JobRunner, JobStatus]:
    job = store.jobs.claim(pid=1) if job_id is None else store.jobs.get(job_id)
    assert job is not None
    runner = JobRunner(store, deps, job, config=kw.pop("config", CFG), heartbeat_thread=False, **kw)
    return runner, runner.run()


def test_runner_end_to_end_two_speakers_in_r_and_user_in_l(platform, store):
    me = store.upsert_me("Eu", role="Gerente")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    deps = FakeDeps(SCRIPT)
    runner, status = _run(store, deps)
    assert status == JobStatus.DONE and runner.language == "pt"
    job = store.jobs.get(job_id)
    assert job["status"] == "concluído" and job["progress"] == 1.0 and job["worker_pid"] is None
    timings = json.loads(job["timings_json"])
    assert {"diarize", "embed", "transcribe_r", "transcribe_l", "segments"} <= set(timings)
    assert timings["diarize"]["audio_s"] == pytest.approx(4.0, abs=0.01) and timings["transcribe_r"]["speech_s"] > 0   # 2 janelas de 2 s
    assert timings["transcribe_r"]["model"] == "fake:script" and timings["embed"]["model"] == "fake-tone-256"
    m = store.get_meeting(mid)
    assert m["status"] == MeetingStatus.PROCESSED and m["language"] == "pt"
    clusters = store.segments.list_clusters(mid)
    r = [c for c in clusters if c["channel"] == "R"]
    l = [c for c in clusters if c["channel"] == "L"]
    assert len(r) == 2 and {c["status"] for c in r} == {ClusterStatus.UNNAMED} and all(c["person_id"] is None for c in r)
    assert all(1000 <= c["speech_ms"] <= 2000 and c["centroid"].shape == (256,) and c["model"] == "fake-tone-256" for c in r)
    assert float(r[0]["centroid"] @ r[1]["centroid"]) < 0.1                       # tons diferentes: vozes diferentes
    assert len(l) == 1 and l[0]["status"] == ClusterStatus.CONFIRMED and l[0]["person_id"] == me
    segs = store.segments.list_segments(mid)
    texts = [(s["channel"], s["text"]) for s in segs]
    assert texts == [("R", "bom dia pessoal"), ("L", "oi gente sim claro"), ("R", "tudo certo então")]   # pausa de 0,8 s não corta
    r_segs = [s for s in segs if s["channel"] == "R"]
    assert {s["cluster_id"] for s in r_segs} == {c["id"] for c in r} and all(s["attribution"] == "pyannote" for s in r_segs)
    assert all(s["person_id"] is None and s["source"] == "final" for s in r_segs)
    l_seg = next(s for s in segs if s["channel"] == "L")
    assert l_seg["person_id"] == me and l_seg["attribution"] == "canal" and l_seg["role_at_time"] == "Gerente"
    assert json.loads(l_seg["words_json"])[0][2] == "oi" and l_seg["t_start_ms"] == 300
    assert store.jobs.artifacts(job_id, "diarize") == [] and store._q("SELECT COUNT(*) FROM job_artifacts")[0][0] == 0
    types = [e["type"] for e in store.list_events(mid)]
    assert types.index("job_start") < types.index("job_step") < types.index("job_done")
    emb = store._q("SELECT source, platform FROM voice_embeddings")
    assert len(emb) == 1 and emb[0][0] == "automatico" and emb[0][1] == "meeting"               # P3: reforço do usuário
    prof = store.profiles.list_profiles("fake-tone-256")
    assert len(prof) == 1 and prof[0]["person_id"] == me and prof[0]["n_samples"] == 0        # fora do centroide
    rtf = json.loads(store.get_setting("pipeline.rtf_json"))
    assert "diarize:fake:tone" in rtf and "transcribe_r:fake:script" in rtf
    # chamadas: idioma None só na primeira janela com fala; prompt com título e participantes; L também transcrito
    first = next(c for c in deps.scripted.calls if c[0] == "R")
    assert first[2] is None and "Planejamento" in first[3] and "Ana, Bia" in first[3]
    assert all(c[2] == "pt" for c in deps.scripted.calls[1:]) and "pessoal tudo" in deps.scripted.calls[2][3]   # contexto anterior
    assert {c[0] for c in deps.scripted.calls} == {"R", "L"}
    assert deps.tones.calls[0][1:] == (1, 3)                                                     # min 1, max n + 1


def test_runner_resumes_after_crash_skipping_saved_windows(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    deps = FakeDeps(SCRIPT, fail_once_at={("R", 2)})
    _, status = _run(store, deps)
    assert status == JobStatus.ERROR
    job = store.jobs.get(job_id)
    assert job["status"] == "erro" and job["attempts"] == 1 and "queda simulada" in job["error"] and job["step"] == "transcribe_r"
    assert store.jobs.artifact_indexes(job_id, "diarize") == {0, 1} and store.jobs.artifact_indexes(job_id, "transcribe_r") == {0, 1}
    assert len(store.segments.list_clusters(mid, job_id)) == 2 and store.segments.count_segments(mid) == 0
    assert store.get_meeting(mid)["status"] == "gravada" and store.get_meeting(mid)["language"] == "pt"
    assert store.jobs.retry(job_id)
    tone_calls = len(deps.tones.calls)
    _, status = _run(store, deps)
    assert status == JobStatus.DONE and store.jobs.get(job_id)["attempts"] == 0                 # "tentar de novo" zera
    assert len(deps.tones.calls) == tone_calls                                                   # diarização não refeita
    r_calls = [c for c in deps.scripted.calls if c[0] == "R"]
    assert [c[1] for c in r_calls] == [0, 1, 2, 2] and r_calls[-1][2] == "pt"                   # só a janela 2 foi refeita
    assert store.segments.count_segments(mid) > 0 and store.get_meeting(mid)["status"] == "processada"
    assert store._q("SELECT COUNT(*) FROM job_artifacts")[0][0] == 0
    assert store._q("SELECT COUNT(*) FROM voice_embeddings WHERE source = 'automatico'")[0][0] == 1      # R2-06: um por reunião


def test_runner_cancel_between_windows_and_permanent_error(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    job = store.jobs.claim(pid=1)
    store.jobs.request_cancel(job_id)
    status = JobRunner(store, FakeDeps(SCRIPT), job, config=CFG, heartbeat_thread=False).run()
    assert status == JobStatus.CANCELLED and store.jobs.get(job_id)["status"] == "cancelado"
    assert store.segments.list_clusters(mid) == [] and store._q("SELECT COUNT(*) FROM job_artifacts")[0][0] == 0
    assert "job_cancel" in [e["type"] for e in store.list_events(mid)] and store.get_meeting(mid)["status"] == "gravada"

    def no_token(progress):
        raise PermanentError("token do Hugging Face ausente: informe nas Configurações")

    job2 = store.jobs.enqueue(mid)
    _, status = _run(store, FakeDeps(SCRIPT, prepare=no_token))
    row = store.jobs.get(job2)
    assert status == JobStatus.ERROR and row["attempts"] == 0 and "token" in row["error"]
    ev = [e for e in store.list_events(mid) if e["type"] == "job_error"][-1]
    assert ev["data"]["permanent"] is True
    store.jobs.retry(job2)
    deps = FakeDeps(SCRIPT)
    _, status = _run(store, deps)
    assert status == JobStatus.DONE and deps.prepared


def test_runner_waits_for_recording_lease_and_manual_pause(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    clock = {"now": T0}
    seen: list[tuple[str, str | None]] = []

    def sleep(seconds: float) -> None:
        row = store.jobs.get(job_id)
        seen.append((row["status"], row["step"]))
        clock["now"] += timedelta(seconds=seconds)

    store.set_setting("pipeline.paused_until", (T0 + timedelta(minutes=10)).isoformat(timespec="milliseconds"))
    _, status = _run(store, FakeDeps(SCRIPT), now=lambda: clock["now"], sleep=sleep, wait_poll_s=300.0)
    assert status == JobStatus.DONE
    assert seen and all(s == ("pausado", "waiting") for s in seen) and len(seen) == 2          # 10 min em passos de 5
    assert clock["now"] >= T0 + timedelta(minutes=10) and store.jobs.get(job_id)["status"] == "concluído"
    # pausa manual: o laço espera até a chave cair; a lease vencida não segura
    job2 = store.jobs.enqueue(mid)
    store.set_setting("pipeline.paused_manual", True)
    polls: list[int] = []

    def release(seconds: float) -> None:
        polls.append(1)
        if len(polls) == 2:
            store.set_setting("pipeline.paused_manual", False)

    _, status = _run(store, FakeDeps(SCRIPT), now=lambda: clock["now"], sleep=release)
    assert status == JobStatus.DONE and len(polls) == 2
    # com `pause_on_record` desligado a lease é ignorada
    job3 = store.jobs.enqueue(mid)
    store.set_setting("pipeline.paused_until", (clock["now"] + timedelta(minutes=10)).isoformat(timespec="milliseconds"))
    cfg = JobConfig(**{**CFG.to_dict(), "pause_on_record": False})
    _, status = _run(store, FakeDeps(SCRIPT), config=cfg, now=lambda: clock["now"], sleep=lambda s: pytest.fail("não devia esperar"))
    assert status == JobStatus.DONE and store.jobs.get(job3)["status"] == "concluído" and job2 != job3


def test_reprocess_transports_decisions_and_failure_keeps_old_results(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    store.jobs.enqueue(mid)
    _run(store, FakeDeps(SCRIPT))
    old = store.segments.list_clusters(mid)
    old_r = sorted((c for c in old if c["channel"] == "R"), key=lambda c: c["id"])
    ana = store.people.create_person("Ana", role="CFO")
    # revisão: o 1º cluster do R é a Ana (confirmação grava o vetor); o 2º é ignorado
    store.segments.update_cluster(old_r[0]["id"], person_id=ana, status=ClusterStatus.CONFIRMED)
    store.segments.set_segments_person(mid, old_r[0]["id"], ana, "CFO")
    seg_id = next(s["id"] for s in store.segments.list_segments(mid) if s["cluster_id"] == old_r[0]["id"])
    store.profiles.confirm(ana, "fake-tone-256", old_r[0]["centroid"], 0.0, mid, segment_id=seg_id)
    store.segments.update_cluster(old_r[1]["id"], status=ClusterStatus.IGNORED)
    old_seg_ids = {s["id"] for s in store.segments.list_segments(mid)}
    # reprocessar que falha: tudo o que existia continua
    bad = store.jobs.enqueue(mid, JobKind.REPROCESS)
    _, status = _run(store, FakeDeps(SCRIPT, fail_once_at={("L", 0)}))
    assert status == JobStatus.ERROR and store.jobs.get(bad)["status"] == "erro"
    assert {s["id"] for s in store.segments.list_segments(mid)} == old_seg_ids
    assert {c["id"] for c in store.segments.list_clusters(mid)} > {c["id"] for c in old}      # os novos já existem, os antigos ficam
    assert store.get_meeting(mid)["status"] == "processada"
    # tentar de novo: conclui, transporta as decisões e troca atomicamente
    store.jobs.retry(bad)
    _, status = _run(store, FakeDeps(SCRIPT), job_id=None)
    assert status == JobStatus.DONE
    new = store.segments.list_clusters(mid)
    assert {c["job_id"] for c in new} == {bad} and len(new) == 3
    new_r = {c["cluster"]: c for c in new if c["channel"] == "R"}
    ana_cluster = next(c for c in new_r.values() if float(c["centroid"] @ old_r[0]["centroid"]) > 0.9)
    other = next(c for c in new_r.values() if c is not ana_cluster)
    assert ana_cluster["status"] == ClusterStatus.CONFIRMED and ana_cluster["person_id"] == ana
    assert other["status"] == ClusterStatus.IGNORED
    segs = store.segments.list_segments(mid)
    assert {s["job_id"] for s in segs} == {bad} and not ({s["id"] for s in segs} & old_seg_ids)
    ana_segs = [s for s in segs if s["cluster_id"] == ana_cluster["id"]]
    assert ana_segs and all(s["person_id"] == ana and s["role_at_time"] == "CFO" for s in ana_segs)
    assert store._q("SELECT segment_id FROM voice_embeddings WHERE source = 'confirmacao'")[0][0] is None   # solto, não apagado
    assert store.profiles.profile_for(ana, "fake-tone-256")["n_samples"] == 1
    assert store._q("SELECT COUNT(*) FROM voice_embeddings WHERE source = 'automatico'")[0][0] == 1      # R2-06: reprocessar não soma
    assert store._q("SELECT COUNT(*) FROM revisions WHERE field = 'transporte'")[0][0] == 0     # tudo casou


def test_reprocess_reports_what_could_not_be_transported(platform, store):
    store.upsert_me("Eu")
    mid = _record(platform, store)
    store.jobs.enqueue(mid)
    _run(store, FakeDeps(SCRIPT))
    old_r = [c for c in store.segments.list_clusters(mid) if c["channel"] == "R"]
    ana = store.people.create_person("Ana")
    store.segments.update_cluster(old_r[0]["id"], person_id=ana, status=ClusterStatus.CONFIRMED)
    store.segments.update_cluster(old_r[0]["id"], speech_ms=1)
    store._x("UPDATE clusters SET centroid = ? WHERE id = ?", (np.full(256, 1 / 16, np.float32).tobytes(), old_r[0]["id"]))   # outra "voz"
    store.jobs.enqueue(mid, JobKind.REPROCESS)
    _, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE
    rev = store._q("SELECT entity, entity_id, field, before FROM revisions WHERE field = 'transporte'")
    assert len(rev) == 1 and rev[0]["entity_id"] == old_r[0]["id"] and rev[0]["before"] == f"confirmado:{ana}"
    assert all(c["person_id"] is None for c in store.segments.list_clusters(mid) if c["channel"] == "R")


def test_prompt_and_rtf_helpers():
    prev = tuple(Word(i, i + 1, f"w{i}") for i in range(100))
    p = build_prompt("glossário " * 200, "Ana, Bia", "Título", prev)
    assert p.count("glossário") == 110 and "Ana, Bia" not in p and p.endswith("w99") and "w39" not in p and "w40" in p
    assert build_prompt("", None, None, ()) == "" and build_prompt("", "Ana", "T", ()) == "Ana T"
    from listener.pipeline.runner import StepTiming

    out = json.loads(update_rtf("{}", {"transcribe_r": StepTiming(100, 50, 40, "m"), "diarize": StepTiming(100, 0, 80, "d"),
                                       "segments": StepTiming(0, 0, 1, "")}))
    assert out == {"transcribe_r:m": 0.8, "diarize:d": 0.8}
    out2 = json.loads(update_rtf(json.dumps(out), {"diarize": StepTiming(100, 0, 40, "d")}))
    assert out2["diarize:d"] == pytest.approx(0.3 * 0.4 + 0.7 * 0.8) and update_rtf("lixo", {}) == "{}"


def test_app_modules_do_not_import_models():
    """Regra dura da F2: nada que o app importa puxa torch, whisper.py, pyannote.py ou models.py."""
    import listener.pipeline.runner as runner_mod
    import listener.ui.controller as controller_mod

    assert runner_mod.__name__ == "listener.pipeline.runner" and controller_mod.__name__ == "listener.ui.controller"
    heavy = {"torch", "faster_whisper", "pyannote", "pyannote.audio", "ctranslate2", "listener.pipeline.whisper",
             "listener.pipeline.pyannote", "listener.pipeline.models"}
    assert not (heavy & set(sys.modules)), sorted(heavy & set(sys.modules))


def test_crash_between_segment_batches_leaves_nothing_and_retry_rewrites_all(platform, store, monkeypatch):
    """R2-03: os lotes de segmentos ficam numa só transação; uma queda no 2º lote não deixa parciais e a retomada regrava tudo."""
    from listener.store import segments as segments_mod

    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)
    monkeypatch.setattr(segments_mod, "BATCH", 1)
    real_batched = segments_mod.batched
    state = {"fail": True}

    def flaky_batched(rows, n):
        for i, batch in enumerate(real_batched(rows, n)):
            if state["fail"] and i == 1:
                raise RuntimeError("queda entre lotes")
            yield batch

    monkeypatch.setattr(segments_mod, "batched", flaky_batched)
    _, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.ERROR and "queda entre lotes" in store.jobs.get(job_id)["error"]
    assert store.segments.count_segments(mid) == 0                                           # nada parcial
    assert store.jobs.artifact_indexes(job_id, "transcribe_r") and store.get_meeting(mid)["status"] == "gravada"
    state["fail"] = False
    store.jobs.retry(job_id)
    _, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE and store.segments.count_segments(mid) == 3 and store.get_meeting(mid)["status"] == "processada"


def test_confirmation_during_transcription_reaches_segments(platform, store):
    """R2-04 (P1): o usuário confirma uma voz enquanto o Whisper roda; os segmentos nascem com a pessoa e o cargo."""
    from listener.pipeline import review

    store.upsert_me("Eu", role="Gerente")
    mid = _record(platform, store)
    store.jobs.enqueue(mid)
    done: list[int] = []

    def user_acts(window):
        if window.channel == "L" and not done:
            items = review.review_items(store, mid)
            ana = store.people.create_person("Ana", role="CFO")
            review.confirm(store, items[0].cluster_id, ana)
            done.append(items[0].cluster_id)

    _, status = _run(store, FakeDeps(SCRIPT, before=user_acts))
    assert status == JobStatus.DONE and done
    segs = [s for s in store.segments.list_segments(mid) if s["cluster_id"] == done[0]]
    ana = store.people.find_by_name("Ana")["id"]
    assert segs and all(s["person_id"] == ana and s["role_at_time"] == "CFO" for s in segs)
    assert store.segments.get_cluster(done[0])["status"] == ClusterStatus.CONFIRMED


def test_transport_does_not_overwrite_decision_taken_during_reprocess(platform, store):
    """R2-05: decisão nova (feita durante o reprocessamento) vence a transportada; o conflito fica em revisions."""
    from listener.pipeline import review

    store.upsert_me("Eu")
    mid = _record(platform, store)
    store.jobs.enqueue(mid)
    _run(store, FakeDeps(SCRIPT))
    old_items = review.review_items(store, mid)
    ana = review.new_person(store, old_items[0].cluster_id, "Ana")
    old_centroid = store.segments.get_cluster(old_items[0].cluster_id)["centroid"]
    bia = store.people.create_person("Bia")
    rep = store.jobs.enqueue(mid, JobKind.REPROCESS)
    acted: list[int] = []

    def user_acts(window):
        if window.channel == "L" and not acted:                                             # clusters novos já gravados (P1)
            new = [c for c in store.segments.list_clusters(mid, rep) if c["channel"] == "R"]
            target = max(new, key=lambda c: float(c["centroid"] @ old_centroid))
            review.confirm(store, int(target["id"]), bia)                                  # "esta voz é a Bia", corrigindo a Ana
            acted.append(int(target["id"]))

    _, status = _run(store, FakeDeps(SCRIPT, before=user_acts))
    assert status == JobStatus.DONE and acted
    c = store.segments.get_cluster(acted[0])
    assert c["status"] == ClusterStatus.CONFIRMED and c["person_id"] == bia and c["person_id"] != ana
    assert all(s["person_id"] == bia for s in store.segments.list_segments(mid) if s["cluster_id"] == acted[0])
    rev = store._q("SELECT entity_id, before, after FROM revisions WHERE field = 'transporte_conflito'")
    assert len(rev) == 1 and rev[0]["entity_id"] == acted[0]                                 # o L (mesma decisão) não anota
    assert rev[0]["before"] == f"confirmado:{ana}" and rev[0]["after"] == f"confirmado:{bia}"


def test_cancel_removes_undecided_clusters_but_keeps_decided_ones_with_stitch(platform, store):
    """R2-10: cancelar sem decisão limpa clusters e artefatos; com decisão humana, clusters e costura ficam para a retomada."""
    from listener.pipeline import review

    store.upsert_me("Eu")
    mid = _record(platform, store)
    job_id = store.jobs.enqueue(mid)

    def cancel_on_l(window):
        if window.channel == "L":
            store.jobs.request_cancel(job_id)

    _, status = _run(store, FakeDeps(SCRIPT, before=cancel_on_l))
    assert status == JobStatus.CANCELLED and store.segments.list_clusters(mid) == [] and review.review_items(store, mid) == []
    assert store._q("SELECT COUNT(*) FROM job_artifacts")[0][0] == 0
    assert store.jobs.retry(job_id)
    _, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE and len(store.segments.list_clusters(mid, job_id)) == 3
    # segunda reunião: o usuário confirma uma voz e então cancela
    mid2 = _record(type(platform)(platform.tmp), store)
    job2 = store.jobs.enqueue(mid2)
    ana = store.people.create_person("Ana")
    acted: list[int] = []

    def confirm_then_cancel(window):
        if window.channel == "L" and not acted:
            items = review.review_items(store, mid2)
            review.confirm(store, items[0].cluster_id, ana)
            acted.append(items[0].cluster_id)
            store.jobs.request_cancel(job2)

    _, status = _run(store, FakeDeps(SCRIPT, before=confirm_then_cancel))
    assert status == JobStatus.CANCELLED
    kept = store.segments.list_clusters(mid2, job2)
    assert len(kept) == 2 and store.jobs.artifact_indexes(job2, "stitch") == {0}            # o L do usuário nasce nos segmentos
    assert store._q("SELECT COUNT(*) FROM job_artifacts WHERE step != 'stitch'")[0][0] == 0
    assert review.clip_range(store, acted[0]) is not None                                   # trecho de 5 s pela costura
    assert store.jobs.retry(job2)
    _, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE and len(store.segments.list_clusters(mid2, job2)) == 3
    c = store.segments.get_cluster(acted[0])
    assert c["status"] == ClusterStatus.CONFIRMED and c["person_id"] == ana
    assert all(s["person_id"] == ana for s in store.segments.list_segments(mid2) if s["cluster_id"] == acted[0])


def test_review_finished_during_transcription_is_not_downgraded(platform, store):
    """R2-11: "Concluir" durante o Whisper deixa `revisada`; só vozes novas pendentes num reprocessamento voltam a `processada`."""
    from listener.pipeline import review

    store.upsert_me("Eu")
    mid = _record(platform, store)
    store.jobs.enqueue(mid)
    done: list[int] = []

    def user_finishes(window):
        if window.channel == "L" and not done:
            ana = store.people.create_person("Ana")
            for it in review.review_items(store, mid):
                review.confirm(store, it.cluster_id, ana)
            review.finish(store, mid)
            done.append(1)

    _, status = _run(store, FakeDeps(SCRIPT, before=user_finishes))
    assert status == JobStatus.DONE and store.get_meeting(mid)["status"] == MeetingStatus.REVIEWED
    store.jobs.enqueue(mid, JobKind.REPROCESS)                                             # tudo transportado: continua revisada
    _, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE and store.get_meeting(mid)["status"] == MeetingStatus.REVIEWED
    store.jobs.enqueue(mid, JobKind.REPROCESS)
    store._x("UPDATE clusters SET centroid = ? WHERE meeting_id = ?", (np.full(256, 1 / 16, np.float32).tobytes(), mid))   # nada casa
    _, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE and store.get_meeting(mid)["status"] == MeetingStatus.PROCESSED   # vozes novas pendentes


def test_current_job_is_the_latest_done_during_and_after_a_failed_reprocess(platform, store, monkeypatch):
    """R2-17: durante um reprocessamento (e depois de ele falhar no fim) a revisão e a leitura seguem o job concluído."""
    from listener.pipeline import review
    from listener.pipeline import runner as runner_mod

    store.upsert_me("Eu")
    mid = _record(platform, store)
    first = store.jobs.enqueue(mid)
    _run(store, FakeDeps(SCRIPT))
    assert store.segments.current_job_id(mid) == first
    rep = store.jobs.enqueue(mid, JobKind.REPROCESS)
    seen: list[int | None] = []

    def during(window):
        if window.channel == "L" and not seen:
            seen.append(store.segments.current_job_id(mid))
            seen.append(len(review.transcript_rows(store, mid)))

    def boom(self, others):
        raise RuntimeError("falha no fim")

    monkeypatch.setattr(runner_mod.JobRunner, "_transport_decisions", boom)
    _, status = _run(store, FakeDeps(SCRIPT, before=during))
    assert status == JobStatus.ERROR and seen == [first, 3]                                 # durante: o concluído
    assert store.segments.count_segments(mid) == 6                                         # segmentos novos já gravados...
    assert len(review.transcript_rows(store, mid)) == 3 and store.segments.current_job_id(mid) == first   # ...mas a leitura não dobra
    assert all(i.cluster_id in {c["id"] for c in store.segments.list_clusters(mid, first)} for i in review.review_items(store, mid))
    monkeypatch.undo()
    store.jobs.retry(rep)
    _, status = _run(store, FakeDeps(SCRIPT))
    assert status == JobStatus.DONE and store.segments.current_job_id(mid) == rep and store.segments.count_segments(mid) == 3
