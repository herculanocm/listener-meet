"""M-05 `echo`: para cada reunião do banco real (`--meeting` ou todas as `revisada`/`processada`), decodifica L e R em
blocos de `--block-s` (10 s) e aplica a regra de `live/proc.py::_is_echo`, portada aqui como função pura (`echo_block`):
correlação de `estimate_offset(l, r, 16000)` acima de `capture.echo_corr_min` do banco e nenhuma fala própria por 0,5 s ou
mais (quadros de 100 ms com L acima de `capture.silence_rms` e R abaixo). Saída por id de reunião (nunca o título):
blocos, blocos com sinal no L, blocos de eco, fração de eco entre os com sinal, blocos poupados pela fala própria,
distribuição da correlação e `is_in_room`. Decide o limiar e a regra dos 0,5 s do PF-07."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from . import audio
from .common import Report, stats

ECHO_FRAME_MS = 100               # os mesmos de `live/proc.py`
ECHO_OWN_SPEECH_MS = 500
SPAN_MS = 60_000                  # decodificação por minuto (um pedaço FLAC de cada canal), cortada em blocos


@dataclass(frozen=True, slots=True)
class BlockVerdict:
    l_signal: bool
    own_speech_ms: int
    correlation: float | None
    offset_ms: float | None
    is_echo: bool


def echo_block(l: np.ndarray, r: np.ndarray, sr: int, corr_min: float, silence_rms: float) -> BlockVerdict:
    """A regra do ao vivo, pura: eco = R com sinal, menos de 0,5 s de fala própria e correlação >= `corr_min`."""
    from listener.capture.align import estimate_offset

    frame = ECHO_FRAME_MS * sr // 1000
    n = min(len(l), len(r)) // frame
    own_ms, l_signal = 0, False
    if n:
        l_rms = np.sqrt(np.mean(np.square(l[:n * frame].reshape(n, frame)), axis=1))
        r_rms = np.sqrt(np.mean(np.square(r[:n * frame].reshape(n, frame)), axis=1))
        l_signal = bool(np.any(l_rms > silence_rms))
        own_ms = int(np.count_nonzero((l_rms > silence_rms) & (r_rms <= silence_rms))) * ECHO_FRAME_MS
    if not np.any(r):
        return BlockVerdict(l_signal, own_ms, None, None, False)
    res = estimate_offset(l, r, sr)
    corr = float(res["correlation"]) if res else None
    offset = float(res["offset_ms"]) if res else None
    if own_ms >= ECHO_OWN_SPEECH_MS or corr is None or corr_min <= 0:
        return BlockVerdict(l_signal, own_ms, corr, offset, False)
    return BlockVerdict(l_signal, own_ms, corr, offset, corr >= corr_min)


def meeting_blocks(store: Any, meeting_id: int, block_s: float, minutes: float, corr_min: float, silence_rms: float) -> list[BlockVerdict]:
    total = audio.meeting_ms(store, meeting_id)
    if minutes and minutes > 0:
        total = min(total, round(minutes * 60_000))
    block = int(block_s * audio.SR)
    verdicts: list[BlockVerdict] = []
    for t0 in range(0, total, SPAN_MS):
        t1 = min(total, t0 + SPAN_MS)
        l, r = audio.decode_channel(store, meeting_id, "L", t0, t1), audio.decode_channel(store, meeting_id, "R", t0, t1)
        n = min(len(l), len(r))
        verdicts.extend(echo_block(l[a:a + block], r[a:a + block], audio.SR, corr_min, silence_rms) for a in range(0, n - block // 2, block))
    return verdicts


def summarize(meeting: dict[str, Any], verdicts: list[BlockVerdict]) -> dict[str, Any]:
    with_signal = [v for v in verdicts if v.l_signal]
    echo = [v for v in verdicts if v.is_echo]
    corr = [v.correlation for v in with_signal if v.correlation is not None]
    return {"meeting_id": int(meeting["id"]), "status": str(meeting["status"]), "is_in_room": bool(meeting.get("is_in_room")),
            "duration_s": round(int(meeting.get("duration_ms") or 0) / 1000), "blocks": len(verdicts), "blocks_l_signal": len(with_signal),
            "echo_blocks": len(echo), "echo_fraction_of_signal": round(len(echo) / len(with_signal), 4) if with_signal else None,
            "echo_fraction_of_all": round(len(echo) / len(verdicts), 4) if verdicts else None,
            "spared_by_own_speech": sum(1 for v in with_signal if v.own_speech_ms >= ECHO_OWN_SPEECH_MS
                                        and v.correlation is not None and v.correlation >= 0.25),
            "correlation": stats(corr, 3), "offset_ms_echo": stats([v.offset_ms for v in echo if v.offset_ms is not None], 1)}


def run(args: Any, report: Report, store: Any) -> None:
    from listener.config.defaults import SPECS
    from listener.model import MeetingStatus

    if store is None:
        raise SystemExit("echo precisa do banco real (só leitura)")
    corr_min = float(args.corr_min if args.corr_min is not None else store.get(SPECS["capture.echo_corr_min"]))
    silence_rms = float(store.get(SPECS["capture.silence_rms"]))
    wanted = {str(MeetingStatus.REVIEWED), str(MeetingStatus.PROCESSED)}
    meetings = [m for m in store.list_meetings() if (args.meeting is not None and int(m["id"]) == int(args.meeting))
                or (args.meeting is None and str(m["status"]) in wanted)]
    report.add("echo_setup", corr_min=corr_min, silence_rms=silence_rms, block_s=args.block_s, meetings=[int(m["id"]) for m in meetings],
               minutes=args.minutes or None)
    rows = []
    for m in sorted(meetings, key=lambda m: int(m["id"])):
        verdicts = meeting_blocks(store, int(m["id"]), args.block_s, float(args.minutes or 0), corr_min, silence_rms)
        rows.append(report.add("echo_meeting", **summarize(m, verdicts)))
    fractions = [r["echo_fraction_of_signal"] for r in rows if r.get("echo_fraction_of_signal") is not None]
    report.finish(meetings=len(rows), echo_fraction_of_signal=stats(fractions, 4), corr_min=corr_min)
