"""As telas da F5.20 no `win_probe shots` (não fazem parte do produto): a bandeja de legendas com o cartão da sugestão (três
opções: uma "Boa", uma "Ruim" com os motivos abertos, uma "sem evidência"; o cartão tem até 45 % da bandeja, então são três
fotos, rolado no começo, nos motivos e no fim), o cartão recolhido, o cartão "pedindo…" com uma opção, o cartão em "Me
situar", os dois popups como o host os abre (o texto livre e o objetivo, vazios, com a dica inteira), o "Antes de gravar"
com a caixa "Sugestões" e o objetivo, a página Prompts rolada até o memorando e a aba "Sugestões" da reunião. Nada é
gravado fora do banco temporário da sonda.

`f520_shots(store, shot, app, platform)` usa o `shot` do `win_probe` (a foto, os `cortados` e os `pequenos` de cada tela).
`f520_timings(store, app, platform)` é o Aceite "Interface" da spec (cada legenda nova e cada opção chegando abaixo de 5 ms na
thread da interface [chute]): a bandeja com 2.000 legendas e o cartão aberto, com o host das sugestões de verdade. O `ok` é
pelo máximo ("cada", R-04 da revisão do L4), com a mediana e o p95 ao lado; as `pinturas` de uma rodada fora do cronômetro
provam que a bandeja fora da tela é pintada (sem pintura, a medida sairia pela metade)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, override

from probe_f517 import CHAT, _discard, _insert, _offscreen

SPEAK = (("Perguntar", "Antes de fecharmos o corte, qual é o impacto no projeto de dados?", ((4, 45_000),)),
         ("Posicionar", "Apoio o corte de oito por cento, com a exceção da tecnologia até o fim do trimestre.", ((1, 12_000), (2, 20_000))),
         ("Propor o próximo passo", "Proponho fecharmos hoje e revisarmos a planilha amanhã cedo.", ()))
ORIENT = (("Assunto", "O orçamento de 2027 e a exceção da área de tecnologia.", ((1, 12_000),)),
          ("Decidido", "O corte de oito por cento em todas as áreas.", ((7, 75_000),)),
          ("Pendente", "A exceção para o projeto de dados e o fluxo de caixa.", ((5, 58_000),)))


def _options(card: Any, rows: tuple[tuple[str, str, tuple[tuple[int, int], ...]], ...]) -> None:
    from listener.llm.suggest_task import SuggestOption
    from listener.model import Evidence

    for i, (kind, text, facts) in enumerate(rows):
        card.add_option(SuggestOption(i, kind, text, facts, Evidence.OK if facts else Evidence.NONE))


def f520_shots(store: Any, shot: Callable[..., None], app: Any, platform: Any = None) -> None:
    from PySide6.QtWidgets import QScrollArea

    from listener.llm.suggest_task import Outcome, SuggestResult
    from listener.llm.types import LlmTask
    from listener.model import SuggestMode
    from listener.ui.caption_tray import CaptionTray
    from listener.ui.settings_min import SettingsDialog
    from listener.ui.settings_prompts import PROMPTS_TITLE
    from listener.ui.suggest_card import SuggestCard
    from listener.ui.suggest_host import GOAL_ASK, NUDGE_ASK
    from listener.ui.suggest_popup import SuggestTextPopup

    mid = store.create_meeting("Comitê de investimentos (sugestões)", "meeting", None, None, goal="aprovar a exceção da tecnologia")
    _insert(store, mid, CHAT)
    tray = CaptionTray(store, mid, platform=platform)
    tray.set_live_state("live")
    tray.restore_geometry()
    tray.resize(tray.width(), 560)
    _offscreen(tray, app)
    tray.load_new()
    tray.set_suggest_state(True, True, "Sugerir o que falar agora (Ctrl+Alt+Shift+D) · ~US$ 0,001 por pedido", False)
    card = SuggestCard()
    tray.set_card(card)
    tray.show_card()
    card.begin(SuggestMode.SPEAK, "14:05", 81_000)
    _options(card, SPEAK)
    card.finish(SuggestResult(Outcome.DONE, mid, cost_usd=0.0007), [1, 2, 3], "Ruim: longa demais (1); Boa: 1")
    card.rows[0].set_rating(1)
    card.rows[1].set_rating(0, "longa demais")
    card.set_chips(["mais curta"])
    card.set_nudge("mais curta")
    shot(tray, "f520-bandeja-sugestao")
    for target, name in ((card.rows[1].reasons, "motivos"), (card.rows[2], "fim")):   # R-01 da revisão do L4: o cartão rola
        card.body_area.ensureWidgetVisible(target)
        app.processEvents()
        shot(tray, f"f520-bandeja-sugestao-{name}")
    card.body_area.verticalScrollBar().setValue(0)
    card.set_collapsed(True)
    shot(tray, "f520-bandeja-sugestao-recolhida")
    card.set_collapsed(False)
    card.begin(SuggestMode.SPEAK, "14:07", 96_000)
    _options(card, SPEAK[:1])
    shot(tray, "f520-bandeja-sugestao-pedindo")
    card.begin(SuggestMode.ORIENT, "14:08", 96_000)
    _options(card, ORIENT)
    card.finish(SuggestResult(Outcome.DONE, mid, cost_usd=0.0005), [4, 5, 6], "")
    shot(tray, "f520-bandeja-me-situar")
    tray.set_card(None)
    card.deleteLater()
    _discard(tray)
    for (title, hint), name in ((NUDGE_ASK, "como-sugerir"), (GOAL_ASK, "objetivo")):   # R-03 da revisão do L4: como o host abre
        popup = SuggestTextPopup(title, "", hint, platform)
        shot(popup, f"f520-popup-{name}")
        _discard(popup)
    if platform is not None:
        _warmup_shot(store, shot, platform)
    store.set_setting("suggest.memo", "Prefere opções curtas, em pergunta, com os números da reunião; evita tom de cobrança.")
    store.set_setting("suggest.memo_at", "2026-11-08T14:30:00.000-03:00")
    store.set_setting("suggest.memo_ratings", "14")
    sd = SettingsDialog(store, platform, {})
    try:
        sd.nav.setCurrentRow(list(sd.pages).index(PROMPTS_TITLE))
        pane = sd.select_prompt(LlmTask.SUGGEST_MEMO)
        _offscreen(sd, app)                                                       # o layout pronto antes de rolar
        page = sd.stack.currentWidget()
        if isinstance(page, QScrollArea) and pane.memo is not None:             # R-01 da revisão do L4: o memorando à vista
            page.ensureWidgetVisible(pane.memo)
            app.processEvents()
        shot(sd, "f520-prompts-memorando")
        sd.reject()
    finally:
        _discard(sd)
    _meeting_tab_shot(store, shot, mid)


def _warmup_shot(store: Any, shot: Callable[..., None], platform: Any) -> None:
    """O "Antes de gravar" com a legenda ligada, a caixa "Sugestões" e o objetivo (U-156, U-163)."""
    from listener.ui.warmup import WarmupDialog

    before = store.get_setting("live.enabled")
    store.set_setting("live.enabled", True)
    try:
        dlg = WarmupDialog(platform, store)
        dlg.live.goal_edit.setText("aprovar a exceção da tecnologia até o fim do trimestre")
        shot(dlg, "f520-warmup-sugestoes")
        dlg.reject()
        _discard(dlg)
    finally:
        store.set_setting("live.enabled", before)


def _meeting_tab_shot(store: Any, shot: Callable[..., None], mid: int) -> None:
    """A aba "Sugestões" da reunião com um pedido avaliado (U-162)."""
    import json

    from listener.model import Evidence, SuggestStatus
    from listener.store.suggestions import OptionInsert, SuggestionInsert
    from listener.ui.meeting import MeetingWindow

    sid = store.suggestions.insert(SuggestionInsert(mid, 81_000, "fala", "mais curta", None, 0, 7, 81_000, True, 900, 620, "o fio",
                                                    None, "{}", "fake", "m", "suggest/1.0", None, 0.0007, False, 1400, 3900,
                                                    SuggestStatus.OK, None))
    ids = store.suggestions.insert_options(sid, [OptionInsert(i, kind, text, json.dumps([{"caption_id": c, "t_ms": t} for c, t in facts]),
                                                              Evidence.OK if facts else Evidence.NONE)
                                                 for i, (kind, text, facts) in enumerate(SPEAK)])
    store.suggestions.set_rating(ids[0], 1)
    store.suggestions.set_rating(ids[1], 0, "longa demais")
    store.suggestions.set_used(ids[0], True)
    win = MeetingWindow(store, mid)
    win.show_tab("sugestoes")
    shot(win, "f520-reuniao-sugestoes")
    _discard(win)


GOAL_MS = 5.0                             # o Aceite "Interface" [chute]


def _stats(samples_ms: list[float]) -> dict[str, float | int]:
    s = sorted(samples_ms)
    return {"n": len(s), "p50": round(s[len(s) // 2], 3), "p95": round(s[min(len(s) - 1, int(0.95 * len(s)))], 3),
            "max": round(s[-1], 3)}


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def f520_timings(store: Any, app: Any, platform: Any, *, falas: int = 2000, novas: int = 200, pedidos: int = 50) -> dict[str, Any]:
    """Na thread da interface, com os eventos que cada passo posta (o layout e a pintura): cada legenda nova, gravada antes
    (como o filho faz) e lida pela bandeja e pelo host das sugestões (o "falaram com você" com o primeiro nome), e cada opção
    chegando ao cartão pelo slot do `progress`. A bandeja tem `falas` legendas e o cartão aberto com três opções; o host é o
    do app (`CaptionTrayHost.on_caption` repassa a legenda aos dois), sem o dossiê, que roda numa `Task`."""
    import time

    from PySide6.QtCore import QCoreApplication, QEvent

    from listener.llm.suggest_task import SuggestOption
    from listener.model import Evidence, SuggestMode
    from listener.store.captions import CaptionInsert
    from listener.ui.caption_tray import CaptionTray
    from listener.ui.suggest_host import SuggestHost
    from listener.ui.widget import RecordingWidget

    dossier = store.get_setting("suggest.dossier")
    store.set_setting("suggest.dossier", False)
    mid = store.create_meeting("Comitê de investimentos (tempos das sugestões)", "meeting", None, None)
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
    host.first = "Carla"                      # o "falaram com você" (U-160) lê cada legenda do R
    host.name_hint = True
    tray.show_card()
    host.card.begin(SuggestMode.SPEAK, "14:05", falas * 3000)
    _options(host.card, SPEAK)
    app.processEvents()
    caption_ms: list[float] = []
    option_ms: list[float] = []
    try:
        for k in range(novas):
            t0 = (falas + k) * 3000
            text = f"Carla, pode comentar o item {k}?" if k % 10 == 0 else f"Sigo com o item {k} do orçamento e o fluxo de caixa."
            cid = store.captions.insert(CaptionInsert(mid, "R", t0, t0 + 2500, text, None, "Ana — CFO", 0.8, "fake:small", 2500))
            msg = {"type": "caption", "id": cid, "channel": "R", "t_start_ms": t0, "t_end_ms": t0 + 2500, "text": text,
                   "label": "Ana — CFO"}
            start = time.perf_counter()
            tray.on_caption(msg)
            host.on_caption(msg)
            app.processEvents()
            caption_ms.append((time.perf_counter() - start) * 1000)
        for _ in range(pedidos):
            tray.show_card()
            host.card.begin(SuggestMode.SPEAK, "14:06", (falas + novas) * 3000)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)   # as opções de antes, como o laço do app
            app.processEvents()                   # R-02 da revisão do L4: no app, o laço pinta o "pedindo…" antes da 1ª opção
            for i, (kind, text, facts) in enumerate(SPEAK):
                option = SuggestOption(i, kind, text, facts, Evidence.OK if facts else Evidence.NONE)
                start = time.perf_counter()
                host._option(option)
                app.processEvents()
                option_ms.append((time.perf_counter() - start) * 1000)
        paints = _paints(app, store, mid, tray, host, falas + novas)
    finally:
        host.teardown()
        _discard(tray)
        _discard(widget)
        app.processEvents()
        store.set_setting("suggest.dossier", dossier)
    legenda, opcao = _stats(caption_ms), _stats(option_ms)
    return {"falas": falas, "legenda_ms": legenda, "opcao_ms": opcao, "meta_ms": GOAL_MS, "pinturas": paints,
            "ok": legenda["max"] < GOAL_MS and opcao["max"] < GOAL_MS}


def _paints(app: Any, store: Any, mid: int, tray: Any, host: Any, k: int) -> dict[str, int]:
    """As pinturas de uma legenda e de uma opção, numa rodada fora do cronômetro (o filtro custa a cada evento). O Qt entra na
    chamada: o topo do `win_probe` só importa a biblioteca padrão."""
    from PySide6.QtCore import QEvent, QObject

    from listener.llm.suggest_task import SuggestOption
    from listener.model import Evidence
    from listener.store.captions import CaptionInsert

    class PaintCount(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.n = 0

        @override
        def eventFilter(self, _watched: QObject, event: QEvent) -> bool:
            if event.type() == QEvent.Type.Paint:
                self.n += 1
            return False

    counter = PaintCount()
    app.installEventFilter(counter)
    try:
        t0 = k * 3000
        cid = store.captions.insert(CaptionInsert(mid, "R", t0, t0 + 2500, "Uma fala a mais.", None, "Ana — CFO", 0.8, "fake:small", 2500))
        msg = {"type": "caption", "id": cid, "channel": "R", "t_start_ms": t0, "t_end_ms": t0 + 2500, "text": "Uma fala a mais.",
               "label": "Ana — CFO"}
        tray.on_caption(msg)
        host.on_caption(msg)
        app.processEvents()
        caption, counter.n = counter.n, 0
        kind, text, facts = SPEAK[0]
        host._option(SuggestOption(9, kind, text, facts, Evidence.OK))
        app.processEvents()
        return {"legenda": caption, "opcao": counter.n}
    finally:
        app.removeEventFilter(counter)


__all__ = ["f520_shots", "f520_timings"]
