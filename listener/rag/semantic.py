"""F5.10 (D4): a camada semântica que a LLM consulta. O que tem regra do app é materializado em Python pelas funções que já
existem e entra na sandbox como tabela TEMP tipada: a reunião com o job vigente (`current_job_id`), o dia local
(`started_at[:10]`: o `date()` do SQLite converteria o fuso para UTC) e o título efetivo (`effective_title`); quem fala, pela
regra da F5.5 (`segments.speakers()`, `number_voices` e `speaker_of`, um falante por grupo de `speakers()`) e a participação
somada dele (calculada na hora custaria ~1 s em 1.000 reuniões, D4). Por cima, as visões TEMP em PT-BR (`VIEWS`); as que leem
o arquivo (falas, marcadores, ações, itens da ata, resumos, trechos, custos) leem ao vivo, com o job e as reuniões visíveis
vindos das TEMP. Os filtros da aba Reuniões valem aqui (D-074): só as reuniões filtradas entram na sandbox.

O cache fica no processo, por banco, com a impressão digital (`fingerprint`: os jobs do pipeline concluídos, a última
revisão, as pessoas, as reuniões e os resumos; o `data_version` não serve, toda pergunta grava `llm_calls`) e validade de
10 min para o caminho de escrita que ela não vê. Sem Qt."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, cast

from ..log import get_logger
from ..model import PIPELINE_JOB_KINDS, JobStatus, SummaryKind, SummaryStatus
from ..pipeline.voices import numbering, speaker_of
from ..store.meetings import effective_title
from ..store.segments import role_of
from .sandbox import Sandbox

if TYPE_CHECKING:
    import sqlite3

    from ..store import Store
    from ..store.rag import SearchFilters
    from ..store.segments import ClusterRow

log = get_logger("rag.semantic")

CACHE_TTL_S = 600.0
WEEKDAYS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
DAY = 3                                  # posição do `dia` na linha de `_reunioes`


def _stamp(col: str) -> str:
    """`mm:ss` de uma coluna em ms, como `prompts.common.stamp` (nulo fica nulo)."""
    return f"CASE WHEN {col} IS NULL THEN NULL ELSE printf('%02d:%02d', max(0, {col}) / 60000, max(0, {col}) / 1000 % 60) END"


TABLES: tuple[str, ...] = (
    "CREATE TEMP TABLE _agora(hoje TEXT, hora TEXT, dia_semana TEXT, fuso TEXT, filtrado INTEGER NOT NULL)",
    ("CREATE TEMP TABLE _reunioes(reuniao_id INTEGER PRIMARY KEY, job_id INTEGER, titulo TEXT, dia TEXT, hora TEXT, mes TEXT, "
     "dia_semana TEXT, duracao_min REAL, estado TEXT, plataforma TEXT, presencial INTEGER, idioma TEXT, participantes_previstos TEXT, "
     "tem_falas INTEGER, tem_ata INTEGER)"),
    ("CREATE TEMP TABLE _falantes(reuniao_id INTEGER, person_id INTEGER, cluster_id INTEGER, cluster TEXT, role_at_time TEXT, "
     "channel TEXT, falante TEXT, pessoa_id INTEGER, cargo TEXT)"),
    "CREATE INDEX temp.idx_falantes ON _falantes(reuniao_id, cluster_id)",
    ("CREATE TEMP TABLE _participacao(reuniao_id INTEGER, pessoa_id INTEGER, falante TEXT, cargo TEXT, canal TEXT, falas INTEGER, "
     "fala_min REAL, primeira_fala TEXT)"),
)
INSERTS: tuple[tuple[str, str], ...] = (("meetings", "INSERT INTO temp._reunioes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
                                        ("speakers", "INSERT INTO temp._falantes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"),
                                        ("participation", "INSERT INTO temp._participacao VALUES (?, ?, ?, ?, ?, ?, ?, ?)"))
# os valores fixos do app nas visões (estado da ata, espécie, ação superada) são as constantes de `model.py`, escritas aqui
VIEWS: dict[str, str] = {
    "agora": "SELECT hoje, hora, dia_semana, fuso FROM temp._agora",
    "reunioes": ("SELECT reuniao_id, titulo, dia, hora, mes, dia_semana, duracao_min, estado, plataforma, presencial, idioma, "
                 "participantes_previstos, tem_falas, tem_ata FROM temp._reunioes"),
    "pessoas": ("SELECT id AS pessoa_id, name AS nome, NULLIF(role, '') AS cargo, NULLIF(company, '') AS empresa, is_me AS eu "
                "FROM main.people"),
    "participacao": "SELECT reuniao_id, pessoa_id, falante, cargo, canal, falas, fala_min, primeira_fala FROM temp._participacao",
    "falas": (f"SELECT s.id AS fala_id, s.meeting_id AS reuniao_id, {_stamp('s.t_start_ms')} AS inicio, s.t_start_ms AS inicio_ms, "  # noqa: S608
              "s.t_end_ms AS fim_ms, f.falante AS falante, f.pessoa_id AS pessoa_id, f.cargo AS cargo, s.channel AS canal, "
              "CASE WHEN COALESCE(s.text_llm, '') != '' THEN s.text_llm ELSE COALESCE(s.text, '') END AS texto "
              "FROM temp._reunioes r JOIN main.segments s ON s.meeting_id = r.reuniao_id AND (r.job_id IS NULL OR s.job_id = r.job_id) "
              "LEFT JOIN temp._falantes f ON f.reuniao_id = s.meeting_id AND f.cluster_id IS s.cluster_id AND f.person_id IS s.person_id "
              "AND f.cluster IS s.cluster AND f.role_at_time IS s.role_at_time AND f.channel IS s.channel"),
    "marcadores": (f"SELECT k.id AS marcador_id, k.meeting_id AS reuniao_id, {_stamp('k.t_ms')} AS inicio, k.t_ms AS inicio_ms, "  # noqa: S608
                   "NULLIF(k.note, '') AS nota FROM temp._reunioes r JOIN main.markers k ON k.meeting_id = r.reuniao_id"),
    "acoes": ("SELECT a.id AS acao_id, a.meeting_id AS reuniao_id, a.text AS texto, COALESCE(p.name, NULLIF(a.owner_text, '')) AS dono, "  # noqa: S608
              f"a.owner_person_id AS dono_pessoa_id, NULLIF(a.due, '') AS prazo, a.status AS estado, {_stamp('a.t_ms')} AS inicio, "
              "a.t_ms AS inicio_ms, a.segment_id AS fala_id FROM temp._reunioes r JOIN main.action_items a ON a.meeting_id = r.reuniao_id "
              "JOIN main.summaries s ON s.id = a.summary_id AND s.status = 'atual' AND s.kind = 'minutes' "
              "LEFT JOIN main.people p ON p.id = a.owner_person_id WHERE a.status IS NOT 'superada'"),
    "ata_itens": (f"SELECT c.meeting_id AS reuniao_id, c.kind AS tipo, c.text AS texto, {_stamp('c.t_start_ms')} AS inicio, "  # noqa: S608
                  "c.t_start_ms AS inicio_ms FROM temp._reunioes r JOIN main.rag_chunks c ON c.meeting_id = r.reuniao_id "
                  "WHERE c.kind IN ('ata', 'decisao', 'destaque', 'pendencia', 'proximo_passo')"),
    "resumos": ("SELECT s.meeting_id AS reuniao_id, CASE s.kind WHEN 'minutes' THEN 'ata' WHEN 'executive' THEN 'executivo' "
                "WHEN 'pre_summary' THEN 'pre_resumo' ELSE 'parcial' END AS tipo, "
                "COALESCE(NULLIF(s.text_edited, ''), s.text_llm) AS texto, s.language AS idioma "
                "FROM temp._reunioes r JOIN main.summaries s ON s.meeting_id = r.reuniao_id "
                "WHERE s.status = 'atual' AND s.kind IN ('minutes', 'executive', 'pre_summary', 'partial')"),
    "trechos": (f"SELECT c.id AS trecho_id, c.meeting_id AS reuniao_id, c.kind AS tipo, {_stamp('c.t_start_ms')} AS inicio, "  # noqa: S608
                "c.t_start_ms AS inicio_ms, c.speakers AS falantes, c.text AS texto, f.rag_fts AS busca, f.rank AS relevancia "
                "FROM main.rag_fts f JOIN main.rag_chunks c ON c.id = f.rowid JOIN temp._reunioes r ON r.reuniao_id = c.meeting_id"),
    "custos_llm": ("SELECT l.id AS chamada_id, l.meeting_id AS reuniao_id, substr(l.created_at, 1, 10) AS dia, substr(l.created_at, 1, 7) "
                   "AS mes, l.task AS tarefa, l.provider AS provedor, l.model AS modelo, l.input_tokens AS tokens_entrada, "
                   "l.output_tokens AS tokens_saida, l.cached_tokens AS tokens_cache, l.cost_usd AS custo_usd, l.status AS status "
                   "FROM main.llm_calls l WHERE l.meeting_id IN (SELECT reuniao_id FROM temp._reunioes) "
                   "OR (l.meeting_id IS NULL AND (SELECT filtrado FROM temp._agora) = 0)"),
}


class BuildStopped(Exception):  # noqa: N818 - o nome diz o que houve: quem pediu desistiu
    """A montagem parou porque quem pediu desistiu (a pergunta foi cancelada)."""


@dataclass(frozen=True, slots=True)
class Layer:
    """O que vai às TEMP, de todas as reuniões (os filtros são aplicados ao abrir a sandbox)."""
    fingerprint: tuple[Any, ...]
    meetings: tuple[tuple[Any, ...], ...]          # `_reunioes`, com o job vigente
    speakers: tuple[tuple[Any, ...], ...]          # `_falantes`: um por grupo de `speakers()`
    participation: tuple[tuple[Any, ...], ...]     # `_participacao`
    built_ms: float

    def visible(self, filters: SearchFilters | None) -> frozenset[int]:
        """As reuniões dos filtros da aba Reuniões: o período pelo dia local e a pessoa entre quem falou."""
        keep = {int(m[0]) for m in self.meetings}
        if filters is None or not filters.active:
            return frozenset(keep)
        if filters.meeting_ids:
            keep &= {int(i) for i in filters.meeting_ids}
        day = {int(m[0]): str(m[DAY] or "") for m in self.meetings}
        if filters.date_from:
            keep = {i for i in keep if day[i] >= str(filters.date_from)}
        if filters.date_to:
            keep = {i for i in keep if day[i] and day[i] <= str(filters.date_to)}
        if filters.person_ids:
            wanted = {int(p) for p in filters.person_ids}
            keep &= {int(p[0]) for p in self.participation if p[1] is not None and int(p[1]) in wanted}
        return frozenset(keep)


def fingerprint(store: Store) -> tuple[Any, ...]:
    """A impressão digital do que as TEMP guardam (D4): mudou, o cache é refeito."""
    jobs = store._q("SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(SUM(id), 0) FROM processing_jobs WHERE status = ? "
                    "AND kind IN (?, ?)", (str(JobStatus.DONE), *PIPELINE_JOB_KINDS))[0]
    revision = store._q("SELECT COALESCE(MAX(id), 0) FROM revisions")[0]
    people = store._q("SELECT COUNT(*), COALESCE(MAX(updated_at), '') FROM people")[0]
    meetings = store._q("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM meetings")[0]
    summaries = store._q("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM summaries")[0]
    return tuple(tuple(r) for r in (jobs, revision, people, meetings, summaries))


def weekday(day: str | None) -> str | None:
    try:
        return WEEKDAYS[date.fromisoformat(str(day)).weekday()] if day else None
    except ValueError:
        return None


def stamp(t_ms: int | None) -> str | None:
    if t_ms is None:
        return None
    s = max(0, int(t_ms)) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def _meeting_row(m: Mapping[str, Any], job: int | None, spoken: bool, minutes: bool) -> tuple[Any, ...]:
    started = str(m.get("started_at") or "")
    day = started[:10] or None
    dur = m.get("duration_ms")
    return (int(m["id"]), job, effective_title(m), day, started[11:16] or None, started[:7] or None, weekday(day),
            round(int(dur) / 60000, 1) if dur is not None else None, m.get("status"), m.get("platform") or None,
            int(bool(m.get("is_in_room"))), m.get("language") or None, m.get("expected_participants") or None, int(spoken),
            int(minutes))


def _speaker_rows(mid: int, job: int | None, clusters: Sequence[Mapping[str, Any]], spoken: Sequence[Mapping[str, Any]],
                  people: Mapping[int, Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    """Um falante por grupo de `speakers()`, pela regra da leitura (F5.5): `speaker_of`, os números de `numbering` e o cargo
    do dia por `role_of`. Leva também a primeira fala, as falas e os ms do grupo (para a participação)."""
    by_id = cast("dict[int, ClusterRow]", {int(c["id"]): c for c in clusters})
    names = numbering(by_id.values(), job, spoken, "first")
    out: list[tuple[Any, ...]] = []
    for s in spoken:
        person, name = speaker_of(s, by_id, people, names)
        role = role_of(s, person) if person is not None else ""
        out.append((mid, s.get("person_id"), s.get("cluster_id"), s.get("cluster"), s.get("role_at_time"), s.get("channel"), name,
                    int(person["id"]) if person is not None else None, role or None, int(s["first"]), int(s.get("n") or 0),
                    int(s.get("ms") or 0)))
    return out


def _participation(rows: Iterable[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """A participação de uma reunião: os grupos somados por (pessoa, falante, cargo, canal)."""
    acc: dict[tuple[Any, ...], list[int]] = {}
    for r in rows:
        key = (r[0], r[7], r[6], r[8], r[5])
        first, n, ms = r[9], r[10], r[11]
        got = acc.setdefault(key, [first, 0, 0])
        got[0], got[1], got[2] = min(got[0], first), got[1] + n, got[2] + ms
    return [(*key, n, round(ms / 60000, 1), stamp(first)) for key, (first, n, ms) in acc.items()]


def build(store: Store, should_stop: Callable[[], bool] | None = None, fp: tuple[Any, ...] | None = None) -> Layer:
    """Monta a camada de todas as reuniões (uma leitura de `speakers()` e do job vigente por reunião, o resto numa consulta);
    `should_stop()` verdadeiro entre duas reuniões levanta `BuildStopped`."""
    t0 = time.perf_counter()
    fp = fp if fp is not None else fingerprint(store)
    people = {int(p["id"]): dict(p) for p in store.people.list_people()}
    clusters: dict[int, list[dict[str, Any]]] = {}
    for c in store.segments.light_clusters():
        clusters.setdefault(int(c["meeting_id"]), []).append(c)
    minutes = {int(r[0]) for r in store._q("SELECT DISTINCT meeting_id FROM summaries WHERE kind = ? AND status = ?",
                                           (str(SummaryKind.MINUTES), str(SummaryStatus.CURRENT)))}
    meetings: list[tuple[Any, ...]] = []
    speakers: list[tuple[Any, ...]] = []
    participation: list[tuple[Any, ...]] = []
    for m in store.list_meetings():
        if should_stop is not None and should_stop():
            raise BuildStopped
        mid = int(m["id"])
        job = store.segments.current_job_id(mid)
        spoken = store.segments.speakers(mid, job)
        mine = [c for c in clusters.get(mid, ()) if job is None or c.get("job_id") == job]
        rows = _speaker_rows(mid, job, mine, spoken, people)
        meetings.append(_meeting_row(m, job, bool(spoken), mid in minutes))
        speakers += [r[:9] for r in rows]
        participation += _participation(rows)
    took = round((time.perf_counter() - t0) * 1000, 1)
    log.info("camada semântica montada: %d reunião(ões), %d falante(s), em %.1f ms", len(meetings), len(speakers), took)
    return Layer(fp, tuple(meetings), tuple(speakers), tuple(participation), took)


class _Cache:
    """A camada por banco, no processo: vale enquanto a impressão digital não muda e por até 10 min."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[float, Layer]] = {}
        self.lock = threading.Lock()
        self.builds = 0                                       # instrumentação dos testes

    def get(self, store: Store, should_stop: Callable[[], bool] | None = None,
            clock: Callable[[], float] = time.monotonic) -> tuple[Layer, bool]:
        """(camada, veio do cache)."""
        key, fp = str(store.main_path), fingerprint(store)
        with self.lock:
            hit = self.entries.get(key)
        if hit is not None and hit[1].fingerprint == fp and clock() - hit[0] < CACHE_TTL_S:
            return hit[1], True
        layer = build(store, should_stop, fp)
        with self.lock:
            self.entries[key] = (clock(), layer)
            self.builds += 1
        return layer, False

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()


CACHE = _Cache()


def now_row(now: datetime, filtered: bool) -> tuple[Any, ...]:
    """A linha de `agora`: o dia, a hora, o dia da semana e o fuso do computador."""
    zone = now.strftime("%z")
    return (now.date().isoformat(), now.strftime("%H:%M"), WEEKDAYS[now.weekday()], f"{zone[:3]}:{zone[3:]}" if zone else "",
            int(filtered))


def fill(layer: Layer, visible: frozenset[int], now: datetime, filtered: bool) -> Callable[[sqlite3.Connection], None]:
    """O que a sandbox roda na transação de abertura: as TEMP só com as reuniões visíveis e as visões por cima."""
    rows = {"meetings": [m for m in layer.meetings if m[0] in visible], "speakers": [s for s in layer.speakers if s[0] in visible],
            "participation": [p for p in layer.participation if p[0] in visible]}

    def run(conn: sqlite3.Connection) -> None:
        for sql in TABLES:
            conn.execute(sql)
        conn.execute("INSERT INTO temp._agora VALUES (?, ?, ?, ?, ?)", now_row(now, filtered))
        for name, sql in INSERTS:
            conn.executemany(sql, rows[name])
        for view, select in VIEWS.items():
            conn.execute(f"CREATE TEMP VIEW {view} AS {select}")

    return run


def open_sandbox(store: Store, filters: SearchFilters | None = None, *, layer: Layer | None = None,
                 now: datetime | None = None) -> Sandbox:
    """A sandbox das visões com os filtros da aba Reuniões (só as reuniões filtradas) e o agora de quem pergunta."""
    layer = layer if layer is not None else CACHE.get(store)[0]
    filtered = filters is not None and filters.active
    return Sandbox.open(store.main_path, fill(layer, layer.visible(filters), now or datetime.now().astimezone(), filtered))


__all__ = ["CACHE", "CACHE_TTL_S", "TABLES", "VIEWS", "WEEKDAYS", "BuildStopped", "Layer", "build", "fill", "fingerprint", "now_row",
           "open_sandbox", "stamp", "weekday"]
