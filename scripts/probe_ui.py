"""Sondas da interface usadas pelo `win_probe` (F5.4 L1, A-21). Não fazem parte do produto.

tempos     a abertura das janelas (warm-up, Configurações, reunião, Histórico, fila e revisão) e o tique do widget de
           gravação, num processo por rodada. Cada rodada abre as seis janelas em sequência, 7 vezes (rodadas alternadas:
           o que deriva no tempo pesa igual em todas), e fica com a mediana de cada uma; o tique é `update_state` com o
           nível mudando mais a pintura do que muda (a hora e os medidores), 300 vezes (60 s a 5 Hz), com mediana e p95.
           São 3 rodadas por código e vale o menor dos 3 (o ruído da máquina só soma tempo). Com `--base PASTA` (uma
           cópia do código de antes, por exemplo `git archive 0ec729e listener tests`), as rodadas alternam base e atual
           no mesmo dia (Aceite 5).
           A abertura é construir, mostrar fora da tela, processar os eventos e pintar a janela inteira: [fato, 28/10] a
           janela em (-10000, -10000) não fica exposta e o Windows não a pinta sozinho, então a pintura é a do `grab()`
           (o mesmo `paintEvent` e os mesmos `draw*` do estilo, sem a cópia para a tela). Os aparelhos são os da
           plataforma falsa dos testes (mede a interface, não o áudio: o warm-up abriria o microfone de verdade a cada
           vez), e o tema é aplicado com a plataforma real, como no app. [fato, 28/10] nesta CPU híbrida (Core Ultra 5
           235U: núcleos P e E) o mesmo código variou até 40 % entre processos, conforme o Windows os punha num núcleo ou
           noutro: cada filho fica preso aos núcleos da classe de eficiência mais alta (`GetSystemCpuSetInformation`).
cortados   rótulo, botão, aba, cabeçalho de tabela ou título de cartão (L1b) com o texto maior que o espaço; o cortado de
           propósito, com reticências e o texto inteiro na dica, não conta.
pequenos   o alvo de clique do Aceite 3: botão, campo e combo abaixo de 32 por 32 px; caixa de seleção abaixo de 24 px.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

WINDOWS = ("warmup", "configuracoes", "reuniao", "historico", "fila", "revisao")
ROUNDS, RUNS, TICKS = 7, 3, 300
TARGET_PX, CHECK_PX = 32, 24          # Aceite 3: botão, campo e combo; caixa de seleção
CARD_TITLE_INSET = 6                  # a folga à direita do título do cartão (o `TITLE_INSET` de `ui/style.py`)
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"


def apply_theme(app: Any, platform: Any, scheme: Any = None) -> bool:
    """O tema da F5.4 quando o código tem (o da base, de antes do L1, não tem): False sem ele."""
    try:
        from listener.ui import theme
    except ImportError:
        return False
    theme.apply(app, platform, scheme)
    return True


def release_theme(app: Any) -> None:
    """O estilo em Python sai antes da derrubada do PySide (A-09; R-06)."""
    try:
        from listener.ui import theme
    except ImportError:
        return
    theme.release(app)


# ---------------------------------------------------------------- tempos (processo filho)
def _settle(app: Any) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def open_ms(app: Any, make: Callable[[], Any], before_close: Callable[[Any], None] | None = None) -> float:
    """Construir, mostrar fora da tela sem ativar, processar os eventos e pintar a janela inteira (`grab`, a primeira
    pintura que a tela faria); a janela é apagada depois."""
    from PySide6.QtCore import Qt

    t0 = time.perf_counter()
    w = make()
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.move(-10000, -10000)
    w.show()
    app.processEvents()
    w.grab()
    elapsed = (time.perf_counter() - t0) * 1000
    if before_close is not None:
        before_close(w)
    w.close()
    w.deleteLater()
    _settle(app)
    return elapsed


def tick_samples(app: Any, ticks: int = TICKS) -> list[float]:
    """O tique do widget de gravação: o estado com o nível mudando e a pintura do que muda (a hora e os dois medidores,
    com o fundo do widget por trás), como a 5 Hz na gravação."""
    from PySide6.QtCore import Qt

    from listener.ui.widget import RecordingWidget

    rw = RecordingWidget()
    rw.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    rw.move(-10000, -10000)
    rw.show()
    app.processEvents()
    health = {"L": "ok", "R": "ok", "db": "ok"}
    samples: list[float] = []
    for i in range(ticks):
        level = 0.05 + 0.3 * abs(math.sin(i / 7))
        t0 = time.perf_counter()
        rw.update_state(i * 0.2, {"L": level, "R": level / 2}, health, False, False, 0)
        app.processEvents()
        rw.grab(rw.time_label.geometry().united(rw.meter_l.geometry()).united(rw.meter_r.geometry()))
        samples.append((time.perf_counter() - t0) * 1000)
    rw.close()
    rw.deleteLater()
    _settle(app)
    return samples


CPU_SET_ENTRY = 32                     # bytes de um SYSTEM_CPU_SET_INFORMATION (o tipo 0, CpuSetInformation)


def performance_mask() -> int:
    """Os processadores lógicos (grupo 0) da classe de eficiência mais alta: os núcleos P de um Intel híbrido; 0 quando o
    Windows não responde ou a CPU não é híbrida (todos na mesma classe)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetSystemCpuSetInformation.argtypes = (ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), wintypes.HANDLE,
                                               wintypes.ULONG)
    size = wintypes.ULONG(0)
    k32.GetSystemCpuSetInformation(None, 0, ctypes.byref(size), k32.GetCurrentProcess(), 0)
    buf = ctypes.create_string_buffer(size.value)
    if not size.value or not k32.GetSystemCpuSetInformation(buf, size, ctypes.byref(size), k32.GetCurrentProcess(), 0):
        return 0
    raw, cpus, offset = buf.raw, [], 0
    while offset + CPU_SET_ENTRY <= size.value:
        length, kind = int.from_bytes(raw[offset:offset + 4], "little"), int.from_bytes(raw[offset + 4:offset + 8], "little")
        group = int.from_bytes(raw[offset + 12:offset + 14], "little")
        if kind == 0 and group == 0:
            cpus.append((raw[offset + 14], raw[offset + 18]))          # LogicalProcessorIndex, EfficiencyClass
        offset += max(length, CPU_SET_ENTRY)
    classes = {ec for _lp, ec in cpus}
    return sum(1 << lp for lp, ec in cpus if ec == max(classes)) if len(classes) > 1 else 0


def pin_to_performance_cores() -> int:
    """O processo nos núcleos P (quando há); devolve a máscara usada (0 = sem mudar)."""
    import ctypes
    from ctypes import wintypes

    mask = performance_mask()
    if mask:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessAffinityMask.argtypes = (wintypes.HANDLE, ctypes.c_size_t)
        if not k32.SetProcessAffinityMask(k32.GetCurrentProcess(), mask):
            return 0
    return mask


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def measure(root: str | None, rounds: int) -> dict[str, Any]:
    """(processo filho) As medianas de abertura de cada janela e o tique; `root` troca o código medido (a base)."""
    if root:
        sys.path.insert(0, str(Path(root).resolve()))
    os.environ.pop("QT_QPA_PLATFORM", None)
    mask = pin_to_performance_cores()
    from PySide6.QtGui import QFontInfo
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    import listener
    from listener.platform import get_platform
    from listener.store import Store
    from listener.ui.common import install_translations
    from listener.ui.history import HistoryWindow
    from listener.ui.meeting import MeetingWindow
    from listener.ui.player import Player
    from listener.ui.queue import QueueDialog
    from listener.ui.review import ReviewDialog
    from listener.ui.settings_min import SettingsDialog
    from listener.ui.warmup import WarmupDialog
    from tests.fake_platform import FakePlatform

    install_translations(app)
    themed = apply_theme(app, get_platform())
    tmp = Path(tempfile.mkdtemp(prefix="listener-tempos-"))
    store = Store(tmp / "db")
    store.upsert_me("Teste")
    store.seed_default_presets()
    mid = store.create_meeting("Comitê de investimentos", "sistema", None, None)
    store.finish_meeting(mid, 1000)
    fake = FakePlatform(tmp)
    player = Player(store)
    makers: dict[str, tuple[Callable[[], Any], Callable[[Any], None] | None]] = {
        "warmup": (lambda: WarmupDialog(fake, store), None),
        "configuracoes": (lambda: SettingsDialog(store, fake, {}), None),
        "reuniao": (lambda: MeetingWindow(store, mid, player=player), None),
        "historico": (lambda: HistoryWindow(store), lambda w: w.page.shutdown()),
        "fila": (lambda: QueueDialog(store), None),
        "revisao": (lambda: ReviewDialog(store, mid), None),
    }
    samples: dict[str, list[float]] = {name: [] for name in WINDOWS}
    for _ in range(rounds):
        for name in WINDOWS:
            make, before_close = makers[name]
            samples[name].append(open_ms(app, make, before_close))
    ticks = tick_samples(app)
    player.shutdown()
    _settle(app)
    store.close()
    release_theme(app)
    return {"path": str(Path(listener.__file__).resolve().parent), "cpus": f"{mask:#x}", "themed": themed, "style": app.style().name(),
            "font": f"{app.font().family()} {QFontInfo(app.font()).pixelSize()} px",
            "open_ms": {name: round(statistics.median(v), 1) for name, v in samples.items()},
            "tick_ms": round(statistics.median(ticks), 3), "tick_p95_ms": round(p95(ticks), 3)}


# ---------------------------------------------------------------- tempos (processo pai)
def _child(script: Path, root: str | None, rounds: int) -> dict[str, Any]:
    argv = [sys.executable, str(script), "_tempos", "--rounds", str(rounds), *(["--root", root] if root else [])]
    done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=900, check=False)  # noqa: S603
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
    if done.returncode != 0 or not lines:
        return {"error": f"saída {done.returncode}", "stderr": done.stderr[-2000:]}
    return dict(json.loads(lines[-1]))


def _best(runs: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in runs if "open_ms" in r]
    if not ok:
        return {}
    return {"open_ms": {name: min(r["open_ms"][name] for r in ok) for name in WINDOWS},
            "tick_ms": min(r["tick_ms"] for r in ok),
            "tick_p95_ms": [min(r["tick_p95_ms"] for r in ok), max(r["tick_p95_ms"] for r in ok)]}


def _delta(best: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    if not best or not base:
        return {}
    pct = {name: round(100 * (best["open_ms"][name] / base["open_ms"][name] - 1), 1) for name in WINDOWS}
    pct["tique"] = round(100 * (best["tick_ms"] / base["tick_ms"] - 1), 1)
    return pct


def run_tempos(script: Path, base: str | None, runs: int, rounds: int, out: Callable[..., None]) -> None:
    """3 rodadas por código, alternando base e atual (ABBA...) quando há base; o menor de cada janela vale."""
    variants: list[tuple[str, str | None]] = [("atual", None)] + ([("base", base)] if base else [])
    results: dict[str, list[dict[str, Any]]] = {label: [] for label, _root in variants}
    for run in range(runs):
        order = variants if run % 2 == 0 else variants[::-1]
        for label, root in order:
            data = _child(script, root, rounds)
            results[label].append(data)
            out("tempos_rodada", code=label, run=run + 1, **data)
    best = {label: _best(rs) for label, rs in results.items()}
    out("tempos", rounds=rounds, runs=runs, best=best, delta_pct=_delta(best.get("atual", {}), best.get("base", {})))


# ---------------------------------------------------------------- cortados e pequenos
def _name(w: Any) -> str:
    text = ""
    for attr in ("text", "title", "windowTitle"):
        fn = getattr(w, attr, None)
        if callable(fn):
            text = str(fn() or "")
            if text:
                break
    text = " ".join(text.split())
    return f"{type(w).__name__}{'#' + w.objectName() if w.objectName() else ''} {text[:48]!r}"


def _internal(w: Any) -> bool:
    """Peças internas do Qt (setas de rolagem das abas, o botão de limpar do campo, o campo de um combo editável)."""
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QTabBar

    parent = w.parentWidget()
    return w.objectName().startswith("qt_") or isinstance(parent, QTabBar | QLineEdit | QComboBox | QAbstractSpinBox)


def _label_cut(lab: Any) -> dict[str, Any] | None:
    pix = lab.pixmap()
    text = lab.text()
    if (pix is not None and not pix.isNull()) or not text.strip():
        return None
    if text.rstrip().endswith(ELLIPSIS) and lab.toolTip():
        return None                                   # cortado de propósito, inteiro na dica
    if lab.wordWrap():
        need = lab.heightForWidth(lab.width())
        return {"need_h": need, "h": lab.height()} if need > lab.height() + 1 else None
    need = lab.sizeHint().width()
    return {"need_w": need, "w": lab.width()} if need > lab.width() + 1 else None


def _button_cut(btn: Any) -> dict[str, Any] | None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QToolButton

    if not btn.text() or (isinstance(btn, QToolButton) and btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly):
        return None
    need = btn.sizeHint().width()
    return {"need_w": need, "w": btn.width()} if need > btn.width() + 1 else None


def _tabs_cut(bar: Any) -> list[dict[str, Any]]:
    from PySide6.QtWidgets import QStyle

    space = bar.style().pixelMetric(QStyle.PixelMetric.PM_TabBarTabHSpace, None, bar)
    found = []
    for i in range(bar.count()):
        if not bar.isTabVisible(i):
            continue
        rect, text = bar.tabRect(i), bar.tabText(i)
        need = bar.fontMetrics().horizontalAdvance(text) + space
        if need > rect.width() + 1 or not bar.rect().contains(rect):
            found.append({"what": f"aba {text!r}", "need_w": need, "w": rect.width(), "inside": bar.rect().contains(rect)})
    return found


def _header_cut(header: Any) -> list[dict[str, Any]]:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QStyle

    model = header.model()
    if model is None or header.orientation() != Qt.Orientation.Horizontal:
        return []
    style, fm = header.style(), header.fontMetrics()
    pad = 2 * style.pixelMetric(QStyle.PixelMetric.PM_HeaderMargin, None, header)
    mark = style.pixelMetric(QStyle.PixelMetric.PM_HeaderMarkSize, None, header)
    found = []
    for logical in range(header.count()):
        if header.isSectionHidden(logical):
            continue
        text = str(model.headerData(logical, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
        need = max(fm.horizontalAdvance(line) for line in text.split("\n")) + pad
        if header.isSortIndicatorShown() and header.sortIndicatorSection() == logical:
            need += mark
        if text and need > header.sectionSize(logical) + 1:
            found.append({"what": f"cabeçalho {text!r}", "need_w": need, "w": header.sectionSize(logical)})
    return found


def _group_cut(box: Any) -> dict[str, Any] | None:
    """F5.4 L1b: o título do cartão (o `ThemeStyle` o pinta dentro, `TITLE_INSET` abaixo) no espaço que o grupo tem."""
    from PySide6.QtWidgets import QStyle, QStyleOptionGroupBox

    if not box.title():
        return None
    opt = QStyleOptionGroupBox()
    box.initStyleOption(opt)
    label = box.style().subControlRect(QStyle.ComplexControl.CC_GroupBox, opt, QStyle.SubControl.SC_GroupBoxLabel, box)
    need = label.left() + box.fontMetrics().horizontalAdvance(box.title()) + CARD_TITLE_INSET
    return {"need_w": need, "w": box.width()} if need > box.width() + 1 else None


def _cut(w: Any) -> list[dict[str, Any]]:
    from PySide6.QtWidgets import QAbstractButton, QGroupBox, QHeaderView, QLabel, QTabBar

    if isinstance(w, QLabel):
        found = _label_cut(w)
    elif isinstance(w, QAbstractButton):
        found = _button_cut(w)
    elif isinstance(w, QGroupBox):
        found = _group_cut(w)
    elif isinstance(w, QTabBar):
        return [{"widget": _name(w), **item} for item in _tabs_cut(w)]
    elif isinstance(w, QHeaderView):
        return [{"widget": _name(w), **item} for item in _header_cut(w)]
    else:
        return []
    return [{"widget": _name(w), **found}] if found else []


def _small(w: Any) -> dict[str, Any] | None:
    from PySide6.QtWidgets import QAbstractSpinBox, QCheckBox, QComboBox, QLineEdit, QPushButton, QRadioButton, QToolButton

    size = {"w": w.width(), "h": w.height()}
    if isinstance(w, QCheckBox | QRadioButton):
        return {"widget": _name(w), "min": f"altura {CHECK_PX}", **size} if w.height() < CHECK_PX else None
    if isinstance(w, QPushButton | QToolButton):
        small = w.height() < TARGET_PX or w.width() < TARGET_PX
        return {"widget": _name(w), "min": f"{TARGET_PX} x {TARGET_PX}", **size} if small else None
    if isinstance(w, QLineEdit | QComboBox | QAbstractSpinBox):
        return {"widget": _name(w), "min": f"altura {TARGET_PX}", **size} if w.height() < TARGET_PX else None
    return None


def layout_report(root: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Os `cortados` e os `pequenos` do que está à vista na janela (abas escondidas e janelas fechadas ficam fora)."""
    from PySide6.QtWidgets import QWidget

    cut: list[dict[str, Any]] = []
    small: list[dict[str, Any]] = []
    for w in (root, *root.findChildren(QWidget)):
        if (w is not root and not w.isVisibleTo(root)) or w.width() <= 0 or _internal(w):
            continue
        cut.extend(_cut(w))
        found = _small(w)
        if found is not None:
            small.append(found)
    return cut, small
