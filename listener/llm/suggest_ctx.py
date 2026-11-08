"""F5.20 (C8), dividido da `llm/suggest_task.py` no L0 da F5.21 (A-27: o módulo passava de 400 linhas), sem mudar nada: os
tipos da sugestão ao vivo (o desfecho, a opção, o resultado, a janela), os textos e os limites, e a montagem do pedido.

- A janela (`window`): na primeira sugestão e a cada 20 min de reunião, os últimos 15 min de legendas (reancorada); nas
  outras, desde o fim da anterior, com ao menos 3 min e no máximo `suggest.window_min`.
- O pedido (`suggest_ctx`): quem pede (a ficha "Você"), o cabeçalho com os marcadores e os falantes da janela, o dossiê, o
  memorando, o objetivo, o parcial atual, o fio da sugestão anterior, as avaliações desta reunião e "Como sugerir agora".
- `estimate_usd` (o custo pelos caracteres, sem chamar o provedor) e `bounded` (os tetos do esquema cortados antes da
  validação, R-02 da revisão do L1 da F5.20).
Nenhum Qt aqui."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..config.defaults import SPECS
from ..model import Evidence, SuggestMode
from . import partial_task
from .costs import CHARS_PER_TOKEN, fmt_usd, price_for
from .jobconfig import LlmJobConfig
from .prompts.common import Marker, Person
from .prompts.suggest import MAX_FACTS, MAX_OPTIONS, SUMMARY_LIMIT, TEXT_LIMIT, RatedOption, SuggestCtx
from .suggest_compact import PARTICIPANT, Compacted

if TYPE_CHECKING:
    from ..store import Store
    from ..store.people import PersonRow
    from ..store.suggestions import OptionRow

TIMEOUT_S = 30.0
POLL_S = 0.2
MIN_WINDOW_MS = 3 * 60_000
ANCHOR_WINDOW_MS = 15 * 60_000
REANCHOR_EVERY_MS = 20 * 60_000
MAX_INPUT_CHARS = 8_000
MAX_ANCHOR_CHARS = 12_000
RATINGS_IN_PROMPT = 10
ORIENT_OPTIONS = 3
ME_FALLBACK = "Usuário"
NO_PROVIDER = "sem provedor de IA com chave: as sugestões ficam de fora (Configurações, Chaves de acesso)"
BUDGET_TEXT = "sugestões paradas nesta reunião: o custo da IA chegou a {spent}, metade do alerta de {alert}; o resto fica para a ata"
NO_OPTIONS = "a IA não devolveu nenhuma opção válida"
TIMED_OUT = f"prazo de {TIMEOUT_S:.0f} s esgotado"
CANCELLED = "cancelado"

type OnOption = Callable[[SuggestOption], None]


class Outcome(StrEnum):
    DONE = "done"                  # sugestão gravada com opções (no memorando: refeito, ou nada a refazer)
    NO_PROVIDER = "no_provider"    # sem provedor ativo com chave
    BUDGET = "budget"              # o custo da reunião já na metade do alerta (o teto do parcial)
    ERROR = "error"                # rede, provedor, prazo, resposta fora do esquema ou sem opção
    CANCELLED = "cancelled"
    GONE = "gone"                  # a reunião não existe mais (Cancelar)


@dataclass(frozen=True, slots=True)
class SuggestOption:
    idx: int
    kind: str
    text: str
    facts: tuple[tuple[int, int], ...]     # (caption_id, t_ms) das legendas citadas que estão no pedido
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class SuggestResult:
    outcome: Outcome
    meeting_id: int
    suggestion_id: int | None = None
    call_id: int | None = None
    options: tuple[SuggestOption, ...] = ()
    option_ids: tuple[int, ...] = ()       # os ids de `suggestion_options`, na ordem de `options` (o cartão avalia por eles)
    thread_summary: str | None = None
    cost_usd: float | None = None
    cost_estimated: bool = False
    first_ms: int | None = None
    total_ms: int | None = None
    raw_chars: int = 0
    input_chars: int = 0
    until_caption_id: int = 0
    t_until_ms: int = 0
    spent_usd: float = 0.0
    limit_usd: float = 0.0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Window:
    t_from_ms: int
    anchored: bool
    max_chars: int


# ---------------------------------------------------------------- o pedido
def budget_text(spent: float, limit: float) -> str:
    return BUDGET_TEXT.format(spent=fmt_usd(spent), alert=fmt_usd(limit / partial_task.PARTIAL_BUDGET_SHARE))


def window(store: Store, meeting_id: int, t_ms: int, window_min: int) -> Window:
    """A janela de legendas do pedido feito em `t_ms`: reancorada (15 min, teto maior) na primeira e a cada 20 min; senão desde o
    fim da sugestão anterior, com ao menos 3 min e no máximo `window_min`."""
    prev = store.suggestions.last_ok(meeting_id)
    anchor_t = store.suggestions.last_anchored_t_ms(meeting_id)
    if prev is None or anchor_t is None or t_ms - anchor_t >= REANCHOR_EVERY_MS:
        return Window(max(0, t_ms - ANCHOR_WINDOW_MS), True, MAX_ANCHOR_CHARS)
    since = min(int(prev.get("t_until_ms") or 0), t_ms - MIN_WINDOW_MS)
    return Window(max(0, t_ms - window_min * 60_000, since), False, MAX_INPUT_CHARS)


def me_label(me: PersonRow | None) -> str:
    """O rótulo de quem pede nas legendas sem nome do canal L ("Nome — Cargo")."""
    if me is None:
        return ME_FALLBACK
    role = str(me.get("role") or "").strip()
    return f"{me['name']} — {role}" if role else str(me["name"])


def _asker(me: PersonRow | None) -> Person:
    if me is None:
        return Person(0, ME_FALLBACK)
    return Person(int(me["id"]), str(me["name"]), str(me.get("role") or ""), str(me.get("company") or ""))


def _speakers(store: Store, rows: list[Mapping[str, Any]], in_room: bool = False) -> tuple[Person, ...]:
    """Os falantes do R na janela (e os do L na sala presencial, R-06), com o cargo do rótulo e a empresa da ficha (o e-mail
    nunca)."""
    out: dict[str, Person] = {}
    for row in rows:
        label = str(row.get("label") or "").strip()
        if (row.get("channel") != "R" and not in_room) or not label or label == PARTICIPANT or label in out:
            continue
        name, _sep, role = label.partition(" — ")
        person = store.people.find_by_name(name.strip())
        out[label] = Person(int(person["id"]) if person else 0, name.strip(), role.strip(), str((person or {}).get("company") or ""))
    return tuple(out.values())


def rated(rows: list[OptionRow]) -> tuple[RatedOption, ...]:
    return tuple(RatedOption(str(r["kind"]), str(r["text"]), r.get("rating"), r.get("reason"), bool(r.get("used"))) for r in rows)


def suggest_ctx(store: Store, meeting: Mapping[str, Any], me: PersonRow | None, mode: SuggestMode, options_n: int, dossier: str | None,
                nudge: str | None, c: Compacted, rows: list[Mapping[str, Any]]) -> SuggestCtx:
    """O contexto do pedido (C4): o cabeçalho com os marcadores e os falantes da janela, quem pede, os blocos opcionais."""
    mid = int(meeting["id"])
    markers = tuple(Marker(int(m["id"]), int(m["t_ms"]), str(m.get("note") or "")) for m in store.list_markers(mid))
    head = replace(partial_task.meeting_ctx(store, meeting), markers=markers,
                   speakers=_speakers(store, rows, bool(meeting.get("is_in_room"))))
    previous, _last = partial_task.partial_state(store, mid)
    prev = store.suggestions.last_ok(mid)
    memo = str(store.get(SPECS["suggest.memo"])).strip() or None
    goal = str(meeting.get("goal") or "").strip() or None
    return SuggestCtx(head, _asker(me), mode, ORIENT_OPTIONS if mode == SuggestMode.ORIENT else options_n, goal, dossier or None, memo,
                      partial_task.prompt_json(previous), (prev or {}).get("thread_summary") or None, c.lines,
                      rated(store.suggestions.rated_for_meeting(mid, RATINGS_IN_PROMPT)), (nudge or "").strip() or None)


def estimate_usd(store: Store, cfg: LlmJobConfig, chars_in: int, chars_out: int) -> float | None:
    """O custo pelos caracteres (÷ 3,5 por token, o contrato), sem chamar o provedor: a dica do botão e a sugestão cancelada."""
    price = price_for(store.providers.list_prices(cfg.provider_kind or None), cfg.provider_kind, cfg.fast_model)
    if price is None:
        return None
    return round((chars_in / CHARS_PER_TOKEN * price.input_usd_m + chars_out / CHARS_PER_TOKEN * price.output_usd_m) / 1e6, 6)


def bounded(obj: Any) -> Any:
    """R-02: a OpenAI e a Anthropic não recebem os tetos do esquema (`maxItems`, `maxLength`): as listas e os textos acima
    deles são cortados antes da validação, como a margem do texto e do fio, em vez de reprovar a sugestão inteira."""
    if not isinstance(obj, dict):
        return obj
    out = dict(obj)
    if isinstance(out.get("facts"), list):
        out["facts"] = out["facts"][:MAX_FACTS]
    if isinstance(out.get("text"), str):
        out["text"] = out["text"][:TEXT_LIMIT]
    if isinstance(out.get("options"), list):
        out["options"] = [bounded(o) for o in out["options"][:MAX_OPTIONS]]
    if isinstance(out.get("thread_summary"), str):
        out["thread_summary"] = out["thread_summary"][:SUMMARY_LIMIT]
    return out


__all__ = ["ANCHOR_WINDOW_MS", "CANCELLED", "MAX_ANCHOR_CHARS", "MAX_INPUT_CHARS", "MIN_WINDOW_MS", "NO_OPTIONS", "NO_PROVIDER", "POLL_S",
           "REANCHOR_EVERY_MS", "TIMED_OUT", "TIMEOUT_S", "OnOption", "Outcome", "SuggestOption", "SuggestResult", "Window", "bounded",
           "budget_text", "estimate_usd", "me_label", "rated", "suggest_ctx", "window"]
