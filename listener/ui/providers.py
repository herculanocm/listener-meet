"""Sub-abas de provedor nas Configurações (CP8, CP10, F3 item 12): Gemini, GPT e Claude, cada uma com a chave em texto aberto,
modelo rápido e forte escolhidos de `list_models()` (cache em `providers.models_json`), parâmetros (JSON), "testar conexão"
numa `Task` (latência, modelo, erro traduzido), "testar pelo worker" (job `llm` de tarefa `test`), preços editáveis
(`llm_prices`) e o gasto do mês; acima das abas, o provedor ativo para texto e para embeddings (a Anthropic não oferece
embeddings). Aviso factual: o texto da reunião sai da máquina para o provedor escolhido (CP1). Nada aqui importa SDK.
F5.1 (A-01): "Local (e5-small, sem custo)" sempre no "Ativo para embeddings" (sem sub-aba: não tem chave nem modelo a
escolher); a linha `local` nasce ao salvar com ele escolhido (`ensure_local`), e cada papel é gravado num `UPDATE` só.
U-037 (D-053): a chave, o "Testar conexão" e o estado da chave (`key_status`) de cada sub-aba ficam no grupo "Chaves de
acesso", o primeiro das Configurações (`ui/keys.py`); a sub-aba diz onde e guarda o resto."""
from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from ..llm.adapters_common import friendly_error
from ..llm.aio import run_async
from ..llm.costs import fmt_usd
from ..llm.models_table import DEFAULT_EMBED_MODELS, DEFAULT_MODELS, PROVIDER_CAPABILITIES
from ..llm.registry import NetworkConfig, load_provider
from ..llm.types import Capability, ConnectionTest, ModelInfo, ProviderKind
from ..log import get_logger
from ..store.core import _now
from ..store.providers import LLM_KINDS, LOCAL_ID
from .tasks import run_task

if TYPE_CHECKING:
    from ..store import Store
    from ..store.providers import ProviderRow

log = get_logger("ui.providers")

TITLES = {"gemini": "Gemini (Google)", "openai": "GPT (OpenAI)", "anthropic": "Claude (Anthropic)"}
LOCAL_TITLE = "Local (e5-small, sem custo)"
LOCAL_TIP = ("Busca por sentido calculada nesta máquina, sem chave, sem rede e sem custo; prepare antes em Configurações, "
             "grupo Busca (135 MB)")
PRICE_COLUMNS = ("Modelo (id ou prefixo)", "Entrada US$/M", "Saída US$/M", "Cache US$/M", "Áudio US$/M")
KEY_ELSEWHERE = "no grupo Chaves de acesso, o primeiro das Configurações (com o Testar conexão)"
NO_KEY = "falta a chave"
KEY_EDITED = "chave nova: Testar confere antes de salvar"
SEND_WARNING = "O texto da reunião (transcrição, marcadores e nomes) é enviado ao provedor escolhido. Áudio só sai com a opção ligada."


def test_connection(kind: str, api_key: str, model: str, net: NetworkConfig) -> ConnectionTest:
    """Roda numa `Task`: cliente criado e fechado dentro do `run_async()` (contrato "Decoradores e registro")."""
    async def go() -> ConnectionTest:
        provider = load_provider(kind, api_key, net)
        try:
            return await provider.test_connection(model)
        finally:
            await provider.aclose()
    return run_async(go())


def list_models(kind: str, api_key: str, net: NetworkConfig) -> tuple[ModelInfo, ...]:
    async def go() -> tuple[ModelInfo, ...]:
        provider = load_provider(kind, api_key, net)
        try:
            return await provider.list_models()
        finally:
            await provider.aclose()
    return run_async(go())


def models_cache(infos: tuple[ModelInfo, ...]) -> list[dict[str, Any]]:
    return [{"id": m.id, "display_name": m.display_name, "input_limit": m.input_limit, "output_limit": m.output_limit,
             "capabilities": sorted(str(c) for c in m.capabilities)} for m in infos]


class ProviderTab(QWidget):
    def __init__(self, store: Store, row: ProviderRow, on_launch: Callable[[bool], Any] | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.pid = str(row["id"])
        self.kind = ProviderKind(str(row["kind"]))
        self.on_launch = on_launch
        self.last_test: ConnectionTest | None = None
        self.last_models: tuple[ModelInfo, ...] = ()
        self.key = QLineEdit(str(row.get("api_key") or ""))
        self.key.setPlaceholderText("chave de API (fica no banco do app, em texto aberto; CP10)")
        self.fast = QComboBox()
        self.strong = QComboBox()
        for combo in (self.fast, self.strong):
            combo.setEditable(True)
        defaults = DEFAULT_MODELS[self.kind]
        self._fill_models([m["id"] for m in store.providers.models(self.pid)] or list(defaults))
        self.fast.setCurrentText(str(row.get("fast_model") or defaults[0]))
        self.strong.setCurrentText(str(row.get("strong_model") or defaults[1]))
        self.embed_model = QLineEdit(str(row.get("model") or DEFAULT_EMBED_MODELS.get(self.kind, "")))
        self.embed_model.setEnabled(Capability.EMBEDDINGS in PROVIDER_CAPABILITIES[self.kind])
        self.params = QLineEdit(str(row.get("params") or ""))
        self.params.setPlaceholderText('JSON opcional, ex.: {"top_p": 0.9}')
        self.btn_models = QPushButton("Listar modelos")
        self.btn_test = QPushButton("Testar conexão")
        self.btn_worker = QPushButton("Testar pelo worker")
        self.btn_models.clicked.connect(self.refresh_models)
        self.btn_test.clicked.connect(self.run_test)
        self.btn_worker.clicked.connect(self.test_via_worker)
        self.status = QLabel(self._tested_text(row))
        self.status.setWordWrap(True)
        self.saved_key = str(row.get("api_key") or "").strip()
        self.saved_state = self._tested_text(row) if self.saved_key else NO_KEY
        self.key_status = QLabel(self.saved_state)                                       # posto junto da chave (U-037)
        self.key_status.setTextFormat(Qt.TextFormat.PlainText)
        self.key_status.setWordWrap(True)
        self.key_status.setStyleSheet("color: #888; font-size: 11px;")
        self.key.textChanged.connect(self._key_edited)
        self.prices = QTableWidget(0, len(PRICE_COLUMNS))
        self.prices.setHorizontalHeaderLabels(list(PRICE_COLUMNS))
        self.prices.horizontalHeader().setStretchLastSection(True)
        self.prices.setMaximumHeight(160)
        self._fill_prices()
        self.month = QLabel("")
        self.refresh_month()
        warn = QLabel(SEND_WARNING)
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #e67e22;")
        form = QFormLayout()
        where = QLabel(KEY_ELSEWHERE)
        where.setWordWrap(True)
        form.addRow("Chave", where)                      # o campo e o Testar conexão estão nas Chaves de acesso (U-037)
        form.addRow("Modelo rápido", self.fast)
        form.addRow("Modelo forte", self.strong)
        form.addRow("Modelo de embeddings", self.embed_model)
        form.addRow("Parâmetros", self.params)
        buttons = QHBoxLayout()
        for b in (self.btn_models, self.btn_worker):
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(buttons)
        lay.addWidget(self.status)
        lay.addWidget(QLabel("Preços por milhão de tokens (sementes do projeto; confira contra a fatura):"))
        lay.addWidget(self.prices)
        lay.addWidget(self.month)
        lay.addWidget(warn)

    # ---------------------------------------------------------------- leitura
    @staticmethod
    def _tested_text(row: ProviderRow) -> str:
        if row.get("tested_at"):
            return f"último teste em {str(row['tested_at'])[:16].replace('T', ' ')}: {row.get('latency_ms') or '?'} ms"
        return "ainda não testado"

    def _fill_models(self, ids: list[str]) -> None:
        for combo in (self.fast, self.strong):
            current = combo.currentText()
            combo.clear()
            combo.addItems(ids)
            if current:
                combo.setCurrentText(current)

    def _fill_prices(self) -> None:
        rows = self.store.providers.list_prices(self.pid)
        self.prices.setRowCount(len(rows) + 1)                      # última linha vazia para um modelo novo
        for r, p in enumerate(rows):
            for c, value in enumerate((p["model"], p["input_usd_m"], p["output_usd_m"], p["cached_usd_m"], p["audio_usd_m"])):
                self.prices.setItem(r, c, QTableWidgetItem(str(value)))
        for c in range(len(PRICE_COLUMNS)):
            self.prices.setItem(len(rows), c, QTableWidgetItem(""))

    def refresh_month(self) -> None:
        self.month.setText(f"Gasto do mês neste provedor: {fmt_usd(self.store.llm_calls.month_total(provider=self.pid))} · "
                           f"total do mês: {fmt_usd(self.store.llm_calls.month_total())}")

    def _net(self) -> NetworkConfig:
        return NetworkConfig.from_store(self.store)

    # ---------------------------------------------------------------- ações
    def _say(self, text: str) -> None:
        """O resultado do teste na sub-aba e junto da chave (U-037)."""
        self.status.setText(text)
        self.key_status.setText(text)

    def _key_edited(self, text: str) -> None:
        key = text.strip()
        self.key_status.setText(NO_KEY if not key else self.saved_state if key == self.saved_key else KEY_EDITED)

    def run_test(self) -> None:
        key = self.key.text().strip()
        if not key:
            self._say("informe a chave antes de testar")
            return
        self.btn_test.setEnabled(False)
        self._say("testando…")
        kind, model, net = str(self.kind), self.fast.currentText().strip(), self._net()
        run_task(functools.partial(test_connection, kind, key, model, net), self.store, self._on_test)

    def _on_test(self, result: Any) -> None:
        self.btn_test.setEnabled(True)
        if isinstance(result, BaseException):
            self._say(f"falhou: {friendly_error(str(result))}")
            return
        self.last_test = result
        if result.ok:
            self._say(f"ok: {result.model} respondeu em {result.latency_ms} ms")
            self.store.providers.update_provider(self.pid, tested_at=_now(), latency_ms=int(result.latency_ms))
        else:
            self._say(f"falhou: {friendly_error(result.error)}")

    def refresh_models(self) -> None:
        key = self.key.text().strip()
        if not key:
            self.status.setText("informe a chave antes de listar os modelos")
            return
        self.btn_models.setEnabled(False)
        run_task(functools.partial(list_models, str(self.kind), key, self._net()), self.store, self._on_models)

    def _on_models(self, result: Any) -> None:
        self.btn_models.setEnabled(True)
        if isinstance(result, BaseException):
            self.status.setText(f"modelos: {friendly_error(str(result))}")
            return
        self.last_models = tuple(result)
        self.store.providers.update_provider(self.pid, models_json=models_cache(self.last_models))
        self._fill_models([m.id for m in self.last_models])
        self.status.setText(f"{len(self.last_models)} modelo(s) listados")

    def test_via_worker(self) -> None:
        from ..pipeline.llm_steps import enqueue_test_job

        self.save()
        job_id = enqueue_test_job(self.store)
        if job_id is None:
            self.status.setText("testar pelo worker precisa de uma reunião gravada (a fila é por reunião)")
            return
        if self.on_launch is not None:
            self.on_launch(True)
        self.status.setText(f"job {job_id} de teste na fila do worker leve; o resultado aparece na Fila de processamento")

    # ---------------------------------------------------------------- gravação
    def _price_rows(self) -> list[tuple[str, list[float]]]:
        """Linhas preenchidas da tabela de preços; `ValueError` com o texto para o usuário no primeiro preço inválido."""
        out: list[tuple[str, list[float]]] = []
        for r in range(self.prices.rowCount()):
            cells = [self.prices.item(r, c) for c in range(len(PRICE_COLUMNS))]
            model = (cells[0].text() if cells[0] else "").strip()
            if not model:
                continue
            values = []
            for cell in cells[1:]:
                text = (cell.text() if cell else "").strip()
                try:
                    value = float(text.replace(",", ".") or 0)
                except ValueError as e:
                    raise ValueError(f"{TITLES.get(self.pid, self.pid)}: preço inválido para {model}: {text}") from e
                if value < 0:
                    raise ValueError(f"{TITLES.get(self.pid, self.pid)}: preço negativo para {model}: {text}")
                values.append(value)
            out.append((model, values))
        return out

    def validation_error(self) -> str | None:
        """Primeiro erro da sub-aba antes de gravar qualquer coisa (F3.1 R3-09, R3-12): parâmetros, modelo rápido e preços."""
        name = TITLES.get(self.pid, self.pid)
        params = self.params.text().strip()
        if params:
            try:
                json.loads(params)
            except ValueError as e:
                return f"{name}: parâmetros não são JSON ({e})"
        if not self.fast.currentText().strip():
            return f"{name}: escolha o modelo rápido (é o das tarefas de todo dia)"
        try:
            self._price_rows()
        except ValueError as e:
            return str(e)
        return None

    def save(self) -> None:
        err = self.validation_error()
        if err:
            raise ValueError(err)
        params = self.params.text().strip()
        self.store.providers.update_provider(self.pid, api_key=self.key.text().strip() or None,
                                             fast_model=self.fast.currentText().strip(),
                                             strong_model=self.strong.currentText().strip() or None, params=params or None,
                                             model=self.embed_model.text().strip() or None)
        for model, values in self._price_rows():
            self.store.providers.set_price(self.pid, model, *values)


class ProviderTabs(QWidget):
    def __init__(self, store: Store, on_launch: Callable[[bool], Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        rows = store.providers.list_llm()
        self.active_llm = QComboBox()
        self.active_embed = QComboBox()
        self.active_llm.addItem("nenhum", "")
        self.active_embed.addItem("nenhum", "")
        for row in rows:
            self.active_llm.addItem(TITLES.get(str(row["id"]), str(row["id"])), str(row["id"]))
            if Capability.EMBEDDINGS in PROVIDER_CAPABILITIES[ProviderKind(str(row["kind"]))]:
                self.active_embed.addItem(TITLES.get(str(row["id"]), str(row["id"])), str(row["id"]))
        self.active_embed.addItem(LOCAL_TITLE, LOCAL_ID)                # A-01: sempre no combo, sem precisar da linha
        self.active_embed.setItemData(self.active_embed.count() - 1, LOCAL_TIP, Qt.ItemDataRole.ToolTipRole)
        current_llm = store.providers.active_provider("llm")
        current_embed = store.providers.active_provider("embed")
        self.active_llm.setCurrentIndex(max(0, self.active_llm.findData(str(current_llm["id"])) if current_llm else 0))
        self.active_embed.setCurrentIndex(max(0, self.active_embed.findData(str(current_embed["id"])) if current_embed else 0))
        self.tabs = QTabWidget()
        self.tab_by_id: dict[str, ProviderTab] = {}
        for row in rows:
            if str(row["id"]) not in LLM_KINDS:
                continue
            tab = ProviderTab(store, row, on_launch)
            self.tab_by_id[str(row["id"])] = tab
            self.tabs.addTab(tab, TITLES.get(str(row["id"]), str(row["id"])))
        form = QFormLayout()
        form.addRow("Ativo para texto", self.active_llm)
        form.addRow("Ativo para embeddings", self.active_embed)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.tabs)

    def validation_error(self) -> str | None:
        for tab in self.tab_by_id.values():
            err = tab.validation_error()
            if err:
                return err
        return None

    def save(self) -> None:
        """As sub-abas e os ativos; cada papel num `UPDATE` só, que escolhe o provedor ou zera o papel ("nenhum"). A linha
        `local` só nasce aqui, no caminho de escrita, quando ele é o escolhido (A-01)."""
        for tab in self.tab_by_id.values():
            tab.save()
        llm_id = str(self.active_llm.currentData() or "")
        embed_id = str(self.active_embed.currentData() or "")
        if embed_id == LOCAL_ID:
            self.store.providers.ensure_local()
        self.store.providers.set_role("llm", llm_id or None)
        self.store.providers.set_role("embed", embed_id or None)
