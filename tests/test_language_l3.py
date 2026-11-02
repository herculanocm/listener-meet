"""F5.11 L3, linguagem e consistência: o plural de verdade, com o teste estático (U-109, A-27); os textos da revisão e da
fila sem "LLM" nem "pyannote" (U-106, A-28) e o vocabulário à vista ("IA", "custo", "Resposta", "Microfone: você", a busca
híbrida fora da vista); "Cancelar" só em diálogo (U-107); "Você" na tela pela pessoa marcada como você (U-108, A-26); o
rodapé da transcrição que diz o que mostra (U-117); e o alerta do widget em até duas linhas, medido com a fonte do app
(U-116, A-33)."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from listener.model import ClusterStatus, JobKind
from listener.pipeline.review import ReviewItem, TranscriptRow

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parent.parent
PLURAL = re.compile(r"[a-zà-ú]\((s|es|ões|is|ns)\)")
LOG_CALLS = {"debug", "info", "warning", "error", "exception", "critical"}
LLM_BOUND = {"rag/agent_results.py": {"search_item"}}      # o texto que vai à LLM não muda (CP11, fora de escopo da F5.11)
SEGOE = Path("C:/Windows/Fonts/SegUIVar.ttf")


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _user_strings(path: Path) -> list[tuple[int, str]]:
    """As strings de um módulo fora das docstrings, dos argumentos de log e das funções cujo texto vai à LLM (o que pode chegar
    à tela)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = LLM_BOUND.get(path.relative_to(ROOT / "listener").as_posix(), set())
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in bound:
            skip.update(id(n) for n in ast.walk(node) if isinstance(n, ast.Constant))
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_CALLS:
            skip.update(id(n) for n in ast.walk(node) if isinstance(n, ast.Constant))
    return [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]


# ---------------------------------------------------------------- U-109 e A-27: o plural
def test_no_parenthesized_plural_left_in_the_app():
    """A-27: nenhum "fala(s)", "voz(es)" ou "reunião(ões)" em `listener/` (o log, as docstrings e o texto que vai à LLM ficam
    de fora); a unidade " (s)" das Configurações não casa, pelo espaço antes do parêntese."""
    found = [f"{p.relative_to(ROOT)}:{line}" for p in sorted((ROOT / "listener").rglob("*.py"))
             for line, text in _user_strings(p) if PLURAL.search(text)]
    assert found == []
    assert PLURAL.search("fala(s)") and PLURAL.search("reunião(ões)") and PLURAL.search("clicável(is)")
    assert not PLURAL.search("Intervalo do resumo parcial (s)")


def test_the_queue_shows_dates_as_dd_mm_yyyy(store):
    from listener.ui.queue_rows import job_row_texts

    mid = store.create_meeting("Diretoria", "teams", None, None)
    store.finish_meeting(mid, 60_000)
    jid = store.jobs.enqueue(mid)
    texts = job_row_texts(store.jobs.get(jid), store.get_meeting(mid), "{}")
    assert re.fullmatch(r"Diretoria \(\d{2}/\d{2}/\d{4} \d{2}:\d{2}\)", texts[0]), texts[0]


# ---------------------------------------------------------------- U-106 e A-28: o vocabulário
def test_review_and_queue_texts_have_no_llm_nor_pyannote():
    """A-28: os textos de `pipeline/review.py` (o cartão e as recusas) e os rótulos e a Ajuda da fila em `model.py`."""
    from listener.model import JOB_KIND_HELP, JOB_KIND_LABELS, JOB_STATUS_HELP, JOB_STEP_HELP, JOB_STEP_LABELS

    base = ReviewItem(1, "SPK_1", "R", ClusterStatus.AMBIGUOUS, 60_000, 3, "Ana", 0.7, 0.03, None, True, 4, "Rodrigo", 0.8,
                      ((9, "valeu, Rodrigo", 12_000),))
    items = [base, replace(base, status=ClusterStatus.UNNAMED), replace(base, status=ClusterStatus.UNNAMED, person_id=None, person_name=""),
             replace(base, status=ClusterStatus.AUTO), replace(base, status=ClusterStatus.CONFIRMED), replace(base, merged_into=2),
             replace(base, status=ClusterStatus.IGNORED)]
    shown = [t for it in items for t in (it.summary(), it.llm_text())]
    shown += [text for _line, text in _user_strings(ROOT / "listener" / "pipeline" / "review.py")]
    shown += [*JOB_KIND_LABELS.values(), *JOB_KIND_HELP.values(), *JOB_STEP_LABELS.values(), *JOB_STEP_HELP.values(),
              *JOB_STATUS_HELP.values()]
    assert [t for t in shown if "LLM" in t or "pyannote" in t.casefold()] == []
    assert base.summary() == "pela voz: Ana (similaridade 0,70, margem 0,03) · pelo contexto: Rodrigo (0,80) “valeu, Rodrigo” aos 00:12"
    assert (JOB_STEP_LABELS["minutes"], JOB_KIND_LABELS[JobKind.LLM], JOB_STEP_LABELS["waiting"]) == ("IA: ata", "IA", "espera a gravação")


def test_the_visible_words_are_the_owners(qtbot, store, platform, monkeypatch):
    from listener.llm import chat_task
    from listener.model import ChatRole
    from listener.ui import llm_ask
    from listener.ui.llm_tabs import cost_badge, llm_done_text
    from listener.ui.queue_rows import job_row_texts
    from listener.ui.warmup import WarmupDialog

    mid = store.create_meeting("Diretoria", "teams", None, None)
    store.finish_meeting(mid, 60_000)
    jid = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["identify", "minutes"], "parent_job_id": 1})
    store.jobs.claim(pid=1, kinds=("llm",))
    store.jobs.set_progress(jid, "minutes", 0.5, {"llm": {"cost_usd": 0.12, "done": ["identify"], "calls": 1, "skipped": {}}})
    texts = job_row_texts(store.jobs.get(jid), store.get_meeting(mid), "{}")
    assert (texts[2], texts[4]) == ("IA", "IA: ata (2 de 2)")
    assert llm_done_text({"timings_json": json.dumps({"llm": {"done": ["title"], "cost_usd": 0}})}) == "IA concluída."
    monkeypatch.setattr(store.llm_calls, "cost_for_meeting", lambda _m: (0.1234, False))
    assert cost_badge(store, mid) == "custo US$ 0,12"                                 # "custo", sem "da LLM"
    store.chat.add(mid, ChatRole.USER, "Quem decidiu?")
    store.chat.add(mid, ChatRole.ASSISTANT, "A Ana.", "gemini", "m")
    assert "<b>Resposta:</b> A Ana." in chat_task.history_html(store, mid) and "LLM" not in chat_task.history_html(store, mid)
    assert llm_ask.NO_KEY_TIP.startswith("Sem chave da IA") and "LLM" not in llm_ask.NO_SPEECH_TIP
    store.seed_default_presets()
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    assert (dlg.mic_box.title(), dlg.src_box.title()) == ("Microfone: você", "Saída: quem você ouve")
    dlg.room_check.setChecked(True)                                                  # A-41: a sala presencial
    assert dlg.mic_box.title() == "Microfone: você e a sala"
    dlg.room_check.setChecked(False)
    assert dlg.mic_box.title() == "Microfone: você"


# ---------------------------------------------------------------- U-107: "Cancelar" só fecha diálogo
def test_cancel_is_only_for_dialogs_and_the_discard_asks_with_its_own_words(qtbot, qapp, store):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from listener.ui.history import HistoryWindow
    from listener.ui.icons import ICON_PROP
    from listener.ui.queue import BUTTON_HELP, QueueDialog
    from listener.ui.widget import RecordingWidget
    from listener.ui.widget_parts import confirm_discard

    w = RecordingWidget(live_line=False, shortcuts={})
    qtbot.addWidget(w)
    assert w.btn_cancel.text() == "Descartar gravação" and str(w.btn_cancel.property(ICON_PROP)).startswith("delete:")
    queue = QueueDialog(store)
    qtbot.addWidget(queue)
    assert queue.btn_cancel.text() == "Cancelar processamento" and "Cancelar processamento" in BUTTON_HELP
    assert "Cancelar" not in BUTTON_HELP
    hist = HistoryWindow(store)
    qtbot.addWidget(hist)
    try:
        assert hist.page.btn_stop.text() == "Parar"                                   # como no chat da reunião
    finally:
        hist.page.shutdown()
    seen: list[tuple[str, str, Any]] = []

    def answer(click: str) -> None:
        box = next(b for b in QApplication.topLevelWidgets() if isinstance(b, QMessageBox) and b.isVisible())
        yes, no = box.button(QMessageBox.StandardButton.Yes), box.button(QMessageBox.StandardButton.No)
        seen.append((yes.text(), no.text(), box.standardButton(box.defaultButton())))
        (yes if click == "descartar" else no).click()

    for click, discarded in (("continuar", False), ("descartar", True)):
        QTimer.singleShot(0, lambda c=click: answer(c))
        assert confirm_discard(None) is discarded
    assert seen == [("Descartar", "Continuar gravando", QMessageBox.StandardButton.No)] * 2   # D-076: o Enter continua


# ---------------------------------------------------------------- U-108: "Você" na tela
def test_you_on_screen_and_the_name_everywhere_else(qapp):
    from PySide6.QtCore import Qt

    from listener.ui.live_text import YOU, caption_note, you_label
    from listener.ui.speech_delegate import ROLE_ROLE, SPEAKER_ROLE
    from listener.ui.transcript import SegmentsModel

    mine = TranscriptRow(1, "L", 0, 1_000, "Herculano", "Gerente", "bom dia", 0, person_id=7)
    other = TranscriptRow(2, "R", 1_000, 2_000, "Ana", "CFO", "olá", 900, person_id=8)
    model = SegmentsModel([mine, other], me_id=7)
    rows = [(model.data(model.index(i), Qt.ItemDataRole.DisplayRole), model.data(model.index(i), SPEAKER_ROLE),
             model.data(model.index(i), ROLE_ROLE)) for i in range(2)]
    assert rows == [("[00:00] Você: bom dia", YOU, ""), ("[00:01] Ana — CFO: olá", "Ana", "CFO")]
    assert mine.line == "[00:00] Herculano — Gerente: bom dia"                        # a exportação e a citação: o nome
    caption = {"person_id": 7, "label": "Herculano — Gerente", "channel": "L", "text": "bom dia"}
    assert you_label(caption, 7, in_room=False) == YOU and caption_note(caption).startswith("Herculano")
    assert you_label({"label": None, "channel": "L"}, None, in_room=False) == YOU      # nomes desligados, fora da sala
    assert you_label({"label": None, "channel": "L"}, None, in_room=True) is None     # na sala, o microfone ouve todos
    assert you_label({"label": "Ana", "channel": "R", "person_id": 8}, 7, in_room=False) == "Ana"


# ---------------------------------------------------------------- U-117: o rodapé da transcrição
def test_the_transcript_footer_says_what_it_shows(qtbot, store, platform):
    from listener.ui.transcript import TranscriptTab
    from listener.ui.transcript_footer import status_text
    from tests.test_review import _processed

    assert status_text(1, 0, True) == "1 fala da transcrição original. Clique numa fala para ouvir daquele ponto."
    assert status_text(3, 2, True).startswith("3 falas, 2 revisadas pela IA: mostrando a transcrição revisada")
    assert status_text(3, 1, False).startswith("3 falas: mostrando a transcrição original (1 revisada pela IA)")
    mid, _items = _processed(platform, store)
    tab = TranscriptTab(store, mid)
    qtbot.addWidget(tab)
    tab.show()
    assert not tab.toggle.isVisible() and "da transcrição original" in tab.status.text()      # sem revisada, sem caixa
    store._x("UPDATE segments SET text_llm = 'Bom dia, pessoal.' WHERE id = (SELECT MIN(id) FROM segments WHERE meeting_id = ?)", (mid,))
    tab.reload()
    assert tab.toggle.isVisible() and "1 revisada pela IA: mostrando a transcrição revisada" in tab.status.text()


# ---------------------------------------------------------------- U-116 e A-33: o alerta do widget
def _alerts() -> list[tuple[str, str]]:
    """Cada texto do alerta com o botão dele (os valores variáveis no tamanho típico)."""
    from listener.ui import recording_alerts as ra

    dev = ra.DEVICES_BUTTON
    return [*((t, dev) for t in (*ra.LOST_TEXT.values(), *ra.NEVER_OPENED_TEXT.values(), *ra.GONE_TEXT.values(),
                                 *ra.BACK_TEXT.values(), ra.APP_LEFT_TEXT, ra.FALLBACK_TEXT, ra.NO_SIGNAL_TEXT,
                                 ra.NO_SIGNAL_COMM_TEXT.format(name="Fone de ouvido"), ra.MIC_SILENT_TEXT.format(seconds=30))),
            (ra.ECHO_TEXT, ra.ROOM_BUTTON), (ra.db_stuck_text(12, 3), ""), (ra.db_slow_text(3), ""), ("novo: Fone de ouvido", "Usar")]


# a largura do texto do alerta por botão, medida no widget real no Windows (01/11, 150 %): o resto é do ícone e do botão
ALERT_WIDTH = {"Dispositivos…": 207, "Marcar como sala": 182, "Usar": 227, "": 314}
MEASURE = """
import json, sys
from PySide6.QtGui import QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QApplication
app = QApplication([])
QFontDatabase.addApplicationFont(sys.argv[1])
from listener.ui.theme import font_for
from listener.ui.widget_parts import wrap_elided
fm = QFontMetrics(font_for("caption"))
print(json.dumps([t for t, w in json.loads(sys.argv[2]) if wrap_elided(t, fm, w, 2) != t]))
"""


@pytest.mark.skipif(not SEGOE.exists(), reason="a fonte do app é a do Windows (CP15: a medida vale no alvo)")
def test_every_widget_alert_fits_in_two_lines_with_the_app_font():
    """A-33: medido num processo filho com a Segoe UI Variable carregada (o offscreen da suíte não tem fonte, e carregar uma
    no processo dela mudaria a medida dos outros testes)."""
    cases = [(text, ALERT_WIDTH[button]) for text, button in _alerts()]
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    out = subprocess.run([sys.executable, "-c", MEASURE, str(SEGOE), json.dumps(cases)], capture_output=True, text=True,  # noqa: S603
                         encoding="utf-8", cwd=ROOT, env=env, timeout=60, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_the_alert_wraps_in_two_lines_and_cuts_only_what_does_not_fit(qtbot):
    from PySide6.QtCore import QRect, Qt

    from listener.ui.widget import RecordingWidget
    from listener.ui.widget_parts import wrap_elided

    w = RecordingWidget(live_line=False, shortcuts={})
    qtbot.addWidget(w)
    w.show()
    w.show_alert("banco lento: 3 pedaços na fila")
    assert w.alert_label.wordWrap() and w.alert_label.text() == w.alert_label.full_text
    fm = w.alert_label.fontMetrics()
    long = " ".join(["palavra"] * 60)
    cut = wrap_elided(long, fm, 200, 2)
    assert cut.endswith("…") and len(cut) < len(long)
    assert fm.boundingRect(QRect(0, 0, 200, 10_000), int(Qt.TextFlag.TextWordWrap), cut).height() <= 2 * fm.lineSpacing()
    assert wrap_elided("curto", fm, 200, 2) == "curto"
