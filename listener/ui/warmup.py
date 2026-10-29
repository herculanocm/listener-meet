"""Warm-up (CP6): microfone e fonte de saída, medidores, teste de 3 s com detecção de eco (U-016), preset, título.
O microfone abre uma vez só (no `showEvent`); a fonte salva é escolhida na primeira lista de apps (M-12).
F3 (item 4, U-018): ao sair do título ou dos participantes, com 1,5 s de debounce, uma `Task` casa os participantes com
`people` e pede à LLM os termos prováveis; uma chamada em voo, resposta velha descartada; o campo "Termos prováveis" fica
editável e vai para `meetings.glossary`; sem chave, aviso discreto com link para as Configurações. Os participantes esperados
saem do casamento local mesmo sem a LLM, e uma resposta que chega depois do "Gravar" religa a chamada à reunião (F3.1 R3-11).
F4 (CP6): caixas "Legenda ao vivo" e "Nomes ao vivo"; F5.6 L1b (U-091): "Resumo parcial", de `live.partial`, fora do
preset; as três e a lógica delas em `ui/warmup_live.py` (A-21); os modelos ausentes viram a faixa com o link.
U-037 (D-053): sem o token do Hugging Face ou sem a chave da LLM ativa, um aviso acima do "Gravar" diz o que não vai sair
e leva às Chaves de acesso; some quando nada falta, sem bloquear a gravação.
U-043 (F5.2): a cada 2 s, um fone conectado ou removido com o warm-up aberto entra ou sai do combo (`ui/devices.py`).
U-045 (F5.3 L1, A-01): "Idioma da reunião", com o padrão das Configurações; vai no resultado como o glossário e a reunião
nasce com ele (transcrição, legenda e LLM). O glossário é pedido no idioma do combo; trocar o combo depois não o refaz.
F5.3 L4: o combo do microfone começa com "Padrão do Windows (acompanha)" (pergunta 11), que vira o microfone `None` da
gravação e segue o padrão do Windows (A-20); sem escolha salva, é ele. A tabela de fontes ganha "Sistema inteiro: <saída>"
por saída ativa (U-051) e a escolha fica em `warmup.last_output_id`; o teste de 3 s abre o loopback nessa saída.
F5.4 L2: duas colunas, Áudio (o microfone e a saída, em cartões) e Reunião (U-065), com o conteúdo em até 570 px de altura e a
janela inteira dentro da área livre pelo `fit_to_screen` (A-17); a falta de chave numa faixa no rodapé, ao lado do Gravar, o
primário com o ícone de gravar (U-066); as fontes com o medidor de cada uma e "Reunião" em selo, e a sala e o ao vivo numa
linha "Opções" (U-067). Os avisos do microfone, do eco e dos modelos do ao vivo viram faixas (U-057); os links vão a métodos,
não a lambdas que prendam o diálogo (R-06).
Os presets e a última escolha moram em `ui/warmup_source.py` (refactor antes da F5.7: o arquivo não cresce)."""
from __future__ import annotations

import functools
import json
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, override

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout, QWidget)

from ..capture.align import estimate_offset
from ..capture.audio import resample, rms, to_mono
from ..capture.recorder import RecordConfig
from ..config.defaults import SPECS
from ..config.language import LANGUAGES, default_language, label
from ..llm.glossary_task import GlossaryResult, match_people_locally, merge_glossary, request_glossary, split_names
from ..log import get_logger
from ..model import SourceKind
from .common import fit_to_screen
from .components import Card, InfoBar
from .devices import FOLLOW_ID, FOLLOW_TEXT, REFRESH_MS, fill_mic_combo, refresh_mics
from .icons import set_icon
from .keys import missing_keys, missing_keys_text
from .meters import LevelMeter
from .sources import SourceTable
from .tasks import run_task
from .theme import METRICS, Tone, note, primary
from .warmup_live import LiveBoxes
from .warmup_source import SourceChoiceMixin

if TYPE_CHECKING:
    from ..platform.base import AudioDevice, CaptureHandle, Platform
    from ..store import Store

log = get_logger("ui.warmup")
GLOSSARY_DEBOUNCE_MS = 1500
MIC_NAME_CHARS = 24                 # U-033: largura do combo do microfone em caracteres (o nome inteiro fica na lista e na dica)
DIALOG_MARGIN = 16                  # A-17: o conteúdo cabe em 570 px de altura (notebook a 150 %: 1280 x 672 lógicos)
SOURCES_MIN_W, SOURCES_MIN_H = 420, 132    # a tabela das fontes: as três colunas à vista e o cabeçalho com três linhas
AUDIO_STRETCH, MEETING_STRETCH = 5, 4
LINKED_BARS = frozenset({"keys", "live"})            # as faixas com o link para as Configurações


class WarmupDialog(SourceChoiceMixin, QDialog):
    def __init__(self, platform: Platform, store: Store, parent: QWidget | None = None, on_settings: Any = None,
                 on_late_call: Callable[[int], Any] | None = None):
        super().__init__(parent)
        self.platform = platform
        self.store = store
        self.on_settings = on_settings
        self.on_late_call = on_late_call                 # chamada do glossário que volta depois do "Gravar" (F3.1 R3-11)
        self.glossary_result: GlossaryResult | None = None
        self.glossary_call_ids: list[int] = []           # toda chamada feita aqui, velha ou não, é da reunião que nascer
        self._accepted = False
        self._glossary_seq = 0
        self._glossary_inflight = False
        self._glossary_pending = False
        self.glossary_requests = 0                       # instrumentação dos testes
        self.setWindowTitle("Antes de gravar")
        self._mic_handle: CaptureHandle | None = None
        self._r_handle: CaptureHandle | None = None
        self._mic_level = 0.0
        self._test_buffer: list[np.ndarray] = []
        self._r_buffer: list[np.ndarray] = []
        self._testing = False
        self._pending_source: tuple[str, str] | None = None
        self._devices = platform.list_input_devices()
        self._bars: dict[str, InfoBar] = {}                 # F5.4 L2: as faixas já montadas (`bar`)
        self._slots: dict[str, tuple[QBoxLayout, int, int]] = {}

        self._build_mic()
        self._build_sources(platform)
        meet_box = self._build_meeting(store)
        self._build_footer()
        self._lay_out(meet_box)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._devices_timer = QTimer(self)                  # U-043: fone conectado com o warm-up aberto
        self._devices_timer.setInterval(REFRESH_MS)
        self._devices_timer.timeout.connect(self._refresh_devices)
        self._silent_ticks = 0
        self.live.set_values(None, None)                    # padrão das Configurações; um preset salvo sobrescreve
        self._update_keys_note()
        self._restore_last_choice()
        self.mic_combo.currentIndexChanged.connect(self._on_mic_changed)   # só depois de restaurar: um open só
        self._update_src_status()

    # ---------------------------------------------------------------- faixas (F5.4 L2, U-057 e U-066)
    def bar(self, slot: str) -> InfoBar:
        """A faixa de um aviso (`mic`, `echo`, `live` ou `keys`), montada só na primeira vez que ele aparece, no lugar guardado
        em `_slots`: as quatro quase sempre ficam escondidas, e montá-las à toa deixava a abertura mais lenta que o limite do
        Aceite 5 ([fato, 28/10] ~0,5 ms cada faixa contra ~0,15 de um rótulo)."""
        bar = self._bars.get(slot)
        if bar is None:
            bar = InfoBar("warning")
            bar.hide()
            if slot in LINKED_BARS:                         # o aviso com o link para as Configurações
                bar.label.setTextFormat(Qt.TextFormat.RichText)
                bar.label.linkActivated.connect(self._open_settings_for_keys if slot == "keys" else self._open_settings_for_live)
            layout, index, stretch = self._slots[slot]
            layout.insertWidget(index, bar, stretch)
            self._bars[slot] = bar
        return bar

    def _show_bar(self, slot: str, text: str, kind: str = "warning") -> None:
        """O texto na faixa e a faixa à vista; sem texto, escondida (e nem montada, se ainda não foi)."""
        if not text and slot not in self._bars:
            return
        bar = self.bar(slot)
        bar.set_kind(kind)
        bar.label.setText(text)
        bar.label.setVisible(bool(text))
        bar.setVisible(bool(text))

    @property
    def mic_note(self) -> QLabel:
        return self.bar("mic").label

    @property
    def echo_label(self) -> QLabel:
        return self.bar("echo").label

    @property
    def live_note(self) -> QLabel:
        return self.bar("live").label

    @property
    def keys_note(self) -> QLabel:
        return self.bar("keys").label

    # ---------------------------------------------------------------- montagem (F5.4 L2: U-065 a U-067)
    def _build_mic(self) -> None:
        self.mic_box = Card("Microfone (canal L: você)")
        self.mic_combo = QComboBox()
        fill_mic_combo(self.mic_combo, self._devices, FOLLOW_ID, None, follow=True)   # F5.3: "acompanha" primeiro
        # U-033: o nome do dispositivo cabe cortado no combo e inteiro na lista e na dica; com o nome todo ("Grupo de
        # microfones (Tecnologia Intel® Smart Sound para microfones digitais)") a coluna do áudio engolia a da reunião
        self.mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mic_combo.setMinimumContentsLength(MIC_NAME_CHARS)
        names = [FOLLOW_TEXT, *(d.name for d in self._devices)]   # a lista aberta nunca mais estreita que o maior nome
        longest = max(self.mic_combo.fontMetrics().horizontalAdvance(n) for n in names)
        self.mic_combo.view().setMinimumWidth(longest + 32)
        self.mic_combo.currentTextChanged.connect(self.mic_combo.setToolTip)
        self.mic_combo.setToolTip(self.mic_combo.currentText())
        self.mic_meter = LevelMeter(height=8)
        self.mic_status = QLabel("ouvindo…")
        self.mic_status.setWordWrap(True)                 # "microfone indisponível: <erro>" não alarga a janela
        self.test_btn = QPushButton("Testar 3 s")
        self.test_btn.setToolTip("Grava 3 s do microfone e da saída escolhida, toca o microfone de volta e verifica eco.")
        self.test_btn.clicked.connect(self._test_mic)
        ml = self.mic_box.content
        ml.addWidget(self.mic_combo)
        ml.addWidget(self.mic_meter)
        row = QHBoxLayout()
        row.addWidget(self.mic_status, 1)
        row.addWidget(self.test_btn)
        ml.addLayout(row)
        self._slots["mic"] = (ml, -1, 0)                   # U-057: o aviso do microfone numa faixa, embaixo

    def _build_sources(self, platform: Platform) -> None:
        self.src_box = Card("Saída (canal R: quem você ouve)")      # U-067: o app de reunião vem em selo na tabela
        self.sources = SourceTable(platform)
        self.sources.setMinimumSize(SOURCES_MIN_W, SOURCES_MIN_H)
        self.sources.selection_changed.connect(self._update_src_status)
        self.sources.first_apps_ready.connect(self._apply_pending_source)
        self.src_status = QLabel("")
        note(self.src_status)
        sl = self.src_box.content
        sl.addWidget(self.sources, 1)
        sl.addWidget(self.src_status)
        self._slots["echo"] = (sl, -1, 0)                  # U-057: o resultado do teste de eco numa faixa

    def _build_meeting(self, store: Store) -> Card:
        meet_box = Card("Reunião (opcional)")
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("(último usado)", None)
        for p in store.list_presets():
            self.preset_combo.addItem(p["name"], p["id"])
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        self.save_preset_btn = QPushButton("Salvar como preset…")
        self.save_preset_btn.clicked.connect(self._save_preset)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("título da reunião")
        self.participants_edit = QLineEdit()
        self.participants_edit.setPlaceholderText("participantes esperados, separados por vírgula")
        self.language_combo = QComboBox()                # U-045: o código vai no `itemData`; o texto é só o rótulo
        for code in LANGUAGES:
            self.language_combo.addItem(label(code), code)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(default_language(store))))
        self.language_combo.setToolTip("O idioma falado na reunião: vale para a transcrição, a legenda ao vivo e tudo o que a LLM "
                                       "escrever (o padrão fica nas Configurações, Idioma e avisos).")
        self.room_check = QCheckBox("Sala de reunião presencial (o microfone ouve outras pessoas)")
        self.live = LiveBoxes(store, functools.partial(self._show_bar, "live"), self)     # F4 e F5.6 (U-091, A-21)
        self.live_check, self.names_check, self.partial_check = self.live.live_check, self.live.names_check, self.live.partial_check
        self.glossary_edit = QLineEdit()
        self.glossary_edit.setPlaceholderText("termos prováveis para a transcrição (a LLM sugere; edite à vontade)")
        self.glossary_status = QLabel("")
        note(self.glossary_status)
        self.glossary_status.setOpenExternalLinks(False)
        self.glossary_status.linkActivated.connect(self._open_settings_from_glossary)
        self._meeting_form(meet_box)
        self._glossary_timer = QTimer(self)
        self._glossary_timer.setSingleShot(True)
        self._glossary_timer.setInterval(GLOSSARY_DEBOUNCE_MS)
        self._glossary_timer.timeout.connect(self.request_glossary)
        self.title_edit.editingFinished.connect(self._glossary_timer.start)
        self.participants_edit.editingFinished.connect(self._glossary_timer.start)
        return meet_box

    def _meeting_form(self, meet_box: Card) -> None:
        """U-067: sala e ao vivo juntos em "Opções", com o rótulo em cima e as caixas na largura do cartão: o texto inteiro de
        cada caixa fica (leitor de tela e testes) e a da sala, a mais longa, não alarga a coluna dos campos."""
        fl = QFormLayout()
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setVerticalSpacing(METRICS.form_row)
        prow = QHBoxLayout()
        prow.addWidget(self.preset_combo, 1)
        prow.addWidget(self.save_preset_btn)
        fl.addRow("Preset", prow)
        fl.addRow("Título", self.title_edit)
        fl.addRow("Participantes", self.participants_edit)
        fl.addRow("Idioma da reunião", self.language_combo)
        fl.addRow("Termos prováveis", self.glossary_edit)
        fl.addRow("", self.glossary_status)
        options = QVBoxLayout()
        options.setSpacing(4)
        options.addWidget(self.room_check)
        options.addLayout(self.live.row())
        self._slots["live"] = (options, -1, 0)              # U-057: os modelos que faltam numa faixa, com o link
        fl.addRow(QLabel("Opções"))
        fl.addRow(options)
        meet_box.content.addLayout(fl)
        meet_box.content.addStretch(1)

    def _build_footer(self) -> None:
        """U-066: a falta de chave numa faixa no rodapé, ao lado do Gravar (o primário, com o ícone de gravar)."""
        self.buttons = QDialogButtonBox()
        self.record_btn = self.buttons.addButton("Gravar", QDialogButtonBox.ButtonRole.AcceptRole)
        set_icon(self.record_btn, "record", Tone.ON_ACCENT)
        self.buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    def _lay_out(self, meet_box: Card) -> None:
        """U-065: duas colunas, Áudio (microfone e saída) e Reunião, e o rodapé com a faixa e os botões."""
        audio = QVBoxLayout()
        audio.setSpacing(METRICS.card_gap)
        audio.addWidget(self.mic_box)
        audio.addWidget(self.src_box, 1)
        columns = QHBoxLayout()
        columns.setSpacing(METRICS.card_gap)
        columns.addLayout(audio, AUDIO_STRETCH)
        columns.addWidget(meet_box, MEETING_STRETCH)
        footer = QHBoxLayout()
        footer.setSpacing(METRICS.card_gap)
        footer.addWidget(self.buttons, 0, Qt.AlignmentFlag.AlignVCenter)
        self._slots["keys"] = (footer, 0, 1)                # U-037: falta chave ou token? diz antes de gravar, com o link
        lay = QVBoxLayout(self)
        lay.setContentsMargins(DIALOG_MARGIN, DIALOG_MARGIN, DIALOG_MARGIN, DIALOG_MARGIN)
        lay.setSpacing(METRICS.card_gap)
        lay.addLayout(columns, 1)
        lay.addLayout(footer)
        primary(self, self.record_btn)                      # F5.4 (U-056, A-02): Gravar é o primário (Enter)

    # ---------------------------------------------------------------- ciclo
    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        fit_to_screen(self)                                # A-17: a janela inteira, com a moldura, na área livre
        if self._mic_handle is None:
            self._open_mic()
        self.sources.start()
        self._timer.start()
        self._devices_timer.start()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown()
        super().closeEvent(event)

    @override
    def accept(self) -> None:
        self._accepted = True
        self._save_last_choice()
        self._shutdown()
        super().accept()

    @override
    def reject(self) -> None:
        self._shutdown()
        super().reject()

    def _shutdown(self) -> None:
        self._timer.stop()
        self._devices_timer.stop()
        self._glossary_timer.stop()
        self.live.disconnect_models()                    # `_shutdown` roda mais de uma vez (aceitar e depois fechar)
        self._glossary_seq += 1                            # resposta que chegar depois é descartada
        self.sources.stop()
        self._close_mic()
        self._close_r()

    # ---------------------------------------------------------------- glossário (F3 item 4)
    def request_glossary(self) -> None:
        """Uma chamada em voo; mudança durante a chamada agenda outra; nada sem título nem participantes."""
        title, participants = self.title_edit.text().strip(), self.participants_edit.text().strip()
        if not title and not participants:
            return
        if self._glossary_inflight:
            self._glossary_pending = True
            return
        self._glossary_seq += 1
        self._glossary_inflight = True
        self.glossary_requests += 1
        self.glossary_status.setText("pedindo termos prováveis à LLM…")
        stop = threading.Event()                           # a saída do app cancela a chamada presa na rede (R8-04)
        run_task(functools.partial(request_glossary, self.store, title or None, participants or None,
                                   language=self.meeting_language(), should_stop=stop.is_set),
                 self.store, functools.partial(self._on_glossary, self._glossary_seq), cancel=stop)

    def _on_glossary(self, seq: int, result: Any) -> None:
        self._glossary_inflight = False
        call_id = result.call_id if isinstance(result, GlossaryResult) else None
        if call_id is not None:
            if self._accepted:                             # "Gravar" já criou a reunião: o controlador religa a chamada
                if self.on_late_call is not None:
                    self.on_late_call(call_id)
            elif call_id not in self.glossary_call_ids:
                self.glossary_call_ids.append(call_id)
        if seq != self._glossary_seq:                      # velha: o usuário mudou o texto ou fechou
            return
        if self._glossary_pending:
            self._glossary_pending = False
            self._glossary_timer.start()
        if isinstance(result, BaseException):
            log.warning("glossário indisponível: %s", result)
            self.glossary_status.setText(f"glossário indisponível: {result}")
            return
        self.glossary_result = result
        if result.error:
            text = result.error
            if text.startswith("sem chave"):
                text = 'sem chave de LLM: <a href="settings">configurar as chaves</a>'
            self.glossary_status.setText(text)
            return
        self.glossary_edit.setText(merge_glossary(result.terms, self.glossary_edit.text()))
        people = f"; {len(result.people)} participante(s) com ficha" if result.people else ""
        self.glossary_status.setText(f"{len(result.terms)} termo(s) sugeridos pela LLM{people}")

    # ---------------------------------------------------------------- mic
    def _on_mic_changed(self, _index: int) -> None:
        self._open_mic()

    def _set_mic_note(self, text: str, kind: str = "warning") -> None:
        """O aviso do microfone na faixa (U-057), escondida sem texto."""
        self._show_bar("mic", text, kind)

    def _selected_device(self) -> AudioDevice | None:
        """O microfone do combo; None é o "Padrão do Windows (acompanha)" (F5.3)."""
        key = self.mic_combo.currentData()
        return next((d for d in self._devices if d.id == key), None) if key else None

    def _open_mic(self) -> None:
        self._close_mic()
        dev = self._selected_device()
        try:
            handle = self.platform.open_mic(dev, 16000, self._on_mic)
            handle.start()
            self._mic_handle = handle
            self.mic_status.setText("ouvindo…")
        except Exception as e:
            self._mic_handle = None
            self.mic_status.setText(f"microfone indisponível: {e}")
            log.warning("warm-up: microfone indisponível: %s", e)

    def _close_mic(self) -> None:
        if self._mic_handle is not None:
            try:
                self._mic_handle.stop()
            except Exception as e:
                log.debug("fechar mic do warm-up: %s", e)
            self._mic_handle = None

    def _refresh_devices(self) -> None:
        """U-043: o fone conectado ou removido com o warm-up aberto entra ou sai do combo, sem reiniciar o app."""
        if self._testing:
            return                                         # o teste de 3 s lê o medidor: fica para o próximo ciclo
        result = refresh_mics(self.platform, self.mic_combo, self._devices, meter_open=self._mic_handle is not None,
                              close_meter=self._close_mic, follow=True)
        if result is None:
            return
        self._devices = result.devices
        if result.note:
            self._set_mic_note(result.note, "warning" if "saiu" in result.note else "info")
        if result.reopen:
            self._open_mic()

    def _close_r(self) -> None:
        if self._r_handle is not None:
            try:
                self._r_handle.stop()
            except Exception as e:
                log.debug("fechar saída do teste: %s", e)
            self._r_handle = None

    def _on_mic(self, block: np.ndarray, t_first: float) -> None:
        self._mic_level = rms(block)
        if self._testing:
            self._test_buffer.append(np.asarray(block, dtype=np.float32).reshape(-1))

    def _on_r_test(self, block: np.ndarray, t_first: float) -> None:
        if self._testing:
            self._r_buffer.append(np.asarray(block, dtype=np.float32))

    def _test_mic(self) -> None:
        if self._mic_handle is None or self._testing:
            return
        self._testing = True
        self._test_buffer = []
        self._r_buffer = []
        spec = self.sources.selected_spec()
        try:
            if spec.kind == SourceKind.APP and spec.pid:
                self._r_handle = self.platform.open_app_loopback(spec.pid, self._on_r_test)
            else:
                self._r_handle = self.platform.open_system_loopback(self._on_r_test, output_id=spec.output_id or None)
            self._r_handle.start()
        except Exception as e:
            log.debug("teste: saída não abriu: %s", e)
            self._r_handle = None
        self.test_btn.setText("gravando…")
        self.test_btn.setEnabled(False)
        self._set_echo("")
        QTimer.singleShot(3000, self._finish_test)

    def _finish_test(self) -> None:
        self._testing = False
        r_rate = self._r_handle.sample_rate if self._r_handle is not None else None
        self._close_r()
        self.test_btn.setText("Testar 3 s")
        self.test_btn.setEnabled(True)
        if not self._test_buffer:
            self.mic_status.setText("nada gravado no teste")
            return
        from ..store.playback import play

        audio = np.concatenate(self._test_buffer)
        self._check_echo(audio, r_rate)
        self.mic_status.setText(f"tocando o teste ({len(audio) / 16000:.1f} s)…".replace(".", ","))
        try:
            play(audio, 16000)
        except Exception as e:
            self.mic_status.setText(f"não consegui tocar: {e}")

    def _check_echo(self, mic: np.ndarray, r_rate: int | None) -> None:
        """U-016: o microfone está ouvindo a saída? Correlação cruzada entre os 3 s do mic e os 3 s da saída."""
        spec = self.sources.selected_spec()
        if not self._r_buffer or not r_rate:
            log.info("teste de 3 s: microfone %.1f s (nível %.4f); a saída (%s) não entregou áudio", len(mic) / 16000, rms(mic),
                     spec.log_text())
            self._set_echo("Eco: a saída não entregou áudio durante o teste (nada tocando?).", "info")
            return
        r = resample(to_mono(np.concatenate(self._r_buffer)), r_rate, 16000)
        log.info("teste de 3 s: microfone %.1f s (nível %.4f); saída %s %.1f s (nível %.4f)", len(mic) / 16000, rms(mic),
                 spec.log_text(), len(r) / 16000, rms(r))
        res = estimate_offset(mic, r, 16000)
        if res is None:
            log.info("teste de eco: sinal insuficiente para correlacionar")
            self._set_echo("Eco: sinal insuficiente para testar (fale e deixe a saída tocando).", "info")
            return
        corr, limit = res["correlation"], self.store.get(SPECS["capture.echo_corr_min"])
        log.info("teste de eco: correlação %.3f, atraso %.0f ms, limite %.2f", corr, res["offset_ms"], limit)
        corr_text = f"{corr:.2f}".replace(".", ",")                  # vírgula decimal (U-032)
        if corr >= limit:
            self._set_echo(f"Eco: seu microfone está ouvindo a reunião (correlação {corr_text}, atraso aproximado "
                           f"{res['offset_ms']:.0f} ms). Use fone ou marque 'Sala de reunião'.", "warning")
        else:
            self._set_echo(f"Sem eco: o microfone não ouve a saída (correlação {corr_text}).", "success")

    def _set_echo(self, text: str, kind: str = "info") -> None:
        """O resultado do teste de eco na faixa (U-057): sucesso, aviso ou informação; escondida sem texto."""
        self._show_bar("echo", text, kind)

    # ---------------------------------------------------------------- tick
    def _tick(self) -> None:
        self.mic_meter.set_rms(self._mic_level)
        if self._mic_handle is not None:
            if self._mic_level < 0.002:
                self._silent_ticks += 1
                if self._silent_ticks == 51:
                    self.mic_status.setText("sem sinal no microfone há 5 s")
            else:
                if self._silent_ticks > 50 and not self._testing:
                    self.mic_status.setText("ouvindo…")
                self._silent_ticks = 0

    def _update_src_status(self) -> None:
        spec = self.sources.selected_spec()
        self.src_status.setText(f"selecionado: {spec.label or spec.kind}")

    # ---------------------------------------------------------------- ao vivo (F4; as caixas em `ui/warmup_live.py`)
    def _open_settings_for_live(self, _link: str = "") -> None:
        self._open_settings_from_glossary()
        self.live.update_note()

    def _open_settings_from_glossary(self, _link: str = "") -> None:
        if self.on_settings is not None:
            self.on_settings()

    # ---------------------------------------------------------------- chaves (U-037)
    def _update_keys_note(self) -> None:
        self._show_bar("keys", missing_keys_text(missing_keys(self.store)))
        self.live.refresh_key()                                     # F5.6 L1b (R-09): o "Resumo parcial" segue a chave

    def _open_settings_for_keys(self, _link: str = "") -> None:
        if self.on_settings is not None:
            self.on_settings()
        self._update_keys_note()

    # ---------------------------------------------------------------- resultado
    def meeting_language(self) -> str:
        """O código do combo "Idioma da reunião" (`pt-BR` ou `en`)."""
        return str(self.language_combo.currentData())

    def result_config(self) -> tuple[RecordConfig, dict[str, Any]]:
        cfg = RecordConfig.from_settings(self.store, self._selected_device(), self.sources.selected_spec())
        spec = cfg.source
        platform_name = (spec.exe or "sistema").removesuffix(".exe") if spec.kind == SourceKind.APP else "sistema"
        result = self.glossary_result
        participants = self.participants_edit.text().strip()
        # casamento local sempre (R3-11): gravar com o glossário em voo não perde os participantes esperados
        ids = {pid for _name, pid in match_people_locally(split_names(participants), [dict(p) for p in self.store.people.list_people()])[0]}
        ids |= {pid for _name, pid in (result.people if result else ())}
        fields = {
            "title": self.title_edit.text().strip() or None,
            "participants": participants or None,
            "platform": platform_name,
            "preset_id": self.preset_combo.currentData(),
            "is_in_room": self.room_check.isChecked(),
            "glossary": self.glossary_edit.text().strip() or None,
            "expected_people_json": json.dumps(sorted(ids)) if ids else None,
            "glossary_call_ids": list(self.glossary_call_ids),
            "language": self.meeting_language(),                          # F5.3 (A-01): a reunião nasce com ele
            **self.live.fields(),                                         # F4 e F5.6: legenda, nomes e parcial (CP6, A-21)
        }
        return cfg, fields
