"""F5.10 (U-097, D7): "Ver dados" de uma consulta da resposta de "Perguntar à base": a tabela dela refeita na sandbox, com os
filtros da pergunta (`run_query`, numa `Task` da página), e "Copiar como tabela" (o texto com tab, que cola numa planilha, e
uma tabela HTML). A sandbox é a mesma das rodadas: só leitura, o autorizador, o prazo e o teto de linhas das Configurações.
Os dados ficam na tela e na área de transferência, nunca no log."""
from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..rag import agent_results as ar
from ..rag import semantic, trace
from .common import copy_rich
from .theme import note

if TYPE_CHECKING:
    from ..rag.sandbox import MemoryRss, QueryResult
    from ..store import Store

TITLE = "Ver dados"
RUNNING = "rodando a consulta de novo…"


def run_query(store: Store, steps: dict[str, Any], ref: str, max_rows: int, timeout_s: float,
              memory_rss: MemoryRss | None = None) -> QueryResult:
    """A consulta `ref` da resposta, refeita na sandbox com os filtros com que a pergunta rodou (numa `Task`), com o teto de
    memória pela `memory_rss` da plataforma."""
    q = trace.query(steps, ref)
    if q is None:
        raise ValueError("a consulta não está nesta resposta")
    with semantic.open_sandbox(store, trace.filters_of(steps), memory_rss=memory_rss) as box:
        return box.run(str(q.get("sql") or ""), [str(p) for p in q.get("params") or ()], max_rows=max_rows, timeout_s=timeout_s)


def footer(res: QueryResult) -> str:
    if len(res.rows) >= res.total:
        return f"{ar.fmt_count(res.total)} {'linha' if res.total == 1 else 'linhas'} · {res.ms:.0f} ms"
    return f"as primeiras {ar.fmt_count(len(res.rows))} de {ar.fmt_count(res.total)} linhas · {res.ms:.0f} ms"


class AskDataDialog(QDialog):
    """A tabela de uma consulta (não modal), com o objetivo, o SQL e "Copiar como tabela"."""

    def __init__(self, goal: str, sql: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{TITLE} — {goal}" if goal else TITLE)
        self.data: QueryResult | None = None
        self.copied: list[str] = []                                   # instrumentação dos testes
        self.goal = QLabel(goal)
        self.goal.setTextFormat(Qt.TextFormat.PlainText)
        self.sql = QPlainTextEdit(trace.pretty(sql))
        self.sql.setReadOnly(True)
        self.sql.setMaximumHeight(96)
        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.status = QLabel(RUNNING)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        note(self.status)
        self.btn_copy = QPushButton("Copiar como tabela")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self.copy_table)
        close = QPushButton("Fechar")
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.btn_copy)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.goal)
        lay.addWidget(self.sql)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)
        self.resize(760, 480)

    def show_result(self, result: Any) -> None:
        """O resultado da `Task`: a tabela, o erro da consulta ou a falha (banco ocupado, consulta que saiu)."""
        if isinstance(result, BaseException):
            self.status.setText(f"A consulta não rodou: {result}")
            return
        res: QueryResult = result
        if not res.ok:
            self.status.setText(f"Erro ({res.error}): {res.message}")
            return
        self.data = res
        self.table.setColumnCount(len(res.columns))
        self.table.setHorizontalHeaderLabels(list(res.columns))
        self.table.setRowCount(len(res.rows))
        for r, values in enumerate(res.rows):
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(ar.NULL if value is None else str(value)))
        self.status.setText(footer(res))
        self.btn_copy.setEnabled(bool(res.columns))

    def copy_table(self) -> str:
        """O texto com tab (cola numa planilha) e uma tabela HTML (cola no Outlook e no Teams)."""
        if self.data is None:
            return ""
        res = self.data
        plain = "\n".join(["\t".join(res.columns), *("\t".join(ar.cell(v) for v in row) for row in res.rows)])
        head = "".join(f"<th>{html.escape(c)}</th>" for c in res.columns)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(ar.NULL if v is None else str(v))}</td>" for v in row) + "</tr>"
                       for row in res.rows)
        copy_rich(plain, f"<table><tr>{head}</tr>{body}</table>")
        self.copied.append(plain)
        return plain


__all__ = ["AskDataDialog", "footer", "run_query"]
