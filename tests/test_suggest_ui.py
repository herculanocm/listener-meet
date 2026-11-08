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
    assert store.get_setting("ui.caption_tray_geometry").endswith(f",{MIN_H}")
    tray.hide_card()
    assert tray.height() == MIN_H and tray.card_extra == 0


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
    assert len((ROOT / "listener" / "ui" / "widget.py").read_text(encoding="utf-8").splitlines()) < 400


# ---------------------------------------------------------------- o "Antes de gravar"
def test_the_warmup_box_lights_only_with_captions_key_and_tray_and_carries_the_goal(qtbot, store, monkeypatch):
    from listener.ui.llm_ask import NO_KEY_TIP
    from listener.ui.warmup_live import SUGGEST_NEED_TRAY, SUGGEST_OFF_TIP, SUGGEST_TIP, LiveBoxes
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


# ---------------------------------------------------------------- as Configurações e o menu da bandeja
def test_the_live_page_has_the_suggestions_group_and_the_hotkeys_page_the_shortcut(qtbot, store, platform):
    from listener.config.defaults import SUGGEST_KEYS
    from tests.test_settings_pages import _dialog

    dlg = _dialog(qtbot, store, platform)
    assert set(SUGGEST_KEYS) <= set(dlg._editors) and "hotkeys.suggest" in dlg._editors
    assert SUGGEST_KEYS == ("suggest.enabled", "suggest.learn", "suggest.options", "suggest.window_min", "suggest.dossier",
                            "suggest.name_hint")
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
    assert menu_shortcuts(store.get_setting, {})["suggest"] == "ctrl+alt+shift+d"
    tray.hide()
    tray.deleteLater()
