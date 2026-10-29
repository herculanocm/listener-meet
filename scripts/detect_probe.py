"""F5.7 (L1): a sonda do sensor do aviso de reunião sem gravação (não faz parte do produto; substitui o `watch.py` da
consulta de 29/10). Para o dono rodar numa chamada real, com o app fechado ou aberto:

    .venv\\Scripts\\python.exe scripts\\detect_probe.py --seconds 120

A cada segundo (ou antes, quando o indicador de microfone do Windows muda), uma linha por app com sinal: executável, pid
da raiz, microfone, pico, serviço, se a leitura é só pelo registro e quantas entradas do indicador estão em uso. Os
navegadores da lista de fábrica pedem o título; ele nunca é impresso, só o serviço reconhecido. Só leitura: nenhuma stream,
nenhum som, nada escrito no registro. Saída sem título de janela, caminho nem nome de aparelho (D-053)."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from listener.platform import get_platform  # noqa: E402
from listener.platform.base import KIND_BROWSER, Platform  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="sonda do sensor do aviso de reunião (F5.7)")
    ap.add_argument("--seconds", type=float, default=30.0, help="por quanto tempo ler (padrão 30)")
    ap.add_argument("--degraded", action="store_true", help="só o registro, como depois de três falhas do COM")
    args = ap.parse_args()
    plat = get_platform()
    browsers = frozenset(w.exe for w in plat.watched_apps() if w.kind == KIND_BROWSER)
    watch = plat.mic_watch()
    print(f"sensor: aviso do registro {'armado' if watch else 'indisponível (consulta a cada 1 s)'}; "
          f"{len(browsers)} navegadores com título pedido")
    t_end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < t_end:
            changed = False
            if watch is not None:
                changed = watch.wait(1.0)
            else:
                time.sleep(1.0)
            _line(plat, browsers, args.degraded, changed)
    finally:
        plat.close_audio_activity()                       # na mesma thread que leu
        if watch is not None:
            watch.close()
    return 0


def _line(plat: Platform, browsers: frozenset[str], degraded: bool, changed: bool) -> None:
    t0 = time.perf_counter()
    try:
        activity = plat.audio_activity(browsers, degraded=degraded)
    except Exception as e:                                 # a sonda conta a falha, não esconde
        print(f"{time.strftime('%H:%M:%S')} leitura falhou: {type(e).__name__}")
        return
    ms = (time.perf_counter() - t0) * 1000
    users = plat.mic_users()
    head = (f"{time.strftime('%H:%M:%S')} {'[registro mudou] ' if changed else ''}{ms:5.1f} ms | "
            f"indicador em uso: {'?' if users is None else len(users)}")
    if activity is None:
        print(head + " | sem leitura")
        return
    print(head + f" | degraded {activity.degraded} | {len(activity.apps)} apps")
    for a in activity.apps:
        if a.mic or a.peak >= 0.001:
            print(f"    {a.exe:<26} pid {a.pid:>6}  mic {'SIM' if a.mic else 'não':<3}  pico {a.peak:.3f}  "
                  f"serviço {a.service or '-'}")


if __name__ == "__main__":
    raise SystemExit(main())
