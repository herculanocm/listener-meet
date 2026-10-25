"""Evidência verificável (contrato "Evidência verificável", R-4): toda decisão, ação, destaque e sugestão de identidade
carrega `segment_ids` ou `{segment_id, quote}`; o projeto confere que o segmento existe na reunião e que a citação está
contida no texto dele (casefold, sem pontuação); evidência inválida é descartada e a confiança multiplicada por 0,5; item
sem evidência válida é mantido com a marca "sem evidência". O minuto exibido vem sempre de `segments.t_start_ms`."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

NO_EVIDENCE = "sem evidência"
CONFIDENCE_PENALTY = 0.5
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Casefold, sem acentos nem pontuação, espaços únicos: `"Valeu, Rodrigo!"` -> `"valeu rodrigo"`."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return _SPACES.sub(" ", _PUNCT.sub(" ", t.casefold())).strip()


def quote_in(segment_text: str, quote: str) -> bool:
    q = normalize(quote)
    return bool(q) and q in normalize(segment_text)


@dataclass(frozen=True, slots=True)
class Evidence:
    segment_id: int
    quote: str
    t_start_ms: int


def check_identify(evidence: Iterable[Mapping[str, Any]], confidence: float,
                   segments: Mapping[int, tuple[str, int]]) -> tuple[tuple[Evidence, ...], float]:
    """Evidências válidas (segmento existe e a citação está nele) e a confiança ajustada: vezes 0,5 quando alguma foi
    descartada ou quando não sobrou nenhuma."""
    valid: list[Evidence] = []
    dropped = 0
    for e in evidence:
        try:
            sid = int(e.get("segment_id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            dropped += 1
            continue
        seg = segments.get(sid)
        quote = str(e.get("quote") or "")
        if seg is None or not quote_in(seg[0], quote):
            dropped += 1
            continue
        valid.append(Evidence(sid, quote, seg[1]))
    conf = max(0.0, min(1.0, float(confidence)))
    if dropped or not valid:
        conf = round(conf * CONFIDENCE_PENALTY, 3)
    return tuple(valid), conf


def valid_segment_ids(ids: Iterable[Any], segments: Mapping[int, Any]) -> list[int]:
    out: list[int] = []
    for raw in ids:
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            continue
        if sid in segments and sid not in out:
            out.append(sid)
    return out


def first_time_ms(ids: Iterable[int], segments: Mapping[int, tuple[str, int]]) -> int | None:
    times = [segments[i][1] for i in ids if i in segments]
    return min(times) if times else None
