"""F5.3 L4 (U-049 a U-051): os dispositivos durante a gravação, fora do controlador. A cada 2 s a leitura do Core Audio
(só a assinatura; os nomes quando ela muda, A-19) diz o que chegou depois do começo da gravação ("novo"), se o padrão
mudou (o que acompanha reabre, A-20) e se o escolhido sumiu ou voltou (A-16); a troca automática que não deu tenta de novo
em 10 s quando nada fechou (o player segurava o PortAudio) ou quando a saída ficou sem loopback, nunca reabrindo à toa um
microfone que funciona. A troca de microfone passa o mudo ao novo de uma vez (A-18) e avisa o controlador, que ignora a
primeira leitura do poller do mudo; soltar o player antes de reler o PortAudio é o gancho `before_reread` do gravador
(A-17). U-050: o microfone novo aparece na linha de alerta do widget, "novo: <nome>" com "Usar", por 30 s; "Usar" troca o
microfone e abre o painel (`ui/devices_panel.py`) com as saídas do mesmo aparelho primeiro. A linha de alerta tem
prioridade: a saúde dos canais, o banco parado, o aviso dos dispositivos e o banco lento (A-21). Nomes de aparelho só na
tela (A-22)."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer

from ..capture.recorder import REREAD_REFUSED, DeviceSwitch, Recorder, SourceSpec
from ..log import get_logger
from ..model import DeviceReason, Health, SourceKind
from ..platform.base import AudioDevice, Endpoint, Endpoints
from .common import clamp_to_screens
from .devices_panel import (MIC_FOLLOW, OTHER_APP, OUT_FOLLOW, DevicesPanel, PanelView, is_hands_free, mic_rows, output_rows,
                            panel_notes)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from ..platform.base import Platform

log = get_logger("ui.devices")

POLL_MS = 2000
NEW_NOTICE_S = 30.0               # U-050: "novo: <nome>" some depois disto
INFO_NOTICE_S = 15.0              # o escolhido que saiu ou voltou
RETRY_S = 10.0                    # a troca automática que não deu (o player não soltou) tenta de novo
ACTION_USE, ACTION_DEVICES = "use", "devices"
DEVICES_BUTTON = "Dispositivos…"


@dataclass(frozen=True, slots=True)
class Alert:
    """A linha de alerta do widget: o texto, o rótulo do botão (vazio = sem botão), a ação do clique e a dica."""
    text: str
    button: str = ""
    action: str = ""
    tip: str = ""


def endpoint_device(ep: Endpoint) -> AudioDevice:
    """O endpoint do Core Audio como pedido de microfone (o índice do PortAudio sai da leitura atual dele, pelo id)."""
    return AudioDevice(ep.id, ep.name, "", 1, 0)


# ---------------------------------------------------------------- decisões (sem Qt)
def mic_action(reading: Endpoints, wanted: AudioDevice | None, open_id: str) -> tuple[str, bool] | None:
    """(motivo, strict) da troca automática do microfone, ou None. Acompanha: o padrão mudou. Escolhido: voltou (A-16,
    pergunta 10), sumiu ou, fora dele, o padrão mudou (o lugar dele é o padrão)."""
    if wanted is None:
        target = reading.capture_default
        return (DeviceReason.DEFAULT, False) if target and target != open_id else None
    if reading.mic(wanted.id) is not None:
        return (DeviceReason.BACK, True) if open_id != wanted.id else None
    if open_id == wanted.id or (reading.capture_default and open_id != reading.capture_default):
        return (DeviceReason.MISSING, False)
    return None


def output_action(reading: Endpoints, source: SourceSpec, open_id: str) -> str | None:
    """O motivo de reabrir o loopback do sistema inteiro, ou None; a fonte de um app segue o app em qualquer saída."""
    if source.kind != SourceKind.SYSTEM:
        return None
    if source.output_id and reading.output(source.output_id) is not None:
        return DeviceReason.BACK if open_id != source.output_id else None
    target = reading.render_default
    if not target or target == open_id:
        return None
    return DeviceReason.DEFAULT if source.follows else DeviceReason.MISSING


def comm_hint(reading: Endpoints | None, source: SourceSpec, open_id: str) -> Endpoint | None:
    """A-20: a saída de comunicação, quando difere da padrão e não é a gravada (a chamada pode estar nela)."""
    if reading is None or source.kind != SourceKind.SYSTEM:
        return None
    comm = reading.render_comm
    if not comm or comm == reading.render_default or comm == open_id:
        return None
    return reading.output(comm)


def alert_line(rec: Recorder, health: Mapping[str, str], notice: Alert | None, comm: Endpoint | None) -> Alert | None:
    """A linha de alerta por prioridade (A-21): a saúde dos canais, o banco parado, o aviso dos dispositivos, o banco
    lento."""
    snap = rec.snapshot
    if health.get("L") == Health.LOST:
        return Alert("microfone desconectado; tentando reabrir…", DEVICES_BUTTON, ACTION_DEVICES)
    if health.get("R") == Health.LOST:
        return Alert("saída desconectada; tentando reabrir…", DEVICES_BUTTON, ACTION_DEVICES)
    if health.get("R") == Health.ALERT:
        text = (f"saída sem sinal: a chamada pode estar em {comm.name} (comunicação)" if comm is not None
                else "saída sem sinal: a reunião está tocando em outro app?")
        return Alert(text, DEVICES_BUTTON, ACTION_DEVICES)
    if health.get("L") == Health.ALERT:
        return Alert(f"microfone sem sinal há mais de {int(rec.cfg.l_alert_seconds)}s", DEVICES_BUTTON, ACTION_DEVICES)
    db = health.get("db", Health.OK)
    if db == Health.ALERT:
        since = int(rec.elapsed() - (snap.pending_since or rec.elapsed()))
        return Alert(f"banco não responde há {since}s; {snap.pending} pedaço(s) retido(s) em memória")
    if notice is not None:
        return notice
    if db == Health.WARN:
        return Alert(f"banco lento: {snap.pending} pedaço(s) na fila")
    return None


# ---------------------------------------------------------------- a gravação
class RecordingDevices(QObject):
    """Os dispositivos de uma gravação: a vigia de 2 s, as trocas, o aviso do widget e o painel do botão 🎧."""

    def __init__(self, platform: Platform, recorder: Recorder, *, panel_parent: QWidget | None = None,
                 on_mic_switched: Callable[[], object] | None = None, open_other_app: Callable[[], object] | None = None,
                 monotonic: Callable[[], float] = time.monotonic, parent: QObject | None = None):
        super().__init__(parent)
        self.platform = platform
        self.recorder = recorder
        self.panel_parent = panel_parent
        self.on_mic_switched = on_mic_switched           # o controlador ignora a primeira leitura do poller do mudo (A-18)
        self.open_other_app = open_other_app             # o "Trocar fonte" de antes ("Outro app…")
        self.monotonic = monotonic
        self.reading: Endpoints | None = None
        self.start_ids: frozenset[str] = frozenset()     # A-19: "novo" é o que está fora deste conjunto
        self.panel: DevicesPanel | None = None
        self._announced: set[str] = set()
        self._notice: tuple[Alert, float, Endpoint | None] | None = None   # (alerta, até quando, o microfone novo)
        self._last_sig: tuple[object, ...] | None = None
        self._retry_at: float | None = None
        self._first_container = ""
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)

    def start(self) -> None:
        self.reading = self._read()
        self.start_ids = self.reading.ids() if self.reading is not None else frozenset()
        self._last_sig = self.reading.signature if self.reading is not None else None
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self.panel is not None:
            panel, self.panel = self.panel, None
            panel.close()
            panel.deleteLater()

    def _read(self, refresh_names: bool = False) -> Endpoints | None:
        try:
            return self.platform.audio_endpoints(refresh_names)
        except Exception as e:  # sem Core Audio: o painel mostra a leitura do PortAudio, sem "novo"
            log.debug("endpoints de áudio: %s", e)
            return None

    # ---------------------------------------------------------------- vigia
    def poll(self) -> None:
        """A cada 2 s: só a assinatura (A-19); mudou, ou uma troca automática espera nova tentativa: o novo, o padrão e o
        escolhido."""
        if self.recorder.stopped:
            return
        reading = self._read()
        if reading is None:
            return
        retry = self._retry_at is not None and self.monotonic() >= self._retry_at
        self.reading = reading
        if reading.signature == self._last_sig and not retry:
            return
        self._last_sig = reading.signature
        self._announce(reading)
        self._follow(reading)
        if self.panel is not None and self.panel.isVisible():
            self._refresh_panel()

    def _announce(self, reading: Endpoints) -> None:
        present = {e.id for e in reading.capture}
        self._announced &= present                       # saiu e voltou: avisa de novo
        for ep in reading.capture:
            if ep.id in self.start_ids or ep.id in self._announced:
                continue
            self._announced.add(ep.id)
            log.info("reunião %s: microfone novo durante a gravação: %s", self.recorder.meeting_id, ep.id)
            tip = "Usar troca o microfone para ele e mostra as saídas do mesmo aparelho."
            if is_hands_free(ep.name):
                tip += "\nHands-Free: o fone vai para o perfil de chamada (som mono)."
            self._set_notice(Alert(f"novo: {ep.name or ep.id}", "Usar", ACTION_USE, tip), NEW_NOTICE_S, ep)

    def _follow(self, reading: Endpoints) -> None:
        rec = self.recorder
        retry = False
        mic = mic_action(reading, rec.mic_device, rec.mic_open_id)
        if mic is not None:
            reason, strict = mic
            result = self.switch_mic(rec.mic_device, reason, strict=strict)
            retry = not result.ok and result.message == REREAD_REFUSED      # nada fechou: tentar de novo não abre buraco
            if result.ok and reason in (DeviceReason.MISSING, DeviceReason.BACK) and rec.mic_device is not None:
                what = "saiu: gravando no padrão do Windows" if reason == DeviceReason.MISSING else "voltou"
                self._set_notice(Alert(f"microfone escolhido {what}", DEVICES_BUTTON, ACTION_DEVICES), INFO_NOTICE_S)
        out = output_action(reading, rec.source, rec.output_open_id)
        if out is not None:
            retry = not rec.reopen_output(out) or retry
            if out == DeviceReason.MISSING:
                self._set_notice(Alert("saída escolhida saiu: gravando a saída padrão", DEVICES_BUTTON, ACTION_DEVICES),
                                 INFO_NOTICE_S)
        self._retry_at = self.monotonic() + RETRY_S if retry else None

    # ---------------------------------------------------------------- trocas
    def switch_mic(self, device: AudioDevice | None, reason: str = DeviceReason.USER, *, strict: bool = True) -> DeviceSwitch:
        """A troca no gravador (A-17) e, se abriu, o mudo passado ao novo de uma vez (A-18)."""
        rec = self.recorder
        muted = rec.muted
        result = rec.switch_mic(device, reason=reason, strict=strict)
        if result.ok:
            try:
                self.platform.mic_mute().set_device(self._open_device(result.endpoint_id), muted)
            except Exception as e:  # o mudo é do sistema: a gravação segue (o silêncio do mudo é do gravador)
                log.warning("reunião %s: o mudo não passou ao microfone novo: %s", rec.meeting_id, e)
            if self.on_mic_switched is not None:
                self.on_mic_switched()
        return result

    def _open_device(self, endpoint_id: str) -> AudioDevice | None:
        ep = self.reading.mic(endpoint_id) if self.reading is not None else None
        return endpoint_device(ep) if ep is not None else (AudioDevice(endpoint_id, "", "", 1, 0) if endpoint_id else None)

    def switch_output(self, output_id: str) -> bool:
        """O sistema inteiro na saída escolhida ("" = a padrão, que acompanha; A-21); de um app, é troca de fonte."""
        ep = self.reading.output(output_id) if self.reading is not None and output_id else None
        label = f"Sistema inteiro: {ep.name}" if ep is not None else "Sistema inteiro"
        return self.recorder.switch_source(SourceSpec(SourceKind.SYSTEM, label=label, output_id=output_id))

    def use_new(self) -> None:
        """U-050, o "Usar": o microfone novo e o painel com as saídas do mesmo aparelho primeiro."""
        notice, self._notice = self._notice, None
        ep = notice[2] if notice is not None else None
        if ep is None:
            self.open_panel()
            return
        result = self.switch_mic(endpoint_device(ep), DeviceReason.USER)
        self.open_panel(ep.container, self._status(result, "microfone"))

    def on_alert(self, action: str) -> None:
        if action == ACTION_USE:
            self.use_new()
        elif action == ACTION_DEVICES:
            self.open_panel()

    # ---------------------------------------------------------------- aviso e alerta
    def _set_notice(self, alert: Alert, seconds: float, ep: Endpoint | None = None) -> None:
        self._notice = (alert, self.monotonic() + seconds, ep)

    def notice(self) -> Alert | None:
        if self._notice is not None and self.monotonic() >= self._notice[1]:
            self._notice = None
        return self._notice[0] if self._notice is not None else None

    def alert(self, health: Mapping[str, str]) -> Alert | None:
        rec = self.recorder
        return alert_line(rec, health, self.notice(), comm_hint(self.reading, rec.source, rec.output_open_id))

    # ---------------------------------------------------------------- painel
    def open_panel(self, first_container: str = "", status: str = "") -> DevicesPanel:
        if self.panel is None:
            self.panel = DevicesPanel(self.panel_parent)
            self.panel.mic_chosen.connect(self.choose_mic)
            self.panel.output_chosen.connect(self.choose_output)
        fresh = self._read(refresh_names=True)           # A-19: o painel abrindo relê os nomes
        self.reading = fresh if fresh is not None else self.reading
        self._first_container = first_container
        self._refresh_panel()
        self.panel.set_status(status)
        self.panel.adjustSize()
        self._place_panel(self.panel)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        return self.panel

    def _place_panel(self, panel: DevicesPanel) -> None:
        """Acima do widget, alinhado à direita dele e dentro da tela dele (o widget mora num canto)."""
        anchor = self.panel_parent
        if anchor is None:
            return
        geo = anchor.frameGeometry()
        screen = anchor.screen().availableGeometry() if anchor.screen() is not None else None
        rects = [(screen.x(), screen.y(), screen.width(), screen.height())] if screen is not None else []
        size = panel.sizeHint()
        x, y = geo.right() - size.width(), geo.top() - size.height() - 8
        if screen is not None and y < screen.y():
            y = geo.bottom() + 8                         # sem espaço em cima: embaixo do widget
        pos = clamp_to_screens(x, y, size.width(), size.height(), rects) if rects else None
        panel.move(*(pos or (x, y)))

    def view(self) -> PanelView:
        rec = self.recorder
        pa = [] if self.reading is not None else self._portaudio_mics()
        mics = mic_rows(self.reading, pa, rec.mic_device, self.start_ids)
        outputs = output_rows(self.reading, rec.source, self.start_ids, self._first_container)
        return PanelView(mics, outputs, panel_notes(self.reading, rec.source, mics))

    def _portaudio_mics(self) -> list[AudioDevice]:
        try:
            return list(self.platform.list_input_devices())
        except Exception as e:
            log.debug("microfones do PortAudio: %s", e)
            return []

    def _refresh_panel(self) -> None:
        if self.panel is not None:
            self.panel.show_view(self.view())

    def choose_mic(self, key: str) -> DeviceSwitch:
        """Uma linha do microfone no painel: "acompanha" ou um aparelho (pelo id)."""
        device: AudioDevice | None = None
        if key != MIC_FOLLOW:
            eid = key.removeprefix("mic:")
            ep = self.reading.mic(eid) if self.reading is not None else None
            device = endpoint_device(ep) if ep is not None else next(
                (d for d in self._portaudio_mics() if d.id == eid), AudioDevice(eid, "", "", 1, 0))
        result = self.switch_mic(device, DeviceReason.USER)
        self._after_choice(self._status(result, "microfone"))
        return result

    def choose_output(self, key: str) -> bool:
        """Uma linha da saída: "acompanha", uma saída do sistema inteiro ou "Outro app…" (o "Trocar fonte" de antes)."""
        if key == OTHER_APP:
            if self.open_other_app is not None:
                self.open_other_app()
            self._after_choice("")
            return True
        ok = self.switch_output("" if key == OUT_FOLLOW else key.removeprefix("out:"))
        self._after_choice("Saída trocada." if ok else "A saída escolhida não abriu; segue a anterior.", error=not ok)
        return ok

    def _after_choice(self, status: str, error: bool | None = None) -> None:
        self._refresh_panel()
        if self.panel is not None:
            self.panel.set_status(status, error=status.startswith("Não") if error is None else error)

    @staticmethod
    def _status(result: DeviceSwitch, what: str) -> str:
        if result.ok:
            return f"{what.capitalize()} trocado."
        return f"Não deu para trocar o {what}: {result.message}; segue o anterior."
