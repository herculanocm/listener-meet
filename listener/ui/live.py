"""Janela "Ao vivo" (F4 U-021 a U-023): não modal, arrastável (janela comum), com "sempre visível" lembrado e posição e
tamanho guardados como os do widget (`ui.live_geometry`, presos a um monitor existente). Acima, o resumo parcial atual
(`summaries` kind `partial`) com a hora do último e "Atualizar resumo agora"; as possíveis decisões e ações dele têm
"marcar" (U-023: viram marcador "Decisão: ..." ou "Ação: ..." no `t_ms` do item, ou no fim do parcial). Abaixo, as
legendas rolando com tempo, rótulo e texto: ao abrir, carrega do banco; a cada `live_caption`, lê só as novas
(`after_id`), na ordem do tempo, e só rola sozinha quando já estava no fim. Clique numa legenda cria um marcador no
instante dela com o texto (U-022), uma vez por legenda. Abre pelo clique no widget e pela bandeja, nunca sozinha (Q5);
depois de Parar ou Cancelar fica só para leitura até o usuário fechar. Marcar e atualizar passam pelo controlador
(callbacks): a janela não conhece o gravador."""
from __future__ import annotations

import bisect
import html
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QGuiApplication, QMoveEvent, QResizeEvent
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSplitter, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..config.defaults import SPECS
from ..llm.prompts.common import stamp
from ..log import get_logger
from ..model import SummaryKind
from .common import clamp_to_screens
from .theme import tokens
from .widget import LIVE_BADGES, _screen_rects

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("ui.live")

NOTE_MAX = 300                    # nota do marcador criado de uma legenda ou de um item do parcial
DEFAULT_SIZE = (460, 600)
MIN_SIZE = (320, 320)
SAVE_DELAY_MS = 600
KIND_PREFIX = {"decisions": "Decisão", "actions": "Ação"}
KIND_TITLE = {"decisions": "Possíveis decisões", "actions": "Possíveis ações"}
STATE_TEXT = {"off": "legenda ao vivo desligada nesta gravação", "unavailable": "legenda ao vivo indisponível nesta gravação",
              "stopping": "gravação parada: terminando as últimas legendas", "stopped": "ao vivo encerrado"}

type MarkCaption = Callable[[Mapping[str, Any]], tuple[bool, str]]
type MarkItem = Callable[[str, Mapping[str, Any], int | None], tuple[bool, str]]
type Refresh = Callable[[], str]


def clip(text: str, limit: int = NOTE_MAX) -> str:
    """Espaços simples e no máximo `limit` caracteres (com reticências)."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def caption_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Identidade de uma legenda para não marcar duas vezes: o id do banco ou, sem ele, canal e tempo."""
    if row.get("id") is not None:
        return ("id", int(row["id"]))
    return ("t", str(row.get("channel") or ""), int(row.get("t_start_ms") or 0))


def caption_note(row: Mapping[str, Any]) -> str:
    """Nota do marcador de uma legenda (U-022): "Ana — CFO: texto" cortado em ~300 caracteres."""
    label, text = row.get("label"), str(row.get("text") or "")
    return clip(f"{label}: {text}" if label else text)


def caption_line(row: Mapping[str, Any]) -> str:
    label = row.get("label")
    text = " ".join(str(row.get("text") or "").split())
    return f"[{stamp(int(row.get('t_start_ms') or 0))}] " + (f"{label}: {text}" if label else text)


def item_key(kind: str, item: Mapping[str, Any]) -> tuple[str, str]:
    return kind, " ".join(str(item.get("text") or "").casefold().split())


def item_note(kind: str, item: Mapping[str, Any]) -> str:
    """"Decisão: ..." ou "Ação: ... (dono)" cortado em ~300 caracteres (U-023)."""
    owner = str(item.get("owner") or "").strip() if kind == "actions" else ""
    return clip(f"{KIND_PREFIX.get(kind, 'Item')}: {str(item.get('text') or '').strip()}" + (f" ({owner})" if owner else ""))


def partial_data(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """`data_json` do parcial (resumo, decisões e ações); vazio quando ilegível."""
    if not row:
        return {}
    try:
        data = json.loads(str(row.get("data_json") or "{}"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def partial_html(data: Mapping[str, Any], marked: set[tuple[str, str]], can_mark: bool) -> str:
    """O parcial como HTML: resumo e as listas com "marcar" (link `mark:<espécie>:<índice>`) ou "marcado"."""
    parts = [f"<p>{html.escape(str(data.get('summary') or '')).replace(chr(10), '<br>') or '<i>(sem texto)</i>'}</p>"]
    for kind, title in KIND_TITLE.items():
        items = [it for it in data.get(kind) or () if isinstance(it, Mapping)]
        if not items:
            continue
        parts.append(f"<p><b>{title}</b></p><ul>")
        for i, it in enumerate(items):
            t = it.get("t_ms")
            when = f"[{stamp(int(t))}] " if isinstance(t, int | float) else ""
            owner = f" <i>({html.escape(str(it['owner']))})</i>" if kind == "actions" and it.get("owner") else ""
            if item_key(kind, it) in marked:
                tail = ' · <span style="color:#27ae60">marcado ✓</span>'
            elif can_mark:
                tail = f' · <a href="mark:{kind}:{i}">marcar</a>'
            else:
                tail = ""
            parts.append(f"<li>{when}{html.escape(str(it.get('text') or ''))}{owner}{tail}</li>")
        parts.append("</ul>")
    return "".join(parts)


def hhmm(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%H:%M")
    except (TypeError, ValueError):
        return "?"


class LiveWindow(QWidget):
    closed = Signal()

    def __init__(self, store: Store, meeting_id: int, *, on_mark_caption: MarkCaption | None = None,
                 on_mark_item: MarkItem | None = None, on_refresh: Refresh | None = None,
                 marked_captions: set[tuple[Any, ...]] | None = None, marked_items: set[tuple[str, str]] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.store = store
        self.meeting_id = meeting_id
        self.on_mark_caption = on_mark_caption
        self.on_mark_item = on_mark_item
        self.on_refresh = on_refresh
        self.marked_captions = marked_captions if marked_captions is not None else set()   # do controlador: sobrevive a fechar
        self.marked_items = marked_items if marked_items is not None else set()
        self.finished = False
        self.last_id = 0
        self._keys: list[tuple[int, int]] = []               # (t_start_ms, id) de cada linha, em ordem: inserção por bisect
        self.partial: dict[str, Any] = {}
        self.partial_row: dict[str, Any] | None = None
        m = store.get_meeting(meeting_id) or {}
        self.setWindowTitle(f"Ao vivo — {m.get('title') or f'reunião {meeting_id}'}")
        self.setMinimumSize(*MIN_SIZE)

        # --- resumo parcial ---
        self.partial_title = QLabel("<b>Resumo parcial</b>")
        self.partial_when = QLabel("")
        self.partial_when.setStyleSheet("color: #888; font-size: 11px;")
        self.refresh_btn = QPushButton("Atualizar resumo agora")
        self.refresh_btn.clicked.connect(self._refresh)
        self.refresh_btn.setVisible(on_refresh is not None)
        self.partial_view = QTextBrowser()
        self.partial_view.setOpenLinks(False)
        self.partial_view.setOpenExternalLinks(False)
        self.partial_view.anchorClicked.connect(self._anchor)
        self.partial_when.setWordWrap(True)
        head = QHBoxLayout()
        head.addWidget(self.partial_title, 1)
        head.addWidget(self.refresh_btn)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addLayout(head)
        tl.addWidget(self.partial_when)                   # numa linha própria: não corta na largura padrão
        tl.addWidget(self.partial_view, 1)

        # --- legendas ---
        self.live_label = QLabel("")
        self.live_label.setStyleSheet("font-size: 11px;")
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(False)
        self.list.itemClicked.connect(self._caption_clicked)
        chead = QHBoxLayout()
        chead.addWidget(QLabel("<b>Legendas</b> <span style='color:#888'>(clique numa legenda para marcar o instante)</span>"), 1)
        chead.addWidget(self.live_label)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addLayout(chead)
        bl.addWidget(self.list, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        self.on_top = QCheckBox("Sempre visível")
        self.on_top.setChecked(bool(store.get(SPECS["ui.live_on_top"])))
        self.on_top.toggled.connect(self._set_on_top)
        self.status = QLabel("")
        self.status.setStyleSheet("color: #888; font-size: 11px;")
        foot = QHBoxLayout()
        foot.addWidget(self.status, 1)
        foot.addWidget(self.on_top)
        lay = QVBoxLayout(self)
        lay.addWidget(splitter, 1)
        lay.addLayout(foot)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save_geometry)
        self._restoring = True
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.on_top.isChecked())
        self._restore_geometry()
        self._restoring = False
        self.reload_partial()
        self.load_new()

    # ---------------------------------------------------------------- legendas
    def on_caption(self, msg: object = None) -> None:
        """Sinal `live_caption`: lê só as legendas novas desta reunião no banco (o filho já gravou antes de emitir)."""
        try:
            self.load_new()
        except Exception as e:                           # a reunião sumiu (Cancelar) ou o banco fechou: a janela fica como está
            log.debug("janela ao vivo: legendas não lidas: %s", e)

    def load_new(self) -> int:
        rows = self.store.captions.list_for_meeting(self.meeting_id, after_id=self.last_id)
        if not rows:
            return 0
        bar = self.list.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        for r in rows:
            self._add(dict(r))
            self.last_id = max(self.last_id, int(r["id"]))
        if at_end:
            self.list.scrollToBottom()
        return len(rows)

    def _add(self, row: dict[str, Any]) -> None:
        key = (int(row.get("t_start_ms") or 0), int(row.get("id") or 0))
        pos = bisect.bisect_right(self._keys, key)            # o filho grava por fim de janela: L e R podem chegar fora de ordem
        self._keys.insert(pos, key)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, row)
        latency = row.get("latency_ms")
        delay = f" · atraso {int(latency) / 1000:.1f} s".replace(".", ",") if latency is not None else ""   # vírgula (U-032)
        tip = f"canal {row.get('channel') or '?'}{delay}"
        item.setToolTip(f"{tip}\nclique: marcador neste instante com o texto da legenda")
        self._paint(item, row)
        self.list.insertItem(pos, item)

    def _paint(self, item: QListWidgetItem, row: Mapping[str, Any]) -> None:
        marked = caption_key(row) in self.marked_captions
        item.setText(("⚑ " if marked else "") + caption_line(row))
        if marked:
            item.setForeground(QColor(tokens().accent))        # F5.4 (A-11): a legenda marcada no destaque do tema

    @property
    def captions(self) -> list[dict[str, Any]]:
        """As linhas na ordem da tela (instrumentação dos testes)."""
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def _caption_clicked(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(row, dict):
            return
        if self.finished or self.on_mark_caption is None:
            self.status.setText("gravação encerrada: a janela é só para leitura")
            return
        if caption_key(row) in self.marked_captions:
            self.status.setText("esta legenda já virou marcador")
            return
        ok, message = self.on_mark_caption(row)
        self.status.setText(message)
        if ok:
            self._paint(item, row)

    # ---------------------------------------------------------------- resumo parcial
    def reload_partial(self) -> None:
        try:
            row = self.store.summaries.current(self.meeting_id, SummaryKind.PARTIAL)
        except Exception as e:
            log.debug("janela ao vivo: parcial não lido: %s", e)
            return
        self.partial_row = dict(row) if row else None
        self.partial = partial_data(row)
        self._render_partial()

    def _render_partial(self) -> None:
        row = self.partial_row
        if row is None:
            interval = int(self.store.get(SPECS["live.summary_interval_min"]))
            self.partial_when.setText("")
            self.partial_view.setHtml("<p style='color:#888'>Ainda sem resumo parcial: " +
                                      (f"o primeiro sai em até {interval} min de legendas." if interval > 0 else
                                       "o automático está desligado (intervalo 0 nas Configurações, Ao vivo).") + "</p>")
            return
        until = row.get("t_until_ms")
        self.partial_when.setText(f"atualizado às {hhmm(row.get('created_at'))}"
                                  + (f" · até [{stamp(int(until))}] da reunião" if until is not None else ""))
        self.partial_view.setHtml(partial_html(self.partial, self.marked_items, not self.finished and self.on_mark_item is not None))

    def _anchor(self, url: QUrl) -> None:
        parts = url.toString().split(":")
        if len(parts) != 3 or parts[0] != "mark" or parts[1] not in KIND_TITLE:
            return
        kind = parts[1]
        try:
            item = (self.partial.get(kind) or [])[int(parts[2])]
        except (ValueError, IndexError):
            return
        if self.finished or self.on_mark_item is None or not isinstance(item, Mapping):
            return
        until = (self.partial_row or {}).get("t_until_ms")
        ok, message = self.on_mark_item(kind, item, int(until) if until is not None else None)
        self.status.setText(message)
        if ok:
            self._render_partial()

    def _refresh(self) -> None:
        if self.on_refresh is not None and not self.finished:
            self.status.setText(self.on_refresh())

    def on_partial_state(self, state: object) -> None:
        """Estado do resumo parcial vindo do controlador: atualizando, pronto (recarrega), falhou, parado, nada novo."""
        st = dict(state) if isinstance(state, Mapping) else {}
        if st.get("meeting_id") not in (None, self.meeting_id):
            return
        match st.get("state"):
            case "running":
                self.status.setText("atualizando o resumo parcial…")
                self.refresh_btn.setEnabled(False)
            case "done":
                self.reload_partial()
                self.status.setText("resumo parcial atualizado")
                self.refresh_btn.setEnabled(not self.finished)
            case "stopped":
                self.status.setText(str(st.get("message") or "resumo parcial parado nesta reunião"))
                self.refresh_btn.setEnabled(False)
            case _:
                if st.get("message"):
                    self.status.setText(str(st["message"]))
                self.refresh_btn.setEnabled(not self.finished)

    # ---------------------------------------------------------------- estado
    def set_live_state(self, state: str) -> None:
        badge, t = LIVE_BADGES.get(state), tokens()
        text, color = (badge[0], t.tone(badge[1])) if badge else (STATE_TEXT.get(state, ""), t.secondary)
        self.live_label.setText(f"<span style='color:{color}'>{html.escape(text)}</span>" if text else "")

    def set_finished(self, text: str = "gravação encerrada: só leitura") -> None:
        """Parar ou Cancelar: fica aberta para leitura, sem marcar nem atualizar."""
        self.finished = True
        self.refresh_btn.setEnabled(False)
        self.status.setText(text)
        self._render_partial()

    # ---------------------------------------------------------------- janela
    def _set_on_top(self, on: bool) -> None:
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)     # trocar a flag esconde a janela: mostra de novo
        if visible:
            self.show()
        with suppress(Exception):
            self.store.set_setting("ui.live_on_top", "1" if on else "0")

    def _restore_geometry(self) -> None:
        raw = self.store.get_setting("ui.live_geometry")
        pos: tuple[int, int] | None = None
        try:
            x, y, w, h = (int(v) for v in raw.split(","))
            pos = (x, y)
        except ValueError:
            w, h = DEFAULT_SIZE
        w, h = max(MIN_SIZE[0], w), max(MIN_SIZE[1], h)
        self.resize(w, h)
        if pos is not None:
            clamped = clamp_to_screens(pos[0], pos[1], w, h, _screen_rects())
            if clamped is not None:
                self.move(*clamped)
                return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - w - 24, screen.top() + 60)

    def save_geometry(self) -> None:
        g = self.geometry()
        with suppress(Exception):                        # banco já fechado na saída do app: a posição anterior fica
            self.store.set_setting("ui.live_geometry", f"{g.x()},{g.y()},{g.width()},{g.height()}")

    @override
    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        if not self._restoring and self.isVisible():
            self._save_timer.start()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if not self._restoring and self.isVisible():
            self._save_timer.start()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_timer.stop()
        self.save_geometry()
        super().closeEvent(event)
        self.closed.emit()
