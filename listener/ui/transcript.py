"""Leitura da transcrição (F2 item 14, Q6): `QListView` com modelo (milhares de falas sem milhares de widgets), linhas
`[mm:ss] Nome — Cargo: texto`. F3 item 8 (P4): mostra a transcrição revisada quando existe, com alternância para a original, e
"Revisar transcrição pela LLM" sob demanda com o motivo de quando foi pulada (F3.1 R3-05).

F5 L4 (Q6): a janela "Transcrição" vira a aba "Transcrição" da reunião aberta (`TranscriptTab`). O clique numa fala (ou
Enter) toca pelo player único do app, a partir da primeira palavra menos 150 ms (R-14), com L e R misturados até parar;
`select_time` acha a fala de início mais perto de um tempo, seleciona e rola até ela ("abrir neste ponto"); a recarga só
refaz a lista quando as falas mudaram e, refazendo, mantém a fala selecionada e a rolagem (o fim de um job não leva a
leitura para o topo); o menu de contexto tem "Tocar daqui" e "Copiar com citação" (U-029: texto e HTML).

F5.1 (U-025, A-17): tocando esta reunião, a fala do instante fica destacada (fundo âmbar com uma faixa à esquerda) e rola
para o meio da lista quando sai da vista, salvo nos 5 s depois de o usuário rolar (barra, roda ou teclado; Q2); a seleção
não muda (o "Tocar" da barra segue a fala selecionada). O destaque é um papel do modelo (`BackgroundRole` da linha), achado
por `bisect` com 150 ms de antecedência (a fala clicada toca da primeira palavra menos 150 ms) e, entre falas, fica na
última que começou. As duas linhas são repintadas sem `dataChanged`: neste `QListView` com quebra de linha e alturas
variáveis cada `dataChanged` refaz o layout da lista inteira (200 a 360 ms com 3.000 falas, no Windows em 26/10); pelo
mesmo motivo não há negrito, que mudaria a quebra das linhas sem o layout refeito. Recarregar reaplica o destaque; pausado,
fica; parado ou com outra reunião tocando, some. F5.4 (A-11): a faixa no tom de aviso e o fundo no fundo dele, dos tokens do
tema vigente (claro e escuro), refeitos quando o tema muda.

F5.4 L4: a fala pelo `SpeechDelegate` (U-081: o [mm:ss] em legenda e o ponto na cor da faixa do falante na linha do tempo,
o nome em negrito), a voz sem pessoa como "Voz N" (U-035; F5.5 A-18: o nome de `review.transcript_rows`, o mesmo da ata,
da exportação e da busca; o `DisplayRole` leva o mesmo nome) e, sem falas, o estado vazio (U-059). A barra de rolagem fica
sempre à vista: a largura das falas não muda quando ela aparece, e a altura de cada uma é medida uma vez só. F5.18 (PF-21): o
layout da lista é em lotes de 200 falas (`Batched`): a primeira tela sai antes de medir as 3.000, e a rolagem até uma fala
ainda sem lugar (abrir neste ponto, a recarga, o destaque que segue o som) espera o lote dela (`_when_laid_out`).

F5.11 L2: o rodapé foi para `ui/transcript_footer.py` (A-35); na lista, o Espaço pausa e retoma pelo `on_space` (o da barra
do player da reunião, P-06), e o Enter segue tocando a fala."""
from __future__ import annotations

import bisect
import functools
import itertools
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QObject, QPersistentModelIndex, QPoint, Qt, QTimer
from PySide6.QtGui import QBrush, QGradient, QKeyEvent, QLinearGradient, QShowEvent
from PySide6.QtWidgets import QAbstractItemView, QListView, QMenu, QStackedWidget, QVBoxLayout, QWidget

from ..log import get_logger
from ..pipeline import review
from ..rag import export
from .common import copy_rich
from .components import EmptyState
from .speech_delegate import COLOR_ROLE, ROLE_ROLE, SPEAKER_ROLE, STAMP_ROLE, TEXT_ROLE, SpeechDelegate
from .theme import Tokens, Tone, qcolor, tokens
from .timeline import YOU, build_lanes, lane_of_rows
from .transcript_footer import TranscriptFooterMixin

if TYPE_CHECKING:
    from ..store import Store
    from .player import Player

log = get_logger("ui.transcript")

FOLLOW_PAUSE_S = 5.0             # Q2: o destaque não rola nos 5 s seguintes a uma rolagem do usuário
LAYOUT_BATCH = 200               # F5.18 (PF-21): falas medidas por lote do layout da lista (`QListView.LayoutMode.Batched`)
LAYOUT_POLL_MS = 15              # a rolagem a uma fala ainda sem lugar na lista confere de novo a cada 15 ms...
LAYOUT_WAIT_MS = 3000            # ...e desiste de esperar o lote dela depois de 3 s (a aba escondida não mede)
NAV_KEYS = frozenset({Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End})
PLAYING_TONE = Tone.WARNING              # âmbar: o fundo dele é legível com o texto nos dois temas e diferente da seleção
PLAYING_ACCENT_SPAN = 0.005              # da largura da linha (~5 px numa lista de 1.000 px)
EMPTY_TEXT = "Sem falas nesta reunião: a transcrição aparece quando o processamento terminar."
SPEECH_ROLES = {STAMP_ROLE: "t_start_ms", SPEAKER_ROLE: "name", ROLE_ROLE: "role", TEXT_ROLE: "shown_text"}


def playing_brush(t: Tokens) -> QBrush:
    """O fundo da fala que toca: a faixa forte à esquerda e o fundo âmbar no resto (relativo ao retângulo da linha)."""
    grad = QLinearGradient(0.0, 0.0, 1.0, 0.0)
    grad.setCoordinateMode(QGradient.CoordinateMode.ObjectBoundingMode)
    accent, tint = qcolor(t.tone(PLAYING_TONE)), qcolor(t.background(PLAYING_TONE))
    for stop, color in ((0.0, accent), (PLAYING_ACCENT_SPAN, accent), (PLAYING_ACCENT_SPAN + 1e-4, tint), (1.0, tint)):
        grad.setColorAt(stop, color)
    return QBrush(grad)


class SegmentsModel(QAbstractListModel):
    """As falas (com o nome da tela, U-035) e a cor de cada uma (o índice da faixa do falante, U-081). F5.11 (U-108, A-26): a
    pessoa marcada como você aparece como "Você", sem cargo; o nome segue na ata, na exportação, na citação e na LLM."""

    def __init__(self, rows: list[review.TranscriptRow], parent: Any = None, *, me_id: int | None = None):
        super().__init__(parent)
        self.highlight: int | None = None                 # U-025: a linha da fala que toca (papel `BackgroundRole`)
        self._brush: tuple[Tokens, QBrush] | None = None  # F5.4: refeito quando os tokens do tema mudam
        self.me_id = me_id
        self.rows = rows
        self._index()

    def _index(self) -> None:
        """Começos, fins e o maior fim até cada fala: a fala que toca sai por `bisect` (A-17)."""
        self.starts = [r.t_start_ms for r in self.rows]
        self.ends = [max(r.t_end_ms, r.t_start_ms) for r in self.rows]
        self.reach = list(itertools.accumulate(self.ends, max))
        self.colors = lane_of_rows(build_lanes(self.rows, self.me_id), len(self.rows))

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, SPEAKER_ROLE, ROLE_ROLE):
            name, job = (YOU, "") if self.me_id is not None and row.person_id == self.me_id else (row.name, row.role)
            if role != Qt.ItemDataRole.DisplayRole:
                return name if role == SPEAKER_ROLE else job
            return f"[{row.stamp}] {f'{name} — {job}' if job else name}: {row.shown_text}"
        if role == Qt.ItemDataRole.ToolTipRole:
            span = f"{row.t_start_ms / 1000:.1f} a {row.t_end_ms / 1000:.1f} s".replace(".", ",")
            return f"canal {row.channel} · {span} · clique para ouvir daqui"
        if role == Qt.ItemDataRole.BackgroundRole and index.row() == self.highlight:
            return self._playing_brush()
        if role == COLOR_ROLE:
            return self.colors[index.row()]
        attr = SPEECH_ROLES.get(role)                     # os papéis do `SpeechDelegate` (U-081)
        return getattr(row, attr) if attr is not None else None

    def _playing_brush(self) -> QBrush:
        t = tokens()
        if self._brush is None or self._brush[0] is not t:
            self._brush = (t, playing_brush(t))
        return self._brush[1]

    def replace(self, rows: list[review.TranscriptRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.highlight = None
        self._index()
        self.endResetModel()


def playing_row(starts: Sequence[int], ends: Sequence[int], reach: Sequence[int], t_ms: int) -> int | None:
    """A fala que toca em `t_ms` (A-17, por `bisect`): a de início mais tarde que ainda não acabou; entre falas, a última que
    começou; antes da primeira, None. `reach` é o maior fim até cada fala (para a busca para trás parar cedo)."""
    i = bisect.bisect_right(starts, t_ms) - 1
    j = i
    while j >= 0 and reach[j] > t_ms:
        if ends[j] > t_ms:
            return j
        j -= 1
    return i if i >= 0 else None


def nearest_row(rows: list[review.TranscriptRow], t_ms: int) -> int | None:
    """A fala de `t_start_ms` mais perto de `t_ms` (a primeira, no empate); None sem falas."""
    best: tuple[int, int] | None = None
    for i, r in enumerate(rows):
        d = abs(r.t_start_ms - int(t_ms))
        if best is None or d < best[0]:
            best = (d, i)
    return best[1] if best is not None else None


class TranscriptTab(TranscriptFooterMixin, QWidget):
    def __init__(self, store: Store, meeting_id: int, parent: QWidget | None = None, *, player: Player | None = None,
                 on_launch: Callable[[bool], Any] | None = None, title: Callable[[], str] | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.player = player
        self.on_launch = on_launch
        self.title_fn = title
        self.requested: list[int] = []                    # jobs `llm` de revisão pedidos aqui: instrumentação dos testes
        self.played: deque[tuple[int, int]] = deque(maxlen=100)   # (t0, fim da fala) pedidos: instrumentação dos testes
        self.followed: deque[int] = deque(maxlen=100)     # instrumentação dos testes: as falas que o destaque trouxe à vista
        self.prefer_llm = True
        self.player_keys: dict[int, Callable[[], Any]] = {}   # P-06 e R-02 (F5.11): Espaço, ← e → na lista vão à barra
        self.playing_ms: int | None = None                # U-025: onde o som desta reunião está (None: parado)
        self._user_scroll_at = float("-inf")
        me = store.get_me()
        self.model = SegmentsModel(self._rows(), self, me_id=int(me["id"]) if me else None)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(SpeechDelegate(self.view))
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.view.setWordWrap(True)
        self.view.setUniformItemSizes(False)
        self.view.setAlternatingRowColors(True)
        self.view.setSpacing(1)
        self.view.setLayoutMode(QListView.LayoutMode.Batched)     # F5.18 (PF-21): as 3.000 falas medidas em lotes, não no
        self.view.setBatchSize(LAYOUT_BATCH)                      # primeiro layout (200 a 360 ms) nem a cada redimensionamento
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.clicked.connect(self._clicked)
        self.view.installEventFilter(self)                # Enter toca a fala selecionada (P-06); setas e páginas rolam
        self._viewport = self.view.viewport()
        self._viewport.installEventFilter(self)           # a roda do mouse é rolagem do usuário (Q2)
        self.view.verticalScrollBar().actionTriggered.connect(self.user_scrolled)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        bottom = self._build_footer()                    # F5.11 (A-35): o rodapé, de `ui/transcript_footer.py`
        self._empty: EmptyState | None = None                            # U-059: sem falas, o estado vazio (sob demanda)
        self.empty_text = EMPTY_TEXT                                     # F5.11 (U-114): processada sem fala, o porquê
        self._deferred = False
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        lay = QVBoxLayout(self)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.status)
        lay.addWidget(self.llm_note)
        lay.addLayout(bottom)
        self._update_status()

    @property
    def empty(self) -> EmptyState:
        if self._empty is None:
            self._empty = EmptyState("document", self.empty_text)
            self.stack.addWidget(self._empty)
        return self._empty

    def set_empty_text(self, text: str) -> None:
        self.empty_text = text
        if self._empty is not None:
            self._empty.label.setText(text)

    def _page(self) -> None:
        """A lista ou o estado vazio; o vazio de uma aba escondida nasce quando ela aparece (a abertura não o cria)."""
        rows = self.model.rowCount() > 0
        self._deferred = not rows and self._empty is None and not self.isVisible()
        if not self._deferred:
            self.stack.setCurrentWidget(self.view if rows else self.empty)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._deferred:
            self._page()

    # ---------------------------------------------------------------- leitura
    def _rows(self) -> list[review.TranscriptRow]:
        """As falas com o nome da tela: "Voz N" no lugar do rótulo interno da voz sem pessoa (U-035), já na leitura (F5.5)."""
        return review.transcript_rows(self.store, self.meeting_id, self.prefer_llm)

    def reload(self, force: bool = False) -> bool:
        """Relê as falas; sem mudança, a lista fica como está (seleção e rolagem); com mudança, refaz e devolve as duas. True
        quando refez (o `modelReset` já avisou quem segue as falas, como a linha do tempo)."""
        rows = self._rows()
        changed = force or rows != self.model.rows
        if changed:
            current = self.current_row()
            bar = self.view.verticalScrollBar()
            scroll = bar.value()
            self.model.replace(rows)
            if current is not None:
                i = next((k for k, r in enumerate(rows) if r.segment_id == current.segment_id), None)
                if i is not None:
                    self.view.setCurrentIndex(self.model.index(i))
            if not force:                                     # depois do layout das linhas (em lotes: a última fala medida)
                self._when_laid_out(self.model.index(len(rows) - 1), functools.partial(self._restore_scroll, bar, scroll))
            self._highlight(self._row_at(self.playing_ms), follow=False)                     # o destaque volta (A-17)
        self._update_status()
        return changed

    # ---------------------------------------------------------------- navegação e som
    def current_row(self) -> review.TranscriptRow | None:
        index = self.view.currentIndex()
        return self.model.rows[index.row()] if index.isValid() and index.row() < len(self.model.rows) else None

    def current_play_from(self) -> int:
        """Onde "Tocar" da barra começa: a fala selecionada, senão o começo."""
        row = self.current_row()
        return row.play_from_ms if row is not None else 0

    def select_time(self, t_ms: int) -> review.TranscriptRow | None:
        """Seleciona a fala de início mais perto de `t_ms` e rola até ela (no meio da lista); None sem falas."""
        i = nearest_row(self.model.rows, t_ms)
        if i is None:
            return None
        index = self.model.index(i)
        self.view.setCurrentIndex(index)
        self._when_laid_out(index, functools.partial(self.view.scrollTo, index, QAbstractItemView.ScrollHint.PositionAtCenter))
        return self.model.rows[i]

    def _when_laid_out(self, index: QModelIndex, fn: Callable[[], Any], waited_ms: int = 0) -> None:
        """Roda `fn` quando a fala já tem lugar na lista. No layout em lotes (F5.18, PF-21) uma fala depois do primeiro lote só
        ganha retângulo quando o lote dela chega, uns ms depois; antes disso o `scrollTo` não teria onde parar (o `singleShot(0)`
        de antes esperava só o layout único). Confere a cada `LAYOUT_POLL_MS`; sem lugar em `LAYOUT_WAIT_MS` (a aba escondida),
        roda assim mesmo."""
        if not index.isValid() or self.view.visualRect(index).isValid() or waited_ms >= LAYOUT_WAIT_MS:
            fn()
            return
        QTimer.singleShot(LAYOUT_POLL_MS, self, functools.partial(self._when_laid_out, index, fn, waited_ms + LAYOUT_POLL_MS))

    @staticmethod
    def _restore_scroll(bar: Any, value: int) -> None:
        bar.setValue(min(value, bar.maximum()))

    def _clicked(self, index: QModelIndex) -> None:
        if index.isValid():
            self.play_row(index.row())

    def play_row(self, row: int) -> None:
        r = self.model.rows[row]
        self.played.append((r.play_from_ms, r.t_end_ms))
        if self.player is None:
            self.status.setText("Reprodução indisponível nesta janela.")
            return
        self.player.play_from(self.meeting_id, r.play_from_ms, title=self._title())

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # pelo `__dict__`: a coleta de ciclos pode limpar os atributos antes de o Qt apagar a janela (veja a revisão)
        if watched is self.__dict__.get("_viewport") and event.type() == QEvent.Type.Wheel:
            self.user_scrolled()
        elif watched is self.__dict__.get("view") and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() in NAV_KEYS:
                self.user_scrolled()
            elif event.key() in self.player_keys and not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
                self.player_keys[event.key()]()                  # P-06: o Espaço pausa e retoma, ← e → voltam e avançam
                return True
            elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                index = self.view.currentIndex()
                if index.isValid():
                    self.play_row(index.row())
                    return True
        return super().eventFilter(watched, event)

    # ---------------------------------------------------------------- destaque da fala que toca (U-025, A-17)
    def user_scrolled(self, *_args: object) -> None:
        self._user_scroll_at = time.monotonic()

    def show_playing(self, t_ms: int | None) -> None:
        """A fala que toca em `t_ms` fica destacada (None apaga); quando muda e sai da vista, a lista rola até ela, salvo nos
        5 s depois de o usuário rolar. A seleção fica como está."""
        self.playing_ms = t_ms
        self._highlight(self._row_at(t_ms), follow=True)

    def _row_at(self, t_ms: int | None) -> int | None:
        if t_ms is None:
            return None
        m = self.model
        return playing_row(m.starts, m.ends, m.reach, int(t_ms) + review.PLAY_LEAD_MS)

    def _highlight(self, row: int | None, follow: bool) -> None:
        old = self.model.highlight
        if row == old:
            return
        self.model.highlight = row
        for r in (old, row):              # só as duas linhas: `dataChanged` refaria o layout da lista inteira
            if r is not None and r < self.model.rowCount():
                self._viewport.update(self.view.visualRect(self.model.index(r)))
        if follow and row is not None and time.monotonic() - self._user_scroll_at >= FOLLOW_PAUSE_S:
            index = self.model.index(row)
            if not self._viewport.rect().contains(self.view.visualRect(index)):
                self._when_laid_out(index, functools.partial(self._follow, index, row))

    def _follow(self, index: QModelIndex, row: int) -> None:
        """A fala que toca rola para o meio da lista (quando já tem lugar nela, PF-21)."""
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.followed.append(row)

    # ---------------------------------------------------------------- copiar com citação (U-029)
    def _title(self) -> str:
        if self.title_fn is not None:
            return self.title_fn()
        m = self.store.get_meeting(self.meeting_id) or {}
        return str(m.get("title") or m.get("title_llm") or f"Reunião {self.meeting_id}")

    def quote_row(self, row: review.TranscriptRow) -> tuple[str, str]:
        """A fala "copiada com citação": `"<fala>" — <Nome — Cargo>, <título> (dd/mm/aaaa) [mm:ss]` em texto e HTML."""
        m = self.store.get_meeting(self.meeting_id) or {}
        speaker = f"{row.name} — {row.role}" if row.role else row.name
        return export.quote(row.shown_text, speaker, self._title(), str(m.get("started_at") or ""), row.t_start_ms)

    def copy_quote(self, row: review.TranscriptRow) -> str:
        plain, rich = self.quote_row(row)
        copy_rich(plain, rich)
        self.status.setText("Fala copiada com a citação.")
        return plain

    def _context_menu(self, pos: QPoint) -> None:
        index = self.view.indexAt(pos)
        if not index.isValid():
            return
        row = self.model.rows[index.row()]
        menu = QMenu(self)
        menu.addAction("Tocar daqui", lambda: self.play_row(index.row()))
        menu.addAction("Copiar com citação", lambda: self.copy_quote(row))
        menu.exec(self.view.viewport().mapToGlobal(pos))


__all__ = ["SegmentsModel", "TranscriptTab", "nearest_row", "playing_brush", "playing_row"]
