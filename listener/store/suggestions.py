"""As sugestões ao vivo (F5.20, C9): `suggestions`, uma linha por pedido (o modo, a janela de legendas, a compactação, o fio,
o texto como chegou, a saída validada, o custo e os tempos), e `suggestion_options`, uma por opção (o tipo, a fala, os fatos
com o `t_ms` de cada legenda citada, a evidência e a avaliação: "Boa" 1, "Ruim" 0 com o motivo, "Usei"). Gravadas pelo app
(a tarefa da sugestão roda numa `StreamTask`); vão com a reunião, as opções pela cascata das sugestões; nada se apaga sozinho
(CP3): "desfazer" só volta a nota a nula. `rated_at` marca a última avaliação ("Boa", "Ruim" ou "Usei") e ordena o que entra no
pedido seguinte e no memorando. F5.21: `suggestion_screens`, a tela de um pedido (o estado, a fonte, o rótulo do app, o
tamanho do que foi enviado, a miniatura e a cópia de 960 px), `suggestions.screen_summary` (o resumo da tela, que os pedidos
seguintes leem) e `suggestion_options.screen` (a opção que usou a tela)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypedDict, cast

from ..model import Evidence, RatingReason, ScreenSource, ScreenStatus, SuggestStatus
from .core import Core, _now

RATED = "(o.rating IS NOT NULL OR o.used = 1)"         # uma opção avaliada: com nota ou usada
REASONS = frozenset(str(r) for r in RatingReason)


class SuggestionRow(TypedDict, total=False):
    id: int
    meeting_id: int
    t_ms: int
    mode: str
    nudge: str | None
    goal: str | None
    since_t_ms: int
    until_caption_id: int
    t_until_ms: int
    anchored: int
    raw_chars: int
    input_chars: int
    thread_summary: str | None
    data_json: str | None
    text_llm: str | None
    provider: str | None
    model: str | None
    prompt_version: str | None
    call_id: int | None
    cost_usd: float | None
    cost_estimated: int
    first_ms: int | None
    total_ms: int | None
    status: str
    error: str | None
    created_at: str
    screen_summary: str | None      # F5.21: o resumo da tela que a LLM devolveu


class OptionRow(TypedDict, total=False):
    id: int
    suggestion_id: int
    idx: int
    kind: str
    text: str
    facts_json: str
    evidence: str
    rating: int | None
    reason: str | None
    used: int
    rated_at: str | None
    screen: int                     # F5.21: a opção usou a tela


@dataclass(frozen=True, slots=True)
class SuggestionInsert:
    meeting_id: int
    t_ms: int
    mode: str
    nudge: str | None
    goal: str | None
    since_t_ms: int
    until_caption_id: int
    t_until_ms: int
    anchored: bool
    raw_chars: int
    input_chars: int
    thread_summary: str | None
    data_json: str | None
    text_llm: str | None
    provider: str | None
    model: str | None
    prompt_version: str | None
    call_id: int | None
    cost_usd: float | None
    cost_estimated: bool
    first_ms: int | None
    total_ms: int | None
    status: SuggestStatus
    error: str | None
    screen_summary: str | None = None       # F5.21: o resumo da tela que a LLM devolveu


@dataclass(frozen=True, slots=True)
class OptionInsert:
    idx: int
    kind: str
    text: str
    facts_json: str                 # [{"caption_id", "t_ms"}]
    evidence: Evidence
    screen: bool = False            # F5.21: a opção usou a tela


@dataclass(frozen=True, slots=True)
class ScreenInsert:
    """F5.21: a tela de um pedido. `thumb` sempre (o chat e a aba a mostram); `data`, a cópia de 960 px, só com
    `suggest.screen_keep`; nunca a imagem enviada, de 1.600 px (A-23)."""
    suggestion_id: int
    status: ScreenStatus
    source: ScreenSource
    app: str                        # "janela do Teams", "monitor 2": nunca o título da janela (A-06)
    width: int
    height: int
    sent_bytes: int
    covered: int
    thumb: bytes | None
    data: bytes | None


class ScreenRow(TypedDict, total=False):
    suggestion_id: int
    status: str
    source: str
    app: str
    width: int
    height: int
    bytes: int
    covered: int
    thumb: bytes | None
    t_ms: int


class SuggestionsRepo:
    def __init__(self, core: Core):
        self.core = core

    # ---------------------------------------------------------------- gravar
    def insert(self, row: SuggestionInsert) -> int:
        return self.core.insert(
            "INSERT INTO suggestions(meeting_id, t_ms, mode, nudge, goal, since_t_ms, until_caption_id, t_until_ms, anchored, raw_chars, "
            "input_chars, thread_summary, data_json, text_llm, provider, model, prompt_version, call_id, cost_usd, cost_estimated, "
            "first_ms, total_ms, status, error, screen_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row.meeting_id, int(row.t_ms), str(row.mode), row.nudge, row.goal, int(row.since_t_ms), int(row.until_caption_id),
             int(row.t_until_ms), int(bool(row.anchored)), int(row.raw_chars), int(row.input_chars), row.thread_summary, row.data_json,
             row.text_llm, row.provider, row.model, row.prompt_version, row.call_id, row.cost_usd, int(bool(row.cost_estimated)),
             row.first_ms, row.total_ms, str(row.status), row.error, row.screen_summary, _now()))

    def insert_options(self, suggestion_id: int, options: Sequence[OptionInsert]) -> list[int]:
        sql = "INSERT INTO suggestion_options(suggestion_id, idx, kind, text, facts_json, evidence, screen) VALUES (?, ?, ?, ?, ?, ?, ?)"
        with self.core.transaction():
            return [self.core.insert(sql, (suggestion_id, int(o.idx), o.kind, o.text, o.facts_json, str(o.evidence), int(o.screen)))
                    for o in options]

    def insert_screen(self, row: ScreenInsert) -> None:
        """F5.21: a tela do pedido (uma por sugestão)."""
        self.core.execute(
            "INSERT INTO suggestion_screens(suggestion_id, status, source, app, width, height, bytes, covered, thumb, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row.suggestion_id, str(row.status), str(row.source), row.app, int(row.width), int(row.height), int(row.sent_bytes),
             int(row.covered), row.thumb, row.data))

    def screens_for_meeting(self, meeting_id: int) -> dict[int, ScreenRow]:
        """F5.21: as telas da reunião com a miniatura (sem a cópia), por sugestão: o chat e a aba."""
        rows = self.core.q("SELECT t.suggestion_id, t.status, t.source, t.app, t.width, t.height, t.bytes, t.covered, t.thumb, s.t_ms "
                           "FROM suggestion_screens t JOIN suggestions s ON s.id = t.suggestion_id WHERE s.meeting_id = ? "
                           "ORDER BY s.id", (meeting_id,))
        return {int(r["suggestion_id"]): cast(ScreenRow, dict(r)) for r in rows}

    def count_copies(self, meeting_id: int) -> int:
        """F5.21: quantas cópias de 960 px a reunião guarda (o `--check`)."""
        rows = self.core.q("SELECT COUNT(*) AS n FROM suggestion_screens t JOIN suggestions s ON s.id = t.suggestion_id "
                           "WHERE s.meeting_id = ? AND t.data IS NOT NULL", (meeting_id,))
        return int(rows[0]["n"]) if rows else 0

    def screen_copy(self, suggestion_id: int) -> bytes | None:
        """F5.21: a cópia de 960 px guardada (ou nada, com `suggest.screen_keep` desligada)."""
        rows = self.core.q("SELECT data FROM suggestion_screens WHERE suggestion_id = ?", (suggestion_id,))
        return bytes(rows[0]["data"]) if rows and rows[0]["data"] is not None else None

    def screen_summaries(self, meeting_id: int, t_from_ms: int, t_to_ms: int, limit: int) -> list[tuple[int, str]]:
        """F5.21 (7b): os resumos das telas anteriores da reunião na janela do pedido, os mais novos primeiro."""
        rows = self.core.q("SELECT t_ms, screen_summary FROM suggestions WHERE meeting_id = ? AND t_ms >= ? AND t_ms < ? "
                           "AND screen_summary IS NOT NULL AND screen_summary != '' ORDER BY t_ms DESC, id DESC LIMIT ?",
                           (meeting_id, int(t_from_ms), int(t_to_ms), int(limit)))
        return [(int(r["t_ms"]), str(r["screen_summary"])) for r in rows]

    def set_rating(self, option_id: int, rating: int | None, reason: str | None = None) -> bool:
        """"Boa" (1), "Ruim" (0, com o motivo ou sem) ou "desfazer" (None). O motivo só vale com o "Ruim"; `rated_at` fica com a
        hora quando há nota ou a opção foi usada, senão nulo. `ValueError` com nota ou motivo fora da lista."""
        if rating not in (None, 0, 1):
            raise ValueError(f"avaliação inválida: {rating!r}")
        if reason is not None and reason not in REASONS:
            raise ValueError(f"motivo inválido: {reason!r}")
        why = reason if rating == 0 else None
        return self.core.execute(
            "UPDATE suggestion_options SET rating = ?, reason = ?, rated_at = CASE WHEN ? IS NOT NULL OR used = 1 THEN ? ELSE NULL END "
            "WHERE id = ?", (rating, why, rating, _now(), option_id)) > 0

    def set_used(self, option_id: int, used: bool) -> bool:
        """"Usei" ligado ou desligado; `rated_at` pela mesma regra da nota."""
        return self.core.execute(
            "UPDATE suggestion_options SET used = ?, rated_at = CASE WHEN rating IS NOT NULL OR ? = 1 THEN ? ELSE NULL END WHERE id = ?",
            (int(bool(used)), int(bool(used)), _now(), option_id)) > 0

    # ---------------------------------------------------------------- ler
    def get(self, suggestion_id: int) -> SuggestionRow | None:
        rows = self.core.q("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,))
        return cast(SuggestionRow, dict(rows[0])) if rows else None

    def get_option(self, option_id: int) -> OptionRow | None:
        rows = self.core.q("SELECT * FROM suggestion_options WHERE id = ?", (option_id,))
        return cast(OptionRow, dict(rows[0])) if rows else None

    def last_ok(self, meeting_id: int) -> SuggestionRow | None:
        """A última sugestão que terminou bem (o fio dela entra no pedido seguinte)."""
        rows = self.core.q("SELECT * FROM suggestions WHERE meeting_id = ? AND status = ? ORDER BY id DESC LIMIT 1",
                           (meeting_id, str(SuggestStatus.OK)))
        return cast(SuggestionRow, dict(rows[0])) if rows else None

    def last_anchored_t_ms(self, meeting_id: int) -> int | None:
        """O instante da última sugestão com a janela reancorada que terminou bem (a falha não deixou fio)."""
        rows = self.core.q("SELECT MAX(t_ms) AS t FROM suggestions WHERE meeting_id = ? AND anchored = 1 AND status = ?",
                           (meeting_id, str(SuggestStatus.OK)))
        t = rows[0]["t"] if rows else None
        return int(t) if t is not None else None

    def list_for_meeting(self, meeting_id: int) -> list[SuggestionRow]:
        return [cast(SuggestionRow, dict(r)) for r in self.core.q("SELECT * FROM suggestions WHERE meeting_id = ? ORDER BY id",
                                                                  (meeting_id,))]

    def options_for(self, suggestion_id: int) -> list[OptionRow]:
        return [cast(OptionRow, dict(r)) for r in self.core.q("SELECT * FROM suggestion_options WHERE suggestion_id = ? ORDER BY idx",
                                                              (suggestion_id,))]

    def options_for_meeting(self, meeting_id: int) -> dict[int, list[OptionRow]]:
        """As opções de todas as sugestões da reunião, por sugestão (uma consulta)."""
        out: dict[int, list[OptionRow]] = {}
        for r in self.core.q("SELECT o.* FROM suggestion_options o JOIN suggestions s ON s.id = o.suggestion_id WHERE s.meeting_id = ? "
                             "ORDER BY o.suggestion_id, o.idx", (meeting_id,)):
            out.setdefault(int(r["suggestion_id"]), []).append(cast(OptionRow, dict(r)))
        return out

    def rated_for_meeting(self, meeting_id: int, limit: int) -> list[OptionRow]:
        """As opções avaliadas desta reunião, as mais recentes primeiro (o que vai ao pedido seguinte)."""
        rows = self.core.q(f"SELECT o.* FROM suggestion_options o JOIN suggestions s ON s.id = o.suggestion_id WHERE s.meeting_id = ? "  # noqa: S608
                           f"AND {RATED} ORDER BY o.rated_at DESC, o.id DESC LIMIT ?", (meeting_id, int(limit)))
        return [cast(OptionRow, dict(r)) for r in rows]

    def count_rated(self) -> int:
        """Quantas opções foram avaliadas, em todas as reuniões (o gatilho do memorando)."""
        return int(self.core.q(f"SELECT COUNT(*) FROM suggestion_options o WHERE {RATED}")[0][0])  # noqa: S608

    def count_rated_since(self, since: str | None) -> int:
        """As avaliações feitas depois de `since` (o `suggest.memo_at` do último memorando; nulo, todas): o gatilho do memorando
        (R-08 da revisão do L1: excluir uma reunião não mexe nesta conta, como mexeria na diferença das contagens)."""
        if not since:
            return self.count_rated()
        return int(self.core.q(f"SELECT COUNT(*) FROM suggestion_options o WHERE {RATED} AND o.rated_at > ?", (since,))[0][0])  # noqa: S608

    def list_rated(self, limit: int) -> list[OptionRow]:
        """As opções avaliadas de todas as reuniões, as mais recentes primeiro (o que vai ao memorando)."""
        rows = self.core.q(f"SELECT o.* FROM suggestion_options o WHERE {RATED} ORDER BY o.rated_at DESC, o.id DESC LIMIT ?",  # noqa: S608
                           (int(limit),))
        return [cast(OptionRow, dict(r)) for r in rows]


__all__ = ["OptionInsert", "OptionRow", "ScreenInsert", "ScreenRow", "SuggestionInsert", "SuggestionRow", "SuggestionsRepo"]
