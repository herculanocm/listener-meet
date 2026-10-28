"""F5.3 L4 (U-049): o painel do botão 🎧 do widget. Microfone (canal L): "Padrão do Windows (acompanha)" e cada microfone
ativo; saída gravada (canal R): a fonte atual quando é um app, "Sistema inteiro: saída padrão (acompanha)", "Sistema
inteiro: <saída>" (com "(comunicação)" quando o padrão de comunicação difere, A-20) e "Outro app…" (o "Trocar fonte" de
antes). O que chegou depois do começo da gravação vem "novo" (A-19); com a fonte de um app, uma linha diz que o som dele é
captado em qualquer saída; microfone com "Hands-Free" no nome avisa do perfil de chamada, mono (item 19: pelo nome é
palpite). Sem o Core Audio, a lista dos microfones é a do PortAudio, sem "novo". U-050: as saídas do mesmo aparelho
(mesmo ContainerId) do microfone novo vêm primeiro, a de comunicação antes. O clique numa linha pede a troca a quem abriu
o painel (`ui/recording_devices.py`), que devolve o resultado na linha de estado; Esc fecha. As linhas são montadas sem
Qt (`mic_rows`, `output_rows`); os nomes dos aparelhos só aparecem aqui, na tela (A-22). R-06: os sinais das listas vão a
métodos do próprio painel e o adiamento da troca tem o painel como contexto (um `partial` com `self` ficava preso do lado
C++ do PySide, num ciclo que o coletor do Python não vê, e o painel vivia até a derrubada do Qt).
F5.4 L2 (U-064): o microfone (L) e a saída (R) lado a lado, para o painel caber no notebook a 150 % com as linhas em duas (o
nome do aparelho e, embaixo, em nota, o tipo: microfone, sistema inteiro, app); o atual com a marca de "em uso" no lugar do
"●", o "novo" num selo e o resultado da troca numa faixa (`InfoBar`: trocando, trocado ou o erro). A linha é pintada pelo
`DeviceRowDelegate`; o texto da linha (`Row.text`) segue no `DisplayRole` para o leitor de tela e na dica."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget)

from ..model import SourceKind
from ..platform.base import AudioDevice, Endpoint, Endpoints
from . import icons
from .components import BADGE_PAD_X, BADGE_PAD_Y, InfoBar
from .style import rounded
from .theme import Tokens, font_for, note, qcolor, surface_tokens

if TYPE_CHECKING:
    from ..capture.recorder import SourceSpec

MIC_FOLLOW, OUT_FOLLOW, OTHER_APP, APP_ROW = "mic:", "out:", "other", "app"
FOLLOW_MIC_TEXT = "Padrão do Windows (acompanha)"
FOLLOW_OUT_TEXT = "Sistema inteiro: saída padrão (acompanha)"
NEW_MARK = " · novo"
HANDS_FREE_NOTE = "Hands-Free: com este microfone o fone vai para o perfil de chamada (som mono)."
APP_NOTE = "A fonte é um app: o som dele é captado em qualquer saída (trocar a saída do Windows não muda a gravação)."
COMM_TIP = "A saída de comunicação do Windows: a chamada costuma tocar nela."
NO_CORE_AUDIO = "O Windows não respondeu: os microfones são os do sistema de áudio, sem marcar os novos."
CURRENT_ROLE = Qt.ItemDataRole.UserRole + 1
NAME_ROLE, DETAIL_ROLE, NEW_ROLE = (Qt.ItemDataRole.UserRole + n for n in (2, 3, 4))    # U-064: as duas linhas e o selo
NEW_BADGE = "Novo"
ROW_PAD, LIST_MIN_W = 6, 360            # U-064: a folga das duas linhas e a largura de cada lista
MIC_DETAIL, SYSTEM_DETAIL = "Microfone", "Sistema inteiro"


@dataclass(frozen=True, slots=True)
class Row:
    key: str                     # MIC_FOLLOW, "mic:<id>", OUT_FOLLOW, "out:<id>", APP_ROW ou OTHER_APP
    text: str                    # a linha inteira (o leitor de tela e a dica)
    current: bool = False        # o que a gravação pediu agora
    tip: str = ""
    name: str = ""               # U-064: a primeira linha (sem nome, o `text`)
    detail: str = ""             # e a segunda, em nota: o tipo
    new: bool = False            # chegou depois do começo da gravação: o selo "Novo"


@dataclass(frozen=True, slots=True)
class PanelView:
    mics: tuple[Row, ...]
    outputs: tuple[Row, ...]
    notes: tuple[str, ...] = ()


def is_hands_free(name: str) -> bool:
    """Item 19: o microfone de um fone Bluetooth no perfil de chamada (HFP); pelo nome é palpite."""
    low = name.lower()
    return "hands-free" in low or "handsfree" in low


def _now(prefix: str, ep: Endpoint | None) -> str:
    return f"{prefix}: agora {ep.name}" if ep is not None and ep.name else prefix


def _now_detail(kind: str, ep: Endpoint | None) -> str:
    return f"{kind} · agora {ep.name}" if ep is not None and ep.name else kind


def mic_rows(reading: Endpoints | None, pa_devices: Sequence[AudioDevice], wanted: AudioDevice | None,
             start_ids: frozenset[str]) -> tuple[Row, ...]:
    """As linhas do microfone: "acompanha" e cada microfone (do Core Audio; sem ele, do PortAudio e sem "novo"); o
    escolhido que saiu fica na lista, marcado."""
    if reading is not None:
        items = [(e.id, e.name or e.id, e.id not in start_ids) for e in reading.capture]
        default = reading.mic(reading.capture_default)
    else:
        items = [(d.id, d.name, False) for d in pa_devices]
        default = None
    rows = [Row(MIC_FOLLOW, _now(FOLLOW_MIC_TEXT, default), current=wanted is None, name=FOLLOW_MIC_TEXT,
                detail=_now_detail(MIC_DETAIL, default))]
    for eid, name, new in items:
        hands_free = is_hands_free(name)
        rows.append(Row(f"mic:{eid}", name + (NEW_MARK if new else ""), current=wanted is not None and wanted.id == eid,
                        tip=HANDS_FREE_NOTE if hands_free else "", name=name,
                        detail=MIC_DETAIL + (" · perfil de chamada (mono)" if hands_free else ""), new=new))
    if wanted is not None and all(eid != wanted.id for eid, _, _ in items):
        rows.append(Row(f"mic:{wanted.id}", f"{wanted.name or wanted.id} (saiu: gravando no padrão)", current=True,
                        name=wanted.name or wanted.id, detail="saiu: gravando no padrão"))
    return tuple(rows)


def output_rows(reading: Endpoints | None, source: SourceSpec, start_ids: frozenset[str],
                first_container: str = "") -> tuple[Row, ...]:
    """As linhas da saída gravada; com `first_container` (U-050), as saídas desse aparelho primeiro, a de comunicação
    antes."""
    rows: list[Row] = []
    if source.kind == SourceKind.APP:
        app = source.label or source.exe or "app"
        rows.append(Row(APP_ROW, f"{app} (a fonte atual)", current=True, tip=APP_NOTE, name=app, detail="App: a fonte atual"))
    outs = list(reading.render) if reading is not None else []
    first = [e for e in outs if first_container and e.container == first_container]
    if reading is not None:
        first.sort(key=lambda e: (e.id != reading.render_comm, not is_hands_free(e.name)))
    rows += [_output_row(e, reading, source, start_ids) for e in first]
    system = source.kind == SourceKind.SYSTEM
    default = reading.output(reading.render_default) if reading is not None else None
    rows.append(Row(OUT_FOLLOW, _now(FOLLOW_OUT_TEXT, default), current=system and source.follows,
                    name="Saída padrão (acompanha)", detail=_now_detail(SYSTEM_DETAIL, default)))
    rows += [_output_row(e, reading, source, start_ids) for e in outs if e not in first]
    if system and source.output_id and (reading is None or reading.output(source.output_id) is None):
        gone = source.label or source.output_id
        rows.append(Row(f"out:{source.output_id}", f"{gone} (saiu: gravando a padrão)", current=True, name=gone,
                        detail="saiu: gravando a padrão"))
    rows.append(Row(OTHER_APP, "Outro app…", tip="Gravar só o som de um app (o Trocar fonte)", name="Outro app…",
                    detail="Gravar só o som de um app"))
    return tuple(rows)


def _output_row(e: Endpoint, reading: Endpoints | None, source: SourceSpec, start_ids: frozenset[str]) -> Row:
    comm = reading is not None and e.id == reading.render_comm and reading.render_comm != reading.render_default
    new = e.id not in start_ids
    text = f"Sistema inteiro: {e.name or e.id}" + (" (comunicação)" if comm else "") + (NEW_MARK if new else "")
    current = source.kind == SourceKind.SYSTEM and source.output_id == e.id
    return Row(f"out:{e.id}", text, current=current, tip=COMM_TIP if comm else "", name=e.name or e.id,
               detail=SYSTEM_DETAIL + (" (comunicação)" if comm else ""), new=new)


def panel_notes(reading: Endpoints | None, source: SourceSpec, mics: Sequence[Row]) -> tuple[str, ...]:
    notes: list[str] = []
    if reading is None:
        notes.append(NO_CORE_AUDIO)
    if source.kind == SourceKind.APP:
        notes.append(APP_NOTE)
    if any(r.tip == HANDS_FREE_NOTE for r in mics):
        notes.append(HANDS_FREE_NOTE)
    return tuple(notes)


class DeviceRowDelegate(QStyledItemDelegate):
    """U-064: a linha em duas (o nome em corpo, o tipo em nota), a marca de "em uso" à esquerda do atual (o nome dele em
    semibold) e o selo "Novo" à direita; o fundo, a seleção e o foco são os do estilo (A-05). O `DisplayRole` fica com o texto
    inteiro, para o leitor de tela."""
    PAD, MARK = 8, 24

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        t = surface_tokens(opt.widget)
        r = opt.rect.adjusted(self.PAD, 4, -self.PAD, -4)
        current, new = bool(index.data(CURRENT_ROLE)), bool(index.data(NEW_ROLE))
        name = str(index.data(NAME_ROLE) or index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.save()
        if current:
            dpr = painter.device().devicePixelRatioF() if painter.device() is not None else 1.0
            mark = icons.pixmap("check", t.accent, 16, dpr)
            painter.drawPixmap(r.left() + 2, r.top() + (r.height() - 16) // 2, mark)
        text = QRect(r.left() + self.MARK, r.top(), r.width() - self.MARK, r.height())
        if new:
            text.setRight(text.right() - self._badge(painter, text, t) - 8)
        self._lines(painter, text, name, str(index.data(DETAIL_ROLE) or ""), current, t)
        painter.restore()

    @staticmethod
    def _lines(painter: QPainter, r: QRect, name: str, detail: str, current: bool, t: Tokens) -> None:
        top_font = font_for("body_strong" if current else "body")
        sub_font = font_for("caption")
        top, sub = QFontMetrics(top_font), QFontMetrics(sub_font)
        y = r.top() + (r.height() - top.height() - (sub.height() if detail else 0)) // 2
        painter.setFont(top_font)
        painter.setPen(qcolor(t.text))
        painter.drawText(QRect(r.left(), y, r.width(), top.height()), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         top.elidedText(name, Qt.TextElideMode.ElideMiddle, r.width()))
        if detail:
            painter.setFont(sub_font)
            painter.setPen(qcolor(t.secondary))
            painter.drawText(QRect(r.left(), y + top.height(), r.width(), sub.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             sub.elidedText(detail, Qt.TextElideMode.ElideRight, r.width()))

    @staticmethod
    def _badge(painter: QPainter, r: QRect, t: Tokens) -> int:
        """O selo "Novo" no canto direito da linha; devolve a largura dele."""
        font = font_for("caption")
        fm = QFontMetrics(font)
        width, height = fm.horizontalAdvance(NEW_BADGE) + 2 * BADGE_PAD_X, fm.height() + 2 * BADGE_PAD_Y
        pill = QRectF(r.right() - width, r.center().y() - height / 2, width, height)
        rounded(painter, pill, height / 2, t.info_bg)
        painter.setFont(font)
        painter.setPen(qcolor(t.info))
        painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), NEW_BADGE)
        return int(width)

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        lines = QFontMetrics(font_for("body")).height() + QFontMetrics(font_for("caption")).height() + ROW_PAD
        return QSize(size.width(), max(surface_tokens(None).metrics.list_item_h, lines))


class DevicesPanel(QDialog):
    """O painel (não modal, por cima de tudo como o widget): duas listas; o clique numa linha que não é a atual emite a
    chave dela, uma troca por vez; Esc fecha."""
    mic_chosen = Signal(str)
    output_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Dispositivos da gravação")
        self.view: PanelView | None = None                 # instrumentação dos testes
        self._busy = False
        self._pending: tuple[str, str] | None = None       # ("mic" ou "out", a chave) à espera do adiamento
        self.mics = self._list()
        self.outputs = self._list()
        self.mics.itemClicked.connect(self._on_mic_item)
        self.mics.itemActivated.connect(self._on_mic_item)              # Enter pelo teclado
        self.outputs.itemClicked.connect(self._on_output_item)
        self.outputs.itemActivated.connect(self._on_output_item)
        mic_box = QGroupBox("Microfone (canal L: você)")
        QVBoxLayout(mic_box).addWidget(self.mics)
        out_box = QGroupBox("Saída gravada (canal R: quem você ouve)")
        QVBoxLayout(out_box).addWidget(self.outputs)
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        note(self.notes)
        self.status_bar = InfoBar("success")             # U-064: o resultado numa faixa
        self.status = self.status_bar.label
        self.status_bar.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        columns = QHBoxLayout()
        columns.setSpacing(surface_tokens(None).metrics.card_gap)
        columns.addWidget(mic_box, 1, Qt.AlignmentFlag.AlignTop)
        columns.addWidget(out_box, 1, Qt.AlignmentFlag.AlignTop)
        lay = QVBoxLayout(self)
        lay.addLayout(columns)
        for w in (self.notes, self.status_bar, buttons):
            lay.addWidget(w)

    @staticmethod
    def _list() -> QListWidget:
        lst = QListWidget()
        lst.setTextElideMode(Qt.TextElideMode.ElideMiddle)        # nomes longos: o começo e as marcas do fim ficam à vista
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.setItemDelegate(DeviceRowDelegate(lst))
        lst.setMinimumWidth(LIST_MIN_W)
        return lst

    def show_view(self, view: PanelView) -> None:
        self.view = view
        self._fill(self.mics, view.mics)
        self._fill(self.outputs, view.outputs)
        self.notes.setText("\n".join(view.notes))
        self.notes.setVisible(bool(view.notes))

    @staticmethod
    def _fill(lst: QListWidget, rows: Sequence[Row]) -> None:
        lst.clear()
        for r in rows:
            item = QListWidgetItem(r.text + (" (em uso)" if r.current else ""))     # o que o leitor de tela lê
            item.setData(Qt.ItemDataRole.UserRole, r.key)
            item.setData(CURRENT_ROLE, r.current)
            item.setData(NAME_ROLE, r.name or r.text)
            item.setData(DETAIL_ROLE, r.detail)
            item.setData(NEW_ROLE, r.new)
            item.setToolTip(f"{r.text}\n{r.tip}" if r.tip else r.text)          # o nome inteiro, cortado ou não
            lst.addItem(item)
        rows_h = sum(lst.sizeHintForRow(i) for i in range(lst.count()))
        lst.setFixedHeight(rows_h + 2 * lst.frameWidth() + 8)

    def set_status(self, text: str, error: bool = False, busy: bool = False) -> None:
        """O resultado na faixa (U-064): o erro em vermelho, o "trocando…" em informação e o trocado em sucesso."""
        self.status_bar.set_kind("error" if error else "info" if busy else "success")
        self.status_bar.set_text(text)
        self.status_bar.setVisible(bool(text))

    def _on_mic_item(self, item: QListWidgetItem) -> None:
        self._choose("mic", item)

    def _on_output_item(self, item: QListWidgetItem) -> None:
        self._choose("out", item)

    def _choose(self, which: str, item: QListWidgetItem) -> None:
        key = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if self._busy or not key or key == APP_ROW or item.data(CURRENT_ROLE):
            return
        self._busy = True
        self._pending = (which, key)
        self.set_status("trocando…", busy=True)
        QTimer.singleShot(0, self, self._emit_pending)     # a linha de estado aparece antes; apagado o painel, nada chama

    def _emit_pending(self) -> None:
        pending, self._pending = self._pending, None
        try:
            if pending is not None:
                (self.mic_chosen if pending[0] == "mic" else self.output_chosen).emit(pending[1])
        finally:
            self._busy = False
