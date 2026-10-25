"""`LlmJobConfig` (F3 item 15, F3A-21): snapshot do provedor, modelos, parâmetros e `llm.*` gravado em `config_json` do job
`llm` no claim; jobs em curso terminam com o snapshot, o próximo usa o novo ativo. A chave nunca entra aqui: é lida de
`providers` na hora da chamada (chave trocada vale já; apagada vira erro permanente). `retry(reset_config=True)` descarta."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Self

from .models_table import DEFAULT_MODELS
from .types import ProviderKind

if TYPE_CHECKING:
    from ..store import Store

FORBIDDEN_KEYS = ("api_key", "key", "token", "secret")


@dataclass(frozen=True, slots=True)
class LlmJobConfig:
    provider_id: str = ""
    provider_kind: str = ""
    fast_model: str = ""
    strong_model: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    output_language: str = "pt-BR"
    max_parallel: int = 2
    cost_alert_usd: float = 2.0
    audio_second_opinion: bool = False
    store_payloads: bool = True
    auto_tasks: bool = True
    proxy_url: str = ""
    ca_bundle: str = ""
    system_certs: bool = True

    @classmethod
    def from_store(cls, store: Store, provider_id: str | None = None) -> Self:
        """Do banco agora: o provedor pedido ou o ativo para texto (sem provedor ativo, `provider_id` fica vazio)."""
        from ..config.defaults import SPECS

        row = store.providers.get(provider_id) if provider_id else store.providers.active_provider("llm")
        params: dict[str, Any] = {}
        fast = strong = kind = pid = ""
        if row is not None:
            pid, kind = str(row["id"]), str(row["kind"])
            try:
                params = {k: v for k, v in dict(json.loads(row.get("params") or "{}")).items() if k not in FORBIDDEN_KEYS}
            except (ValueError, TypeError):
                params = {}
            defaults = DEFAULT_MODELS.get(ProviderKind(kind), ("", "")) if kind in ProviderKind.__members__.values() else ("", "")
            fast = str(row.get("fast_model") or row.get("model") or defaults[0])
            strong = str(row.get("strong_model") or defaults[1] or fast)
        g = store.get
        return cls(pid, kind, fast, strong, params, str(g(SPECS["llm.output_language"])), int(g(SPECS["llm.max_parallel"])),
                   float(g(SPECS["llm.cost_alert_usd"])), bool(g(SPECS["llm.audio_second_opinion"])), bool(g(SPECS["llm.store_payloads"])),
                   bool(g(SPECS["llm.auto_tasks"])), str(g(SPECS["llm.proxy_url"])), str(g(SPECS["llm.ca_bundle"])),
                   bool(g(SPECS["llm.system_certs"])))

    @classmethod
    def from_json(cls, text: str | None) -> Self:
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        names = set(cls.__dataclass_fields__)
        clean = {k: v for k, v in data.items() if k in names and k not in FORBIDDEN_KEYS}
        if isinstance(clean.get("params"), dict):
            clean["params"] = {k: v for k, v in clean["params"].items() if k not in FORBIDDEN_KEYS}
        try:
            return cls(**clean)
        except TypeError:
            return cls()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    def model_for(self, strong: bool) -> str:
        return (self.strong_model or self.fast_model) if strong else (self.fast_model or self.strong_model)

    @property
    def ready(self) -> bool:
        return bool(self.provider_id and self.fast_model)


def audio_capable(store: Store) -> bool:
    """`pipeline.transcriber = llm` só com provedor ativo com chave cujo modelo rápido tem `AUDIO_IN` (F3 item 9, F3A-10):
    capacidade pelo cache de `list_models()` em `providers.models_json`, senão pela tabela do projeto."""
    from .models_table import PROVIDER_CAPABILITIES, model_info
    from .types import Capability

    row = store.providers.active_with_key("llm")
    if row is None:
        return False
    cfg = LlmJobConfig.from_store(store, str(row["id"]))
    if not cfg.ready:
        return False
    caps = None
    for m in store.providers.models(cfg.provider_id):
        if str(m.get("id")) == cfg.fast_model and isinstance(m.get("capabilities"), list):
            caps = frozenset(Capability(c) for c in m["capabilities"] if c in Capability.__members__.values())
    if caps is None:
        caps = model_info(cfg.provider_kind, cfg.fast_model).capabilities
    return Capability.AUDIO_IN in caps and Capability.AUDIO_IN in PROVIDER_CAPABILITIES[ProviderKind(cfg.provider_kind)]
