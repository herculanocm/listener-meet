"""Histórico (F5 item 8, U-024, U-026, U-027, U-028, U-029): uma janela não modal (`HistoryWindow`) que hospeda a página
(`HistoryPage`, um `QWidget`: vira página da janela principal na F6). Em cima, a busca ("Buscar em todas as reuniões…",
Ctrl+F foca, Esc limpa), o filtro de pessoa e o de período (lembrados em `ui.history_filters`) e uma linha de estado com o
selo ("busca híbrida" ou "busca por palavras: motivo") e a cobertura do índice. Abas:

- "Reuniões": sem busca, a lista (`MeetingsModel` carregado numa `Task`: data, título, duração, participantes, estado e
  custo), filtrada por pessoa e período; Enter ou duplo clique abre a reunião. Com busca, os trechos achados agrupados por
  reunião (U-024) com o termo em negrito, o tipo, o [mm:ss] e quem fala; o clique num trecho abre a reunião naquele ponto e
  toca ("abrir neste ponto"); o título do grupo abre a reunião. A parte por palavra roda a cada pausa curta da digitação
  (~250 ms) e o vetor da consulta (pago, com cache) no Enter ou 600 ms depois de parar, refazendo a busca com ele; cada
  busca numa `Task` com contador de geração (resultado velho é descartado).
- "Ações abertas" (U-026): as ações abertas da ata atual de todas as reuniões, com dono, prazo (atrasada em destaque) e a
  reunião de origem, filtradas por dono e período; "Marcar feita" grava com `revisions` e reindexa a reunião; Enter ou duplo
  clique abre a reunião na evidência.
- "Perguntar às reuniões": as conversas guardadas (Q3) e "Nova conversa"; a resposta em streaming numa `StreamTask` com os
  filtros da aba Reuniões; as citações abrem a reunião no ponto e tocam; "Citação certa? sim · não" (U-027) grava e
  redesenha; custo e citações com link na linha de estado.

Menu de contexto "Copiar com citação" (U-029) nos trechos achados e nas respostas. Textos para o usuário sem jargão: "busca
por sentido", nunca "embedding" nem "FTS". Notas em texto puro e dicas convertidas de texto puro: um título ou nome com "<b>"
não vira HTML (R7-07) e "S&OP" aparece como está (R8-02). A saída do app cancela o chat em voo e o vetor da consulta presos
na rede (um `threading.Event` ligado ao `shutdown`, R7-03) e espera as `Task`s antes de fechar o banco (F5A-32).
F5.1 (A-11): com o embedder local ativo e pronto, abrir o Histórico abre a sessão do e5 numa `Task` (a primeira busca por
sentido não espera a carga; a saída espera a `Task`), e um `QTimer` a solta depois de 10 min sem uso.
F5.3 (A-12): a resposta (ou a falha) de "Perguntar às reuniões" que chega com o Histórico fora de foco, ou escondido, chama
`on_answer(ok)` (o aviso do controlador, cujo clique abre o Histórico); a pergunta cancelada não."""
from __future__ import annotations

import functools
import html
import json
import re
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast, override

from PySide6 import QtGui
from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QObject, QPersistentModelIndex, QPoint, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QColor, QContextMenuEvent, QFont, QGuiApplication, QKeyEvent, QKeySequence, QMoveEvent, \
    QResizeEvent, QShortcut, QShowEvent
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QStackedWidget,
                               QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..llm.costs import fmt_usd
from ..llm.prompts.common import stamp
from ..log import get_logger
from ..model import ActionStatus, ChatRole, ChunkKind, MeetingStatus
from ..rag import chat_global, citations, export
from ..rag import search as rag_search
from ..store.rag import SearchFilters
from ..store.segments import ClusterRow, resolve_person
from .common import clamp_to_screens, copy_rich
from .tasks import StreamTask, run_stream, run_task, wait_before_close
from .widget import _screen_rects

if TYPE_CHECKING:
    from ..rag.search import SearchHit, SearchResult
    from ..store import Store

log = get_logger("ui.history")

WORDS_DELAY_MS = 250             # a parte por palavra a cada pausa curta da digitação
VECTOR_DELAY_MS = 600            # o vetor da consulta (pago) no Enter ou depois de parar de digitar
TASK_WAIT_MS = 3000              # saída do app: espera cada busca ou pergunta em voo até isto
LOCAL_IDLE_S = 600.0             # F5.1 (A-11): a sessão do e5 local é solta depois de 10 min sem uso
LOCAL_RELEASE_TICK_MS = 60_000   # ...conferido a cada minuto
PERIODS: tuple[tuple[str, str], ...] = (("all", "Qualquer data"), ("7", "Últimos 7 dias"), ("30", "Últimos 30 dias"),
                                        ("90", "Últimos 90 dias"), ("year", "Este ano"))
MEETING_COLUMNS = ("Data", "Título", "Duração", "Participantes", "Estado", "Custo")
ACTION_COLUMNS = ("Ação", "Dono", "Prazo", "Reunião")
STATUS_TEXT: dict[str, str] = {MeetingStatus.RECORDING: "gravando", MeetingStatus.PAUSED: "pausada", MeetingStatus.RECORDED: "gravada",
                               MeetingStatus.CANCELLED: "cancelada", MeetingStatus.INTERRUPTED: "interrompida",
                               MeetingStatus.PROCESSED: "falantes a revisar", MeetingStatus.REVIEWED: "revisada"}
KIND_TAB: dict[str, str] = {ChunkKind.EXECUTIVE: "resumo", ChunkKind.ACTION: "acoes", ChunkKind.DECISION: "ata", ChunkKind.HIGHLIGHT: "ata",
                            ChunkKind.PENDING: "ata", ChunkKind.NEXT_STEP: "ata", ChunkKind.MINUTES: "ata"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HIT_SCHEME, MEETING_SCHEME = "listener://hit?c=", "listener://meeting?m="
CHAT_EMPTY = ("<p style='color:#888'>Pergunte sobre todas as reuniões. A resposta cita a reunião, quem falou e o minuto; o clique "
              "numa citação abre a reunião naquele ponto e toca. Os filtros de pessoa e período valem aqui também.</p>")
OVERDUE_COLOR = "#c0392b"
KEPT = 100                       # instrumentação dos testes: as últimas respostas e cópias

type OpenMeeting = Callable[..., Any]          # on_open(meeting_id, tab=None, t_ms=None, play=False)
type ContextActions = Callable[[QPoint, str], list[tuple[str, Callable[[], Any]]]]


# ---------------------------------------------------------------- funções puras
def period_range(key: str, today: date) -> tuple[str | None, str | None]:
    """(desde, até) em `AAAA-MM-DD` para o período escolhido; "últimos 7 dias" inclui hoje e os 6 anteriores."""
    match key:
        case "7" | "30" | "90":
            return (today - timedelta(days=int(key) - 1)).isoformat(), None
        case "year":
            return f"{today.year:04d}-01-01", None
        case _:
            return None, None


def search_filters(person_id: int | None, period: str, today: date) -> SearchFilters:
    date_from, date_to = period_range(period, today)
    return SearchFilters(person_ids=(int(person_id),) if person_id is not None else (), date_from=date_from, date_to=date_to)


def friendly_reason(reason: str) -> str:
    """O motivo da busca só por palavras em texto para o usuário (sem "embedding", modelo nem detalhe técnico)."""
    low = (reason or "").strip().lower()
    if not low or low == rag_search.NO_QUERY_VECTOR:
        return "busca por sentido indisponível agora"
    rules = (("índice ainda sem vetores", "o índice da busca por sentido ainda está vazio"),
             ("trechos filtrados sem vetor", "os trechos filtrados ainda não têm busca por sentido"),
             ("modelo de embeddings mudou", "o modelo da busca por sentido mudou: busque de novo"),
             ("nenhum provedor ativo", "escolha um provedor para a busca por sentido (Configurações, LLM)"),
             ("busca local não baixada", "a busca local ainda não foi baixada: prepare em Configurações (Busca)"),     # F5.1 (A-02)
             ("extra local", "a busca local precisa do extra local instalado (pip install -e .[local])"),
             ("falhou ao calcular", "a busca local falhou nesta busca (memória?): tente de novo; se repetir, veja o log"),
             ("busca local", "a busca local não abriu o modelo: prepare de novo em Configurações (Busca)"),
             ("anthropic", "o provedor escolhido não faz busca por sentido: escolha Gemini, OpenAI ou Local (Configurações, LLM)"),
             ("não tem modelo", "falta o modelo da busca por sentido no provedor (Configurações, LLM)"),
             ("sem chave", "falta a chave do provedor da busca por sentido (Configurações, Chaves de acesso)"),
             ("sem rede", "sem rede"), ("tempo esgotado", "o provedor não respondeu a tempo"), ("consulta vazia", "consulta vazia"),
             ("dimens", "o índice da busca por sentido é de outro modelo: reindexe nas Configurações"),
             ("assinatura", "o índice da busca por sentido é de outro modelo: reindexe nas Configurações"))
    return next((text for key, text in rules if key in low), "busca por sentido indisponível agora")


def fmt_int(n: int) -> str:
    """Milhar com ponto (pt-BR): 3120 -> "3.120"."""
    return f"{int(n):,}".replace(",", ".")


def coverage_text(cov: dict[str, int], processed: int) -> str:
    """"Índice: 42 de 45 reuniões processadas · 3.120 trechos (2.980 com busca por sentido)"."""
    chunks = int(cov.get("chunks") or 0)
    text = f"Índice: {int(cov.get('meetings') or 0)} de {processed} reuniões processadas · {fmt_int(chunks)} trechos"
    if "vectors" in cov and chunks:
        text += f" ({fmt_int(min(int(cov['vectors']), chunks))} com busca por sentido)"
    return text


def hit_link(hit: SearchHit) -> str:
    return f"{HIT_SCHEME}{hit.chunk_id}"


def results_html(result: SearchResult) -> str:
    """Os trechos achados agrupados por reunião (U-024), na ordem do melhor trecho de cada uma. O destaque (`snippet_html`)
    já vem escapado; título e falantes vêm crus e são escapados aqui."""
    if not result.hits:
        return "<p style='color:#888'>Nada encontrado com essas palavras e filtros.</p>"
    parts: list[str] = []
    for g in rag_search.group_by_meeting(result.hits):
        n = len(g.hits)
        title = html.escape(g.title or "Reunião sem título")
        parts.append(f"<p style='margin-top:12px; margin-bottom:2px'><a href='{MEETING_SCHEME}{g.meeting_id}'><b>{title}</b></a>"
                     f" <span style='color:#888'>· {export.fmt_date(g.date)} · {n} {'trecho' if n == 1 else 'trechos'}</span></p>")
        for h in g.hits:
            when = f"▶ {stamp(h.t_start_ms)}" if h.t_start_ms is not None else "abrir"
            meta = " · ".join(x for x in (h.kind_label, html.escape(h.speakers)) if x)
            snippet = h.snippet_html or html.escape(" ".join(h.text.split())[:200])
            parts.append(f"<p style='margin-top:0; margin-bottom:6px; margin-left:16px'><a href='{hit_link(h)}'>{when}</a> "
                         f"<span style='color:#888'>{meta}</span><br>{snippet}</p>")
    return "".join(parts)


def hit_target(hit: SearchHit) -> tuple[str | None, int | None, bool]:
    """(aba, tempo, tocar) do clique num trecho: com tempo, a transcrição naquele ponto tocando; sem tempo, a aba do tipo
    (resumo executivo, ações, ata) ou a primeira da reunião."""
    if hit.t_start_ms is not None:
        return "transcricao", int(hit.t_start_ms), True
    return KIND_TAB.get(hit.kind), None, False


def hit_quote(hit: SearchHit) -> tuple[str, str]:
    """U-029 de um trecho achado: o texto do trecho, quem fala, a reunião, a data e o [mm:ss]."""
    return export.quote(hit.text, hit.speakers, hit.meeting_title or "Reunião sem título", hit.meeting_date, hit.t_start_ms)


def is_overdue(due: str | None, today: date) -> bool:
    d = str(due or "")
    return bool(DATE_RE.match(d)) and d < today.isoformat()


@dataclass(frozen=True, slots=True)
class MeetingLine:
    meeting_id: int
    started_at: str
    title: str
    duration_ms: int | None
    participants: tuple[str, ...]
    status: str
    cost_usd: float | None

    def texts(self) -> tuple[str, ...]:
        people = ", ".join(self.participants)
        return (export.fmt_datetime(self.started_at), self.title, export.fmt_duration(self.duration_ms),
                people if len(people) <= 60 else people[:59].rstrip(", ") + "…", STATUS_TEXT.get(self.status, self.status),
                fmt_usd(self.cost_usd) if self.cost_usd is not None else "")


def load_meetings(store: Store, person_id: int | None = None, date_from: str | None = None,
                  date_to: str | None = None) -> list[MeetingLine]:
    """A lista do Histórico: as reuniões do período, com os participantes pelos clusters decididos do job vigente (auto ou
    confirmado, seguindo as mesclas; mais fala primeiro; sem processamento, os esperados) e o custo; com `person_id`, só as
    reuniões em que a pessoa fala. Quatro consultas, qualquer que seja o número de reuniões (mais uma por reunião só quando ela
    tem falas com pessoa e nenhum cluster); roda numa `Task`."""
    rows = store.meetings.overview(date_from, date_to)
    names = {int(p["id"]): str(p["name"]) for p in store.people.list_people()}
    clusters: dict[int, list[dict[str, Any]]] = {}
    for c in store.segments.light_clusters([int(r["id"]) for r in rows]) if rows else ():
        clusters.setdefault(int(c["meeting_id"]), []).append(c)
    out: list[MeetingLine] = []
    for r in rows:
        mid, job = int(r["id"]), r.get("job_id")
        mine = [c for c in clusters.get(mid, ()) if job is not None and c.get("job_id") == job]
        by_id = {int(c["id"]): cast(ClusterRow, c) for c in mine}
        speech: dict[int, int] = {}
        for c in mine:
            pid = resolve_person(cast(ClusterRow, c), by_id)
            if pid is not None:
                speech[pid] = speech.get(pid, 0) + int(c.get("speech_ms") or 0)
        if not mine and job is not None:                 # falas com pessoa e sem clusters (importadas): quem fala, na ordem
            for i, sp in enumerate(store.segments.speakers(mid, int(job))):
                if sp.get("person_id") is not None:
                    speech.setdefault(int(sp["person_id"]), -i)
        if person_id is not None and int(person_id) not in speech:
            continue
        people = tuple(names[p] for p in sorted(speech, key=lambda p: (-speech[p], names.get(p, ""))) if p in names)
        if not people and r.get("expected_participants"):
            people = tuple(p.strip() for p in str(r["expected_participants"]).replace(";", ",").split(",") if p.strip())
        cost = r.get("cost_usd")
        out.append(MeetingLine(mid, str(r.get("started_at") or ""), str(r.get("title") or r.get("title_llm") or f"Reunião {mid}"),
                               r.get("duration_ms"), people, str(r.get("status") or ""), float(cost) if cost is not None else None))
    return out


@dataclass(frozen=True, slots=True)
class ActionLine:
    action_id: int
    meeting_id: int
    text: str
    owner: str
    due: str
    t_ms: int | None
    meeting_title: str
    meeting_date: str
    overdue: bool

    def texts(self) -> tuple[str, ...]:
        due = f"{export.fmt_date(self.due) or self.due} (atrasada)" if self.overdue else (export.fmt_date(self.due) or self.due)
        return self.text, self.owner, due, f"{self.meeting_title} — {export.fmt_date(self.meeting_date)}"


def load_actions(store: Store, person_id: int | None, date_from: str | None, date_to: str | None, today: date) -> list[ActionLine]:
    """As ações abertas da ata atual de cada reunião (U-026), com o dono (pessoa casada ou o texto livre) e a reunião."""
    out: list[ActionLine] = []
    for a in store.actions.list_open(person_id, date_from, date_to):
        mid = int(a["meeting_id"])
        owner = str(a.get("owner_name") or a.get("owner_text") or "")
        out.append(ActionLine(int(a["id"]), mid, " ".join(str(a.get("text") or "").split()), owner, str(a.get("due") or ""), a.get("t_ms"),
                              str(a.get("meeting_title") or a.get("meeting_title_llm") or f"Reunião {mid}"),
                              str(a.get("started_at") or ""), is_overdue(a.get("due"), today)))
    return out


# ---------------------------------------------------------------- widgets
class MeetingsModel(QAbstractTableModel):
    """A lista de reuniões do Histórico (milhares de linhas sem milhares de widgets), ordenável por coluna."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.lines: list[MeetingLine] = []

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.lines)

    @override
    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(MEETING_COLUMNS)

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.lines):
            return None
        line = self.lines[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return line.texts()[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            # a dica do Qt é AutoText: o texto puro vira um parágrafo com o HTML escapado, e o que aparece é o título e os
            # nomes como estão ("<b>" não vira negrito, R7-07, e "S&OP" não vira "S&amp;OP", R8-02)
            text = ", ".join(line.participants) if index.column() == 3 else line.title
            return QtGui.Qt.convertFromPlainText(text, Qt.WhiteSpaceMode.WhiteSpaceNormal)
        if role == Qt.ItemDataRole.UserRole:
            return line.meeting_id
        return None

    @override
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal and 0 <= section < len(MEETING_COLUMNS):
            return MEETING_COLUMNS[section]
        return None

    @override
    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        keys: tuple[Callable[[MeetingLine], Any], ...] = (
            lambda m: m.started_at, lambda m: m.title.casefold(), lambda m: m.duration_ms or 0,
            lambda m: ", ".join(m.participants).casefold(), lambda m: m.status, lambda m: m.cost_usd or 0.0)
        if not 0 <= column < len(keys):
            return
        self.layoutAboutToBeChanged.emit()
        self.lines.sort(key=keys[column], reverse=order == Qt.SortOrder.DescendingOrder)
        self.layoutChanged.emit()

    def replace(self, lines: list[MeetingLine]) -> None:
        self.beginResetModel()
        self.lines = list(lines)
        self.endResetModel()


class LinkBrowser(QTextBrowser):
    """`QTextBrowser` que não navega sozinho (os links `listener://` vão para quem ouve `anchorClicked`) e acrescenta ao menu
    de contexto as ações de quem o usa (U-029), conforme o ponto e o link debaixo do cursor."""

    def __init__(self, actions: ContextActions | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.actions_for = actions

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = self.createStandardContextMenu(event.pos())
        extra = self.actions_for(event.pos(), self.anchorAt(event.pos())) if self.actions_for is not None else []
        first = menu.actions()[0] if menu.actions() else None
        for label, fn in extra:
            act = QAction(label, menu)
            act.triggered.connect(fn)
            if first is not None:
                menu.insertAction(first, act)
            else:
                menu.addAction(act)
        if extra and first is not None:
            menu.insertSeparator(first)
        menu.exec(event.globalPos())


class HistoryPage(QWidget):
    def __init__(self, store: Store, *, on_open: OpenMeeting | None = None, on_edited: Callable[..., Any] | None = None,
                 on_answer: Callable[[bool], Any] | None = None, today: Callable[[], date] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.on_open = on_open
        self.on_edited = on_edited
        self.on_answer = on_answer                                # F5.3 (A-12): a resposta chegou com a janela fora de foco
        self._chat_stopped = False                                # "Parar": o usuário cancelou, não avisa
        self.today = today or (lambda: datetime.now().astimezone().date())
        self.result: SearchResult | None = None
        self.answers: deque[chat_global.GlobalChatAnswer] = deque(maxlen=KEPT)   # instrumentação dos testes
        self.copied: deque[str] = deque(maxlen=KEPT)              # instrumentação dos testes: o texto copiado com citação
        self.thread_id: int | None = None
        self.chat_task: StreamTask | None = None
        self._gen = 0                                             # contador de geração das buscas (resultado velho sai)
        self._list_gen = 0
        self._hits: dict[int, SearchHit] = {}
        self._tasks: set[QThread] = set()
        self._stop = threading.Event()                            # a saída do app: o vetor da consulta preso na rede para (R7-03)
        self._closed = False
        self._partial = ""
        self._base_html = ""

        # --- barra de cima ---
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar em todas as reuniões…")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Palavras de qualquer reunião (sem acento e sem maiúsculas). Enter também busca pelo sentido.")
        self.search.installEventFilter(self)
        self.person = QComboBox()
        self.person.setToolTip("Só trechos, reuniões e ações desta pessoa")
        self.period = QComboBox()
        for key, label in PERIODS:
            self.period.addItem(label, key)
        self.badge = QLabel("")
        self.coverage = QLabel("")
        self.coverage.setTextFormat(Qt.TextFormat.PlainText)
        self.coverage.setStyleSheet("color: #888; font-size: 11px;")
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(QLabel("Pessoa"))
        top.addWidget(self.person)
        top.addWidget(QLabel("Período"))
        top.addWidget(self.period)
        state = QHBoxLayout()
        state.addWidget(self.badge, 1)
        state.addWidget(self.coverage)

        # --- Reuniões ---
        self.model = MeetingsModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        for col in range(len(MEETING_COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col in (1, 3) else QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.activated.connect(self._meeting_activated)
        self.results = LinkBrowser(self._hit_actions)
        self.results.anchorClicked.connect(self._results_link)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.results)
        self.list_note = QLabel("")
        self.list_note.setTextFormat(Qt.TextFormat.PlainText)          # o filtro leva o nome da pessoa (R7-07)
        self.list_note.setStyleSheet("color: #888; font-size: 11px;")
        meetings = QWidget()
        ml = QVBoxLayout(meetings)
        ml.addWidget(self.stack, 1)
        ml.addWidget(self.list_note)

        # --- Ações abertas ---
        self.action_table = QTableWidget(0, len(ACTION_COLUMNS))
        self.action_table.setHorizontalHeaderLabels(list(ACTION_COLUMNS))
        self.action_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.action_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for col in range(len(ACTION_COLUMNS)):
            self.action_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch if col == 0 else QHeaderView.ResizeMode.ResizeToContents)
        self.action_table.setAlternatingRowColors(True)
        self.action_table.verticalHeader().setVisible(False)
        self.action_table.activated.connect(lambda _i: self.open_action())
        self.action_lines: list[ActionLine] = []
        self.btn_done = QPushButton("Marcar feita")
        self.btn_done.setToolTip("Marca as ações selecionadas como feitas (fica no histórico de edições)")
        self.btn_done.clicked.connect(self.mark_done)
        self.btn_open_action = QPushButton("Abrir reunião")
        self.btn_open_action.clicked.connect(self.open_action)
        self.actions_note = QLabel("")
        self.actions_note.setTextFormat(Qt.TextFormat.PlainText)
        self.actions_note.setStyleSheet("color: #888; font-size: 11px;")
        arow = QHBoxLayout()
        arow.addWidget(self.actions_note, 1)
        arow.addWidget(self.btn_open_action)
        arow.addWidget(self.btn_done)
        actions = QWidget()
        al = QVBoxLayout(actions)
        al.addWidget(self.action_table, 1)
        al.addLayout(arow)

        # --- Perguntar às reuniões ---
        self.threads = QComboBox()
        self.threads.setToolTip("Conversas guardadas")
        self.threads.activated.connect(self._thread_chosen)
        self.btn_new = QPushButton("Nova conversa")
        self.btn_new.clicked.connect(self.new_thread)
        self.chat_view = LinkBrowser(self._answer_actions)
        self.chat_view.anchorClicked.connect(self._chat_link)
        self.chat_note = QLabel("")
        self.chat_note.setTextFormat(Qt.TextFormat.PlainText)
        self.chat_note.setStyleSheet("color: #888; font-size: 11px;")
        self.question = QLineEdit()
        self.question.setPlaceholderText("Pergunte sobre todas as reuniões (as citações abrem a reunião no ponto)")
        self.question.returnPressed.connect(self.ask)
        self.btn_ask = QPushButton("Perguntar")
        self.btn_ask.clicked.connect(self.ask)
        self.btn_stop = QPushButton("Parar")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_chat)
        self.chat_status = QLabel("")
        self.chat_status.setWordWrap(True)
        self.chat_status.setStyleSheet("color: #888; font-size: 11px;")
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Conversa"))
        trow.addWidget(self.threads, 1)
        trow.addWidget(self.btn_new)
        qrow = QHBoxLayout()
        qrow.addWidget(self.question, 1)
        qrow.addWidget(self.btn_ask)
        qrow.addWidget(self.btn_stop)
        chat = QWidget()
        cl = QVBoxLayout(chat)
        cl.addLayout(trow)
        cl.addWidget(self.chat_view, 1)
        cl.addWidget(self.chat_note)
        cl.addLayout(qrow)
        cl.addWidget(self.chat_status)

        self.tabs = QTabWidget()
        self.tabs.addTab(meetings, "Reuniões")
        self.tabs.addTab(actions, "Ações abertas")
        self.tabs.addTab(chat, "Perguntar às reuniões")
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(state)
        lay.addWidget(self.tabs, 1)

        self._words_timer = QTimer(self)
        self._words_timer.setSingleShot(True)
        self._words_timer.setInterval(WORDS_DELAY_MS)
        self._words_timer.timeout.connect(lambda: self.run_search(with_vector=False))
        self._vector_timer = QTimer(self)
        self._vector_timer.setSingleShot(True)
        self._vector_timer.setInterval(VECTOR_DELAY_MS)
        self._vector_timer.timeout.connect(lambda: self.run_search(with_vector=True))
        self._local_timer = QTimer(self)                          # F5.1 (A-11): solta a sessão do e5 parada há 10 min
        self._local_timer.setInterval(LOCAL_RELEASE_TICK_MS)
        self._local_timer.timeout.connect(self._release_local)
        self._preloading = False
        self.search.textChanged.connect(self._text_changed)
        self.search.returnPressed.connect(self._search_now)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self, self.focus_search)
        self._fill_people()
        self._restore_filters()
        self.person.currentIndexChanged.connect(self._filters_changed)
        self.period.currentIndexChanged.connect(self._filters_changed)
        self._load_threads()
        self._render_thread()
        self._update_chat_note()

    # ---------------------------------------------------------------- filtros
    def _fill_people(self) -> None:
        self.person.clear()
        self.person.addItem("Todas", None)
        for p in self.store.people.list_people():
            self.person.addItem(f"{p['name']} (você)" if p.get("is_me") else str(p["name"]), int(p["id"]))

    def _restore_filters(self) -> None:
        try:
            saved = json.loads(self.store.get_setting("ui.history_filters") or "{}")
        except ValueError:
            saved = {}
        saved = saved if isinstance(saved, dict) else {}
        self.person.setCurrentIndex(max(0, self.person.findData(saved.get("person"))) if saved.get("person") is not None else 0)
        self.period.setCurrentIndex(max(0, self.period.findData(saved.get("period", "all"))))

    def person_id(self) -> int | None:
        data = self.person.currentData()
        return int(data) if data is not None else None

    def period_key(self) -> str:
        return str(self.period.currentData() or "all")

    def filters(self) -> SearchFilters:
        return search_filters(self.person_id(), self.period_key(), self.today())

    def filters_text(self) -> str:
        parts = []
        if self.person_id() is not None:
            parts.append(f"pessoa: {self.person.currentText()}")
        if self.period_key() != "all":
            parts.append(self.period.currentText().lower())
        return " · ".join(parts)

    def _filters_changed(self) -> None:
        with suppress(Exception):
            self.store.set_setting("ui.history_filters", json.dumps({"person": self.person_id(), "period": self.period_key()}))
        self.reload_meetings()
        self.reload_actions()
        self._update_chat_note()
        if self.search.text().strip():
            self.run_search(with_vector=True)            # o vetor da mesma consulta vem do cache: sem custo

    # ---------------------------------------------------------------- carga
    def refresh(self) -> None:
        """Ao mostrar a janela: a lista, a cobertura, as ações e as conversas de novo (uma `Task` para a lista)."""
        current = self.person_id()
        self.person.blockSignals(True)
        self._fill_people()
        self.person.setCurrentIndex(max(0, self.person.findData(current)) if current is not None else 0)
        self.person.blockSignals(False)
        self.reload_meetings()
        self.reload_actions()
        self._load_threads(select=self.thread_id)
        self.preload_local()

    # ---------------------------------------------------------------- busca local (F5.1, A-11)
    def preload_local(self) -> bool:
        """Com o embedder local ativo e pronto, abre a sessão do e5 numa `Task` (a saída a espera): a primeira busca por
        sentido não espera a carga (1 a 1,4 s no Windows, quase toda do tokenizer). Liga a soltura depois de 10 min sem uso.
        False sem nada a carregar."""
        from ..llm import local_embed
        from ..rag.index import embedder_status, is_local

        if self._closed or self._preloading:
            return False
        try:
            status = embedder_status(self.store)
        except Exception as e:  # banco ocupado: a primeira busca carrega
            log.debug("pré-carga da busca local: %s", e)
            return False
        if not status.ready or not is_local(status.model_key):
            return False
        self._local_timer.start()
        if local_embed.loaded():
            return False
        folder = local_embed.model_dir(self.store.get_setting("pipeline.models_dir"))
        self._preloading = True
        self._start(functools.partial(local_embed.preload, folder), self._on_preloaded)
        return True

    def _on_preloaded(self, result: Any) -> None:
        self._preloading = False
        if isinstance(result, BaseException):                    # a busca cai para "por palavras", com o motivo no selo
            log.warning("pré-carga da busca local falhou: %s", result)
        elif result:
            log.info("busca local pré-carregada em %.0f ms (Histórico)", float(result))

    def _release_local(self) -> None:
        from ..llm import local_embed

        if local_embed.release_if_idle(LOCAL_IDLE_S) or not local_embed.loaded():
            self._local_timer.stop()

    def _watch_local(self) -> None:
        """Uma busca por sentido ou uma pergunta pode abrir a sessão do e5 de novo depois da soltura: a conferência de
        ociosidade volta a rodar (ela mesma para quando não há sessão; com um pago, para no primeiro disparo)."""
        if not self._closed and not self._local_timer.isActive():
            self._local_timer.start()

    def _start(self, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> QThread:
        task = run_task(fn, self.store, on_done)
        self._tasks.add(task)
        task.finished.connect(functools.partial(self._tasks.discard, task))
        return task

    def reload_meetings(self) -> None:
        self._list_gen += 1
        gen = self._list_gen
        store, pid, (date_from, date_to) = self.store, self.person_id(), period_range(self.period_key(), self.today())

        def load() -> tuple[list[MeetingLine], str]:
            from ..rag.index import embedder_status

            key = embedder_status(store).model_key
            coverage = coverage_text(store.rag.coverage(key), len(store.rag.processed_meetings()))
            return load_meetings(store, pid, date_from, date_to), coverage

        self.list_note.setText("carregando…")
        self._start(load, functools.partial(self._on_meetings, gen))

    def _on_meetings(self, gen: int, result: Any) -> None:
        if self._closed or gen != self._list_gen:
            return
        if isinstance(result, BaseException):
            self.list_note.setText(f"Não consegui carregar as reuniões: {result}")
            return
        lines, coverage = result
        self.model.replace(lines)
        header = self.table.horizontalHeader()
        self.model.sort(header.sortIndicatorSection(), header.sortIndicatorOrder())
        self.coverage.setText(coverage)
        filt = self.filters_text()
        self.list_note.setText(f"{len(lines)} reunião(ões)" + (f" ({filt})" if filt else "") + "; Enter ou duplo clique abre a reunião.")

    def _meeting_activated(self, index: QModelIndex) -> None:
        mid = self.model.data(self.model.index(index.row(), 0), Qt.ItemDataRole.UserRole)
        if mid is not None:
            self._open(int(mid))

    def _open(self, meeting_id: int, tab: str | None = None, t_ms: int | None = None, play: bool = False) -> None:
        if self.on_open is not None:
            self.on_open(meeting_id, tab, t_ms, play)

    # ---------------------------------------------------------------- busca (U-024)
    def focus_search(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()
        self.tabs.setCurrentIndex(0)

    def _text_changed(self, text: str) -> None:
        if text.strip():
            self._words_timer.start()
            self._vector_timer.start()
            return
        self._words_timer.stop()
        self._vector_timer.stop()
        self._gen += 1                                   # a busca em voo, se houver, chega velha
        self.result = None
        self._hits = {}
        self.badge.setText("")
        self.stack.setCurrentWidget(self.table)

    def _search_now(self) -> None:
        self._words_timer.stop()
        self._vector_timer.stop()
        self.run_search(with_vector=True)

    def run_search(self, with_vector: bool) -> None:
        """Uma busca numa `Task`; só a da última geração chega à tela. Com `with_vector`, primeiro o vetor da consulta
        (pago, com cache; sem provedor ou sem rede volta o motivo e a busca segue por palavras)."""
        query = self.search.text().strip()
        if not query:
            return
        if with_vector:
            self._words_timer.stop()
        self._gen += 1
        gen = self._gen
        store, filters, stop = self.store, self.filters(), self._stop.is_set
        if with_vector:
            def fn() -> SearchResult:
                return rag_search.search(store, query, filters, query_vector=rag_search.embed_query(store, query, should_stop=stop))
        else:
            def fn() -> SearchResult:
                return rag_search.search(store, query, filters)
        if with_vector and self.result is not None and self.result.query == query:
            self.badge.setText(self.badge.text().split(" · ")[0] + " · buscando por sentido…")
        log.debug("busca %d: %d caractere(s), %s", gen, len(query), "palavras e sentido" if with_vector else "palavras")
        self._start(fn, functools.partial(self._on_search, gen))
        if with_vector:
            self._watch_local()                          # o vetor da consulta pode reabrir a sessão do e5 solta antes

    def _on_search(self, gen: int, result: Any) -> None:
        if self._closed or gen != self._gen:
            return                                       # resultado velho: o usuário já digitou outra coisa
        if isinstance(result, BaseException):
            log.warning("busca %d falhou: %s", gen, type(result).__name__)
            self.badge.setText(f"<span style='color:{OVERDUE_COLOR}'>a busca falhou: {html.escape(str(result))}</span>")
            return
        log.info("busca %d: %d trecho(s) de %d reunião(ões)", gen, len(result.hits), len({h.meeting_id for h in result.hits}))
        self.show_result(result)

    def show_result(self, result: SearchResult) -> None:
        self.result = result
        self._hits = {h.chunk_id: h for h in result.hits}
        scroll = self.results.verticalScrollBar().value() if self.stack.currentWidget() is self.results else 0
        self.results.setHtml(results_html(result))
        self.results.verticalScrollBar().setValue(scroll if result.query == self.search.text().strip() else 0)
        self.stack.setCurrentWidget(self.results)
        meetings = len({h.meeting_id for h in result.hits})
        found = f"{len(result.hits)} trecho(s) em {meetings} reunião(ões)"
        if result.hybrid:
            self.badge.setText(f"<span style='color:#27ae60'>busca híbrida</span> · {found}")
        else:
            why = html.escape(friendly_reason(result.reason))
            self.badge.setText(f"<span style='color:#e67e22'>busca por palavras: {why}</span> · {found}")
        filt = self.filters_text()
        self.list_note.setText("Clique num trecho para abrir a reunião naquele ponto e ouvir; no título, para abrir a reunião."
                               + (f" Filtros: {filt}." if filt else ""))

    def _results_link(self, url: QUrl) -> None:
        link = url.toString()
        if link.startswith(MEETING_SCHEME):
            with suppress(ValueError):
                self._open(int(link.removeprefix(MEETING_SCHEME)))
            return
        hit = self._hit_from(link)
        if hit is not None:
            tab, t_ms, play = hit_target(hit)
            self._open(hit.meeting_id, tab, t_ms, play)

    def _hit_from(self, link: str) -> SearchHit | None:
        if not link.startswith(HIT_SCHEME):
            return None
        try:
            return self._hits.get(int(link.removeprefix(HIT_SCHEME)))
        except ValueError:
            return None

    def _hit_actions(self, _pos: QPoint, link: str) -> list[tuple[str, Callable[[], Any]]]:
        hit = self._hit_from(link)
        if hit is None:
            return []
        return [("Abrir neste ponto", lambda: self._results_link(QUrl(link))), ("Copiar com citação", lambda: self.copy_hit(hit))]

    def copy_hit(self, hit: SearchHit) -> str:
        plain, rich = hit_quote(hit)
        copy_rich(plain, rich)
        self.copied.append(plain)
        return plain

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Escape and self.search.text():
                self.search.clear()                      # Esc limpa a busca e volta à lista
                return True
            if event.key() == Qt.Key.Key_Down:           # seta para baixo: para a lista ou os trechos
                target = self.stack.currentWidget() or self.table
                target.setFocus(Qt.FocusReason.TabFocusReason)
                if target is self.table and self.model.rowCount() and not self.table.currentIndex().isValid():
                    self.table.selectRow(0)
                return True
        return super().eventFilter(watched, event)

    # ---------------------------------------------------------------- ações abertas (U-026)
    def reload_actions(self) -> None:
        date_from, date_to = period_range(self.period_key(), self.today())
        try:
            lines = load_actions(self.store, self.person_id(), date_from, date_to, self.today())
        except Exception as e:  # banco ocupado: a aba fica como estava
            log.warning("ações abertas: %s", e)
            return
        self.action_lines = lines
        self.action_table.setRowCount(len(lines))
        bold = QFont()
        bold.setBold(True)
        for r, line in enumerate(lines):
            for c, text in enumerate(line.texts()):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, line.action_id)
                if c == 3:
                    item.setToolTip("Enter ou duplo clique abre a reunião na evidência da ação")
                if line.overdue:
                    item.setForeground(QColor(OVERDUE_COLOR))
                    if c == 2:
                        item.setFont(bold)
                self.action_table.setItem(r, c, item)
        overdue = sum(1 for line in lines if line.overdue)
        filt = self.filters_text()
        self.actions_note.setText((f"{len(lines)} ação(ões) aberta(s)" + (f", {overdue} atrasada(s)" if overdue else "")
                                   + (f" ({filt})" if filt else "")) if lines else "Nenhuma ação aberta" + (f" ({filt})." if filt else "."))
        self.btn_done.setEnabled(bool(lines))
        self.btn_open_action.setEnabled(bool(lines))

    def selected_actions(self) -> list[ActionLine]:
        rows = sorted({i.row() for i in self.action_table.selectedIndexes()})
        return [self.action_lines[r] for r in rows if r < len(self.action_lines)]

    def mark_done(self) -> int:
        """"Marcar feita": grava o estado (com `revisions`) e reindexa a reunião; devolve quantas mudaram."""
        done = 0
        for line in self.selected_actions():
            if self.store.actions.set_status(line.action_id, ActionStatus.DONE):
                done += 1
                if self.on_edited is not None:
                    self.on_edited(line.meeting_id, "edit")
        self.reload_actions()
        return done

    def open_action(self) -> None:
        lines = self.selected_actions()
        if not lines:
            return
        line = lines[0]
        if line.t_ms is not None:
            self._open(line.meeting_id, "transcricao", int(line.t_ms), False)
        else:
            self._open(line.meeting_id, "acoes")

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
        self.chat_status.setText("procurando nas reuniões…")
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
        self.chat_status.setText(f"recebendo… {len(self._partial)} caracteres")

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
            self.chat_status.setText("Pergunta cancelada.")
        else:
            cost = f" · {fmt_usd(ans.cost_usd)}" if ans.cost_usd is not None else ""
            how = "busca híbrida" if ans.hybrid else "busca por palavras"
            stopped = " · interrompida" if ans.cancelled else ""
            self.chat_status.setText(f"{ans.linked} citação(ões) com link · {ans.excerpts} trecho(s) de {len(ans.aliases)} "
                                     f"reunião(ões) · {how}{cost}{stopped}")
        if ans.error or not ans.cancelled:
            self._answered_away(not ans.error)

    def _answered_away(self, ok: bool) -> None:
        """F5.3 (A-12): terminou com o Histórico fora de foco (o usuário foi para outro app ou o escondeu: a pergunta segue e
        grava): o controlador avisa. Parada pelo usuário ("Parar"), não."""
        if self.on_answer is not None and not self._chat_stopped and not self.window().isActiveWindow():
            self.on_answer(ok)

    def _chat_warning(self, text: str) -> None:
        self.chat_status.setText(f"<span style='color:#e67e22'>{html.escape(text)}</span>")

    def stop_chat(self) -> None:
        if self.chat_task is not None:
            self._chat_stopped = True
            self.chat_task.cancel()
            self.chat_status.setText("parando…")

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

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not event.spontaneous():                      # abrir (ou reabrir) a janela recarrega; voltar de minimizado, não
            self.refresh()

    # ---------------------------------------------------------------- saída
    def shutdown(self, wait_ms: int = TASK_WAIT_MS) -> None:
        """Saída do app (F5A-32): cancela a pergunta em voo e o vetor da consulta presos na rede (R7-03) e espera as buscas e
        a pergunta antes de o banco fechar; passado o prazo, avisa no log e espera o fim."""
        self._closed = True
        self._stop.set()
        self._words_timer.stop()
        self._vector_timer.stop()
        self._local_timer.stop()                         # a pré-carga da busca local em voo está em `_tasks`: esperada abaixo
        if self.chat_task is not None:
            self.chat_task.cancel()
        for task in list(self._tasks):
            wait_before_close(task, wait_ms, "uma tarefa do Histórico")


class HistoryWindow(QWidget):
    """A janela do Histórico (não modal, uma só): fechar só esconde (a conversa e a busca ficam; a pergunta em voo termina e
    grava); posição e tamanho em `ui.history_geometry`."""

    def __init__(self, store: Store, *, on_open: OpenMeeting | None = None, on_edited: Callable[..., Any] | None = None,
                 on_answer: Callable[[bool], Any] | None = None, today: Callable[[], date] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.store = store
        self.setWindowTitle("Histórico — listener-meet")
        self.setMinimumSize(640, 420)
        self.page = HistoryPage(store, on_open=on_open, on_edited=on_edited, on_answer=on_answer, today=today)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.page)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save_geometry)
        self._restoring = True
        self._restore_geometry()
        self._restoring = False

    def _restore_geometry(self) -> None:
        raw = self.store.get_setting("ui.history_geometry")
        try:
            x, y, w, h = (int(v) for v in raw.split(","))
        except ValueError:
            screen = QGuiApplication.primaryScreen().availableGeometry()
            w, h = min(1100, screen.width() - 80), min(760, screen.height() - 80)
            self.resize(w, h)
            self.move(screen.left() + (screen.width() - w) // 2, screen.top() + (screen.height() - h) // 3)
            return
        self.resize(max(640, w), max(420, h))
        clamped = clamp_to_screens(x, y, self.width(), self.height(), _screen_rects())
        if clamped is not None:
            self.move(*clamped)

    def save_geometry(self) -> None:
        g = self.geometry()
        with suppress(Exception):                        # banco fechado na saída do app: a anterior fica
            self.store.set_setting("ui.history_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")

    @override
    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        if not self._restoring and self.isVisible():
            self._save_timer.start()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if not self._restoring and self.isVisible():
            self._save_timer.start()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_timer.stop()
        self.save_geometry()
        super().closeEvent(event)


__all__ = ["ActionLine", "HistoryPage", "HistoryWindow", "LinkBrowser", "MeetingLine", "MeetingsModel", "coverage_text", "friendly_reason",
           "hit_quote", "hit_target", "is_overdue", "load_actions", "load_meetings", "period_range", "results_html", "search_filters"]
