"""F5.3 L2 (U-047): a fila com a coluna "Enviado" (o `created_at` na hora local; na dica, início, fim e o tempo de trabalho
pela soma do `timings_json`, A-08), a "%" como barra pintada por um delegate na cor do estado (o número com 4,5:1 ou mais
nos temas claro e escuro; a faixa do "rodando" só anda com linha rodando e a fila visível) e a Ajuda com todo tipo, estado
e etapa, na ordem em que rodam (A-09)."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from listener.model import JobKind, JobStatus, JobStep
from listener.store import Store

LIGHT, DARK = ("#ffffff", "#000000"), ("#2d2d2d", "#ffffff")       # base e texto da palette() do windows11 (medidos em 27/10)
CELL_W, CELL_H = 200, 28
BACKDROP = "#123456"                                                  # o que já estava na célula: fora da barra, fica


def _meeting(store: Store, title: str = "Diretoria") -> int:
    mid = store.create_meeting(title, "teams", None, None)
    store.finish_meeting(mid, 1_800_000)
    return mid


def _timing(wall_s: float) -> dict[str, Any]:
    return {"audio_s": 60.0, "speech_s": 30.0, "wall_s": wall_s, "model": "m"}


def _six_states(store: Store) -> None:
    """Um job em cada estado (o rodando a 42 %, o pendente devolvido à fila com 30 %) e a linha agregada do índice rodando a
    55 %, como no `win_probe shots`. Cada classe de job só tem um claim vivo por vez: um tipo por claim."""
    a, b, c, d = (_meeting(store, t) for t in ("Diretoria", "Planejamento", "Comitê", "Vendas"))
    done = store.jobs.enqueue(a)
    store.jobs.claim(pid=1, kinds=("meeting",))
    store.jobs.finish(done, JobStatus.DONE, timings={"diarize": _timing(30.0)})
    error = store.jobs.enqueue(a, JobKind.LLM, {"tasks": ["identify", "minutes"]})
    store.jobs.claim(pid=2, kinds=("llm",))
    store.jobs.set_progress(error, JobStep.MINUTES, 0.5)
    store.jobs.finish(error, JobStatus.ERROR, error="ata falhou: sem rede")
    cancelled = store.jobs.enqueue(b, JobKind.REPROCESS)
    store.jobs.claim(pid=3, kinds=("reprocess",))
    store.jobs.set_progress(cancelled, JobStep.TRANSCRIBE_R, 0.6)
    store.jobs.finish(cancelled, JobStatus.CANCELLED)
    paused = store.jobs.enqueue(b, JobKind.LLM, {"tasks": ["minutes"]})
    store.jobs.claim(pid=4, kinds=("llm",))
    store.jobs.set_progress(paused, JobStep.MINUTES, 0.5)
    store.jobs.set_status(paused, JobStatus.PAUSED, JobStep.WAITING)
    running = store.jobs.enqueue(c)
    store.jobs.claim(pid=5, kinds=("meeting",))
    store.jobs.set_progress(running, JobStep.TRANSCRIBE_R, 0.42)
    pending = store.jobs.enqueue(d, JobKind.REPROCESS)
    store.jobs.claim(pid=6, kinds=("reprocess",))
    store.jobs.set_progress(pending, JobStep.DIARIZE, 0.3)
    store.jobs.finish(pending, JobStatus.ERROR, error="sem memória")
    store.jobs.retry(pending)
    index = store.jobs.enqueue_index(a, "pipeline")
    store.jobs.enqueue_index(b, "pipeline")
    store.jobs.claim(pid=7, kinds=("index",))
    store.jobs.set_progress(index, JobStep.INDEX_VECTORS, 0.55)


def _paint_cell(dlg: Any, row: int, phase: float = 0.0) -> Any:
    """A célula "%" da linha pintada pelo delegate num `QImage`, sem tela."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QStyleOptionViewItem

    from listener.ui.queue import PROGRESS_COL

    img = QImage(CELL_W, CELL_H, QImage.Format.Format_ARGB32)
    img.fill(QColor(BACKDROP))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, CELL_W, CELL_H)
    option.palette = dlg.table.palette()
    option.font = dlg.table.font()
    dlg.bar_delegate.phase = phase
    painter = QPainter(img)
    dlg.bar_delegate.paint(painter, option, dlg.table.model().index(row, PROGRESS_COL))
    painter.end()
    return img


# ---------------------------------------------------------------- "Enviado" e os tempos (item 7)
def test_sent_is_the_created_at_and_the_tip_sums_the_work_of_every_attempt(qtbot, store):
    from listener.ui.queue import SENT_COL, TIME_FORMAT, QueueDialog, job_row_texts, job_times_tip

    jid = store.jobs.enqueue(_meeting(store))
    job = store.jobs.get(jid)
    sent = datetime.fromisoformat(job["created_at"]).strftime(TIME_FORMAT)        # `_now` grava a hora local com o fuso
    assert job_row_texts(job, None, "{}")[SENT_COL] == sent
    assert job_times_tip(job).splitlines() == [f"Enviado: {sent}", "Início: —", "Fim: —", "Tempo de trabalho: —"]
    store.jobs.claim(pid=1)
    store.jobs.set_progress(jid, JobStep.DIARIZE, 0.4, {"diarize": _timing(12.4), "embed": _timing(3.0)})
    store.jobs.finish(jid, JobStatus.ERROR, error="sem memória", timings={"diarize": _timing(12.4), "embed": _timing(3.0)})
    first_start = store.jobs.get(jid)["started_at"]
    assert store.jobs.retry(jid)
    store.jobs.claim(pid=2)
    store.jobs.finish(jid, JobStatus.DONE, timings={"diarize": _timing(12.4), "embed": _timing(3.0), "transcribe_r": _timing(20.0)})
    job = store.jobs.get(jid)
    assert job["started_at"] == first_start                                         # o "Tentar de novo" não zera o início (A-08)
    tip = job_times_tip(job)
    assert tip.splitlines() == [f"Enviado: {sent}", f"Início: {datetime.fromisoformat(first_start).strftime(TIME_FORMAT)}",
                                f"Fim: {datetime.fromisoformat(job['finished_at']).strftime(TIME_FORMAT)}", "Tempo de trabalho: 35 s"]
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    dlg._timer.stop()                                                                # o banco fecha no fim do teste
    item = dlg.table.item(0, SENT_COL)
    assert dlg.table.horizontalHeaderItem(SENT_COL).text() == "Enviado" and item.text() == sent and item.toolTip() == tip


def test_local_time_and_work_time_of_each_timings_format():
    from listener.ui.queue import TIME_FORMAT, fmt_work, local_time, work_seconds

    assert local_time("2026-10-27T12:05:07.000+00:00") == datetime(2026, 10, 27, 12, 5, 7, tzinfo=UTC).astimezone().strftime(TIME_FORMAT)
    assert local_time("2026-10-27T09:05:07") == "27/10 09:05:07"                    # sem fuso: já é a hora local
    assert local_time(None) == local_time("") == local_time("ontem") == ""
    assert [fmt_work(s) for s in (None, 0.4, 45, 187.2, 3900)] == ["", "menos de 1 s", "45 s", "3 min 07 s", "1 h 05 min"]
    pipeline = {"diarize": _timing(600.0), "embed": _timing(30.5), "match": _timing(0.5), "transcribe_r": _timing(400.0)}
    assert work_seconds(json.dumps(pipeline)) == pytest.approx(1031.0)
    llm = {"identify": _timing(10.0), "minutes": _timing(30.0), "executive": _timing(8.0), "pre_summary": _timing(5.0),
           "title": _timing(9.5), "revise": _timing(40.0), "llm": {"cost_usd": 0.12, "calls": 6, "done": ["identify"]}}
    assert work_seconds(json.dumps(llm)) == pytest.approx(10 + 30 + 9.5 + 40)      # executivo, pré-resumo e título juntos: o maior
    index = {"index_text": _timing(0.4), "index_vectors": _timing(2.1), "index": {"chunks": 12, "cost_usd": 0.0, "note": ""}}
    assert work_seconds(json.dumps(index)) == pytest.approx(2.5)
    assert work_seconds(None) is None and work_seconds("{}") is None and work_seconds("[1]") is None
    assert work_seconds("não é json") is None and work_seconds(json.dumps({"llm": {"cost_usd": 1.0}})) is None
    assert work_seconds('{"diarize": {"wall_s": NaN}, "embed": {"wall_s": 2.0}}') == pytest.approx(2.0)     # o JSON aceita NaN


def test_index_row_sent_tip_and_bar_follow_the_running_index_job(qtbot, store):
    from listener.ui.queue import PROGRESS_COL, SENT_COL, TIME_FORMAT, QueueDialog, job_times_tip
    from listener.ui.queue_bar import PROGRESS_ROLE, STATUS_ROLE

    a, b = _meeting(store, "Diretoria"), _meeting(store, "Comitê")
    i1, i2 = store.jobs.enqueue_index(a, "pipeline"), store.jobs.enqueue_index(b, "sweep")
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    dlg._timer.stop()
    sent, bar = dlg.table.item(0, SENT_COL), dlg.table.item(0, PROGRESS_COL)
    assert sent.text() == sent.toolTip() == "" and bar.text() == "0 %"                # sem job rodando: vazio
    assert bar.data(PROGRESS_ROLE) == 0.0 and bar.data(STATUS_ROLE) == "pendente"
    store.jobs.claim(pid=1, kinds=("index",))
    store.jobs.set_progress(i1, JobStep.INDEX_VECTORS, 0.55, {"index_text": _timing(0.4), "index": {"chunks": 3}})
    dlg.refresh()
    running = store.jobs.get(i1)
    sent, bar = dlg.table.item(0, SENT_COL), dlg.table.item(0, PROGRESS_COL)
    assert sent.text() == datetime.fromisoformat(running["created_at"]).strftime(TIME_FORMAT)
    assert sent.toolTip() == job_times_tip(running) and "Tempo de trabalho: menos de 1 s" in sent.toolTip()
    assert bar.text() == "55 %" and bar.data(PROGRESS_ROLE) == pytest.approx(0.55) and bar.data(STATUS_ROLE) == "rodando"
    store.jobs.finish(i1, JobStatus.ERROR, error="sem rede")
    store.jobs.request_cancel(i2)
    dlg.refresh()
    bar = dlg.table.item(0, PROGRESS_COL)
    assert dlg.table.item(0, 0).text() == "Índice: 0 reuniões (1 com erro)" and bar.text() == "" and bar.data(PROGRESS_ROLE) is None
    img = _paint_cell(dlg, 0)                                                        # só erro: nada de barra
    assert {img.pixel(x, y) for x in range(0, CELL_W, 7) for y in range(0, CELL_H, 3)} == {img.pixel(0, 0)}


# ---------------------------------------------------------------- a barra na cor do estado (item 8)
@pytest.mark.parametrize(("base", "text"), [LIGHT, DARK], ids=["claro", "escuro"])
def test_bar_colors_per_state_keep_the_number_readable_in_both_themes(base, text):
    from listener.ui.common import BAND_OPACITY, JOB_STATUS_COLOR_NAMES, JOB_STATUS_COLORS, bar_colors, contrast, mix

    assert set(JOB_STATUS_COLORS) == set(JOB_STATUS_COLOR_NAMES) == set(JobStatus)
    looks = set()
    for status in JobStatus:
        c = bar_colors(status, base, text)
        assert contrast(c.text, c.fill) >= 4.5 and contrast(c.track_text, c.track) >= 4.5, status
        assert contrast(c.text, mix(c.fill, c.band, BAND_OPACITY)) >= contrast(c.text, c.fill), status          # a faixa só ajuda
        assert contrast(c.track_text, mix(c.track, c.track_band, BAND_OPACITY)) >= contrast(c.track_text, c.track), status
        if c.hatch:
            assert contrast(c.text, c.hatch) >= 4.5
        looks.add((c.fill, c.hatch))
    assert len(looks) == len(JobStatus)                                              # cada estado com a sua cor
    assert bar_colors(JobStatus.CANCELLED, base, text).hatch is not None             # o cancelado, hachurado
    assert bar_colors("???", base, text).fill == JOB_STATUS_COLORS[JobStatus.PENDING]
    assert contrast(bar_colors(JobStatus.RUNNING, base, text).track, base) > 1.3     # o trilho aparece na tabela


def test_black_or_white_text_always_reaches_4_5_to_1():
    from listener.ui.common import BLACK, WHITE, contrast, luminance, text_on

    assert luminance(BLACK) == 0.0 and luminance(WHITE) == pytest.approx(1.0) and contrast(BLACK, WHITE) == pytest.approx(21.0)
    for v in range(256):
        for color in (f"#{v:02x}{v:02x}{v:02x}", f"#{v:02x}0000", f"#00{v:02x}00", f"#0000{v:02x}", f"#{v:02x}{v:02x}00"):
            assert text_on(color) in (BLACK, WHITE) and contrast(text_on(color), color) >= 4.5, color


def test_delegate_paints_each_state_in_its_color_on_a_qimage(qtbot, store):
    from PySide6.QtGui import QColor, QPalette

    from listener.ui.common import bar_colors, contrast
    from listener.ui.queue import PROGRESS_COL, STATE_COL, QueueDialog
    from listener.ui.queue_bar import BAND_MIN_PX, BAND_SHARE, MARGIN_X, PROGRESS_ROLE

    _six_states(store)
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    dlg._timer.stop()
    rows = [(r, dlg.table.item(r, STATE_COL).text()) for r in range(dlg.table.rowCount())]
    assert sorted({s for _, s in rows}) == sorted(JobStatus) and rows[-1][1] == "rodando"            # a última é a do índice
    pal = dlg.table.palette()
    base, text = pal.color(QPalette.ColorRole.Base).name(), pal.color(QPalette.ColorRole.Text).name()
    mid_y = CELL_H // 2
    for row, status in rows:
        colors = bar_colors(status, base, text)
        img = _paint_cell(dlg, row)
        left = {QColor(img.pixel(x, mid_y)).name() for x in range(MARGIN_X + 2, MARGIN_X + 14)}     # o número fica no meio
        assert left == ({colors.fill, colors.hatch} if colors.hatch else {colors.fill}), status
        full = dlg.table.item(row, PROGRESS_COL).data(PROGRESS_ROLE) >= 1.0
        assert QColor(img.pixel(CELL_W - MARGIN_X - 2, mid_y)).name() == (colors.fill if full else colors.track), status
        assert QColor(img.pixel(1, 1)).name() == BACKDROP                                            # a margem não é pintada
    running = next(r for r, s in rows if s == JobStatus.RUNNING)
    bar_w = CELL_W - 2 * MARGIN_X
    width = max(BAND_MIN_PX, bar_w * BAND_SHARE)
    phase = (16 + width / 2) / (bar_w + width)                                                       # o meio da faixa em x = 16
    colors = bar_colors(JobStatus.RUNNING, base, text)
    glow = QColor(_paint_cell(dlg, running, phase).pixel(MARGIN_X + 16, mid_y)).name()
    assert glow != colors.fill and contrast(colors.text, glow) >= contrast(colors.text, colors.fill)   # a faixa aparece e ajuda
    assert not dlg.grab().isNull()                                                                   # a tabela inteira pinta


def test_band_moves_only_with_a_running_row_and_the_queue_visible(qtbot, store):
    from listener.ui.queue import BAND_MS, QueueDialog

    mid = _meeting(store)
    jid = store.jobs.enqueue(mid)
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    dlg._timer.stop()                                                                # o refresh fica na mão do teste
    dlg.show()
    assert not dlg._band_timer.isActive()                                            # visível, mas nada rodando
    store.jobs.claim(pid=1)
    store.jobs.set_progress(jid, JobStep.DIARIZE, 0.3)
    dlg.refresh()
    assert dlg._band_timer.isActive() and dlg._band_timer.interval() == BAND_MS
    start = dlg.bar_delegate.phase
    qtbot.waitUntil(lambda: dlg.bar_delegate.phase != start, timeout=2000)
    dlg.hide()
    assert not dlg._band_timer.isActive()
    dlg.refresh()
    assert not dlg._band_timer.isActive()                                            # o refresh com a fila escondida não religa
    dlg.show()
    assert dlg._band_timer.isActive()
    store.jobs.finish(jid, JobStatus.DONE)
    dlg.refresh()
    assert not dlg._band_timer.isActive()
    store.jobs.enqueue_index(mid, "pipeline")
    store.jobs.claim(pid=2, kinds=("index",))
    dlg.refresh()
    assert dlg._band_timer.isActive()                                                # a linha agregada do índice também anda
    dlg.hide()


# ---------------------------------------------------------------- a Ajuda (item 9)
def test_help_covers_every_kind_status_and_step_in_the_order_they_run(qtbot, store, caplog):
    from listener.model import JOB_KIND_HELP, JOB_KIND_LABELS, JOB_STATUS_HELP, JOB_STEP_HELP, JOB_STEP_LABELS
    from listener.ui.queue import BUTTON_HELP, COLUMN_HELP, QueueDialog
    from listener.ui.queue_help import kind_steps, queue_help_html

    assert set(JOB_KIND_LABELS) == set(JOB_KIND_HELP) == set(JobKind)
    assert set(JOB_STATUS_HELP) == set(JobStatus)
    assert set(JOB_STEP_HELP) == set(JOB_STEP_LABELS) == set(JobStep)
    assert [s for g in kind_steps(JobKind.MEETING) for s in g] == [JobStep.DOWNLOAD, JobStep.DIARIZE, JobStep.EMBED, JobStep.MATCH,
                                                                   JobStep.TRANSCRIBE_R, JobStep.TRANSCRIBE_L, JobStep.SEGMENTS]
    assert kind_steps(JobKind.REPROCESS) == kind_steps(JobKind.MEETING) and kind_steps(JobKind.EMBED_RANGE) == ()
    assert kind_steps(JobKind.LLM) == ((JobStep.TEST,), (JobStep.IDENTIFY,), (JobStep.MINUTES,),
                                       (JobStep.EXECUTIVE, JobStep.PRE_SUMMARY, JobStep.TITLE), (JobStep.REVISE,))
    assert kind_steps(JobKind.INDEX) == ((JobStep.INDEX_TEXT,), (JobStep.INDEX_VECTORS,))
    assert {s for k in JobKind for g in kind_steps(k) for s in g} | {JobStep.WAITING} == set(JobStep)
    page = queue_help_html(BUTTON_HELP, COLUMN_HELP)
    for kind in JobKind:
        assert f"<b>{JOB_KIND_LABELS[kind]}</b>" in page and JOB_KIND_HELP[kind] in page, kind
    for status in JobStatus:
        assert f"<b>{status}</b>" in page and JOB_STATUS_HELP[status] in page, status
    for step in JobStep:
        assert page.count(f"<b>{JOB_STEP_LABELS[step]}</b>") == 1 and JOB_STEP_HELP[step] in page, step
    order = [s for k in (JobKind.MEETING, JobKind.LLM, JobKind.INDEX) for g in kind_steps(k) for s in g]
    positions = [page.index(f"<b>{JOB_STEP_LABELS[s]}</b>") for s in order]
    assert positions == sorted(positions)                                            # na ordem em que rodam
    assert "F6: ainda não roda" in page and all(f"<b>{name}</b>" in page for name in (*BUTTON_HELP, *COLUMN_HELP))
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    dlg._timer.stop()
    shown = [b.text() for b in (dlg.btn_pause, dlg.btn_cancel, dlg.btn_retry, dlg.btn_reprocess, dlg.btn_review, dlg.btn_open)]
    assert shown == list(BUTTON_HELP)                                                # todo botão, com o nome da tela
    with caplog.at_level(logging.INFO, logger="listener.ui.queue"):
        dlg.btn_help.click()
    help_dialog = dlg._help
    assert help_dialog is not None and help_dialog.isVisible() and "fila: ajuda aberta" in caplog.text
    assert "separando vozes" in help_dialog.view.toPlainText() and dlg.open_help() is help_dialog   # reabrir traz a mesma
    help_dialog.close()
