"""As telas da F5.17 no `win_probe shots` (não fazem parte do produto): a bandeja de legendas vazia; com o chat (você, dois
falantes, um deles com o cargo longo, um "Participante", uma legenda marcada, uma fala longa e o "↓ 2 novas" de quem subiu
para ler); recolhida; e na largura mínima (320 px, sem o título). E o widget com o botão "Legendas", com a bandeja à vista e
fechada. Nada é gravado além das legendas da reunião da sonda, no banco temporário dela.

`f517_shots(store, shot, app)` usa o `shot` do `win_probe` (a foto, os `cortados` e os `pequenos` de cada tela)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

CHAT = ((12_000, "Ana — CFO", "Bom dia a todos. Vamos começar pelo orçamento de 2027, que precisa fechar hoje."),
        (20_000, "Ana — CFO", "O corte proposto é de oito por cento em todas as áreas."),
        (31_000, None, "Concordo com o corte, mas a área de tecnologia precisa de uma exceção para o projeto de dados."),
        (45_000, "Bruno Carvalho — Diretor de Tecnologia e Operações", "Exato. Sem a exceção, o projeto atrasa dois trimestres."),
        (58_000, "Participante", "Posso fazer uma pergunta sobre o fluxo de caixa?"),
        (66_000, None, "Pode, claro."),
        (75_000, "Ana — CFO", ("Antes disso, vale registrar a decisão: o corte fica em oito por cento, com a exceção da "
                               "tecnologia aprovada até o fim do trimestre, e a planilha revisada circula amanhã.")))
LATE = ((90_000, "Bruno Carvalho — Diretor de Tecnologia e Operações", "Mando a planilha hoje à tarde."),
        (96_000, None, "Combinado."))


def _insert(store: Any, mid: int, rows: tuple[tuple[int, str | None, str], ...]) -> None:
    from listener.store.captions import CaptionInsert

    for t0, label, text in rows:
        store.captions.insert(CaptionInsert(mid, "L" if label is None else "R", t0, t0 + 6000, text, None, label, 0.8, "fake:small",
                                            6000))


def _discard(w: Any) -> None:
    """R-06: a janela da foto é apagada, não só fechada."""
    w.close()
    w.deleteLater()


def _offscreen(w: Any, app: Any) -> None:
    """Na tela de verdade, fora da vista e sem ativar (como o `shot`), para a lista ter a largura antes de rolar."""
    from PySide6.QtCore import Qt

    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.move(-10000, -10000)
    w.show()
    w.move(-10000, -10000)
    for _ in range(3):
        app.processEvents()


def f517_shots(store: Any, shot: Callable[..., None], app: Any) -> None:
    from PySide6.QtCore import Qt

    from listener.model import Health
    from listener.ui.caption_tray import CaptionTray
    from listener.ui.live_text import caption_key
    from listener.ui.widget import RecordingWidget

    mid = store.create_meeting("Comitê de investimentos (legendas)", "meeting", None, None)
    empty = CaptionTray(store, mid)
    empty.set_live_state("starting")
    shot(empty, "bandeja-legendas-vazia")
    _discard(empty)
    _insert(store, mid, CHAT)
    marked: set[Any] = set()
    tray = CaptionTray(store, mid, marked=marked)
    tray.set_live_state("live")
    _offscreen(tray, app)
    tray.load_new()
    chat = tray.chat
    bruno = next(chat.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(chat.list.count())
                 if "Bruno" in str(chat.list.item(i).data(Qt.ItemDataRole.UserRole).get("label")))
    marked.add(caption_key(bruno))
    tray.refresh_mark(caption_key(bruno))
    chat.list.scrollToTop()
    app.processEvents()
    _insert(store, mid, LATE)
    tray.on_caption({"type": "caption", "text": LATE[-1][2], "channel": "L", "label": None})   # quem subiu para ler
    shot(tray, "bandeja-legendas")
    tray.resize(320, tray.height())
    app.processEvents()
    shot(tray, "bandeja-legendas-320")
    tray.set_collapsed(True)
    shot(tray, "bandeja-legendas-recolhida")
    tray.set_collapsed(False)
    _discard(tray)
    for shown, name in ((True, "widget-legendas"), (False, "widget-legendas-fechada")):
        rw = RecordingWidget(captions_button=True)
        rw.update_state(754, {"L": 0.12, "R": 0.4}, {"L": Health.OK, "R": Health.OK}, False, False, 2)
        rw.set_live_state("live", "legenda ao vivo")
        rw.set_captions_shown(shown)
        shot(rw, name)
        _discard(rw)


__all__ = ["f517_shots"]
