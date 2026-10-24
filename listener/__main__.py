"""python -m listener            abre o app (interface gráfica, CP7)

Flags de desenvolvimento (não fazem parte do produto; servem ao gate G1):
  --selftest [--seconds N] [--app EXE] [--db DIR]   grava N s sem interface, imprime estatísticas e continuidade
  --check DIR [--meeting ID]                        relatório de continuidade dos pedaços de uma gravação
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def selftest(args) -> int:
    from .capture.recorder import RecordConfig, Recorder, SourceSpec
    from .platform import get_platform
    from .store import Store
    from .store.playback import decode_range
    from .capture.audio import rms

    platform = get_platform()
    db_dir = Path(args.db) if args.db else Path(tempfile.mkdtemp(prefix="listener-selftest-"))
    store = Store(db_dir)
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
    rec = Recorder(platform, store, meeting_id, cfg)
    rec.start()
    print(json.dumps({"db": str(db_dir), "meeting_id": meeting_id, "mic": rec.L.detail, "source": rec.R.detail,
                      "warnings": rec.warnings}, ensure_ascii=False), flush=True)
    t_end = time.time() + args.seconds
    marked = False
    while time.time() < t_end:
        time.sleep(1)
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
        print(json.dumps({"decode": ch, "frames": int(len(samples)), "sr": sr, "rms": round(rms(samples), 4)}), flush=True)
    store.close()
    return 0


def report(store, meeting_id: int) -> None:
    m = store.get_meeting(meeting_id) or {}
    dur = m.get("duration_ms")
    out = {"meeting": meeting_id, "status": m.get("status"), "duration_ms": dur, "channels": {}}
    for ch, st in store.chunk_stats(meeting_id).items():
        covered_ms = int(st["frames"] * 1000 / (st["sr"] or 16000)) if st["frames"] else 0
        out["channels"][ch] = {"chunks": st["n"], "covered_ms": covered_ms, "gap_ms": int((st["gaps"] or 0) * 1000 / (st["sr"] or 16000)),
                               "bytes": st["bytes"], "delta_vs_duration_ms": (covered_ms - dur) if dur else None,
                               "continuity_issues": store.continuity(meeting_id, ch)}
    out["markers"] = store.list_markers(meeting_id)
    out["events"] = [(e["t_ms"], e["type"]) for e in store.list_events(meeting_id)]
    print(json.dumps(out, ensure_ascii=False, default=str, indent=1), flush=True)


def check(args) -> int:
    from .store import Store

    store = Store(args.check)
    ids = [args.meeting] if args.meeting else [m["id"] for m in store.list_meetings()]
    for mid in ids:
        report(store, mid)
    store.close()
    return 0


def main(argv=None) -> int:
    _utf8()
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
