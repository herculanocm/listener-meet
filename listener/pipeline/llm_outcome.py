"""O resultado de cada tarefa de um job `llm` (`TaskOutcome`, saído do `llm_steps.py`, que passa de 400 linhas) e o resumo que
o job grava em `timings_json.llm` (`llm_timings`): custo, estimativa, chamadas, puladas pelo alerta de custo e feitas
(`done`). F5.6 L2 (revisão, R-01): também as que falharam com a resposta fora do esquema (`off_schema`), para o "Restaurar o
prompt padrão" culpar só a tarefa que falhou assim, nunca uma irmã que nem chegou a chamar a LLM (A-29)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..llm.base import OFF_SCHEMA
from ..model import JobStep


@dataclass(slots=True)
class TaskOutcome:
    step: JobStep
    ok: bool
    error: str | None = None
    permanent: bool = False
    skipped: str | None = None

    @property
    def off_schema(self) -> bool:
        """Falhou com a resposta fora do esquema (o erro do `RetryingProvider`, já redigido)."""
        return not self.ok and OFF_SCHEMA in (self.error or "")


def llm_timings(outcomes: Mapping[JobStep, TaskOutcome], skipped: Mapping[str, str], cost_usd: float, estimated_usd: float | None,
                calls: int) -> dict[str, Any]:
    """O `timings_json.llm` do job."""
    return {"cost_usd": round(cost_usd, 6), "estimated_usd": estimated_usd, "calls": calls, "skipped": dict(skipped),
            "done": [str(s) for s, o in outcomes.items() if o.ok and not o.skipped],
            "off_schema": [str(s) for s, o in outcomes.items() if o.off_schema]}


__all__ = ["TaskOutcome", "llm_timings"]
