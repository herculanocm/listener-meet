"""`LlmJobConfig` (F3 item 15, F3A-21): snapshot do provedor, modelos, parâmetros e `llm.*` gravado em `config_json` do job
`llm` no claim; jobs em curso terminam com o snapshot, o próximo usa o novo ativo. A chave nunca entra aqui: é lida de
`providers` na hora da chamada (chave trocada vale já; apagada vira erro permanente). `retry(reset_config=True)` descarta.
F5.3 (A-02): o idioma saiu do snapshot (é o da reunião); um `config_json` antigo com `output_language` carrega sem ele.
F5.6 L2 (A-26, A-28): as instruções personalizadas de cada tarefa (`prompt.<tarefa>`) entram no snapshot: o job que já
pegou o seu usa o texto dele até o fim, mesmo com o prompt trocado nas Configurações. `build` monta o prompt com elas e
`version` grava a marca `+u<hash>` na chamada; o texto nunca vai ao log. Revisão do L2 (R-08, minimização): o snapshot do
claim vai sem instruções (`claim_json`) e o runner congela só as das tarefas do job (`for_job`); o `index` não leva nenhuma."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Any, Self

from ..config import prompts as prompt_settings
from . import prompts
from .models_table import DEFAULT_MODELS
from .types import LlmTask, Message, ProviderKind

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
    max_parallel: int = 2
    cost_alert_usd: float = 2.0
    audio_second_opinion: bool = False
    store_payloads: bool = True
    auto_tasks: bool = True
    proxy_url: str = ""
    ca_bundle: str = ""
    system_certs: bool = True
    prompts: dict[str, str] = field(default_factory=dict)     # F5.6 L2: tarefa -> instruções do usuário (só as personalizadas)

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
            fast = str(row.get("fast_model") or defaults[0])      # `model` é o de embeddings: nunca vira o rápido (F3.1 R3-09)
            strong = str(row.get("strong_model") or defaults[1] or fast)
        g = store.get
        return cls(pid, kind, fast, strong, params, max_parallel=int(g(SPECS["llm.max_parallel"])),
                   cost_alert_usd=float(g(SPECS["llm.cost_alert_usd"])), audio_second_opinion=bool(g(SPECS["llm.audio_second_opinion"])),
                   store_payloads=bool(g(SPECS["llm.store_payloads"])), auto_tasks=bool(g(SPECS["llm.auto_tasks"])),
                   proxy_url=str(g(SPECS["llm.proxy_url"])), ca_bundle=str(g(SPECS["llm.ca_bundle"])),
                   system_certs=bool(g(SPECS["llm.system_certs"])), prompts=prompt_settings.custom_prompts(store))

    @classmethod
    def from_json(cls, text: str | None) -> Self:
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        names = set(cls.__dataclass_fields__)          # o que não é campo (o `output_language` de antes da F5.3) fica de fora
        clean = {k: v for k, v in data.items() if k in names and k not in FORBIDDEN_KEYS}
        if isinstance(clean.get("params"), dict):
            clean["params"] = {k: v for k, v in clean["params"].items() if k not in FORBIDDEN_KEYS}
        if "prompts" in clean:
            clean["prompts"] = prompt_settings.clean(clean["prompts"])
        try:
            return cls(**clean)
        except TypeError:
            return cls()

    @classmethod
    def for_job(cls, store: Store, job: Mapping[str, Any], tasks: Iterable[object]) -> Self:
        """R-08: o snapshot de um job `llm`. O do claim vem sem instruções (`claim_json`); na primeira vez que o job roda,
        ganha as do usuário só das tarefas dele, gravadas no `config_json` (vazias também: o padrão fica congelado). A
        tentativa seguinte, a devolução à fila e o worker que morreu as releem; o "Tentar de novo" da fila zera o snapshot.
        Sem snapshot, devolve um não pronto: quem chama cai no `from_store`."""
        text = job.get("config_json")
        cfg = cls.from_json(text)
        if not cfg.ready or _frozen(text):
            return cfg
        wanted = {str(t) for t in tasks}
        cfg = replace(cfg, prompts={t: v for t, v in prompt_settings.custom_prompts(store).items() if t in wanted})
        store.jobs.set_config(int(job["id"]), cfg.to_json())
        return cfg

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    def claim_json(self) -> str:
        """O snapshot do claim (F3 item 15) sem as instruções: o runner congela só as das tarefas do job (`for_job`, R-08)."""
        data = asdict(self)
        del data["prompts"]
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    def model_for(self, strong: bool) -> str:
        return (self.strong_model or self.fast_model) if strong else (self.fast_model or self.strong_model)

    def prompt(self, task: LlmTask) -> str | None:
        """As instruções do usuário para a tarefa, ou None (as do app)."""
        text = self.prompts.get(str(task), "")
        return text if text.strip() else None

    def build(self, task: LlmTask, ctx: Any, *, reduce: bool = False) -> tuple[Message, ...]:
        """O prompt da tarefa com as instruções deste snapshot (A-26)."""
        return prompts.build(task, ctx, self.prompt(task), reduce=reduce)

    def version(self, task: LlmTask) -> str:
        """O `prompt_version` da chamada: `minutes/1.2`, ou `minutes/1.2+u3fa9c1` com instruções do usuário (A-28)."""
        return prompts.prompt_version(task, self.prompt(task))

    @property
    def ready(self) -> bool:
        return bool(self.provider_id and self.fast_model)


def _frozen(text: str | None) -> bool:
    """O `config_json` já tem as instruções congeladas pelo runner (a chave `prompts`, mesmo vazia)."""
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return False
    return isinstance(data, dict) and "prompts" in data


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
