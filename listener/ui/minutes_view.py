"""A ata, o executivo e o pré-resumo (F3 item 11; F5.4 L4: A-15, U-079, U-059), saídos do `ui/llm_tabs.py`, que passava de
400 linhas: `TextTab`, usada pela reunião aberta e pela revisão.

- A-15: um `QStackedWidget` com a leitura (`reader`, um `QTextBrowser` com `setMarkdown`), o `editor` (o `QPlainTextEdit` de
  sempre, com o Markdown) e o estado vazio. "Editar" abre o Markdown; "Salvar edição" grava e volta à leitura; "Cancelar
  edição" descarta. `editing()` é "em edição ou modificado" (R3-01): a recarga por assinatura nunca apaga a edição em
  curso; com uma versão nova chegada nela, acende o aviso e o "Recarregar".
- U-079: a origem em dd/mm/aaaa ("gemini · modelo · 28/10/2026 15:39 · editado por você").
- U-059: sem versão, o estado vazio com o ícone, a frase ("Ata ainda não gerada") e a ação ("Gerar ata", o "gerar de novo"
  da tarefa); com a ata à espera da identificação (U-042), a ação fica na faixa ("Gerar ata mesmo assim").
- U-057: os avisos (versão nova, gerada antes do reprocessamento, pulada pelo alerta de custo, U-042) numa faixa só, com a
  ação contextual dela (`btn_extra`).
- A leitura e o estado vazio nascem quando a página precisa deles, e o vazio de uma aba escondida só quando ela aparece
  ([fato, 28/10] criados sempre, as três abas de texto deixavam a reunião e a revisão mais lentas para abrir: cada widget
  a mais é criado e polido na abertura)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

from PySide6.QtGui import QShowEvent, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget

from ..model import JobStep, SummaryKind
from ..rag.export import fmt_datetime
from .components import EmptyState, InfoBar
from .icons import set_icon
from .theme import PRIMARY, note, set_kind

if TYPE_CHECKING:
    from ..store import Store

KIND_STEP: dict[str, JobStep] = {SummaryKind.MINUTES: JobStep.MINUTES, SummaryKind.EXECUTIVE: JobStep.EXECUTIVE,
                                 SummaryKind.PRE_SUMMARY: JobStep.PRE_SUMMARY}
KIND_TITLE: dict[str, str] = {SummaryKind.PRE_SUMMARY: "Pré-resumo", SummaryKind.MINUTES: "Ata", SummaryKind.EXECUTIVE: "Executivo"}
# U-059: a frase e a ação do estado vazio de cada tipo
EMPTY: dict[str, tuple[str, str]] = {SummaryKind.MINUTES: ("Ata ainda não gerada.", "Gerar ata"),
                                     SummaryKind.EXECUTIVE: ("Resumo executivo ainda não gerado.", "Gerar resumo executivo"),
                                     SummaryKind.PRE_SUMMARY: ("Pré-resumo ainda não gerado.", "Gerar pré-resumo")}
INDEXED_KINDS = frozenset({SummaryKind.MINUTES, SummaryKind.EXECUTIVE})   # F5: o pré-resumo não entra na busca
STALE_MARK = "gerados antes do reprocessamento"
NEWER_NOTICE = ("Chegou uma versão nova enquanto você editava: \"Salvar edição\" grava o seu texto sobre a versão nova (o texto "
                "da LLM fica guardado como original); \"Recarregar\" mostra a nova e descarta o que não foi salvo.")
BUSY_TIP = "há um job de LLM em andamento para esta reunião"
HEADING_GAP = 12                 # px lógicos antes de cada título da leitura (o primeiro, colado no topo)


def origin_text(row: dict[str, Any]) -> str:
    """U-079: "gemini · modelo · 28/10/2026 15:39 · editado por você"."""
    edited = " · editado por você" if row.get("text_edited") else ""
    when = fmt_datetime(row.get("created_at")) or str(row.get("created_at") or "")[:16].replace("T", " ")
    return f"{row.get('provider')} · {row.get('model')} · {when}{edited}"


class TextTab(QWidget):
    """Pré-resumo, ata ou executivo: a leitura formatada, o Markdown em edição, a origem, os avisos e "gerar de novo". O
    editor guarda a versão que mostra (`summary_id` e o texto carregado); uma versão nova chegada com o editor em edição ou
    modificado não o toca: acende o aviso e o botão "Recarregar"; "Salvar edição" grava o texto do editor sobre a versão
    atual, como edição (o texto da LLM continua em `text_llm`), para a edição nunca ficar numa versão superada que nenhuma
    tela mostra (R3-01)."""

    def __init__(self, store: Store, meeting_id: int, kind: SummaryKind, on_regenerate: Callable[[JobStep], Any],
                 parent: QWidget | None = None, on_edited: Callable[[], Any] | None = None):
        super().__init__(parent)
        self.store = store
        self.meeting_id = meeting_id
        self.kind = kind
        self.on_regenerate = on_regenerate
        self.on_edited = on_edited                               # F5: reindexar a reunião (ata e executivo)
        self.summary_id: int | None = None
        self._loaded: tuple[int | None, str] | None = None       # (versão, texto) que estão no editor
        self.newer = False                                       # chegou versão nova durante a edição
        self.edit_mode = False                                   # A-15: o Markdown aberto ("Editar")
        self._last: tuple[set[str], dict[str, str], bool] = (set(), {}, False)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        note(self.info)                                          # U-079: a origem em legenda
        self._bar: InfoBar | None = None                         # U-057: os avisos numa faixa (sob demanda)
        self._bar_state: tuple[str, str] = ("", "warning")       # (texto, tipo) da faixa
        self._extra_note, self._extra_label = "", ""
        self._extra_action: Callable[[], Any] | None = None
        self._reader: QTextBrowser | None = None                 # A-15: a leitura formatada (sob demanda)
        self._empty: EmptyState | None = None                    # U-059 (sob demanda)
        self._busy = self._waiting = self._deferred = False
        self.editor = QPlainTextEdit()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.editor)
        self._buttons()
        self._lay = QVBoxLayout(self)
        self._lay.addWidget(self.info)
        self._lay.addWidget(self.stack, 1)
        self._lay.addLayout(self._row)

    def _buttons(self) -> None:
        self.btn_edit = QPushButton("Editar")
        self.btn_edit.setToolTip("Abre o texto em Markdown para editar")
        self.btn_save = QPushButton("Salvar edição")
        self.btn_cancel = QPushButton("Cancelar edição")
        self.btn_cancel.setToolTip("Descarta o que não foi salvo e volta à leitura")
        self.btn_regen = QPushButton("Gerar de novo")
        self.btn_reload = QPushButton("Recarregar")
        self.btn_reload.setVisible(False)
        for button, icon in ((self.btn_edit, "edit"), (self.btn_save, "save"), (self.btn_regen, "refresh")):
            set_icon(button, icon)
        set_kind(self.btn_save, PRIMARY)
        self.btn_edit.clicked.connect(self.start_edit)
        self.btn_save.clicked.connect(self.save)
        self.btn_cancel.clicked.connect(self.cancel_edit)
        self.btn_regen.clicked.connect(self._generate)
        self.btn_reload.clicked.connect(self.load_newer)
        self._row = QHBoxLayout()
        for b in (self.btn_edit, self.btn_save, self.btn_cancel, self.btn_regen, self.btn_reload):
            self._row.addWidget(b)
        self._row.addStretch(1)

    # ---------------------------------------------------------------- a faixa e as páginas sob demanda
    @property
    def notice_bar(self) -> InfoBar:
        """A faixa dos avisos, com a ação contextual (`btn_extra`, U-042); nasce no primeiro aviso."""
        return self._ensure_bar()

    def _ensure_bar(self) -> InfoBar:
        if self._bar is None:
            self._bar = InfoBar("warning")
            self._bar.action.clicked.connect(self._extra_clicked)
            self._lay.insertWidget(1, self._bar)
            self._sync_bar()
        return self._bar

    @property
    def notice(self) -> QLabel:
        return self.notice_bar.label

    @property
    def btn_extra(self) -> QPushButton:
        """U-042: "Gerar ata mesmo assim" ou "Atualizar a ata com os nomes"."""
        return self.notice_bar.action

    def _sync_bar(self) -> None:
        if self._bar is None:
            return
        text, kind = self._bar_state
        self._bar.set_text(text)
        self._bar.set_kind(kind)
        self._bar.setVisible(bool(text))
        self._bar.action.setText(self._extra_label)
        self._bar.action.setVisible(bool(self._extra_label and self._extra_action is not None))

    @property
    def reader(self) -> QTextBrowser:
        if self._reader is None:
            self._reader = QTextBrowser()
            self._reader.setOpenLinks(False)
            self._reader.setOpenExternalLinks(False)
            self.stack.addWidget(self._reader)
        return self._reader

    @property
    def empty(self) -> EmptyState:
        if self._empty is None:
            text, action = EMPTY[str(self.kind)]
            self._empty = EmptyState("document", text, action)
            self._empty.action.clicked.connect(self._generate)
            self.stack.addWidget(self._empty)
            self._sync_empty()
        return self._empty

    def _sync_empty(self) -> None:
        """A ação do estado vazio: apagada com a LLM em andamento; escondida quando a faixa oferece a dela (U-042)."""
        if self._empty is not None:
            self._empty.action.setEnabled(not self._busy)
            self._empty.action.setToolTip(BUSY_TIP if self._busy else "")
            self._empty.action.setVisible(not self._waiting)

    # ---------------------------------------------------------------- estado
    def editing(self) -> bool:
        """R3-01 (A-15): em edição ("Editar") ou com o Markdown modificado."""
        return self.edit_mode or self.editor.document().isModified()

    def _page(self) -> None:
        """A página à vista e os botões do modo: o vazio sem versão, o editor em edição, a leitura no resto."""
        has = self.summary_id is not None
        self._deferred = not (has or self.edit_mode) and self._empty is None and not self.isVisible()
        if not self._deferred:                                   # o vazio de uma aba escondida nasce quando ela aparece
            self.stack.setCurrentWidget(self.editor if self.edit_mode else self.reader if has else self.empty)
        self.btn_edit.setVisible(has and not self.edit_mode)     # sem versão, o estado vazio diz tudo (nem a origem)
        self.info.setVisible(has)
        self.btn_save.setVisible(self.edit_mode)
        self.btn_cancel.setVisible(self.edit_mode)
        self.btn_regen.setVisible(has or self.edit_mode)        # sem versão, a ação é a do estado vazio
        self._row.invalidate()          # [fato, 28/10] numa aba escondida, o mínimo da fileira ficava o de todos os botões à vista
        self.updateGeometry()           # (e o da aba e do cartão em volta, que somavam a largura deles)

    def reload(self, stale: set[str], skipped: dict[str, str], busy: bool, *, force: bool = False) -> None:
        self._last = (stale, skipped, busy)
        row = self.store.summaries.current(self.meeting_id, self.kind)
        current = (int(row["id"]) if row else None, self.store.summaries.display_text(row))
        if current != self._loaded:
            if force or self._loaded is None or not (self.newer or self.editing()):
                self._show(current)
            else:
                self.newer = True
        shown = row
        if self.summary_id != current[0]:                        # a versão aberta ficou para trás: o rótulo fala dela
            shown = self.store.summaries.get(self.summary_id) if self.summary_id is not None else None
        self.info.setText("ainda não gerado" if shown is None else origin_text(dict(shown)))
        self._notices(stale, skipped, row is None)
        self.btn_reload.setVisible(self.newer)
        self._busy = busy
        self.btn_regen.setEnabled(not busy)
        self.btn_regen.setToolTip(BUSY_TIP if busy else "")
        self.btn_save.setEnabled(self.summary_id is not None)
        self._page()
        self._sync_empty()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._deferred:
            self._page()
            self._sync_empty()

    def _notices(self, stale: set[str], skipped: dict[str, str], missing: bool) -> None:
        title, step = KIND_TITLE[str(self.kind)], KIND_STEP[str(self.kind)]
        warnings = []
        if self.newer:
            warnings.append(NEWER_NOTICE)
        if str(self.kind) in stale:
            warnings.append(f"{title}: {STALE_MARK}; use \"Gerar de novo\" para refazer com a transcrição atual.")
        if str(step) in skipped and missing:
            warnings.append(f"{title} não gerado: {skipped[str(step)]}; gerar?")
        notes = [*warnings, self._extra_note] if self._extra_note else warnings
        self._bar_state = ("\n".join(notes), "warning" if warnings else "info")
        if notes:
            self._ensure_bar()                                  # a faixa nasce no primeiro aviso
        self._sync_bar()
        self._waiting = self._extra_action is not None and missing     # U-042: a ação da faixa no lugar da do estado vazio

    def set_extra(self, note_text: str, label: str = "", action: Callable[[], Any] | None = None) -> None:
        """U-042: um aviso a mais e um botão contextual (a ata que espera as vozes, os nomes que mudaram); vazio esconde."""
        self._extra_note, self._extra_label, self._extra_action = note_text, label, action
        self._sync_bar()

    def _extra_clicked(self) -> None:
        if self._extra_action is not None:
            self._extra_action()

    def _generate(self) -> None:
        self.on_regenerate(KIND_STEP[str(self.kind)])

    def _show(self, current: tuple[int | None, str]) -> None:
        self._loaded = current
        self.summary_id = current[0]
        self.newer = False
        self.edit_mode = False
        if self.editor.toPlainText() != current[1]:
            self.editor.setPlainText(current[1])
        self.editor.document().setModified(False)
        self._read(current[1])

    def _read(self, md: str) -> None:
        """A leitura (A-15): o Markdown formatado pelo `QTextDocument`, com o respiro do Fluent antes de cada título. Sem
        texto e sem leitura ainda criada, nada a fazer."""
        if self._reader is None and not md:
            return
        self.reader.setMarkdown(md)
        cursor = QTextCursor(self.reader.document())
        block = self.reader.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel():
                fmt = block.blockFormat()
                fmt.setTopMargin(HEADING_GAP if block.blockNumber() else 0)
                fmt.setBottomMargin(HEADING_GAP / 3)
                cursor.setPosition(block.position())
                cursor.setBlockFormat(fmt)
            block = block.next()
        self.reader.document().setModified(False)

    # ---------------------------------------------------------------- edição (A-15)
    def start_edit(self) -> None:
        """"Editar": o Markdown no editor, com o foco nele."""
        if self.summary_id is None:
            return
        self.edit_mode = True
        self._page()
        self.editor.setFocus()

    def cancel_edit(self) -> None:
        """"Cancelar edição": o editor volta ao texto carregado e a leitura volta (uma versão nova chegada entra agora)."""
        self.edit_mode = False
        self.editor.document().setModified(False)
        if self.newer:
            self.load_newer()
            return
        if self._loaded is not None and self.editor.toPlainText() != self._loaded[1]:
            self.editor.setPlainText(self._loaded[1])
            self.editor.document().setModified(False)
        self._page()

    def load_newer(self) -> None:
        """"Recarregar": mostra a versão atual e descarta o que não foi salvo."""
        self.reload(*self._last, force=True)

    def save(self) -> bool:
        """Grava o editor na versão atual (a que chegou durante a edição, se chegou), limpa a marca de modificado e volta à
        leitura."""
        current = self.store.summaries.current(self.meeting_id, self.kind)
        target = int(current["id"]) if current is not None else self.summary_id
        if target is None:
            return False
        text = self.editor.toPlainText()
        row = self.store.summaries.get(target)
        original = str((row or {}).get("text_llm") or "")
        changed = self.store.summaries.set_text_edited(target, None if text == original else text)
        self.summary_id = target
        self.newer = False
        self.edit_mode = False
        self._loaded = (target, self.store.summaries.display_text(self.store.summaries.get(target)))
        self.editor.document().setModified(False)
        self._read(self._loaded[1])
        self.reload(*self._last)
        if changed and self.on_edited is not None and str(self.kind) in INDEXED_KINDS:
            self.on_edited()
        return changed


__all__ = ["EMPTY", "INDEXED_KINDS", "KIND_STEP", "KIND_TITLE", "NEWER_NOTICE", "STALE_MARK", "TextTab", "origin_text"]
