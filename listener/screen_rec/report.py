"""F5.27: o bloco `screen_rec` do `--check` de uma reunião, só leitura (`Store.open_readonly`), sem Qt nem PyAV.

- Dos pedaços (`store.screen.list_chunks`, que sempre existem quando houve tela): quantos, MB, o tempo coberto, MB por hora
  coberta, quadros, a taxa efetiva (quadros por segundo coberto), os tamanhos, os índices perdidos e os buracos entre pedaços
  (o começo de um menos o fim do anterior, acima de 1 s). Buraco não é defeito por si: a pausa, a falha de leitura e o monitor
  perdido os deixam (a regra do `recorder`); por isso é dado, não critério.
- Dos eventos: cada `screen_stop` traz no `data` o resumo do gravador daquela sessão (quadros, pulados, falhas, tempos) e o
  motivo; pode faltar (o filho morto) ou vir mais de um (parou e começou de novo). Os pulados e as falhas somam; o cycle p95
  vale o maior das sessões (percentil não se soma). `screen_start`, `screen_lost` e `screen_switch` vão em listas com o
  instante e o número do monitor (nunca a chave do sistema).
- O critério do Aceite de disco: até 150 MB por hora a 1 quadro por segundo e 1920 px (marcado só nessa configuração).
Sem tela nesta reunião, o bloco diz que não houve."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from ..model import EventType

if TYPE_CHECKING:
    from ..store import Store

MB = 1_000_000
GAP_MS = 1000                          # buraco: mais de 1 s entre o fim de um pedaço e o começo do seguinte
TARGET_MB_PER_HOUR = 150.0             # Aceite (disco), a 1 quadro por segundo e 1920 px
GATE_FPS, GATE_SIDE = 1, 1920
DELETED = "screen_deleted"             # o evento do "Apagar a tela desta reunião" (U-184)
NO_SCREEN = "sem tela nesta reunião"
SESSION_KEYS = ("reason", "frames", "skipped", "failures", "segments", "bytes", "grab_ms_p95", "encode_ms_p95", "cycle_ms_p50",
                "cycle_ms_p95")


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _event_list(events: Sequence[Mapping[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [{"t_ms": e.get("t_ms"), **(e.get("data") or {})} for e in events if e.get("type") == kind]


def chunk_gaps(chunks: Sequence[Mapping[str, Any]]) -> list[dict[str, int]]:
    """Os buracos entre pedaços seguidos, acima de `GAP_MS`: o índice do pedaço que volta, onde começa e quanto durou."""
    out: list[dict[str, int]] = []
    for prev, cur in pairwise(chunks):
        gap = int(cur["t_start_ms"]) - int(prev["t_end_ms"])
        if gap > GAP_MS:
            out.append({"idx": int(cur["idx"]), "t_ms": int(prev["t_end_ms"]), "gap_ms": gap})
    return out


def _sessions(stops: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sessions = [{k: s.get(k) for k in ("t_ms", *SESSION_KEYS) if k in s} for s in stops]
    cycles = [v for v in (_num(s.get("cycle_ms_p95")) for s in stops) if v is not None]
    return {"sessions": sessions, "skipped": sum(int(_num(s.get("skipped")) or 0) for s in stops),
            "failures": sum(int(_num(s.get("failures")) or 0) for s in stops),
            "cycle_ms_p95": max(cycles) if cycles else None}


def _disk_gate(mb_per_hour: float | None, starts: Sequence[Mapping[str, Any]], fps: int, side: int) -> dict[str, Any]:
    """Marcado só a 1 quadro por segundo e 1920 px (os de agora nas Configurações, ou os do `screen_start`, se gravados)."""
    fps = int(_num(starts[-1].get("fps")) or fps) if starts else fps
    side = int(_num(starts[-1].get("max_side")) or side) if starts else side
    ok = None if mb_per_hour is None or (fps, side) != (GATE_FPS, GATE_SIDE) else mb_per_hour <= TARGET_MB_PER_HOUR
    return {"target": f"<= {TARGET_MB_PER_HOUR:g} MB/h a {GATE_FPS} quadro/s e {GATE_SIDE} px", "value": mb_per_hour,
            "settings": {"fps": fps, "max_side": side}, "ok": ok}


def screen_report(store: Store, meeting_id: int, events: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """O bloco `screen_rec` da reunião."""
    events = list(events if events is not None else store.list_events(meeting_id))
    kinds = {str(t) for t in (EventType.SCREEN_START, EventType.SCREEN_STOP, EventType.SCREEN_LOST, EventType.SCREEN_SWITCH)}
    screen_events = [e for e in events if e.get("type") in kinds]
    chunks: list[dict[str, Any]] = [dict(c) for c in store.screen.list_chunks(meeting_id)]
    file = store.screen.file_for(meeting_id)
    if not chunks and not screen_events and file is None:
        return {"enabled": False, "note": NO_SCREEN}
    total = sum(int(c["bytes"]) for c in chunks)
    covered_ms = sum(max(0, int(c["t_end_ms"]) - int(c["t_start_ms"])) for c in chunks)
    frames = sum(int(c["frames"]) for c in chunks)
    hours = covered_ms / 3_600_000
    mb = round(total / MB, 1)
    mb_per_hour = round(total / MB / hours, 1) if hours > 0 else None
    idx = [int(c["idx"]) for c in chunks]
    gaps = chunk_gaps(chunks)
    starts = _event_list(events, str(EventType.SCREEN_START))
    out: dict[str, Any] = {
        "enabled": True, "screen_file": file, "chunks": len(chunks), "bytes": total, "mb": mb, "covered_ms": covered_ms,
        "mb_per_hour": mb_per_hour, "frames": frames,
        "fps_effective": round(frames / (covered_ms / 1000), 2) if covered_ms > 0 else None,
        "sizes": sorted({f"{c['width']}x{c['height']}" for c in chunks}), "codecs": sorted({str(c["codec"]) for c in chunks}),
        "idx_missing": (max(idx) + 1 - len(set(idx))) if idx else 0,
        "gaps": {"n": len(gaps), "total_ms": sum(g["gap_ms"] for g in gaps), "list": gaps},
        **_sessions(_event_list(events, str(EventType.SCREEN_STOP))),
        "starts": starts, "lost": _event_list(events, str(EventType.SCREEN_LOST)),
        "switches": _event_list(events, str(EventType.SCREEN_SWITCH))}
    deleted = _event_list(events, DELETED)
    if deleted:
        out["deleted"] = deleted                       # U-184: os pedaços saíram por pedido do usuário; os eventos ficam
    out["gate"] = {"disk": _disk_gate(mb_per_hour, starts, int(store.get_setting("screen_rec.fps") or GATE_FPS),
                                      int(store.get_setting("screen_rec.max_side") or GATE_SIDE))}
    return out


__all__ = ["GAP_MS", "NO_SCREEN", "TARGET_MB_PER_HOUR", "chunk_gaps", "screen_report"]
