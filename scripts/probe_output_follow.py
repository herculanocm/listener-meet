"""D-067: a sonda do dono para a saída do player que acompanha o padrão do Windows. Não faz parte do produto.

  python scripts/probe_output_follow.py [--seconds 120]

Roda o caminho real do player (o `Win32Platform`: o fluxo no WASAPI pelo endpoint padrão de agora, a leitura do padrão a
cada 1 s na escritora e a releitura do PortAudio para o aparelho que chegou depois) tocando SÓ ZEROS: nenhum som sai.
A cada segundo, a tela mostra o padrão de saída de agora (console/multimídia, pelo Core Audio) e o endpoint em que está a
sessão de áudio ATIVA deste processo, com "segue" ou "NÃO segue". O dono troca o padrão no Windows (o ícone de som da
barra de tarefas) quantas vezes quiser: a cada troca, em até ~2 s, a linha volta a "segue".

- Não depende do app aberto nem mexe nele: processo próprio, PortAudio próprio, banco temporário apagado no fim; não abre
  microfone; não troca o padrão do Windows (isso é do dono).
- O nome do aparelho aparece só na tela (para o dono reconhecer); o log do player, também na tela, leva só o id do endpoint.
- No fim: quantos segundos seguiu e não seguiu, as trocas feitas e os buracos da saída."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SR = 16000
CHUNK_S = 60
TICK_S = 0.1                     # o tique da "interface" (o `poll` do player é de 100 ms)


def silent_meeting(store, seconds: int) -> int:
    """Uma reunião de zeros (L e R) de `seconds` segundos, em pedaços de 1 min."""
    from listener.capture.audio import silent_flac

    mid = store.create_meeting("sonda: saída que acompanha", None, None, None)
    for idx, t0 in enumerate(range(0, seconds, CHUNK_S)):
        frames = min(CHUNK_S, seconds - t0) * SR
        for ch in ("L", "R"):
            store.insert_chunk(mid, ch, idx, t0 * 1000, frames, SR, 0, silent_flac(SR, frames))
    store.finish_meeting(mid, seconds * 1000)
    return mid


def session_endpoints(flows) -> list[str]:
    """Os endpoints de saída em que este processo tem uma sessão ATIVA agora."""
    from listener.platform.win32.sessions import STATE_ACTIVE

    me = os.getpid()
    return [eid for eid, group in flows.read().items() if any(s.pid == me and s.state == STATE_ACTIVE for s in group)]


def label(names: dict[str, str], endpoint_id: str) -> str:
    from listener.platform.base import log_id

    return f"{names.get(endpoint_id) or '?'} ({log_id(endpoint_id)[-14:]})" if endpoint_id else "(nenhum)"


def status_line(names: dict[str, str], default: str, active: list[str]) -> tuple[str, bool | None]:
    """A linha da tela e se segue (None: trocando, sem sessão ativa ou com duas)."""
    if len(active) != 1:
        where = " e ".join(label(names, e) for e in active) if active else "(nenhuma ativa)"
        return f"padrão: {label(names, default):<58} sessão: {where:<58} trocando…", None
    follows = active[0] == default
    return f"padrão: {label(names, default):<58} sessão: {label(names, active[0]):<58} " + \
        ("segue" if follows else "NÃO segue"), follows


def run(seconds: int) -> int:
    from listener.platform import get_platform
    from listener.platform.base import log_id
    from listener.platform.win32 import endpoints, sessions
    from listener.store import Store
    from listener.ui.player import PLAYING, PlaybackEngine, PlayRequest
    from listener.ui.player_route import Reroute

    plat = get_platform()
    plat.refresh_audio_devices()                        # como o primeiro "Tocar" do app (U-043)
    reader = endpoints.EndpointReader()
    flows = sessions.FlowSessions(endpoints.FLOW_RENDER)
    counts = {True: 0, False: 0, None: 0}
    last: tuple[str, list[str]] = ("", [])
    with tempfile.TemporaryDirectory(prefix="listener-sonda-") as tmp:
        store = Store(Path(tmp))
        mid = silent_meeting(store, seconds + 30)
        engine = PlaybackEngine(store, plat.open_output, probe=plat.default_output_id)
        reroute = Reroute(engine, plat.refresh_audio_devices, plat.audio_devices_changed)
        engine.play(PlayRequest(mid, 0, title="sonda"))
        print(f"tocando zeros por {seconds} s; troque o padrão de saída no Windows e veja a coluna da direita (Ctrl+C para)")
        t_end, next_line = time.monotonic() + seconds, time.monotonic() + 1.0
        try:
            while time.monotonic() < t_end and engine.snapshot().state == PLAYING:
                reroute.tick()
                if time.monotonic() >= next_line:
                    next_line += 1.0
                    reading = reader.read()
                    names = {e.id: e.name for e in reading.render} if reading is not None else {}
                    last = (endpoints.default_id(endpoints.FLOW_RENDER), session_endpoints(flows))
                    line, follows = status_line(names, *last)
                    counts[follows] += 1
                    print(time.strftime("%H:%M:%S"), line, flush=True)
                time.sleep(TICK_S)
        except KeyboardInterrupt:
            print("parado pelo Ctrl+C")
        finally:
            st = engine.snapshot()
            engine.stop()
            engine.shutdown()
            store.close()
    print(f"\nresumo: segue {counts[True]} s, NÃO segue {counts[False]} s, trocando {counts[None]} s; "
          f"{engine.route.moves} troca(s) de aparelho; {engine.underflows} buraco(s) no último fluxo; estado no fim: {st.state}"
          + (f" ({st.note})" if st.note else ""))
    print(f"última leitura: padrão {log_id(last[0])}; sessão ativa {', '.join(log_id(e) for e in last[1]) or '(nenhuma)'}")
    return 0 if counts[False] == 0 else 1


def main() -> int:
    if sys.platform != "win32":
        print("a sonda é do Windows")
        return 2
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=int, default=120)
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")            # type: ignore[union-attr]
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    for name in ("listener.ui.player", "listener.platform.win32.output"):   # as aberturas e trocas, só com o id do endpoint
        logging.getLogger(name).setLevel(logging.INFO)
    return run(max(5, args.seconds))


if __name__ == "__main__":
    raise SystemExit(main())
