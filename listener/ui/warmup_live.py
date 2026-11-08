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
  para a gravação. O aviso dos modelos passou para a faixa do rodapé (`ui/warmup_notice.py`). Caixa apagada nunca aparece
  marcada (os nomes e o parcial, como o rodapé da transcrição): o pedido do preset ou das Configurações fica guardado por
  dentro (`names_wanted`, `partial_wanted`) e volta quando a caixa acende.
- F5.20 (U-163, U-156): "Sugestões" (o botão Sugerir desta gravação), marcada por `suggest.enabled`, acesa só com a legenda,
  a bandeja de legendas, a chave e `suggest.enabled` ligada (R-05 da revisão do L2: desligada, a caixa marcada não faria
  nada; religada com o diálogo aberto, a caixa volta marcada), fora do preset; e "Meu objetivo nesta reunião", uma linha
  opcional, abaixo das caixas. Vão em `fields["suggest"]` e `fields["goal"]`. Moram aqui, não no `warmup.py`, que já passa
  de 400 linhas (A-12).
Sinais ligados a métodos (R-06)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

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
NAMES_OFF_TIP = "Os nomes saem na legenda: ligue a legenda ao vivo."
NAMES_NEED_HF = "Nomes ao vivo precisa do token do Hugging Face (Configurações, Chaves de acesso)."
NAMES_NEED_VOICES = "Nomes ao vivo precisa dos modelos de voz, que chegam com o primeiro processamento com o token."
SUGGEST_TIP = "Sugestões do que falar pela LLM, a pedido, na bandeja de legendas; tem custo por clique."
SUGGEST_OFF_TIP = "A sugestão lê a legenda ao vivo: ligue-a."
SUGGEST_NEED_TRAY = "Ligue a bandeja de legendas (Configurações, Ao vivo): o cartão da sugestão mora nela."
SUGGEST_NEED_SETTING = "As sugestões estão desligadas: ligue \"Sugestões ao vivo\" nas Configurações (Ao vivo)."
GOAL_HINT = "Meu objetivo nesta reunião (opcional; a IA o lê nas sugestões)"
GOAL_MAX = 300


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
        self.names_wanted = False                            # o pedido dos nomes (preset, Configurações ou o clique)
        self.partial_wanted = bool(store.get(SPECS["live.partial"]))     # P-06: o padrão das Configurações
        self.names_off = ""                                  # U-111: o motivo dos nomes apagados (vazio = saem)
        self._touched = False                                # o usuário mexeu na legenda: o download não a muda
        self.live_check = QCheckBox("Legenda ao vivo")
        self.live_check.setToolTip(LIVE_TIP)
        self.names_check = QCheckBox("Nomes ao vivo")
        self.names_check.setToolTip(NAMES_TIP)
        self.partial_check = QCheckBox("Resumo parcial")
        self.suggest_check = QCheckBox("Sugestões")                                  # F5.20 (U-163)
        self.suggest_on = bool(store.get(SPECS["suggest.enabled"]))
        self.suggest_wanted = self.suggest_on
        self.goal_edit = QLineEdit()                                                 # F5.20 (U-156)
        self.goal_edit.setPlaceholderText(GOAL_HINT)
        self.goal_edit.setMaxLength(GOAL_MAX)
        self.goal_edit.setAccessibleName("Meu objetivo nesta reunião")
        self.key = has_key(store)                                                    # R-09: sem chave, o parcial não sai
        self.live_check.toggled.connect(self.toggled)
        self.live_check.clicked.connect(self._clicked)        # só o clique do usuário (o `setChecked` não emite)
        self.names_check.toggled.connect(self._names_toggled)       # a marca com a caixa acesa é o pedido
        self.partial_check.toggled.connect(self._partial_toggled)
        self.suggest_check.toggled.connect(self._suggest_toggled)

    def row(self) -> QVBoxLayout:
        """As caixas numa linha e, embaixo, o objetivo da reunião (F5.20)."""
        row = QHBoxLayout()
        row.setSpacing(METRICS.card_padding)
        for box in (self.live_check, self.names_check, self.partial_check, self.suggest_check):
            row.addWidget(box)
        row.addStretch(1)
        goal = QHBoxLayout()
        goal.setSpacing(METRICS.card_padding)
        goal.addWidget(QLabel("Objetivo"))
        goal.addWidget(self.goal_edit, 1)
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(row)
        outer.addLayout(goal)
        return outer

    def set_values(self, enabled: int | bool | None, names: int | bool | None) -> None:
        """Legenda e nomes: o valor do preset quando não nulo, senão o padrão das Configurações (o parcial não muda); sem os
        modelos, a legenda vem desmarcada e o pedido fica em `wanted` (U-111)."""
        self.wanted = bool(self.store.get(SPECS["live.enabled"]) if enabled is None else enabled)
        self._touched = False
        self.live_check.setChecked(self.wanted and not missing_models(self.store))
        self.names_wanted = bool(self.store.get(SPECS["live.names"]) if names is None else names)
        self.toggled(self.live_check.isChecked())

    def _clicked(self, _checked: bool) -> None:
        self._touched = True

    def _names_toggled(self, checked: bool) -> None:
        if self.names_check.isEnabled():
            self.names_wanted = checked

    def _partial_toggled(self, checked: bool) -> None:
        if self.partial_check.isEnabled():
            self.partial_wanted = checked

    def _suggest_toggled(self, checked: bool) -> None:
        if self.suggest_check.isEnabled():
            self.suggest_wanted = checked

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
        self._suggest_state()
        self.changed.emit()

    def _names_state(self, on: bool) -> None:
        """Os nomes acesos só com a legenda e o que eles precisam; apagados, desmarcados, com o motivo na dica."""
        self.names_off = names_reason(self.store)
        lit = on and not self.names_off
        self.names_check.setEnabled(lit)
        self.names_check.setChecked(lit and self.names_wanted)
        self.names_check.setToolTip(self.names_off or (NAMES_TIP if on else NAMES_OFF_TIP))

    def refresh_key(self) -> None:
        """A chave e o token podem ter chegado pelas Configurações com o diálogo aberto (o link da faixa)."""
        self.key = has_key(self.store)
        self._partial_state()
        self._suggest_state()
        self._names_state(self.live_check.isChecked())

    def _partial_state(self) -> None:
        on = self.live_check.isChecked()
        self.partial_check.setEnabled(on and self.key)
        self.partial_check.setChecked(on and self.key and self.partial_wanted)
        self.partial_check.setToolTip(NO_KEY_TIP if not self.key else PARTIAL_TIP if on else PARTIAL_OFF_TIP)

    def _suggest_state(self) -> None:
        """F5.20: acesa só com a legenda, a bandeja de legendas, a chave e `suggest.enabled`; apagada, desmarcada, com o motivo
        na dica. Religada nas Configurações com o diálogo aberto, o padrão dela volta (a caixa marcada)."""
        on, tray = bool(self.store.get(SPECS["suggest.enabled"])), bool(self.store.get(SPECS["live.caption_tray"]))
        if on and not self.suggest_on:
            self.suggest_wanted = True
        self.suggest_on = on
        lit = self.live_check.isChecked() and self.key and tray and on
        self.suggest_check.setEnabled(lit)
        self.suggest_check.setChecked(lit and self.suggest_wanted)
        self.suggest_check.setToolTip(SUGGEST_TIP if lit else SUGGEST_NEED_SETTING if not on else NO_KEY_TIP if not self.key
                                      else SUGGEST_NEED_TRAY if not tray else SUGGEST_OFF_TIP)

    def fields(self) -> dict[str, bool | str | None]:
        """O que vai para o controlador (CP6): a legenda, os nomes (só quando saem, U-111) e o parcial (só com a legenda e a
        chave, A-21, R-09)."""
        live = self.live_check.isChecked()
        return {"live_enabled": live, "live_names": self.names_check.isEnabled() and self.names_check.isChecked(),
                "live_partial": self.partial_check.isEnabled() and self.partial_check.isChecked(),
                "suggest": self.suggest_check.isEnabled() and self.suggest_check.isChecked(),     # F5.20 (U-163)
                "goal": self.goal_edit.text().strip() or None}                                    # F5.20 (U-156)


__all__ = ["NAMES_NEED_HF", "NAMES_NEED_VOICES", "PARTIAL_TIP", "LiveBoxes", "names_reason"]
