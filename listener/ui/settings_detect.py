"""F5.7 (L3, U-094): o cartão "Reunião sem gravação" da página "Idioma e avisos" das Configurações (vale na hora: a cola do
aviso, `ui/call_watch.py`, relê as chaves quando o diálogo fecha). Saiu de `ui/settings_general.py` para ela não crescer;
`DetectCardMixin` é parte do `SettingsDialog` (pela `GeneralPagesMixin`) e registra os editores pelo `_add` dele.

- "Avisar quando um app de reunião tocar áudio sem gravação", com a ajuda embaixo; desligado, o resto do cartão fica apagado.
- "Avisar depois de [8] s com o microfone em uso" (spin de 3 a 120, U-073) e "Lembrar uma vez, 2 min depois...".
- "Apps observados": uma caixa por linha (`call_config.app_rows`: Teams, Zoom, Reunião no navegador, Webex e Slack, os
  acrescentados e os desligados pela bandeja), "Remover" só nos acrescentados, a nota "Desmarcado, o app nunca avisa." e
  "Adicionar app que está tocando…", que lista os apps com som agora (a mesma lista do "Antes de gravar", lida numa `Task`)
  num menu, sem digitar executável. A lista grava duas chaves, `detect.apps` e `detect.off` (`AppsEditor`), e o texto salvo
  volta igual quando nada mudou (D-059).
- "Também outros apps que usam o microfone com som (20 s)".
A validação e o erro na página são os das Configurações (U-072, U-073 e R-04). O Tab segue a ordem da tela (R-03): as linhas
nascem antes do "Adicionar…", e o app acrescentado entra antes dele na cadeia do foco."""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QCheckBox, QFormLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from ..log import get_logger
from . import call_config as cfg
from .call_config import AppRow
from .keys import note as wrap_note
from .settings_editors import BoolEditor, SettingEditor
from .settings_pages import FORM_SPACING, SettingsPage
from .tasks import run_task
from .theme import METRICS

if TYPE_CHECKING:
    from ..platform.base import Platform, PlayingApp, WatchedApp
    from ..store import Store
    from .theme import Tone

log = get_logger("ui.settings")

DETECT_TITLE = "Reunião sem gravação"
HELP = "O listener-meet nunca grava sozinho: o aviso abre o \"Antes de gravar\" com o app já escolhido como fonte."
SECONDS_TAIL = "s com o microfone em uso"
APPS_LABEL = "Apps observados"
OFF_NOTE = "Desmarcado, o app nunca avisa."
ADD_TEXT = "Adicionar app que está tocando…"
NOTHING_NEW = "Nenhum app novo tocando agora"


class AppsList(QWidget):
    """"Apps observados": as linhas com caixa, o "Remover" dos acrescentados, a nota e o "Adicionar…"."""

    def __init__(self, platform: Platform, watched: Sequence[WatchedApp], apps_raw: str, off_raw: str) -> None:
        super().__init__()
        self.platform = platform
        self.watched = tuple(watched)
        self.menu: QMenu | None = None
        self._rows: list[tuple[AppRow, QCheckBox, QWidget]] = []
        self._removers: dict[QPushButton, AppRow] = {}
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(2)
        self.add_button = QPushButton(ADD_TEXT)
        self.add_button.setToolTip("Escolha na lista dos apps com som agora (a mesma do \"Antes de gravar\"): o app passa a avisar "
                                   "como um app de reunião")
        self.add_button.clicked.connect(self._ask_playing)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(METRICS.form_row)
        lay.addLayout(self.rows_layout)
        off = cfg.exe_set(off_raw)
        for row in cfg.app_rows(self.watched, apps_raw, off_raw):   # R-03: as linhas antes do "Adicionar…" na cadeia do Tab
            self._add_row(row, row.checked(off))
        lay.addWidget(wrap_note(OFF_NOTE))
        add_row = QHBoxLayout()
        add_row.addWidget(self.add_button)
        add_row.addStretch(1)
        lay.addLayout(add_row)
        self.setAccessibleName(APPS_LABEL)

    def _add_row(self, row: AppRow, checked: bool) -> list[QWidget]:
        """A linha e os campos dela que pegam o Tab, na ordem (a caixa e, num acrescentado, o "Remover")."""
        box = QCheckBox(row.label)
        box.setChecked(checked)
        box.setToolTip(", ".join(row.exes))
        line = QWidget()
        h = QHBoxLayout(line)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(box)
        stops: list[QWidget] = [box]
        if row.added:                                # o "Remover" junto do nome, não na outra ponta da linha
            remove = QPushButton("Remover")
            stops.append(remove)
            remove.setAccessibleName(f"Remover {row.label}")
            remove.clicked.connect(self._remove_clicked)
            self._removers[remove] = row
            h.addSpacing(METRICS.card_gap)
            h.addWidget(remove)
        h.addStretch(1)
        self.rows_layout.addWidget(line)
        self._rows.append((row, box, line))
        return stops

    def _tab_tail(self) -> QWidget | None:
        """O último campo das linhas na cadeia do Tab (o "Remover" do último acrescentado ou a última caixa)."""
        if not self._rows:
            return None
        _row, box, line = self._rows[-1]
        return next(iter(line.findChildren(QPushButton)), box)

    # ---------------------------------------------------------------- o estado
    def boxes(self) -> dict[str, QCheckBox]:
        return {row.label: box for row, box, _line in self._rows}

    def added(self) -> list[str]:
        return [row.label for row, _box, _line in self._rows if row.added]

    def known(self) -> frozenset[str]:
        return frozenset(key for row, _box, _line in self._rows for key in row.keys)

    def lists(self) -> tuple[str, str]:
        """(`detect.apps`, `detect.off`) como a tela está."""
        return cfg.lists_from((row, box.isChecked()) for row, box, _line in self._rows)

    # ---------------------------------------------------------------- acrescentar e remover
    def add_app(self, exe: str) -> bool:
        """Acrescenta um app (marcado, como app de reunião); False se ele já está na lista."""
        if not exe.strip() or exe.lower() in self.known():
            return False
        before = self._tab_tail()
        stops = self._add_row(AppRow(cfg.app_name(exe), (exe,), added=True), True)
        for first, second in zip([before, *stops], [*stops, self.add_button], strict=True):   # R-03: antes do "Adicionar…"
            if first is not None:
                QWidget.setTabOrder(first, second)
        return True

    def remove(self, label: str) -> bool:
        """Tira um acrescentado (o de fábrica não sai: desmarca-se)."""
        found = next(((row, line) for row, _box, line in self._rows if row.added and row.label == label), None)
        if found is None:
            return False
        row, line = found
        self._rows = [entry for entry in self._rows if entry[0] is not row]
        self._removers = {button: r for button, r in self._removers.items() if r is not row}
        self.rows_layout.removeWidget(line)
        line.hide()
        line.deleteLater()
        return True

    def _remove_clicked(self) -> None:
        row = self._removers.get(self.sender())  # type: ignore[call-overload]
        if row is not None:
            self.remove(row.label)

    def _ask_playing(self) -> None:
        """A lista dos apps com som agora, fora da thread da interface (M-26: o sistema de áudio pode demorar)."""
        self.add_button.setEnabled(False)
        run_task(self.platform.list_playing_apps, None, self._on_playing)

    def _on_playing(self, result: Any) -> None:
        self.add_button.setEnabled(True)
        if isinstance(result, BaseException):
            log.debug("configurações: a lista dos apps tocando falhou (%s)", type(result).__name__)
        self.offer(result if isinstance(result, list) else [])

    def offer(self, apps: Sequence[PlayingApp]) -> QMenu:
        """O menu dos apps com som que ainda não estão na lista (o nome e o executável, nunca o título da janela)."""
        menu = QMenu(self)
        known, seen = self.known(), set()
        for app in apps:
            low = app.exe.lower()
            if low in known or low in seen:
                continue
            seen.add(low)
            menu.addAction(f"{cfg.app_name(app.exe)} ({app.exe})").setData(app.exe)
        if not seen:
            menu.addAction(NOTHING_NEW).setEnabled(False)
        menu.triggered.connect(self._picked)
        self.menu = menu
        menu.popup(self.add_button.mapToGlobal(QPoint(0, self.add_button.height())))
        return menu

    def _picked(self, action: QAction) -> None:
        exe = action.data()
        if isinstance(exe, str) and exe:
            self.add_app(exe)


class AppsEditor(SettingEditor):
    """Uma das duas chaves da lista (`index` 0: `detect.apps`; 1: `detect.off`), lida da tela; sem mexer, o texto salvo."""
    typed = False

    def __init__(self, key: str, raw: str, apps: AppsList, index: int) -> None:
        super().__init__(key, raw)
        self.apps, self.index = apps, index
        self.widget = apps
        self.shown = self.value()

    @override
    def value(self) -> str:
        return self.apps.lists()[self.index]

    @override
    def show(self, raw: str) -> None:
        return None                                  # a lista nasce uma vez, com as duas chaves

    @override
    def shown_text(self) -> str:
        return self.value()


def _form(parent: QWidget) -> QFormLayout:
    """Um formulário como o da página (`SettingsPage.form`), dentro do cartão: o que o "Avisar…" desligado apaga."""
    form = QFormLayout(parent)
    form.setContentsMargins(0, 0, 0, 0)
    form.setVerticalSpacing(METRICS.form_row)
    form.setHorizontalSpacing(FORM_SPACING)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    return form


def _suffix(field: QWidget, text: str) -> None:
    """O texto depois do spin ("s com o microfone em uso"), na mesma linha (o `add_row` pôs o spin numa caixa com folga)."""
    holder = field.parentWidget()
    layout = holder.layout() if holder is not None else None
    if isinstance(layout, QHBoxLayout):
        layout.insertWidget(1, QLabel(text))


class DetectCardMixin:
    """O cartão "Reunião sem gravação" no `SettingsDialog`."""
    store: Store
    platform: Platform
    _editors: dict[str, SettingEditor]

    def _add(self, page: SettingsPage, form: QFormLayout, key: str, editor: SettingEditor | None = None,
             badge: tuple[str, Tone] | None = None, *, compact: bool = False) -> SettingEditor:
        raise NotImplementedError

    def _detect_card(self, page: SettingsPage) -> None:
        form = page.form(DETECT_TITLE)
        enabled = self._add(page, form, cfg.ENABLED)
        form.addRow(wrap_note(HELP))
        self.detect_body = QWidget()
        inner = _form(self.detect_body)
        form.addRow(self.detect_body)
        seconds = self._add(page, inner, cfg.SECONDS)
        seconds.widget.setToolTip("Quanto tempo seguido um app de reunião fica com o microfone aberto antes do aviso (de 3 a "
                                  "120 s). Outros apps esperam 20 s, e só ouvindo (webinar), 45 s.")
        _suffix(seconds.widget, SECONDS_TAIL)
        self._add(page, inner, cfg.REMIND)
        apps_raw, off_raw = (self.store.get_setting(key) for key in (cfg.APPS, cfg.OFF))
        self.detect_apps = AppsList(self.platform, self.platform.watched_apps(), apps_raw, off_raw)
        inner.addRow(APPS_LABEL, self.detect_apps)
        self._editors[cfg.APPS] = AppsEditor(cfg.APPS, apps_raw, self.detect_apps, 0)
        self._editors[cfg.OFF] = AppsEditor(cfg.OFF, off_raw, self.detect_apps, 1)
        self._add(page, inner, cfg.OTHER_APPS)
        if isinstance(enabled, BoolEditor):
            enabled.box.toggled.connect(self._detect_toggled)
            self._detect_toggled(enabled.box.isChecked())

    def _detect_toggled(self, on: bool) -> None:
        self.detect_body.setEnabled(on)


__all__ = ["ADD_TEXT", "APPS_LABEL", "DETECT_TITLE", "HELP", "OFF_NOTE", "AppsEditor", "AppsList", "DetectCardMixin"]
