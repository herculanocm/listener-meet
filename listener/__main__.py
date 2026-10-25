"""python -m listener            abre o app (interface gráfica, CP7)

Flags de desenvolvimento (não fazem parte do produto; servem ao gate G1):
  --selftest [--seconds N] [--app EXE] [--db DIR]   grava N s sem interface, imprime estatísticas e continuidade
  --check DIR [--meeting ID]                        relatório de continuidade, deriva (F1.1 A7) e alinhamento L/R (U-016),
                                                    pedaços órfãos e arquivos de áudio ausentes; bloco `live` com a medição
                                                    do G4 (F4: atraso, CPU, nome, parciais); abre o banco só leitura
  --worker [--db DIR] [--once] [--light]            worker da fila de processamento (F2): o app o lança sozinho; à mão
                                                    serve para testes (LISTENER_PIPELINE=fake) e diagnóstico; `--light` é
                                                    o worker leve da F3 (só jobs de LLM, sem torch; LISTENER_LLM=fake)
  --live --db DIR --meeting ID                      processo ao vivo (F4): o app o lança ao gravar e conversa por stdin e
                                                    stdout (LISTENER_LIVE=fake nos testes)
  --live --prepare [--db DIR]                       baixa os modelos do ao vivo (small e base), progresso em linhas JSON
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import Store


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def selftest(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .capture.audio import rms
    from .capture.recorder import RecordConfig, Recorder, SourceSpec
    from .platform import get_platform
    from .store import Store
    from .store.playback import decode_range

    platform = get_platform()
    db_dir = Path(args.db) if args.db else Path(tempfile.mkdtemp(prefix="listener-selftest-"))
    with Store(db_dir) as store:
        store.upsert_me("selftest")
        source = SourceSpec("system", label="Sistema inteiro")
        if args.app:
            pid = platform.root_pid(args.app)
            if pid:
                source = SourceSpec("app", pid=pid, exe=args.app, label=args.app)
            else:
                print(f"{args.app} não está rodando; usando o sistema inteiro")
        cfg = RecordConfig(mic_device=platform.default_input_device(), source=source, chunk_seconds=args.chunk)
        meeting_id = store.create_meeting("selftest", source.exe or "sistema", None, None)
        platform.mic_mute().set_device(cfg.mic_device)
        rec = Recorder(platform, store, meeting_id, cfg)
        rec.start()
        print(json.dumps({"db": str(db_dir), "meeting_id": meeting_id, "mic": rec.L.detail, "source": rec.R.detail,
                          "warnings": rec.warnings}, ensure_ascii=False), flush=True)
        t_end = time.time() + args.seconds
        marked = False
        while time.time() < t_end:
            time.sleep(1)
            rec.check_devices()
            if not marked and rec.elapsed() >= 2:
                rec.mark("marcador do selftest")
                marked = True
            print(json.dumps({"t": round(rec.elapsed(), 1), "levels": {k: round(v, 4) for k, v in rec.levels().items()},
                              "health": rec.health()}), flush=True)
        stats = rec.stop()
        print(json.dumps({"stats": stats}, ensure_ascii=False, default=str), flush=True)
        report(store, meeting_id)
        for ch in ("L", "R"):
            samples, sr = decode_range(store, meeting_id, ch, 0, 2000)
            print(json.dumps({"decode": ch, "frames": len(samples), "sr": sr, "rms": round(rms(samples), 4)}), flush=True)
    return 0


def alignment(store: Store, meeting_id: int, window_s: int = 10, search_s: int = 300) -> dict[str, Any]:
    """Deslocamento L/R (U-016) na janela de `window_s` com mais sinal nos dois canais, nos primeiros `search_s`.
    Decodifica cada pedaço uma vez e fatia as janelas (M-11)."""
    import numpy as np

    from .capture.align import estimate_offset
    from .capture.audio import rms
    from .store.playback import decode_range

    m = store.get_meeting(meeting_id) or {}
    dur = min(m.get("duration_ms") or 0, search_s * 1000)
    if dur <= 0:
        return {"error": "sem duração"}
    left, sr = decode_range(store, meeting_id, "L", 0, dur)
    right, sr_r = decode_range(store, meeting_id, "R", 0, dur)
    if not len(left) or not len(right) or sr != sr_r:
        return {"error": "sem áudio nos dois canais na mesma taxa"}
    win = window_s * sr
    best: tuple[float, int, np.ndarray, np.ndarray] | None = None
    for start in range(0, min(len(left), len(right)) - win + 1, win):
        l_win, r_win = left[start:start + win], right[start:start + win]
        score = min(rms(l_win), rms(r_win))
        if best is None or score > best[0]:
            best = (score, start, l_win, r_win)
    if best is None or best[0] < 0.004:
        return {"error": "sem janela com sinal nos dois canais"}
    res = estimate_offset(best[2], best[3], sr) or {}
    return {"t0_ms": int(best[1] * 1000 / sr), "window_s": window_s, **{k: round(v, 3) for k, v in res.items()}}


def meeting_report(store: Store, meeting_id: int) -> dict[str, Any]:
    m = store.get_meeting(meeting_id) or {}
    dur = m.get("duration_ms")
    channels: dict[str, Any] = {}
    out: dict[str, Any] = {"meeting": meeting_id, "status": m.get("status"), "duration_ms": dur,
                           "audio_file": m.get("audio_file"), "channels": channels}
    for ch, st in store.chunk_stats(meeting_id).items():
        sr = st["sr"] or 16000
        covered_ms = int(st["frames"] * 1000 / sr) if st["frames"] else 0
        channels[ch] = {"chunks": st["n"], "covered_ms": covered_ms, "gap_ms": int((st["gaps"] or 0) * 1000 / sr),
                        "bytes": st["bytes"], "delta_vs_duration_ms": (covered_ms - dur) if dur else None,
                        "continuity_issues": store.continuity(meeting_id, ch)}
    events = store.list_events(meeting_id)
    out["markers"] = store.list_markers(meeting_id)
    out["events"] = [(e["t_ms"], e["type"]) for e in events]
    stop = next((e for e in reversed(events) if e["type"] == "stop"), None)
    out["drift"] = (stop or {}).get("data", {}).get("drift") if stop else None
    out["unsaved"] = (stop or {}).get("data", {}).get("unsaved") if stop else None
    out["alignment"] = alignment(store, meeting_id)
    try:                                                     # F4 item 11: medição do G4, só leitura
        from .live.report import live_report

        out["live"] = live_report(store, meeting_id, events, channels)
    except Exception as e:                                   # o bloco do ao vivo nunca derruba o relatório da F1
        out["live"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def report(store: Store, meeting_id: int) -> None:
    out = meeting_report(store, meeting_id)
    out["orphans"] = store.orphan_report()
    out["missing_audio_files"] = store.missing_audio_files()
    print(json.dumps(out, ensure_ascii=False, default=str, indent=1), flush=True)


def check(args: argparse.Namespace) -> int:
    from .store import Store

    with Store.open_readonly(args.check) as store:
        ids = [args.meeting] if args.meeting else [m["id"] for m in store.list_meetings()]
        for mid in ids:
            report(store, mid)
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--live" in argv:                                     # o processo ao vivo tem o seu próprio parser e log (F4)
        from .live.proc import main as live_main

        return live_main([a for a in argv if a != "--live"])
    if "--worker" in argv:                                   # o worker tem o seu próprio parser e log (F2)
        from .pipeline.worker import main as worker_main

        return worker_main([a for a in argv if a != "--worker"])
    from .log import setup

    setup("INFO")
    ap = argparse.ArgumentParser(prog="listener", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--chunk", type=float, default=3.0)
    ap.add_argument("--app")
    ap.add_argument("--db")
    ap.add_argument("--check", metavar="DIR")
    ap.add_argument("--meeting", type=int)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest(args)
    if args.check:
        return check(args)
    from .ui.app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
