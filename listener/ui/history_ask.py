"""A conversa de "Perguntar às reuniões" do Histórico (F5 item 11, U-027, R7-03, F5.3 A-12), fora do `ui/history.py` (F5.10,
D7: só a mudança de lugar, antes da feature; no padrão do `SearchMixin`). `AskMixin` é parte da `HistoryPage`: lê dela o banco,
os filtros, a conversa escolhida e os widgets da aba (`ui/history_chat.py`) e abre a reunião pelo `_open` dela.

F5.10 (Interface, D6, D7): a pergunta vai a "Perguntar à base" (`rag.agent.run_agent`), com os filtros da aba Reuniões e o
texto deles; durante, "Consultando a base… rodada 2 · 7 consultas" (o progresso pelo `on_progress` do `run_stream`) e
Cancelar; na resposta, o "Como cheguei aqui" recolhido (`rag/trace.py`), que abre e fecha no clique, com "Copiar SQL" e "Ver
dados" (U-097, `ui/ask_data.py`, a consulta refeita numa `Task`); no estado vazio, sugestões como links
`listener://ask?q=` (U-096). O log leva o tamanho da pergunta e a conversa, nunca o texto."""
from __future__ import annotations

import functools
import html
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, quote, urlsplit

import shiboken6
from PySide6.QtCore import QPoint, QThread, QUrl
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QTextBrowser, QWidget

from ..config.defaults import SPECS
from ..llm.costs import fmt_usd
from ..log import get_logger
from ..model import ChatRole
from ..rag import agent, chat_global, citations, export, trace
from . import ask_data
from .common import copy_rich
from .tasks import StreamTask, run_stream
from .theme import Tone, tone

if TYPE_CHECKING:
    from collections import deque

    from ..store import Store
    from ..store.rag import SearchFilters

log = get_logger("ui.history")

CHAT_EMPTY = ("<p class='note'>Pergunte sobre todas as reuniões: a resposta consulta a base (contagens, datas, ações, custos) e "
              "cita a reunião e o minuto; o clique numa citação abre a reunião naquele ponto e toca. Os filtros de pessoa e "
              "período valem aqui também.</p>")
ASK_SCHEME = "listener://ask"


def suggestions(store: Store) -> list[str]:
    """U-096: uma contagem com uma pessoa (a primeira cadastrada que não é você), as ações atrasadas por dono e o custo."""
    other = next((p for p in store.people.list_people() if not p.get("is_me")), None)
    first = [f"Quantas reuniões tive com {other['name']} este mês?"] if other is not None else []
    return [*first, "Quais ações estão atrasadas, por dono?", "Quanto gastei com a LLM por mês?"]


def ask_link(question: str) -> str:
    return f"{ASK_SCHEME}?q={quote(question)}"


def parse_ask_link(url: str) -> str | None:
    parts = urlsplit(url)
    if f"{parts.scheme}://{parts.netloc}" != ASK_SCHEME:
        return None
    return (parse_qs(parts.query).get("q") or [""])[0].strip() or None


def empty_html(store: Store) -> str:
    links = " · ".join(f'<a href="{html.escape(ask_link(q))}">{html.escape(q)}</a>' for q in suggestions(store))
    return f"{CHAT_EMPTY}<p class='note'>Experimente: {links}</p>"


def done_text(ans: agent.AgentAnswer) -> str:
    """A linha de estado da resposta: citações com link, consultas e rodadas, a busca, o custo e onde parou."""
    steps = ans.trace
    searched = [i for r in steps.get("rodadas") or () for i in r.get("itens") or () if i.get("tipo") == "busca" and i.get("status") == "ok"]
    how = ("busca híbrida" if all(i.get("hibrida") for i in searched) else "busca por palavras") if searched else ""
    cost = fmt_usd(ans.cost_usd) if ans.cost_usd is not None else ("sem preço cadastrado" if steps.get("sem_preco") else "")
    stop = str(steps.get("parou") or "")
    rounds = f"{steps.get('consultas', 0)} consulta(s) em {len(steps.get('rodadas') or ())} rodada(s)"
    guard = f"parou: {trace.STOPS[stop]}" if stop != "resposta" and stop in trace.STOPS else ""
    parts = [f"{ans.linked} citação(ões) com link", rounds, how, cost, guard, "interrompida" if ans.cancelled else ""]
    return " · ".join(p for p in parts if p)


class AskMixin:
    """A conversa da aba "Perguntar às reuniões": as conversas guardadas, a pergunta em streaming, as citações, a avaliação
    U-027 e a cópia com citação (U-029)."""
    store: Store
    on_answer: Callable[[bool], Any] | None
    thread_id: int | None
    chat_task: StreamTask | None
    answers: deque[Any]
    copied: deque[str]
    threads: QComboBox
    chat_view: QTextBrowser
    chat_note: QLabel
    question: QLineEdit
    btn_ask: QPushButton
    btn_stop: QPushButton
    btn_new: QPushButton
    chat_status: QLabel
    _chat_stopped: bool
    _partial: str
    _base_html: str
    _closed: bool
    _tasks: set[QThread]
    _expanded: set[int]
    data_dialogs: list[ask_data.AskDataDialog]

    def filters(self) -> SearchFilters:
        raise NotImplementedError

    def filters_text(self) -> str:
        raise NotImplementedError

    def _open(self, meeting_id: int, tab: str | None = None, t_ms: int | None = None, play: bool = False) -> None:
        raise NotImplementedError

    def _watch_local(self) -> None:
        raise NotImplementedError

    def _start(self, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> QThread:
        raise NotImplementedError

    # ---------------------------------------------------------------- perguntar às reuniões (item 11, U-027)
    def _load_threads(self, select: int | None = None) -> None:
        self.threads.blockSignals(True)
        self.threads.clear()
        self.threads.addItem("Nova conversa", None)
        for t in self.store.chat.list_threads():
            when = export.fmt_date(str(t.get("last_at") or t.get("created_at") or ""))
            title = str(t.get("title") or "(sem título)")
            self.threads.addItem(f"{title} · {when}" if when else title, int(t["id"]))
        self.threads.setCurrentIndex(max(0, self.threads.findData(select)) if select is not None else 0)
        self.threads.blockSignals(False)

    def _thread_chosen(self, index: int) -> None:
        if self.chat_task is not None:
            return
        data = self.threads.itemData(index)
        self.thread_id = int(data) if data is not None else None
        self._render_thread()

    def new_thread(self) -> None:
        if self.chat_task is not None:
            return
        self.thread_id = None
        self.threads.setCurrentIndex(0)
        self._render_thread()
        self.question.setFocus()

    def _render_thread(self, keep_scroll: bool = False) -> None:
        bar = self.chat_view.verticalScrollBar()
        value = bar.value()
        self.chat_view.setHtml(chat_global.thread_html(self.store, self.thread_id, expanded=self._expanded)
                               if self.thread_id is not None else empty_html(self.store))
        if keep_scroll:
            bar.setValue(value)
        else:
            self.chat_view.moveCursor(self.chat_view.textCursor().MoveOperation.End)

    def _update_chat_note(self) -> None:
        filt = self.filters_text()
        self.chat_note.setText(f"Filtros da aba Reuniões: {filt}." if filt else "Sem filtros: a pergunta vai a todas as reuniões.")

    def ask(self) -> None:
        text = self.question.text().strip()
        if not text or self.chat_task is not None:
            return
        if self.store.providers.active_with_key("llm") is None:
            self._chat_warning("Nenhum provedor de LLM ativo com chave: informe a chave em Configurações, Chaves de acesso.")
            return
        self.question.clear()
        self._partial = ""
        self._chat_stopped = False
        self._base_html = ((chat_global.thread_html(self.store, self.thread_id) if self.thread_id is not None else "")
                           + f"<p><b>Você:</b> {html.escape(text)}</p>")
        self._render_partial()
        self.btn_ask.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_new.setEnabled(False)
        self.threads.setEnabled(False)
        self._chat_status("Consultando a base…")
        log.info("perguntar à base: pergunta enviada (%d caracteres; conversa %s)", len(text), self.thread_id)   # o texto não (LGPD)
        fn = functools.partial(agent.run_agent, self.store, self.thread_id, text, self.filters(), self.filters_text())
        task = run_stream(fn, self.store, self._on_chunk, self._on_chat_done, self._on_progress)
        self._watch_local()                              # o vetor da pergunta pode reabrir a sessão do e5 solta antes
        self.chat_task = task
        self._tasks.add(task)
        task.finished.connect(functools.partial(self._tasks.discard, task))

    def _on_progress(self, info: Any) -> None:
        """D6: "Consultando a base… rodada 2 · 7 consultas" e, na resposta, "escrevendo a resposta…"."""
        if self._closed or not isinstance(info, dict):
            return
        if info.get("respondendo"):
            self._chat_status("escrevendo a resposta…")
            return
        n, b = int(info.get("consultas") or 0), int(info.get("buscas") or 0)
        found = f"{n} consulta" + ("" if n == 1 else "s") + (f" · {b} busca" + ("" if b == 1 else "s") if b else "")
        self._chat_status(f"Consultando a base… rodada {int(info.get('rodada') or 0)} · {found}")

    def _on_chunk(self, piece: str) -> None:
        if self._closed:
            return
        self._partial += piece
        self._render_partial()
        self._chat_status(f"recebendo… {len(self._partial)} caracteres")

    def _render_partial(self) -> None:
        partial = html.escape(self._partial).replace("\n", "<br>") or "…"
        self.chat_view.setHtml(f"{self._base_html}<p><b>LLM:</b> {partial}</p>")
        self.chat_view.moveCursor(self.chat_view.textCursor().MoveOperation.End)

    def _on_chat_done(self, result: Any) -> None:
        self.chat_task = None
        if self._closed:
            return
        self.btn_ask.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_new.setEnabled(True)
        self.threads.setEnabled(True)
        if isinstance(result, ValueError):              # sem provedor, pergunta vazia, conversa apagada: aviso
            self._render_thread()
            self._chat_warning(str(result)[:1].upper() + str(result)[1:] + ".")
            self._answered_away(False)
            return
        if isinstance(result, BaseException):
            self._render_thread()
            self._chat_warning(f"A pergunta falhou: {result}")
            self._answered_away(False)
            return
        ans: agent.AgentAnswer = result
        self.answers.append(ans)
        if ans.thread_id is not None and ans.thread_id != self.thread_id:
            self.thread_id = ans.thread_id
            self._load_threads(select=ans.thread_id)
        self._render_thread()
        if ans.error:
            self._chat_warning(f"A LLM falhou: {ans.error}")
        elif ans.cancelled and ans.message_id is None:
            self._chat_status("Pergunta cancelada.")
        else:
            self._chat_status(done_text(ans))
        if ans.error or not ans.cancelled:
            self._answered_away(not ans.error)

    def _answered_away(self, ok: bool) -> None:
        """F5.3 (A-12): terminou com o Histórico fora de foco (o usuário foi para outro app ou o escondeu: a pergunta segue e
        grava): o controlador avisa. Parada pelo usuário ("Cancelar"), não."""
        if self.on_answer is not None and not self._chat_stopped and not cast(QWidget, self).window().isActiveWindow():
            self.on_answer(ok)

    def _chat_warning(self, text: str) -> None:
        self._chat_status(text, Tone.WARNING)

    def _chat_status(self, text: str, value: Tone = Tone.SECONDARY) -> None:
        """A linha de estado do chat, em texto puro no tom (F5.4, A-10: o aviso em `warning`, sem HTML com cor)."""
        self.chat_status.setText(text)
        tone(self.chat_status, value)

    def stop_chat(self) -> None:
        if self.chat_task is not None:
            self._chat_stopped = True
            self.chat_task.cancel()
            self._chat_status("parando…")

    def _chat_link(self, url: QUrl) -> None:
        link = url.toString()
        opened = citations.parse_open_link(link)
        if opened is not None:
            self._open(opened.meeting_id, "transcricao" if opened.t_ms is not None else None, opened.t_ms, opened.t_ms is not None)
            return
        rated = chat_global.parse_rate_link(link)
        if rated is not None and self.chat_task is None:
            chat_global.rate_answer(self.store, rated[0], rated[1])
            self._render_thread(keep_scroll=True)
            return
        asked = parse_ask_link(link)
        if asked is not None and self.chat_task is None:                    # U-096: a sugestão vira a pergunta
            self.question.setText(asked)
            self.ask()
            return
        step = trace.parse_link(link)
        if step is not None and self.chat_task is None:
            self._trace_link(*step)

    def _trace_link(self, action: str, message_id: int, ref: str | None) -> None:
        if action == "trace":                                               # abre e fecha o "Como cheguei aqui"
            self._expanded ^= {message_id}
            self._render_thread(keep_scroll=True)
        elif action == "copysql" and ref is not None:
            self.copy_sql(message_id, ref)
        elif ref is not None:
            self.view_data(message_id, ref)

    def _query(self, message_id: int, ref: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        row = self.store.chat.get(message_id)
        steps = trace.load(row.get("trace_json")) if row is not None else None
        q = trace.query(steps, ref) if steps is not None else None
        return (steps, q) if steps is not None and q is not None else None

    def copy_sql(self, message_id: int, ref: str) -> str:
        """"Copiar SQL": a consulta formatada, com os `params` num comentário quando há."""
        found = self._query(message_id, ref)
        if found is None:
            return ""
        q = found[1]
        params = f"\n-- params: {', '.join(str(p) for p in q.get('params') or ())}" if q.get("params") else ""
        sql = trace.pretty(str(q.get("sql") or "")) + params
        copy_rich(sql)
        self.copied.append(sql)
        self._chat_status("SQL copiado.")
        return sql

    def view_data(self, message_id: int, ref: str) -> ask_data.AskDataDialog | None:
        """U-097: a tabela da consulta, refeita na sandbox numa `Task`, com os filtros com que a pergunta rodou."""
        found = self._query(message_id, ref)
        if found is None:
            return None
        steps, q = found
        dialog = ask_data.AskDataDialog(str(q.get("objetivo") or ""), str(q.get("sql") or ""), cast(QWidget, self))
        self.data_dialogs.append(dialog)
        dialog.show()
        fn = functools.partial(ask_data.run_query, self.store, steps, ref, int(self.store.get(SPECS["rag.sql_max_rows"])),
                               float(self.store.get(SPECS["rag.sql_timeout_s"])))
        self._start(fn, functools.partial(self._on_data, dialog))
        return dialog

    def _on_data(self, dialog: ask_data.AskDataDialog, result: Any) -> None:
        if not self._closed and shiboken6.isValid(dialog):
            dialog.show_result(result)

    def _answer_at(self, pos: QPoint) -> dict[str, Any] | None:
        """A mensagem do chat debaixo do cursor: a conversa é um parágrafo por mensagem (`thread_html`)."""
        if self.thread_id is None or self.chat_task is not None:
            return None
        block = self.chat_view.cursorForPosition(pos).block().blockNumber()
        rows = self.store.chat.list_for_thread(self.thread_id)
        if 0 <= block < len(rows) and str(rows[block]["role"]) == ChatRole.ASSISTANT:
            return dict(rows[block])
        return None

    def _answer_actions(self, pos: QPoint, _link: str) -> list[tuple[str, Callable[[], Any]]]:
        row = self._answer_at(pos)
        if row is None:
            return []
        return [("Copiar com citação", functools.partial(self.copy_answer, row))]

    def copy_answer(self, row: dict[str, Any]) -> str:
        """U-029 numa resposta: o texto com as citações legíveis (reunião, data e [mm:ss]), em texto e HTML sem links."""
        saved = chat_global.load_citations(row.get("citations_json"))
        meetings: dict[int, tuple[str | None, str | None]] = {}
        for mid in citations.saved_meeting_ids(saved):
            m = self.store.get_meeting(mid)
            if m is not None:
                meetings[mid] = (m.get("title") or m.get("title_llm"), str(m.get("started_at") or "")[:10])
        plain, rich = export.quote_answer(str(row.get("text") or ""), saved, meetings)
        copy_rich(plain, rich)
        self.copied.append(plain)
        return plain


__all__ = ["ASK_SCHEME", "CHAT_EMPTY", "AskMixin", "ask_link", "done_text", "empty_html", "parse_ask_link", "suggestions"]
