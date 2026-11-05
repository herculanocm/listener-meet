"""F5.16: a reunião transcrita sem diarização (sem o token do Hugging Face). O job grava a marca no `timings_json` e no
`job_done` (`sem_diarizacao`, com o motivo); a reunião aberta e a fila leem a marca do job vigente e mostram como resolver
(U-139). Puro, sem Qt; o token nunca passa por aqui (só se ele existe)."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..store import Store

KEY = "sem_diarizacao"              # no `timings_json` do job e no `job_done`
REASON = "token"                    # o único motivo de hoje: o token do Hugging Face vazio
NOTICE_MISSING = ("Sem quem falou no R: falta o token do Hugging Face. Cole em Configurações, Chaves de acesso, e "
                  "reprocesse a reunião.")
NOTICE_PRESENT = "Sem quem falou no R: a reunião foi processada sem o token. Reprocesse a reunião para saber quem falou."
QUEUE_NOTE = "sem quem falou (falta o token)"
REPROCESS_REFUSED = ("sem o token do Hugging Face, reprocessar tiraria quem falou nesta reunião: cole o token em "
                     "Configurações, Chaves de acesso, e tente de novo")


def mark() -> dict[str, Any]:
    """O que o job grava no `timings_json` e no `job_done`."""
    return {"motivo": REASON}


def reason_in(timings_json: str | None) -> str | None:
    """O motivo gravado num `timings_json` (None = processado com diarização, ou texto inválido)."""
    try:
        data = json.loads(timings_json) if timings_json else {}
    except ValueError:
        return None
    entry = data.get(KEY) if isinstance(data, dict) else None
    return str(entry.get("motivo") or REASON) if isinstance(entry, dict) else None


def meeting_reason(store: Store, meeting_id: int) -> str | None:
    """O motivo da reunião sem diarização, pelo job vigente (o que escreveu os segmentos que a tela mostra); None sem ele."""
    job_id = store.segments.current_job_id(meeting_id)
    job = store.jobs.get(job_id) if job_id is not None else None
    return reason_in(job.get("timings_json")) if job else None


def would_lose_voices(store: Store, meeting_id: int, job_id: int) -> bool:
    """Reprocessar sem o token tiraria quem falou: um job anterior da reunião tem voz que não é o `L` do usuário (o
    `USER_CLUSTER` do runner). O runner então recusa, e a reunião fica como estava (CP3)."""
    return any(not (c["channel"] == "L" and c["cluster"] == "L") for c in store.segments.list_clusters(meeting_id)
               if int(c["job_id"]) != job_id)


def notice(has_key: bool) -> str:
    """U-139: o texto da faixa da reunião aberta, sem o token ou com ele já colado."""
    return NOTICE_PRESENT if has_key else NOTICE_MISSING


__all__ = ["KEY", "NOTICE_MISSING", "NOTICE_PRESENT", "QUEUE_NOTE", "REASON", "REPROCESS_REFUSED", "mark", "meeting_reason", "notice",
           "reason_in", "would_lose_voices"]
