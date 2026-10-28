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
C++ do PySide, num ciclo que o coletor do Python não vê, e o painel vivia até a derrubada do Qt)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGroupBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout,
                               QWidget)

from ..model import SourceKind
from ..platform.base import AudioDevice, Endpoint, Endpoints

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


@dataclass(frozen=True, slots=True)
class Row:
    key: str                     # MIC_FOLLOW, "mic:<id>", OUT_FOLLOW, "out:<id>", APP_ROW ou OTHER_APP
    text: str
    current: bool = False        # o que a gravação pediu agora
    tip: str = ""


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
    rows = [Row(MIC_FOLLOW, _now(FOLLOW_MIC_TEXT, default), current=wanted is None)]
    for eid, name, new in items:
        rows.append(Row(f"mic:{eid}", name + (NEW_MARK if new else ""), current=wanted is not None and wanted.id == eid,
                        tip=HANDS_FREE_NOTE if is_hands_free(name) else ""))
    if wanted is not None and all(eid != wanted.id for eid, _, _ in items):
        rows.append(Row(f"mic:{wanted.id}", f"{wanted.name or wanted.id} (saiu: gravando no padrão)", current=True))
    return tuple(rows)


def output_rows(reading: Endpoints | None, source: SourceSpec, start_ids: frozenset[str],
                first_container: str = "") -> tuple[Row, ...]:
    """As linhas da saída gravada; com `first_container` (U-050), as saídas desse aparelho primeiro, a de comunicação
    antes."""
    rows: list[Row] = []
    if source.kind == SourceKind.APP:
        rows.append(Row(APP_ROW, f"{source.label or source.exe or 'app'} (a fonte atual)", current=True, tip=APP_NOTE))
    outs = list(reading.render) if reading is not None else []
    first = [e for e in outs if first_container and e.container == first_container]
    if reading is not None:
        first.sort(key=lambda e: (e.id != reading.render_comm, not is_hands_free(e.name)))
    rows += [_output_row(e, reading, source, start_ids) for e in first]
    system = source.kind == SourceKind.SYSTEM
    default = reading.output(reading.render_default) if reading is not None else None
    rows.append(Row(OUT_FOLLOW, _now(FOLLOW_OUT_TEXT, default), current=system and source.follows))
    rows += [_output_row(e, reading, source, start_ids) for e in outs if e not in first]
    if system and source.output_id and (reading is None or reading.output(source.output_id) is None):
        rows.append(Row(f"out:{source.output_id}", f"{source.label or source.output_id} (saiu: gravando a padrão)", current=True))
    rows.append(Row(OTHER_APP, "Outro app…", tip="Gravar só o som de um app (o Trocar fonte)"))
    return tuple(rows)


def _output_row(e: Endpoint, reading: Endpoints | None, source: SourceSpec, start_ids: frozenset[str]) -> Row:
    comm = reading is not None and e.id == reading.render_comm and reading.render_comm != reading.render_default
    text = f"Sistema inteiro: {e.name or e.id}" + (" (comunicação)" if comm else "") + (NEW_MARK if e.id not in start_ids else "")
    current = source.kind == SourceKind.SYSTEM and source.output_id == e.id
    return Row(f"out:{e.id}", text, current=current, tip=COMM_TIP if comm else "")


def panel_notes(reading: Endpoints | None, source: SourceSpec, mics: Sequence[Row]) -> tuple[str, ...]:
    notes: list[str] = []
    if reading is None:
        notes.append(NO_CORE_AUDIO)
    if source.kind == SourceKind.APP:
        notes.append(APP_NOTE)
    if any(r.tip == HANDS_FREE_NOTE for r in mics):
        notes.append(HANDS_FREE_NOTE)
    return tuple(notes)


class DevicesPanel(QDialog):
    """O painel (não modal, por cima de tudo como o widget): duas listas; o clique numa linha que não é a atual emite a
    chave dela, uma troca por vez; Esc fecha."""
    mic_chosen = Signal(str)
    output_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Dispositivos da gravação")
        self.setMinimumWidth(560)
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
        self.notes.setStyleSheet("color: #888; font-size: 11px;")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        for w in (mic_box, out_box, self.notes, self.status, buttons):
            lay.addWidget(w)

    @staticmethod
    def _list() -> QListWidget:
        lst = QListWidget()
        lst.setTextElideMode(Qt.TextElideMode.ElideMiddle)        # nomes longos: o começo e as marcas do fim ficam à vista
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
            item = QListWidgetItem(("● " if r.current else "    ") + r.text)
            item.setData(Qt.ItemDataRole.UserRole, r.key)
            item.setData(CURRENT_ROLE, r.current)
            item.setToolTip(f"{r.text}\n{r.tip}" if r.tip else r.text)          # o nome inteiro, cortado ou não
            if r.current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            lst.addItem(item)
        rows_h = sum(lst.sizeHintForRow(i) for i in range(lst.count()))
        lst.setFixedHeight(rows_h + 2 * lst.frameWidth() + 8)

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setStyleSheet(f"color: {'#e74c3c' if error else '#2ecc71'};")
        self.status.setText(text)
        self.status.setVisible(bool(text))

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
        self.set_status("trocando…")
        QTimer.singleShot(0, self, self._emit_pending)     # a linha de estado aparece antes; apagado o painel, nada chama

    def _emit_pending(self) -> None:
        pending, self._pending = self._pending, None
        try:
            if pending is not None:
                (self.mic_chosen if pending[0] == "mic" else self.output_chosen).emit(pending[1])
        finally:
            self._busy = False
