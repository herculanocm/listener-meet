"""Medição do G4 no `--check` (F4 item 11), só leitura (`Store.open_readonly`): legendas por modelo, atraso
(`captions.latency_ms`: média, p95 e máximo), CPU dos eventos `live_cpu` (app, ao vivo e a soma: média e máximo),
`live_degraded`, os contadores do `live_stopped`, o acerto do nome e os resumos parciais contra o esperado (os intervalos
inteiros com legenda, pelo intervalo gravado em cada parcial); cada critério do Aceite marcado contra o alvo. F5.6 (A-23): com o
resumo parcial automático desligado na gravação (`partial` falso no `live_started`; sem a chave, ligado, como antes), o
esperado é zero parciais automáticos (os do "Atualizar resumo agora" têm `manual` e não contam).

Acerto do nome: o denominador são as legendas cujo tempo cai sobretudo (metade ou mais) num segmento do mesmo canal, no job
vigente, cuja pessoa (o `person_id` do segmento ou o do cluster `auto`/`confirmado`, como a ata) tinha perfil de voz no
começo da reunião (embedding de cadastro ou confirmação anterior a `meetings.started_at`: quem só ganhou perfil na revisão
desta reunião não podia ser reconhecido ao vivo); o numerador, as que têm o mesmo `person_id`. O canal L sem sala
presencial fica de fora: o nome dele vem do canal, não da voz. Puro: só consultas e contas, nada de ML."""
from __future__ import annotations

import bisect
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..config.defaults import SPECS
from ..model import EmbeddingSource, EventType, LlmCallStatus, SummaryKind

if TYPE_CHECKING:
    from ..store import Store

TARGET_LATENCY_MEAN_S = 10.0
TARGET_LATENCY_P95_S = 15.0
TARGET_CPU_PCT = 35.0
TARGET_NAME_PCT = 70.0
GAP_LIMIT_MS = 1000                 # "gravação intacta": nenhum buraco acima de 1 s entre pedaços
MAJORITY = 0.5                      # "sobretudo": o segmento cobre ao menos metade da legenda
PARTIAL_SLACK_MS = 20_000           # o disparo do parcial confere a cada 15 s: intervalo que fecha tão perto do fim não conta


def _stats(values: Sequence[float]) -> dict[str, Any]:
    """n, média, p95 (posição mais próxima) e máximo; nulos sem valor."""
    if not values:
        return {"n": 0, "mean": None, "p95": None, "max": None}
    s = sorted(values)
    p95 = s[max(0, math.ceil(0.95 * len(s)) - 1)]
    return {"n": len(s), "mean": round(sum(s) / len(s), 1), "p95": round(p95, 1), "max": round(s[-1], 1)}


def expected_partials(captions: Sequence[Mapping[str, Any]], duration_ms: int, interval_min: int) -> int:
    """Intervalos inteiros (contados do início da gravação) com alguma legenda e que fecharam ao menos 20 s antes do fim:
    cada um pede um parcial. Silêncio e pausa não pedem (sem legenda nova não há chamada); o último pedaço, incompleto,
    também não (Parar vem antes do disparo)."""
    if interval_min <= 0 or duration_ms <= 0:
        return 0
    slot = interval_min * 60_000
    used = {int(c.get("t_start_ms") or 0) // slot for c in captions}
    return sum(1 for k in used if (k + 1) * slot + PARTIAL_SLACK_MS <= duration_ms)


def _partial_interval(store: Store, datas: Sequence[Mapping[str, Any]]) -> int:
    """O intervalo que valia na reunião: o mais comum entre os gravados nos parciais (CP8 deixa mudar durante a gravação);
    sem parcial, o das Configurações de agora."""
    recorded = [int(d["interval_min"]) for d in datas if isinstance(d.get("interval_min"), int)]
    return Counter(recorded).most_common(1)[0][0] if recorded else int(store.get(SPECS["live.summary_interval_min"]))


def _partials(store: Store, meeting_id: int, duration_ms: int, captions: Sequence[Mapping[str, Any]],
              started: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Os parciais gravados contra o esperado, pelo intervalo que valia; com o automático desligado na gravação (o último
    `live_started`, A-23), o esperado é zero e só os automáticos (sem `manual`) contam."""
    datas: list[dict[str, Any]] = []
    for r in store._q("SELECT data_json FROM summaries WHERE meeting_id = ? AND kind = ?", (meeting_id, str(SummaryKind.PARTIAL))):
        try:
            d = json.loads(r[0] or "{}")
        except ValueError:
            d = {}
        datas.append(d if isinstance(d, dict) else {})
    interval = _partial_interval(store, datas)
    auto = all((e.get("data") or {}).get("partial", True) for e in started[-1:])
    failed = int(store._q("SELECT COUNT(*) FROM llm_calls WHERE meeting_id = ? AND task = 'partial' AND status = ? "
                          "AND COALESCE(error, '') != 'cancelado'", (meeting_id, str(LlmCallStatus.ERROR)))[0][0])
    return {"n": len(datas), "expected": expected_partials(captions, duration_ms, interval) if auto else 0, "interval_min": interval,
            "failed_calls": failed, "auto": auto, "n_auto": sum(1 for d in datas if not d.get("manual"))}


def _partials_gate(p: Mapping[str, Any]) -> dict[str, Any]:
    """Ligado: um parcial por intervalo com fala e nenhuma falha. Desligado na gravação (A-23): nenhum automático."""
    expected, n, failed = p["expected"], p["n"], p["failed_calls"]
    if not p["auto"]:
        return {"target": "0 automáticos (resumo parcial desligado nesta gravação) e 0 falhas",
                "value": {"n": n, "auto": p["n_auto"], "failed": failed}, "ok": p["n_auto"] == 0 and failed == 0}
    return {"target": f">= {expected} (1 por intervalo com fala) e 0 falhas", "value": {"n": n, "failed": failed},
            "ok": (n >= expected and failed == 0) if expected else None}


def _gate(value: float | None, target: float, *, at_least: bool = False, unit: str = "") -> dict[str, Any]:
    ok = None if value is None else (value >= target if at_least else value <= target)
    return {"target": f"{'>=' if at_least else '<='} {target:g}{unit}", "value": value, "ok": ok}


def _parse(ts: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def profiled_before(store: Store, started_at: str | None) -> set[int]:
    """Pessoas com perfil de voz no começo da reunião: embedding de cadastro ou confirmação criado antes de `started_at`."""
    start = _parse(started_at)
    rows = store._q("SELECT p.person_id, e.created_at FROM voice_profiles p JOIN voice_embeddings e ON e.profile_id = p.id "
                    "WHERE p.person_id IS NOT NULL AND e.source IN (?, ?)", (str(EmbeddingSource.ENROLL), str(EmbeddingSource.CONFIRM)))
    out: set[int] = set()
    for r in rows:
        created = _parse(r["created_at"])
        if start is None or (created is not None and created < start):
            out.add(int(r["person_id"]))
    return out


def segment_people(store: Store, meeting_id: int) -> dict[str, list[tuple[int, int, int | None]]] | None:
    """Por canal, (início, fim, pessoa) dos segmentos do job vigente, em ordem; None quando a reunião não foi processada."""
    from ..store.segments import resolve_person

    job = store.segments.current_job_id(meeting_id)
    if job is None:
        return None
    clusters = {int(c["id"]): c for c in store.segments.list_clusters(meeting_id, job)}
    out: dict[str, list[tuple[int, int, int | None]]] = {}
    for s in store.segments.list_segments(meeting_id, job):
        pid = s.get("person_id")
        cid = s.get("cluster_id")
        if pid is None and cid is not None and int(cid) in clusters:
            pid = resolve_person(clusters[int(cid)], clusters)
        out.setdefault(str(s.get("channel") or "R"), []).append((int(s["t_start_ms"] or 0), int(s["t_end_ms"] or 0),
                                                                None if pid is None else int(pid)))
    for segs in out.values():
        segs.sort()
    return out


def majority_person(segs: Sequence[tuple[int, int, int | None]], starts: Sequence[int], longest: int, t0: int,
                    t1: int) -> tuple[bool, int | None]:
    """(achou um segmento cobrindo a maior parte da legenda, pessoa dele)."""
    best, best_overlap = None, 0
    i = bisect.bisect_left(starts, t0 - longest)
    while i < len(segs) and segs[i][0] < t1:
        a, b, pid = segs[i]
        overlap = min(b, t1) - max(a, t0)
        if overlap > best_overlap:
            best, best_overlap = pid, overlap
        i += 1
    if best_overlap <= 0 or best_overlap < MAJORITY * max(1, t1 - t0):
        return False, None
    return True, best


def name_accuracy(store: Store, meeting_id: int, meeting: Mapping[str, Any], captions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_channel = segment_people(store, meeting_id)
    profiled = profiled_before(store, meeting.get("started_at"))
    base: dict[str, Any] = {"people_with_profile": len(profiled), "channels": ["L", "R"] if meeting.get("is_in_room") else ["R"]}
    if by_channel is None:
        return {**base, "n": 0, "correct": 0, "pct": None, "note": "reunião ainda não processada: sem segmentos para comparar"}
    index = {ch: ([s[0] for s in segs], max((s[1] - s[0] for s in segs), default=0)) for ch, segs in by_channel.items()}
    n = correct = unnamed = 0
    for c in captions:
        ch = str(c.get("channel") or "R")
        if ch not in base["channels"] or ch not in by_channel:
            continue
        starts, longest = index[ch]
        found, pid = majority_person(by_channel[ch], starts, longest, int(c.get("t_start_ms") or 0), int(c.get("t_end_ms") or 0))
        if not found or pid is None or pid not in profiled:
            continue
        n += 1
        if c.get("person_id") is not None and int(c["person_id"]) == pid:
            correct += 1
        elif c.get("person_id") is None:
            unnamed += 1
    return {**base, "n": n, "correct": correct, "unnamed": unnamed, "wrong": n - correct - unnamed,
            "pct": round(100.0 * correct / n, 1) if n else None}


def recording_intact(channels: Mapping[str, Any], idx_missing: Mapping[str, int]) -> dict[str, Any]:
    holes = sum(1 for ch in channels.values() for i in ch.get("continuity_issues") or () if abs(int(i["delta_ms"])) > GAP_LIMIT_MS)
    missing = sum(idx_missing.values())
    return {"target": f"0 buracos > {GAP_LIMIT_MS} ms e 0 idx perdidos", "value": {"holes": holes, "idx_missing": missing},
            "ok": holes == 0 and missing == 0}


def live_report(store: Store, meeting_id: int, events: Sequence[Mapping[str, Any]] | None = None,
                channels: Mapping[str, Any] | None = None) -> dict[str, Any]:
    meeting: dict[str, Any] = dict(store.get_meeting(meeting_id) or {})
    events = list(events if events is not None else store.list_events(meeting_id))
    captions: list[dict[str, Any]] = [dict(r) for r in store.captions.list_for_meeting(meeting_id)]
    started = [e for e in events if e["type"] == EventType.LIVE_STARTED]
    if not captions and not started:
        return {"enabled": False}
    by_model: dict[str, int] = {}
    for c in captions:
        key = str(c.get("model") or "?")
        by_model[key] = by_model.get(key, 0) + 1
    latency = _stats([float(c["latency_ms"]) for c in captions if c.get("latency_ms") is not None])
    app, live, both = [], [], []
    for e in events:
        if e["type"] != EventType.LIVE_CPU:
            continue
        d = e.get("data") or {}
        a, v = d.get("app_pct"), d.get("live_pct")
        if isinstance(a, int | float):
            app.append(float(a))
        if isinstance(v, int | float):
            live.append(float(v))
        if isinstance(a, int | float) and isinstance(v, int | float):
            both.append(float(a) + float(v))
    cpu = {k: {"mean": _stats(vals)["mean"], "max": _stats(vals)["max"], "n": len(vals)} for k, vals in
           (("app", app), ("live", live), ("sum", both))}
    degraded = [{"t_ms": e.get("t_ms"), **(e.get("data") or {})} for e in events if e["type"] == EventType.LIVE_DEGRADED]
    stopped = [e.get("data") or {} for e in events if e["type"] == EventType.LIVE_STOPPED]
    names = name_accuracy(store, meeting_id, meeting, captions)
    partials = _partials(store, meeting_id, int(meeting.get("duration_ms") or 0), captions, started)
    mean_s = None if latency["mean"] is None else round(latency["mean"] / 1000, 1)
    p95_s = None if latency["p95"] is None else round(latency["p95"] / 1000, 1)
    gate = {"latency_mean": _gate(mean_s, TARGET_LATENCY_MEAN_S, unit=" s"),
            "latency_p95": _gate(p95_s, TARGET_LATENCY_P95_S, unit=" s"),
            "cpu": {**_gate(cpu["sum"]["mean"], TARGET_CPU_PCT, unit=" %"), "basis": "média da soma app + ao vivo"},
            "name": _gate(names["pct"], TARGET_NAME_PCT, at_least=True, unit=" %"),
            "partials": _partials_gate(partials)}
    if channels is not None:
        missing = {}
        for ch in channels:
            idx = [int(c["idx"]) for c in store.list_chunks(meeting_id, ch)]
            missing[ch] = (max(idx) + 1 - len(set(idx))) if idx else 0
        gate["recording_intact"] = recording_intact(channels, missing)
    return {"enabled": True, "started": [e.get("data") for e in started], "captions": {"n": len(captions), "by_model": by_model},
            "latency_ms": latency, "cpu_pct": cpu, "degraded": degraded, "stopped": stopped[-1] if stopped else None,
            "names": names, "partials": partials, "gate": gate}
