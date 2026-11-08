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
    assert pick_target(platform, "cursor", 4242, "ms-teams.exe", watched).source == ScreenSource.FOREGROUND     # pula a janela
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
    view = host.screen.view
    assert view is not None and view.excluded and not view.picture.pixmap().isNull() and int(view.winId()) in platform.excluded
    view.close()
    platform.capture_exclusion = False                                            # a exclusão falhou: sem a imagem
    host.tray.chat.screen_clicked.emit(shots[0].data(SCREEN_ROLE))
    assert host.screen.view.picture.pixmap().isNull() and "escondida" in host.screen.view.picture.text()
    host.screen.view.close()


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
    from PySide6.QtGui import QImage

    from listener.store.suggestions import ScreenInsert
    from listener.ui.meeting_suggestions import SuggestionsTab
    from tests.test_suggest_store import suggestion

    def jpeg(w: int, h: int) -> bytes:
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(0x336699)
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPG")
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

