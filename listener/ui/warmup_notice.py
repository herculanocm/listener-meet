"""Os avisos do "Antes de gravar" (F5.11: U-111, U-112, A-31), fora do `ui/warmup.py`, que passava de 400 linhas (A-35).

- `Bars`: as faixas do diálogo (o aviso do microfone, o resultado do eco e "Falta configurar"), cada uma montada só na
  primeira vez que tem o que dizer, no lugar guardado (F5.4 L2: montar à toa deixava a abertura mais lenta).
- `SetupNotice`: a faixa "Falta configurar" do rodapé, ao lado do Gravar, um aviso só com o que falta (o token do Hugging
  Face, a chave da IA e os modelos da legenda ao vivo) e a ação de cada item, por link (nenhum texto do usuário vira HTML,
  R7-07): "Configurar" abre as Configurações e relê tudo na volta; "Preparar agora" baixa os modelos pelo preparador único
  do app (`preparer()`, o mesmo das Configurações), com o tamanho, o progresso e "Cancelar", e a faixa diz antes que "Gravar"
  interrompe o download (o controlador o interrompe ao começar a gravar). Terminado o download, a "Legenda ao vivo" volta
  ao padrão da gravação (`LiveBoxes.models_ready`). Os modelos só entram na faixa quando a legenda é pedida (o padrão das
  Configurações, o preset ou a caixa)."""
from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QBoxLayout

from .components import InfoBar
from .keys import missing_keys
from .live_prepare import LIVE, missing_models, missing_size_mb, preparer

if TYPE_CHECKING:
    from ..store import Store
    from .live_prepare import Spawn
    from .warmup_live import LiveBoxes

SETUP = "setup"                                   # a faixa do rodapé
LINKED_BARS = frozenset({SETUP})                  # as faixas com links
HEAD = "Falta configurar"
CONFIGURE, PREPARE, CANCEL = '<a href="keys">Configurar</a>', '<a href="prepare">Preparar agora</a>', '<a href="cancel">Cancelar</a>'
CANCELED = "download cancelado"
RECORD_STOPS = "“Gravar” interrompe o download."
TOKEN_LINE = f"Token do Hugging Face: sem ele, o processamento não sai. {CONFIGURE}"
KEY_LINE = f"Chave da IA: sem ela, não sai ata, resumo nem título. {CONFIGURE}"
MODELS = "Modelos da legenda ao vivo"


def models_line(models: list[str], state: Mapping[str, Any]) -> str:
    """O item dos modelos: o tamanho e "Preparar agora"; baixando, o progresso e "Cancelar"; o erro com o motivo."""
    size = f"{MODELS} ({missing_size_mb(models)} MB)"
    st = str(state.get("state") or "")
    if st == "running" and state.get("what", LIVE) == LIVE:
        model, pct = html.escape(str(state.get("model") or "")), round(float(state.get("fraction") or 0.0) * 100)
        return f"{MODELS}: baixando{' ' + model if model else ''} ({pct} %). {CANCEL} · {RECORD_STOPS}"
    if st == "running":
        return f"{size}: espere o download da busca local terminar."
    if st == "error" and state.get("message") != CANCELED:
        return f"{size}: o download falhou ({html.escape(str(state.get('message') or 'erro'))}). {PREPARE} · {RECORD_STOPS}"
    return f"{size}. {PREPARE} · {RECORD_STOPS}"


def setup_text(missing: tuple[str, ...], models: list[str], state: Mapping[str, Any]) -> str:
    """A faixa inteira (HTML fixo com os links), vazia quando nada falta (U-112)."""
    lines = [line for key, line in (("hf", TOKEN_LINE), ("llm", KEY_LINE)) if key in missing]
    if models:
        lines.append(models_line(models, state))
    return f"<b>{HEAD}</b><br>{'<br>'.join(lines)}" if lines else ""


class Bars(QObject):
    """As faixas do diálogo: `slots[nome] = (layout, posição, esticar)`, montadas na primeira vez (`bar`); o clique num link
    de uma faixa sai pelo sinal `link` (R-06: nenhum método do diálogo guardado aqui)."""
    link = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bars: dict[str, InfoBar] = {}
        self.slots: dict[str, tuple[QBoxLayout, int, int]] = {}

    def bar(self, slot: str) -> InfoBar:
        bar = self.bars.get(slot)
        if bar is None:
            bar = InfoBar("warning")
            bar.hide()
            if slot in LINKED_BARS:
                bar.label.setTextFormat(Qt.TextFormat.RichText)
                bar.label.linkActivated.connect(self.link)
            layout, index, stretch = self.slots[slot]
            layout.insertWidget(index, bar, stretch)
            self.bars[slot] = bar
        return bar

    def show(self, slot: str, text: str, kind: str = "warning") -> None:
        """O texto na faixa e a faixa à vista; sem texto, escondida (e nem montada, se ainda não foi)."""
        if not text and slot not in self.bars:
            return
        bar = self.bar(slot)
        bar.set_kind(kind)
        bar.label.setText(text)
        bar.label.setVisible(bool(text))
        bar.setVisible(bool(text))


class SetupNotice(QObject):
    """A faixa "Falta configurar" e as ações dela; `refresh()` relê o que falta (abertura, volta das Configurações, download)."""

    def __init__(self, store: Store, live: LiveBoxes, bars: Bars, on_settings: Callable[[], Any] | None, spawn: Spawn,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store, self.live, self.bars, self.on_settings, self.spawn = store, live, bars, on_settings, spawn
        self.missing: tuple[str, ...] = ()
        self.preparer = preparer()
        self.preparer.changed.connect(self._on_models)
        self._connected = True

    @property
    def label(self) -> Any:
        return self.bars.bar(SETUP).label

    def refresh(self) -> None:
        self.missing = missing_keys(self.store)
        models = missing_models(self.store) if self.live.wants_live() else []
        self.bars.show(SETUP, setup_text(self.missing, models, self.preparer.state))

    def on_link(self, link: str) -> None:
        if link == "prepare":
            self.preparer.start(self.store, self.spawn)         # um download por vez no app: com outro rodando, nada
        elif link == "cancel":
            self.preparer.cancel(CANCELED)
        else:
            if self.on_settings is not None:
                self.on_settings()                              # as Configurações modais: na volta, tudo de novo
            self.live.refresh_key()
        self.refresh()

    def _on_models(self, state: object) -> None:
        if isinstance(state, dict) and state.get("state") == "done" and state.get("what", LIVE) == LIVE:
            self.live.models_ready()
        self.refresh()

    def release(self) -> None:
        """Fechar o diálogo (roda mais de uma vez: aceitar e depois fechar); o download segue."""
        if self._connected:
            self._connected = False
            with suppress(RuntimeError, TypeError):
                self.preparer.changed.disconnect(self._on_models)


__all__ = ["CANCELED", "HEAD", "KEY_LINE", "LINKED_BARS", "MODELS", "RECORD_STOPS", "SETUP", "TOKEN_LINE", "Bars", "SetupNotice",
           "models_line", "setup_text"]
