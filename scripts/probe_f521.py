"""As telas e as medidas da F5.21 no `win_probe` (não fazem parte do produto).

`f521_shots(store, shot, app, platform)`, no `win_probe shots`: a bandeja com o cartão com a chave "Com a tela" ligada, a linha
"com a tela · janela do Teams", uma opção com "[tela]" e a miniatura no chat; a confirmação da primeira vez; a janela maior; a
aba "Sugestões" com as miniaturas. As imagens são sintéticas: nenhuma tela de verdade vai para as fotos. `f521_timings`, na
linha `f521_tempos` do mesmo subcomando (o Aceite "A miniatura no chat"): com 2.000 falas na bandeja, pôr cada uma de 40
miniaturas pelo host e cada legenda nova depois delas, na thread da interface (meta: abaixo de 5 ms).

`f521_screen(app, platform, store, keep)`, no `win_probe tela` (o Aceite, só a pedido do dono, porque olha a tela de verdade e
mostra por menos de 1 s um quadrado colorido no canto do monitor principal e, com um app de reunião aberto, outro no meio da
janela dele, sem roubar o foco): o print e o preparo de cada monitor e da janela da reunião (os ms e os kB, A-02); um pedido
com a tela, do clique à miniatura no chat (até 300 ms) e a thread da interface nele (até 5 ms), com a resposta do provedor
falso (`LISTENER_LLM=fake`: nada sai da máquina); a janela de cor única excluída da captura por cima do retângulo, com zero
pixels dela no print (A-04), e a mesma janela sem a exclusão como controle; a janela de outro processo por cima da reunião,
que aparece no print cru e sai preta na imagem enviada (A-09); o contexto de DPI da thread (A-03). Nada é gravado em disco,
salvo com `--guardar PASTA` (A-40): as imagens de cada monitor e da janela da reunião como iriam ao provedor, para o dono
julgar se o texto pequeno de uma planilha aberta num monitor 4K ainda se lê. Nenhum título de janela sai daqui."""
from __future__ import annotations

import itertools
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from probe_f517 import CHAT, _discard, _insert, _offscreen

MAGENTA = (255, 0, 255)
THUMB_GOAL_MS = 300.0                    # do clique à miniatura no chat (o Aceite)
UI_GOAL_MS = 5.0                         # a thread da interface num pedido com a tela (o Aceite)
# A-09: a janela de outro processo por cima da reunião: sem foco, por cima, de cor única, posta em pixels físicos pelo
# `SetWindowPos` (o Qt 6 roda em PMv2); fecha sozinha em 1,5 s se a sonda cair
COVER_CHILD = r"""
import ctypes, sys
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

x, y, w, h = (int(v) for v in sys.argv[1:5])
app = QApplication(sys.argv[:1])
box = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
              | Qt.WindowType.WindowDoesNotAcceptFocus)
box.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
pal = box.palette()
pal.setColor(QPalette.ColorRole.Window, QColor(255, 0, 255))
box.setPalette(pal)
box.setAutoFillBackground(True)
hwnd = ctypes.c_void_p(int(box.winId()))
move = ctypes.windll.user32.SetWindowPos
move(hwnd, ctypes.c_void_p(-1), x, y, w, h, 0x0010)
box.show()
move(hwnd, ctypes.c_void_p(-1), x, y, w, h, 0x0010 | 0x0040)
for _ in range(20):
    app.processEvents()
print("pronta", flush=True)
QTimer.singleShot(1500, app.quit)
app.exec()
"""


def _synthetic(w: int, h: int) -> Any:
    """Um "slide" de mentira: o fundo, uma faixa de título e três barras."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(245, 247, 250))
    painter = QPainter(img)
    painter.fillRect(0, 0, w, h // 6, QColor(31, 78, 140))
    for i, share in enumerate((0.8, 0.55, 0.35)):
        painter.fillRect(w // 10, h // 3 + i * h // 6, int(w * 0.8 * share), h // 10, QColor(64, 140, 90))
    painter.setPen(Qt.GlobalColor.white)
    painter.end()
    return img


def _jpeg(img: Any, quality: int = 80) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImageWriter

    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buf, QByteArray(b"jpeg"))
    writer.setQuality(quality)
    writer.write(img)
    return bytes(data.data())


def f521_shots(store: Any, shot: Callable[..., None], app: Any, platform: Any = None) -> None:
    from listener.llm.suggest_task import SuggestOption
    from listener.model import Evidence, ScreenSource, ScreenStatus, SuggestMode
    from listener.store.suggestions import ScreenInsert
    from listener.ui.caption_tray import CaptionTray
    from listener.ui.meeting import MeetingWindow
    from listener.ui.screen_view import ScreenView
    from listener.ui.suggest_card import SuggestCard
    from tests.test_suggest_store import suggestion

    mid = store.create_meeting("Comitê de investimentos (tela)", "meeting", None, None)
    _insert(store, mid, CHAT)
    tray = CaptionTray(store, mid, platform=platform)
    tray.set_live_state("live")
    tray.restore_geometry()
    tray.resize(tray.width(), 620)
    _offscreen(tray, app)
    tray.load_new()
    slide = _synthetic(1600, 900)
    thumb = slide.scaled(320, 180)
    tray.chat.add_screen(70_000, thumb, "Tela enviada · janela do Teams", True)
    card = SuggestCard()
    tray.set_card(card)
    tray.show_card()
    card.screen_row.set_state(True, True, "")
    card.screen_row.set_on(True)
    card.begin(SuggestMode.SPEAK, "14:05", 81_000)
    card.screen_row.show_line("com a tela · janela do Teams")
    card.add_option(SuggestOption(0, "Perguntar", "A margem de 12 % do slide já considera a exceção da tecnologia?", ((1, 12_000),),
                                  Evidence.OK, True))
    card.add_option(SuggestOption(1, "Posicionar", "Apoio o corte, com a exceção da tecnologia até o fim do trimestre.", (), Evidence.OK,
                                  True))
    app.processEvents()
    shot(tray, "f521-bandeja-com-a-tela")
    card.begin(SuggestMode.SPEAK, "14:07", 96_000)
    card.screen_row.ask("Gemini")
    app.processEvents()
    shot(tray, "f521-bandeja-confirmacao")
    tray.set_card(None)
    card.deleteLater()
    _discard(tray)
    view = ScreenView(slide, "Tela enviada", platform)
    shot(view, "f521-tela-maior")
    _discard(view)
    sid, _ = suggestion(store, mid, 70_000)
    store.suggestions.insert_screen(ScreenInsert(sid, ScreenStatus.SENT, ScreenSource.WINDOW, "janela do Teams", 1600, 900, 219_000, 0,
                                                 _jpeg(thumb, 75), _jpeg(slide.scaled(960, 540), 70)))
    win = MeetingWindow(store, mid)
    win.show_tab("sugestoes")
    shot(win, "f521-reuniao-com-a-tela")
    _discard(win)


def f521_timings(store: Any, app: Any, platform: Any, *, falas: int = 2000, novas: int = 200, telas: int = 40) -> dict[str, Any]:
    """Na thread da interface, com os eventos que cada passo posta: pôr cada miniatura pelo host (o `ScreenFlow.sent` do app,
    com a decodificação do JPEG, o item na lista pelo tempo e a linha do cartão) e cada legenda nova com `falas` legendas e
    `telas` miniaturas na lista. A bandeja e o host são os do app; as imagens, sintéticas."""
    from listener.llm.suggest_ctx import ScreenSent, ScreenShot
    from listener.llm.types import ImagePart
    from listener.model import ScreenSource, ScreenStatus
    from listener.store.captions import CaptionInsert
    from listener.ui.caption_tray import CaptionTray
    from listener.ui.suggest_host import SuggestHost
    from listener.ui.widget import RecordingWidget
    from probe_f520 import GOAL_MS, _noop, _stats

    mid = store.create_meeting("Comitê de investimentos (tempos da tela)", "meeting", None, None)
    for i in range(falas):
        r = i % 3 != 0
        store.captions.insert(CaptionInsert(mid, "R" if r else "L", i * 3000, i * 3000 + 2500,
                                            f"Fala {i}: o orçamento de 2027 e o corte de oito por cento em todas as áreas.", None,
                                            "Ana — CFO" if r else None, 0.8, "fake:small", 2500))
    widget = RecordingWidget(captions_button=True)
    tray = CaptionTray(store, mid, platform=platform)
    tray.set_live_state("live")
    tray.resize(tray.width(), 560)
    _offscreen(tray, app)
    tray.load_new()
    host = SuggestHost(store, platform, widget, tray, mid, active=True, live_state="live", elapsed=lambda: falas * 3.0,
                       on_notice=_noop)
    slide = _synthetic(1600, 900)
    image = ImagePart(_jpeg(slide), "image/jpeg", 1600, 900, "tela: janela do Teams")
    shot = ScreenShot(ScreenStatus.SENT, ScreenSource.WINDOW, "janela do Teams", image, _jpeg(slide.scaled(320, 180), 75), None, 0)
    app.processEvents()
    put_ms: list[float] = []
    caption_ms: list[float] = []
    try:
        for k in range(telas):
            start = time.perf_counter()
            host.screen.sent(ScreenSent((falas // telas) * k * 3000 + 1500, shot))
            app.processEvents()
            put_ms.append((time.perf_counter() - start) * 1000)
        for k in range(novas):
            t0 = (falas + k) * 3000
            text = f"Sigo com o item {k} do orçamento e o fluxo de caixa."
            cid = store.captions.insert(CaptionInsert(mid, "R", t0, t0 + 2500, text, None, "Ana — CFO", 0.8, "fake:small", 2500))
            msg = {"type": "caption", "id": cid, "channel": "R", "t_start_ms": t0, "t_end_ms": t0 + 2500, "text": text,
                   "label": "Ana — CFO"}
            start = time.perf_counter()
            tray.on_caption(msg)
            host.on_caption(msg)
            app.processEvents()
            caption_ms.append((time.perf_counter() - start) * 1000)
    finally:
        host.teardown()
        _discard(tray)
        _discard(widget)
        app.processEvents()
    put, legenda = _stats(put_ms), _stats(caption_ms)
    return {"falas": falas, "telas": telas, "com_imagem": tray.excluded is not False, "miniatura_ms": put, "legenda_ms": legenda,
            "meta_ms": GOAL_MS, "ok": legenda["max"] < GOAL_MS}


def _count_color(frame: Any, rgb: tuple[int, int, int]) -> int:
    import numpy as np

    px = np.frombuffer(frame.data, dtype=np.uint8).reshape(frame.height, frame.stride // 4, 4)[:, :frame.width]
    r, g, b = rgb
    return int(np.count_nonzero((np.abs(px[..., 2].astype(int) - r) < 8) & (np.abs(px[..., 1].astype(int) - g) < 8)
                                & (np.abs(px[..., 0].astype(int) - b) < 8)))


def _dpi_context() -> Any:
    """A-03: o contexto de DPI da thread (2 = por monitor; o PMv2 também dá 2) [exp]."""
    import ctypes

    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32")
        ctx = user32.GetThreadDpiAwarenessContext()
        return int(user32.GetAwarenessFromDpiAwarenessContext(ctypes.c_void_p(ctx)))
    except (AttributeError, OSError):
        return None


def _exclusion(app: Any, platform: Any) -> dict[str, Any]:
    """A-04: uma janela de cor única, sem foco, por cima de um retângulo do monitor principal: com a exclusão, zero pixels dela no
    print; sem, a cor aparece (o controle). Fica menos de 1 s à vista, no canto."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QWidget

    from listener.platform.screen_types import ScreenRect

    primary = next((m for m in platform.monitors() if m.primary), None)
    if primary is None:
        return {"ok": None, "motivo": "sem monitor"}
    box = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                  | Qt.WindowType.WindowDoesNotAcceptFocus)
    box.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    pal = box.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(*MAGENTA))
    box.setPalette(pal)
    box.setAutoFillBackground(True)
    box.resize(64, 64)
    box.move(primary.rect.x + 8, primary.rect.y + 8)
    out: dict[str, Any] = {}
    try:
        box.show()
        for _ in range(10):
            app.processEvents()
            time.sleep(0.03)
        dpr = box.devicePixelRatioF()
        rect = ScreenRect(primary.rect.x, primary.rect.y, int(96 * dpr), int(96 * dpr))
        before = platform.grab_screen(rect)
        out["sem_exclusao"] = _count_color(before, MAGENTA) if before is not None else None
        out["excluiu"] = bool(platform.exclude_from_capture(int(box.winId())))
        for _ in range(10):
            app.processEvents()
            time.sleep(0.03)
        after = platform.grab_screen(rect)
        out["com_exclusao"] = _count_color(after, MAGENTA) if after is not None else None
    finally:
        box.close()
        box.deleteLater()
        app.processEvents()
    out["ok"] = bool(out.get("sem_exclusao")) and out.get("com_exclusao") == 0
    return out


def _save(keep: Path | None, name: str, data: bytes | None) -> str | None:
    """A-40: só com `--guardar PASTA`, a imagem como iria ao provedor (a tela de verdade do dono, que fica na máquina dele)."""
    if keep is None or not data:
        return None
    keep.mkdir(parents=True, exist_ok=True)
    path = keep / name
    path.write_bytes(data)
    return str(path)


def _magenta_in_jpeg(data: bytes) -> int:
    """Os pixels quase magenta de um JPEG (o JPEG borra a cor: a margem é larga)."""
    import numpy as np
    from PySide6.QtGui import QImage

    img = QImage.fromData(data).convertToFormat(QImage.Format.Format_RGB32)
    px = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes()).reshape(img.height(), img.bytesPerLine() // 4, 4)
    px = px[:, :img.width()]
    return int(np.count_nonzero((px[..., 2] > 180) & (px[..., 1] < 90) & (px[..., 0] > 180)))


def _meeting(platform: Any) -> tuple[Any, Any] | None:
    """O primeiro app de reunião da lista de fábrica com a janela à vista (sem o navegador: sem o serviço reconhecido, a janela
    dele não é a da reunião)."""
    from listener.platform.base import KIND_MEETING

    for wa in platform.watched_apps():
        if wa.kind == KIND_MEETING:
            win = platform.meeting_window(None, wa.exe)
            if win is not None:
                return wa, win
    return None


def _monitors(platform: Any, keep: Path | None) -> list[dict[str, Any]]:
    from listener.model import ScreenSource
    from listener.ui.screen_grab import Target, prepare

    rows = []
    for i, mon in enumerate(platform.monitors(), 1):
        t0 = time.perf_counter()
        frame = platform.grab_screen(mon.rect)
        t1 = time.perf_counter()
        shot = prepare(frame, Target(mon.rect, ScreenSource.MONITOR, mon.label.split(" · ")[0]), keep=True)
        t2 = time.perf_counter()
        rows.append({"monitor": mon.label, "print_ms": round((t1 - t0) * 1000, 1), "preparo_ms": round((t2 - t1) * 1000, 1),
                     "estado": str(shot.status), "kB": len(shot.image.data) // 1024 if shot.image else 0,
                     "copia_kB": len(shot.copy or b"") // 1024,
                     "guardada": _save(keep, f"f521-monitor-{i}.jpg", shot.image.data if shot.image else None)})
    return rows


def _window(platform: Any, found: tuple[Any, Any] | None, keep: Path | None) -> dict[str, Any] | None:
    """A janela da reunião como o automático a manda: o recorte, as janelas por cima pintadas e a imagem de 1.600 px."""
    from listener.model import ScreenSource
    from listener.ui.screen_grab import Target, app_label, prepare

    if found is None:
        return None
    wa, win = found
    t0 = time.perf_counter()
    frame = platform.grab_screen(win.rect)
    t1 = time.perf_counter()
    shot = prepare(frame, Target(win.rect, ScreenSource.WINDOW, app_label(win.app, platform.watched_apps()), win.covered), keep=False)
    t2 = time.perf_counter()
    return {"app": wa.label, "tamanho": f"{win.rect.width}x{win.rect.height}", "por_cima": len(win.covered),
            "print_ms": round((t1 - t0) * 1000, 1), "preparo_ms": round((t2 - t1) * 1000, 1), "estado": str(shot.status),
            "kB": len(shot.image.data) // 1024 if shot.image else 0,
            "guardada": _save(keep, "f521-janela.jpg", shot.image.data if shot.image else None)}


def _click_to_thumb(app: Any, platform: Any, store: Any) -> dict[str, Any]:
    """Um pedido com a tela pelo host do app, com a resposta do provedor falso (nada sai da máquina): a thread da interface no
    clique, o tempo do clique à miniatura no chat (o alvo, o print e o preparo correm na tarefa, antes da chamada) e o maior
    buraco da thread da interface enquanto a tarefa roda (um timer de 1 ms: o GIL preso pelo preparo aparece aqui, R-06 da
    revisão do L2)."""
    from PySide6.QtCore import Qt, QTimer

    from listener.ui.caption_tray import CaptionTray
    from listener.ui.suggest_host import SuggestHost
    from listener.ui.widget import RecordingWidget

    before = os.environ.get("LISTENER_LLM")
    os.environ["LISTENER_LLM"] = "fake"
    store.providers.update_provider("gemini", api_key="sonda-sem-rede", fast_model="gemini-3.8-flash")
    store.set_setting("suggest.screen_consent", "gemini:2026-11-08")
    mid = store.create_meeting("Sonda da tela", "meeting", None, None)
    _insert(store, mid, CHAT)
    widget = RecordingWidget(captions_button=True)
    tray = CaptionTray(store, mid, platform=platform)
    _offscreen(tray, app)
    tray.load_new()
    host = SuggestHost(store, platform, widget, tray, mid, active=True, live_state="live", elapsed=lambda: 90.0, on_notice=lambda _t: None)
    added: list[float] = []
    real = tray.chat.add_screen

    def timed(t_ms: int, thumb: Any, text: str, sent: bool) -> None:
        added.append(time.perf_counter())
        real(t_ms, thumb, text, sent)

    tray.chat.add_screen = timed                     # type: ignore[method-assign]
    ticks: list[float] = []
    beat = QTimer()
    beat.setTimerType(Qt.TimerType.PreciseTimer)
    beat.setInterval(1)
    beat.timeout.connect(lambda: ticks.append(time.perf_counter()))
    beat.start()
    t0 = time.perf_counter()
    try:
        state = host.request(screen=True)
        ui_ms = (time.perf_counter() - t0) * 1000
        for _ in range(3000):                        # com o fake, o pedido termina em menos de 1 s
            app.processEvents()
            if host.task is None:
                break
            time.sleep(0.002)
    finally:
        beat.stop()
        host.teardown()
        _discard(tray)
        _discard(widget)
        app.processEvents()
        if before is None:
            os.environ.pop("LISTENER_LLM", None)
        else:
            os.environ["LISTENER_LLM"] = before
    thumb_ms = round((added[0] - t0) * 1000, 1) if added else None
    gap = round(max((b - a for a, b in itertools.pairwise(ticks)), default=0.0) * 1000, 1)
    return {"pedido": state, "interface_ms": round(ui_ms, 2), "miniatura_ms": thumb_ms, "buraco_ms": gap, "meta_interface_ms": UI_GOAL_MS,
            "meta_miniatura_ms": THUMB_GOAL_MS, "ok": ui_ms <= UI_GOAL_MS and thumb_ms is not None and thumb_ms <= THUMB_GOAL_MS}


def _child_ready(child: subprocess.Popen[str], timeout_s: float = 15.0) -> bool:
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: lines.put(child.stdout.readline() if child.stdout else ""), daemon=True).start()
    try:
        return lines.get(timeout=timeout_s).strip() == "pronta"
    except queue.Empty:
        return False


def _covering(platform: Any, found: tuple[Any, Any] | None) -> dict[str, Any]:
    """A-09: a janela de cor única de outro processo, sem foco, no meio da janela da reunião, por menos de 1 s: no print cru
    ela aparece (o controle); na imagem enviada, sai preta."""
    from listener.model import ScreenSource
    from listener.ui.screen_grab import Target, app_label, prepare

    if found is None:
        return {"ok": None, "motivo": "sem a janela de um app de reunião aberta"}
    wa, win = found
    r = win.rect
    w, h = max(96, r.width // 5), max(96, r.height // 5)
    x, y = r.x + (r.width - w) // 2, r.y + (r.height - h) // 2
    out: dict[str, Any] = {"app": wa.label}
    child = subprocess.Popen([sys.executable, "-c", COVER_CHILD, str(x), str(y), str(w), str(h)],  # noqa: S603
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        if not _child_ready(child):
            return {**out, "ok": None, "motivo": "a janela de cor única não abriu"}
        time.sleep(0.1)                              # a composição do DWM
        win = platform.meeting_window(None, wa.exe)
        if win is None:
            return {**out, "ok": None, "motivo": "a janela da reunião sumiu"}
        frame = platform.grab_screen(win.rect)
        target = Target(win.rect, ScreenSource.WINDOW, app_label(win.app, platform.watched_apps()), win.covered)
        shot = prepare(frame, target, keep=False)
        out["por_cima"] = len(win.covered)
        out["no_print_cru"] = _count_color(frame, MAGENTA) if frame is not None else None
        out["na_enviada"] = _magenta_in_jpeg(shot.image.data) if shot.image is not None else None
    finally:
        child.kill()
        child.wait(5)
    out["ok"] = bool(out.get("por_cima")) and bool(out.get("no_print_cru")) and out.get("na_enviada") == 0
    return out


def f521_screen(app: Any, platform: Any, store: Any, keep: Path | None = None) -> dict[str, Any]:
    found = _meeting(platform)
    return {"monitores": _monitors(platform, keep), "janela": _window(platform, found, keep), "meta_tarefa_ms": 150.0,
            "pedido": _click_to_thumb(app, platform, store), "exclusao": _exclusion(app, platform),
            "por_cima": _covering(platform, found), "dpi_thread": _dpi_context()}


__all__ = ["f521_screen", "f521_shots", "f521_timings"]
