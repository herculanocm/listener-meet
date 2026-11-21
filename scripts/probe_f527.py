"""F5.27 lote 0 — a sonda da gravação da tela (spec specs/features/F5.27-gravar-a-tela.md, aprovada em 2026-11-20, D-113). Não
faz parte do produto. Roda no venv do Windows (`.venv/Scripts/python.exe`).

  python scripts/probe_f527.py [--seconds 120] [--fps 1] [--max-side 1920] [--monitor N]
  python scripts/probe_f527.py --encode [--minutes 5]

Sem `--encode` (o go/no-go do Aceite): escolhe o maior monitor (ou o `--monitor N`, na numeração da F5.21, da esquerda para
a direita) e roda o `ScreenRecorder` com o BitBlt real da plataforma por `--seconds`, num processo filho que pede os pixels
físicos (`use_physical_pixels`: sem o Qt, o monitor com escala sairia em pixels lógicos) e se rebaixa a abaixo do normal
(`lower_own_priority`), como o filho `--screen` vai fazer. O filho imprime uma linha JSON: a CPU do processo em % de um núcleo
(o delta de `psutil.Process().cpu_times()` sobre o tempo de parede, medido no próprio filho, depois de importar o PyAV e
abrir o codificador), os ms do print, da redução com a codificação e do ciclo dos dois (p50 e p95), os bytes por hora
extrapolados, os quadros, os pulados, as falhas, a taxa efetiva, o monitor (número, tamanho e se é o principal; nunca a
chave do sistema) e o codificador. Nenhuma imagem é guardada. Os bytes por hora só valem para o que passou na tela durante a
medida: uma área de trabalho parada é quase só quadros pulados pelo codificador (veja `--encode` para slides).

Com `--encode` (a escolha do CRF e do intervalo de quadros-chave do `segment.py`): slides sintéticos de 1920 x 1080 (texto
de vários tamanhos e uma tabela de letra pequena, desenhados pelo Qt fora da tela, um slide novo a cada 40 s) a um quadro por
segundo, em pedaços de 60 s, em três cenários: `slides`; `slides+camera` (um quadrado de 320 x 180 que muda a cada quadro,
como a câmera de quem apresenta); `rolagem` (a tabela rolando 24 px por quadro, o pior caso). Uma linha JSON por cenário,
CRF e intervalo: MB por hora, ms de codificação p50 e p95 e o PSNR do texto pequeno decodificado contra o original."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                     # o pacote deste checkout (o .pth do venv pode apontar para outro)

W, H = 1920, 1080
SLIDE_S = 40
CAMERA = (1560, 860, 320, 180)                    # x, y, largura, altura do quadrado da câmera
TABLE = (80, 520, 1100, 480)                      # a tabela de letra pequena (a região do PSNR)


# ---------------------------------------------------------------- a gravação real (no filho)
def _pick_monitor(monitors: tuple[Any, ...], number: int | None) -> tuple[int, Any]:
    if not monitors:
        raise SystemExit("nenhum monitor lido")
    if number is not None:
        if not 1 <= number <= len(monitors):
            raise SystemExit(f"monitor {number} não existe (há {len(monitors)})")
        return number, monitors[number - 1]
    best = max(range(len(monitors)), key=lambda i: (monitors[i].rect.area, monitors[i].primary))
    return best + 1, monitors[best]


def _child(args: argparse.Namespace) -> None:
    import psutil

    from listener.platform import get_platform
    from listener.screen_rec.recorder import ScreenRecorder
    from listener.screen_rec.segment import _codec_for, fit_size

    platform = get_platform()
    physical = platform.use_physical_pixels()
    lowered = platform.lower_own_priority()
    number, monitor = _pick_monitor(platform.monitors(), args.monitor)
    codec = _codec_for(fit_size(monitor.rect.width, monitor.rect.height, args.max_side))   # o PyAV importado e o x264 aberto
    sizes: list[tuple[int, int]] = []
    status: list[str] = []
    t0 = time.perf_counter()
    rec = ScreenRecorder(platform.grab_screen, monitor.rect, lambda: int((time.perf_counter() - t0) * 1000),
                         on_segment=lambda s: sizes.append((s.width, s.height)), on_status=status.append,
                         fps=args.fps, max_side=args.max_side)
    stop = threading.Event()
    timer = threading.Timer(args.seconds, stop.set)
    proc = psutil.Process()
    cpu0, wall0 = proc.cpu_times(), time.perf_counter()
    timer.start()
    rec.run(stop)
    wall = time.perf_counter() - wall0
    cpu1 = proc.cpu_times()
    cpu = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    summary = rec.stats.summary()
    out = {"probe": "f527_tela", "seconds": round(wall, 1), "fps": args.fps, "max_side": args.max_side,
           "monitor": {"number": number, "width": monitor.rect.width, "height": monitor.rect.height, "primary": monitor.primary},
           "physical_pixels": physical, "below_normal": lowered, "recorded": list(sizes[0]) if sizes else None, "codec": codec,
           "cpu_pct_core": round(cpu / wall * 100, 1), **summary,
           "bytes_per_hour": round(summary["bytes"] / wall * 3600), "fps_effective": round(summary["frames"] / wall, 2),
           "status": status}
    print(json.dumps(out, ensure_ascii=False), flush=True)


def _record(args: argparse.Namespace) -> int:
    argv = [sys.executable, str(Path(__file__).resolve()), "--child", "--seconds", str(args.seconds), "--fps", str(args.fps),
            "--max-side", str(args.max_side)] + (["--monitor", str(args.monitor)] if args.monitor else [])
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(ROOT), env.get("PYTHONPATH", "")) if p)
    return subprocess.run(argv, env=env, check=False).returncode  # noqa: S603 (o próprio script, no mesmo Python)


# ---------------------------------------------------------------- o CRF e o intervalo de quadros-chave (slides sintéticos)
def _slides(n: int) -> list[Any]:
    """`n` slides BGRA de W x H desenhados pelo Qt fora da tela: título, tópicos e uma tabela de letra pequena."""
    if sys.platform != "win32":                   # no Windows, o "offscreen" não acha as fontes (sai tudo em quadrados)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

    _app = QGuiApplication.instance() or QGuiApplication([])
    out = []
    for k in range(n):
        img = QImage(W, H, QImage.Format.Format_ARGB32)
        img.fill(QColor(250, 250, 252))
        p = QPainter(img)
        p.fillRect(0, 0, W, 120, QColor(20, 60, 120))
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("Segoe UI", 40, QFont.Weight.Bold))
        p.drawText(QRect(60, 20, W - 120, 90), Qt.AlignmentFlag.AlignVCenter, f"Resultados do trimestre {k + 1}: carteira e risco")
        p.setPen(QColor(30, 30, 30))
        p.setFont(QFont("Segoe UI", 22))
        for i in range(6):
            p.drawText(80, 200 + i * 48, f"• Item {k}.{i}: inadimplência de {3.1 + i * 0.4 + k:.1f}% no cedente {chr(65 + i)} em 90 dias")
        p.setFont(QFont("Segoe UI", 10))
        x, y, w, h = TABLE
        for r in range(24):
            for c in range(7):
                p.drawRect(x + c * (w // 7), y + r * (h // 24), w // 7, h // 24)
                p.drawText(x + c * (w // 7) + 4, y + r * (h // 24) + 14, f"{(r * 7 + c) * 1234.56 + k:,.2f}")
        p.fillRect(1300, 520, 500, 300, QColor(230, 236, 245))
        for i in range(8):
            p.fillRect(1330 + i * 58, 800 - (i * 23 + k * 11) % 260, 40, (i * 23 + k * 11) % 260, QColor(40, 110, 200))
        p.end()
        arr = np.frombuffer(img.constBits(), np.uint8, count=W * H * 4).reshape(H, W, 4).copy()
        out.append(arr)
    return out


def _scene(kind: str, slides: list[Any], t: int) -> Any:
    import numpy as np

    frame = slides[(t // SLIDE_S) % len(slides)]
    if kind == "rolagem":
        x, y, w, h = TABLE
        frame = frame.copy()
        shift = (t * 24) % h
        frame[y:y + h, x:x + w] = np.roll(slides[0][y:y + h, x:x + w], -shift, axis=0)
    elif kind == "slides+camera":
        x, y, w, h = CAMERA
        frame = frame.copy()
        yy, xx = np.mgrid[0:h, 0:w]
        rng = np.random.default_rng(t)
        tile = (128 + 80 * np.sin((xx + t * 7) / 23.0) * np.cos((yy - t * 5) / 17.0)).astype(np.int16)
        tile = np.clip(tile + rng.integers(-6, 7, tile.shape), 0, 255).astype(np.uint8)
        frame[y:y + h, x:x + w, 0], frame[y:y + h, x:x + w, 1], frame[y:y + h, x:x + w, 2] = tile, tile // 2 + 60, 255 - tile
    return frame


def _psnr(a: Any, b: Any) -> float:
    import numpy as np

    mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
    return 99.0 if mse == 0 else round(10 * np.log10(255.0 ** 2 / mse), 2)


def _encode_case(kind: str, slides: list[Any], crf: int, keyint: int, seconds: int) -> dict[str, Any]:
    from listener.screen_rec.recorder import percentile
    from listener.screen_rec.segment import SegmentEncoder, decode_segment

    total, times, first = 0, [], None
    for start in range(0, seconds, 60):
        enc = SegmentEncoder(W, H, W, 1, crf=crf, keyint=keyint)
        for t in range(start, min(start + 60, seconds)):
            frame = _scene(kind, slides, t)
            t0 = time.perf_counter()
            enc.add(frame, t * 1000)
            times.append((time.perf_counter() - t0) * 1000)
        seg = enc.close()
        if seg is None:
            raise RuntimeError("pedaço sem quadros")
        total += len(seg.data)
        first = first or seg.data
    _t, rgb = next(iter(decode_segment(first)))
    x, y, w, h = TABLE
    src = _scene(kind, slides, 0)[y:y + h, x:x + w, 2::-1]
    return {"probe": "f527_encode", "scene": kind, "crf": crf, "keyint": keyint, "mb_per_hour": round(total / seconds * 3600 / 1e6, 1),
            "encode_ms_p50": round(percentile(times, 50) or 0, 1), "encode_ms_p95": round(percentile(times, 95) or 0, 1),
            "psnr_table_db": _psnr(rgb[y:y + h, x:x + w], src)}


def _encode(args: argparse.Namespace) -> int:
    slides = _slides(4)
    seconds = args.minutes * 60
    for kind in ("slides", "slides+camera", "rolagem"):
        for crf in (20, 23, 26, 28):
            for keyint in (30, 300):
                print(json.dumps(_encode_case(kind, slides, crf, keyint, seconds), ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--fps", type=int, default=1)
    ap.add_argument("--max-side", type=int, default=1920)
    ap.add_argument("--monitor", type=int, default=None)
    ap.add_argument("--encode", action="store_true")
    ap.add_argument("--minutes", type=int, default=5)
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.encode:
        return _encode(args)
    if args.child:
        _child(args)
        return 0
    return _record(args)


if __name__ == "__main__":
    raise SystemExit(main())
