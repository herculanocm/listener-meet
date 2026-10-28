"""As páginas "Ao vivo" e "Busca" das Configurações e o que elas fazem (F5.4 L3: saíram do `settings_min.py`, que passava
de 400 linhas; o comportamento é o da F4, da F5 L5 e da F5.1).

- Ao vivo (F4 item 10): as `live.*` e "Preparar modelos do ao vivo" (`ModelPreparer`, um download por vez no app inteiro),
  desabilitado durante a gravação.
- Busca (F5 L5, F5.1): as `rag.*`, "Preparar busca local (135 MB)" e o estado do índice com "Completar índice" e
  "Reindexar tudo…" (a estimativa e a confirmação antes). Cada página mostra o seu download (A-08): o do outro só desabilita
  o botão.
- F5.4 L3: cada download com o estado em selo (U-075: Prontos, Faltam, Baixando, Falhou); o estado do índice com o nome
  legível do modelo ("e5-small, local") e sem mandar para as Configurações quem já está nelas (U-036).

`SearchMixin` é parte do `SettingsDialog`: lê `store`, `platform`, `recording`, `preparer` e `on_launch` dele e pergunta
pelo `_ask` dele (o `ask_reindex` do `settings_min`, que os testes trocam)."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QPushButton

from ..config.defaults import LIVE_KEYS, RAG_KEYS
from ..llm import local_embed
from ..model import SettingScope
from .keys import note
from .live_prepare import LIVE, RAG, ModelPreparer, local_status_text, missing_models, models_status_text
from .settings_pages import DownloadRow, SettingsPage
from .theme import Tone

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..rag.index import ReindexEstimate
    from ..store import Store
    from .settings_editors import SettingEditor

IDLE: dict[str, Any] = {"state": "idle"}
OTHER_DOWNLOAD = "outro download em curso (um por vez)"
LIVE_SUBTITLE = ("A legenda e o resumo parcial durante a gravação. Vale na próxima gravação; a linha diz o que aplica "
                 "agora.")
SEARCH_SUBTITLE = ("A busca do Histórico e o chat sobre todas as reuniões. Aplica agora; o índice por palavra é grátis, e "
                   "os vetores só custam nos provedores pagos.")
PROVIDER_NAMES = {"gemini": "Gemini", "openai": "OpenAI", "anthropic": "Anthropic"}
LOCAL_NAME = "e5-small, local"


def vectors_note(est: ReindexEstimate) -> str:
    """O que acontece com os vetores: pedidos ao provedor (pago) ou calculados nesta máquina, sem custo (o local, A-05)."""
    if est.local:
        return "Os vetores que já existem ficam; só os que faltam são calculados nesta máquina, sem custo."
    return "Os vetores que já existem ficam; só os que faltam são pedidos ao provedor."


def model_name(key: str) -> str:
    """U-036: o modelo de embeddings pelo nome ("e5-small, local", "gemini-embedding-001, Gemini"), não pela chave interna
    (`local/multilingual-e5-small@384`)."""
    from ..rag.index import LOCAL, parse_model_key

    try:
        kind, model, _dim = parse_model_key(key)
    except ValueError:
        return key
    return LOCAL_NAME if kind == LOCAL else f"{model}, {PROVIDER_NAMES.get(kind, kind)}"


def here(text: str) -> str:
    """U-036: dentro das Configurações, o caminho até elas sai do motivo ("prepare em Configurações (Busca)" vira "prepare
    nesta página"; "(Configurações, LLM)" vira "(página LLM)")."""
    return re.sub(r"\(Configurações, ([^)]+)\)", r"(página \1)", text.replace("em Configurações (Busca)", "nesta página"))


def _thousands(n: int | None) -> str:
    return f"{int(n or 0):,}".replace(",", ".")


def index_status_text(store: Store) -> str:
    """"42 de 45 reuniões processadas no índice · 3.120 trechos", "2.980 vetores de e5-small, local · faltam 140" e se os
    embeddings estão prontos (com o motivo quando não), uma informação por linha."""
    from ..rag.index import embedder_status

    status = embedder_status(store)
    cov = store.rag.coverage(status.model_key)
    processed = len(store.rag.processed_meetings())
    text = f"{cov['meetings']} de {processed} reuniões processadas no índice · {_thousands(cov['chunks'])} trechos"
    if status.model_key is not None:
        text += (f"\n{_thousands(cov.get('vectors', 0))} vetores de {model_name(status.model_key)}"
                 + (f" · faltam {_thousands(cov.get('missing', 0))}" if cov.get("missing") else " · nenhum faltando"))
    text += ("\nembeddings prontos (busca por sentido ligada)" if status.ready
             else f"\nembeddings indisponíveis: {here(status.reason)}")
    return text


def download_badge(store: Store, what: str, state: str | None) -> tuple[str, Tone]:
    """U-075: o selo de um download: baixando, falhou ou o estado da pasta (prontos ou faltam; a busca local pronta, não
    baixada ou indisponível, como sem o extra instalado)."""
    if state == "running":
        return "Baixando", Tone.INFO
    if state == "error":
        return "Falhou", Tone.DANGER
    if what == LIVE:
        return ("Faltam", Tone.WARNING) if missing_models(store) else ("Prontos", Tone.SUCCESS)
    ready, reason = local_embed.status(store.get_setting("pipeline.models_dir"))
    if ready:
        return "Pronta", Tone.SUCCESS
    return ("Não baixada" if reason == local_embed.NOT_DOWNLOADED else "Indisponível"), Tone.WARNING


class SearchMixin:
    """As páginas "Ao vivo" e "Busca" do `SettingsDialog`, com os downloads e o índice."""
    store: Store
    platform: Platform
    recording: bool
    preparer: ModelPreparer
    on_launch: Callable[[bool], Any] | None
    reindexed: int

    def _ask(self, text: str, yes: str, no: str) -> bool:
        raise NotImplementedError

    def _add(self, page: SettingsPage, form: QFormLayout, key: str, editor: SettingEditor | None = None,
             badge: tuple[str, Tone] | None = None, *, compact: bool = False) -> SettingEditor:
        raise NotImplementedError

    # ---------------------------------------------------------------- páginas
    def _live_page(self) -> SettingsPage:
        page = SettingsPage("Ao vivo", LIVE_SUBTITLE, SettingScope.NEXT_RECORDING)
        form = page.form()
        for key in LIVE_KEYS:
            self._add(page, form, key)
        self.live_row = DownloadRow("Preparar modelos do ao vivo", "Baixa os modelos small e base da legenda ao vivo para a "
                                    "pasta de modelos (fora da gravação).")
        self.prepare_btn, self.prepare_bar, self.prepare_status = self.live_row.btn, self.live_row.bar, self.live_row.status
        self.prepare_btn.clicked.connect(self.prepare_models)
        form.addRow("Modelos", self.live_row)
        page.finish()
        return page

    def _search_page(self) -> SettingsPage:
        page = SettingsPage("Busca", SEARCH_SUBTITLE, SettingScope.NOW)
        form = page.form()
        for key in RAG_KEYS:
            self._add(page, form, key)
        self.local_row = DownloadRow(f"Preparar busca local ({local_embed.SIZE_MB} MB)", "Baixa para a pasta de modelos o "
                                     "e5-small, a busca por sentido sem custo e sem rede (escolha Local em \"Ativo para "
                                     "embeddings\", na página LLM); fora da gravação.")
        self.local_btn, self.local_bar, self.local_status = self.local_row.btn, self.local_row.bar, self.local_row.status
        self.local_btn.clicked.connect(self.prepare_local)
        page.card("Busca local").content.addWidget(self.local_row)
        self.index_status = note("")
        self.complete_btn = QPushButton("Completar índice")
        self.complete_btn.setToolTip("Põe na fila as reuniões com trecho sem vetor do modelo ativo (e as ainda sem índice)")
        self.complete_btn.clicked.connect(self.complete_index)
        self.reindex_btn = QPushButton("Reindexar tudo…")
        self.reindex_btn.setToolTip("Refaz o índice de todas as reuniões processadas e pede os vetores que faltam (mostra a estimativa "
                                    "antes)")
        self.reindex_btn.clicked.connect(self.reindex_all)
        buttons = QHBoxLayout()
        buttons.addWidget(self.complete_btn)
        buttons.addWidget(self.reindex_btn)
        buttons.addStretch(1)
        index = page.card("Índice")
        index.content.addWidget(self.index_status)
        index.content.addLayout(buttons)
        page.finish()
        return page

    # ---------------------------------------------------------------- índice (F5 L5)
    def refresh_index_status(self) -> None:
        """O estado do índice e a estimativa do que falta (o botão "Completar" só com algo a completar)."""
        from ..rag.index import reindex_estimate

        try:
            text = index_status_text(self.store)
            est = reindex_estimate(self.store)
        except Exception as e:  # banco ocupado: a página fica sem o estado, as configurações seguem
            self.index_status.setText(f"estado do índice indisponível agora: {e}")
            return
        missing = est.chunks if est.ready else 0
        if missing:
            text += f"\ncompletar: {est.text()}"
        self.index_status.setText(text)
        self.complete_btn.setEnabled(bool(missing or est.without_chunks))
        self.reindex_btn.setEnabled(est.model_key is not None or bool(est.without_chunks))

    def _launch_light(self) -> None:
        if self.on_launch is not None:
            self.on_launch(True)

    def complete_index(self) -> int:
        """"Completar índice": as reuniões com trecho sem vetor do modelo ativo (e as sem trechos) entram na fila. Com os
        embeddings prontos há vetores a pedir (com custo): a estimativa do "Reindexar tudo…" e a confirmação antes; sem eles,
        só trechos e o índice por palavra (sem custo), na hora. Não confirma o modelo para a varredura (é do "reindexar")."""
        from ..rag.index import complete_index, reindex_estimate

        est = reindex_estimate(self.store)
        if est.ready and (est.chunks or est.without_chunks):
            text = f"Completar o índice da busca?\n\n{est.text()}.\n\n{vectors_note(est)}"
            if not self._ask(text, "Completar", "Cancelar"):
                return 0
        n = complete_index(self.store)
        self.reindexed += n
        if n:
            self._launch_light()
        self.refresh_index_status()
        self.index_status.setText(self.index_status.text() + f"\n{n} reunião(ões) na fila do índice.")
        return n

    def reindex_all(self) -> int:
        """"Reindexar tudo…": a estimativa e a confirmação antes; confirma o modelo ativo para a varredura completar as reuniões
        antigas (`rag.index.reindex_all`)."""
        from ..rag.index import reindex_all, reindex_estimate

        est = reindex_estimate(self.store)
        if not est.ready and est.model_key is not None:
            extra = f"\n\nOs embeddings não estão prontos ({here(est.reason)}): só o índice por palavra é refeito agora."
        else:
            extra = ""
        text = f"Refazer o índice de todas as reuniões processadas?\n\n{est.text()}.\n\n{vectors_note(est)}{extra}"
        if not self._ask(text, "Reindexar", "Cancelar"):
            return 0
        n = reindex_all(self.store)
        self.reindexed += n
        if n:
            self._launch_light()
        self.refresh_index_status()
        return n

    def offer_reindex(self, before: str | None) -> int:
        """Comportamento 4: o modelo de embeddings ativo mudou (outro provedor ou outro modelo) e está pronto: a estimativa com
        "Reindexar agora" e "Depois"; os vetores do modelo anterior ficam (voltar não custa)."""
        from ..rag.index import embedder_status, is_local, reindex_all, reindex_estimate

        status = embedder_status(self.store)
        if not status.ready or status.model_key is None or status.model_key == before:
            return 0
        est = reindex_estimate(self.store)
        name = "a busca local (e5-small, sem custo)" if is_local(status.model_key) else status.model_key
        text = (f"O modelo da busca por sentido agora é {name}.\n\nReindexar as reuniões neste modelo: {est.text()}."
                "\n\nOs vetores do modelo anterior ficam guardados: voltar para ele não custa nada. A busca por palavra segue "
                "funcionando enquanto isso.")
        if not self._ask(text, "Reindexar agora", "Depois"):
            return 0
        n = reindex_all(self.store)
        self.reindexed += n
        if n:
            self._launch_light()
        return n

    # ---------------------------------------------------------------- downloads (F4, F5.1)
    def prepare_models(self) -> None:
        if self.recording or self.preparer.running:
            return
        self.preparer.start(self.store, self.platform.spawn_child)

    def prepare_local(self) -> None:
        """"Preparar busca local (135 MB)" (F5.1 item 3): o e5-small pelo mesmo preparador, um download por vez."""
        if self.recording or self.preparer.running:
            return
        self.preparer.start(self.store, self.platform.spawn_child, RAG)

    def _on_prepare_changed(self, state: Any) -> None:
        """Cada página mostra o seu download (A-08): o do outro só desabilita o botão (um por vez no app inteiro)."""
        st = dict(state or {})
        what = str(st.get("what") or LIVE)
        running = st.get("state") == "running"
        self._show_prepare(self.live_row, LIVE, st if what == LIVE else IDLE, running and what != LIVE, models_status_text)
        self._show_prepare(self.local_row, RAG, st if what == RAG else IDLE, running and what != RAG, local_status_text)
        if what == RAG and st.get("state") == "done":
            self.refresh_index_status()                  # a busca local ficou pronta: o estado do índice muda junto

    def _show_prepare(self, row: DownloadRow, what: str, st: dict[str, Any], other: bool,
                      status_text: Callable[[Store], str]) -> None:
        running = st.get("state") == "running"
        row.btn.setEnabled(not running and not other and not self.recording)
        row.bar.setVisible(running or st.get("state") == "done")
        row.bar.setValue(round(float(st.get("fraction") or 0.0) * 100))
        if running:
            model, status = str(st.get("model") or ""), str(st.get("status") or "iniciando…")
            text = f"{model}: {status}…" if model and status in ("baixando", "conferindo") else (f"{model}: {status}" if model else status)
        elif st.get("state") == "error":
            text = f"falhou: {st.get('message') or 'erro'} · {status_text(self.store)}"
        else:
            text = status_text(self.store)
        if self.recording and not running:
            text += " · prepare depois de parar a gravação"
            row.btn.setToolTip("Desabilitado durante a gravação: o download disputaria a CPU e a rede com a reunião.")
        elif other:
            text += f" · {OTHER_DOWNLOAD}"
        row.status.setText(here(text))
        row.set_badge(*download_badge(self.store, what, str(st.get("state") or "")))


__all__ = ["IDLE", "OTHER_DOWNLOAD", "SearchMixin", "download_badge", "here", "index_status_text", "model_name", "vectors_note"]
