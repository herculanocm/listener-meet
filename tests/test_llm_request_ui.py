"""F5.6 L1b (U-089 a U-091; A-02, A-16, A-17, A-19, A-21, A-49; Comportamento 1): o popup do pedido nas quatro situações
(antes da transcrição, vozes sem nome, reunião já revisada, todas com nome), o padrão "quando todos tiverem nome" no Enter, Esc
e o X sem pedido; o custo com o núcleo pendente; as caixas ao parar (a última escolha, Esc sem pedido, o pedido preso ao job do
processamento, o `auto_start` desligado indo ao próximo) e o controlador passando o job; sem chave, tudo apagado e o popup nem
abre; a troca de idioma com as caixas (o pedido preso ao reprocessamento) e a caixa "Resumo parcial" do warm-up."""
from __future__ import annotations

import json
from typing import Any

import pytest
from PySide6.QtCore import Qt

from listener.capture.recorder import RecordConfig, SourceSpec
from listener.model import EventType, JobKind, JobStatus
from listener.pipeline import llm_requests, review
from listener.pipeline.llm_requests import Mode, Outcome, Species
from listener.pipeline.runner import JobRunner
from listener.ui import llm_ask
from listener.ui.llm_request import GO, NAMES, NOW, RequestDialog
from listener.ui.theme import KIND_PROP, PRIMARY
from tests.fake_pipeline import FakeDeps
from tests.test_llm_steps import SCRIPT, _pipeline, _provider_ready, _record, _run_llm
from tests.test_runner import CFG


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _asked(store: Any, mid: int) -> list[dict]:
    return [e["data"] for e in store.list_events(mid) if e["type"] == EventType.LLM_REQUESTED]


def _heavy(store: Any) -> int:
    job = store.jobs.claim(pid=1, kinds=(JobKind.MEETING, JobKind.REPROCESS))
    assert job is not None
    assert JobRunner(store, FakeDeps(SCRIPT), job, config=CFG, heartbeat_thread=False).run() == JobStatus.DONE
    return int(job["id"])


def _spy_after(monkeypatch: Any) -> list[int | None]:
    """O `after_job_id` que cada pedido da tela entrega a `llm_requests.request` (A-18: o job vem de quem pede; o `_bind`
    cairia no mesmo processamento ativo, e só o argumento mostra a ligação)."""
    seen: list[int | None] = []
    real = llm_requests.request

    def spy(*a: Any, **k: Any) -> Any:
        seen.append(k.get("after_job_id"))
        return real(*a, **k)

    monkeypatch.setattr(llm_requests, "request", spy)
    return seen


def _pending(store: Any, mid: int) -> list[int]:
    return [c.cluster_id for c in review.pending(review.review_items(store, mid))]


class Chooser:
    """O popup dos testes: guarda (pai, pergunta) e devolve o modo combinado."""

    def __init__(self, mode: Mode | None) -> None:
        self.mode = mode
        self.seen: list[tuple[Any, llm_ask.Question]] = []

    def __call__(self, parent: Any, question: llm_ask.Question) -> Mode | None:
        self.seen.append((parent, question))
        return self.mode


# ---------------------------------------------------------------- o popup (U-090, A-16, A-17)
def test_the_popup_in_the_four_situations(qtbot, platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    store._x("UPDATE meetings SET duration_ms = ? WHERE id = ?", (60 * 60_000, mid))
    store.jobs.enqueue(mid)
    before = llm_ask.question(store, mid, ["minutes"])                             # 1. antes da transcrição
    assert before.title == "Gerar ata e ações" and before.lines[0] == llm_ask.NOT_RUN and not before.named
    assert llm_ask.ADVICE in before.lines and before.lines[-1].startswith("Custo: ~ US$ ") and "estimado pela duração" in before.lines[-1]
    _heavy(store)
    assert llm_ask.question(store, mid, ["minutes"]).lines[0] == llm_ask.NOT_RUN     # a identificação na fila: ainda não rodou
    _run_llm(store)
    pending = llm_ask.question(store, mid, ["executive", "pre_summary"])            # 2. vozes sem nome
    assert pending.title == "Gerar resumo" and pending.lines[0] == "2 vozes ainda sem nome." and llm_ask.REVIEWED not in pending.lines
    assert llm_ask.WITH_MINUTES in pending.lines and "estimado pela transcrição" in pending.lines[-1]   # P-07: a ata vai junto
    dlg = RequestDialog(pending)
    qtbot.addWidget(dlg)
    assert (dlg.btn_primary.text(), dlg.btn_now and dlg.btn_now.text(), dlg.btn_cancel.text()) == (NAMES, NOW, "Cancelar")
    assert dlg.btn_primary.isDefault() and dlg.btn_primary.property(KIND_PROP) == PRIMARY and dlg.windowTitle() == "Gerar resumo"
    review.finish(store, mid)                                                       # 3. revisada, com as vozes sem nome
    reviewed = llm_ask.question(store, mid, ["minutes", "executive"])
    assert reviewed.title == "Gerar ata, ações e resumo" and llm_ask.REVIEWED in reviewed.lines and not reviewed.named
    for cid, name in zip(_pending(store, mid), ("Ana", "Bia"), strict=True):         # 4. todas com nome
        review.create_person(store, cid, name)
    named = llm_ask.question(store, mid, ["minutes"])
    assert named.named and named.lines[0] == llm_ask.ALL_NAMED and llm_ask.ADVICE not in named.lines
    go = RequestDialog(named)
    qtbot.addWidget(go)
    assert go.btn_primary.text() == GO and go.btn_now is None and go.btn_primary.isDefault()


def test_enter_is_when_everyone_has_a_name_and_esc_or_the_x_ask_nothing(qtbot, platform, store):
    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    question = llm_ask.question(store, mid, ["minutes"])
    dlg = RequestDialog(question)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.btn_primary.text() == NAMES and dlg.btn_primary.isDefault() and dlg.focusWidget() is dlg.btn_primary
    assert dlg.btn_primary.property(KIND_PROP) == PRIMARY and dlg.btn_now is not None and dlg.btn_now.property(KIND_PROP) != PRIMARY
    qtbot.keyClick(dlg, Qt.Key.Key_Return)                                          # o padrão: foco e Enter
    assert dlg.mode == Mode.NAMES and dlg.result() == RequestDialog.DialogCode.Accepted
    for close in ("esc", "x"):
        other = RequestDialog(question)
        qtbot.addWidget(other)
        other.show()
        if close == "esc":
            qtbot.keyClick(other, Qt.Key.Key_Escape)
        else:
            other.close()
        assert other.mode is None and other.result() == RequestDialog.DialogCode.Rejected, close
    cancel = Chooser(None)                                                          # cancelado: nada registrado
    assert llm_ask.ask(store, mid, ["minutes"], "teste", lambda q: cancel(None, q)) is None and _asked(store, mid) == []
    assert len(cancel.seen) == 1


def test_the_cost_includes_the_core_left_pending_by_a_processing_without_a_key(platform, store):
    """O L1a deixou para o L1b: processada sem chave, o primeiro pedido leva identidade, título e revisada na frente da ata."""
    mid = _record(platform, store)
    _pipeline(store, mid)                                                           # sem chave: o encadeamento fica pendente
    _provider_ready(store)
    pending = llm_ask.facts(store, mid, ["minutes"]).cost
    assert pending.core == ("identify", "title", "revise") and pending.tasks == ("minutes",)
    line = llm_ask.cost_line(pending)
    assert "inclui a identificação das vozes, o título e a transcrição revisada, que ficaram pendentes" in line
    llm_ask.submit(store, mid, ["minutes"], Mode.NOW, "teste")                      # encadeia: o núcleo e a ata na fila
    chained = llm_ask.facts(store, mid, ["minutes"]).cost
    assert chained.core == () and chained.usd is not None and pending.usd is not None and pending.usd > chained.usd


# ---------------------------------------------------------------- as caixas ao parar (U-089, A-19)
def test_the_stop_boxes_remember_the_last_choice_and_the_request_waits_for_the_processing(qtbot, platform, store, monkeypatch):
    from listener.ui.summary import SummaryDialog

    mid = _record(platform, store)
    _provider_ready(store)
    job = store.jobs.enqueue(mid)
    handed = _spy_after(monkeypatch)
    first = SummaryDialog(store, mid, {"duration_s": 3.0}, after_job_id=job)
    qtbot.addWidget(first)
    assert [b.isChecked() for b in first.boxes.boxes.values()] == [False, False] and first.boxes.note is None   # 1ª vez
    popup = first.choose = Chooser(Mode.NAMES)
    first.boxes.boxes[Species.MINUTES].setChecked(True)
    first.accept()
    assert popup.seen[0][0] is first and popup.seen[0][1].lines[0] == llm_ask.NOT_RUN and handed == [job]
    assert _asked(store, mid) == [{"tasks": ["minutes"], "mode": "names", "origin": "ao parar a gravação", "after_job_id": job}]
    assert llm_ask.last_choice(store) == {Species.MINUTES}
    again = SummaryDialog(store, mid, {"duration_s": 3.0}, after_job_id=job)
    qtbot.addWidget(again)
    again.choose = Chooser(Mode.NOW)
    assert again.boxes.boxes[Species.MINUTES].isChecked() and not again.boxes.boxes[Species.SUMMARY].isChecked()
    again.boxes.boxes[Species.SUMMARY].setChecked(True)
    again.show()
    qtbot.keyClick(again, Qt.Key.Key_Escape)                                         # Esc: nada pedido nem lembrado
    assert again.choose.seen == [] and len(_asked(store, mid)) == 1 and llm_ask.last_choice(store) == {Species.MINUTES}
    assert _heavy(store) == job                                                      # o fim do processamento consome o pedido
    waiting = [d for d in (e["data"] for e in store.list_events(mid)) if d.get("requested")]
    assert len(waiting) == 1 and waiting[0]["tasks"] == ["minutes"] and waiting[0]["pending"] == 2   # espera as 2 vozes


def test_the_controller_hands_the_job_to_the_summary_and_auto_start_off_goes_to_the_next(qtbot, qapp, store, platform, monkeypatch):
    from listener.ui import controller as ctl
    from listener.ui.controller import Controller

    _provider_ready(store)
    c = Controller(qapp, platform, store, clock_factory=lambda: platform.clock)
    c.tray = None
    monkeypatch.setattr(c, "launch_worker", lambda light=False: False)
    monkeypatch.setattr(c, "notify", lambda _text: None)
    handed: list[dict[str, Any]] = []
    def summary(*a: Any, **k: Any) -> Any:
        handed.append({"mid": a[1], **k})
        return type("D", (), {"exec": lambda _self: None})()

    monkeypatch.setattr(ctl, "SummaryDialog", summary)
    cfg = RecordConfig(source=SourceSpec("app", pid=4242, exe="meeting.exe", label="Reunião"), chunk_seconds=0.5,
                       stop_timeout_s=1.0, retry_base_s=0.001)
    try:
        for auto in (True, False):
            store.set_setting("pipeline.auto_start", auto)
            assert c.start_recording(cfg, {"title": "t"})
            platform.pump(1.2)
            c.stop_recording()
            qtbot.waitUntil(lambda: c.state.name == "IDLE", timeout=5000)
        jobs = [int(j["id"]) for j in store.jobs.for_meeting(handed[0]["mid"])]
        assert handed[0]["after_job_id"] == jobs[0] and handed[1]["after_job_id"] is None
        assert handed[0]["on_launch"] is not None and handed[0]["on_message"] is not None    # R-05: o resultado no aviso
    finally:
        c.emergency_stop()


def test_auto_start_off_the_stop_request_goes_to_the_next_processing(qtbot, platform, store):
    from listener.ui.summary import SummaryDialog

    mid = _record(platform, store)
    _provider_ready(store)
    dlg = SummaryDialog(store, mid, {"duration_s": 3.0}, after_job_id=None)          # `_enqueue_processing` devolveu None
    qtbot.addWidget(dlg)
    dlg.choose = Chooser(Mode.NOW)
    dlg.boxes.boxes[Species.MINUTES].setChecked(True)
    dlg.accept()
    assert _asked(store, mid)[0]["after_job_id"] is None and not [j for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM]
    assert llm_ask.submit(store, mid, ["executive"], Mode.NOW, "teste").outcome == Outcome.NEXT
    store.jobs.enqueue(mid)
    _heavy(store)                                                                    # o próximo processamento leva os pedidos
    tasks = {t for j in store.jobs.for_meeting(mid) if j["kind"] == JobKind.LLM for t in json.loads(j["params_json"])["tasks"]}
    assert {"minutes", "executive"} <= tasks


# ---------------------------------------------------------------- sem chave (Comportamento 1)
def test_without_a_key_boxes_buttons_and_the_popup_are_off(qtbot, platform, store):
    from listener.ui.meeting import MeetingWindow
    from listener.ui.meeting_language import ChangeConfirm
    from listener.ui.summary import SummaryDialog

    mid = _record(platform, store)
    _pipeline(store, mid)
    llm_ask.save_choice(store, {Species.MINUTES})
    summary = SummaryDialog(store, mid, {"duration_s": 3.0})
    qtbot.addWidget(summary)
    boxes = summary.boxes.boxes.values()
    assert not any(b.isEnabled() or b.isChecked() for b in boxes) and summary.boxes.note is not None
    assert summary.boxes.note.text() == llm_ask.NO_KEY_TIP
    summary.boxes.boxes[Species.SUMMARY].setChecked(True)
    summary.choose = Chooser(Mode.NOW)
    summary.accept()
    assert summary.choose.seen == [] and _asked(store, mid) == [] and llm_ask.last_choice(store) == {Species.MINUTES}
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    bar = win.llm.bar
    assert bar is not None and not any(b.isEnabled() for b in bar.buttons.values()) and bar.note.isVisibleTo(win)
    ata = win.llm.text_tabs["minutes"]
    assert not ata.empty.action.isEnabled() and ata.empty.action.toolTip() == llm_ask.NO_KEY_TIP
    popup = Chooser(Mode.NOW)
    sent = llm_ask.ask(store, mid, ["minutes"], "teste", lambda q: popup(None, q))
    assert sent is not None and sent.job_id is None and "chave" in sent.message and popup.seen == [] and _asked(store, mid) == []
    change = ChangeConfirm("Reprocessar?", "Reprocessar", store)
    qtbot.addWidget(change)
    assert change.boxes is not None and not any(b.isEnabled() for b in change.boxes.boxes.values())


# ---------------------------------------------------------------- a troca de idioma (A-49)
def test_the_language_change_with_a_box_asks_and_binds_the_request_to_the_reprocessing(qtbot, platform, store, monkeypatch):
    from listener.pipeline import reprocess
    from listener.ui.meeting import MeetingWindow

    mid = _record(platform, store)
    _provider_ready(store)
    _pipeline(store, mid)
    _run_llm(store)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    lang = win.language_bar
    boxes_for: list[Any] = []

    def confirm(_parent: Any, _text: str, _yes: str, boxes: Any) -> frozenset[Species]:
        boxes_for.append(boxes)
        return frozenset({Species.MINUTES})

    lang.confirm = confirm
    popup = lang.choose = Chooser(Mode.NOW)
    handed = _spy_after(monkeypatch)
    lang.button.click()
    job = reprocess.pending_language(store, mid)
    assert job is not None and boxes_for == [store] and popup.seen[0][1].lines[0] == llm_ask.RERUN     # R-04: roda de novo
    assert handed == [job[0]]
    assert _asked(store, mid) == [{"tasks": ["minutes"], "mode": "now", "origin": "troca de idioma", "after_job_id": job[0]}]
    assert "sai quando o processamento da reunião terminar" in win.message.text()
    lang.button.click()                                                              # desfazer: sem caixas nem pedido
    assert boxes_for[-1] is None and len(_asked(store, mid)) == 1 and len(popup.seen) == 1


def test_the_language_boxes_keep_the_same_last_choice(qtbot, store):
    from listener.ui.meeting_language import ChangeConfirm

    _provider_ready(store)
    llm_ask.save_choice(store, {Species.SUMMARY})
    dlg = ChangeConfirm("Reprocessar em Inglês?", "Reprocessar em Inglês (en)", store)
    qtbot.addWidget(dlg)
    assert dlg.boxes is not None and dlg.boxes.boxes[Species.SUMMARY].isChecked() and dlg.btn_cancel.isDefault()
    dlg.boxes.boxes[Species.MINUTES].setChecked(True)
    assert dlg.take_choice() == {Species.MINUTES, Species.SUMMARY}
    assert llm_ask.last_choice(store) == {Species.SUMMARY}                           # grava quem pede, depois do popup
    plain = ChangeConfirm("Desfazer?", "Desfazer a troca")
    qtbot.addWidget(plain)
    assert plain.boxes is None and plain.take_choice() == frozenset()


# ---------------------------------------------------------------- o warm-up (U-091, A-21)
def test_the_partial_box_comes_from_the_settings_needs_the_live_and_reaches_live_partial(qtbot, store, platform):
    from listener.ui.warmup import WarmupDialog

    _provider_ready(store)
    store.set_setting("live.enabled", True)
    with_flags = store.upsert_preset("Com ao vivo", None, None, "system", None, live_enabled=True, live_names=False)
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    box = dlg.partial_check
    assert box.text() == "Resumo parcial" and not box.isChecked() and box.isEnabled()          # P-06: desmarcada
    assert dlg.result_config()[1]["live_partial"] is False
    box.setChecked(True)
    assert dlg.result_config()[1]["live_partial"] is True
    dlg.preset_combo.setCurrentIndex(dlg.preset_combo.findData(with_flags))                      # o preset não a muda
    assert box.isChecked() and dlg.result_config()[1]["live_partial"] is True
    dlg.live_check.setChecked(False)                                                             # sem legenda, sem parcial
    assert not box.isEnabled() and dlg.result_config()[1]["live_partial"] is False
    dlg.reject()
    store.set_setting("live.partial", True)
    other = WarmupDialog(platform, store)
    qtbot.addWidget(other)
    assert other.partial_check.isChecked() and other.result_config()[1]["live_partial"] is True
    other.reject()

