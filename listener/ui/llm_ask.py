"""F5.6 L1b (U-089, U-090; A-02, A-16, A-17; Comportamento 1 e 2): o pedido de ata, resumo e ações pela tela, sem Qt. O
popup (`ui/llm_request.py`) é só a vista: mostra a `Question` e devolve a escolha (`Mode` ou None); quem pergunta, decide se
pergunta e pede é `ask`, e o único lugar da tela que chama `llm_requests.request` é `submit`.

- `question`: o que o popup diz, lido do banco numa passada: as vozes sem nome ("2 vozes ainda sem nome"; "a identificação
  das vozes ainda não rodou" sem transcrição, com um processamento na fila, que o pedido espera, ou durante a identificação),
  a reunião já revisada (o pedido sai quando a última voz tiver nome ou no "Concluir" de novo, R-03 do L1a), a recomendação,
  a ata que vai junto do resumo (P-07) e o custo ("~ US$ x", pela duração antes da transcrição, A-17; com o núcleo quando o
  encadeamento ficou pendente). `compose` monta o texto dos fatos (`Facts`), sem banco (a sonda e os testes).
- Revisão do L1b: o pedido preso a um processamento por rodar conta a ata como desatualizada (R-03); com o reprocessamento na
  fila, a frase das vozes diz que a identificação roda de novo, e com a identidade no núcleo pendente, que ainda não rodou
  (R-04); sem falas, nem pergunta (R-07); a ata adiada à espera dos nomes que sai de novo depois de um resumo "agora" é dita
  numa linha (o desvio 9 da revisão).
- `ask`: sem chave, nem pergunta (Comportamento 1: quem chama já deixa o botão apagado); a espécie em "falhou" pede de novo
  sem perguntar (Comportamento 2: o custo já foi aceito), salvo a transcrição que mudou (`STALE_TRANSCRIPT`), que pergunta;
  senão o popup decide e o pedido sai com o modo dele. Cancelar no popup não pede nada.
- A última escolha das caixas (ao parar e na troca de idioma, A-19, A-49) em `llm.ask_last`, fora do preset (A-47).
O log leva ids, tarefas e o resultado; nunca título, nome ou fala."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config.defaults import SPECS
from ..llm.costs import fmt_usd
from ..log import get_logger
from ..model import JOB_STEP_LABELS, JobKind, JobStep, MeetingStatus
from ..pipeline import llm_requests, summary_gate
from ..pipeline.llm_request_state import SPECIES_TASKS, ready_job
from ..pipeline.llm_requests import Estimate, Mode, Outcome, RequestState, Species, State, Voices
from ..pipeline.llm_steps import REQUEST_ONLY, STALE_TRANSCRIPT, TASK_ORDER
from ..pipeline.reprocess import active_jobs

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.llm_ask")

LAST_CHOICE = SPECS["llm.ask_last"].key
NO_KEY_TIP = "Sem chave de LLM: informe a chave em Configurações, Chaves de acesso."
NOT_RUN = "A identificação das vozes ainda não rodou."
RERUN = "A identificação das vozes roda de novo depois do reprocessamento."
NO_SPEECH_TIP = "Esta reunião não tem falas: nada para a LLM."
ASK_FAILED = "O pedido de ata e resumo falhou; peça de novo pela reunião."
AGAIN_WITH_NAMES = ("A ata pedida para quando todos tiverem nome ainda sai de novo, com os nomes: gerar agora é uma segunda chamada "
                    "da ata, fora deste custo.")
ALL_NAMED = "Todas as vozes têm nome."
ADVICE = "Recomendado: gerar quando todos tiverem nome, para a ata sair com os nomes de cada um (e não \"Voz 2\")."
REVIEWED = ("Esta reunião já foi revisada: o pedido sai quando a última voz tiver nome ou quando você clicar em \"Concluir\" na "
            "revisão de novo.")
WITH_MINUTES = "A ata e as ações vão junto: o resumo sai da ata, que falta ou está desatualizada."
CORE_TEXT = {JobStep.IDENTIFY: "a identificação das vozes", JobStep.TITLE: "o título", JobStep.REVISE: "a transcrição revisada"}
TITLES = {frozenset({Species.MINUTES}): "Gerar ata e ações", frozenset({Species.SUMMARY}): "Gerar resumo",
          frozenset(Species): "Gerar ata, ações e resumo"}
OUTCOME_TEXT = {Outcome.QUEUED: "gerando pela fila (3 a 5 s para começar)…", Outcome.WAITING: "sai quando todas as vozes tiverem nome.",
                Outcome.BOUND: "sai quando o processamento da reunião terminar.", Outcome.NEXT: "sai quando a reunião for processada.",
                Outcome.NO_KEY: "pedido guardado; sai quando houver uma chave de LLM (Configurações, Chaves de acesso)."}
STEP_SPECIES = {str(step): sp for sp, steps in SPECIES_TASKS.items() for step in steps}


@dataclass(frozen=True, slots=True)
class Facts:
    """O que o popup precisa saber, lido do banco (`facts`) ou montado à mão (a sonda e os testes)."""
    tasks: tuple[str, ...]              # o que foi pedido
    voices: Voices
    waiting: bool                       # um processamento na fila: o pedido espera ele (a identificação ainda não rodou)
    reviewed: bool                      # a revisão já foi concluída (`revisada`)
    cost: Estimate
    rerun: bool = False                 # o processamento na fila é um reprocessamento: a identificação roda de novo (R-04)
    deferred_minutes: bool = False      # a ata espera os nomes num pedido adiado: sai de novo depois (o desvio 9)


@dataclass(frozen=True, slots=True)
class Question:
    title: str
    lines: tuple[str, ...]              # as frases do popup, em ordem
    named: bool                         # todas as vozes com nome e a identificação feita: "Gerar" e "Cancelar"
    tasks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Sent:
    job_id: int | None                  # o job `llm` que gera (None: adiado, preso, guardado ou recusado)
    message: str                        # o que a tela mostra
    outcome: Outcome = Outcome.REFUSED


type Choose = Callable[[Question], Mode | None]


# ---------------------------------------------------------------- textos
def step_label(step: JobStep | str) -> str:
    return JOB_STEP_LABELS.get(str(step), str(step)).removeprefix("LLM: ")


def species_of(tasks: Iterable[JobStep | str]) -> frozenset[Species]:
    return frozenset(STEP_SPECIES[str(t)] for t in tasks if str(t) in STEP_SPECIES)


def tasks_of(species: Iterable[Species]) -> list[JobStep]:
    """As tarefas das espécies, na ordem da fila ("Ata e ações": a ata; "Resumo": executivo e pré-resumo)."""
    chosen = {t for sp in species for t in SPECIES_TASKS[sp]}
    return [t for t in TASK_ORDER if t in chosen]


def title_for(tasks: Iterable[JobStep | str]) -> str:
    return TITLES.get(species_of(tasks), "Gerar ata e ações")


def identified(f: Facts) -> bool:
    """A identificação desta transcrição já rodou: há transcrição, nada a refaz na fila, a identidade não está rodando nem
    pendente no núcleo que o pedido encadeia (R-04)."""
    return f.voices.ready and not f.waiting and not f.voices.identifying and str(JobStep.IDENTIFY) not in f.cost.core


def voices_line(f: Facts) -> str:
    if f.rerun:
        return RERUN
    if not identified(f):
        return NOT_RUN
    if not f.voices.pending:
        return ALL_NAMED
    return f"{f.voices.pending} {'voz' if f.voices.pending == 1 else 'vozes'} ainda sem nome."


def cost_line(e: Estimate) -> str:
    """"Custo: ~ US$ 0,04 (estimado pela duração)." (A-17); com o núcleo pendente, o que ele inclui."""
    value = f"~ {fmt_usd(e.usd)}" if e.usd is not None else fmt_usd(None)
    how = "estimado pela duração" if e.by_duration else "estimado pela transcrição"
    core = [CORE_TEXT[JobStep(c)] for c in e.core if JobStep(c) in CORE_TEXT]
    if core:
        joined = core[0] if len(core) == 1 else ", ".join(core[:-1]) + " e " + core[-1]
        how += f"; inclui {joined}, que ficaram pendentes"
    return f"Custo: {value} ({how})."


def compose(f: Facts) -> Question:
    """O texto do popup (U-090): as vozes, a revisão já concluída, a recomendação, a ata junto (e a adiada que sai de novo)
    e o custo."""
    fresh = identified(f)
    named = fresh and not f.voices.pending
    lines = [voices_line(f)]
    if fresh and f.voices.pending and f.reviewed:
        lines.append(REVIEWED)
    if not named:
        lines.append(ADVICE)
    if Species.SUMMARY in species_of(f.tasks) and str(JobStep.MINUTES) in f.cost.tasks and str(JobStep.MINUTES) not in f.tasks:
        lines.append(WITH_MINUTES)
        if f.deferred_minutes:
            lines.append(AGAIN_WITH_NAMES)
    lines.append(cost_line(f.cost))
    return Question(title_for(f.tasks), tuple(lines), named, f.tasks)


def outcome_text(outcome: Outcome, tasks: Iterable[JobStep | str], refused: str = "") -> str:
    if outcome == Outcome.REFUSED:
        return refused
    what = " e ".join(step_label(t) for t in tasks).capitalize()
    return f"{what}: {OUTCOME_TEXT[outcome]}"


# ---------------------------------------------------------------- banco
def has_key(store: Store) -> bool:
    """Comportamento 1: sem provedor de LLM ativo com chave, caixas e botões apagados."""
    return store.providers.active_with_key("llm") is not None


def speechless(store: Store, meeting_id: int) -> bool:
    """R-07: a transcrição pronta, sem processamento na fila, não tem fala com texto (o mesmo teste do `request`)."""
    ready = ready_job(store, meeting_id)
    return (ready is not None and not active_jobs(store, meeting_id)
            and not store.segments.count_segments(meeting_id, ready, with_text=True))


def refusal(store: Store, meeting_id: int) -> str:
    """Por que nada pode ser pedido agora (a dica dos botões apagados; vazio = pode): sem chave (Comportamento 1) ou sem falas."""
    if not has_key(store):
        return NO_KEY_TIP
    return NO_SPEECH_TIP if speechless(store, meeting_id) else ""


def facts(store: Store, meeting_id: int, tasks: Iterable[JobStep | str], after_job_id: int | None = None) -> Facts:
    """Os fatos do popup. Com processamento ativo ou o pedido preso a um (`after_job_id`: o parar e a troca de idioma), a
    transcrição será outra: a ata conta como desatualizada (R-03) e a identificação ainda vai rodar."""
    asked = tuple(str(t) for t in TASK_ORDER if t in REQUEST_ONLY and str(t) in {str(x) for x in tasks})
    reviewed = str((store.get_meeting(meeting_id) or {}).get("status")) == MeetingStatus.REVIEWED
    heavy = active_jobs(store, meeting_id)
    waiting = bool(heavy) or after_job_id is not None
    rerun = any(str(j.get("kind")) == JobKind.REPROCESS for j in heavy)
    later = summary_gate.deferred(store, meeting_id)
    return Facts(asked, llm_requests.voices(store, meeting_id), waiting, reviewed,
                 llm_requests.estimate(store, meeting_id, asked, new_transcript=waiting), rerun,
                 later is not None and str(JobStep.MINUTES) in later.tasks)


def question(store: Store, meeting_id: int, tasks: Iterable[JobStep | str], after_job_id: int | None = None) -> Question:
    return compose(facts(store, meeting_id, tasks, after_job_id))


def retries(states: Mapping[Species, RequestState], tasks: Iterable[JobStep | str]) -> bool:
    """Comportamento 2: as espécies pedidas em "falhou" pedem de novo sem o popup (o custo já foi aceito), salvo a transcrição
    que mudou (`STALE_TRANSCRIPT`), que pergunta de novo. "Falhou" só vale sobre a transcrição vigente (R-01: `request_states`);
    `ask` exige ainda que nada a refaça na fila."""
    species = species_of(tasks)
    return bool(species) and all(states[sp].state == State.FAILED and STALE_TRANSCRIPT not in states[sp].error for sp in species)


def submit(store: Store, meeting_id: int, tasks: Iterable[JobStep | str], mode: Mode, origin: str,
           after_job_id: int | None = None) -> Sent:
    """O pedido (`llm_requests.request`, A-11) e a mensagem do resultado (`RequestResult.outcome`)."""
    wanted = [str(t) for t in tasks]
    res = llm_requests.request(store, meeting_id, wanted, mode, origin, after_job_id=after_job_id)
    log.info("reunião %s: pedido %s pela tela (%s; modo %s; %s): %s", meeting_id, res.request_id, ", ".join(wanted), mode, origin,
             res.outcome)
    return Sent(res.job_id, outcome_text(res.outcome, wanted, res.refused), res.outcome)


def ask(store: Store, meeting_id: int, tasks: Iterable[JobStep | str], origin: str, choose: Choose, *,
        after_job_id: int | None = None, retry: bool = True) -> Sent | None:
    """O pedido pela tela (U-090): sem chave ou sem falas, a recusa sem popup (R-07); "falhou" pede de novo sem popup
    (`retry`: só nos botões da reunião, sem processamento na fila; o pedido preso a um processamento novo sempre pergunta);
    senão o popup decide. None = cancelado no popup."""
    wanted = [str(t) for t in tasks]
    refused = refusal(store, meeting_id)
    if refused:
        return Sent(None, refused)
    if (retry and after_job_id is None and retries(llm_requests.request_states(store, meeting_id), wanted)
            and not active_jobs(store, meeting_id)):
        return submit(store, meeting_id, wanted, Mode.NOW, f"{origin} (de novo, depois da falha)", after_job_id)
    mode = choose(question(store, meeting_id, wanted, after_job_id))
    if mode is None:
        log.info("reunião %s: pedido de %s cancelado no popup (%s)", meeting_id, ", ".join(wanted), origin)
        return None
    return submit(store, meeting_id, wanted, mode, origin, after_job_id)


# ---------------------------------------------------------------- a última escolha das caixas (A-19, A-49)
def last_choice(store: Store) -> frozenset[Species]:
    raw = store.get_setting(LAST_CHOICE)
    return frozenset(Species(v) for v in raw.split(",") if v in {str(sp) for sp in Species})


def save_choice(store: Store, species: Iterable[Species]) -> None:
    store.set_setting(LAST_CHOICE, ",".join(str(sp) for sp in Species if sp in set(species)))


__all__ = ["ADVICE", "AGAIN_WITH_NAMES", "ALL_NAMED", "ASK_FAILED", "NOT_RUN", "NO_KEY_TIP", "NO_SPEECH_TIP", "RERUN", "REVIEWED",
           "Choose", "Facts", "Question", "Sent", "ask", "compose", "cost_line", "facts", "has_key", "identified", "last_choice",
           "outcome_text", "question", "refusal", "retries", "save_choice", "species_of", "speechless", "step_label", "submit",
           "tasks_of", "title_for", "voices_line"]
