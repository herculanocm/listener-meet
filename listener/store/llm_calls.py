"""Toda chamada à LLM (`llm_calls`, F3 item 13, CP9): tokens, custo, latência, estado, erro já redigido, estratégia
(`direct`, `map_reduce:n`, `resend`), pedido e resposta completos comprimidos com zlib quando `llm.store_payloads` está
ligado (P8). `meeting_id` nulo = chamada fora de reunião (glossário no warm-up, testar conexão); `set_meeting` religa."""
from __future__ import annotations

import json
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from ..model import LlmCallStatus
from .core import Core, _now, json_default


class LlmCallRow(TypedDict, total=False):
    id: int
    meeting_id: int | None
    job_id: int | None
    task: str
    provider: str
    model: str
    prompt_version: str | None
    schema_hash: str | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    audio_tokens: int
    cost_usd: float | None
    latency_ms: int
    status: str
    error: str | None
    strategy: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class LlmCallInsert:
    meeting_id: int | None
    job_id: int | None
    task: str
    provider: str
    model: str
    prompt_version: str | None
    schema_hash: str | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    audio_tokens: int
    cost_usd: float | None
    latency_ms: int
    status: LlmCallStatus
    error: str | None = None
    strategy: str | None = "direct"
    request: Any = None              # serializável em JSON (já redigido); comprimido só se `store_payloads`
    response: Any = None


def pack_blob(data: Any) -> bytes | None:
    if data is None:
        return None
    return zlib.compress(json.dumps(data, ensure_ascii=False, default=json_default).encode("utf-8"), 6)


def unpack_blob(blob: bytes | memoryview | None) -> Any:
    if not blob:
        return None
    return json.loads(zlib.decompress(bytes(blob)).decode("utf-8"))


class LlmCallsRepo:
    def __init__(self, core: Core):
        self.core = core

    def insert(self, call: LlmCallInsert, store_payloads: bool = True) -> int:
        return self.core.insert(
            "INSERT INTO llm_calls(meeting_id, job_id, task, provider, model, prompt_version, schema_hash, input_tokens, output_tokens, "
            "cached_tokens, audio_tokens, cost_usd, latency_ms, status, error, strategy, request_blob, response_blob, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (call.meeting_id, call.job_id, call.task, call.provider, call.model, call.prompt_version, call.schema_hash,
             int(call.input_tokens), int(call.output_tokens), int(call.cached_tokens), int(call.audio_tokens), call.cost_usd,
             int(call.latency_ms), str(call.status), call.error, call.strategy, pack_blob(call.request) if store_payloads else None,
             pack_blob(call.response) if store_payloads else None, _now()))

    def get(self, call_id: int) -> LlmCallRow | None:
        rows = self.core.q("SELECT * FROM llm_calls WHERE id = ?", (call_id,))
        return cast(LlmCallRow, {k: v for k, v in dict(rows[0]).items() if not k.endswith("_blob")}) if rows else None

    def payloads(self, call_id: int) -> tuple[Any, Any]:
        rows = self.core.q("SELECT request_blob, response_blob FROM llm_calls WHERE id = ?", (call_id,))
        if not rows:
            return None, None
        return unpack_blob(rows[0]["request_blob"]), unpack_blob(rows[0]["response_blob"])

    def set_meeting(self, call_id: int, meeting_id: int) -> None:
        self.core.execute("UPDATE llm_calls SET meeting_id = ? WHERE id = ?", (meeting_id, call_id))

    def detach_meeting(self, meeting_id: int) -> int:
        """Antes de apagar a reunião (Cancelar): as chamadas pagas ficam, para o custo do mês, mas sem a reunião e sem o
        conteúdo guardado (pedido e resposta)."""
        return self.core.execute("UPDATE llm_calls SET meeting_id = NULL, request_blob = NULL, response_blob = NULL "
                                 "WHERE meeting_id = ?", (meeting_id,))

    def list_for_meeting(self, meeting_id: int) -> list[LlmCallRow]:
        rows = self.core.q("SELECT id, meeting_id, job_id, task, provider, model, prompt_version, schema_hash, input_tokens, "
                           "output_tokens, cached_tokens, audio_tokens, cost_usd, latency_ms, status, error, strategy, created_at "
                           "FROM llm_calls WHERE meeting_id = ? ORDER BY id", (meeting_id,))
        return [cast(LlmCallRow, dict(r)) for r in rows]

    def cost_for_meeting(self, meeting_id: int) -> tuple[float | None, int]:
        """(soma do custo, chamadas sem preço); soma nula quando não há chamada com custo."""
        row = self.core.q("SELECT SUM(cost_usd), SUM(cost_usd IS NULL), COUNT(*) FROM llm_calls WHERE meeting_id = ?", (meeting_id,))[0]
        total = float(row[0]) if row[0] is not None else None
        return total, int(row[1] or 0)

    def cost_for_job(self, job_id: int) -> float | None:
        row = self.core.q("SELECT SUM(cost_usd) FROM llm_calls WHERE job_id = ?", (job_id,))[0]
        return float(row[0]) if row[0] is not None else None

    def month_total(self, month: str | None = None, provider: str | None = None) -> float:
        """Custo do mês (`AAAA-MM` de `created_at`, local); por provedor quando pedido."""
        month = month or _now()[:7]
        if provider is None:
            row = self.core.q("SELECT SUM(cost_usd) FROM llm_calls WHERE substr(created_at, 1, 7) = ?", (month,))[0]
        else:
            row = self.core.q("SELECT SUM(cost_usd) FROM llm_calls WHERE substr(created_at, 1, 7) = ? AND provider = ?",
                              (month, provider))[0]
        return float(row[0] or 0.0)

    def avg_latency_ms(self, task: str | None = None, model: str | None = None, last: int = 20, *,
                       tasks: Iterable[str] | None = None) -> float | None:
        """Latência média das últimas chamadas bem-sucedidas (estimativa de tempo do job `llm`, F3 item 16); `tasks` limita às
        tarefas dadas (a fila passa as do job `llm`: os lotes de vetores e o chat derrubariam a média, R5-05)."""
        sql = "SELECT latency_ms FROM llm_calls WHERE status = ?"
        params: list[Any] = [str(LlmCallStatus.OK)]
        if task is not None:
            sql += " AND task = ?"
            params.append(task)
        wanted = [str(t) for t in tasks] if tasks is not None else []
        if wanted:
            sql += f" AND task IN ({','.join('?' * len(wanted))})"
            params += wanted
        if model is not None:
            sql += " AND model = ?"
            params.append(model)
        rows = self.core.q(sql + " ORDER BY id DESC LIMIT ?", (*params, int(last)))
        if not rows:
            return None
        return sum(float(r[0]) for r in rows) / len(rows)
