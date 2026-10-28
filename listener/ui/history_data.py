"""Os dados do Histórico (F5 item 8, U-024, U-026), fora do `ui/history.py`, que passava de 400 linhas (F5.4 L4): os filtros de
período, o motivo da busca só por palavras em texto para o usuário, a cobertura do índice, os trechos achados como HTML por
classes (U-085: nenhuma cor no HTML; as classes `note` e o link saem dos tokens na folha do `HtmlView`), o alvo e a citação
de um trecho, a lista de reuniões (`load_meetings`, `MeetingsModel`) e as ações abertas (`load_actions`).

F5.4 L4: no modelo das reuniões, o Estado em selo (U-084, o tom no `TONE_ROLE`, lido pelo `BadgeDelegate`; o texto fica no
`DisplayRole`) e o Custo alinhado à direita; nas ações abertas, o prazo em dd/mm/aaaa com "(atrasada)" (U-080)."""
from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, cast, override

from PySide6 import QtGui
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt

from ..llm.costs import fmt_usd
from ..llm.prompts.common import stamp
from ..model import ChunkKind, MeetingStatus
from ..rag import export
from ..rag import search as rag_search
from ..store.rag import SearchFilters
from ..store.segments import ClusterRow, resolve_person
from .actions_table import due_text, is_overdue
from .theme import TONE_ROLE, Tone

if TYPE_CHECKING:
    from ..rag.search import SearchHit, SearchResult
    from ..store import Store

PERIODS: tuple[tuple[str, str], ...] = (("all", "Qualquer data"), ("7", "Últimos 7 dias"), ("30", "Últimos 30 dias"),
                                        ("90", "Últimos 90 dias"), ("year", "Este ano"))
MEETING_COLUMNS = ("Data", "Título", "Duração", "Participantes", "Estado", "Custo")
STATUS_COL, COST_COL = 4, 5
ACTION_COLUMNS = ("Ação", "Dono", "Prazo", "Reunião")
DUE_COL = 2                      # U-080: o prazo (o vencido em selo)
STATUS_TEXT: dict[str, str] = {MeetingStatus.RECORDING: "gravando", MeetingStatus.PAUSED: "pausada", MeetingStatus.RECORDED: "gravada",
                               MeetingStatus.CANCELLED: "cancelada", MeetingStatus.INTERRUPTED: "interrompida",
                               MeetingStatus.PROCESSED: "falantes a revisar", MeetingStatus.REVIEWED: "revisada"}
# U-084: o Estado em selo, no tom de cada um (o texto junto: o estado nunca só pela cor, U-054)
STATUS_TONES: dict[str, Tone] = {MeetingStatus.RECORDING: Tone.DANGER, MeetingStatus.PAUSED: Tone.WARNING,
                                 MeetingStatus.RECORDED: Tone.INFO, MeetingStatus.CANCELLED: Tone.NEUTRAL,
                                 MeetingStatus.INTERRUPTED: Tone.WARNING, MeetingStatus.PROCESSED: Tone.WARNING,
                                 MeetingStatus.REVIEWED: Tone.SUCCESS}
KIND_TAB: dict[str, str] = {ChunkKind.EXECUTIVE: "resumo", ChunkKind.ACTION: "acoes", ChunkKind.DECISION: "ata", ChunkKind.HIGHLIGHT: "ata",
                            ChunkKind.PENDING: "ata", ChunkKind.NEXT_STEP: "ata", ChunkKind.MINUTES: "ata"}
HIT_SCHEME, MEETING_SCHEME = "listener://hit?c=", "listener://meeting?m="
RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


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
        return "<p class='note'>Nada encontrado com essas palavras e filtros.</p>"
    parts: list[str] = []
    for g in rag_search.group_by_meeting(result.hits):
        n = len(g.hits)
        title = html.escape(g.title or "Reunião sem título")
        parts.append(f"<p style='margin-top:12px; margin-bottom:2px'><a href='{MEETING_SCHEME}{g.meeting_id}'><b>{title}</b></a>"
                     f" <span class='note'>· {export.fmt_date(g.date)} · {n} {'trecho' if n == 1 else 'trechos'}</span></p>")
        for h in g.hits:
            when = f"▶ {stamp(h.t_start_ms)}" if h.t_start_ms is not None else "abrir"
            meta = " · ".join(x for x in (h.kind_label, html.escape(h.speakers)) if x)
            snippet = h.snippet_html or html.escape(" ".join(h.text.split())[:200])
            parts.append(f"<p style='margin-top:0; margin-bottom:6px; margin-left:16px'><a href='{hit_link(h)}'>{when}</a> "
                         f"<span class='note'>{meta}</span><br>{snippet}</p>")
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
        return self.text, self.owner, due_text(self.due, self.overdue), f"{self.meeting_title} — {export.fmt_date(self.meeting_date)}"


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
        if role == TONE_ROLE and index.column() == STATUS_COL:                    # U-084: o Estado em selo
            return STATUS_TONES.get(line.status, Tone.NEUTRAL).value
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == COST_COL:
            return RIGHT                                                         # U-084: o custo à direita
        return None

    @override
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(MEETING_COLUMNS):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return MEETING_COLUMNS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole and section == COST_COL:
            return RIGHT
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


__all__ = ["ACTION_COLUMNS", "DUE_COL", "HIT_SCHEME", "KIND_TAB", "MEETING_COLUMNS", "MEETING_SCHEME", "PERIODS", "STATUS_TEXT",
           "STATUS_TONES", "ActionLine", "MeetingLine", "MeetingsModel", "coverage_text", "fmt_int", "friendly_reason", "hit_link",
           "hit_quote", "hit_target", "is_overdue", "load_actions", "load_meetings", "period_range", "results_html", "search_filters"]
