"""F5.17, a bandeja de legendas (pytest-qt, offscreen): as contas puras do encosto e das bordas, o botão "Legendas" do
widget, o chat (lado, cabeçalho da sequência, ordem do tempo, cores da gravação), recolher, fechar e voltar com o que
faltou, o "↓ N novas", o menu do clique direito, a captura de tela, a posição guardada, o arrasto e as bordas, a medida
igual à pintura (D-094) e o controlador com o ao vivo falso, que abre a bandeja, marca nas duas janelas e a fecha com o
widget."""
from __future__ import annotations

from typing import Any

import pytest

from listener.capture.recorder import RecordConfig, SourceSpec
from listener.model import AppState
from listener.store.captions import CaptionInsert
from tests.fake_live import FakeEngines, ThreadChild, thread_spawner

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")


def _caption(store: Any, mid: int, t0: int, text: str, label: str | None = "Ana — CFO", channel: str = "R") -> int:
    return store.captions.insert(CaptionInsert(mid, channel, t0, t0 + 3000, text, None, label, 0.8, "fake:small", 6000))


def _tray(qtbot: Any, store: Any, mid: int, **kw: Any) -> Any:
    from listener.ui.caption_tray import CaptionTray

    tray = CaptionTray(store, mid, **kw)
    qtbot.addWidget(tray)
    tray.restore_geometry()
    tray.show()
    tray.load_new()
    return tray


# ---------------------------------------------------------------- contas puras
def test_geometry_reads_docks_anchors_and_resizes():
    from listener.ui import caption_geometry as g

    assert g.parse_rect("10,20,360,300") == (10, 20, 360, 300) and g.rect_text((10, 20, 360, 300)) == "10,20,360,300"
    assert g.parse_rect("") is None and g.parse_rect("1,2,3") is None and g.parse_rect("1,2,0,5") is None
    assert g.parse_dock("acima,0") == g.Dock("acima", 0) and g.parse_dock("abaixo,12") == g.Dock("abaixo", 12)
    assert g.parse_dock("solta") is None and g.parse_dock("lixo,3") is None and g.parse_dock("acima,x") == g.Dock("acima")
    assert g.dock_text(None) == "solta" and g.dock_text(g.Dock("abaixo", 5)) == "abaixo,5"
    widget, area = (900, 600, 360, 106), (0, 0, 1280, 720)
    assert g.docked(widget, (900, 292, 360, 300)) == g.Dock("acima", 0)                  # 8 px acima
    assert g.docked(widget, (880, 290, 360, 300)) == g.Dock("acima", 20)                 # 10 px acima, 20 px para a esquerda
    assert g.docked(widget, (900, 714, 360, 300)) == g.Dock("abaixo", 0)
    assert g.docked(widget, (900, 280, 360, 300)) is None                                # 20 px: solta
    assert g.docked(widget, (100, 292, 360, 300)) is None                                # sem cruzar na horizontal
    assert g.dock_rect(widget, (360, 300), g.Dock("acima"), area) == (900, 292, 360, 300)
    assert g.dock_rect(widget, (360, 300), g.Dock("acima"), (0, 400, 1280, 320)) == (900, 400, 360, 192)   # encolhe
    assert g.dock_rect(widget, (360, 300), g.Dock("acima"), (0, 500, 1280, 220))[3] == 160                  # até o mínimo
    assert g.dock_rect(widget, (360, 300), g.Dock("acima", 1000), area)[0] == 0                             # dentro da tela
    assert g.dock_rect(widget, (360, 300), g.Dock("abaixo"), area) == (900, 714, 360, 160)
    top = (900, 20, 360, 106)
    assert g.default_dock(widget, area) == g.Dock("acima") and g.default_dock(top, area) == g.Dock("abaixo")
    assert g.fit_dock(g.Dock("acima", 7), top, area, 160) == g.Dock("abaixo", 7)
    assert g.fit_dock(g.Dock("abaixo"), widget, area, 160) == g.Dock("acima")
    assert g.fit_dock(g.Dock("acima"), widget, area, 160) == g.Dock("acima")
    assert g.anchored((100, 100, 360, 300), 76, None) == (100, 324, 360, 76)            # solta: a borda de baixo
    assert g.anchored((100, 100, 360, 300), 76, g.Dock("abaixo")) == (100, 100, 360, 76)  # embaixo do widget: a de cima
    assert g.anchored((100, 324, 360, 76), 300, None, (0, 200, 1280, 520), 160) == (100, 200, 360, 200)
    size = (360, 300)
    assert g.edges_at(2, 150, size) == g.Edge.LEFT and g.edges_at(358, 150, size) == g.Edge.RIGHT
    assert g.edges_at(180, 2, size) == g.Edge.TOP and g.edges_at(180, 298, size) == g.Edge.BOTTOM
    assert g.edges_at(2, 2, size) == g.Edge.LEFT | g.Edge.TOP and g.edges_at(180, 150, size) == g.Edge.NONE
    assert g.edges_at(180, 2, size, collapsed=True) == g.Edge.NONE and g.edges_at(2, 2, size, collapsed=True) == g.Edge.LEFT
    start = (100, 100, 360, 300)
    assert g.resized(start, g.Edge.LEFT, -50, 0) == (50, 100, 410, 300)
    assert g.resized(start, g.Edge.LEFT, 100, 0) == (140, 100, 320, 300)                # o mínimo segura a borda
    assert g.resized(start, g.Edge.TOP, 0, -40) == (100, 60, 360, 340)
    assert g.resized(start, g.Edge.RIGHT | g.Edge.BOTTOM, -100, -200) == (100, 100, 320, 160)


# ---------------------------------------------------------------- widget
def test_widget_captions_button_sits_before_stop_and_shows_the_tray_state(qtbot):
    """Pergunta 6: sem o preenchimento do Mudo; o ícone diz se a bandeja está à vista. Sem bandeja, o botão não aparece, e
    a altura do widget é a mesma (106 px na base)."""
    from PySide6.QtCore import Qt

    from listener.ui.widget import RecordingWidget

    w = RecordingWidget(captions_button=True)
    plain = RecordingWidget()
    for x in (w, plain):
        qtbot.addWidget(x)
        x.show()
    assert w.btn_captions.isVisibleTo(w) and not plain.btn_captions.isVisibleTo(plain) and w.height() == plain.height() == 106
    assert w.btn_devices.x() < w.btn_captions.x() < w.btn_stop.x() and not w.btn_captions.isCheckable()
    w.set_captions_shown(False)
    assert w.btn_captions.property("theme_icon") == "chat-off:text:20" and "mostrar" in w.btn_captions.toolTip()
    w.set_captions_shown(True)
    assert w.btn_captions.property("theme_icon") == "chat:text:20" and "esconder" in w.btn_captions.toolTip()
    with qtbot.waitSignal(w.captions_clicked, timeout=1000):
        qtbot.mouseClick(w.btn_captions, Qt.MouseButton.LeftButton)


# ---------------------------------------------------------------- o chat
def test_tray_chat_sides_heads_order_and_colors(qtbot, store, platform):
    from listener.ui.chat_delegate import HEAD_ROLE, MINE_ROLE
    from listener.ui.speech_delegate import COLOR_ROLE, SPEAKER_ROLE

    store.upsert_me("Eu", role="Gerente")
    mid = store.create_meeting("Diretoria", None, None, None)
    _caption(store, mid, 2000, "bom dia a todos")
    _caption(store, mid, 30000, "vamos ao orçamento")
    _caption(store, mid, 40000, "concordo", None, "L")                  # sem nome: o L fora da sala é você
    _caption(store, mid, 50000, "uma dúvida", "Participante")
    _caption(store, mid, 200000, "seguimos")                            # mais de 60 s depois: o cabeçalho volta
    speakers = {"Bruno": 0}                                             # as cores da gravação, também da janela Ao vivo
    tray = _tray(qtbot, store, mid, platform=platform, speakers=speakers)
    lst = tray.chat.list
    rows = [lst.item(i) for i in range(lst.count())]
    assert [i.text() for i in rows] == ["[00:02] Ana — CFO: bom dia a todos", "[00:30] Ana — CFO: vamos ao orçamento",
                                        "[00:40] Você: concordo", "[00:50] Participante: uma dúvida", "[03:20] Ana — CFO: seguimos"]
    assert [i.data(MINE_ROLE) for i in rows] == [False, False, True, False, False]
    assert [i.data(HEAD_ROLE) for i in rows] == [True, False, True, True, True]
    assert [i.data(SPEAKER_ROLE) for i in rows] == ["Ana", "Ana", "", "", "Ana"]
    assert speakers == {"Bruno": 0, "Ana": 1} and rows[0].data(COLOR_ROLE) == 1
    # fora de ordem: a fala do R que terminou depois entra no lugar do tempo e entra na sequência
    _caption(store, mid, 10000, "e o fluxo de caixa")
    tray.on_caption({"type": "caption", "text": "e o fluxo de caixa", "channel": "R", "label": "Ana — CFO"})
    rows = [lst.item(i) for i in range(lst.count())]
    assert rows[1].text() == "[00:10] Ana — CFO: e o fluxo de caixa" and [i.data(HEAD_ROLE) for i in rows[:3]] == [True, False, False]
    d, width = tray.chat.delegate, lst.item_width()
    assert rows[1].sizeHint() == d.item_size("e o fluxo de caixa", False, width)
    assert d.item_size("x", True, width).height() - d.item_size("x", False, width).height() == d.head_h + 2
    tray.resize(tray.width() + 120, tray.height())                      # a largura nova refaz as alturas, uma vez
    qtbot.waitUntil(lambda: lst.laid_width == lst.item_width() and lst.item_width() == width + 120, timeout=3000)
    assert rows[1].sizeHint() == d.item_size("e o fluxo de caixa", False, width + 120)


def test_tray_collapses_closes_and_catches_up(qtbot, store, platform):
    mid = store.create_meeting("Diretoria", None, None, None)
    _caption(store, mid, 1000, "primeira")
    tray = _tray(qtbot, store, mid, platform=platform)
    tray.setGeometry(100, 100, 360, 300)
    tray.full_height = 300
    tray.set_collapsed(True)
    assert not tray.chat.isVisibleTo(tray) and tray.line.isVisibleTo(tray) and tray.btn_min.text() == "Restaurar"
    assert tray.height() == tray.collapsed_height() and tray.y() + tray.height() == 400            # a borda de baixo no lugar
    assert store.get_setting("ui.caption_tray_collapsed") == "1"
    _caption(store, mid, 5000, "segunda", None, "L")
    tray.on_caption({"type": "caption", "text": "segunda", "channel": "L", "label": None})
    assert tray.stale and tray.chat.list.count() == 1 and tray.line.full_text == "Você: segunda"   # recolhida, não lê o banco
    assert tray.line.toolTip() == "Clique para abrir o chat"            # D-095: a dica (fora da exclusão) sem a legenda
    tray.line.clicked.emit()                                                                     # o clique na linha restaura
    assert not tray.collapsed and tray.chat.list.count() == 2 and not tray.stale
    assert (tray.y(), tray.height()) == (100, 300) and store.get_setting("ui.caption_tray_collapsed") == "0"
    with qtbot.waitSignal(tray.closed_by_user, timeout=1000):
        tray.btn_close.click()
    assert tray.isHidden()
    _caption(store, mid, 9000, "terceira")
    tray.on_caption({"type": "caption", "text": "terceira", "channel": "R", "label": "Ana — CFO"})
    assert tray.stale and tray.chat.list.count() == 2
    tray.show_again()
    assert tray.isVisible() and tray.chat.list.count() == 3 and not tray.stale


def test_tray_new_button_menu_mark_and_copy(qtbot, store, platform):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    from listener.ui.live_text import caption_key, caption_note
    from listener.ui.speech_delegate import FLAG_ROLE

    mid = store.create_meeting("Diretoria", None, None, None)
    for i in range(40):                                                  # mais de 20: entram em lote
        _caption(store, mid, i * 5000, f"fala {i} " + "texto " * 12, "Ana — CFO" if i % 2 else None, "R" if i % 2 else "L")
    marked: set[Any] = set()
    marks: list[dict[str, Any]] = []
    notes: list[str] = []

    def on_mark(row: Any) -> tuple[bool, str]:
        marks.append(dict(row))
        if len(marks) > 1:
            return False, "não consegui criar o marcador"
        marked.add(caption_key(row))                                     # o controlador guarda a chave antes de responder
        return True, "marcador criado"

    tray = _tray(qtbot, store, mid, platform=platform, on_mark=on_mark, on_notice=notes.append, marked=marked)
    chat = tray.chat
    assert chat.list.count() == 40
    qtbot.waitUntil(chat.list.at_end, timeout=3000)
    chat.list.scrollToTop()
    for t0 in (300000, 305000):                                          # quem subiu para ler fica onde está
        _caption(store, mid, t0, "nova fala")
        tray.on_caption({"type": "caption", "text": "nova fala", "channel": "R", "label": "Ana — CFO"})
    assert chat.unread == 2 and chat.new_btn.isVisibleTo(tray) and chat.new_btn.text() == "↓ 2 novas"
    chat.new_btn.click()
    assert chat.unread == 0 and not chat.new_btn.isVisibleTo(tray) and chat.list.at_end()
    chat.list.scrollToTop()
    first = chat.list.item(0)
    row = first.data(Qt.ItemDataRole.UserRole)
    chat._context_menu(chat.list.visualItemRect(first).center())
    assert chat.menu is not None and [a.text() for a in chat.menu.actions()] == ["Criar marcador neste instante", "Copiar"]
    chat.menu.actions()[0].trigger()
    assert marks == [row] and first.data(FLAG_ROLE) is True and notes == []
    chat.menu.close()
    chat._context_menu(chat.list.visualItemRect(first).center())
    assert not chat.menu.actions()[0].isEnabled()                                               # uma vez por legenda
    chat.menu.actions()[1].trigger()
    assert QGuiApplication.clipboard().text() == caption_note(row)
    chat.menu.close()
    chat.mark(chat.list.item(1).data(Qt.ItemDataRole.UserRole))                                # a falha vira aviso
    assert notes == ["não consegui criar o marcador"] and chat.list.item(1).data(FLAG_ROLE) is False
    tray.set_finished()
    chat._context_menu(chat.list.visualItemRect(chat.list.item(2)).center())
    assert not chat.menu.actions()[0].isEnabled() and chat.menu.actions()[1].isEnabled()
    chat.menu.close()


def test_tray_stays_at_the_end_when_the_list_changes_and_the_menu_ignores_alt(qtbot, store, platform):
    """Revisão do diff: presa ao fim, a lista volta ao fim quando a faixa da barra muda (a bandeja mais baixa, as alturas
    refeitas, aberta recolhida com mais de 20 falas e restaurada); só a rolagem de quem lê a solta. O menu ignora o Alt que
    o `bring_to_front` injeta e fecha com o Esc. A roda anda 20 px por passo."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from listener.ui.caption_list import SCROLL_STEP_PX

    mid = store.create_meeting("Diretoria", None, None, None)
    for i in range(40):
        _caption(store, mid, i * 5000, f"fala {i} " + "texto " * 12)
    store.set_setting("ui.caption_tray_collapsed", "1")                 # abre recolhida: as falas entram sem largura
    tray = _tray(qtbot, store, mid, platform=platform)
    lst = tray.chat.list
    assert tray.collapsed and lst.count() == 40 and lst.laid_width == 0
    tray.restore()
    qtbot.waitUntil(lambda: lst.laid_width > 0 and lst.verticalScrollBar().maximum() > 0 and lst.at_end(), timeout=3000)
    assert lst.pinned and lst.verticalScrollBar().singleStep() == SCROLL_STEP_PX
    tray.resize(tray.width(), tray.height() - 100)                      # a bandeja mais baixa: continua no fim
    qtbot.waitUntil(lst.at_end, timeout=3000)
    tray.resize(tray.width() - 30, tray.height())                       # as alturas refeitas: continua no fim
    qtbot.waitUntil(lambda: lst.laid_width == lst.item_width() and lst.at_end(), timeout=3000)
    lst.verticalScrollBar().setValue(0)                                 # quem lê subiu: solta
    assert not lst.pinned
    tray.resize(tray.width(), tray.height() + 60)
    qtbot.wait(150)
    assert lst.verticalScrollBar().value() == 0 and not lst.pinned
    tray.chat.scroll_to_end()
    tray.chat._context_menu(lst.visualItemRect(lst.item(lst.count() - 1)).center())
    menu = tray.chat.menu
    assert menu is not None and menu.isVisible()
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(menu, QKeyEvent(kind, Qt.Key.Key_Alt, Qt.KeyboardModifier.AltModifier))
    assert menu.isVisible()                                             # o Alt injetado não o fecha
    QApplication.sendEvent(menu, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not menu.isVisible()


def test_tray_leaves_capture_and_restores_where_it_was(qtbot, store, platform):
    from listener.ui.caption_geometry import GAP, Dock
    from listener.ui.caption_tray import CaptionTray, area_of

    mid = store.create_meeting("Diretoria", None, None, None)
    tray = CaptionTray(store, mid, platform=platform)
    qtbot.addWidget(tray)
    assert tray.excluded is True and int(tray.winId()) in platform.excluded
    platform.capture_exclusion = False
    refused = CaptionTray(store, mid, platform=platform)
    qtbot.addWidget(refused)
    assert refused.excluded is False
    ax, ay, aw, ah = area_of((0, 0, 1, 1))
    anchor = [(ax + aw - 380, ay + ah - 130, 360, 106)]
    docked = CaptionTray(store, mid, platform=platform, anchor=lambda: anchor[0])
    qtbot.addWidget(docked)
    docked.restore_geometry()                                            # nada salvo: encostada acima do widget
    wx, wy, ww, wh = anchor[0]
    g = docked.geometry()
    assert docked.dock == Dock("acima", 0) and (g.y() + g.height(), g.x() + g.width()) == (wy - GAP, wx + ww)
    anchor[0] = (wx, ay + 10, ww, wh)                                    # o widget no alto da tela: a bandeja embaixo
    docked.follow()
    assert docked.y() == ay + 10 + wh + GAP
    docked.dock = None                                                   # solta: a posição e o tamanho voltam como estavam
    docked.setGeometry(ax + 50, ay + 60, 400, 250)
    docked.full_height = 250
    docked.save_geometry()
    assert store.get_setting("ui.caption_tray_dock") == "solta"
    again = CaptionTray(store, mid, platform=platform, anchor=lambda: anchor[0])
    qtbot.addWidget(again)
    again.restore_geometry()
    assert again.dock is None and (again.x(), again.y(), again.width(), again.height()) == (ax + 50, ay + 60, 400, 250)
    store.set_setting("ui.caption_tray_geometry", "-9000,-9000,400,250")    # fora dos monitores: encostada no widget
    lost = CaptionTray(store, mid, platform=platform, anchor=lambda: anchor[0])
    qtbot.addWidget(lost)
    lost.restore_geometry()
    assert lost.dock is not None and lost.y() == ay + 10 + wh + GAP


def test_tray_drags_resizes_by_the_edges_and_snaps_to_the_widget(qtbot, store, platform):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from listener.ui.caption_geometry import Dock

    def mouse(kind: QEvent.Type, local: tuple[int, int], held: bool = True) -> None:
        pos = QPoint(*local)
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        buttons = Qt.MouseButton.LeftButton if held and kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
        event = QMouseEvent(kind, QPointF(pos), QPointF(start.topLeft() + pos), button, buttons, Qt.KeyboardModifier.NoModifier)
        {QEvent.Type.MouseButtonPress: tray.mousePressEvent, QEvent.Type.MouseMove: tray.mouseMoveEvent,
         QEvent.Type.MouseButtonRelease: tray.mouseReleaseEvent}[kind](event)

    mid = store.create_meeting("Diretoria", None, None, None)
    widget = (100, 400, 360, 106)
    tray = _tray(qtbot, store, mid, platform=platform, anchor=lambda: widget)
    tray.dock = None
    tray.setGeometry(100, 50, 360, 300)
    start = tray.geometry()
    mouse(QEvent.Type.MouseButtonPress, (60, 15))                        # o cabeçalho: arrasta
    mouse(QEvent.Type.MouseMove, (60, 50))
    assert tray.y() == 85
    mouse(QEvent.Type.MouseButtonRelease, (60, 50))
    assert tray.dock == Dock("acima", 0) and tray.y() + tray.height() == 392    # a 15 px do widget: encosta a 8
    assert store.get_setting("ui.caption_tray_dock") == "acima,0"
    start = tray.geometry()
    mouse(QEvent.Type.MouseMove, (2, 150), held=False)
    assert tray.cursor().shape() == Qt.CursorShape.SizeHorCursor
    mouse(QEvent.Type.MouseButtonPress, (2, 150))                        # a borda esquerda: alarga para a esquerda
    mouse(QEvent.Type.MouseMove, (-48, 150))
    mouse(QEvent.Type.MouseButtonRelease, (-48, 150))
    assert (tray.x(), tray.width()) == (50, 410) and tray.dock == Dock("acima", 0)
    start = tray.geometry()
    mouse(QEvent.Type.MouseButtonPress, (200, 2))                        # a borda de cima: encolhe até o mínimo
    mouse(QEvent.Type.MouseMove, (200, 250))
    mouse(QEvent.Type.MouseButtonRelease, (200, 250))
    assert tray.height() == tray.full_height == 160 and tray.y() + tray.height() == 392


def test_the_bubble_is_painted_inside_the_measured_height(qtbot):
    """D-094: a altura vem do item, medida uma vez; a pintura quebra igual e não passa dela. Sem `sizeHint` em Python na
    bandeja nem na janela Ao vivo; a Transcrição (um modelo) segue pedindo a altura a cada layout."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QImage, QPainter, QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QListView, QStyleOptionViewItem

    from listener.ui.chat_delegate import HEAD_ROLE, MINE_ROLE, ChatDelegate
    from listener.ui.speech_delegate import (COLOR_ROLE, FLAG_ROLE, ROLE_ROLE, SPEAKER_ROLE, STAMP_ROLE, TEXT_ROLE, SpeechDelegate,
                                             SpeechItemDelegate)

    assert "sizeHint" not in vars(ChatDelegate) and "sizeHint" not in vars(SpeechItemDelegate) and "sizeHint" in vars(SpeechDelegate)
    view = QListView()
    qtbot.addWidget(view)
    delegate = ChatDelegate(view)
    model = QStandardItemModel()
    texts = ("ok", "uma fala curta", "uma fala bem comprida " * 12, "palavra" * 40, "quebra\nde linha\tcom tabulação")
    for text in texts:
        for mine in (False, True):
            for head in (False, True):
                item = QStandardItem(text)
                for role, value in ((TEXT_ROLE, text), (MINE_ROLE, mine), (HEAD_ROLE, head), (SPEAKER_ROLE, "Maria Aparecida"),
                                    (ROLE_ROLE, "Gerente de TI"), (COLOR_ROLE, 2), (STAMP_ROLE, 754_000), (FLAG_ROLE, head)):
                    item.setData(value, role)
                model.appendRow(item)
    for width in (320, 451, 700):
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            height = delegate.item_size(index.data(TEXT_ROLE), bool(index.data(HEAD_ROLE)), width).height()
            image = QImage(width, height + 40, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            option = QStyleOptionViewItem()
            option.initFrom(view)
            option.rect = QRect(0, 0, width, height)
            delegate.paint(painter, option, index)
            painter.end()
            below = QImage(width, 40, QImage.Format.Format_ARGB32_Premultiplied)
            below.fill(Qt.GlobalColor.transparent)
            assert image.copy(0, height, width, 40) == below, (width, row)


# ---------------------------------------------------------------- controlador
def test_controller_opens_the_tray_marks_in_both_views_and_closes_it_with_the_widget(qtbot, qapp, store, platform, monkeypatch):
    from PySide6.QtCore import Qt

    from listener.ui import controller as ctl
    from listener.ui.live_text import caption_key
    from listener.ui.speech_delegate import FLAG_ROLE

    monkeypatch.setenv("LISTENER_LIVE", "fake")
    store.upsert_me("Eu", role="Gerente")
    store.set_setting("live.enabled", True)
    children: list[ThreadChild] = []
    monkeypatch.setattr(platform, "spawn_child", thread_spawner(store.base_dir, FakeEngines(), children))
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, {"title": "bandeja", "live_names": True})
        w, host = c.widget, c.captions
        assert w is not None and host is not None and not w.live_line and w.btn_captions.isVisibleTo(w)
        tray = host.tray
        assert tray.isVisible() and tray.parent() is w and int(tray.winId()) in platform.excluded
        qtbot.waitUntil(lambda: c.live_status == "live", timeout=5000)
        assert tray.badge.text() == "ao vivo"
        platform.pump(10.5)                                                    # tom contínuo: a janela fecha em 10 s
        qtbot.waitUntil(lambda: tray.chat.list.count() >= 1, timeout=5000)
        row = tray.chat.list.item(0).data(Qt.ItemDataRole.UserRole)
        win = c.open_live_window()
        assert win is not None and win.speakers is c._speakers
        tray.chat.mark(row)
        assert caption_key(row) in c._marked_captions and tray.chat.list.item(0).data(FLAG_ROLE) is True
        assert win.list.item(0).data(FLAG_ROLE) is True and win.list.item(0).text().startswith("⚑")
        w.captions_clicked.emit()
        assert tray.isHidden() and w.btn_captions.property("theme_icon") == "chat-off:text:20"
        w.captions_clicked.emit()
        assert tray.isVisible() and w.btn_captions.property("theme_icon") == "chat:text:20"
        before = tray.x()
        w.move(w.x() - 40, w.y())                                              # encostada, acompanha o widget
        qtbot.waitUntil(lambda: tray.x() == before - 40, timeout=3000)
        c.stop_recording()
        assert tray.chat.finished
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
        assert c.captions is None and store.get_setting("ui.caption_tray_dock").startswith("acima")
        win.close()
    finally:
        c.emergency_stop()


def test_controller_without_the_tray_keeps_the_widget_line(qtbot, qapp, store, platform, monkeypatch):
    """`live.caption_tray` desligada: tudo como antes (a linha do widget, sem botão e sem bandeja)."""
    from listener.ui import controller as ctl

    monkeypatch.setenv("LISTENER_LIVE", "fake")
    store.set_setting("live.enabled", True)
    store.set_setting("live.caption_tray", False)
    children: list[ThreadChild] = []
    monkeypatch.setattr(platform, "spawn_child", thread_spawner(store.base_dir, FakeEngines(), children))
    c = ctl.Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(ctl, "SummaryDialog", lambda *a, **k: type("D", (), {"exec": lambda self: None})())
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5, stop_timeout_s=1.0,
                       retry_base_s=0.001)
    try:
        assert c.start_recording(cfg, {"title": "sem bandeja"})
        assert c.captions is None and c.widget is not None and c.widget.live_line and not c.widget.btn_captions.isVisibleTo(c.widget)
        c.stop_recording()
        qtbot.waitUntil(lambda: c.state == AppState.IDLE, timeout=10000)
    finally:
        c.emergency_stop()
