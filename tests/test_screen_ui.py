"""F5.21 L2: a tela nas sugestões ao vivo do lado da interface (pytest-qt, offscreen, a plataforma falsa). O alvo e o preparo
(a janela da reunião recortada, o monitor fixo, o que sumiu, o monitor em uso; as janelas por cima pretas; a enviada, a cópia e
a miniatura; a vazia; o print que falha); a miniatura no chat no tempo do pedido (o item sem cabeçalho, a fala seguinte com
cabeçalho, a rolagem, o clique); o host com a chave (o custo com a tela, a miniatura no chat, a linha do cartão, o "[tela]",
a tela gravada, a janela maior fora da captura); a confirmação da primeira vez ("Agora não" e "Mandar"); o atalho mudo sem o
recurso; o modelo sem imagem; "Tentar sem a tela"; o menu da bandeja do sistema, o atalho e a escolha nas Configurações."""
from __future__ import annotations

from typing import Any

import pytest

from listener.llm.suggest_task import Outcome, SuggestResult
from listener.model import AppState, ScreenSource, ScreenStatus
from listener.platform.screen_types import Frame, MeetingWindow, ScreenRect

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

MODEL = "gemini-3.8-flash"                       # o rápido padrão do Gemini recebe imagem pela tabela (o fake-fast não)


def _screen_host(qtbot: Any, store: Any, platform: Any, monkeypatch: Any, *, consent: bool = True) -> tuple[Any, list[str]]:
    from tests.test_suggest_host import _bare

    host, notices = _bare(qtbot, store, platform, monkeypatch)
    store.providers.update_provider("gemini", fast_model=MODEL)
    store.providers.set_price("gemini", MODEL, 0.30, 2.50, 0.075)
    if consent:
        store.set_setting("suggest.screen_consent", "gemini:2026-11-08")
    host.reload()
    return host, notices


def _screen_items(chat: Any) -> list[Any]:
    from listener.ui.chat_delegate import SCREEN_ROLE

    return [chat.list.item(i) for i in range(chat.list.count()) if chat.list.item(i).data(SCREEN_ROLE) is not None]


# ---------------------------------------------------------------- o alvo e o preparo
def test_the_target_and_the_preparation(qtbot, platform):
    from PySide6.QtGui import QColor, QImage

    from listener.ui.screen_grab import NO_MONITOR, capture, parse_source, pick_target, prepare

    watched = platform.watched_apps()
    assert parse_source("monitor:") == ("auto", "") and parse_source("cursor") == ("cursor", "")
    assert parse_source("monitor:DISPLAY2") == ("monitor", "DISPLAY2") and parse_source(None) == ("auto", "")
    platform.meeting_win = MeetingWindow(ScreenRect(1920, 0, 1600, 900), platform.screen_monitors[1], (ScreenRect(2000, 100, 300, 200),),
                                         "ms-teams.exe")
    window = pick_target(platform, "auto", 4242, "ms-teams.exe", watched)
    assert window is not None and window.source == ScreenSource.WINDOW and window.app == "janela do Teams" and window.covered
    assert pick_target(platform, "cursor", 4242, "ms-teams.exe", watched).source == ScreenSource.CURSOR    # pula a janela (R-08)
    fixed = pick_target(platform, "monitor:DISPLAY2", None, "", watched)
    assert (fixed.source, fixed.app, fixed.rect) == (ScreenSource.MONITOR, "Monitor 2", platform.screen_monitors[1].rect)
    gone = pick_target(platform, "monitor:DISPLAY9", 4242, "ms-teams.exe", watched)
    assert gone.source == ScreenSource.WINDOW and gone.note == NO_MONITOR                  # o fixo que sumiu: o automático
    shot = prepare(platform.grab_screen(window.rect), window, keep=True)
    assert shot.status == ScreenStatus.SENT and shot.image is not None and shot.copy is not None and shot.covered == 1
    sent = QImage.fromData(shot.image.data)
    assert (sent.width(), shot.image.width, QImage.fromData(shot.copy).width()) == (1600, 1600, 960)
    assert QImage.fromData(shot.thumb).width() <= 320 and QImage.fromData(shot.thumb).height() <= 180
    assert QColor(sent.pixel(230, 200)).value() < 30 and QColor(sent.pixel(1000, 600)).value() > 60    # a janela por cima, preta
    assert prepare(platform.grab_screen(window.rect), window, keep=False).copy is None
    dark = bytearray(platform.grab_screen(window.rect).data)                     # o protegido preto no meio da moldura (90 %)
    for y in range(23, 877):
        dark[(y * 1600 + 40) * 4:(y * 1600 + 1560) * 4] = bytes(1520 * 4)
    assert prepare(Frame(bytes(dark), 1600, 900, 6400), window, keep=True).status == ScreenStatus.EMPTY
    platform.grab_color = (250, 250, 250)
    assert prepare(platform.grab_screen(fixed.rect), fixed, keep=True).image is None                   # uma cor só
    platform.grab_fail = True
    assert capture(platform, "auto", 4242, "ms-teams.exe", watched, True).status == ScreenStatus.FAILED


# ---------------------------------------------------------------- o chat
def test_the_thumbnail_enters_the_chat_at_its_time_without_a_header(qtbot, store, platform):
    from PySide6.QtGui import QImage

    from listener.ui.chat_delegate import HEAD_ROLE, SCREEN_ROLE
    from tests.test_live_partial import _caption
    from tests.test_suggest_ui import _tray

    tray = _tray(qtbot, store, platform)
    chat = tray.chat
    mid = chat.meeting_id
    _caption(store, mid, 10_000, "Bom dia.", label="Ana — CFO")
    _caption(store, mid, 30_000, "Vamos ao orçamento.", label="Ana — CFO")
    tray.load_new()
    thumb = QImage(320, 180, QImage.Format.Format_RGB32)
    thumb.fill(0x336699)
    chat.add_screen(20_000, thumb, "Tela enviada · janela do Teams", True)
    order = [chat.list.item(i).data(SCREEN_ROLE) for i in range(chat.list.count())]
    assert order == [None, 20_000, None]                                          # no tempo do pedido, entre as falas
    after = chat.list.item(2)
    assert after.data(HEAD_ROLE) and chat.list.item(1).text() == "Tela enviada · janela do Teams"   # a fala volta com cabeçalho
    assert chat.list.item(1).sizeHint().height() == chat.delegate.screen_size(chat.list.item_width()).height()
    clicked: list[int] = []
    chat.screen_clicked.connect(clicked.append)
    chat.list.itemClicked.emit(chat.list.item(1))
    chat.list.itemClicked.emit(chat.list.item(0))                                 # uma fala: nada
    assert clicked == [20_000] and chat.scroll_to_screen(20_000) and not chat.scroll_to_screen(99_000)
    assert not chat.list.viewport().grab().isNull()                               # o delegate pinta o anexo


# ---------------------------------------------------------------- o host com a tela
def test_with_the_screen_the_thumbnail_goes_to_the_chat_the_option_cites_it_and_the_view_opens(qtbot, store, platform, monkeypatch):
    from PySide6.QtGui import QImage

    from listener.ui.chat_delegate import SCREEN_ROLE, THUMB_ROLE

    host, _notices = _screen_host(qtbot, store, platform, monkeypatch)
    row = host.card.screen_row
    assert not row.toggle.isHidden() and row.toggle.isEnabled() and not host.screen.on and "com a tela" not in host.cost_tip
    row.toggle.click()
    assert host.screen.on and "por pedido com a tela" in host.cost_tip
    host.request()
    qtbot.waitUntil(lambda: host.task is None, timeout=5000)
    shots = _screen_items(host.tray.chat)
    assert len(shots) == 1 and shots[0].text() == "Tela enviada · Monitor 1" and isinstance(shots[0].data(THUMB_ROLE), QImage)
    assert row.line.text().endswith("com a tela · Monitor 1</a>") and "[tela]" in host.card.rows[0].facts.text()
    sid = store.suggestions.list_for_meeting(host.meeting_id)[-1]["id"]
    assert store.suggestions.screens_for_meeting(host.meeting_id)[sid]["source"] == "primeiro_plano"
    host.tray.chat.screen_clicked.emit(shots[0].data(SCREEN_ROLE))
    assert host.screen.view is None and host.screen.view_task is not None         # R-07: decodificada numa `Task`
    qtbot.waitUntil(lambda: host.screen.view is not None, timeout=3000)
    view: Any = host.screen.view                                                  # o `waitUntil` muda o que o mypy estreitou
    assert view.excluded and not view.picture.pixmap().isNull() and int(view.winId()) in platform.excluded
    view.close()
    platform.capture_exclusion = False                                            # a exclusão falhou: sem a imagem
    host.tray.chat.screen_clicked.emit(shots[0].data(SCREEN_ROLE))
    qtbot.waitUntil(lambda: host.screen.view is not view, timeout=3000)
    hidden: Any = host.screen.view
    assert hidden.picture.pixmap().isNull() and "escondida" in hidden.picture.text()
    hidden.close()


def test_the_first_time_with_a_provider_the_card_asks_and_the_request_waits(qtbot, store, platform, monkeypatch):
    host, _notices = _screen_host(qtbot, store, platform, monkeypatch, consent=False)
    row = host.card.screen_row
    row.toggle.click()
    assert host.request() == "confirme a tela" and host.task is None and not row.consent.isHidden()
    assert "Gemini" in row.consent_text.text()
    row.btn_skip.click()                                                          # "Agora não": sem a tela, e a chave desliga
    assert not host.screen.on and host.task is not None
    qtbot.waitUntil(lambda: host.task is None, timeout=5000)
    assert store.suggestions.screens_for_meeting(host.meeting_id) == {} and store.get_setting("suggest.screen_consent") == ""
    row.toggle.click()
    host.request()
    row.btn_send.click()
    qtbot.waitUntil(lambda: host.task is None, timeout=5000)
    assert store.get_setting("suggest.screen_consent").startswith("gemini:")
    assert len(store.suggestions.screens_for_meeting(host.meeting_id)) == 1
    host.request()                                                                # confirmado: não pergunta de novo
    assert host.task is not None and row.consent.isHidden()
    qtbot.waitUntil(lambda: host.task is None, timeout=5000)


def test_the_screen_shortcut_is_mute_without_it_and_a_failed_request_offers_one_without_the_screen(qtbot, store, platform, monkeypatch):
    from listener.ui.suggest_screen import NO_IMAGE_MODEL

    host, notices = _screen_host(qtbot, store, platform, monkeypatch)
    store.set_setting("suggest.screen", False)
    host.reload()
    host.request_from_outside(screen=True)
    assert notices == [] and host.task is None and host.card.screen_row.toggle.isHidden()
    store.set_setting("suggest.screen", True)
    store.providers.update_provider("gemini", fast_model="fake-fast")
    host.reload()
    assert not host.card.screen_row.toggle.isEnabled() and host.card.screen_row.toggle.toolTip() == NO_IMAGE_MODEL
    store.providers.update_provider("gemini", fast_model=MODEL)
    host.reload()
    host.card.begin(host.mode, "14:05", None)
    host._done(SuggestResult(Outcome.ERROR, host.meeting_id, error="o modelo recusou a imagem", with_screen=True))
    assert not host.card.btn_plain.isHidden() and not host.card.btn_retry.isHidden()
    host.card.btn_plain.click()                                                   # A-18: um clique, sem a tela
    assert host.task is not None
    qtbot.waitUntil(lambda: host.task is None, timeout=5000)
    assert store.suggestions.screens_for_meeting(host.meeting_id) == {}
    host.card.set_finished()
    assert not host.card.screen_row.toggle.isEnabled()


# ---------------------------------------------------------------- o menu, o atalho e as Configurações
def test_the_tray_menu_the_shortcut_and_the_settings_choice(qtbot, qapp, store, platform, monkeypatch):
    from listener.ui import controller as ctl
    from listener.ui.tray import TrayIcon, menu_shortcuts
    from tests.test_settings_pages import _dialog

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    tray = TrayIcon(c)
    names = [a.text() for a in tray.menu.actions()]
    assert names.index("Sugerir com a tela…") == names.index("Sugerir…") + 1
    assert menu_shortcuts(store.get_setting, {})["suggest_screen"] == "ctrl+alt+shift+t"
    tray.menu.aboutToShow.emit()
    assert tray.act_suggest_screen.isVisible()
    store.set_setting("suggest.screen", False)
    tray.menu.aboutToShow.emit()
    assert not tray.act_suggest_screen.isVisible()
    asked: list[bool] = []
    c.captions = type("Captions", (), {"request_suggestion": lambda self, screen=False: asked.append(screen), "suggest": None})()
    try:
        tray.set_state(AppState.RECORDING)
        tray.act_suggest_screen.trigger()
        c._on_hotkey("suggest_screen")
        c._on_hotkey("suggest")
        assert asked == [True, True, False]
    finally:
        c.captions = None
        tray.hide()
        tray.deleteLater()
    dlg = _dialog(qtbot, store, platform)
    editor = dlg._editors["suggest.screen_source"]
    values = [editor.combo.itemData(i) for i in range(editor.combo.count())]
    assert values == ["auto", "cursor", "monitor:DISPLAY1", "monitor:DISPLAY2"] and editor.raw() == "auto"


def test_the_screen_modules_never_log_the_image_the_summary_or_the_title():
    """A-38 (D-053): nenhum módulo da tela passa ao log a imagem, a miniatura, o resumo, o título da janela ou o serviço."""
    from tests.log_privacy import F510_ALLOWED_CALLS, F521_FORBIDDEN, F521_MODULES, FORBIDDEN, leaks

    assert {m.name: leaks(m, F521_FORBIDDEN, F510_ALLOWED_CALLS) for m in F521_MODULES} == {m.name: [] for m in F521_MODULES}
    assert leaks(F521_MODULES[-1], FORBIDDEN) == []                       # `win32/screen.py`: nem título nem nome


# ---------------------------------------------------------------- L3: a aba da reunião
def test_the_meeting_tab_shows_the_thumbnail_marks_the_option_and_opens_the_copy(qtbot, store):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage, QImageWriter

    from listener.store.suggestions import ScreenInsert
    from listener.ui.meeting_suggestions import SuggestionsTab
    from tests.test_suggest_store import suggestion

    def jpeg(w: int, h: int) -> bytes:
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(0x336699)
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        QImageWriter(buf, QByteArray(b"jpeg")).write(img)
        return bytes(data.data())

    mid = store.create_meeting("Orçamento", None, None, None)
    sent, (a, _b) = suggestion(store, mid, 65_000)
    dark, _ = suggestion(store, mid, 90_000)
    store._x("UPDATE suggestion_options SET screen = 1 WHERE id = ?", (a,))
    store.suggestions.insert_screen(ScreenInsert(sent, ScreenStatus.SENT, ScreenSource.WINDOW, "janela do Teams", 1600, 900, 219_000, 0,
                                                 jpeg(320, 180), jpeg(960, 540)))
    store.suggestions.insert_screen(ScreenInsert(dark, ScreenStatus.EMPTY, ScreenSource.CURSOR, "Monitor 1", 0, 0, 0, 0, jpeg(320, 180),
                                                 None))
    tab = SuggestionsTab(store, mid)
    qtbot.addWidget(tab)
    first, second = tab.tree.topLevelItem(0), tab.tree.topLevelItem(1)
    assert "· com a tela ·" in first.text(0) and "· tela preta ·" in second.text(0) and not first.icon(0).isNull()
    assert first.child(0).text(0).endswith(" [tela]") and not first.child(1).text(0).endswith("[tela]")
    tab.tree.itemActivated.emit(first, 0)
    qtbot.waitUntil(lambda: tab.viewer is not None, timeout=3000)
    assert tab.viewer.findChild(QLabel_type()).pixmap().width() > 320
    tab.viewer.close()


def QLabel_type() -> Any:                                                         # noqa: N802 (o tipo do Qt, importado tarde)
    from PySide6.QtWidgets import QLabel

    return QLabel


# ---------------------------------------------------------------- a revisão do diff do L2 (R-01 a R-10)
def _bgra(w: int, h: int, gray: int) -> Any:
    import numpy as np

    px = np.zeros((h, w, 4), dtype=np.uint8)
    px[..., :3] = gray
    px[..., 3] = 255
    return px


def _text(px: Any, x: int, y: int, lines: list[int], size: int, color: int = 255) -> None:
    """Linhas de "texto": dois traços finos por letra, da altura da fonte (o slide, o terminal)."""
    for k, letters in enumerate(lines):
        top = y + int(k * size * 1.5)
        for c in range(letters):
            for dx in (0, int(size * 0.3)):
                x0 = x + c * int(size * 0.6) + dx
                px[top:top + size, x0:x0 + max(1, size // 8), :3] = color


def _frame(px: Any) -> Frame:
    h, w = px.shape[:2]
    return Frame(px.tobytes(), w, h, w * 4)


def test_the_dark_slide_goes_and_the_protected_or_covered_screen_does_not(platform):
    """R-04 (A-37): o slide preto com texto, o de seção, o terminal, o editor escuro e a planilha clara vão; o protegido
    preto no meio da moldura do Teams (82 %), a tela quase preta e a de uma cor só não vão; a reunião coberta em 80 % ou mais
    por outras janelas não vai, com a nota própria, e a coberta pela metade vai."""
    import numpy as np

    from listener.ui.screen_grab import COVERED_NOTE, Target, prepare

    whole = Target(ScreenRect(0, 0, 1280, 720), ScreenSource.MONITOR, "Monitor 1")
    slide = _bgra(1280, 720, 0)
    _text(slide, 80, 60, [30], 40)
    _text(slide, 100, 200, [25, 22, 18, 20], 26)
    section = _bgra(1280, 720, 0)
    _text(section, 380, 320, [15], 60)
    terminal = _bgra(1280, 720, 0)
    _text(terminal, 8, 8, [40] * 12, 12, 204)
    editor = _bgra(1280, 720, 30)
    _text(editor, 40, 20, [32] * 20, 14, 212)
    sheet = _bgra(1280, 720, 255)
    sheet[::22, :, :3] = 210
    sheet[:, ::100, :3] = 210
    protected = _bgra(1280, 720, 31)
    protected[60:680, 40:1240, :3] = 3                                            # o preto do compositor, quase zero
    near_black = _bgra(1280, 720, 0)                                              # um degradê fraco: nada acima de 15
    near_black[..., :3] = (6 + np.arange(1280) * 10 // 1280).astype(np.uint8)[None, :, None]
    for px in (slide, section, terminal, editor, sheet):
        assert prepare(_frame(px), whole, keep=False).status == ScreenStatus.SENT
    for px in (protected, near_black, _bgra(1280, 720, 128)):
        shot = prepare(_frame(px), whole, keep=False)
        assert shot.status == ScreenStatus.EMPTY and shot.image is None and shot.thumb and shot.note == ""
    rect = ScreenRect(0, 0, 1280, 720)
    covered = prepare(_frame(sheet), Target(rect, ScreenSource.WINDOW, "janela do Teams", (ScreenRect(0, 0, 1180, 680),)), keep=False)
    assert covered.status == ScreenStatus.EMPTY and covered.note == COVERED_NOTE and covered.covered == 1
    half = prepare(_frame(sheet), Target(rect, ScreenSource.WINDOW, "janela do Teams", (ScreenRect(0, 0, 640, 720),)), keep=False)
    assert half.status == ScreenStatus.SENT and half.note == ""


def test_the_covered_window_is_painted_black_at_any_offset_in_the_same_buffer_and_in_the_thumbnail(platform):
    """R-10 (o alvo com y diferente de zero; a miniatura, que fica no banco e no chat, também preta) e R-06 (o preparo pinta
    no próprio buffer gravável do print, sem outra cópia)."""
    import numpy as np
    from PySide6.QtGui import QColor, QImage

    from listener.ui.screen_grab import Target, prepare

    rect = ScreenRect(100, 200, 1280, 720)
    target = Target(rect, ScreenSource.WINDOW, "janela do Teams", (ScreenRect(300, 400, 400, 200),))
    raw = np.frombuffer(bytearray(platform.grab_screen(rect).data), dtype=np.uint8)
    shot = prepare(Frame(memoryview(raw), 1280, 720, 1280 * 4), target, keep=False)
    px = raw.reshape(720, 1280, 4)
    assert int(px[300, 400, :3].max()) == 0 and int(px[100, 100, :3].max()) > 60    # em (200, 200) do alvo, no buffer dado
    sent, thumb = QImage.fromData(shot.image.data), QImage.fromData(shot.thumb)
    assert QColor(sent.pixel(400, 300)).value() < 30 and QColor(sent.pixel(100, 100)).value() > 60
    assert QColor(thumb.pixel(100, 75)).value() < 30 and QColor(thumb.pixel(25, 25)).value() > 60


def test_the_system_source_looks_for_the_meeting_apps_and_a_failure_keeps_the_target(qtbot, store, platform, monkeypatch):
    """R-03: a fonte "sistema inteiro" procura o `fallback_exe` e depois os apps de reunião da lista; R-08: a falha guarda a
    fonte e o rótulo do alvo; sem monitor, a da escolha."""
    from types import SimpleNamespace

    from listener.capture.recorder import SourceSpec
    from listener.ui.screen_grab import capture

    host, _n = _screen_host(qtbot, store, platform, monkeypatch)
    platform.meeting_win = MeetingWindow(ScreenRect(1920, 0, 1600, 900), platform.screen_monitors[1], (), "ms-teams.exe")
    host.recording = SimpleNamespace(source=SourceSpec("system", fallback_exe="Zoom.exe"), elapsed=lambda: 60.0)
    shot = host.screen.capturer()()
    assert platform.meeting_asks[-1] == (None, "", ("zoom.exe", "ms-teams.exe", "slack.exe"))      # sem os navegadores
    assert (shot.status, shot.source, shot.app) == (ScreenStatus.SENT, ScreenSource.WINDOW, "janela do Teams")
    watched = platform.watched_apps()
    platform.grab_fail = True
    failed = capture(platform, "auto", None, "", watched, True)
    assert (failed.status, failed.source, failed.app) == (ScreenStatus.FAILED, ScreenSource.WINDOW, "janela do Teams")
    platform.grab_fail = False
    def broken(_rect: ScreenRect) -> Frame:
        raise RuntimeError("o GDI falhou")

    monkeypatch.setattr(platform, "grab_screen", broken)
    boom = capture(platform, "auto", None, "", watched, True)
    assert (boom.status, boom.source, boom.app) == (ScreenStatus.FAILED, ScreenSource.WINDOW, "janela do Teams")
    platform.meeting_win, platform.screen_monitors = None, ()
    assert capture(platform, "cursor", None, "", watched, True).source == ScreenSource.CURSOR
    assert capture(platform, "auto", None, "", watched, True).source == ScreenSource.FOREGROUND


def test_the_card_line_carries_the_note_and_the_chat_reads_the_time(qtbot, store, platform, monkeypatch):
    """R-08: o monitor fixo que sumiu (A-30) e a reunião coberta dizem por que no cartão; o leitor de tela da miniatura diz
    "Tela enviada [mm:ss] · rótulo"."""
    from PySide6.QtCore import Qt

    from listener.ui.screen_grab import COVERED_NOTE, NO_MONITOR

    host, _n = _screen_host(qtbot, store, platform, monkeypatch)
    row = host.card.screen_row
    store.set_setting("suggest.screen_source", "monitor:DISPLAY9")
    platform.meeting_win = MeetingWindow(ScreenRect(1920, 0, 1600, 900), platform.screen_monitors[1], (), "ms-teams.exe")
    host.reload()
    row.toggle.click()
    host.request()
    qtbot.waitUntil(lambda: host.task is None, timeout=5000)
    assert "com a tela · janela do Teams" in row.line.text() and NO_MONITOR in row.line.text()
    item = _screen_items(host.tray.chat)[-1]
    assert item.data(Qt.ItemDataRole.AccessibleTextRole) == "Tela enviada [01:00] · janela do Teams"
    store.set_setting("suggest.screen_source", "auto")
    platform.meeting_win = MeetingWindow(ScreenRect(1920, 0, 1600, 900), platform.screen_monitors[1],
                                         (ScreenRect(1920, 0, 1600, 850),), "ms-teams.exe")
    host.reload()
    host.request()
    qtbot.waitUntil(lambda: host.task is None, timeout=5000)
    assert COVERED_NOTE in row.line.text() and _screen_items(host.tray.chat)[-1].text() == "Tela coberta · não enviada"


def test_the_question_follows_the_card_and_never_outlives_a_new_request_or_the_stop(qtbot, store, platform, monkeypatch):
    """R-05: o cartão recolhido abre para a pergunta; um pedido sem a tela e o Parar a tiram da vista; o fechar das
    Configurações não reacende a chave no cartão encerrado."""
    host, _n = _screen_host(qtbot, store, platform, monkeypatch, consent=False)
    row = host.card.screen_row
    host.card.begin(host.mode, "14:00", None)
    host.card.set_collapsed(True)
    host.request_from_outside(screen=True)
    assert host.screen.pending is not None and not host.card.collapsed and row.consent.isVisibleTo(host.tray)
    assert host.request(screen=False) == "pedindo…"                               # o "Sugerir" sem a tela, no meio
    assert host.screen.pending is None and row.consent.isHidden()
    qtbot.waitUntil(lambda: host.task is None, timeout=5000)
    assert host.request(screen=True) == "confirme a tela"
    host.on_stopped()
    assert host.screen.pending is None and row.consent.isHidden()
    host.reload()
    assert not row.toggle.isEnabled() and not row.pick.isEnabled()
    assert store.get_setting("suggest.screen_consent") == ""


def test_the_tip_has_the_shortcut_of_the_settings_and_off_the_screen_shortcut_is_mute_outside(qtbot, store, platform, monkeypatch):
    """R-09: a dica da chave leva o atalho configurado; fora da gravação, o atalho da tela fica mudo com `suggest.screen`
    desligada, e o "Sugerir" de sempre avisa."""
    from listener.ui.caption_tray_host import NO_TRAY, suggest_outside
    from listener.ui.suggest_screen_row import SHORTCUT_TIP, TOGGLE_TIP
    from listener.ui.widget_parts import shortcut_text

    store.set_setting("hotkeys.suggest_screen", "ctrl+alt+shift+y")
    host, _n = _screen_host(qtbot, store, platform, monkeypatch)
    assert host.card.screen_row.toggle.toolTip() == TOGGLE_TIP + SHORTCUT_TIP.format(shortcut=shortcut_text("ctrl+alt+shift+y"))
    said: list[str] = []
    store.set_setting("suggest.screen", False)
    suggest_outside(store, said.append, screen=True)
    suggest_outside(store, said.append)
    assert said == [NO_TRAY]


def test_the_view_and_the_chat_follow_the_capture_exclusion_and_the_teardown(qtbot, store, platform, monkeypatch):
    """R-10: a miniatura no chat sem a imagem quando a bandeja não saiu da captura (pergunta 15); o `teardown` fecha a janela
    maior; a janela nativa refeita pede a exclusão de novo, e a falha esconde a imagem; fora do Windows, a chave apagada."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QImage

    from listener.llm.suggest_task import ScreenSent, ScreenShot
    from listener.llm.types import ImagePart
    from listener.ui.chat_delegate import THUMB_ROLE
    from listener.ui.screen_view import HIDDEN, ScreenView
    from listener.ui.suggest_screen import ONLY_WINDOWS

    host, _n = _screen_host(qtbot, store, platform, monkeypatch)
    thumb = QImage(320, 180, QImage.Format.Format_RGB32)
    thumb.fill(0x336699)
    host.tray.excluded = False
    host.screen.sent(ScreenSent(70_000, ScreenShot(ScreenStatus.SENT, ScreenSource.WINDOW, "janela do Teams", None, b"x", None, 0)))
    assert _screen_items(host.tray.chat)[-1].data(THUMB_ROLE) is None
    image = ImagePart(b"jpeg", "image/jpeg", 16, 9, "tela: janela do Teams")
    for k in range(12):                                                           # as 10 últimas na memória, para a janela maior
        host.screen.sent(ScreenSent(80_000 + k, ScreenShot(ScreenStatus.SENT, ScreenSource.WINDOW, "janela do Teams", image, b"x")))
    assert sorted(host.screen.images) == [80_000 + k for k in range(2, 12)]
    host.card.screen_row.show_line("com a tela · <b>x</b>")
    assert "&lt;b&gt;" in host.card.screen_row.line.text()                       # o rótulo vai como texto, nunca como HTML
    view = ScreenView(thumb, "Tela enviada", platform)
    host.screen.view = view
    view.show()
    host.screen.teardown()
    assert not view.isVisible()
    again = ScreenView(thumb, "Tela enviada", platform)
    qtbot.addWidget(again)
    asked = len(platform.excluded)
    platform.capture_exclusion = False
    QCoreApplication.sendEvent(again, QEvent(QEvent.Type.WinIdChange))
    qtbot.waitUntil(lambda: again.picture.text() == HIDDEN, timeout=2000)
    assert len(platform.excluded) == asked + 1 and again.picture.pixmap().isNull()
    platform.screen_monitors = ()
    host.reload()
    assert not host.card.screen_row.toggle.isEnabled() and host.card.screen_row.toggle.toolTip() == ONLY_WINDOWS


def test_the_label_the_retry_and_the_thumbnail_in_an_empty_or_long_chat(qtbot, store, platform, monkeypatch):
    """R-10: o rótulo do serviço no navegador; "Tentar sem a tela" só depois de um pedido com a tela; a miniatura troca a
    página do chat antes da primeira legenda (A-33), é pintada, e segue o fim da lista presa a ele."""
    from PySide6.QtGui import QImage

    from listener.ui.screen_grab import app_label
    from tests.test_live_partial import _caption
    from tests.test_suggest_ui import _tray

    assert app_label("Google Meet", platform.watched_apps()) == "janela do Google Meet"
    host, _n = _screen_host(qtbot, store, platform, monkeypatch)
    host.card.begin(host.mode, "14:05", None)
    host._done(SuggestResult(Outcome.ERROR, host.meeting_id, error="falha", with_screen=False))
    assert host.card.btn_plain.isHidden() and not host.card.btn_retry.isHidden()
    tray = _tray(qtbot, store, platform)
    chat = tray.chat
    thumb = QImage(320, 180, QImage.Format.Format_RGB32)
    thumb.fill(0x336699)
    assert chat.pages.currentWidget() is not chat.list
    chat.add_screen(5_000, thumb, "Tela enviada · janela do Teams", True)
    assert chat.pages.currentWidget() is chat.list
    qtbot.wait(30)
    shot = chat.list.viewport().grab().toImage()
    box = chat.list.visualItemRect(chat.list.item(0))
    cells = ((x, y) for x in range(box.left(), box.right(), 3) for y in range(box.top(), box.bottom(), 3))
    assert any(shot.pixel(x, y) & 0xFFFFFF == 0x336699 for x, y in cells)          # o delegate pinta a miniatura
    for i in range(40):
        _caption(store, chat.meeting_id, 10_000 + i * 3000, f"fala {i} sobre o orçamento de 2027", label="Ana — CFO")
    tray.load_new()
    bar = chat.list.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0 and bar.value() == bar.maximum(), timeout=2000)
    chat.add_screen(400_000, thumb, "Tela enviada · janela do Teams", True)
    qtbot.waitUntil(lambda: bar.value() == bar.maximum(), timeout=1000)


def test_the_tray_host_gives_the_suggestions_the_recording_clock_and_source(qtbot, store, platform, monkeypatch):
    """R-10 (A-05): o controlador passa só a gravação; o host das sugestões nasce com o relógio dela e lê a fonte a cada
    pedido com a tela."""
    from types import SimpleNamespace

    from listener.capture.recorder import SourceSpec
    from listener.ui.caption_tray_host import CaptionTrayHost
    from listener.ui.widget import RecordingWidget

    monkeypatch.setenv("LISTENER_LLM", "fake")
    mid = store.create_meeting("Orçamento", "meeting", None, None)
    widget = RecordingWidget(captions_button=True)
    qtbot.addWidget(widget)
    rec = SimpleNamespace(elapsed=lambda: 123.0, source=SourceSpec("app", pid=4242, exe="ms-teams.exe"))
    host = CaptionTrayHost(store, platform, widget, mid, on_mark=lambda *_a: None, on_open_live=lambda: None,
                           on_notice=lambda _t: None, marked=set(), speakers={}, live_state="live", suggest=True, recording=rec)
    try:
        assert host.suggest is not None and host.suggest.elapsed() == 123.0 and host.suggest.recording is rec
        host.suggest.screen.capturer()()
        assert platform.meeting_asks[-1][:2] == (4242, "ms-teams.exe")
    finally:
        if host.suggest is not None:
            host.suggest.teardown()
