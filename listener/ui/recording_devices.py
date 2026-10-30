"""F5.3 L4 (U-049 a U-051): os dispositivos durante a gravação, fora do controlador. A cada 2 s a leitura do Core Audio
(só a assinatura; os nomes quando ela muda, A-19) diz o que chegou depois do começo da gravação ("novo"), se o padrão
mudou (o que acompanha reabre, A-20) e se o escolhido sumiu ou voltou (A-16); a troca automática que não deu tenta de novo
em 10 s quando nada fechou (o player segurava o PortAudio) ou quando a saída ficou sem loopback, nunca reabrindo à toa um
microfone que funciona. A troca de microfone passa o mudo ao novo de uma vez (A-18) e avisa o controlador, que ignora a
primeira leitura do poller do mudo; soltar o player antes de reler o PortAudio é o gancho `before_reread` do gravador
(A-17). U-050: o microfone novo aparece na linha de alerta do widget, "novo: <nome>" com "Usar", por 30 s; "Usar" troca o
microfone e abre o painel (`ui/devices_panel.py`) com as saídas do mesmo aparelho primeiro. A linha de alerta tem
prioridade: a saúde dos canais, o banco parado, o aviso dos dispositivos e o banco lento (A-21). Nomes de aparelho só na
tela (A-22). Revisão do L4: o aviso de que o escolhido saiu (e voltou) vem do estado a cada leitura, não de quem trocou,
porque a reabertura de 200 ms do gravador cai no padrão antes da vigia (R-04); o novo que o "acompanha" já abriu não
oferece "Usar" (R-09); a troca de saída que falha diz se a anterior voltou (R-03). F5.4 L2 (U-060): cada alerta tem um tipo
(`kind`: `info`, `success`, `warning` ou `error`, os da `InfoBar`), que o widget mostra no ícone e na cor da linha."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox

from ..capture.echo_watch import EchoWatch
from ..capture.recorder import REREAD_REFUSED, DeviceSwitch, Recorder, SourceSpec
from ..log import get_logger
from ..model import DeviceReason, EventType, SourceKind
from ..platform.base import AudioDevice, Endpoint, Endpoints, log_id
from .common import clamp_to_screens
from .devices_panel import (MIC_FOLLOW, OTHER_APP, OUT_FOLLOW, DevicesPanel, PanelView, is_hands_free, mic_rows, output_rows,
                            panel_notes)
# F5.9 (L1a): as decisões sem Qt e a linha de alerta moram em `recording_alerts.py`; reexportadas aqui para quem as importa
from .recording_alerts import (ACTION_DEVICES, ACTION_OUTPUTS, ACTION_ROOM, ACTION_USE, BACK_TEXT, DEVICES_BUTTON, ECHO_TEXT,
                               ECHO_TIP, GONE_TEXT, ROOM_BUTTON, Alert, alert_line, comm_hint, mic_action, missing_edge,
                               output_action)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from ..platform.base import Platform

log = get_logger("ui.devices")

POLL_MS = 2000
NEW_NOTICE_S = 30.0               # U-050: "novo: <nome>" some depois disto
INFO_NOTICE_S = 15.0              # o escolhido que saiu ou voltou
RETRY_S = 10.0                    # a troca automática que não deu (o player não soltou) tenta de novo


def endpoint_device(ep: Endpoint) -> AudioDevice:
    """O endpoint do Core Audio como pedido de microfone (o índice do PortAudio sai da leitura atual dele, pelo id)."""
    return AudioDevice(ep.id, ep.name, "", 1, 0)


# ---------------------------------------------------------------- a gravação
class RoomStore(Protocol):
    """F5.9 (item 7, pergunta 2): quem liga "Sala de reunião" na reunião em curso (o `MeetingsRepo` do banco)."""
    def set_in_room(self, meeting_id: int, in_room: bool) -> bool: ...


class RecordingDevices(QObject):
    """Os dispositivos de uma gravação: a vigia de 2 s, as trocas, o aviso do widget e o painel do botão 🎧. F5.9 (item 7):
    a vigia de eco depois de cada troca de microfone (`EchoWatch`, pelo desvio ao vivo do gravador)."""

    def __init__(self, platform: Platform, recorder: Recorder, *, panel_parent: QWidget | None = None,
                 on_mic_switched: Callable[[], object] | None = None, open_other_app: Callable[[], object] | None = None,
                 rooms: RoomStore | None = None, monotonic: Callable[[], float] = time.monotonic, parent: QObject | None = None):
        super().__init__(parent)
        self.platform = platform
        self.recorder = recorder
        self.panel_parent = panel_parent
        self.on_mic_switched = on_mic_switched           # o controlador ignora a primeira leitura do poller do mudo (A-18)
        self.open_other_app = open_other_app             # o "Trocar fonte" de antes ("Outro app…")
        self.rooms = rooms                               # F5.9: o "Marcar como sala" do aviso de eco
        self.monotonic = monotonic
        self.echo = EchoWatch(clock=monotonic)
        self.reading: Endpoints | None = None
        self.start_ids: frozenset[str] = frozenset()     # A-19: "novo" é o que está fora deste conjunto
        self.panel: DevicesPanel | None = None
        self._announced: set[str] = set()
        self._notice: tuple[Alert, float, Endpoint | None] | None = None   # (alerta, até quando, o microfone novo)
        self._last_sig: tuple[object, ...] | None = None
        self._retry_at: float | None = None
        self._episodes: dict[str, str] = {"L": "", "R": ""}   # R-04: o escolhido de cada canal que saiu e ainda não voltou
        self._first_container = ""
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)

    def start(self) -> None:
        self.reading = self._read()
        self.start_ids = self.reading.ids() if self.reading is not None else frozenset()
        self._last_sig = self.reading.signature if self.reading is not None else None
        self.recorder.set_tap("echo", self.echo.tap)      # F5.9 (item 7): só acumula depois de `arm()`, na troca
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.recorder.set_tap("echo", None)
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
        self._check_echo()
        reading = self._read()
        if reading is None:
            return
        retry = self._retry_at is not None and self.monotonic() >= self._retry_at
        self.reading = reading
        if reading.signature != self._last_sig or retry:
            self._last_sig = reading.signature
            self._follow(reading)
            self._announce(reading)                      # R-09: depois da vigia (o novo que o "acompanha" abriu já grava)
            if self.panel is not None and self.panel.isVisible():
                self._refresh_panel()
        self._check_missing(reading)

    def _check_echo(self) -> None:
        """F5.9 (item 7): o resultado da correlação depois da troca de microfone; acima do limiar (`capture.echo_corr_min`), o
        aviso com "Marcar como sala" (pergunta 2) e o evento `echo_warning` (correlação e atraso, sem áudio)."""
        res = self.echo.poll()
        rec = self.recorder
        if res is None or res.correlation < rec.cfg.echo_corr_min:
            return
        log.warning("reunião %s: o microfone novo ouve a reunião (correlação %.2f, atraso %.0f ms)", rec.meeting_id,
                    res.correlation, res.offset_ms)
        rec.sink.add_event(rec.meeting_id, round(rec.elapsed() * 1000), EventType.ECHO_WARNING,
                           {"correlation": round(res.correlation, 3), "offset_ms": round(res.offset_ms)})
        self._set_notice(Alert(ECHO_TEXT, ROOM_BUTTON, ACTION_ROOM, ECHO_TIP), NEW_NOTICE_S)

    def mark_room(self) -> bool:
        """Pergunta 2: com confirmação, liga "Sala de reunião" da reunião em curso para o pipeline final (o ao vivo segue como
        começou); sem onde gravar, só o aviso."""
        rec = self.recorder
        if self.rooms is None:
            return False
        answer = QMessageBox.question(self.panel_parent, ROOM_BUTTON, "Marcar esta reunião como sala de reunião? A identificação "
                                      "final passa a tratar o canal do microfone como a sala (várias vozes).")
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._notice = None
        done = self.rooms.set_in_room(rec.meeting_id, True)
        log.info("reunião %s: marcada como sala de reunião pelo aviso de eco (%s)", rec.meeting_id, "gravado" if done else "não achou")
        return done

    def _check_missing(self, reading: Endpoints) -> None:
        """R-04: o escolhido que sumiu e outro aparelho no lugar, a cada leitura (quem trocou pode ter sido a reabertura do
        gravador, antes da vigia); "voltou" quando ele grava de novo."""
        rec = self.recorder
        mic = rec.mic_device.id if rec.mic_device is not None else ""
        out = rec.source.output_id if rec.source.kind == SourceKind.SYSTEM else ""
        for channel, wanted, present, opened in (("L", mic, reading.mic(mic) is not None, rec.mic_open_id),
                                                 ("R", out, reading.output(out) is not None, rec.output_open_id)):
            self._episodes[channel], said = missing_edge(self._episodes[channel], wanted, present, opened)
            if said:
                text = GONE_TEXT[channel] if said == "saiu" else BACK_TEXT[channel]
                log.info("reunião %s: canal %s, o escolhido %s %s", rec.meeting_id, channel, log_id(wanted), said)
                kind = "warning" if said == "saiu" else "success"
                self._set_notice(Alert(text, DEVICES_BUTTON, ACTION_DEVICES, kind=kind), INFO_NOTICE_S)

    def _announce(self, reading: Endpoints) -> None:
        present = {e.id for e in reading.capture}
        self._announced &= present                       # saiu e voltou: avisa de novo
        for ep in reading.capture:
            if ep.id in self.start_ids or ep.id in self._announced:
                continue
            self._announced.add(ep.id)
            log.info("reunião %s: microfone novo durante a gravação: %s", self.recorder.meeting_id, log_id(ep.id))
            self._set_notice(self._new_alert(ep), NEW_NOTICE_S, ep)

    def _new_alert(self, ep: Endpoint) -> Alert:
        """U-050: "novo: <nome>" com "Usar"; o que já grava (o "acompanha" o seguiu) não oferece "Usar", só as saídas dele
        (R-09: trocar de novo abriria outro buraco e tiraria o "acompanha" sem dizer)."""
        hands_free = "\nHands-Free: o fone vai para o perfil de chamada (som mono)." if is_hands_free(ep.name) else ""
        if ep.id == self.recorder.mic_open_id:
            return Alert(f"novo: {ep.name or ep.id} (gravando nele)", DEVICES_BUTTON, ACTION_OUTPUTS,
                         "Mostra as saídas do mesmo aparelho." + hands_free, kind="info")
        return Alert(f"novo: {ep.name or ep.id}", "Usar", ACTION_USE,
                     "Usar troca o microfone para ele e mostra as saídas do mesmo aparelho." + hands_free, kind="info")

    def _follow(self, reading: Endpoints) -> None:
        rec = self.recorder
        retry = False
        lost = rec.snapshot.lost                         # F5.9 (item 2): o canal perdido (Health.LOST) é da reabertura de 5 s
        mic = mic_action(reading, rec.mic_device, rec.mic_open_id) if "L" not in lost else None
        if mic is not None:
            reason, strict = mic
            result = self.switch_mic(rec.mic_device, reason, strict=strict)
            retry = not result.ok and result.message == REREAD_REFUSED      # nada fechou: tentar de novo não abre buraco
        out = output_action(reading, rec.source, rec.output_open_id) if "R" not in lost else None
        if out is not None:
            retry = not rec.reopen_output(out) or retry
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
            self.echo.arm()                              # F5.9 (item 7): o microfone novo ouve a reunião?
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
        if ep.id == self.recorder.mic_open_id:           # R-09: já grava nele (o "acompanha" o seguiu): nada a trocar
            self.open_panel(ep.container)
            return
        result = self.switch_mic(endpoint_device(ep), DeviceReason.USER)
        self.open_panel(ep.container, self._status(result, "microfone"))

    def on_alert(self, action: str) -> None:
        if action == ACTION_USE:
            self.use_new()
        elif action == ACTION_OUTPUTS:                   # R-09: o novo que já grava: as saídas dele primeiro
            notice = self._notice
            self.open_panel(notice[2].container if notice is not None and notice[2] is not None else "")
        elif action == ACTION_DEVICES:
            self.open_panel()
        elif action == ACTION_ROOM:                      # F5.9 (item 7, pergunta 2)
            self.mark_room()

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
        if ok:
            self._after_choice("Saída trocada.", error=False)
        elif self.recorder.output_open:
            self._after_choice("A saída escolhida não abriu; segue a anterior.", error=True)
        else:                                            # R-03: nem a anterior voltou; a reabertura de 5 s segue tentando
            self._after_choice("A saída escolhida não abriu, nem a anterior: tentando de novo a cada 5 s.", error=True)
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
