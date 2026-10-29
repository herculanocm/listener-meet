"""O ao vivo no "Antes de gravar" (F4, CP6; F5.6 L1b: U-091, A-21 a A-23, P-06), saído do `ui/warmup.py`, que estava acima
de 400 linhas: as caixas "Legenda ao vivo", "Nomes ao vivo" e "Resumo parcial", numa linha das "Opções", e a lógica delas.

- "Legenda ao vivo" e "Nomes ao vivo" (a segunda só com a primeira): o valor do preset quando não nulo, senão `live.enabled`
  e `live.names` (`set_values`); "Salvar como preset" grava as duas. Modelos ausentes viram o aviso discreto do diálogo
  (`show_note`, a faixa com o link para as Configurações), sem bloquear a gravação; o download que termina com o diálogo
  aberto o apaga.
- "Resumo parcial" (U-091): o resumo da reunião pela LLM durante a gravação, no intervalo das Configurações. Vem de
  `live.partial` (desmarcada no padrão, P-06), fora do preset (um preset não a muda); só ligada com a legenda ligada (sem
  legenda não há o que resumir) e com a chave da LLM (revisão do L1b, R-09: sem chave, apagada com a dica das outras caixas
  sem chave; relida na volta das Configurações). O valor vai em `fields["live_partial"]`, que o controlador fixa na gravação
  (A-21); o "Atualizar resumo agora" da janela ao vivo segue com ela desmarcada.
Sinais ligados a métodos (R-06)."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QCheckBox, QHBoxLayout

from ..config.defaults import SPECS
from .live_prepare import missing_models, preparer
from .llm_ask import NO_KEY_TIP, has_key
from .theme import METRICS

if TYPE_CHECKING:
    from ..store import Store

LIVE_TIP = "Legenda de quem fala durante a gravação, com 5 a 10 s de atraso (Configurações, grupo Ao vivo)."
NAMES_TIP = "Nome e cargo de quem tem perfil de voz na legenda (carrega o modelo de voz: mais memória)."
PARTIAL_TIP = ("Resumo da reunião pela LLM durante a gravação, no intervalo das Configurações (Ao vivo); tem custo. Desmarcada, "
               "\"Atualizar resumo agora\" na janela ao vivo continua.")
PARTIAL_OFF_TIP = "O resumo parcial sai das legendas: ligue a legenda ao vivo."


class LiveBoxes(QObject):
    """As três caixas do ao vivo e o aviso dos modelos que faltam (`show_note(texto)`, vazio esconde)."""

    def __init__(self, store: Store, show_note: Callable[[str], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.show_note = show_note
        self.live_check = QCheckBox("Legenda ao vivo")
        self.live_check.setToolTip(LIVE_TIP)
        self.names_check = QCheckBox("Nomes ao vivo")
        self.names_check.setToolTip(NAMES_TIP)
        self.partial_check = QCheckBox("Resumo parcial")
        self.partial_check.setChecked(bool(store.get(SPECS["live.partial"])))       # P-06: o padrão das Configurações
        self.key = has_key(store)                                                    # R-09: sem chave, o parcial não sai
        self.live_check.toggled.connect(self.toggled)
        self._preparer = preparer()                       # download pelo link das Configurações: o aviso some quando terminar
        self._preparer.changed.connect(self.on_models_changed)
        self._connected = True

    def row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(METRICS.card_padding)
        for box in (self.live_check, self.names_check, self.partial_check):
            row.addWidget(box)
        row.addStretch(1)
        return row

    def set_values(self, enabled: int | bool | None, names: int | bool | None) -> None:
        """Legenda e nomes: o valor do preset quando não nulo, senão o padrão das Configurações (o parcial não muda)."""
        self.live_check.setChecked(bool(self.store.get(SPECS["live.enabled"]) if enabled is None else enabled))
        self.names_check.setChecked(bool(self.store.get(SPECS["live.names"]) if names is None else names))
        self.toggled(self.live_check.isChecked())

    def toggled(self, on: bool) -> None:
        self.names_check.setEnabled(on)
        self._partial_state()
        self.update_note()

    def refresh_key(self) -> None:
        """A chave pode ter chegado pelas Configurações com o diálogo aberto (o link do aviso das chaves)."""
        self.key = has_key(self.store)
        self._partial_state()

    def _partial_state(self) -> None:
        on = self.live_check.isChecked()
        self.partial_check.setEnabled(on and self.key)
        self.partial_check.setToolTip(NO_KEY_TIP if not self.key else PARTIAL_TIP if on else PARTIAL_OFF_TIP)

    def update_note(self) -> None:
        """Aviso discreto quando faltam os modelos do ao vivo (a gravação segue sem ele, com um aviso do controlador)."""
        missing = missing_models(self.store) if self.live_check.isChecked() else []
        self.show_note(f'modelos ausentes ({", ".join(missing)}): <a href="settings">prepare nas Configurações, Ao vivo</a>'
                       if missing else "")

    def on_models_changed(self, state: Any) -> None:
        if isinstance(state, dict) and state.get("state") in ("done", "error"):
            self.update_note()

    def disconnect_models(self) -> None:
        """Fechar o diálogo (roda mais de uma vez: aceitar e depois fechar)."""
        if self._connected:
            self._connected = False
            with suppress(RuntimeError, TypeError):
                self._preparer.changed.disconnect(self.on_models_changed)

    def fields(self) -> dict[str, bool]:
        """O que vai para o controlador (CP6): a legenda, os nomes e o parcial (só com a legenda e a chave, A-21, R-09)."""
        live = self.live_check.isChecked()
        return {"live_enabled": live, "live_names": self.names_check.isChecked(),
                "live_partial": live and self.key and self.partial_check.isChecked()}


__all__ = ["PARTIAL_TIP", "LiveBoxes"]
