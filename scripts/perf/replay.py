"""`replay`, a sonda do PF-09 (lote 3): toca o áudio real de uma reunião (L e R decodificados do banco real em `mode=ro`) no
protocolo do filho ao vivo, em ritmo real (`--speed 1.0`; mais rápido só para testar: o `latency_ms` do filho usa o relógio
de parede e sai distorcido), escrevendo os quadros de 100 ms no stdin de `python -m listener --live --db TMP --meeting ID`
lançado pela plataforma como o app faz (`spawn_child`: BELOW_NORMAL e sem janela, `live_env`), num banco TEMPORÁRIO criado
pela sonda (`Store(tmp)` no papel APP, `create_meeting` com o idioma e a sala da reunião; `pipeline.models_dir`, `live.*`,
`app.language` e os limiares de eco copiados do banco real; o `hello` como `live/client.py` monta). `--names` copia o token
do Hugging Face para o banco temporário (apagado no fim) para medir com os nomes; sem ele, `names=False`. `--load 0.85`
liga um filho de carga (o do M-04) a 85 % da máquina. No fim lê do banco temporário as `captions` (latência média, mediana,
p95, máximo), o `live_stopped` (`windows`, `skipped`, `degradations`, `filtered`, `echo`) e os `live_degraded`, apaga o
temporário e nunca imprime texto de legenda."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import audio
from .common import HF_PROVIDER, Report, child_env, parse_child, start_child, stats

BLOCK_S = 0.1
EOF_GRACE_S = 8.0
COPIED_SETTINGS = ("pipeline.models_dir", "live.model", "live.threads", "live.names", "app.language", "capture.echo_corr_min",
                   "capture.silence_rms")
EXIT_SLACK_S = 30.0              # depois do EOF: o prazo do filho (8 s) mais a janela em curso e a saída


def make_temp_db(store: Any, meeting: dict[str, Any], tmp: Path, names: bool) -> int:
    """O banco temporário com as configurações copiadas e uma reunião nova; devolve o id dela."""
    from listener.store import Store

    with Store(tmp) as t:
        for key in COPIED_SETTINGS:
            t.set_setting(key, store.get_setting(key))
        if names:
            t.upsert_provider(HF_PROVIDER, "huggingface", api_key=store.settings.provider_key(HF_PROVIDER))
        return int(t.create_meeting(None, None, None, None, is_in_room=bool(meeting.get("is_in_room")),
                                    language=meeting.get("language")))


class Reader(threading.Thread):
    """Lê as linhas JSON do filho: conta por tipo, marca o `ready` e guarda o `bye` (nunca o texto das legendas)."""

    def __init__(self, stdout: Any, t0: float):
        super().__init__(name="replay-read", daemon=True)
        self.stdout, self.t0 = stdout, t0
        self.counts: dict[str, int] = {}
        self.ready_s: float | None = None
        self.first_caption_s: float | None = None
        self.bye: dict[str, Any] | None = None
        self.errors: list[str] = []

    def run(self) -> None:
        from listener.live.protocol import decode_line

        for line in self.stdout:
            try:
                msg = decode_line(line)
            except ValueError:
                continue
            kind = str(msg.get("type"))
            self.counts[kind] = self.counts.get(kind, 0) + 1
            if kind == "ready" and self.ready_s is None:
                self.ready_s = round(time.perf_counter() - self.t0, 2)
            elif kind == "caption" and self.first_caption_s is None:
                self.first_caption_s = round(time.perf_counter() - self.t0, 2)
            elif kind == "error":
                self.errors.append(str(msg.get("message", ""))[:200])
            elif kind == "bye":
                self.bye = {k: v for k, v in msg.items() if k != "type"}


def feed(stdin: Any, left: np.ndarray, right: np.ndarray, speed: float, hello: Any, t0_perf: float) -> dict[str, Any]:
    """Os quadros de 100 ms dos dois canais no ritmo real (ou `speed` vezes mais rápido), depois `stats` e o EOF."""
    from listener.capture.audio import to_int16
    from listener.live.protocol import encode_audio, encode_control

    block = int(BLOCK_S * audio.SR)
    n = max(len(left), len(right))
    late = 0
    stdin.write(encode_control(hello.to_msg()))
    stdin.flush()
    for k, start in enumerate(range(0, n, block)):
        target = t0_perf + k * BLOCK_S / speed
        now = time.perf_counter()
        if now < target:
            time.sleep(target - now)
        elif now - target > BLOCK_S:
            late += 1
        for channel, x in (("L", left), ("R", right)):
            piece = x[start:start + block]
            if piece.size:
                pcm = to_int16(piece).astype("<i2", copy=False).tobytes()
                stdin.write(encode_audio(channel, start, audio.SR, len(piece), 0, pcm))
        stdin.flush()
    stdin.write(encode_control({"type": "stats", "dropped": 0}))
    stdin.flush()
    stdin.close()
    return {"blocks": (n + block - 1) // block, "late_blocks": late, "fed_s": round(time.perf_counter() - t0_perf, 1)}


def read_results(tmp: Path, meeting_id: int) -> dict[str, Any]:
    from listener.model import EventType
    from listener.store import Store

    with Store.open_readonly(tmp) as t:
        captions = t.captions.list_for_meeting(meeting_id)
        events = t.list_events(meeting_id)
    stopped = [e for e in events if str(e["type"]) == str(EventType.LIVE_STOPPED)]
    degraded = [e for e in events if str(e["type"]) == str(EventType.LIVE_DEGRADED)]
    latencies = [float(c["latency_ms"]) for c in captions if c.get("latency_ms") is not None]
    by_channel = {ch: stats([float(c["latency_ms"]) for c in captions if c.get("channel") == ch and c.get("latency_ms") is not None], 0)
                  for ch in ("L", "R")}
    data = dict(stopped[-1]["data"]) if stopped else {}
    return {"captions": len(captions), "latency_ms": stats(latencies, 0), "latency_by_channel": by_channel,
            "live_stopped": {k: data.get(k) for k in ("windows", "skipped", "degradations", "filtered", "echo", "captions", "errors", "model")},
            "degraded_events": [{"stage": e["data"].get("stage"), "delay_s": e["data"].get("delay_s")} for e in degraded]}


def run(args: Any, report: Report, store: Any) -> None:
    from listener.live.protocol import Hello
    from listener.platform import get_platform
    from listener.platform.common import process

    if store is None or args.meeting is None:
        raise SystemExit("replay precisa de --meeting e do banco real (só leitura)")
    meeting = store.get_meeting(int(args.meeting))
    if meeting is None:
        raise SystemExit(f"reunião {args.meeting} não existe")
    source = audio.from_meeting(store, int(args.meeting), float(args.minutes or 0), "R", both=True)
    left, right = source.left, source.right
    if left is None or right is None:
        raise SystemExit("replay precisa dos dois canais da reunião")
    threads = int(store.get_setting("live.threads") or 2)
    tmp = Path(tempfile.mkdtemp(prefix="perf-replay-"))
    try:
        mid = make_temp_db(store, meeting, tmp, bool(args.names))
        hello = Hello(0.0, names=bool(args.names), model=str(store.get_setting("live.model") or "small"), threads=threads,
                      eof_grace_s=EOF_GRACE_S, partial=False)
        report.add("replay_setup", **source.describe(), speed=args.speed, names=bool(args.names), model=hello.model, threads=threads,
                   load=args.load or None, eof_grace_s=EOF_GRACE_S, temp_meeting=mid)
        load = _start_load(args) if args.load else None
        result = _session(tmp, mid, hello, left, right, float(args.speed), get_platform(), process)
        if load is not None:
            result["load"] = _stop_load(load)
        result.update(read_results(tmp, mid))
        report.add("replay_run", **result)
        report.finish(latency_ms=result["latency_ms"], live_stopped=result["live_stopped"], captions=result["captions"],
                      skipped_zero=result["live_stopped"].get("skipped") == 0, **source.describe(), speed=args.speed)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _session(tmp: Path, mid: int, hello: Any, left: np.ndarray, right: np.ndarray, speed: float, platform: Any, process: Any) -> dict[str, Any]:
    """Lança o filho ao vivo como o app, alimenta no ritmo pedido e espera o `bye`."""
    argv = process.live_command(tmp, mid)
    env = process.live_env(hello.threads)
    t0 = time.perf_counter()
    proc = platform.spawn_child(argv, env)
    reader = Reader(proc.stdout, t0)
    reader.start()
    # o `hello` já: o filho só carrega o VAD e o Whisper depois de lê-lo, e os quadros se acumulam no pipe enquanto carrega,
    # como na gravação de verdade (o `t0_perf` da sessão é o de agora, antes da carga)
    hello_live = type(hello)(time.perf_counter(), names=hello.names, model=hello.model, threads=hello.threads,
                             eof_grace_s=hello.eof_grace_s, partial=hello.partial)
    fed = feed(proc.stdin, left, right, speed, hello_live, hello_live.t0_perf)
    try:
        code = proc.wait(timeout=EOF_GRACE_S + EXIT_SLACK_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait(timeout=5.0)
    reader.join(timeout=5.0)
    return {"ready_s": reader.ready_s, "first_caption_s": reader.first_caption_s, "exit_code": code, "messages": reader.counts,
            "child_errors": reader.errors, "bye": reader.bye, "feed": fed}


def _start_load(args: Any) -> subprocess.Popen[str]:
    import os

    env = child_env(1, {"OPENBLAS_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"})
    tail = ["load", "--threads", str(os.cpu_count() or 4), "--fraction", str(args.load)]
    proc = start_child(tail, env, args.load_class, stdin=subprocess.PIPE)
    time.sleep(1.0)
    return proc


def _stop_load(load: subprocess.Popen[str]) -> dict[str, Any]:
    if load.stdin is not None:
        load.stdin.close()
    try:
        stdout, stderr = load.communicate(timeout=30.0)
    except subprocess.TimeoutExpired:
        load.kill()
        stdout, stderr = load.communicate()
    return parse_child(stdout, stderr, load.returncode)
