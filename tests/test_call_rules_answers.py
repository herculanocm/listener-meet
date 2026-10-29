"""F5.7 (L2), a matriz do Aceite 1, parte 2: as respostas ao aviso, o fim da chamada, a carência, o lembrete, o teto por hora,
o diálogo modal, a gravação (`busy`) e o `mode()` (`listener/ui/call_rules.py`, sem Qt). Relógio manual."""
from __future__ import annotations

from collections.abc import Callable

from listener.platform.base import AppAudio
from listener.ui.call_rules import (MODE_ARMING, MODE_IDLE, MODE_SLOW, MODE_STOPPED, ClosePrompt, EpisodeEnded, Params,
                                    Pending, Prompt, State)
from tests.fake_calls import Calls, act, chrome, teams, whatsapp

TEAMS = "ms-teams.exe"


def test_now_not_holds_through_29_s_without_signal():
    """"Agora não" vale a chamada inteira: 29 s sem sinal ainda é a mesma chamada, e a volta do microfone não avisa."""
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.rules.dismiss(TEAMS)
    calls.run(9, 40, act(teams()))
    calls.run(41, 69, act())                                                # último sinal aos 40: 29 s sem
    calls.run(70, 200, act(teams()))
    assert [t for t, _p in calls.prompts()] == [8] and calls.ended() == []
    assert calls.rules.state(TEAMS) == State.DISMISSED


def test_with_31_s_without_signal_the_call_ends_and_the_grace_holds_120_s():
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.rules.dismiss(TEAMS)
    calls.run(9, 40, act(teams()))
    calls.run(41, 71, act())                                                # aos 70, 30 s sem sinal: acabou
    assert calls.ended() == [(70, EpisodeEnded(TEAMS, 40.0, "dispensado"))]
    calls.run(72, 197, act(teams()))                                        # carência até 190; conta de 190 a 198
    assert [t for t, _p in calls.prompts()] == [8]
    calls.at(198, act(teams()))
    assert calls.prompts()[-1] == (198, Prompt(TEAMS, "Teams", "R1", 8, False))


def test_the_reminder_comes_once_2_min_after_the_prompt_went_away():
    calls = Calls()
    calls.run(0, 28, act(teams()))
    calls.rules.expired(TEAMS)                                              # 20 s na tela sem clique
    assert calls.rules.state(TEAMS) == State.PENDING
    calls.run(29, 147, act(teams()))
    assert [t for t, _p in calls.prompts()] == [8]
    calls.at(148, act(teams()))
    assert calls.prompts()[-1] == (148, Prompt(TEAMS, "Teams", "R1", 148, True))
    calls.run(149, 168, act(teams()))
    calls.rules.expired(TEAMS)                                              # sumiu de novo: fica pendente, sem outro
    calls.run(169, 600, act(teams()))
    assert [t for t, _p in calls.prompts()] == [8, 148] and calls.rules.state(TEAMS) == State.PENDING
    calls.run(601, 631, act())
    assert calls.ended() == [(630, EpisodeEnded(TEAMS, 600.0, "pendente"))]


def test_without_remind_or_without_signal_there_is_no_reminder():
    calls = Calls(Params(remind=False))
    calls.run(0, 28, act(teams()))
    calls.rules.expired(TEAMS)
    calls.run(29, 400, act(teams()))
    assert [t for t, _p in calls.prompts()] == [8]
    calls = Calls()
    calls.run(0, 28, act(teams()))
    calls.rules.expired(TEAMS)
    calls.run(29, 140, act(teams()))
    calls.run(141, 150, act(teams(mic=False)))                              # sem a regra no vencimento: espera
    assert [t for t, _p in calls.prompts()] == [8]
    calls.at(151, act(teams()))
    assert [p.reminder for _t, p in calls.prompts()] == [False, True]


def test_at_most_6_prompts_per_hour_counting_reminders():
    calls = Calls()
    apps = act(*(AppAudio(600 + i, f"app{i}.exe", True, 0.2) for i in range(7)))
    calls.run(0, 19, apps)
    for t in range(20, 30):
        calls.at(t, apps)
        for _t, p in calls.prompts():
            calls.rules.dismiss(p.exe)
    assert [(t, p.exe) for t, p in calls.prompts()] == [(20 + i, f"app{i}.exe") for i in range(6)]
    calls.run(30, 3619, apps, every=7)
    assert len(calls.prompts()) == 6
    calls.at(3620, apps)                                                    # uma hora depois do primeiro
    assert calls.prompts()[-1][1].exe == "app6.exe"
    calls = Calls()                                                         # o lembrete também conta
    calls.run(0, 28, act(teams()))
    calls.rules.expired(TEAMS)
    calls.run(29, 148, act(teams()))
    calls.rules.dismiss(TEAMS)
    others = act(teams(), *(AppAudio(600 + i, f"app{i}.exe", True, 0.2) for i in range(5)))
    calls.run(149, 168, others)
    for t in range(169, 180):
        calls.at(t, others)
        for _t, p in calls.prompts():
            calls.rules.dismiss(p.exe)
    assert [(t, p.exe, p.reminder) for t, p in calls.prompts()] == [
        (8, TEAMS, False), (148, TEAMS, True), *((169 + i, f"app{i}.exe", False) for i in range(4))]


def test_a_modal_dialog_holds_the_prompt():
    calls = Calls()
    calls.run(0, 11, act(teams()), modal=True)
    assert calls.prompts() == []
    calls.at(12, act(teams()))
    assert calls.prompts() == [(12, Prompt(TEAMS, "Teams", "R1", 12, False))]


def test_recording_closes_the_prompt_and_covers_every_app_with_a_rule_signal():
    calls = Calls()
    calls.run(0, 9, act(teams(), whatsapp(), AppAudio(700, "Spotify.exe", False, 0.4)))
    assert [(t, p.exe) for t, p in calls.prompts()] == [(8, TEAMS)]
    assert calls.at(10, act(teams(), whatsapp()), busy=True) == [ClosePrompt(TEAMS, "gravação")]
    assert calls.rules.mode() == MODE_STOPPED
    assert (calls.rules.state(TEAMS), calls.rules.state("whatsapp.root.exe")) == (State.COVERED, State.COVERED)
    assert calls.rules.state("spotify.exe") == State.QUIET                  # som sem regra: segue livre
    calls.run(11, 30, act(teams(), whatsapp()), busy=True)
    calls.run(31, 100, act(teams(), whatsapp()))                            # parou de gravar; a chamada segue
    assert len(calls.prompts()) == 1 and calls.rules.mode() == MODE_SLOW
    calls.run(101, 131, act())
    assert sorted(e.exe for _t, e in calls.ended()) == [TEAMS, "whatsapp.root.exe"]
    assert {e.outcome for _t, e in calls.ended()} == {"coberto"} and calls.rules.mode() == MODE_IDLE
    calls.at(132, act(teams()))
    calls.run(133, 140, act(teams()))
    assert [t for t, _p in calls.prompts()] == [8, 140]                     # coberto não tem carência


def test_record_waits_for_busy_and_closing_the_warmup_without_recording_is_a_dismiss():
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.rules.record(TEAMS)
    assert calls.rules.pending() == () and calls.rules.mode() == MODE_ARMING
    assert calls.at(9, act(teams()), busy=True) == []                       # o clique já fechou o aviso
    assert calls.rules.state(TEAMS) == State.COVERED
    calls.rules.dismiss(TEAMS)                                              # fechou o "Antes de gravar" sem gravar
    calls.run(10, 300, act(teams()))
    assert calls.rules.state(TEAMS) == State.DISMISSED and len(calls.prompts()) == 1


def test_a_call_that_vanishes_with_the_prompt_on_screen_closes_it():
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.run(9, 38, act())
    assert calls.closes() == [(38, ClosePrompt(TEAMS, "sumiu"))]
    assert calls.ended() == [(38, EpisodeEnded(TEAMS, 8.0, "avisado"))]


def test_turning_the_app_off_mid_call_closes_the_prompt_and_dismisses():
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.params = Params(off=frozenset({TEAMS}))
    assert calls.at(9, act(teams())) == [ClosePrompt(TEAMS, "desligado")]
    assert calls.rules.state(TEAMS) == State.DISMISSED


def test_turning_the_warning_off_closes_the_prompt_and_stops():
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.params = Params(enabled=False)
    assert calls.at(9, act(teams())) == [ClosePrompt(TEAMS, "desligado")]
    assert calls.rules.mode() == MODE_STOPPED and calls.rules.state(TEAMS) == State.QUIET
    calls.params = Params()
    calls.run(10, 18, act(teams()))
    assert [t for t, _p in calls.prompts()] == [8, 18]


def test_pending_lists_the_unanswered_calls_for_the_tray():
    calls = Calls()
    calls.run(0, 8, act(teams()))
    assert calls.rules.pending() == (Pending(TEAMS, "Teams"),)
    calls.rules.expired(TEAMS)
    assert calls.rules.pending() == (Pending(TEAMS, "Teams"),)
    calls.rules.dismiss(TEAMS)
    assert calls.rules.pending() == ()


def test_the_mode_in_each_case():
    calls = Calls()
    assert calls.rules.mode() == MODE_IDLE
    calls.at(0, act(teams()))
    assert calls.rules.mode() == MODE_ARMING                                 # contando
    calls.run(1, 8, act(teams()))
    assert calls.rules.mode() == MODE_ARMING                                 # aviso à vista
    calls.rules.expired(TEAMS)
    assert calls.rules.mode() == MODE_ARMING                                 # sem resposta
    calls.rules.dismiss(TEAMS)
    assert calls.rules.mode() == MODE_SLOW                                   # respondida, esperando o fim
    calls.at(9, act(teams()), busy=True)
    assert calls.rules.mode() == MODE_STOPPED
    calls.run(10, 40, act())
    assert calls.rules.mode() == MODE_IDLE
    calls.at(41, act(AppAudio(700, "Spotify.exe", False, 0.4)))
    assert calls.rules.mode() == MODE_IDLE                                   # som sem regra: nada a contar


# ---------------------------------------------------------------- revisão do L1/L2 (R-03, R-04) e lacunas das mutações
def test_a_covered_call_stays_covered_while_recording_without_readings():
    """R-03: gravando, o L3 não lê (spec, item 2). Coberto na leitura da borda; 10 min de passos sem leitura; parou de
    gravar com a chamada seguindo: não avisa."""
    calls = Calls()
    calls.run(0, 5, act(teams()))
    calls.at(6, act(teams()), busy=True)
    assert calls.rules.state(TEAMS) == State.COVERED
    calls.run(7, 600, None, busy=True, every=2)
    assert calls.rules.state(TEAMS) == State.COVERED and calls.ended() == []
    calls.run(602, 700, act(teams()), every=2)
    assert calls.prompts() == []


def test_starting_to_record_with_no_reading_covers_the_counting_call():
    """R-03: o atalho começa a gravar no meio da contagem e a borda chega sem leitura: cobre pela última leitura real."""
    calls = Calls()
    calls.run(0, 5, act(teams()))
    calls.run(6, 300, None, busy=True, every=2)
    assert calls.rules.state(TEAMS) == State.COVERED
    calls.run(302, 400, act(teams()), every=2)
    assert calls.prompts() == []


def test_esc_in_the_warmup_after_40_s_dismisses_the_right_call():
    """R-03: "Gravar…" no aviso; o "Antes de gravar" aberto 40 s (sem leitura) e o Esc: a chamada segue sem novo aviso."""
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.rules.record(TEAMS)
    calls.run(10, 50, None, busy=True, every=2)
    calls.rules.dismiss(TEAMS)
    assert calls.rules.state(TEAMS) == State.DISMISSED
    calls.run(52, 200, act(teams()), every=2)
    assert [t for t, _p in calls.prompts()] == [8]


def _drive(calls: Calls, t_end: float, peak_at: Callable[[float], float], slow_every: float) -> None:
    """Um L3 de mentira: lê a cada 2 s no "armando", a cada `slow_every` s no "lento" e a cada 20 s no ocioso."""
    t = 0.0
    while t <= t_end:
        calls.at(t, act(teams(mic=False, peak=peak_at(t))))
        last = calls.prompts()[-1] if calls.prompts() else None
        if last is not None and last[0] == t:
            calls.rules.dismiss(TEAMS)                                       # "Agora não" na hora
        mode = calls.rules.mode()
        t += 2.0 if mode == MODE_ARMING else (slow_every if mode == MODE_SLOW else 20.0)


def test_a_dismissed_webinar_does_not_end_on_two_sampled_pauses():
    """R-04: no "lento", duas leituras seguidas caem numa pausa da fala (40 s "sem sinal"): a chamada não acaba, e o
    webinar não avisa de novo depois da carência."""
    calls = Calls()
    _drive(calls, 600, lambda t: 0.0 if t in {106.0, 126.0} else 0.3, slow_every=20.0)
    assert [t for t, _p in calls.prompts()] == [46.0] and calls.ended() == []


def test_the_call_ends_only_after_30_s_and_3_silent_readings():
    """R-04: 30 s sem sinal com duas leituras não bastam; a terceira leitura sem sinal encerra."""
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.rules.dismiss(TEAMS)
    calls.at(10, act(teams()))
    calls.at(45, act())
    calls.at(60, act())
    assert calls.ended() == [] and calls.rules.state(TEAMS) == State.DISMISSED
    calls.at(61, act())
    assert calls.ended() == [(61, EpisodeEnded(TEAMS, 10.0, "dispensado"))]


def test_grace_after_an_unanswered_call():
    """M09: sem resposta (pendente) também dá 120 s de carência."""
    calls = Calls()
    calls.run(0, 28, act(teams()))
    calls.rules.expired(TEAMS)
    calls.run(29, 40, act(teams()))
    calls.run(41, 71, act())
    assert [e.outcome for _t, e in calls.ended()] == ["pendente"]
    calls.run(72, 189, act(teams()))
    assert [t for t, _p in calls.prompts()] == [8]


def test_record_from_the_tray_on_a_pending_call():
    """M17: "Gravar a reunião do Teams…" na bandeja, com a chamada pendente."""
    calls = Calls()
    calls.run(0, 28, act(teams()))
    calls.rules.expired(TEAMS)
    calls.rules.record(TEAMS)
    assert calls.rules.state(TEAMS) == State.PROMPTED and calls.rules.pending() == ()


def test_sound_alone_keeps_the_call_alive():
    """M19: dispensado; o microfone some (mudo que fecha a captura) e o som segue: a chamada não acaba."""
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.rules.dismiss(TEAMS)
    calls.run(9, 200, act(teams(mic=False, peak=0.3)))
    assert calls.ended() == [] and calls.rules.state(TEAMS) == State.DISMISSED


def test_a_peak_of_exactly_001_is_sound():
    """M13: a borda de 0,01 é som, para a regra e para manter a chamada."""
    calls = Calls()
    calls.run(0, 8, act(chrome(mic=True, peak=0.01)))
    assert [(t, p.rule) for t, p in calls.prompts()] == [(8, "R2")]
    calls.rules.dismiss("chrome.exe")
    calls.run(9, 200, act(chrome(peak=0.01)))
    assert calls.ended() == [] and calls.rules.state("chrome.exe") == State.DISMISSED


def test_turning_the_app_off_clears_a_pending_call_from_the_tray():
    """M24: "Não avisar para o Teams" com a chamada pendente tira a chamada da bandeja."""
    calls = Calls()
    calls.run(0, 28, act(teams()))
    calls.rules.expired(TEAMS)
    assert calls.rules.pending() == (Pending(TEAMS, "Teams"),)
    calls.params = Params(off=frozenset({TEAMS}))
    calls.at(29, act(teams()))
    assert calls.rules.pending() == () and calls.rules.state(TEAMS) == State.DISMISSED


def test_lost_readings_do_not_end_the_call():
    """R-04: a leitura perdida (o sensor falhando, fora do `busy`) não é uma leitura sem sinal: a chamada não acaba por ela."""
    calls = Calls()
    calls.run(0, 8, act(teams()))
    calls.rules.dismiss(TEAMS)
    calls.run(9, 100, None)
    assert calls.ended() == [] and calls.rules.state(TEAMS) == State.DISMISSED
