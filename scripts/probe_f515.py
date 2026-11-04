"""F5.15 L0 — a medida das referências de voz (spec specs/features/F5.15-referencias-de-voz.md, itens 1 e 2). Não faz
parte do produto.

  python scripts/probe_f515.py --db DIR [--model M] [--threshold T] [--margin G]

Só leitura (`Store.open_readonly`; sem Qt nem torch), no banco real. O modelo padrão é o de mais amostras ativas; o limiar
e a margem padrão, os das Configurações do banco. Vozes-sonda: em cada reunião, os clusters do job vigente com centroide
do modelo, fora o L do usuário; a verdade é a pessoa resolvida (`resolve_person`) dos `confirmado` e dos `auto` de reunião
`revisada`; pendentes, ignoradas e `auto` de reunião não revisada ficam fora (contadas no inventário). Deixa uma reunião
inteira de fora por vez (A-05): as referências saem das amostras ativas de fora dela (as sem reunião e as de reunião
excluída sempre entram) e o próprio `ProfileMatcher` casa as vozes-sonda dela, por canal como o job, com os esperados
dela. A voz cuja pessoa não tem amostra fora da reunião conta em `sem_perfil_fora`, fora das taxas.

Mede corte (0,70 a 0,85 e 1,0) x teto (1, 3, 5 e sem teto) x variante do limiar (A, P e C; o da pessoa por `calibrate`
nas amostras de fora) e a linha de base: a média única com o limiar gravado hoje (`voice_profiles.threshold`, variante
A). Uma linha JSON por configuração, com o mesmo por plataforma de 5 vozes-sonda ou mais; antes, o inventário; no fim, a
escolha pela regra do item 2, com as referências e o limiar de cada pessoa na escolhida (ou nos padrões).
`certas_entre_060_e_limiar`: vozes avaliáveis cuja nota da pessoa certa fica entre 0,60 e o limiar dela. Referências e
limiares por pessoa contam todas as amostras (o estado que o L1 teria hoje). Pessoas como #k, pela ordem do perfil:
nunca nome, id nem vetor; o id da reunião também não sai (D-053)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                    # o pacote deste checkout, não o instalado (o .pth do venv aponta para outro)

from listener.config.defaults import SPECS  # noqa: E402
from listener.model import ClusterStatus, MeetingStatus  # noqa: E402
from listener.pipeline.profiles import ProfileMatcher, ProfileRef, expected_names, expected_person_ids, person_scores  # noqa: E402
from listener.pipeline.runner import USER_CLUSTER  # noqa: E402
from listener.pipeline.types import ClusterData, MatchResult  # noqa: E402
from listener.store import Store  # noqa: E402
from listener.store.profiles import (CENTROID_SOURCES, MAX_REFS, REF_COS, THRESHOLD_HIGH, THRESHOLD_LOW,  # noqa: E402
                                     THRESHOLD_VARIANT, THRESHOLD_VARIANTS, ProfileSamples, calibrate, group_samples, normalize,
                                     references, threshold_for)
from listener.store.segments import resolve_person  # noqa: E402

CUTS = (0.70, 0.75, 0.80, 0.85, 1.0)
CAPS: tuple[int | None, ...] = (1, 3, 5, None)
FROM_SCORE = 0.60                 # "certas entre 0,60 e o limiar"
MIN_PLATFORM_VOICES = 5
MIN_PEOPLE = 2                    # pessoas com amostras em 2 reuniões ou mais para a escolha valer (item 2)
RULE_PP = 1.0                     # auto errado até 1 ponto percentual acima da linha de base
NO_PLATFORM = "sem_plataforma"
DEFAULTS = {"ref_cos": REF_COS, "max_refs": MAX_REFS, "variante": THRESHOLD_VARIANT}
type Config = tuple[float, int | None] | None          # (corte, teto); None = a linha de base
type Score = tuple[float, float, float | None]         # (nota da pessoa certa, o limiar dela, a melhor errada)


@dataclass(frozen=True, slots=True)
class Voice:
    """Uma voz-sonda: o cluster (o rótulo é o id, que nunca sai), o canal, a pessoa certa e a plataforma da reunião."""
    data: ClusterData
    channel: str
    truth: int
    platform: str


@dataclass(frozen=True, slots=True)
class Meeting:
    key: int                                   # o id, só para separar as amostras (nunca sai)
    expected: tuple[str, ...]
    expected_ids: frozenset[int]
    voices: tuple[Voice, ...]


@dataclass(slots=True)
class Tally:
    """As contas de uma configuração, no todo ou numa plataforma."""
    n_avaliavel: int = 0
    sem_perfil_fora: int = 0
    auto_certo: int = 0
    auto_errado: int = 0
    pendente_certa: int = 0
    pendente_errada: int = 0
    certas_entre: int = 0
    right: list[float] = field(default_factory=list)
    wrong: list[float] = field(default_factory=list)

    def add(self, truth: int, r: MatchResult, score: Score | None) -> None:
        if score is None:                                       # a pessoa certa sem amostra fora da reunião
            self.sem_perfil_fora += 1
            return
        s_true, t_true, best_wrong = score
        right = int(r.person_id == truth)
        self.n_avaliavel += 1
        if r.status == ClusterStatus.AUTO:
            self.auto_certo += right
            self.auto_errado += 1 - right
        else:
            self.pendente_certa += right
            self.pendente_errada += 1 - right
        self.right.append(s_true)
        if best_wrong is not None:
            self.wrong.append(best_wrong)
        self.certas_entre += int(FROM_SCORE <= s_true < t_true)

    def as_dict(self) -> dict[str, Any]:
        n = self.n_avaliavel
        counts = {"auto_certo": self.auto_certo, "auto_errado": self.auto_errado, "pendente_certa": self.pendente_certa,
                  "pendente_errada": self.pendente_errada}
        return {"n_avaliavel": n, "sem_perfil_fora": self.sem_perfil_fora, **counts,
                **{f"{k}_pct": round(100 * v / n, 2) if n else None for k, v in counts.items()},
                "certas_entre_060_e_limiar": self.certas_entre, "nota_certa": _pcts(self.right), "melhor_errada": _pcts(self.wrong)}


def _pcts(values: Sequence[float]) -> dict[str, float] | None:
    if not values:
        return None
    p10, p50, p90 = np.percentile(np.asarray(values, np.float64), [10, 50, 90])
    return {"p10": round(float(p10), 3), "p50": round(float(p50), 3), "p90": round(float(p90), 3)}


# ---------------------------------------------------------------------------- vozes-sonda
def _left_out(c: Mapping[str, Any], reviewed: bool, dim: int) -> str | None:
    """Por que a voz fica fora da sonda; None quando ela tem verdade."""
    status = str(c["status"])
    if c["centroid"].size != dim:
        return "outra_dimensao"
    if status == ClusterStatus.CONFIRMED or (status == ClusterStatus.AUTO and reviewed):
        return None
    if status == ClusterStatus.AUTO:
        return "auto_nao_revisada"
    return "ignoradas" if status == ClusterStatus.IGNORED else "pendentes"


def _voices(store: Store, m: Mapping[str, Any], model: str, dim: int, left_out: Counter[str]) -> list[Voice]:
    job = store.segments.current_job_id(int(m["id"]))
    clusters = store.segments.list_clusters(int(m["id"]), job) if job is not None else []
    by_id = {int(c["id"]): c for c in clusters}
    reviewed, platform = str(m.get("status")) == MeetingStatus.REVIEWED, str(m.get("platform") or NO_PLATFORM)
    out: list[Voice] = []
    for c in clusters:
        if (c["channel"] == "L" and c["cluster"] == USER_CLUSTER) or c.get("model") != model or not c["centroid"].size:
            continue
        reason = _left_out(c, reviewed, dim)
        truth = resolve_person(c, by_id) if reason is None else None
        if truth is None:
            left_out[reason or "sem_pessoa"] += 1
            continue
        out.append(Voice(ClusterData(str(c["id"]), int(c["speech_ms"]), c["centroid"]), str(c["channel"]), truth, platform))
    return out


def load_meetings(store: Store, model: str, dim: int, left_out: Counter[str]) -> list[Meeting]:
    """As reuniões com ao menos uma voz-sonda, em ordem de id."""
    meetings: list[Meeting] = []
    for m in sorted(store.list_meetings(), key=lambda row: int(row["id"])):
        voices = _voices(store, m, model, dim, left_out)
        if voices:
            meetings.append(Meeting(int(m["id"]), expected_names(m.get("expected_participants")),
                                    expected_person_ids(m.get("expected_people_json")), tuple(voices)))
    return meetings


# ---------------------------------------------------------------------------- referências sem a reunião
class RefCache:
    """As referências de cada pessoa sem as amostras da reunião deixada de fora (a pessoa sem amostra nela reaproveita as
    de todas). Configuração None: a linha de base (a média única e o limiar gravado em `voice_profiles.threshold`)."""

    def __init__(self, samples: Sequence[ProfileSamples], names: dict[int, str], stored: dict[int, float | None]):
        self.samples, self.names, self.stored = samples, names, stored
        self.meetings = [frozenset(m for m in ps.meetings if m is not None) for ps in samples]
        self.memo: dict[tuple[int, int | None, Config], ProfileRef | None] = {}

    def refs(self, held: int, config: Config) -> list[ProfileRef]:
        out: list[ProfileRef] = []
        for k, ps in enumerate(self.samples):
            key = (k, held if held in self.meetings[k] else None, config)
            if key not in self.memo:
                self.memo[key] = self._build(ps, key[1], config)
            if (ref := self.memo[key]) is not None:
                out.append(ref)
        return out

    def _build(self, ps: ProfileSamples, held: int | None, config: Config) -> ProfileRef | None:
        rest = ps.vectors if held is None else ps.vectors[np.array([m != held for m in ps.meetings], dtype=bool)]
        if not len(rest):
            return None
        name = self.names.get(ps.person_id, "")                  # só para o desempate pelos esperados; nunca sai
        if config is None:
            refs, sizes = references(rest, np.zeros(len(rest), np.intp))
            return ProfileRef(ps.profile_id, ps.person_id, refs, self.stored.get(ps.profile_id), name, sizes)
        labels = group_samples(rest, *config)
        refs, sizes = references(rest, labels)
        return ProfileRef(ps.profile_id, ps.person_id, refs, calibrate(rest, labels).threshold, name, sizes)


# ---------------------------------------------------------------------------- casamento e contas
def _scores(voices: Sequence[Voice], profiles: Sequence[ProfileRef], variant: str, threshold: float) -> list[Score | None]:
    """Por voz: a nota da pessoa certa, o limiar dela e a melhor nota errada; None sem perfil dela fora da reunião."""
    dim = voices[0].data.centroid.size if voices[0].data.centroid is not None else 0
    prof = [p for p in profiles if p.refs.shape[1] == dim and np.any(p.refs)]       # o filtro do `ProfileMatcher`
    cols = {p.person_id: j for j, p in enumerate(prof)}
    if not prof:
        return [None] * len(voices)
    s, win = person_scores(np.stack([normalize(np.asarray(v.data.centroid)) for v in voices]), prof)
    out: list[Score | None] = []
    for r, v in enumerate(voices):
        j = cols.get(v.truth)
        if j is None:
            out.append(None)
            continue
        p, others = prof[j], np.delete(s[r], j)
        t = threshold_for(variant, p.threshold, p.sizes[int(win[r, j])] if p.sizes else None, threshold)
        out.append((float(s[r, j]), t, float(others.max()) if others.size else None))
    return out


def run_config(meetings: Sequence[Meeting], cache: RefCache, config: Config, variant: str, threshold: float,
               margin: float) -> dict[str, Tally]:
    """As contas de uma configuração: "*" é o todo; as outras chaves, as plataformas."""
    totals: dict[str, Tally] = {"*": Tally()}
    for mt in meetings:
        profiles = cache.refs(mt.key, config)
        matcher = ProfileMatcher(threshold, margin, expected=mt.expected, expected_ids=mt.expected_ids, variant=variant)
        for channel in sorted({v.channel for v in mt.voices}):
            voices = [v for v in mt.voices if v.channel == channel]
            results = matcher.match([v.data for v in voices], profiles)
            for v, r, score in zip(voices, results, _scores(voices, profiles, variant, threshold), strict=True):
                for key in ("*", v.platform):
                    totals.setdefault(key, Tally()).add(v.truth, r, score)
    return totals


def person_view(samples: Sequence[ProfileSamples], config: Config,
                stored: dict[int, float | None]) -> list[tuple[int, ProfileSamples, tuple[int, ...], float | None]]:
    """(k, amostras, tamanho de cada referência, limiar) de cada pessoa com todas as amostras."""
    out: list[tuple[int, ProfileSamples, tuple[int, ...], float | None]] = []
    for k, ps in enumerate(samples, 1):
        if not len(ps.vectors):
            continue
        if config is None:
            out.append((k, ps, (len(ps.vectors),), stored.get(ps.profile_id)))
            continue
        labels = group_samples(ps.vectors, *config)
        out.append((k, ps, references(ps.vectors, labels)[1], calibrate(ps.vectors, labels).threshold))
    return out


def summary(view: Sequence[tuple[int, ProfileSamples, tuple[int, ...], float | None]]) -> dict[str, Any]:
    ks = [len(sizes) for _, _, sizes, _ in view]
    th = [t for _, _, _, t in view]
    return {"referencias": {"media": round(float(np.mean(ks)), 2) if ks else None, "maximo": max(ks, default=0)},
            "limiares": {"nulos": sum(t is None for t in th),
                         "em_075": sum(t is not None and abs(t - THRESHOLD_HIGH) < 1e-9 for t in th),
                         "em_045": sum(t is not None and abs(t - THRESHOLD_LOW) < 1e-9 for t in th)}}


def config_line(stage: str, config: Config, variant: str, totals: dict[str, Tally], view_summary: dict[str, Any],
                platform_voices: Counter[str]) -> dict[str, Any]:
    cut, cap = config if config is not None else (None, 1)
    platforms = {k: t.as_dict() for k, t in sorted(totals.items())
                 if k != "*" and platform_voices[k] >= MIN_PLATFORM_VOICES}
    return {"stage": stage, "corte": cut, "teto": cap, "variante": variant, **totals["*"].as_dict(), **view_summary,
            "por_plataforma": platforms}


# ---------------------------------------------------------------------------- inventário e escolha
def people_in_two_meetings(samples: Sequence[ProfileSamples]) -> int:
    return sum(len({m for m in ps.meetings if m is not None}) >= 2 for ps in samples)


def default_model(store: Store) -> str | None:
    rows = store.core.q("SELECT p.model, COUNT(*) AS n FROM voice_profiles p JOIN voice_embeddings e ON e.profile_id = p.id "
                        "WHERE e.source IN (?, ?) GROUP BY p.model ORDER BY n DESC, p.model LIMIT 1", CENTROID_SOURCES)
    return str(rows[0]["model"]) if rows else None


def inventory(store: Store, model: str, samples: Sequence[ProfileSamples], meetings: Sequence[Meeting],
              left_out: Counter[str], load_ms: dict[str, float]) -> dict[str, Any]:
    sources = {str(r[0]): int(r[1]) for r in store.core.q(
        "SELECT e.source, COUNT(*) FROM voice_embeddings e JOIN voice_profiles p ON p.id = e.profile_id WHERE p.model = ? "
        "GROUP BY e.source ORDER BY e.source", (model,))}
    platforms = Counter(p or NO_PLATFORM for ps in samples for p in ps.platforms)
    return {"stage": "inventario", "modelo": model, "pessoas": len(samples), "amostras_ativas": sum(len(ps.ids) for ps in samples),
            "pessoas_em_2_reunioes": people_in_two_meetings(samples), "amostras_por_fonte": sources,
            "amostras_por_plataforma": dict(sorted(platforms.items())), "dimensoes_ignoradas": sum(ps.skipped for ps in samples),
            "reunioes_com_vozes": len(meetings), "vozes_sonda": sum(len(m.voices) for m in meetings),
            "vozes_fora": dict(sorted(left_out.items())), "carga_ms": load_ms}


def _cap_rank(cap: int | None) -> float:
    return float("inf") if cap is None else float(cap)


def choose(grid: Sequence[dict[str, Any]], base: dict[str, Any]) -> dict[str, Any] | None:
    """Item 2: o maior auto certo com auto errado até 1 ponto percentual acima da linha de base; no empate, o corte maior,
    o teto menor e A antes de P antes de C. As contas valem como taxas: todas têm o mesmo `n_avaliavel`."""
    ceiling = 100 * base["auto_errado"] + RULE_PP * base["n_avaliavel"]
    ok = [x for x in grid if 100 * x["auto_errado"] <= ceiling]
    return min(ok, key=lambda x: (-x["auto_certo"], -x["corte"], _cap_rank(x["teto"]), THRESHOLD_VARIANTS.index(x["variante"])),
               default=None)


def per_person(samples: Sequence[ProfileSamples], config: tuple[float, int | None]) -> list[dict[str, Any]]:
    return [{"pessoa": f"#{k}", "amostras": len(ps.vectors), "reunioes": len({m for m in ps.meetings if m is not None}),
             "referencias": list(sizes), "limiar": None if t is None else round(t, 3)}
            for k, ps, sizes, t in person_view(samples, config, {})]


def choice_line(samples: Sequence[ProfileSamples], grid: Sequence[dict[str, Any]], base: dict[str, Any]) -> dict[str, Any]:
    people = people_in_two_meetings(samples)
    line: dict[str, Any] = {"stage": "escolha", "pessoas_em_2_reunioes": people}
    if people < MIN_PEOPLE or not base["n_avaliavel"]:
        why = (f"{people} pessoa(s) com amostras em 2 reuniões ou mais (mínimo {MIN_PEOPLE})" if people < MIN_PEOPLE
               else "nenhuma voz avaliável")
        return {**line, "dados_insuficientes": True, "motivo": f"{why}: os padrões ficam e a medida vai para o G2",
                "padroes_ficam": DEFAULTS, "por_pessoa": per_person(samples, (REF_COS, MAX_REFS))}
    best = choose(grid, base)
    if best is None:
        return {**line, "dados_insuficientes": False, "vencedora": None, "padroes_ficam": DEFAULTS,
                "motivo": "nenhuma configuração com auto errado até 1 ponto percentual acima da linha de base",
                "por_pessoa": per_person(samples, (REF_COS, MAX_REFS))}
    winner = {"ref_cos": best["corte"], "max_refs": best["teto"], "variante": best["variante"]}
    return {**line, "dados_insuficientes": False, "vencedora": winner, "muda_os_padroes": winner != DEFAULTS,
            "auto_certo_pct": best["auto_certo_pct"], "auto_errado_pct": best["auto_errado_pct"],
            "linha_de_base": {"auto_certo_pct": base["auto_certo_pct"], "auto_errado_pct": base["auto_errado_pct"]},
            "por_pessoa": per_person(samples, (best["corte"], best["teto"]))}


# ---------------------------------------------------------------------------- execução
def _load(store: Store, model: str) -> tuple[list[ProfileSamples], dict[str, float]]:
    """As amostras ativas e os ms da carga do L1: a consulta e o agrupamento com os padrões (o scipy já importado)."""
    from scipy.cluster import hierarchy  # noqa: F401  (a importação, ~0,5 s, fora da medida)

    t0 = time.perf_counter()
    samples = store.profiles.active_samples(model)
    t1 = time.perf_counter()
    person_view(samples, (REF_COS, MAX_REFS), {})
    t2 = time.perf_counter()
    return samples, {"consulta": round(1000 * (t1 - t0), 1), "agrupar": round(1000 * (t2 - t1), 1)}


def evaluate(store: Store, model: str | None = None, threshold: float | None = None,
             margin: float | None = None) -> list[dict[str, Any]]:
    """Todas as linhas da sonda, na ordem: o inventário, a linha de base, as 60 configurações e a escolha."""
    model = model or default_model(store)
    if model is None:
        return [{"stage": "escolha", "dados_insuficientes": True, "motivo": "nenhuma amostra ativa: os padrões ficam",
                 "padroes_ficam": DEFAULTS}]
    threshold = float(store.get(SPECS["pipeline.threshold"]) if threshold is None else threshold)
    margin = float(store.get(SPECS["pipeline.margin"]) if margin is None else margin)
    samples, load_ms = _load(store, model)
    dim = Counter(ps.dim for ps in samples).most_common(1)[0][0] if samples else 0
    left_out: Counter[str] = Counter()
    meetings = load_meetings(store, model, dim, left_out)
    names = {int(p["id"]): str(p.get("name") or "") for p in store.people.list_people()}
    stored = {int(p["id"]): p.get("threshold") for p in store.profiles.list_profiles(model)}
    cache, voices = RefCache(samples, names, stored), Counter(v.platform for m in meetings for v in m.voices)
    lines = [{**inventory(store, model, samples, meetings, left_out, load_ms), "limiar_global": threshold, "margem": margin}]
    base = config_line("linha_de_base", None, "A", run_config(meetings, cache, None, "A", threshold, margin),
                       summary(person_view(samples, None, stored)), voices)
    grid: list[dict[str, Any]] = []
    for cut in CUTS:
        for cap in CAPS:
            view = summary(person_view(samples, (cut, cap), stored))
            grid += [config_line("config", (cut, cap), v, run_config(meetings, cache, (cut, cap), v, threshold, margin), view, voices)
                     for v in THRESHOLD_VARIANTS]
    return [*lines, base, *grid, choice_line(samples, grid, base)]


def out(line: dict[str, Any]) -> None:
    print(json.dumps(line, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="a pasta do banco (aberto só para leitura)")
    ap.add_argument("--model", help="o modelo de embedding (padrão: o de mais amostras ativas)")
    ap.add_argument("--threshold", type=float, help="o limiar global (padrão: o das Configurações)")
    ap.add_argument("--margin", type=float, help="a margem entre pessoas (padrão: a das Configurações)")
    args = ap.parse_args(argv)
    with Store.open_readonly(args.db) as store:
        for line in evaluate(store, args.model, args.threshold, args.margin):
            out(line)
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
