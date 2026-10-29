"""F5.7 (L3, U-094): as Configurações do aviso de reunião sem gravação. Sem Qt (`call_config`): as chaves e os `Params`, as
linhas de "Apps observados" (o Teams numa linha só, a dos navegadores, os acrescentados, os desligados pela bandeja) e o que
"Não avisar para o X" desliga. Com Qt (o cartão "Reunião sem gravação" de "Idioma e avisos"): a ordem das linhas, o resto do
cartão apagado com o aviso desligado, os segundos na faixa (U-073, R-04 e o erro na página do U-072), acrescentar, remover e
desmarcar, o texto salvo que volta igual sem mexer (D-059) e o app desligado pela bandeja que aparece desmarcado."""
from __future__ import annotations

import logging
from typing import Any

import pytest

from listener.config.defaults import SPECS, validate_setting
from listener.platform.base import KIND_BROWSER, KIND_MEETING, PlayingApp, WatchedApp
from listener.ui import call_config as cfg
from tests.fake_activity import FAKE_WATCHED

WATCHED = (WatchedApp("ms-teams.exe", "Teams", KIND_MEETING), WatchedApp("teams.exe", "Teams", KIND_MEETING),
           WatchedApp("zoom.exe", "Zoom", KIND_MEETING), WatchedApp("webex.exe", "Webex", KIND_MEETING),
           WatchedApp("slack.exe", "Slack", KIND_MEETING), WatchedApp("msedge.exe", "Edge", KIND_BROWSER),
           WatchedApp("chrome.exe", "Chrome", KIND_BROWSER))
DEFAULTS = tuple(SPECS[k].default_text for k in cfg.KEYS)


def _rows(apps: str = "", off: str = "") -> list[tuple[str, bool, bool]]:
    keys = cfg.exe_set(off)
    return [(r.label, r.checked(keys), r.added) for r in cfg.app_rows(WATCHED, apps, off)]


# ---------------------------------------------------------------- sem Qt
def test_the_defaults_and_the_params_of_the_rules():
    values = cfg.values_from(DEFAULTS)
    assert values == {"detect.enabled": True, "detect.seconds": 8, "detect.remind": True, "detect.apps": "", "detect.off": "",
                      "detect.other_apps": True}
    p = cfg.params_from({**values, "detect.apps": "Discord.exe", "detect.off": "Zoom.exe, WhatsApp.Root.exe"}, WATCHED)
    assert p.enabled and p.seconds == 8.0 and p.remind and p.other_apps and p.browsers
    assert p.apps == {"ms-teams.exe", "teams.exe", "zoom.exe", "webex.exe", "slack.exe", "discord.exe"}
    assert p.off == {"zoom.exe", "whatsapp.root.exe"}
    assert validate_setting("detect.seconds", "2") and validate_setting("detect.seconds", "121")
    assert validate_setting("detect.seconds", "3") is None


def test_an_invalid_saved_value_falls_back_to_the_default_with_one_warning(caplog):
    raw = list(DEFAULTS)
    raw[cfg.KEYS.index(cfg.SECONDS)] = "200"
    with caplog.at_level(logging.WARNING, logger="listener.ui.call_config"):
        values = cfg.values_from(raw)
    assert values[cfg.SECONDS] == 8 and "detect.seconds inválida" in caplog.text and "200" not in caplog.text


def test_the_rows_of_the_watched_apps():
    assert _rows() == [("Teams", True, False), ("Zoom", True, False), ("Reunião no navegador", True, False),
                       ("Webex", True, False), ("Slack", True, False)]
    rows = _rows("Discord.exe,discord.exe", "teams.exe,WhatsApp.Root.exe,Discord.exe")
    assert rows[0] == ("Teams", False, False)                                       # um executável do Teams desliga a linha
    assert rows[5:] == [("Discord", False, True), ("WhatsApp", False, False)]       # acrescentado e desligado pela bandeja (R-12)
    assert ("Chrome", False, False) in _rows("", "chrome.exe") and ("Reunião no navegador", True, False) in _rows("", "chrome.exe")
    both = _rows("", "chrome.exe,msedge.exe")
    assert ("Reunião no navegador", False, False) in both and not any(r[0] in ("Chrome", "Edge") for r in both)


def test_the_lists_come_back_from_the_screen_and_the_tray_switches_a_whole_app_off():
    rows = cfg.app_rows(WATCHED, "Discord.exe", "")
    on = {r.label: True for r in rows} | {"Teams": False, "Reunião no navegador": False}
    assert cfg.lists_from((r, on[r.label]) for r in rows) == ("Discord.exe", "chrome.exe,ms-teams.exe,msedge.exe,teams.exe")
    assert cfg.silence_names(WATCHED, "ms-teams.exe") == ("ms-teams.exe", "teams.exe")   # o Teams inteiro
    assert cfg.silence_names(WATCHED, "chrome.exe") == ("chrome.exe",)                     # só o navegador
    assert cfg.silence_names(WATCHED, "WhatsApp.Root.exe") == ("WhatsApp.Root.exe",)
    assert cfg.join(["b.exe", "A.exe", "a.exe", " "]) == "A.exe,b.exe" and cfg.names(" x.exe ,X.EXE,, y ") == ("x.exe", "y")


# ---------------------------------------------------------------- o cartão das Configurações
def _dialog(qtbot: Any, store: Any, platform: Any) -> Any:
    from listener.ui.settings_min import SettingsDialog

    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    return dlg


@pytest.mark.lento
def test_the_card_lives_in_language_and_notices_and_dims_when_off(qtbot, store, platform):
    from listener.ui.settings_detect import DETECT_TITLE, HELP

    dlg = _dialog(qtbot, store, platform)
    page = dlg.page("Idioma e avisos")
    card = page.cards[DETECT_TITLE]
    assert page.isAncestorOf(card) and page.isAncestorOf(dlg.detect_apps) and "aviso de reunião" in page.subtitle.text()
    assert any(label.text() == HELP for label in card.findChildren(type(page.subtitle)))
    assert list(dlg.detect_apps.boxes()) == ["Teams", "Zoom", "Reunião no navegador", "Slack"]   # a lista falsa
    assert all(box.isChecked() for box in dlg.detect_apps.boxes().values()) and dlg.detect_apps.added() == []
    enabled = dlg.editor(cfg.ENABLED)
    assert enabled.box.text() == "Avisar quando um app de reunião tocar áudio sem gravação" and enabled.box.isChecked()
    assert dlg.detect_body.isEnabled()
    enabled.box.setChecked(False)
    assert not dlg.detect_body.isEnabled() and not dlg.detect_apps.isEnabled() and not dlg.editor(cfg.SECONDS).widget.isEnabled()
    enabled.box.setChecked(True)
    assert dlg.editor(cfg.SECONDS).widget.isEnabled()
    dlg._save()
    assert store.get_setting(cfg.ENABLED) == "True" and dlg.result() == dlg.DialogCode.Accepted


@pytest.mark.lento
def test_the_seconds_stay_in_range_and_an_out_of_range_value_is_shown_like_the_other_pages(qtbot, store, platform):
    from PySide6.QtWidgets import QLabel

    from listener.ui.settings_detect import SECONDS_TAIL
    from listener.ui.settings_editors import IntEditor

    store.set_setting(cfg.SECONDS, "200")                                            # só um banco editado à mão
    dlg = _dialog(qtbot, store, platform)
    seconds = dlg.editor(cfg.SECONDS)
    assert isinstance(seconds, IntEditor) and (seconds.spin.minimum(), seconds.spin.maximum()) == (3, 120)   # U-073
    holder = seconds.widget.parentWidget()
    assert any(label.text() == SECONDS_TAIL for label in holder.findChildren(QLabel))      # "[8] s com o microfone em uso"
    page = dlg.page("Idioma e avisos")
    assert seconds.spin.value() == 8 and page.warning is not None and "Avisar depois de" in page.warning.label.text()   # R-04
    message = validate_setting(cfg.SECONDS, "121")
    dlg.nav.setCurrentRow(0)
    dlg.show_problem(message, seconds.widget)                                        # U-072: a página abre e o campo fica marcado
    assert dlg.stack.currentWidget() is page and seconds.widget.property("invalid") and "acima do máximo 120" in dlg.error_bar.label.text()
    seconds.spin.setValue(20)
    assert seconds.widget.property("invalid") is None
    dlg._save()
    assert store.get_setting(cfg.SECONDS) == "20"


@pytest.mark.lento
def test_add_remove_and_uncheck_apps_and_untouched_lists_keep_the_saved_text(qtbot, store, platform, monkeypatch):
    from listener.ui import settings_detect

    store.set_setting(cfg.APPS, " Discord.exe ")
    store.set_setting(cfg.OFF, "WhatsApp.Root.exe")                                  # desligado pela bandeja
    dlg = _dialog(qtbot, store, platform)
    apps = dlg.detect_apps
    boxes = apps.boxes()
    assert apps.added() == ["Discord"] and not boxes["WhatsApp"].isChecked() and boxes["Discord"].isChecked()
    assert dlg.editor(cfg.APPS).raw() == " Discord.exe " and dlg.editor(cfg.OFF).raw() == "WhatsApp.Root.exe"   # sem mexer
    tasks: list[Any] = []
    monkeypatch.setattr(settings_detect, "run_task", lambda fn, _store, done: tasks.append((fn, done)))
    apps.add_button.click()
    assert not apps.add_button.isEnabled() and tasks[0][0] == platform.list_playing_apps   # fora da thread da interface
    tasks[0][1]([PlayingApp(1, "Discord.exe", "chat"), PlayingApp(2, "Signal.exe", "Fulana"), PlayingApp(3, "ms-teams.exe")])
    menu = apps.menu
    texts = [a.text() for a in menu.actions()]
    assert apps.add_button.isEnabled() and texts == ["Signal (Signal.exe)"]          # sem título de janela, sem repetir
    menu.actions()[0].trigger()
    assert apps.added() == ["Discord", "Signal"] and apps.boxes()["Signal"].isChecked()
    assert not apps.add_app("signal.exe")                                            # já está na lista
    tasks[0][1]([])
    assert [a.text() for a in apps.menu.actions()] == [settings_detect.NOTHING_NEW] and not apps.menu.actions()[0].isEnabled()
    remover = next(b for b in apps.findChildren(type(apps.add_button)) if b.accessibleName() == "Remover Discord")
    remover.click()
    assert apps.added() == ["Signal"] and "Discord" not in apps.boxes()
    apps.boxes()["Zoom"].setChecked(False)
    apps.boxes()["WhatsApp"].setChecked(True)                                        # religado
    assert not apps.remove("Zoom")                                                   # o de fábrica não sai
    dlg._save()
    assert store.get_setting(cfg.APPS) == "Signal.exe" and store.get_setting(cfg.OFF) == "zoom.exe"
    again = _dialog(qtbot, store, platform)
    assert again.detect_apps.added() == ["Signal"] and not again.detect_apps.boxes()["Zoom"].isChecked()
    assert "WhatsApp" not in again.detect_apps.boxes()                               # religado: volta a ser um "outro app"


@pytest.mark.lento
def test_a_saved_change_is_read_right_away_by_the_call_watch(qtbot, store, platform, monkeypatch):
    """CP8: a cola relê quando um diálogo modal fecha; o app desmarcado nas Configurações deixa de avisar já."""
    from listener.ui import tasks
    from listener.ui.call_rules import State
    from listener.ui.call_sensor import CallSensor
    from listener.ui.call_watch import CallWatch
    from tests.fake_calls import act, teams
    from tests.fake_watch import FakeHost

    monkeypatch.setattr(CallSensor, "start", lambda _self: None)
    modal = [False]
    monkeypatch.setattr(CallWatch, "_modal_now", staticmethod(lambda: modal[0]))
    calls = CallWatch(FakeHost(), platform, store)
    calls.start()
    try:
        modal[0] = True
        calls.tick()
        dlg = _dialog(qtbot, store, platform)
        dlg.detect_apps.boxes()["Teams"].setChecked(False)
        dlg._save()
        modal[0] = False
        calls.tick()
        assert calls.params.off == {"ms-teams.exe"}
        now = calls._clock()
        for t in (0, 9, 18):
            calls.rules.step(act(teams()), now + t, False, False, calls.params)
        assert calls.rules.state("ms-teams.exe") == State.QUIET
    finally:
        tasks.live_threads.discard(calls.sensor)
        calls.shutdown()


@pytest.mark.lento
def test_tab_follows_the_screen_order_also_after_adding_an_app(qtbot, store, platform):
    """R-03: o Tab no cartão segue a ordem da tela: Lembrar, as linhas (com o "Remover" de cada acrescentado), "Adicionar…" e
    "Também outros apps"; o app acrescentado entra antes do "Adicionar…"."""
    from PySide6.QtCore import Qt

    store.set_setting(cfg.APPS, "Discord.exe")
    dlg = _dialog(qtbot, store, platform)
    apps = dlg.detect_apps

    def chain() -> list[str]:
        """De "Lembrar…" a "Também outros apps…", pela cadeia do foco (o Tab), só o que está à vista e pega o Tab."""
        w, last, out = dlg.editor(cfg.REMIND).box, dlg.editor(cfg.OTHER_APPS).box, []
        for _ in range(500):
            if w.focusPolicy() & Qt.FocusPolicy.TabFocus and dlg.detect_body.isAncestorOf(w) and w.isVisibleTo(dlg.detect_body):
                out.append(w.accessibleName() or w.text())
            if w is last:
                break
            w = w.nextInFocusChain()
        return out

    remind, other = dlg.editor(cfg.REMIND).box.text(), dlg.editor(cfg.OTHER_APPS).box.text()
    rows = ["Teams", "Zoom", "Reunião no navegador", "Slack", "Discord", "Remover Discord"]
    assert chain() == [remind, *rows, apps.add_button.text(), other]
    assert apps.add_app("Signal.exe")
    assert chain() == [remind, *rows, "Signal", "Remover Signal", apps.add_button.text(), other]
    assert apps.add_app("Telegram.exe") and chain()[-4:] == ["Telegram", "Remover Telegram", apps.add_button.text(), other]


def test_the_fake_list_matches_the_fake_platform(platform):
    assert platform.watched_apps() == FAKE_WATCHED
