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
`on_answer(ok)` (o aviso do controlador, cujo clique abre o Histórico); a pergunta cancelada não.
F5.4 L4 (U-084, U-085, U-080): a busca com a lupa, a cobertura do índice na linha das abas, o Estado da reunião em selo e o
custo à direita; o estado da busca num selo; nas ações abertas, a Ação estica e quebra a linha e o prazo vencido vai num selo
vermelho; os trechos e as respostas em HTML por classes (cores dos tokens). As funções puras e o modelo da lista moram em
`ui/history_data.py`; a janela abre dentro da área livre pelo `fit_to_screen` (A-17).
F5.6 L3 (U-093, A-40, P-04): a lista em seleção múltipla, com "Excluir…" no menu de contexto, num botão sob a lista e na tecla
Delete (só na tabela) e a confirmação única, em `ui/history_delete.py`; quem exclui é o controlador (`on_delete`). A montagem
da aba "Perguntar às reuniões" mora em `ui/history_chat.py`. F5.10 (D7): a conversa dela mora em
`ui/history_ask.py` (`AskMixin`)."""
from __future__ import annotations

import functools
import json
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QContextMenuEvent, QGuiApplication, QKeyEvent, QKeySequence, QMoveEvent, QResizeEvent, \
    QShortcut, QShowEvent
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy,
                               QStackedWidget, QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..log import get_logger
from ..model import ActionStatus
from ..rag import search as rag_search
from .common import clamp_to_screens, copy_rich, fit_to_screen, keep_until_destroyed
from .components import Badge, BadgeDelegate, HtmlView
from .history_ask import CHAT_EMPTY, AskMixin
from .history_chat import build_chat_tab
from .history_data import (ACTION_COLUMNS, DUE_COL, HIT_SCHEME, KIND_TAB, MEETING_COLUMNS, MEETING_SCHEME, PERIODS, STATUS_COL, STATUS_TEXT,
                           ActionLine, MeetingLine, MeetingsModel, coverage_text, fmt_int, friendly_reason, hit_link, hit_quote,
                           hit_target, is_overdue, load_actions, load_meetings, period_range, results_html, search_filters)
from .history_delete import MeetingsDelete, OnDelete
from .icons import tone_icon
from .tasks import StreamTask, run_task, wait_before_close
from .theme import METRICS, TONE_ROLE, Tone, note, surface_tokens
from .widget import _screen_rects

if TYPE_CHECKING:
    from ..rag.search import SearchHit, SearchResult
    from ..store import Store
    from ..store.rag import SearchFilters

log = get_logger("ui.history")

WORDS_DELAY_MS = 250             # a parte por palavra a cada pausa curta da digitação
VECTOR_DELAY_MS = 600            # o vetor da consulta (pago) no Enter ou depois de parar de digitar
TASK_WAIT_MS = 3000              # saída do app: espera cada busca ou pergunta em voo até isto
LOCAL_IDLE_S = 600.0             # F5.1 (A-11): a sessão do e5 local é solta depois de 10 min sem uso
LOCAL_RELEASE_TICK_MS = 60_000   # ...conferido a cada minuto
OVERDUE_TONE = Tone.DANGER       # F5.4 (A-11): a ação atrasada e a busca que falhou, no tom de perigo do tema
KEPT = 100                       # instrumentação dos testes: as últimas respostas e cópias

type OpenMeeting = Callable[..., Any]          # on_open(meeting_id, tab=None, t_ms=None, play=False)
type ContextActions = Callable[[QPoint, str], list[tuple[str, Callable[[], Any]]]]


def _table_look(table: QTableView) -> None:
    """U-058: o cabeçalho à esquerda, sem o número de linha, as linhas de 32 px no mínimo e a zebra."""
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setMinimumSectionSize(METRICS.control_h)
    table.verticalHeader().setDefaultSectionSize(METRICS.control_h)
    table.setAlternatingRowColors(True)


class LinkBrowser(HtmlView):
    """`QTextBrowser` do tema (`HtmlView`: HTML por classes, F5.4 A-10) que não navega sozinho (os links `listener://` vão
    para quem ouve `anchorClicked`) e acrescenta ao menu de contexto as ações de quem o usa (U-029), conforme o ponto e o
    link debaixo do cursor."""

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


class HistoryPage(AskMixin, QWidget):
    def __init__(self, store: Store, *, on_open: OpenMeeting | None = None, on_edited: Callable[..., Any] | None = None,
                 on_answer: Callable[[bool], Any] | None = None, today: Callable[[], date] | None = None,
                 on_delete: OnDelete | None = None, memory_rss: Callable[[], int | None] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.memory_rss = memory_rss                              # F5.10 (D-075): o teto de memória das consultas da base
        self.on_open = on_open
        self.on_edited = on_edited
        self.on_answer = on_answer                                # F5.3 (A-12): a resposta chegou com a janela fora de foco
        self._chat_stopped = False                                # "Parar": o usuário cancelou, não avisa
        self.today = today or (lambda: datetime.now().astimezone().date())
        self.result: SearchResult | None = None
        self.answers: deque[Any] = deque(maxlen=KEPT)             # instrumentação dos testes: as respostas
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
        self._expanded: set[int] = set()                          # F5.10: as respostas com o "Como cheguei aqui" aberto
        self.data_dialogs: list[Any] = []                         # F5.10 (U-097): os "Ver dados" abertos (testes)

        # --- barra de cima ---
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar em todas as reuniões…")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Palavras de qualquer reunião (sem acento e sem maiúsculas). Enter também busca pelo sentido.")
        self.search.installEventFilter(self)
        self.search_icon = self.search.addAction(tone_icon("search", Tone.SECONDARY), QLineEdit.ActionPosition.LeadingPosition)
        self.person = QComboBox()
        self.person.setToolTip("Só trechos, reuniões e ações desta pessoa")
        self.period = QComboBox()
        for key, label in PERIODS:
            self.period.addItem(label, key)
        self.badge = Badge("", Tone.NEUTRAL)            # U-084: o estado da busca num selo, a contagem ao lado
        self.badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.badge.hide()
        self.badge_count = QLabel("")
        self.badge_count.setTextFormat(Qt.TextFormat.PlainText)
        note(self.badge_count)
        self.badge_count.hide()                         # sem busca, a linha do estado some (nada entre a busca e as abas)
        self.coverage = QLabel("")                      # U-084: a cobertura do índice na linha das abas
        self.coverage.setTextFormat(Qt.TextFormat.PlainText)
        note(self.coverage)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(QLabel("Pessoa"))
        top.addWidget(self.person)
        top.addWidget(QLabel("Período"))
        top.addWidget(self.period)
        state = QHBoxLayout()
        state.addWidget(self.badge)
        state.addWidget(self.badge_count, 1)

        # --- Reuniões ---
        self.model = MeetingsModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)    # F5.6 (A-40): várias, para excluir
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        for col in range(len(MEETING_COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if col in (1, 3) else QHeaderView.ResizeMode.ResizeToContents)
        _table_look(self.table)
        self.table.setItemDelegateForColumn(STATUS_COL, BadgeDelegate(self.table))     # U-084: o Estado em selo
        self.table.activated.connect(self._meeting_activated)
        self.results = LinkBrowser(self._hit_actions)
        self.results.anchorClicked.connect(self._results_link)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.results)
        self.list_note = QLabel("")
        self.list_note.setTextFormat(Qt.TextFormat.PlainText)          # o filtro leva o nome da pessoa (R7-07)
        note(self.list_note)
        self.deleting = MeetingsDelete(self, self.table, self.model, on_delete)     # F5.6 L3 (U-093): menu, Delete e botão
        meetings = QWidget()
        ml = QVBoxLayout(meetings)
        ml.addWidget(self.stack, 1)
        ml.addLayout(self.deleting.row(self.list_note))

        # --- Ações abertas ---
        self.action_table = QTableWidget(0, len(ACTION_COLUMNS))
        self.action_table.setHorizontalHeaderLabels(list(ACTION_COLUMNS))
        self.action_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.action_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for col in range(len(ACTION_COLUMNS)):
            self.action_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch if col == 0 else QHeaderView.ResizeMode.ResizeToContents)
        _table_look(self.action_table)
        self.action_table.setWordWrap(True)                                   # U-080: a Ação estica e quebra a linha
        self.action_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.action_table.setItemDelegateForColumn(DUE_COL, BadgeDelegate(self.action_table))   # o prazo vencido em selo
        self.action_table.activated.connect(self._action_activated)
        self.action_lines: list[ActionLine] = []
        self.btn_done = QPushButton("Marcar feita")
        self.btn_done.setToolTip("Marca as ações selecionadas como feitas (fica no histórico de edições)")
        self.btn_done.clicked.connect(self.mark_done)
        self.btn_open_action = QPushButton("Abrir reunião")
        self.btn_open_action.clicked.connect(self.open_action)
        self.actions_note = QLabel("")
        self.actions_note.setTextFormat(Qt.TextFormat.PlainText)
        note(self.actions_note)
        arow = QHBoxLayout()
        arow.addWidget(self.actions_note, 1)
        arow.addWidget(self.btn_open_action)
        arow.addWidget(self.btn_done)
        actions = QWidget()
        al = QVBoxLayout(actions)
        al.addWidget(self.action_table, 1)
        al.addLayout(arow)

        # --- Perguntar às reuniões (a montagem em `ui/history_chat.py`) ---
        self.chat_view = LinkBrowser(self._answer_actions)
        (chat, self.threads, self.btn_new, self.chat_note, self.question, self.btn_ask, self.btn_stop,
         self.chat_status) = build_chat_tab(self, self.chat_view)

        self.tabs = QTabWidget()
        self.tabs.addTab(meetings, "Reuniões")
        self.tabs.addTab(actions, "Ações abertas")
        self.tabs.addTab(chat, "Perguntar às reuniões")
        self.tabs.setCornerWidget(self.coverage, Qt.Corner.TopRightCorner)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(state)
        lay.addWidget(self.tabs, 1)

        self._words_timer = QTimer(self)
        self._words_timer.setSingleShot(True)
        self._words_timer.setInterval(WORDS_DELAY_MS)
        self._words_timer.timeout.connect(self._search_words)                 # R-06: métodos, não lambdas com `self`
        self._vector_timer = QTimer(self)
        self._vector_timer.setSingleShot(True)
        self._vector_timer.setInterval(VECTOR_DELAY_MS)
        self._vector_timer.timeout.connect(self._search_vector)
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

    def retheme(self) -> None:
        """A troca de tema (A-10): a lupa da busca nos tokens novos (o ícone de uma ação não é varrido pelo tema)."""
        self.search_icon.setIcon(tone_icon("search", Tone.SECONDARY, surface_tokens(self)))

    def _action_activated(self, _index: QModelIndex) -> None:
        self.open_action()

    def _search_words(self) -> None:
        self.run_search(with_vector=False)

    def _search_vector(self) -> None:
        self.run_search(with_vector=True)

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
        self._set_badge("")
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
            self.badge_count.setText("buscando por sentido…")
            self.badge_count.show()
        log.debug("busca %d: %d caractere(s), %s", gen, len(query), "palavras e sentido" if with_vector else "palavras")
        self._start(fn, functools.partial(self._on_search, gen))
        if with_vector:
            self._watch_local()                          # o vetor da consulta pode reabrir a sessão do e5 solta antes

    def _on_search(self, gen: int, result: Any) -> None:
        if self._closed or gen != self._gen:
            return                                       # resultado velho: o usuário já digitou outra coisa
        if isinstance(result, BaseException):
            log.warning("busca %d falhou: %s", gen, type(result).__name__)
            self._set_badge(f"a busca falhou: {result}", OVERDUE_TONE)
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
            self._set_badge("busca híbrida", Tone.SUCCESS, found)
        else:
            self._set_badge(f"busca por palavras: {friendly_reason(result.reason)}", Tone.WARNING, found)
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
        if watched is self.__dict__.get("search") and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
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
        for r, line in enumerate(lines):
            for c, text in enumerate(line.texts()):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, line.action_id)
                if c == 3:
                    item.setToolTip("Enter ou duplo clique abre a reunião na evidência da ação")
                if line.overdue and c == DUE_COL:
                    item.setData(TONE_ROLE, OVERDUE_TONE.value)      # U-080: o selo vermelho, no tom do tema (A-05)
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

    def _set_badge(self, text: str, value: Tone | None = None, count: str = "") -> None:
        """O estado da busca num selo (verde híbrida, âmbar só palavras, vermelho falhou) e a contagem ao lado (U-084)."""
        self.badge.setText(text)
        self.badge.set_tone(value if value is not None else Tone.NEUTRAL)
        self.badge.setVisible(bool(text))
        self.badge_count.setText(count)
        self.badge_count.setVisible(bool(count))

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
                 on_delete: OnDelete | None = None, memory_rss: Callable[[], int | None] | None = None, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.store = store
        self.setWindowTitle("Histórico — listener-meet")
        self.setMinimumSize(640, 420)
        self.page = HistoryPage(store, on_open=on_open, on_edited=on_edited, on_answer=on_answer, today=today, on_delete=on_delete,
                                memory_rss=memory_rss)
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
        keep_until_destroyed(self)                       # R-06: a coleta de ciclos não a apaga antes do Qt

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        fit_to_screen(self)                              # A-17: a janela inteira, com a moldura, na área livre

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


__all__ = ["ACTION_COLUMNS", "CHAT_EMPTY", "KIND_TAB", "LOCAL_IDLE_S", "MEETING_COLUMNS", "PERIODS", "STATUS_TEXT", "ActionLine",
           "HistoryPage", "HistoryWindow", "LinkBrowser", "MeetingLine", "MeetingsModel", "coverage_text", "fmt_int", "friendly_reason",
           "hit_link", "hit_quote", "hit_target", "is_overdue", "load_actions", "load_meetings", "period_range", "results_html",
           "search_filters"]
