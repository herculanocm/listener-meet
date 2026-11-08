"""As telas e as medidas da F5.21 no `win_probe` (não fazem parte do produto).

`f521_shots(store, shot, app, platform)`, no `win_probe shots`: a bandeja com o cartão com a chave "Com a tela" ligada, a linha
"com a tela · janela do Teams", uma opção com "[tela]" e a miniatura no chat; a confirmação da primeira vez; a janela maior; a
aba "Sugestões" com as miniaturas. As imagens são sintéticas: nenhuma tela de verdade vai para as fotos.

`f521_screen(app, platform)`, no `win_probe tela` (o Aceite, só a pedido do dono, porque olha a tela de verdade e mostra por
menos de 1 s um quadrado colorido no canto do monitor principal, sem roubar o foco): o print e o preparo de cada monitor (os
ms e os kB, A-02); a thread da interface num pedido com a tela (até 5 ms); a janela de cor única excluída da captura por cima
do retângulo, com zero pixels dela no print (A-04), e a mesma janela sem a exclusão como controle; o contexto de DPI da
thread (A-03). Nada é gravado em disco: os prints ficam na memória e saem."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from probe_f517 import CHAT, _discard, _insert, _offscreen

MAGENTA = (255, 0, 255)


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


def _count_color(frame: Any, rgb: tuple[int, int, int]) -> int:
    import numpy as np

    px = np.frombuffer(frame.data, dtype=np.uint8).reshape(frame.height, frame.stride // 4, 4)[:, :frame.width]
    r, g, b = rgb
    return int(np.count_nonzero((np.abs(px[..., 2].astype(int) - r) < 8) & (np.abs(px[..., 1].astype(int) - g) < 8)
                                & (np.abs(px[..., 0].astype(int) - b) < 8)))


def _dpi_context() -> Any:
    """A-03: o contexto de DPI da thread (2 = por monitor; o PMv2 também dá 2) [exp]."""
    import ctypes

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


def f521_screen(app: Any, platform: Any, store: Any) -> dict[str, Any]:
    from listener.llm.suggest_task import ScreenSent
    from listener.model import ScreenSource
    from listener.ui.caption_tray import CaptionTray
    from listener.ui.screen_grab import Target, prepare
    from listener.ui.suggest_host import SuggestHost
    from listener.ui.widget import RecordingWidget

    monitors = []
    for mon in platform.monitors():
        t0 = time.perf_counter()
        frame = platform.grab_screen(mon.rect)
        t1 = time.perf_counter()
        shot = prepare(frame, Target(mon.rect, ScreenSource.MONITOR, mon.label.split(" · ")[0]), keep=True)
        t2 = time.perf_counter()
        monitors.append({"monitor": mon.label, "print_ms": round((t1 - t0) * 1000, 1), "preparo_ms": round((t2 - t1) * 1000, 1),
                         "estado": str(shot.status), "kB": len(shot.image.data) // 1024 if shot.image else 0,
                         "copia_kB": len(shot.copy or b"") // 1024})
    mid = store.create_meeting("Sonda da tela", "meeting", None, None)
    widget = RecordingWidget(captions_button=True)
    tray = CaptionTray(store, mid, platform=platform)
    _offscreen(tray, app)
    store.set_setting("suggest.screen_consent", "gemini:2026-11-08;openai:2026-11-08;anthropic:2026-11-08")
    host = SuggestHost(store, platform, widget, tray, mid, active=True, live_state="live", elapsed=lambda: 60.0, on_notice=lambda _t: None)
    seen: list[ScreenSent] = []
    host.screen.enabled = True                       # a sonda mede a interface mesmo sem a chave do provedor
    host.keyed = True
    host.screen.sent = seen.append                   # type: ignore[method-assign]
    t0 = time.perf_counter()
    host.request(screen=True)
    ui_ms = (time.perf_counter() - t0) * 1000
    for _ in range(300):                             # o pedido sem provedor termina cedo; espera a tarefa sair
        app.processEvents()
        if host.task is None:
            break
        time.sleep(0.02)
    host.teardown()
    _discard(tray)
    _discard(widget)
    app.processEvents()
    return {"monitores": monitors, "interface_ms": round(ui_ms, 2), "meta_interface_ms": 5.0, "meta_tarefa_ms": 150.0,
            "exclusao": _exclusion(app, platform), "dpi_thread": _dpi_context()}


__all__ = ["f521_screen", "f521_shots"]
