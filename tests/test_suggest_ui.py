"""F5.20 L2 (C11 a C13): as telas das sugestões ao vivo (pytest-qt, offscreen). O cartão: nasce escondido e aparece no pedido;
a opção com o selo, a fala inteira e os fatos como links (ou "sem evidência"); as avaliações só com o id gravado; "Boa" e
"Ruim" exclusivos, os motivos só com o "Ruim", o desfazer e o "Usei" à parte, com os argumentos certos; os chips e o "Limpar";
a altura de 120 px a 45 % e o recolher; nenhum botão padrão nem com foco. O popup: Enter, Esc e a exclusão da captura. A
bandeja: o cartão entre o chat e a linha, o botão "Sugerir" (à vista, apagado, a dica, o destaque) e a bandeja baixa que
cresce. O widget: o botão entre "Legendas" e "Parar" e o arquivo abaixo de 400 linhas. O "Antes de gravar": a caixa acesa
só com a legenda, a chave e a bandeja, e o objetivo. As Configurações: o grupo "Sugestões", o atalho e a página Prompts com
as duas tarefas e o memorando. O menu da bandeja do sistema com "Sugerir…" e o atalho."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from listener.llm.suggest_task import SuggestOption, SuggestResult
from listener.llm.suggest_task import Outcome
from listener.model import AppState, Evidence, SuggestMode

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

ROOT = Path(__file__).resolve().parent.parent
OPTION = SuggestOption(0, "Perguntar", "E o prazo da planilha, fica para sexta?", ((1, 1000), (4, 65_000)), Evidence.OK)
BARE = SuggestOption(1, "Pedir tempo", "Posso voltar nisso amanhã?", (), Evidence.NONE)


def _card(qtbot: Any) -> Any:
    from listener.ui.suggest_card import SuggestCard

    card = SuggestCard()
    qtbot.addWidget(card)
    card.resize(360, 400)
    return card


def _result(**kw: Any) -> SuggestResult:
    base: dict[str, Any] = {"outcome": Outcome.DONE, "meeting_id": 1, "cost_usd": 0.0004}
    base.update(kw)
    return SuggestResult(**base)


# ---------------------------------------------------------------- o cartão
def test_the_card_is_born_hidden_and_a_request_shows_the_options_as_they_come(qtbot):
    card = _card(qtbot)
    assert card.isHidden()
    card.begin(SuggestMode.SPEAK, "14:05", 65_000)
    assert not card.isHidden() and card.state.text() == "pedindo…" and not card.btn_cancel.isHidden() and not card.btn_new.isEnabled()
    assert card.title.toolTip() == "A sugestão mais recente" and "Sugestão · 14:05 · até [01:05]" in card.title.full_text
    row = card.add_option(OPTION)
    assert row.badge.text() == "Perguntar" and row.text.text() == OPTION.text
    assert 'href="fact:1"' in row.facts.text() and "[01:05]" in row.facts.text()
    assert card.add_option(BARE).facts.text() == "sem evidência"
    assert not row.good.isEnabled() and not row.used.isHidden()          # sem o id gravado, ainda não avalia
    card.finish(_result(cost_usd=0.0004), [11, 12], "Ruim: longa demais (2)")
    assert row.good.isEnabled() and card.rows[1].option_id == 12 and card.btn_cancel.isHidden() and card.btn_new.isEnabled()
    assert card.cost.text() == "US$ 0,0004" and "ajustada por: Ruim: longa demais (2)" in card.adjusted.full_text
    card.begin(SuggestMode.ORIENT, "14:06", None, no_news=65_000)
    assert card.rows == [] and "nada novo desde [01:05]" in card.state.text() and card.btn_orient.isChecked()
    assert card.add_option(OPTION).used.isHidden()                       # "Me situar": sem "Usei"


def test_a_new_request_hides_the_old_options_before_the_deferred_delete(qtbot):
    """L4 (a sonda dos tempos): a linha fora do layout seguia à vista, pintada por cima das novas, até a volta ao laço."""
    card = _card(qtbot)
    card.show()
    card.begin(SuggestMode.SPEAK, "14:05", 65_000)
    old = [card.add_option(OPTION), card.add_option(BARE)]
    assert all(row.isVisible() for row in old)
    card.begin(SuggestMode.SPEAK, "14:06", 70_000)                      # sem voltar ao laço: o deleteLater ainda não rodou
    assert card.rows == [] and not any(row.isVisible() for row in old)


def test_a_request_opens_a_collapsed_card_and_keeps_the_mode_it_asked_for(qtbot):
    """R-01 e R-08 da revisão do L2: o pedido pago não chega escondido; o seletor muda o próximo pedido, não o que chega."""
    card = _card(qtbot)
    card.begin(SuggestMode.SPEAK, "14:05", 65_000)
    card.set_collapsed(True)
    card.begin(SuggestMode.SPEAK, "14:06", 70_000)
    assert not card.collapsed and not card.body_area.isHidden()
    card.btn_orient.click()
    assert card.mode == SuggestMode.ORIENT and not card.add_option(OPTION).used.isHidden()
    card.begin(card.mode, "14:07", 70_000)
    assert card.add_option(OPTION).used.isHidden()                       # o pedido seguinte já é "Me situar"


def test_the_options_of_a_failed_or_cancelled_request_can_be_rated_and_the_budget_has_its_own_state(qtbot):
    """R-07 da revisão do L2 (o L1 grava as opções que chegaram) e R-04 da do L3 (o teto não é "falhou")."""
    card = _card(qtbot)
    card.begin(SuggestMode.SPEAK, "14:05", 65_000)
    row = card.add_option(OPTION)
    card.keep(_result(outcome=Outcome.ERROR, cost_usd=0.0003, cost_estimated=True), [21])
    card.fail("prazo de 30 s esgotado", retry=True)
    assert row.option_id == 21 and row.good.isEnabled() and card.cost.text() == "custo estimado US$ 0,0003"
    assert card.state.text() == "falhou: prazo de 30 s esgotado" and not card.btn_retry.isHidden()
    card.keep(_result(outcome=Outcome.ERROR, cost_usd=None), [])         # sem custo conhecido, o de antes fica
    assert card.cost.text() == "custo estimado US$ 0,0003"
    card.stopped("sugestões paradas nesta reunião: o custo da IA chegou à metade do alerta")
    assert card.state.text().startswith("sugestões paradas") and card.btn_retry.isHidden() and not card.btn_new.isEnabled()
    card.set_finished()
    card.fail("prazo de 30 s esgotado", retry=True)                      # o erro de um pedido que termina depois do Parar
    assert card.btn_retry.isHidden() and card.btn_new.isHidden()


def test_the_collapsed_card_fits_its_line_and_its_head(qtbot):
    """R-09 da revisão do L2: a conta à mão cortava 2 px do "Expandir"."""
    card = _card(qtbot)
    card.begin(SuggestMode.SPEAK, "14:05", 65_000)
    card.set_collapsed(True)
    assert card.preferred_height(600) >= card.layout().minimumSize().height() > card.btn_collapse.height()


def test_good_and_bad_are_exclusive_the_reasons_come_with_bad_and_used_is_apart(qtbot):
    card = _card(qtbot)
    card.begin(SuggestMode.SPEAK, "14:05", None)
    row = card.add_option(OPTION)
    card.finish(_result(), [7], "")
    rated: list[tuple[Any, ...]] = []
    used: list[tuple[Any, ...]] = []
    card.rated.connect(lambda *a: rated.append(a))
    card.used_toggled.connect(lambda *a: used.append(a))
    row.good.click()
    assert rated[-1] == (7, 1, None) and row.reasons.isHidden()
    row.bad.click()
    assert rated[-1] == (7, 0, None) and not row.good.isChecked() and not row.reasons.isHidden()
    row.reason_chips["longa demais"].click()
    assert rated[-1] == (7, 0, "longa demais")
    row.reason_chips["genérica"].click()
    assert rated[-1] == (7, 0, "genérica") and not row.reason_chips["longa demais"].isChecked()
    row.bad.click()                                                       # desfaz
    assert rated[-1] == (7, None, None) and row.reasons.isHidden()
    row.bad.click()
    assert not any(c.isChecked() for c in row.reason_chips.values())     # o motivo de antes não volta
    row.used.click()
    assert used == [(7, True)] and row.bad.isChecked()
    row.set_rating(1, None, False)
    assert row.good.isChecked() and not row.bad.isChecked() and not row.used.isChecked() and rated[-1] == (7, 0, None)


def test_the_chips_and_the_text_build_the_line_and_clear_empties_it(qtbot):
    card = _card(qtbot)
    toggled: list[tuple[str, bool]] = []
    card.nudge_chip_toggled.connect(lambda *a: toggled.append(a))
    asked: list[int] = []
    card.nudge_text_requested.connect(lambda: asked.append(1))
    cleared: list[int] = []
    card.nudge_cleared.connect(lambda: cleared.append(1))
    assert card.btn_clear.isHidden()
    card.chips["mais curta"].click()
    card.chips["em pergunta"].click()
    card.chips["mais curta"].click()
    assert toggled == [("mais curta", True), ("em pergunta", True), ("mais curta", False)]
    card.set_nudge("em pergunta; sem jargão")
    assert "Como sugerir agora: em pergunta; sem jargão" in card.nudge.full_text and not card.btn_clear.isHidden()
    assert "jargão" not in card.nudge.toolTip()                          # a dica fica fora da tela compartilhada (A-14)
    card.btn_text.click()
    card.btn_clear.click()
    assert asked == [1] and cleared == [1]
    card.set_chips(["com números"])
    assert card.chips["com números"].isChecked() and not card.chips["em pergunta"].isChecked()


def test_the_height_goes_from_120_px_to_45_percent_and_collapsing_keeps_the_head(qtbot):
    from listener.ui.suggest_card import CARD_MIN_H, CARD_SHARE

    card = _card(qtbot)
    card.begin(SuggestMode.SPEAK, "14:05", None)
    assert card.preferred_height(200) == CARD_MIN_H                      # 45 % de 200 é menos que o mínimo
    for i in range(8):
        card.add_option(SuggestOption(i, "Perguntar", "Uma fala longa " * 8, (), Evidence.NONE))
    assert card.preferred_height(600) == int(600 * CARD_SHARE)
    changed: list[bool] = []
    card.collapsed_changed.connect(changed.append)
    card.btn_collapse.click()
    assert changed == [True] and card.body_area.isHidden() and card.preferred_height(600) == card.head_height()
    card.btn_collapse.click()
    assert changed == [True, False] and not card.body_area.isHidden()


def test_no_card_button_is_default_or_takes_the_focus(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractButton, QPushButton

    card = _card(qtbot)
    card.begin(SuggestMode.SPEAK, "14:05", None)
    card.add_option(OPTION)
    buttons = card.findChildren(QAbstractButton)
    assert len(buttons) > 15
    assert all(not b.isDefault() and not b.autoDefault() for b in buttons if isinstance(b, QPushButton))
    assert all(b.focusPolicy() == Qt.FocusPolicy.NoFocus for b in buttons)


def test_finished_keeps_the_ratings_and_drops_the_requests(qtbot):
    card = _card(qtbot)
    card.begin(SuggestMode.SPEAK, "14:05", None)
    row = card.add_option(OPTION)
    card.finish(_result(), [3], "")
    card.set_finished()
    assert card.btn_new.isHidden() and card.btn_cancel.isHidden() and not card.chips["mais curta"].isEnabled()
    assert row.good.isEnabled() and "avaliações seguem" in card.state.text()
    card.fail("rede", retry=False)
    assert card.btn_retry.isHidden() and "falhou: rede" in card.state.text()


# ---------------------------------------------------------------- o popup do texto livre
def test_the_popup_saves_on_enter_cancels_on_esc_and_leaves_the_capture(qtbot, platform):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    from listener.ui.suggest_popup import MAX_CHARS, SuggestTextPopup

    popup = SuggestTextPopup("Objetivo", "fechar o corte", "meu objetivo", platform)
    qtbot.addWidget(popup)
    popup.show()
    qtbot.waitExposed(popup)
    assert int(popup.winId()) in platform.excluded and popup.edit.maxLength() == MAX_CHARS == 300
    popup.edit.setText("  aprovar a exceção  ")
    qtbot.keyClick(popup.edit, Qt.Key.Key_Return)
    assert popup.result() == QDialog.DialogCode.Accepted and popup.value() == "aprovar a exceção"
    again = SuggestTextPopup("Como sugerir", "", "como sugerir", platform)
    qtbot.addWidget(again)
    again.show()
    qtbot.keyClick(again.edit, Qt.Key.Key_Escape)
    assert again.result() == QDialog.DialogCode.Rejected


# ---------------------------------------------------------------- a bandeja e o widget
def _tray(qtbot: Any, store: Any, platform: Any) -> Any:
    from listener.ui.caption_tray import CaptionTray

    mid = store.create_meeting("Orçamento", None, None, None)
    tray = CaptionTray(store, mid, platform=platform)
    qtbot.addWidget(tray)
    tray.restore_geometry()
    tray.show()
    return tray


def test_the_host_popups_fit_their_whole_hint(qtbot, qapp):
    """R-03 da revisão do L4: com a fonte do tema (14 px), a dica do "Objetivo" pedia 345 px num campo de 318 e saía cortada
    em "…Esc c…"; o campo cresce até caber a dica, em qualquer fonte."""
    from PySide6.QtGui import QFont

    from listener.ui.suggest_host import GOAL_ASK, NUDGE_ASK
    from listener.ui.suggest_popup import FIELD_PAD, MIN_FIELD_W, SuggestTextPopup

    before = qapp.font()
    themed = QFont(before)
    themed.setPixelSize(14)
    qapp.setFont(themed)
    try:
        needs = []
        for title, hint in (NUDGE_ASK, GOAL_ASK):
            popup = SuggestTextPopup(title, "", hint)
            qtbot.addWidget(popup)
            needs.append(popup.edit.fontMetrics().horizontalAdvance(hint) + FIELD_PAD)
            assert popup.edit.minimumWidth() == max(MIN_FIELD_W, needs[-1]), title
        assert max(needs) > MIN_FIELD_W                                   # a do objetivo não caberia nos 320 px
    finally:
        qapp.setFont(before)


def test_the_tray_holds_the_card_between_the_chat_and_the_line_and_the_suggest_button(qtbot, store, platform):
    tray = _tray(qtbot, store, platform)
    card = _card(qtbot)
    tray.set_card(card)
    assert tray.lay.indexOf(tray.chat) == 1 and tray.lay.indexOf(card) == 2 and tray.lay.indexOf(tray.line) == 3 and card.isHidden()
    assert tray.btn_suggest.isHidden()
    tray.set_suggest_state(True, False, "A sugestão lê a legenda ao vivo.", False)
    assert not tray.btn_suggest.isHidden() and not tray.btn_suggest.isEnabled() and "legenda" in tray.btn_suggest.toolTip()
    tray.set_suggest_state(True, True, "Sugerir o que falar agora", True)
    assert tray.btn_suggest.property("theme_icon") == "lightbulb:accent:20" and tray.btn_suggest.isEnabled()
    pressed: list[int] = []
    tray.suggest_requested.connect(lambda: pressed.append(1))
    tray.btn_suggest.click()
    assert pressed == [1]
    card.begin(SuggestMode.SPEAK, "14:05", None)
    tray.show_card()
    assert not card.isHidden() and card.height() == card.preferred_height(tray.height())
    tray.set_collapsed(True)
    assert card.isHidden()
    tray.set_collapsed(False)
    assert not card.isHidden()
    tray.set_card(None)
    assert tray.lay.indexOf(card) == -1 and tray.card is None


def test_a_low_tray_grows_up_for_the_card_without_saving_that_height(qtbot, store, platform):
    from listener.ui.caption_geometry import MIN_H
    from listener.ui.suggest_card import CARD_MIN_H

    tray = _tray(qtbot, store, platform)
    tray.dock = None
    tray.full_height = MIN_H
    tray.setGeometry(100, 400, 360, MIN_H)
    bottom = tray.y() + tray.height()
    card = _card(qtbot)
    tray.set_card(card)
    tray.show_card()                                                      # como o host: a bandeja antes do `begin`
    card.begin(SuggestMode.SPEAK, "14:05", None)
    assert tray.height() == MIN_H + CARD_MIN_H and tray.y() + tray.height() == bottom and tray.full_height == MIN_H
    tray.save_geometry()
    assert store.get_setting("ui.caption_tray_geometry") == f"100,400,360,{MIN_H}"      # pela borda de baixo (R-03 do L2)
    tray.hide_card()
    assert tray.height() == MIN_H and tray.card_extra == 0


def test_the_title_leaves_earlier_with_the_suggest_button(qtbot, store, platform):
    """R-02 da revisão do L2: com o "Sugerir", o cabeçalho tem um botão a mais, e o selo saía cortado."""
    from listener.ui.caption_tray import BUTTON_PX, SPACING, TITLE_MIN_W

    tray = _tray(qtbot, store, platform)
    tray.set_live_state("degraded")
    tray.resize(TITLE_MIN_W + 10, 400)
    assert tray.title.isVisibleTo(tray)
    tray.set_suggest_state(True, True, "Sugerir o que falar agora", False)
    assert not tray.title.isVisibleTo(tray)
    tray.resize(TITLE_MIN_W + BUTTON_PX + SPACING, 400)
    assert tray.title.isVisibleTo(tray)
    tray.set_suggest_state(False, False, "", False)
    tray.resize(TITLE_MIN_W + 10, 400)
    assert tray.title.isVisibleTo(tray)


def test_dragging_the_top_edge_with_the_card_keeps_the_bottom(qtbot, qapp, store, platform):
    """R-04 da revisão do L2: o mínimo do arrasto é o do layout com o cartão, e o Qt não cresce a bandeja para baixo."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    def mouse(kind: QEvent.Type, local: tuple[int, int], glob: tuple[int, int]) -> QMouseEvent:
        return QMouseEvent(kind, QPointF(*local), QPointF(*glob), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)

    tray = _tray(qtbot, store, platform)
    tray.dock = None
    tray.full_height = 400
    tray.setGeometry(100, 300, 360, 400)
    card = _card(qtbot)
    tray.set_card(card)
    tray.show_card()
    card.begin(SuggestMode.SPEAK, "14:05", None)
    card.add_option(OPTION)
    qapp.processEvents()
    bottom = tray.y() + tray.height()
    tray.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, (180, 2), (280, 302)))
    tray.mouseMoveEvent(mouse(QEvent.Type.MouseMove, (180, 302), (280, 602)))
    qapp.processEvents()
    assert tray.y() + tray.height() == bottom and tray.height() < 400


def test_the_widget_has_the_suggest_button_between_captions_and_stop_and_stays_under_400_lines(qtbot):
    from listener.ui.widget import RecordingWidget

    w = RecordingWidget(captions_button=True, shortcuts={"suggest": "ctrl+alt+shift+d"})
    qtbot.addWidget(w)
    w.show()
    assert w.btn_suggest.isHidden() and "Ctrl+Alt+Shift+D" in w.btn_suggest.toolTip()
    w.set_suggest_state(True, True, "Sugerir o que falar agora (Ctrl+Alt+Shift+D)", False)
    assert w.btn_captions.x() < w.btn_suggest.x() < w.btn_stop.x()
    pressed: list[int] = []
    w.suggest_clicked.connect(lambda: pressed.append(1))
    w.btn_suggest.click()
    w.update_state(1.0, {}, {}, paused=True, muted=False, markers=0)
    w.set_suggest_state(True, False, "motivo", False)
    w.update_state(2.0, {}, {}, paused=False, muted=False, markers=0)      # o estado da gravação não o religa
    assert pressed == [1] and not w.btn_suggest.isEnabled()
    for name in ("widget.py", "suggest_card.py", "suggest_option.py", "suggest_host.py", "caption_tray.py"):   # R-12 do L2
        assert len((ROOT / "listener" / "ui" / name).read_text(encoding="utf-8").splitlines()) < 400, name


# ---------------------------------------------------------------- o "Antes de gravar"
def test_the_warmup_box_lights_only_with_captions_key_and_tray_and_carries_the_goal(qtbot, store, monkeypatch):
    from listener.ui.llm_ask import NO_KEY_TIP
    from listener.ui.warmup_live import SUGGEST_NEED_SETTING, SUGGEST_NEED_TRAY, SUGGEST_OFF_TIP, SUGGEST_TIP, LiveBoxes
    from tests.test_live_partial import _ready

    monkeypatch.setenv("LISTENER_LIVE", "fake")
    boxes = LiveBoxes(store)
    boxes.set_values(True, False)
    assert not boxes.suggest_check.isEnabled() and boxes.suggest_check.toolTip() == NO_KEY_TIP
    _ready(store)
    boxes.refresh_key()
    assert boxes.suggest_check.isEnabled() and boxes.suggest_check.isChecked() and boxes.suggest_check.toolTip() == SUGGEST_TIP
    boxes.goal_edit.setText("  fechar o corte  ")
    assert boxes.fields()["suggest"] is True and boxes.fields()["goal"] == "fechar o corte"
    boxes.suggest_check.setChecked(False)
    boxes.live_check.setChecked(False)
    assert not boxes.suggest_check.isEnabled() and boxes.suggest_check.toolTip() == SUGGEST_OFF_TIP and boxes.fields()["suggest"] is False
    boxes.live_check.setChecked(True)
    assert not boxes.suggest_check.isChecked()                            # o "não" de quem desmarcou fica
    store.set_setting("live.caption_tray", False)
    boxes.refresh_key()
    assert not boxes.suggest_check.isEnabled() and boxes.suggest_check.toolTip() == SUGGEST_NEED_TRAY
    boxes.goal_edit.setText("   ")
    assert boxes.fields()["goal"] is None
    store.set_setting("live.caption_tray", True)
    store.set_setting("suggest.enabled", False)                           # R-05 do L2: desligada, marcar não faria nada
    boxes.refresh_key()
    assert not boxes.suggest_check.isEnabled() and boxes.suggest_check.toolTip() == SUGGEST_NEED_SETTING
    assert boxes.fields()["suggest"] is False and not LiveBoxes(store).suggest_check.isChecked()
    store.set_setting("suggest.enabled", True)                            # religada com o diálogo aberto: o padrão dela
    boxes.refresh_key()
    assert boxes.suggest_check.isEnabled() and boxes.suggest_check.isChecked()


# ---------------------------------------------------------------- as Configurações e o menu da bandeja
def test_the_live_page_has_the_suggestions_group_and_the_hotkeys_page_the_shortcut(qtbot, store, platform):
    from listener.config.defaults import SUGGEST_KEYS
    from tests.test_settings_pages import _dialog

    dlg = _dialog(qtbot, store, platform)
    assert set(SUGGEST_KEYS) <= set(dlg._editors) and "hotkeys.suggest" in dlg._editors
    assert SUGGEST_KEYS == ("suggest.enabled", "suggest.learn", "suggest.options", "suggest.window_min", "suggest.dossier",
                            "suggest.name_hint", "suggest.screen", "suggest.screen_source", "suggest.screen_keep")   # F5.21
    assert dlg._editors["hotkeys.suggest"].raw() == "ctrl+alt+shift+d"


def test_the_prompts_page_has_the_two_tasks_and_the_memo_box(qtbot, store, platform):
    from listener.llm.types import LlmTask
    from tests.test_settings_pages import _dialog

    store.set_setting("suggest.memo", "Prefere perguntas curtas.")
    store.set_setting("suggest.memo_at", "2026-11-08T14:05:00-03:00")
    store.set_setting("suggest.memo_ratings", "12")
    dlg = _dialog(qtbot, store, platform)
    dlg.prompt_page_shown(dlg.prompt_page)
    pane = dlg.select_prompt(LlmTask.SUGGEST_MEMO)
    memo = pane.memo
    assert dlg.select_prompt(LlmTask.SUGGEST).memo is None and memo is not None
    assert memo.edit.toPlainText() == "Prefere perguntas curtas." and "08/11/2026 às 14:05 com 12 avaliações" in memo.info.text()
    memo.edit.setPlainText("x" * 601)
    assert dlg.prompt_problem()[0].endswith("o memorando tem 601 caracteres; o limite é 600.")
    memo.clear.click()
    assert memo.edit.toPlainText() == "" and dlg.prompt_problem() is None
    memo.edit.setPlainText("Prefere opções em pergunta.")
    dlg.save_prompts()
    assert store.get_setting("suggest.memo") == "Prefere opções em pergunta."


def test_the_system_tray_menu_has_suggest_with_its_shortcut(qtbot, qapp, platform, store, monkeypatch):
    from listener.ui import controller as ctl
    from listener.ui.tray import TrayIcon, menu_shortcuts

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    tray = TrayIcon(c)
    names = [a.text() for a in tray.menu.actions()]
    assert names.index("Sugerir…") == names.index("Ao vivo…") + 1 and not tray.act_suggest.isEnabled()
    notices: list[str] = []
    monkeypatch.setattr(c, "notify", notices.append)
    c.request_suggestion()                                                # fora da gravação: o aviso, sem bandeja
    assert notices and "durante a gravação" in notices[0]
    tray.set_state(AppState.RECORDING)
    assert tray.act_suggest.isEnabled()
    tray.menu.aboutToShow.emit()
    assert tray.act_suggest.isVisible()
    tray.act_suggest.trigger()                                            # o item pede pelo controlador (R-11 do L2)
    assert len(notices) == 2
    store.set_setting("suggest.enabled", False)                           # R-05 do L2: desligadas, sem o item e o atalho mudo
    tray.menu.aboutToShow.emit()
    c.request_suggestion()
    assert not tray.act_suggest.isVisible() and len(notices) == 2
    store.set_setting("suggest.enabled", True)
    tray.menu.aboutToShow.emit()
    assert tray.act_suggest.isVisible()
    assert menu_shortcuts(store.get_setting, {})["suggest"] == "ctrl+alt+shift+d"
    tray.hide()
    tray.deleteLater()
