"""F5.6 L1b (item 11; A-10, A-13, A-14; Comportamento 2; Aceite 2), desde a F5.11 nas linhas de pedido das abas (U-103,
A-23; A-37: os estados testados na linha nova antes de a barra sair): "Gerar ata e ações" e "Gerar resumo" nos seis
estados, lidos da mesma função (`request_states`) numa recarga; com versão, "Gerar de novo"; com job de LLM ativo os
pedidos seguem ligados e só a espécie "gerando" fica apagada; "falhou" pede de novo sem o popup, salvo a transcrição que
mudou; pela tela, o "quando todos tiverem nome" sai uma vez (ao resolver a última voz) e o "agora" sai já; e as três abas
(Ata, Ações e Resumo) com um botão de pedido cada, a linha da aba escondida nascendo quando ela aparece."""
from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from listener.llm.fake import FakeProvider
from listener.llm.types import LlmTask
from listener.model import JobStatus, JobStep, SummaryKind
from listener.pipeline import review, review_person
from listener.pipeline.llm_request_state import STALE_NAMES
from listener.pipeline.llm_requests import Mode, RequestState, Species, State
from listener.pipeline.llm_steps import STALE_TRANSCRIPT
from listener.rag.export import fmt_datetime
from listener.ui.llm_request_bar import LABELS, RETRY, RUNNING_TIP, STATE_TONE, state_text
from listener.ui.theme import Tone
from tests.test_ata_a_pedido import _drain
from tests.test_llm_steps import _pipeline, _provider_ready, _record, _run_llm


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


class Chooser:
    def __init__(self, mode: Mode | None) -> None:
        self.mode = mode
        self.asked: list[tuple[str, ...]] = []

    def __call__(self, _parent: Any, question: Any) -> Mode | None:
        self.asked.append(question.tasks)
        return self.mode


def _window(qtbot: Any, platform: Any, store: Any, fake: FakeProvider | None = None) -> tuple[int, Any]:
    from listener.ui.meeting import MeetingWindow

    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store, fake)                                                            # o núcleo
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    return mid, win


def _shown(win: Any, sp: Species) -> tuple[str, str, bool]:
    line = win.llm.line(sp)
    return line.badge.text(), line.button.text(), line.button.isEnabled()


def _fail(store: Any, error: str) -> int:
    job = store.jobs.claim(pid=2, kinds=("llm",))
    assert job is not None
    store.jobs.finish(int(job["id"]), JobStatus.ERROR, error=error)
    return int(job["id"])


def test_the_state_text_and_tone_of_each_state():
    assert state_text(RequestState(State.NONE)) == "não gerado" and state_text(RequestState(State.RUNNING)) == "gerando"
    assert state_text(RequestState(State.REQUESTED)) == "pedido" and state_text(RequestState(State.REQUESTED), 1) == "pedido · espera 1 voz"
    assert state_text(RequestState(State.DONE, "2026-10-29T15:39:00.000-03:00")) == "gerado em 29/10"
    assert state_text(RequestState(State.STALE, "x", ("nomes", "idioma"))) == "desatualizado (nomes, idioma)"
    assert state_text(RequestState(State.FAILED, error="Ata falhou: tempo esgotado")) == "falhou"
    assert {STATE_TONE[s] for s in State} == {Tone.DANGER, Tone.INFO, Tone.WARNING, Tone.SUCCESS, Tone.NEUTRAL}


def test_the_bar_shows_the_six_states_from_one_reading(qtbot, platform, store, monkeypatch):
    mid, win = _window(qtbot, platform, store)
    reads: Counter[str] = Counter()
    real = store.meetings.list_events_of

    def counted(m: int, types: Any) -> Any:
        reads[",".join(sorted(str(t) for t in types))] += 1
        return real(m, types)

    monkeypatch.setattr(store.meetings, "list_events_of", counted)
    win.reload()
    assert reads and max(reads.values()) == 1, reads                                 # A-15 (R-06): cada evento lido uma vez
    assert _shown(win, Species.MINUTES) == ("não gerado", LABELS[Species.MINUTES], True)
    win.llm.choose = Chooser(Mode.NAMES)
    win.llm.line(Species.MINUTES).button.click()                                     # pedido: espera as duas vozes
    assert _shown(win, Species.MINUTES) == ("pedido · espera 2 vozes", LABELS[Species.MINUTES], True)
    assert win.llm.line(Species.MINUTES).badge.badge_tone == Tone.INFO
    win.llm.choose = Chooser(Mode.NOW)
    win.llm.line(Species.MINUTES).button.click()                                     # "agora": tira a ata do adiamento
    assert _shown(win, Species.MINUTES) == ("gerando", LABELS[Species.MINUTES], False)
    assert _shown(win, Species.SUMMARY) == ("não gerado", LABELS[Species.SUMMARY], True)    # o outro segue ligado (A-10)
    win.llm.line(Species.SUMMARY).button.click()                                     # entra na fila atrás da ata
    for sp in Species:                                                               # gerando: a espécie apagada, com a dica
        assert _shown(win, sp) == ("gerando", LABELS[sp], False) and win.llm.line(sp).button.toolTip() == RUNNING_TIP
    assert not win.llm.text_tabs[str(SummaryKind.EXECUTIVE)].btn_regen.isEnabled()
    assert _drain(store, FakeProvider()) == 2
    win.reload()
    day = fmt_datetime(store.summaries.current(mid, SummaryKind.MINUTES)["created_at"])[:5]
    assert _shown(win, Species.MINUTES) == (f"gerado em {day}", RETRY, True)                    # A-23: com versão
    assert win.llm.line(Species.MINUTES).badge.badge_tone == Tone.SUCCESS
    first = next(c.cluster_id for c in review.pending(review.review_items(store, mid)))
    review_person.create_person(store, first, "Ana", "CFO")                                 # os nomes mudaram depois da ata
    win.reload()
    assert _shown(win, Species.MINUTES)[0] == f"desatualizado ({STALE_NAMES})"
    assert win.llm.line(Species.MINUTES).badge.badge_tone == Tone.WARNING
    win.llm.line(Species.MINUTES).button.click()
    _fail(store, "Ata falhou: tempo esgotado")
    win.reload()
    assert _shown(win, Species.MINUTES) == ("falhou", RETRY, True)
    assert win.llm.line(Species.MINUTES).badge.toolTip() == "Ata falhou: tempo esgotado"
    assert win.llm.line(Species.MINUTES).badge.badge_tone == Tone.DANGER and not win.llm.line(Species.MINUTES).note.isVisibleTo(win)


def test_with_an_llm_job_running_the_requests_stay_on_and_the_core_regenerate_refuses(qtbot, platform, store):
    """A-10: com o núcleo na fila, os pedidos seguem ligados e entram atrás dele; o "Gerar título de novo" recusa."""
    from listener.ui.meeting import MeetingWindow

    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)                                                            # o núcleo na fila, sem rodar
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    assert all(_shown(win, sp)[2] for sp in Species) and not win.llm.title_tab.act_regen.isEnabled()
    win.show()
    ata = win.llm.text_tabs[str(SummaryKind.MINUTES)]
    assert not ata.empty.action.isVisibleTo(ata) and not ata.btn_regen.isVisibleTo(ata)       # o pedido é o da linha (A-23)
    assert win.llm.line(Species.MINUTES).button.toolTip() != RUNNING_TIP
    win.llm.choose = Chooser(Mode.NOW)
    job = win.llm.regenerate(JobStep.MINUTES)                                        # "Gerar de novo" da ata, pelo popup
    core = [j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm"]
    assert job is not None and len(core) == 2 and job == max(int(j["id"]) for j in core)    # atrás do núcleo


def test_failed_asks_again_without_the_popup_except_after_a_changed_transcript(qtbot, platform, store):
    """Comportamento 2: o custo já foi aceito; com `STALE_TRANSCRIPT`, a transcrição mudou e o popup volta."""
    mid, win = _window(qtbot, platform, store)
    popup = win.llm.choose = Chooser(Mode.NOW)
    win.llm.line(Species.MINUTES).button.click()
    assert popup.asked == [("minutes",)]
    _fail(store, "Ata falhou: tempo esgotado")
    win.reload()
    win.llm.line(Species.MINUTES).button.click()                                     # "Gerar de novo": sem popup
    again = [j for j in store.jobs.for_meeting(mid) if j["kind"] == "llm" and j["status"] == JobStatus.PENDING]
    assert popup.asked == [("minutes",)] and len(again) == 1 and "gerando pela fila" in win.message.text()
    _fail(store, f"Ata falhou: {STALE_TRANSCRIPT}")
    win.reload()
    assert _shown(win, Species.MINUTES) == ("falhou", RETRY, True)
    win.llm.line(Species.MINUTES).button.click()                                     # a transcrição mudou: pergunta
    assert popup.asked == [("minutes",), ("minutes",)]


def test_names_asked_on_screen_fires_once_and_now_fires_at_once(qtbot, platform, store):
    """Aceite 2: "quando todos tiverem nome" pedido pela tela sai uma vez, ao resolver a última voz na revisão; "agora" já."""
    from listener.ui.review import ReviewDialog

    fake = FakeProvider()
    mid, win = _window(qtbot, platform, store, fake)
    win.llm.choose = Chooser(Mode.NAMES)
    win.llm.line(Species.MINUTES).button.click()
    assert _drain(store, fake) == 0 and not fake.calls(LlmTask.MINUTES)              # espera as vozes
    launched: list[bool] = []
    dlg = ReviewDialog(store, mid, on_launch=launched.append)
    qtbot.addWidget(dlg)
    assert dlg.create_person("Ana", "CFO") and launched == []                        # falta uma voz
    assert dlg.create_person("Bia") and launched == [True]                           # a última: sai
    assert _drain(store, fake) == 1 and len(fake.calls(LlmTask.MINUTES)) == 1
    dlg.finish()                                                                     # o "Concluir" depois não repete
    assert _drain(store, fake) == 0 and len(fake.calls(LlmTask.MINUTES)) == 1
    win.llm.choose = Chooser(Mode.NOW)
    win.reload()
    win.llm.line(Species.SUMMARY).button.click()                                     # "agora": já na fila
    assert _drain(store, fake) == 1 and len(fake.calls(LlmTask.EXECUTIVE)) == len(fake.calls(LlmTask.PRE_SUMMARY)) == 1
    assert len(fake.calls(LlmTask.MINUTES)) == 1                                     # P-07: a ata em dia não volta


def test_each_llm_tab_has_one_request_button_and_hidden_lines_are_born_when_shown(qtbot, platform, store):
    """U-103 (A-23): Ata, Resumo e Ações com a linha da espécie em cima; o botão da linha é o único pedido da aba (o "Gerar
    de novo" e a ação do estado vazio das abas de texto saem); a linha de uma aba escondida nasce quando ela aparece, já com o
    estado da última recarga (as três pela mesma leitura)."""
    from PySide6.QtWidgets import QPushButton

    _mid, win = _window(qtbot, platform, store)
    win.show()
    pages = [win.tabs.widget(i) for i in range(3)]
    assert [p.species for p in pages] == [Species.MINUTES, Species.SUMMARY, Species.MINUTES]
    assert win.tabs.currentWidget() is win.transcript and all(p.line is None for p in pages)   # abre na transcrição
    win.show_tab("ata")
    assert pages[0].line is not None and pages[1].line is None and pages[2].line is None      # só a aba que apareceu
    win.show_tab("resumo")
    win.show_tab("acoes")
    assert pages[1].line is not None and pages[2].line is not None
    assert pages[2].line.badge.text() == pages[0].line.badge.text() == "não gerado"
    for page, label in zip(pages, (LABELS[Species.MINUTES], LABELS[Species.SUMMARY], LABELS[Species.MINUTES]), strict=True):
        requests = [b for b in page.findChildren(QPushButton) if b.isVisibleTo(page) and b.text().startswith("Gerar")]
        assert requests == [page.line.button] and page.line.button.text() == label
