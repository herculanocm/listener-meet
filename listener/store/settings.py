"""Repositório de configurações (tabela `settings`): texto bruto por chave e leitura tipada por `SettingSpec` (M-24).
Também a tabela `providers` (chaves de API em texto aberto no SQLite, CP10): a F2 usa a linha `huggingface` (Q2)."""
from __future__ import annotations

from typing import Any

from ..config.defaults import SPECS, SettingSpec
from ..log import get_logger
from .core import Core, _now

log = get_logger("store.settings")


class SettingsRepo:
    def __init__(self, core: Core):
        self.core = core

    def get_setting(self, key: str, default: str | None = None) -> str:
        rows = self.core.q("SELECT value FROM settings WHERE key = ?", (key,))
        if rows:
            return str(rows[0]["value"])
        if default is not None:
            return default
        spec = SPECS.get(key)
        return spec.default_text if spec is not None else ""

    def get[T](self, spec: SettingSpec[T]) -> T:
        """Valor tipado e validado; texto inválido no banco cai no padrão com aviso no log."""
        raw = self.get_setting(spec.key)
        try:
            return spec.value(raw)
        except (ValueError, TypeError) as e:
            log.warning("configuração %s inválida (%r): usando o padrão %r (%s)", spec.key, raw, spec.default, e)
            return spec.default

    def get_int(self, key: str) -> int:
        return int(float(self.get_setting(key)))

    def get_float(self, key: str) -> float:
        return float(self.get_setting(key))

    def set_setting(self, key: str, value: Any, scope: str | None = None) -> None:
        spec = SPECS.get(key)
        scope = scope or (str(spec.scope) if spec is not None else "now")
        self.core.execute("INSERT INTO settings(key, value, scope, updated_at) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value, scope = excluded.scope, "
                          "updated_at = excluded.updated_at", (key, str(value), scope, _now()))

    def all_settings(self) -> dict[str, str]:
        out = {k: s.default_text for k, s in SPECS.items()}
        for r in self.core.q("SELECT key, value FROM settings"):
            out[r["key"]] = r["value"]
        return out

    def settings_with_prefix(self, prefix: str) -> dict[str, str]:
        """Snapshot das chaves de um grupo (`pipeline.`) com os padrões preenchidos: vai para `config_json` do job."""
        return {k: v for k, v in self.all_settings().items() if k.startswith(prefix)}

    # ---------------------------------------------------------------- providers
    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        rows = self.core.q("SELECT * FROM providers WHERE id = ?", (provider_id,))
        return dict(rows[0]) if rows else None

    def upsert_provider(self, provider_id: str, kind: str, api_key: str | None = None, model: str | None = None,
                        params: str | None = None, clear_key: bool = False) -> None:
        """Cria ou atualiza só o que foi passado (F3A-07: antes zerava `model` e `params` ao salvar o token HF).
        `api_key` vazia ou None com `clear_key` apaga a chave; sem `clear_key`, None mantém a atual."""
        key = api_key if (api_key or clear_key) else None
        self.core.execute("INSERT INTO providers(id, kind, api_key, model, params, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                          "ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, "
                          "api_key = CASE WHEN ? THEN excluded.api_key ELSE COALESCE(excluded.api_key, api_key) END, "
                          "model = COALESCE(excluded.model, model), params = COALESCE(excluded.params, params), "
                          "updated_at = excluded.updated_at",
                          (provider_id, kind, key, model, params, _now(), int(bool(clear_key))))

    def provider_key(self, provider_id: str) -> str:
        p = self.get_provider(provider_id)
        return str(p.get("api_key") or "") if p else ""
