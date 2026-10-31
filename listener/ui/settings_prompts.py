"""A página "Prompts" das Configurações (F5.6 L2, U-092; A-25, A-27, A-30, A-31; CP2, CP8): as instruções de cada uma das
tarefas da LLM, editáveis; o resto do pedido fica embaixo, só leitura. F5.10: "Perguntar à base" entra com as consultas
(`rag_sql`, com o dicionário de dados fixo embaixo) e a resposta (`rag_answer`); o chat antigo de todas as reuniões sai.

- À esquerda, as tarefas com o nome e quando rodam; à direita, a tarefa aberta. A lista só nasce quando a página aparece
  (`prompt_page_shown`, pela troca de página do diálogo) e o editor de cada tarefa (`PromptPane`), quando ela é escolhida: a
  abertura das Configurações não paga a lista, onze editores nem onze prévias ([fato, 29/10] com a lista montada na
  abertura, as Configurações abriam ~7 % mais devagar pelo menor de 4 rodadas alternadas).
- O editor mostra as instruções salvas ou, sem elas, as do app. "Restaurar padrão" põe as do app de volta; texto igual ao
  padrão grava vazio (`config/prompts.stored`). O selo diz "Padrão do app" ou "Personalizado"; a contagem, quantos dos 4.000.
- Avisos numa faixa: nas tarefas do núcleo (identidade, revisada, título, glossário, transcrição pelo áudio), que mexer afeta
  a identificação e a transcrição; e quando o padrão do app mudou depois da edição (o `.base` gravado não é o de agora).
- Embaixo, só leitura (A-31): o pedido inteiro como a LLM o recebe, com marcadores no lugar dos dados e das instruções, e o
  formato da resposta (`llm/prompts/preview.py`).
- O Salvar barra (A-27, U-072) texto vazio, `{nome}` desconhecido e mais de 4.000 caracteres: abre a página e a tarefa,
  marca o editor e explica na faixa (`prompt_problem`, `reveal_prompt`); o editor é um `QPlainTextEdit`, que o
  `change_signal` reconhece.

`PromptsMixin` é parte do `SettingsDialog`: lê `store` dele. Log só com a tarefa e o hash, nunca o texto (A-46)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSizePolicy,
                               QStackedWidget, QWidget)

from ..config import prompts as prompt_settings
from ..llm import prompts
from ..llm.prompts.preview import preview
from ..llm.types import LlmTask
from ..log import get_logger
from ..model import SettingScope
from .components import Badge, Card, InfoBar
from .keys import note
from .settings_pages import SettingsPage, pill
from . import theme
from .theme import Tone, role

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.settings")

PROMPTS_TITLE = "Prompts"
SUBTITLE = ("As instruções que a LLM recebe em cada tarefa. Só elas são editáveis: o formato da resposta, os dados da reunião e "
            "o idioma ficam fixos, embaixo. Vale para a próxima tarefa da LLM.")
# tarefa -> (nome, quando roda na lista, quando roda por extenso)
TASKS: dict[LlmTask, tuple[str, str, str]] = {
    LlmTask.IDENTIFY: ("Identificação das vozes", "automática, ao processar",
                       "Roda sozinha depois de processar a reunião: sugere quem é cada voz sem nome."),
    LlmTask.REVISE: ("Transcrição revisada", "automática, ao processar",
                     "Roda sozinha depois de processar: corrige pontuação, nomes e siglas da transcrição."),
    LlmTask.TITLE: ("Título", "automática, ao processar", "Roda sozinha depois de processar: sugere o título da reunião."),
    LlmTask.PRE_SUMMARY: ("Pré-resumo", "a pedido, com o resumo", "Roda quando você pede o resumo: o texto curto da lista de reuniões."),
    LlmTask.MINUTES: ("Ata e ações", "a pedido", "Roda quando você pede a ata, ao parar a gravação ou na reunião."),
    LlmTask.EXECUTIVE: ("Resumo executivo", "a pedido, com o resumo", "Roda quando você pede o resumo, a partir da ata."),
    LlmTask.PARTIAL: ("Resumo parcial", "durante a gravação",
                      "Roda durante a gravação, com o resumo parcial ligado ou em \"Atualizar resumo agora\"."),
    LlmTask.CHAT: ("Chat da reunião", "a cada pergunta", "Roda a cada pergunta no chat de uma reunião."),
    LlmTask.RAG_SQL: ("Perguntar à base: consultas", "a cada pergunta",
                      "Roda a cada pergunta sobre todas as reuniões, no Histórico: decide as consultas à base e quando parar."),
    LlmTask.RAG_ANSWER: ("Perguntar à base: resposta", "a cada pergunta",
                         "Roda no fim de cada pergunta sobre todas as reuniões: escreve a resposta com as citações."),
    LlmTask.GLOSSARY: ("Glossário", "antes de gravar", "Roda no \"Antes de gravar\": os termos que ajudam a transcrição."),
    LlmTask.TRANSCRIBE: ("Transcrição pelo áudio", "ao processar, pela LLM",
                         "Roda ao processar quando a transcrição é feita pela LLM (página Processamento)."),
}
CORE = frozenset({LlmTask.IDENTIFY, LlmTask.REVISE, LlmTask.TITLE, LlmTask.GLOSSARY, LlmTask.TRANSCRIBE})
CORE_WARNING = ("Esta tarefa roda sozinha em toda reunião: instruções ruins pioram a identificação das vozes e a transcrição. Na "
                "dúvida, \"Restaurar padrão\".")
CHANGED_WARNING = ("O padrão do app mudou depois da sua edição. \"Restaurar padrão\" mostra o novo; Cancelar desfaz.")
PICK = "Escolha uma tarefa para ver e editar as instruções que a LLM recebe."
INSTRUCTIONS_NOTE = "Pode escrever em português. {idioma} vira o idioma da reunião; o resto vai como está."
FIXED_NOTE = "O pedido inteiro, como a LLM o recebe: {suas instruções} é o texto acima; entre chaves, os dados de cada reunião."
DEFAULT_BADGE, CUSTOM_BADGE = ("Padrão do app", Tone.NEUTRAL), ("Personalizado", Tone.INFO)
LIST_W = 232
EDITOR_H, FIXED_H = 150, 260


class PromptPane(Card):
    """Uma tarefa: os avisos, as instruções editáveis com "Restaurar padrão", o selo e a contagem, e o que vai junto, fixo."""

    def __init__(self, store: Store, task: LlmTask) -> None:
        name, _when, when_long = TASKS[task]
        super().__init__(name)
        self.task = task
        self.name = name
        self.default = prompts.default_instructions(task)
        saved = store.get_setting(prompt_settings.key(str(task)))
        self.content.addWidget(note(when_long))
        if task in CORE:
            self.content.addWidget(InfoBar("warning", CORE_WARNING))
        self.changed_bar = InfoBar("warning", CHANGED_WARNING) if prompt_settings.default_changed(store, str(task), self.default) else None
        if self.changed_bar is not None:
            self.content.addWidget(self.changed_bar)
        self.edit = QPlainTextEdit(saved if saved.strip() else self.default)          # sem instruções suas, as do app
        self.edit.setAccessibleName(f"Instruções: {name}")
        self.edit.setFixedHeight(EDITOR_H)                    # a prévia fixa embaixo fica com o resto da altura
        self.edit.setTabChangesFocus(True)                    # revisão do L2 (R-04): Tab vai ao próximo campo, sem tabulação
        self.edit.textChanged.connect(self._changed)
        self.restore = QPushButton("Restaurar padrão")
        self.restore.setToolTip("Põe de volta as instruções do app (grava ao salvar)")
        self.restore.clicked.connect(self.restore_default)
        self.badge = pill(Badge())
        self.count = QLabel("")
        self.count.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._lay_out()
        self._changed()

    def _lay_out(self) -> None:
        heading = QLabel("Instruções")
        role(heading, "body_strong")
        self.content.addWidget(heading)
        self.content.addWidget(note(INSTRUCTIONS_NOTE))
        self.content.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.restore)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        row.addWidget(self.count)
        self.content.addLayout(row)
        fixed = QLabel("O que vai junto, fixo")
        role(fixed, "body_strong")
        self.fixed = QPlainTextEdit(preview(self.task))
        self.fixed.setReadOnly(True)
        self.fixed.setTabChangesFocus(True)
        self.fixed.setAccessibleName(f"O que vai junto, fixo: {self.name}")
        self.fixed.setMinimumHeight(FIXED_H)
        self.content.addWidget(fixed)
        self.content.addWidget(note(FIXED_NOTE))
        self.content.addWidget(self.fixed)

    def text(self) -> str:
        return self.edit.toPlainText()

    def value(self) -> str:
        """O que o Salvar grava: vazio quando o texto é o padrão."""
        return prompt_settings.stored(self.text(), self.default)

    def problem(self) -> str | None:
        return prompt_settings.problem(self.text())

    def restore_default(self) -> None:
        self.edit.setPlainText(self.default)

    def _changed(self) -> None:
        custom = bool(self.value())
        text, value = CUSTOM_BADGE if custom else DEFAULT_BADGE
        self.badge.setText(text)
        self.badge.set_tone(value)
        self.restore.setEnabled(custom)
        n = len(self.text())
        self.count.setText(f"{n:,} de {prompt_settings.MAX_CHARS:,} caracteres".replace(",", "."))
        theme.note(self.count, Tone.DANGER if n > prompt_settings.MAX_CHARS else Tone.SECONDARY)    # acima do limite, em vermelho


class PromptsMixin:
    """A página "Prompts" do `SettingsDialog`."""
    store: Store

    def _prompts_page(self) -> SettingsPage:
        """Só o título e o subtítulo: a lista nasce quando a página aparece (`prompt_page_shown`)."""
        self.prompt_page = SettingsPage(PROMPTS_TITLE, SUBTITLE, SettingScope.NOW)
        self.prompt_panes: dict[LlmTask, PromptPane] = {}
        self._prompt_ui: tuple[QListWidget, QStackedWidget] | None = None
        return self.prompt_page

    def prompt_page_shown(self, page: QWidget | None) -> None:
        """A troca de página do diálogo: a primeira vez na página Prompts monta a lista."""
        if page is self.prompt_page:
            self._prompts_ui()

    @property
    def prompt_list(self) -> QListWidget:
        return self._prompts_ui()[0]

    @property
    def prompt_stack(self) -> QStackedWidget:
        return self._prompts_ui()[1]

    def _prompts_ui(self) -> tuple[QListWidget, QStackedWidget]:
        if self._prompt_ui is None:
            self._prompt_ui = self._build_prompts()
        return self._prompt_ui

    def _build_prompts(self) -> tuple[QListWidget, QStackedWidget]:
        tasks = QListWidget()
        tasks.setAccessibleName("Tarefas da LLM")
        tasks.setFixedWidth(LIST_W)
        tasks.setWordWrap(True)
        height = 2 * tasks.fontMetrics().lineSpacing() + 10
        for task in prompts.EDITABLE:
            name, when, when_long = TASKS[task]
            item = QListWidgetItem(f"{name}\n{when}")
            item.setToolTip(when_long)
            item.setSizeHint(QSize(0, height))
            tasks.addItem(item)
        tasks.setFixedHeight(height * tasks.count() + 2 * tasks.frameWidth() + 4)
        tasks.currentRowChanged.connect(self._prompt_row)
        stack = QStackedWidget()
        pick = note(PICK)
        pick.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        stack.addWidget(pick)
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(tasks, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(stack, 1)
        self.prompt_page.content.addLayout(row)
        self.prompt_page.finish()
        return tasks, stack

    def _prompt_row(self, row: int) -> None:
        if 0 <= row < len(prompts.EDITABLE):
            self.select_prompt(prompts.EDITABLE[row])

    def select_prompt(self, task: LlmTask) -> PromptPane:
        """Abre a tarefa: monta o editor dela na primeira vez (A-30) e marca a linha da lista."""
        pane = self.prompt_panes.get(task)
        if pane is None:
            pane = PromptPane(self.store, task)
            self.prompt_panes[task] = pane
            self.prompt_stack.addWidget(pane)
        row = prompts.EDITABLE.index(task)
        if self.prompt_list.currentRow() != row:
            self.prompt_list.setCurrentRow(row)
        self.prompt_stack.setCurrentWidget(pane)
        return pane

    def prompt_problem(self) -> tuple[str, QWidget] | None:
        """O primeiro erro das tarefas abertas (A-27, U-072), na ordem da lista, com o editor dela."""
        for task in prompts.EDITABLE:
            pane = self.prompt_panes.get(task)
            err = pane.problem() if pane is not None else None
            if pane is not None and err:
                return f"Prompts, {pane.name}: {err}", pane.edit
        return None

    def reveal_prompt(self, widget: QWidget) -> None:
        """U-072: o erro num editor de instruções abre a tarefa dele."""
        task = next((t for t, pane in self.prompt_panes.items() if pane.edit is widget), None)
        if task is not None:
            self.select_prompt(task)

    def save_prompts(self) -> list[str]:
        """Grava as tarefas abertas que mudaram; devolve as tarefas gravadas. Log com a tarefa e o hash (A-46)."""
        done = [str(t) for t, pane in self.prompt_panes.items()
                if prompt_settings.save(self.store, str(t), pane.text(), pane.default)]
        for t in done:
            key = prompt_settings.key(t)
            log.info("configurações: prompt de %s: %s", t, prompt_settings.shown(key, self.store.get_setting(key)))
        return done


__all__ = ["CORE", "PROMPTS_TITLE", "TASKS", "PromptPane", "PromptsMixin"]
