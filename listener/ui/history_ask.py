"""A conversa de "Perguntar às reuniões" do Histórico (F5 item 11, U-027, R7-03, F5.3 A-12), fora do `ui/history.py` (F5.10,
D7: só a mudança de lugar, antes da feature; no padrão do `SearchMixin`). `AskMixin` é parte da `HistoryPage`: lê dela o banco,
os filtros, a conversa escolhida e os widgets da aba (`ui/history_chat.py`) e abre a reunião pelo `_open` dela."""
from __future__ import annotations

import functools
import html
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QPoint, QThread, QUrl
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QTextBrowser, QWidget

from ..llm.costs import fmt_usd
from ..log import get_logger
from ..model import ChatRole
from ..rag import chat_global, citations, export
from .common import copy_rich
from .tasks import StreamTask, run_stream
from .theme import Tone, tone

if TYPE_CHECKING:
    from collections import deque

    from ..store import Store
    from ..store.rag import SearchFilters

log = get_logger("ui.history")

CHAT_EMPTY = ("<p class='note'>Pergunte sobre todas as reuniões. A resposta cita a reunião, quem falou e o minuto; o clique "
              "numa citação abre a reunião naquele ponto e toca. Os filtros de pessoa e período valem aqui também.</p>")


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

    def filters(self) -> SearchFilters:
        raise NotImplementedError

    def filters_text(self) -> str:
        raise NotImplementedError

    def _open(self, meeting_id: int, tab: str | None = None, t_ms: int | None = None, play: bool = False) -> None:
        raise NotImplementedError

    def _watch_local(self) -> None:
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
        self.chat_view.setHtml(chat_global.thread_html(self.store, self.thread_id) if self.thread_id is not None else CHAT_EMPTY)
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
        self._chat_status("procurando nas reuniões…")
        log.info("chat global: pergunta enviada (%d caracteres; conversa %s)", len(text), self.thread_id)   # o texto não (LGPD)
        fn = functools.partial(chat_global.run_global_chat, self.store, self.thread_id, text, self.filters())
        task = run_stream(fn, self.store, self._on_chunk, self._on_chat_done)
        self._watch_local()                              # o vetor da pergunta pode reabrir a sessão do e5 solta antes
        self.chat_task = task
        self._tasks.add(task)
        task.finished.connect(functools.partial(self._tasks.discard, task))

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
        ans: chat_global.GlobalChatAnswer = result
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
            cost = f" · {fmt_usd(ans.cost_usd)}" if ans.cost_usd is not None else ""
            how = "busca híbrida" if ans.hybrid else "busca por palavras"
            stopped = " · interrompida" if ans.cancelled else ""
            self._chat_status(f"{ans.linked} citação(ões) com link · {ans.excerpts} trecho(s) de {len(ans.aliases)} "
                                     f"reunião(ões) · {how}{cost}{stopped}")
        if ans.error or not ans.cancelled:
            self._answered_away(not ans.error)

    def _answered_away(self, ok: bool) -> None:
        """F5.3 (A-12): terminou com o Histórico fora de foco (o usuário foi para outro app ou o escondeu: a pergunta segue e
        grava): o controlador avisa. Parada pelo usuário ("Parar"), não."""
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


__all__ = ["CHAT_EMPTY", "AskMixin"]
