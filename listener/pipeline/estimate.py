"""Estimativa de tempo restante de um job (F2 item 15, D-F2-12), pura: RTF por `etapa:modelo` medido (`pipeline.rtf_json`,
média móvel) ou sementes (Whisper 0,67x sobre a fala, pyannote 0,8x sobre a duração, embeddings 0,05x); a fala esperada
vem da taxa já medida no job (`timings_json`) ou de 60 % no R e 30 % no L [exp]. None = "sem estimativa"."""
from __future__ import annotations

import json
from typing import Any

from ..model import HEAVY_JOB_KINDS, JobStep

STEP_ORDER: tuple[JobStep, ...] = (JobStep.DIARIZE, JobStep.EMBED, JobStep.MATCH, JobStep.TRANSCRIBE_R, JobStep.TRANSCRIBE_L,
                                   JobStep.SEGMENTS)
STEP_WEIGHTS: dict[str, float] = {JobStep.DIARIZE: 0.45, JobStep.EMBED: 0.05, JobStep.MATCH: 0.0, JobStep.TRANSCRIBE_R: 0.33,
                                  JobStep.TRANSCRIBE_L: 0.15, JobStep.SEGMENTS: 0.02}
SEEDS: dict[str, float] = {JobStep.DIARIZE: 0.8, JobStep.EMBED: 0.05, JobStep.TRANSCRIBE_R: 0.67, JobStep.TRANSCRIBE_L: 0.67}
SPEECH_SHARE: dict[str, float] = {JobStep.TRANSCRIBE_R: 0.6, JobStep.TRANSCRIBE_L: 0.3}


def _loads(text: str | None) -> dict[str, Any]:
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def rtf_for(step: str, rtf: dict[str, Any], model: str | None) -> float:
    """RTF medido para a etapa (modelo exato, senão o último modelo visto para a etapa), senão a semente."""
    if model and f"{step}:{model}" in rtf:
        return float(rtf[f"{step}:{model}"])
    candidates = [float(v) for k, v in rtf.items() if k.split(":", 1)[0] == step]
    if candidates:
        return candidates[-1]
    return SEEDS.get(step, 0.0)


def remaining_seconds(step: str | None, progress: float, duration_s: float | None, rtf_json: str | None,
                      timings_json: str | None = None, models: dict[str, str] | None = None, kind: str | None = None) -> float | None:
    """Segundos de relógio que faltam; None sem duração ou com job terminado. `progress` é a fração global ponderada.
    Só a classe pesada tem RTF sobre o áudio: `llm` e `index` (classe leve) devolvem None (a fila estima o `llm` pela
    latência média das chamadas, F3 item 16; o `index` não tem estimativa, F5A-05)."""
    if (kind and str(kind) not in HEAVY_JOB_KINDS) or not duration_s or duration_s <= 0 or progress >= 1.0:
        return None
    rtf, timings, models = _loads(rtf_json), _loads(timings_json), models or {}
    done_before = 0.0
    current = step if step in STEP_WEIGHTS else None
    if current is None:
        current = STEP_ORDER[0]
    total = 0.0
    passed = True
    for s in STEP_ORDER:
        weight = STEP_WEIGHTS[s]
        base = duration_s
        if s in SPEECH_SHARE:
            t = timings.get(s) or {}
            share = (float(t["speech_s"]) / float(t["audio_s"])) if t.get("audio_s") else SPEECH_SHARE[s]
            base = duration_s * max(0.0, min(1.0, share))
        cost = rtf_for(s, rtf, (timings.get(s) or {}).get("model") or models.get(s)) * base
        if s == current:
            passed = False
            frac = (progress - done_before) / weight if weight > 0 else 1.0
            total += cost * (1.0 - max(0.0, min(1.0, frac)))
        elif not passed:
            total += cost
        else:
            done_before += weight
    return max(0.0, total)


def fmt_remaining(seconds: float | None) -> str:
    if seconds is None:
        return "sem estimativa"
    s = round(seconds)
    if s < 60:
        return "menos de 1 min"
    if s < 3600:
        return f"≈ {s // 60} min"
    return f"≈ {s // 3600} h {(s % 3600) // 60:02d} min"
