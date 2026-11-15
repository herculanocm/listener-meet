"""As telas e as medidas da F5.25 no `win_probe` (não fazem parte do produto).

`f525_shots(shot, app)`, no `win_probe shots`: a janela "Outra pessoa" com 63 pessoas sintéticas, nos quatro estados do
Aceite: o campo vazio (as três seções, "já na Voz 2" e a nota), com texto, com "Nomes parecidos" e sem resultado.

`f525_timings(app)`, na linha `f525_tempos` do mesmo subcomando (o Aceite): abrir a janela com 200 pessoas com nota contra a
caixa de antes (`QInputDialog` com as 3 de "Pela voz" e a lista), alternadas na mesma rodada, mediana de 7 rodadas (meta: no
máximo 20 ms a mais); e filtrar uma tecla com 1.000 pessoas, p95 de 50 rodadas (meta: até 30 ms). A nota pela voz é a mesma
nos dois caminhos e fica fora da diferença (a spec a mediu: 119 ms com 200 pessoas com perfil). Os nomes são inventados."""
from __future__ import annotations

import random
import statistics
import time
from collections.abc import Callable
from typing import Any

from probe_f517 import _discard, _offscreen

OPEN_GOAL_MS = 20.0                      # a janela a mais que a caixa de antes, com 200 pessoas com nota
KEY_GOAL_MS = 30.0                       # uma tecla com 1.000 pessoas, no p95
FIRST = ["Ana", "Bia", "Caio", "Davi", "Elisa", "Fábio", "Gustavo", "Helena", "Igor", "Júlia", "Lucas", "Marina", "Nuno", "Olívia",
         "Paulo", "Renata", "Sérgio", "Tânia", "Vítor", "Zélia", "João", "Conceição"]
LAST = ["Souza", "Lima", "Reis", "Luz", "Prado", "Moraes", "Teixeira", "Barros", "Cardoso", "Nogueira", "Freitas", "Siqueira"]
ROLES = ["", "CFO", "Diretora de Risco", "Analista de Crédito", "Jurídico", "TI", "Gestor de Fundos", "Compliance"]
COMPANIES = ["", "Acme", "Fundo Alfa", "Banco Beta"]


def _people(n: int, scored: int, seed: int = 25) -> list[Any]:
    """`n` pessoas inventadas na ordem de `list_people` (você primeiro), as `scored` primeiras com nota pela voz."""
    from listener.ui.person import PersonData, person_labels
    from listener.ui.person_picker import Candidate

    rnd = random.Random(seed)                                # noqa: S311 (nomes de teste, não segredo)
    rows = [{"id": 1, "name": "Você", "role": "Gerente", "company": ""}]
    rows += [{"id": i, "name": f"{rnd.choice(FIRST)} {rnd.choice(LAST)}", "role": rnd.choice(ROLES), "company": rnd.choice(COMPANIES)}
             for i in range(2, n + 1)]
    rows = rows[:1] + sorted(rows[1:], key=lambda r: r["name"].casefold())
    labels = person_labels(rows)
    out = []
    for k, (r, label) in enumerate(zip(rows, labels, strict=True)):
        data = PersonData(r["name"], r["role"], r["company"])
        out.append(Candidate(r["id"], label, data.name, data.role, data.company, rnd.uniform(-0.1, 0.62) if k < scored else None,
                             expected=k % 17 == 5, taken="Voz 2" if k == 9 else ""))
    return out


def _picker(people: list[Any], parent: Any = None) -> Any:
    from listener.ui.person_picker import PersonIndex, PersonPicker, top_by_voice

    ranked = sorted(((c.person_id, c.score) for c in people if c.score is not None), key=lambda kv: -kv[1])
    index = PersonIndex(people, top_by_voice(ranked, None, {c.person_id for c in people}))
    return PersonPicker(parent, index, question="Quem é a Voz 3?", threshold=0.60)


def f525_shots(shot: Callable[..., None], app: Any) -> None:
    people = _people(63, 62)
    for name, typed in (("vazio", ""), ("com-texto", "lima"), ("nomes-parecidos", "jaoo"), ("sem-resultado", "Zuleica Prado")):
        picker = _picker(people)
        picker.search.setText(typed)
        app.processEvents()
        shot(picker, f"f525-{name}")
        _discard(picker)


def _old_box(people: list[Any]) -> Any:
    """A caixa de antes (`QInputDialog.getItem` sem o `exec`): as 3 de "Pela voz" com "voz 0,58" e a lista inteira."""
    from PySide6.QtWidgets import QInputDialog

    top = sorted((c for c in people if c.score is not None and c.score >= 0.45), key=lambda c: -c.score)[:3]
    shown = [f"{c.label} · voz {c.score:.2f}".replace(".", ",") for c in top] + [c.label for c in people]
    box = QInputDialog()
    box.setWindowTitle("Outra pessoa")
    box.setLabelText("Quem é esta voz?")
    box.setComboBoxItems(shown)
    box.setComboBoxEditable(False)
    return box


def _open_ms(app: Any, make: Callable[[], Any]) -> float:
    t0 = time.perf_counter()
    w = make()
    _offscreen(w, app)
    ms = (time.perf_counter() - t0) * 1000
    _discard(w)
    app.processEvents()
    return ms


def f525_timings(app: Any, *, rounds: int = 7, keys: int = 50) -> dict[str, Any]:
    people = _people(200, 200)
    base, now = [], []
    for _ in range(rounds):                                  # alternadas: o ruído do Teams e do EDR cai nas duas
        base.append(_open_ms(app, lambda: _old_box(people)))
        now.append(_open_ms(app, lambda: _picker(people)))
    delta = statistics.median(now) - statistics.median(base)
    picker = _picker(_people(1000, 1000))
    _offscreen(picker, app)
    typed = ["a", "an", "ana", "ana ", "ana l", "ana li", "ana lim", "ana lima", "s", "so", "sou", "c", "cf", "cfo", "j", "jo", "joa",
             "joao", "jaoo", "z"]
    took = []
    for i in range(keys):
        t0 = time.perf_counter()
        picker.search.setText(typed[i % len(typed)])       # a tecla: o filtro roda no `textChanged`
        app.processEvents()                                  # e o layout da lista nova
        took.append((time.perf_counter() - t0) * 1000)
    _discard(picker)
    p95 = statistics.quantiles(took, n=20)[-1]
    return {"abrir_base_ms": round(statistics.median(base), 1), "abrir_janela_ms": round(statistics.median(now), 1),
            "abrir_a_mais_ms": round(delta, 1), "abrir_ok": delta <= OPEN_GOAL_MS, "tecla_p95_ms": round(p95, 1),
            "tecla_max_ms": round(max(took), 1), "tecla_ok": p95 <= KEY_GOAL_MS}


__all__ = ["f525_shots", "f525_timings"]
