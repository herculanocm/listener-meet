"""Provedores de LLM (`providers`, CP10: chave em texto aberto no SQLite; F3 item 12). A linha `huggingface` da F2 continua
aqui, fora das sub-abas (`LLM_KINDS`). `update_provider` tem lista branca (padrão de `update_cluster`); `set_active`
troca o ativo numa só instrução; `active_provider(role)` devolve o ativo para texto ou embeddings.
F5.1 (A-01): a linha `local` (embeddings na CPU, sem chave) só vale para o papel `embed`; nasce por `INSERT OR IGNORE`
(`ensure_local`, dado e não esquema) só no caminho de escrita (as Configurações), nunca numa leitura (o `--check` abre só
leitura); `set_role` faz um único `UPDATE` do papel, que escolhe um provedor ou zera o papel.
Revisão da F5.4 (R-05): `tested_at` e `latency_ms` são da chave salva; trocar a chave sem dizer o teste dela os limpa (o
selo "Conectado" só vale para a chave que passou no último teste)."""
from __future__ import annotations

import json
from typing import Any, TypedDict, cast

from ..log import get_logger
from .core import Core, _now
from .settings import safe_url

log = get_logger("store.providers")

LLM_KINDS: tuple[str, ...] = ("gemini", "openai", "anthropic")
SHOWN_PROVIDER_FIELDS = frozenset({"model", "fast_model", "strong_model", "base_url", "latency_ms"})   # vão com o valor ao log
LOCAL_ID = "local"                                    # F5.1: o id e o tipo da linha do embedder local
PROVIDER_FIELDS = frozenset({"api_key", "model", "params", "fast_model", "strong_model", "models_json", "tested_at", "latency_ms",
                             "base_url"})
ROLES = {"llm": "is_active_llm", "embed": "is_active_embed"}
ROLE_KINDS: dict[str, tuple[str, ...]] = {"llm": LLM_KINDS, "embed": (*LLM_KINDS, LOCAL_ID)}


class ProviderRow(TypedDict, total=False):
    id: str
    kind: str
    api_key: str | None
    model: str | None
    params: str | None
    is_active_llm: int
    is_active_embed: int
    updated_at: str | None
    fast_model: str | None
    strong_model: str | None
    models_json: str | None
    tested_at: str | None
    latency_ms: int | None
    base_url: str | None


class ProvidersRepo:
    def __init__(self, core: Core):
        self.core = core

    def get(self, provider_id: str) -> ProviderRow | None:
        rows = self.core.q("SELECT * FROM providers WHERE id = ?", (provider_id,))
        return cast(ProviderRow, dict(rows[0])) if rows else None

    def list_llm(self) -> list[ProviderRow]:
        """As linhas dos provedores de LLM na ordem fixa (Gemini primeiro, CP14); cria as que faltarem."""
        for pid in LLM_KINDS:
            self.core.execute("INSERT OR IGNORE INTO providers(id, kind, updated_at) VALUES (?, ?, ?)", (pid, pid, _now()))
        rows = {str(r["id"]): cast(ProviderRow, dict(r)) for r in self.core.q("SELECT * FROM providers")}
        return [rows[pid] for pid in LLM_KINDS if pid in rows]

    def key(self, provider_id: str) -> str:
        p = self.get(provider_id)
        return str(p.get("api_key") or "") if p else ""

    def update_provider(self, provider_id: str, **fields: Any) -> None:
        bad = set(fields) - PROVIDER_FIELDS
        if bad:
            raise ValueError(f"campos de provedor desconhecidos: {sorted(bad)}")
        self.core.execute("INSERT OR IGNORE INTO providers(id, kind, updated_at) VALUES (?, ?, ?)", (provider_id, provider_id, _now()))
        if not fields:
            return
        if "api_key" in fields and "tested_at" not in fields and (fields["api_key"] or None) != (self.key(provider_id) or None):
            fields = {**fields, "tested_at": None, "latency_ms": None}       # R-05: o teste era da chave anterior
        log.info("provedor %s atualizado: %s", provider_id, ", ".join(
            (f"api_key {'definida' if fields[k] else 'apagada'}" if k == "api_key" else
             f"{k} = {safe_url(str(fields[k]))!r}" if k in SHOWN_PROVIDER_FIELDS else k) for k in fields))   # sem chave nem senha
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else v for v in fields.values()]
        self.core.execute(f"UPDATE providers SET {cols}, updated_at = ? WHERE id = ?", (*values, _now(), provider_id))  # noqa: S608

    def set_active(self, provider_id: str, *, llm: bool | None = None, embed: bool | None = None) -> None:
        """Um ativo por papel: `UPDATE providers SET is_active_llm = (id = ?)` (F3A-07); False desativa todos."""
        log.info("provedor %s: ativo para texto %s, para embeddings %s", provider_id, "—" if llm is None else ("sim" if llm else "não"),
                 "—" if embed is None else ("sim" if embed else "não"))
        with self.core.transaction():
            for flag, col in ((llm, "is_active_llm"), (embed, "is_active_embed")):
                if flag is None:
                    continue
                if flag:
                    self.core.execute(f"UPDATE providers SET {col} = (id = ?)", (provider_id,))  # noqa: S608
                else:
                    self.core.execute(f"UPDATE providers SET {col} = 0 WHERE id = ?", (provider_id,))  # noqa: S608

    def set_role(self, role: str, provider_id: str | None) -> None:
        """O ativo do papel numa instrução só (A-01): `provider_id` passa a ser o único ativo; None (ou vazio) zera o papel."""
        col = ROLES[role]
        log.info("provedor ativo para %s: %s", "texto" if role == "llm" else "embeddings", provider_id or "nenhum")
        if provider_id:
            self.core.execute(f"UPDATE providers SET {col} = (id = ?)", (provider_id,))  # noqa: S608
        else:
            self.core.execute(f"UPDATE providers SET {col} = 0 WHERE {col} != 0")  # noqa: S608

    def ensure_local(self) -> None:
        """A linha `local` (F5.1, A-01), criada só quando o usuário escolhe o embedder local (sem migração: é dado)."""
        self.core.execute("INSERT OR IGNORE INTO providers(id, kind, updated_at) VALUES (?, ?, ?)", (LOCAL_ID, LOCAL_ID, _now()))

    def active_provider(self, role: str = "llm") -> ProviderRow | None:
        """O ativo do papel entre os tipos que o papel aceita (o `local` só para `embed`)."""
        col, kinds = ROLES[role], ROLE_KINDS[role]
        rows = self.core.q(f"SELECT * FROM providers WHERE {col} = 1 AND kind IN ({','.join('?' * len(kinds))}) "  # noqa: S608
                           "ORDER BY id LIMIT 1", kinds)
        return cast(ProviderRow, dict(rows[0])) if rows else None

    def active_with_key(self, role: str = "llm") -> ProviderRow | None:
        """O ativo só conta quando tem chave: sem chave nenhum job `llm` é criado (F3 comportamento 6)."""
        row = self.active_provider(role)
        return row if row is not None and (row.get("api_key") or "").strip() else None

    def models(self, provider_id: str) -> list[dict[str, Any]]:
        """Cache de `list_models()` em `providers.models_json`."""
        p = self.get(provider_id)
        try:
            data = json.loads((p or {}).get("models_json") or "[]")
        except ValueError:
            return []
        return [d for d in data if isinstance(d, dict)]

    # ---------------------------------------------------------------- preços (llm_prices)
    def list_prices(self, provider: str | None = None) -> list[dict[str, Any]]:
        if provider is None:
            rows = self.core.q("SELECT * FROM llm_prices ORDER BY provider, model")
        else:
            rows = self.core.q("SELECT * FROM llm_prices WHERE provider = ? ORDER BY model", (provider,))
        return [dict(r) for r in rows]

    def set_price(self, provider: str, model: str, input_usd_m: float, output_usd_m: float, cached_usd_m: float = 0.0,
                  audio_usd_m: float = 0.0) -> None:
        self.core.execute("INSERT INTO llm_prices(provider, model, input_usd_m, output_usd_m, cached_usd_m, audio_usd_m, updated_at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(provider, model) DO UPDATE SET input_usd_m = excluded.input_usd_m, "
                          "output_usd_m = excluded.output_usd_m, cached_usd_m = excluded.cached_usd_m, audio_usd_m = excluded.audio_usd_m, "
                          "updated_at = excluded.updated_at",
                          (provider, model, float(input_usd_m), float(output_usd_m), float(cached_usd_m), float(audio_usd_m), _now()))
