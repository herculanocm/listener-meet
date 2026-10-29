"""F5.7 (L2), a matriz do Aceite 1, parte 1: quando cada regra avisa (`listener/ui/call_rules.py`, sem Qt). Relógio
manual, uma leitura por segundo; o sensor é de mentira."""
from __future__ import annotations

import pytest

from listener.platform.base import KIND_BROWSER, KIND_MEETING, AppAudio, WatchedApp
from listener.ui.call_rules import CallRules, Params, Prompt, default_apps, rule_for
from tests.fake_activity import FAKE_WATCHED
from tests.fake_calls import Calls, act, chrome, discord, teams, whatsapp, zoom


def test_teams_with_the_mic_warns_at_8_s_and_not_at_7():
    calls = Calls()
    calls.run(0, 7, act(teams()))
    assert calls.prompts() == []
    calls.at(8, act(teams()))
    assert calls.prompts() == [(8, Prompt("ms-teams.exe", "Teams", "R1", 8, False))]


def test_one_lost_reading_is_tolerated_and_two_reset_the_count():
    calls = Calls()
    calls.run(0, 3, act(teams()))
    calls.at(4, None)                                                       # o sensor não leu
    calls.run(5, 8, act(teams()))
    assert [t for t, _p in calls.prompts()] == [8]
    calls = Calls()
    calls.run(0, 3, act(teams()))
    calls.run(4, 5, act())                                                  # duas leituras sem o microfone
    calls.run(6, 13, act(teams()))
    assert calls.prompts() == []
    calls.at(14, act(teams()))
    assert calls.prompts() == [(14, Prompt("ms-teams.exe", "Teams", "R1", 8, False))]


def test_a_browser_with_only_the_mic_does_not_warn():
    calls = Calls()
    calls.run(0, 60, act(chrome(mic=True)))
    assert calls.prompts() == []


def test_a_browser_with_the_mic_and_sound_or_a_meeting_title_warns():
    calls = Calls()
    calls.run(0, 8, act(chrome(mic=True, peak=0.3)))
    assert calls.prompts() == [(8, Prompt("chrome.exe", "Chrome", "R2", 8, False))]
    calls = Calls()
    calls.run(0, 8, act(chrome(mic=True, service="Google Meet")))
    assert calls.prompts() == [(8, Prompt("chrome.exe", "Google Meet no Chrome", "R2", 8, False))]
    calls = Calls()
    calls.run(0, 8, act(AppAudio(210, "msedge.exe", True, 0.0, "Google Meet")))
    assert [p.label for _t, p in calls.prompts()] == ["Google Meet no Edge"]


def test_youtube_with_only_sound_does_not_warn():
    calls = Calls()
    calls.run(0, 120, act(chrome(peak=0.5)))
    assert calls.prompts() == []


def test_sound_counts_for_10_s_after_the_last_reading_with_it():
    """Um navegador com microfone e uma frase há 9 s segue "com som" (avisa aos 9 s); há 10 s, não (aos 10 s a frase saiu da
    janela, e duas leituras sem a regra zeram); um pico abaixo de 0,01 não é som."""
    calls = Calls(Params(seconds=9))
    calls.at(0, act(chrome(mic=True, peak=0.3)))
    calls.run(1, 9, act(chrome(mic=True)))
    assert [t for t, _p in calls.prompts()] == [9]
    calls = Calls(Params(seconds=10))
    calls.at(0, act(chrome(mic=True, peak=0.3)))
    calls.run(1, 30, act(chrome(mic=True)))
    assert calls.prompts() == []
    calls = Calls()
    calls.run(0, 30, act(chrome(mic=True, peak=0.005)))
    assert calls.prompts() == []


def test_a_teams_notification_of_5_s_without_the_mic_does_not_warn():
    calls = Calls()
    calls.run(0, 4, act(teams(mic=False, peak=0.2)))
    calls.run(5, 120, act(teams(mic=False)))
    assert calls.prompts() == []


def test_whatsapp_with_the_mic_and_sound_warns_at_20_s_unless_other_apps_is_off():
    calls = Calls()
    calls.run(0, 19, act(whatsapp()))
    assert calls.prompts() == []
    calls.at(20, act(whatsapp()))
    assert calls.prompts() == [(20, Prompt("whatsapp.root.exe", "WhatsApp", "R3", 20, False))]   # F5.7 L3 (R-12): o nome amigável
    calls = Calls(Params(other_apps=False))
    calls.run(0, 60, act(whatsapp()))
    assert calls.prompts() == []
    calls = Calls()
    calls.run(0, 60, act(whatsapp(peak=0.0)))                               # só o microfone: não
    assert calls.prompts() == []


def test_a_webinar_warns_at_45_s_and_not_at_30():
    calls = Calls()
    calls.run(0, 44, act(teams(mic=False, peak=0.3)))
    assert calls.prompts() == []
    calls.at(45, act(teams(mic=False, peak=0.3)))
    assert calls.prompts() == [(45, Prompt("ms-teams.exe", "Teams", "R4", 45, False))]
    calls = Calls()
    calls.run(0, 45, act(chrome(peak=0.3, service="Google Meet")))
    assert calls.prompts() == [(45, Prompt("chrome.exe", "Google Meet no Chrome", "R4", 45, False))]


def test_the_rule_changing_restarts_the_count():
    """A webinar de 30 s e então o microfone (a pessoa entrou na conversa): a R1 conta do começo."""
    calls = Calls()
    calls.run(0, 30, act(teams(mic=False, peak=0.3)))
    calls.run(31, 38, act(teams()))
    assert calls.prompts() == []
    calls.at(39, act(teams()))
    assert calls.prompts() == [(39, Prompt("ms-teams.exe", "Teams", "R1", 8, False))]


def test_degraded_only_the_meeting_app_with_the_mic_warns():
    calls = Calls()
    reading = act(AppAudio(0, "ms-teams.exe", True, 0.0), AppAudio(0, "chrome.exe", True, 0.0),
                  AppAudio(0, "whatsapp.root.exe", True, 0.0), degraded=True)
    calls.run(0, 60, reading)
    assert [(t, p.exe, p.rule) for t, p in calls.prompts()] == [(8, "ms-teams.exe", "R1")]
    calls = Calls()
    calls.run(0, 60, act(teams(mic=False, peak=0.5), degraded=True))       # sem a R4
    assert calls.prompts() == []


def test_discord_is_another_app_and_warns_by_r3_at_20_s():
    """R-01 (D-065, pergunta 3): fora da lista de fábrica, o Discord é "outro app" (microfone e som por 20 s); quem quiser a
    R1 o acrescenta à mão."""
    assert default_apps(FAKE_WATCHED) == {"ms-teams.exe", "zoom.exe", "slack.exe"}
    calls = Calls()
    calls.run(0, 19, act(discord()))
    assert calls.prompts() == []
    calls.at(20, act(discord()))
    assert calls.prompts() == [(20, Prompt("discord.exe", "Discord", "R3", 20, False))]
    calls = Calls(Params(apps=default_apps(FAKE_WATCHED) | {"discord.exe"}))
    calls.run(0, 8, act(discord()))
    assert calls.prompts() == [(8, Prompt("discord.exe", "Discord", "R1", 8, False))]


def test_a_listed_app_turned_off_never_warns():
    calls = Calls(Params(apps=frozenset({"zoom.exe"})))
    calls.run(0, 60, act(teams(peak=0.3)))
    assert calls.prompts() == []


def test_an_app_added_by_the_owner_is_a_meeting_app():
    calls = Calls(Params(apps=default_apps(FAKE_WATCHED) | {"meetingtool.exe"}))
    calls.run(0, 8, act(AppAudio(500, "MeetingTool.exe", True, 0.0)))
    assert calls.prompts() == [(8, Prompt("meetingtool.exe", "MeetingTool", "R1", 8, False))]


def test_not_warning_for_an_app_silences_any_class():
    calls = Calls(Params(off=frozenset({"whatsapp.root.exe", "ms-teams.exe", "chrome.exe"})))
    calls.run(0, 60, act(whatsapp(), teams(), chrome(mic=True, peak=0.3)))
    assert calls.prompts() == []
    calls = Calls(Params(browsers=False))
    calls.run(0, 60, act(chrome(mic=True, peak=0.3, service="Google Meet")))
    assert calls.prompts() == []


@pytest.mark.parametrize(("seconds", "at"), [(1, 3), (500, 120), (12, 12)])
def test_the_seconds_stay_between_3_and_120(seconds, at):
    calls = Calls(Params(seconds=seconds))
    calls.run(0, at - 1, act(teams()))
    assert calls.prompts() == []
    calls.at(at, act(teams()))
    assert [p.seconds for _t, p in calls.prompts()] == [at]


def test_two_roots_of_the_same_executable_are_one_app():
    """Dois perfis do Chrome: um com o microfone, o outro tocando a chamada."""
    calls = Calls()
    calls.run(0, 8, act(chrome(mic=True), chrome(peak=0.3, pid=250)))
    assert [(p.exe, p.rule) for _t, p in calls.prompts()] == [("chrome.exe", "R2")]


def test_two_apps_ready_together_take_turns():
    calls = Calls()
    calls.run(0, 10, act(teams(), zoom()))
    assert [(t, p.exe) for t, p in calls.prompts()] == [(8, "ms-teams.exe")]
    calls.rules.dismiss("ms-teams.exe")
    calls.at(11, act(teams(), zoom()))
    assert [(t, p.exe, p.seconds) for t, p in calls.prompts()][1:] == [(11, "zoom.exe", 11)]


@pytest.mark.parametrize(("kind", "mic", "sound", "service", "degraded", "want"), [
    (KIND_MEETING, True, False, "", False, "R1"), (KIND_MEETING, False, True, "", False, "R4"),
    (KIND_MEETING, False, False, "", False, None), (KIND_MEETING, False, True, "", True, None),
    (KIND_BROWSER, True, False, "", False, None), (KIND_BROWSER, True, True, "", False, "R2"),
    (KIND_BROWSER, True, False, "Google Meet", False, "R2"), (KIND_BROWSER, False, True, "", False, None),
    (KIND_BROWSER, False, True, "Google Meet", False, "R4"), (KIND_BROWSER, True, True, "", True, None),
    ("other", True, True, "", False, "R3"), ("other", True, False, "", False, None), ("other", False, True, "", False, None),
    (None, True, True, "Google Meet", False, None), ("other", True, True, "", True, None),
])
def test_the_rule_table(kind, mic, sound, service, degraded, want):
    got = rule_for(kind, mic, sound, service, degraded, 8.0)
    assert (got[0] if got else None) == want
    if got:
        assert got[1] == {"R3": 20.0, "R4": 45.0}.get(got[0], 8.0)


def test_the_rules_never_record():
    """D-010: o módulo das regras não chama a gravação (só devolve eventos)."""
    import ast
    import inspect

    from listener.ui import call_rules

    tree = ast.parse(inspect.getsource(call_rules))
    names = {n.attr if isinstance(n, ast.Attribute) else n.id for n in ast.walk(tree) if isinstance(n, (ast.Attribute, ast.Name))}
    modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not names & {"start_recording", "Recorder"} and not any("capture" in m for m in modules)
    assert isinstance(CallRules(FAKE_WATCHED).step(None, 0, False, False, Params()), list)


def test_the_generic_rules_bring_no_executable_of_a_system():
    """R-08 (CP15): o padrão é a lista da plataforma inteira, sem "desmarcado" por nome de executável do Windows, e o
    módulo não tem nenhum ".exe"; o rótulo de um app de outro sistema é o nome dele."""
    import ast
    import inspect

    from listener.ui import call_rules

    watched = (WatchedApp("discord", "Discord", KIND_MEETING), WatchedApp("zoom", "Zoom", KIND_MEETING),
               WatchedApp("firefox", "Firefox", KIND_BROWSER))
    assert default_apps(watched) == {"discord", "zoom"}
    texts = [n.value for n in ast.walk(ast.parse(inspect.getsource(call_rules)))
             if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert [t for t in texts if ".exe" in t.lower()] == []
    rules = CallRules(watched)
    for t in range(21):
        got = rules.step(act(AppAudio(9, "signal-desktop", True, 0.3)), t, False, False, Params())
    assert [(p.exe, p.label, p.rule) for p in got if isinstance(p, Prompt)] == [("signal-desktop", "signal-desktop", "R3")]


def test_no_log_nor_print_gets_a_title_a_path_or_a_device_name():
    """Aceite 4 (D-053), R-09: as regras não logam nem imprimem título, caminho ou nome de aparelho."""
    from tests.log_privacy import L2_MODULES, leaks

    assert {str(m): leaks(m) for m in L2_MODULES} == {str(m): [] for m in L2_MODULES}
