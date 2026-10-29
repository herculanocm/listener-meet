"""F4 L6, medição do G4 no `--check` (só leitura): legendas por modelo, atraso (média, p95, máximo), CPU dos `live_cpu`,
`live_degraded` e os contadores do `live_stopped`, acerto do nome contra os segmentos do job vigente (só quem tinha perfil
antes da reunião; L sem sala fica de fora; "sobretudo" = metade da legenda), parciais contra o esperado e cada critério do
Aceite marcado; reunião sem ao vivo não ganha medição."""
from __future__ import annotations

import hashlib
import json

import numpy as np

from listener.__main__ import main
from listener.capture.recorder import Recorder
from listener.model import EmbeddingSource, EventType, LlmCallStatus, SummaryKind
from listener.store import Store
from listener.store.captions import CaptionInsert
from listener.store.llm_calls import LlmCallInsert
from listener.store.segments import SegmentInsert
from listener.store.summaries import SummaryInsert
from tests.conftest import make_config
from tests.fake_platform import FakePlatform
from tests.test_main_check import _reports


def _profile(store: Store, person_id: int, created_at: str) -> None:
    pid = store.profiles.ensure_profile(person_id, "wespeaker", 4)
    eid = store.profiles.add_embedding(pid, np.ones(4, np.float32), EmbeddingSource.CONFIRM)
    store._x("UPDATE voice_embeddings SET created_at = ? WHERE id = ?", (created_at, eid))


def _contents(db) -> dict[str, str]:
    """Conteúdo dos bancos e dos WAL não vazios (o `-shm` fica de fora: um leitor do WAL atualiza as marcas de leitura nele,
    e cria um `-wal` vazio quando não há)."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in db.iterdir()
            if p.name.endswith((".db", ".db-wal")) and p.stat().st_size}


def test_expected_partials_count_full_intervals_with_speech_and_the_interval_that_was_in_force(store):
    """Revisão do L4 a L6: silêncio e pausa não pedem parcial, nem o intervalo que fecha a menos de 20 s do fim; o
    intervalo é o gravado nos parciais (o das Configurações pode ter mudado depois)."""
    from listener.live import report

    caps = [{"t_start_ms": t} for t in (10_000, 200_000, 650_000, 1_190_000)]          # intervalos 0, 0, 2 e 3 (de 5 min)
    assert report.expected_partials(caps, 21 * 60_000, 5) == 3                           # o 1 foi silêncio
    assert report.expected_partials(caps, 20 * 60_000 + 10_000, 5) == 2                # o 3 fecha a 10 s do fim: não conta
    assert report.expected_partials(caps, 20 * 60_000, 0) == 0 and report.expected_partials([], 60_000, 5) == 0
    store.set_setting("live.summary_interval_min", 9)
    assert report._partial_interval(store, [{"interval_min": 5}, {"interval_min": 5}, {"interval_min": 2}, {}]) == 5
    assert report._partial_interval(store, [{}]) == 9                                     # sem registro: o de agora


def test_check_reports_the_g4_live_block_readonly(tmp_path, capsys):
    platform = FakePlatform(tmp_path)
    db = tmp_path / "db"
    with Store(db) as store:
        me = store.upsert_me("Eu")
        ana = store.people.create_person("Ana", role="CFO")
        bia = store.people.create_person("Bia")
        plain = store.create_meeting("sem ao vivo", "meeting", None, None)
        mid = store.create_meeting("G4", "meeting", None, None)
        rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
        rec.start()
        platform.pump(1.0)
        rec.stop()
        started = store.get_meeting(mid)["started_at"]
        store._x("UPDATE meetings SET duration_ms = ? WHERE id = ?", (11 * 60_000, mid))           # 11 min, fala só nos 5 primeiros
        _profile(store, ana, "2026-01-01T09:00:00.000-03:00")                                       # perfil antes da reunião
        _profile(store, bia, "2999-01-01T09:00:00.000-03:00")                                       # só depois (revisão desta)
        store._x("INSERT INTO processing_jobs(meeting_id, kind, status, created_at) VALUES (?, 'meeting', 'concluído', ?)",
                 (mid, started))
        job_id = int(store._q("SELECT MAX(id) FROM processing_jobs")[0][0])
        segs = [("R", 0, 5000, ana), ("R", 5000, 9500, ana), ("R", 10000, 14000, ana), ("R", 15000, 19000, bia),
                ("L", 20000, 24000, me), ("R", 30000, 31000, ana)]
        store.segments.insert_segments([SegmentInsert(mid, job_id, ch, a, b, "x", "pyannote", "final", person_id=p)
                                        for ch, a, b, p in segs])
        caps = [("R", 0, 4000, ana, "faster-whisper:small:int8", 5000), ("R", 5000, 9000, None, "faster-whisper:small:int8", 6000),
                ("R", 10000, 14000, bia, "faster-whisper:small:int8", 7000), ("R", 15000, 19000, bia, "faster-whisper:base:int8", 9000),
                ("L", 20000, 24000, me, "faster-whisper:base:int8", 8000), ("R", 30000, 34000, ana, "faster-whisper:small:int8", 20000)]
        for ch, a, b, p, model, lat in caps:
            store.captions.insert(CaptionInsert(mid, ch, a, b, "fala", p, None, None, model, lat))
        store.add_event(mid, 0, EventType.LIVE_STARTED, {"model": "faster-whisper:small:int8", "threads": 2, "names": True})
        for app, live in ((None, 20.0), (3.0, 20.0), (5.0, 30.0)):
            store.add_event(mid, 10_000, EventType.LIVE_CPU, {"app_pct": app, "live_pct": live})
        store.add_event(mid, 60_000, EventType.LIVE_DEGRADED, {"stage": "degraded", "delay_s": 21.0, "model": "faster-whisper:base:int8"})
        store.add_event(mid, 660_000, EventType.LIVE_STOPPED, {"captions": 6, "echo": 2, "skipped": 1, "app_dropped": 0})
        store.summaries.insert(SummaryInsert(mid, SummaryKind.PARTIAL, json.dumps({"summary": "x"}), "x", "gemini", "f", "partial/1.0",
                                             None, None, "pt-BR", t_until_ms=300_000))
        for err in ("sem rede", "cancelado"):
            store.llm_calls.insert(LlmCallInsert(mid, None, "partial", "gemini", "f", None, None, 0, 0, 0, 0, None, 0,
                                                 LlmCallStatus.ERROR, err))
    before = _contents(db)
    assert main(["--check", str(db)]) == 0
    reports = {r["meeting"]: r for r in _reports(capsys.readouterr().out) if "meeting" in r}    # o fim é o objeto `rag` geral
    assert reports[plain]["live"] == {"enabled": False}
    live = reports[mid]["live"]
    assert live["enabled"] and live["captions"] == {"n": 6, "by_model": {"faster-whisper:small:int8": 4, "faster-whisper:base:int8": 2}}
    assert live["latency_ms"] == {"n": 6, "mean": 9166.7, "p95": 20000.0, "max": 20000.0}
    assert live["cpu_pct"]["sum"] == {"mean": 29.0, "max": 35.0, "n": 2} and live["cpu_pct"]["live"]["n"] == 3
    assert live["degraded"][0]["stage"] == "degraded" and live["stopped"]["echo"] == 2 and live["started"][0]["threads"] == 2
    # nome: c1 certo, c2 sem nome, c3 errado; Bia (perfil depois), L sem sala e a legenda sem segmento majoritário ficam fora
    assert {k: live["names"][k] for k in ("n", "correct", "unnamed", "wrong", "pct", "people_with_profile")} == {
        "n": 3, "correct": 1, "unnamed": 1, "wrong": 1, "pct": 33.3, "people_with_profile": 1}
    assert live["partials"] == {"n": 1, "expected": 1, "interval_min": 5, "failed_calls": 1, "auto": True, "n_auto": 1}   # F5.6
    gate = live["gate"]
    assert gate["latency_mean"] == {"target": "<= 10 s", "value": 9.2, "ok": True}
    assert gate["latency_p95"]["ok"] is False and gate["cpu"]["ok"] is True and gate["cpu"]["value"] == 29.0
    assert gate["name"] == {"target": ">= 70 %", "value": 33.3, "ok": False} and gate["partials"]["ok"] is False
    assert gate["recording_intact"]["ok"] is True and gate["recording_intact"]["value"] == {"holes": 0, "idx_missing": 0}
    assert _contents(db) == before                                                                 # --check não escreve nada


def test_check_expects_no_automatic_partial_when_the_box_was_off(store):
    """F5.6 (A-23): com `partial` falso no `live_started` o esperado é zero parciais automáticos; o do "Atualizar resumo agora"
    (`manual`) não conta; sem a chave (gravação de antes), ligado, como antes."""
    from listener.live.report import live_report

    mid = store.create_meeting("sem parcial", "meeting", None, None)
    store._x("UPDATE meetings SET duration_ms = ? WHERE id = ?", (11 * 60_000, mid))
    for t0 in (0, 60_000, 400_000):
        store.captions.insert(CaptionInsert(mid, "R", t0, t0 + 4000, "fala", None, None, None, "fake:small", 5000))
    store.add_event(mid, 0, EventType.LIVE_STARTED, {"model": "fake:small", "threads": 2, "names": False, "partial": False})

    def partial(manual: bool) -> None:
        store.summaries.insert(SummaryInsert(mid, SummaryKind.PARTIAL, json.dumps({"summary": "x", "manual": manual}), "x", "gemini", "f",
                                             "partial/1.0", None, None, "pt-BR", t_until_ms=300_000))

    partial(True)
    report = live_report(store, mid)
    assert report["partials"]["expected"] == 0 and report["partials"]["auto"] is False and report["partials"]["n_auto"] == 0
    assert report["gate"]["partials"]["ok"] is True and "0 automáticos" in report["gate"]["partials"]["target"]
    partial(False)                                                                      # um automático com a caixa desmarcada
    assert live_report(store, mid)["gate"]["partials"]["ok"] is False
    store._x("UPDATE events SET data = ? WHERE meeting_id = ? AND type = ?", (json.dumps({"model": "fake:small"}), mid,
                                                                                str(EventType.LIVE_STARTED)))
    again = live_report(store, mid)                                                     # sem a chave: ligado (antes da F5.6)
    assert again["partials"]["auto"] is True and again["partials"]["expected"] == 2
