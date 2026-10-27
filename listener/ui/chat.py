"""Painel de chat da leitura (F3 item 10, U-019): pergunta -> resposta em streaming (`StreamTask`, sinal por pedaço,
cancelamento ao fechar; a resposta parcial aparece a cada pedaço, F3.1 R3-06), citações `[mm:ss]` clicáveis que tocam o
segmento (`listener://play?seg=<id>`), histórico de `chat_messages`. Chat sobre todas as reuniões é F5 (`ui/history.py`).
F5 L4: a citação toca pelo player único do app, do começo da fala com L e R misturados até parar (item 10); a aba "Chat"
da reunião aberta usa este painel. R7-05: a recarga da reunião aberta (job `llm` ou `index` concluído) não apaga a pergunta
em voo e não leva a rolagem ao fim (salvo se estava no fim). R7-03: a saída cancela a pergunta mesmo antes do primeiro
pedaço e espera a thread antes de o banco fechar."""
from __future__ import annotations

import functools
import html
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from ..llm import chat_task
from ..llm.costs import fmt_usd
from ..log import get_logger
from .tasks import StreamTask, run_stream, wait_before_close

if TYPE_CHECKING:
    from ..store import Store
    from .player import Player

log = get_logger("ui.chat")


class ChatPanel(QWidget):
    def __init__(self, store: Store, meeting_id: int, parent: QWidget | None = None, *, player: Player | None = None,
                 title: Any = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.player = player                               # F5: as citações tocam pelo player único
        self.title_fn = title                              # o título da reunião para a barra do player
        self.task: StreamTask | None = None
        self.answers: list[chat_task.ChatAnswer] = []       # instrumentação dos testes
        self.played: list[tuple[str, int, int]] = []
        self.lines = chat_task.transcript_lines(store, meeting_id)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.anchorClicked.connect(self._link_clicked)
        self.question = QLineEdit()
        self.question.setPlaceholderText("pergunte sobre esta reunião (as citações [mm:ss] tocam o áudio)")
        self.question.returnPressed.connect(self.ask)
        self.btn_ask = QPushButton("Perguntar")
        self.btn_ask.clicked.connect(self.ask)
        self.btn_stop = QPushButton("Parar")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        self.status = QLabel("")
        self.status.setStyleSheet("color: #888; font-size: 11px;")
        row = QHBoxLayout()
        row.addWidget(self.question, 1)
        row.addWidget(self.btn_ask)
        row.addWidget(self.btn_stop)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Chat sobre a reunião"))
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        lay.addWidget(self.status)
        self._partial = ""
        self._base_html = ""                                # histórico + pergunta em curso; a parcial vai depois dele
        self.reload()

    def reload(self) -> None:
        """Relê as linhas da transcrição e redesenha a conversa. Com uma pergunta em voo só os dados mudam: a pergunta e a
        resposta parcial ficam na tela, e a resposta, ao chegar, redesenha tudo (R7-05). Fora disso a rolagem volta para onde
        estava, salvo se estava no fim (aí segue no fim, onde chega a resposta)."""
        self.lines = chat_task.transcript_lines(self.store, self.meeting_id)
        if self.task is not None:
            return
        bar = self.view.verticalScrollBar()
        value = bar.value()
        at_end = value >= bar.maximum() - max(4, bar.singleStep())      # a uma linha do fim conta como no fim
        self.view.setHtml(chat_task.history_html(self.store, self.meeting_id, self.lines))
        if at_end:
            self.view.moveCursor(self.view.textCursor().MoveOperation.End)
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(value, bar.maximum()))

    # ---------------------------------------------------------------- perguntar
    def ask(self) -> None:
        text = self.question.text().strip()
        if not text or self.task is not None:
            return
        self.question.clear()
        self._partial = ""
        self._base_html = (chat_task.history_html(self.store, self.meeting_id, self.lines)
                           + f"<p><b>Você:</b> {html.escape(text)}</p>")
        self._render_partial()
        self.btn_ask.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status.setText("perguntando…")
        log.info("chat da reunião %s: pergunta enviada (%d caracteres)", self.meeting_id, len(text))   # o texto não (LGPD)
        fn = functools.partial(chat_task.run_chat, self.store, self.meeting_id, text)
        self.task = run_stream(fn, self.store, self._on_chunk, self._on_done)

    def _on_chunk(self, piece: str) -> None:
        self._partial += piece
        self._render_partial()
        self.status.setText(f"recebendo… {len(self._partial)} caracteres")

    def _render_partial(self) -> None:
        """Histórico + pergunta + resposta parcial como texto escapado; as citações viram links só no `reload()` final."""
        partial = html.escape(self._partial).replace("\n", "<br>") or "…"
        self.view.setHtml(f"{self._base_html}<p><b>LLM:</b> {partial}</p>")
        self.view.moveCursor(self.view.textCursor().MoveOperation.End)

    def _on_done(self, result: Any) -> None:
        self.task = None
        self.btn_ask.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if isinstance(result, BaseException):
            self.status.setText(f"falhou: {result}")
            self.reload()
            return
        self.answers.append(result)
        self.reload()
        if result.error:
            self.status.setText(f"falhou: {result.error}")
        elif result.cancelled:
            self.status.setText("pergunta cancelada antes da resposta")
        else:
            cites = sum(1 for c in result.citations if c.segment_id is not None)
            cost = f" · {fmt_usd(result.cost_usd)}" if result.cost_usd is not None else ""
            self.status.setText(f"{cites} citação(ões) clicável(is){cost}")

    def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            self.status.setText("parando…")

    def close_panel(self) -> None:
        """Ao fechar a janela: cancela o streaming em curso (a corrotina para no próximo pedaço)."""
        self.stop()

    def shutdown(self, wait_ms: int = 2500) -> None:
        """Saída do app (F5A-32): cancela a pergunta (também antes do primeiro pedaço, R7-03) e espera a thread antes de o
        banco fechar; passado o prazo, avisa no log e espera o fim."""
        task = self.task
        if task is not None:
            task.cancel()
            wait_before_close(task, wait_ms, f"o chat da reunião {self.meeting_id}")

    # ---------------------------------------------------------------- citações
    def _link_clicked(self, url: QUrl) -> None:
        seg = chat_task.citation_segment(url.toString())
        if seg is None:
            return
        rng = chat_task.play_range(self.store, seg)
        if rng is None:
            self.status.setText("segmento citado não existe mais")
            return
        self.played.append(rng)
        _channel, t0, _t1 = rng
        if self.player is None:
            self.status.setText("reprodução indisponível nesta janela")
            return
        self.player.play_from(self.meeting_id, t0, title=self.title_fn() if callable(self.title_fn) else "")
