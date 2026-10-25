"""Repositório de configurações (tabela `settings`): texto bruto por chave e leitura tipada por `SettingSpec` (M-24)."""
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
