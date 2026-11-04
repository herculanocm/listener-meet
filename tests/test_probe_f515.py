"""F5.15 L0: a sonda das referências de voz (`scripts/probe_f515.py`) num banco sintético. Deixando uma reunião de fora por
vez, a Ana numa condição nova (outro eixo) só é reconhecida com mais de uma referência; a escolha segue a regra do item
2; nenhum nome, id de pessoa ou de reunião sai no que a sonda imprime (D-053); com uma pessoa só em 2 reuniões, os padrões
ficam."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest

from listener.model import ClusterStatus, MeetingStatus
from listener.pipeline import review
from listener.store import Store
from listener.store.profiles import normalize
from listener.store.segments import ClusterInsert

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("Anastácia Zimmerwald", "Bianca Quintela", "Caio Ventura")


def _load() -> ModuleType:
    """O script pelo caminho, registrado em `sys.modules` antes de rodar (as dataclasses dele procuram o módulo lá)."""
    spec = importlib.util.spec_from_file_location("probe_f515", ROOT / "scripts" / "probe_f515.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _near(axis: int, rng: np.random.Generator) -> np.ndarray:
    v = np.zeros(16, np.float32)
    v[axis] = 1.0
    return normalize(v + rng.normal(scale=0.05, size=16).astype(np.float32))


def _meeting(store: Store, platform: str, voices: list[tuple[int, int, ClusterStatus]], rng: np.random.Generator,
             reviewed: bool = False) -> int:
    """Uma reunião processada com as vozes (eixo, pessoa, estado): `confirmado` passa pela revisão (grava a amostra)."""
    mid = store.create_meeting("reunião sintética", platform, None, None)
    job = store.jobs.enqueue(mid)
    rows = [ClusterInsert(mid, job, "R", f"SPK_{i}", 20_000, _near(axis, rng), "wespeaker",
                          ClusterStatus.UNNAMED if status == ClusterStatus.CONFIRMED else status,
                          person_id=None if status == ClusterStatus.CONFIRMED else person)
            for i, (axis, person, status) in enumerate(voices)]
    ids = store.segments.insert_clusters([*rows, ClusterInsert(mid, job, "L", "L", 30_000, None, None, ClusterStatus.CONFIRMED)])
    for i, (_axis, person, status) in enumerate(voices):
        if status == ClusterStatus.CONFIRMED:
            review.confirm(store, ids[f"SPK_{i}"], person)
    store.meetings.set_status(mid, MeetingStatus.REVIEWED if reviewed else MeetingStatus.PROCESSED)
    return mid


def _rule(grid: list[dict[str, Any]], base: dict[str, Any]) -> dict[str, Any]:
    """A regra do item 2, escrita de novo aqui: auto errado até 1 ponto acima da base; o maior auto certo; no empate, o corte
    maior, o teto menor (sem teto por último) e A, P, C."""
    ok = [x for x in grid if x["auto_errado_pct"] <= base["auto_errado_pct"] + 1.0]
    top = max(x["auto_certo"] for x in ok)
    best = [x for x in ok if x["auto_certo"] == top]
    cut = max(x["corte"] for x in best)
    best = [x for x in best if x["corte"] == cut]
    cap = min(x["teto"] if x["teto"] is not None else 99 for x in best)
    best = [x for x in best if (x["teto"] if x["teto"] is not None else 99) == cap]
    return min(best, key=lambda x: "APC".index(x["variante"]))


def test_probe_measures_leaving_one_meeting_out_and_chooses_by_the_rule(store: Store, capsys: pytest.CaptureFixture[str]):
    rng = np.random.default_rng(15)
    ana, bia, caio = (store.people.create_person(n) for n in NAMES)
    confirmed, auto = ClusterStatus.CONFIRMED, ClusterStatus.AUTO
    _meeting(store, "ms-teams", [(0, ana, confirmed), (1, bia, confirmed)], rng)
    _meeting(store, "ms-teams", [(0, ana, confirmed), (1, bia, confirmed), (2, caio, confirmed)], rng)
    _meeting(store, "zoom", [(3, ana, confirmed), (1, bia, confirmed)], rng)                  # a Ana numa condição nova
    _meeting(store, "zoom", [(3, ana, confirmed), (1, bia, auto)], rng, reviewed=True)         # `auto` de reunião revisada
    _meeting(store, "ms-teams", [(1, bia, auto), (4, caio, ClusterStatus.IGNORED), (5, ana, ClusterStatus.UNNAMED)], rng)
    probe = _load()
    lines = probe.evaluate(store)
    inv, base, grid, choice = lines[0], lines[1], lines[2:-1], lines[-1]
    assert [inv["stage"], base["stage"], choice["stage"]] == ["inventario", "linha_de_base", "escolha"]
    assert inv["modelo"] == "wespeaker" and inv["pessoas"] == 3 and inv["pessoas_em_2_reunioes"] == 2
    assert inv["amostras_por_fonte"] == {"confirmacao": 8} and inv["amostras_por_plataforma"] == {"ms-teams": 5, "zoom": 3}
    assert inv["vozes_sonda"] == 9 and inv["vozes_fora"] == {"auto_nao_revisada": 1, "ignoradas": 1, "pendentes": 1}
    assert inv["dimensoes_ignoradas"] == 0 and set(inv["carga_ms"]) == {"consulta", "agrupar"}
    assert {(x["stage"], x["corte"], x["teto"], x["variante"]) for x in grid} == {
        ("config", c, t, v) for c in (0.70, 0.75, 0.80, 0.85, 1.0) for t in (1, 3, 5, None) for v in "APC"}
    assert len(grid) == 60 and all(x["n_avaliavel"] == base["n_avaliavel"] == 8 for x in grid)
    assert base["sem_perfil_fora"] == 1 and base["teto"] == 1 and base["variante"] == "A"          # o Caio só numa reunião
    keys = {"auto_certo", "auto_errado", "pendente_certa", "pendente_errada", "auto_certo_pct", "certas_entre_060_e_limiar",
            "nota_certa", "melhor_errada", "referencias", "limiares", "por_plataforma"}
    assert keys <= set(base) and set(base["nota_certa"]) == {"p10", "p50", "p90"}
    assert list(base["por_plataforma"]) == ["ms-teams"]                                    # zoom tem só 4 vozes-sonda
    assert base["auto_certo"] + base["auto_errado"] + base["pendente_certa"] + base["pendente_errada"] == 8
    # a média única não acha a Ana na condição nova (nem na antiga, quando ela fica de fora); duas referências acham
    multi = next(x for x in grid if (x["corte"], x["teto"], x["variante"]) == (0.80, 5, "P"))
    assert (base["auto_certo"], base["pendente_certa"], multi["auto_certo"], multi["auto_errado"]) == (4, 4, 8, 0)
    assert multi["referencias"] == {"media": 1.33, "maximo": 2} and multi["limiares"]["nulos"] == 3
    expected = _rule(grid, base)
    winner = (expected["corte"], expected["teto"], expected["variante"])
    assert choice["dados_insuficientes"] is False and choice["vencedora"] == dict(zip(("ref_cos", "max_refs", "variante"), winner,
                                                                                         strict=True))
    assert choice["auto_certo_pct"] == expected["auto_certo_pct"] and choice["linha_de_base"]["auto_certo_pct"] == base["auto_certo_pct"]
    assert [p["pessoa"] for p in choice["por_pessoa"]] == ["#1", "#2", "#3"]
    assert probe.main(["--db", str(store.base_dir)]) == 0
    printed = capsys.readouterr().out
    assert [json.loads(line)["stage"] for line in printed.splitlines()][-1] == "escolha"
    for name in (*NAMES, *(n.split()[0] for n in NAMES)):
        assert name not in printed
    assert all(f'"{key}"' not in printed for key in ("person_id", "meeting_id", "profile_id", "nome", "name"))


def test_probe_keeps_the_defaults_with_too_few_people_in_two_meetings(store: Store):
    rng = np.random.default_rng(2)
    ana, bia = (store.people.create_person(n) for n in NAMES[:2])
    _meeting(store, "ms-teams", [(0, ana, ClusterStatus.CONFIRMED), (1, bia, ClusterStatus.CONFIRMED)], rng)
    _meeting(store, "ms-teams", [(0, ana, ClusterStatus.CONFIRMED)], rng)
    choice = _load().evaluate(store)[-1]
    assert choice["dados_insuficientes"] is True and choice["pessoas_em_2_reunioes"] == 1
    assert choice["padroes_ficam"] == {"ref_cos": 0.80, "max_refs": 5, "variante": "P"} and "vencedora" not in choice
    assert _load().evaluate(store, model="nenhum")[-1]["dados_insuficientes"] is True                  # modelo sem amostra
