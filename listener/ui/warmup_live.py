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
- F5.11 (U-111): sem os modelos da legenda, "Legenda ao vivo" vem desmarcada (o pedido, `wanted`, fica guardado: o preset
  salvo e a faixa "Falta configurar" o usam) e volta ao pedido quando o download termina, se o usuário não mexeu nela;
  "Nomes ao vivo" fica apagada, com o motivo na dica, sem o token do Hugging Face ou sem os modelos de voz, e não vai ligada
  para a gravação. O aviso dos modelos passou para a faixa do rodapé (`ui/warmup_notice.py`).
Sinais ligados a métodos (R-06)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout

from ..config.defaults import HF_PROVIDER_ID, SPECS
from ..live.client import is_fake, live_models_dir
from ..pipeline.hf_check import pyannote_ready
from .live_prepare import missing_models
from .llm_ask import NO_KEY_TIP, has_key
from .theme import METRICS

if TYPE_CHECKING:
    from ..store import Store

LIVE_TIP = "Legenda de quem fala durante a gravação, com 5 a 10 s de atraso (Configurações, grupo Ao vivo)."
NAMES_TIP = "Nome e cargo de quem tem perfil de voz na legenda (carrega o modelo de voz: mais memória)."
PARTIAL_TIP = ("Resumo da reunião pela LLM durante a gravação, no intervalo das Configurações (Ao vivo); tem custo. Desmarcada, "
               "\"Atualizar resumo agora\" na janela ao vivo continua.")
PARTIAL_OFF_TIP = "O resumo parcial sai das legendas: ligue a legenda ao vivo."
NAMES_NEED_HF = "Nomes ao vivo precisa do token do Hugging Face (Configurações, Chaves de acesso)."
NAMES_NEED_VOICES = "Nomes ao vivo precisa dos modelos de voz, que chegam com o primeiro processamento com o token."


def names_reason(store: Store) -> str:
    """U-111: por que os nomes ao vivo não saem nesta máquina (o mesmo que o processo ao vivo confere); vazio = saem."""
    if is_fake():
        return ""
    if not str(store.settings.provider_key(HF_PROVIDER_ID) or "").strip():
        return NAMES_NEED_HF
    if not pyannote_ready(live_models_dir(store.get_setting("pipeline.models_dir"))):
        return NAMES_NEED_VOICES
    return ""


class LiveBoxes(QObject):
    """As três caixas do ao vivo; `wanted` é o pedido da legenda (preset ou Configurações), mesmo sem os modelos. `changed`
    avisa quem mostra os modelos que faltam (a legenda ou o pedido mudou)."""
    changed = Signal()

    def __init__(self, store: Store, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.wanted = False
        self.names_off = ""                                  # U-111: o motivo dos nomes apagados (vazio = saem)
        self._touched = False                                # o usuário mexeu na legenda: o download não a muda
        self.live_check = QCheckBox("Legenda ao vivo")
        self.live_check.setToolTip(LIVE_TIP)
        self.names_check = QCheckBox("Nomes ao vivo")
        self.names_check.setToolTip(NAMES_TIP)
        self.partial_check = QCheckBox("Resumo parcial")
        self.partial_check.setChecked(bool(store.get(SPECS["live.partial"])))       # P-06: o padrão das Configurações
        self.key = has_key(store)                                                    # R-09: sem chave, o parcial não sai
        self.live_check.toggled.connect(self.toggled)
        self.live_check.clicked.connect(self._clicked)        # só o clique do usuário (o `setChecked` não emite)

    def row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(METRICS.card_padding)
        for box in (self.live_check, self.names_check, self.partial_check):
            row.addWidget(box)
        row.addStretch(1)
        return row

    def set_values(self, enabled: int | bool | None, names: int | bool | None) -> None:
        """Legenda e nomes: o valor do preset quando não nulo, senão o padrão das Configurações (o parcial não muda); sem os
        modelos, a legenda vem desmarcada e o pedido fica em `wanted` (U-111)."""
        self.wanted = bool(self.store.get(SPECS["live.enabled"]) if enabled is None else enabled)
        self._touched = False
        self.live_check.setChecked(self.wanted and not missing_models(self.store))
        self.names_check.setChecked(bool(self.store.get(SPECS["live.names"]) if names is None else names))
        self.toggled(self.live_check.isChecked())

    def _clicked(self, _checked: bool) -> None:
        self._touched = True

    def wants_live(self) -> bool:
        """A legenda pedida: a caixa ou, sem o usuário mexer, o pedido que veio desmarcado só pela falta dos modelos (os
        modelos entram na faixa só então, e o "Salvar como preset" grava isto)."""
        return self.live_check.isChecked() or (self.wanted and not self._touched)

    def models_ready(self) -> None:
        """O download terminou com o diálogo aberto: a legenda volta ao pedido, se o usuário não mexeu nela."""
        if not self._touched and not missing_models(self.store):
            self.live_check.setChecked(self.wanted)

    def toggled(self, on: bool) -> None:
        self._names_state(on)
        self._partial_state()
        self.changed.emit()

    def _names_state(self, on: bool) -> None:
        self.names_off = names_reason(self.store)
        self.names_check.setEnabled(on and not self.names_off)
        self.names_check.setToolTip(self.names_off or NAMES_TIP)

    def refresh_key(self) -> None:
        """A chave e o token podem ter chegado pelas Configurações com o diálogo aberto (o link da faixa)."""
        self.key = has_key(self.store)
        self._partial_state()
        self._names_state(self.live_check.isChecked())

    def _partial_state(self) -> None:
        on = self.live_check.isChecked()
        self.partial_check.setEnabled(on and self.key)
        self.partial_check.setToolTip(NO_KEY_TIP if not self.key else PARTIAL_TIP if on else PARTIAL_OFF_TIP)

    def fields(self) -> dict[str, bool]:
        """O que vai para o controlador (CP6): a legenda, os nomes (só quando saem, U-111) e o parcial (só com a legenda e a
        chave, A-21, R-09)."""
        live = self.live_check.isChecked()
        return {"live_enabled": live, "live_names": self.names_check.isChecked() and not self.names_off,
                "live_partial": live and self.key and self.partial_check.isChecked()}


__all__ = ["NAMES_NEED_HF", "NAMES_NEED_VOICES", "PARTIAL_TIP", "LiveBoxes", "names_reason"]
