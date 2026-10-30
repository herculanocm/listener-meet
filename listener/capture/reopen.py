"""F5.9 (L1a): a reabertura dos canais perdidos, fora de `recorder.py`. O canal cuja stream morreu (R-02, R-03) é marcado
por `_mark_lost`, que posta um `_Lost` pela fonte que a thread de trabalho conhece; o `check_devices` da interface (M-09)
registra `device_lost` e tenta reabrir de 5 em 5 s pelo id estável: o anterior fecha antes (A-15), o escolhido que não
está abre o padrão com `device_change` (A-16), eventos e log com o id do endpoint, nunca o nome do aparelho (A-22). Um
mixin do `Recorder` (D-070: um objeto à parte tocaria 26 nomes privados dele); o que vem do gravador está declarado
abaixo, como em `ui/warmup_source.py`."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..log import get_logger
from ..model import DeviceReason, EventType
from ..platform.base import AudioDevice, log_id
from .clock import Clock
from .sinks import RecorderSink

if TYPE_CHECKING:
    from .recorder import CaptureSnapshot, ChannelState, SourceSpec, _Msg

log = get_logger("capture")

REOPEN_EVERY_S = 5.0


@dataclass(frozen=True, slots=True)
class _Lost:
    channel: str
    source: int
    reason: str


class ReopenMixin:
    """A parte do `Recorder` que marca um canal como perdido e o reabre; tudo na thread da interface (a de trabalho só lê
    o `_Lost` postado e publica `lost` no snapshot)."""

    L: ChannelState
    R: ChannelState
    clock: Clock
    sink: RecorderSink
    meeting_id: int
    mic_device: AudioDevice | None
    mic_open_id: str
    output_open_id: str
    source: SourceSpec
    _l_sid: int
    _r_sid: int
    _lost_sid: dict[str, int]
    _lock: threading.Lock
    _stopped: bool
    _snap: CaptureSnapshot

    def _post(self, msg: _Msg) -> None:
        raise NotImplementedError

    def _warn(self, msg: str, type_: EventType = EventType.WARNING) -> None:
        raise NotImplementedError

    def _ms(self) -> int:
        raise NotImplementedError

    def _device_event(self, channel: str, reason: str) -> None:
        raise NotImplementedError

    def _drop_mic(self) -> None:
        raise NotImplementedError

    def _reread(self) -> bool:
        raise NotImplementedError

    def _try_open_mic(self, device: AudioDevice | None, strict: bool) -> str | None:
        raise NotImplementedError

    def _drop_source(self) -> None:
        raise NotImplementedError

    def _open_source(self, spec: SourceSpec) -> bool:
        raise NotImplementedError

    # ---------------------------------------------------------------- o canal perdido
    def _mark_lost(self, channel: str, reason: str) -> None:
        """R-02 e R-03: o canal ficou sem stream; a thread de trabalho o marca perdido e o `check_devices` (M-09) reabre de 5
        em 5 s. Pela fonte que ela conhece (a do último `_Open`), uma vez por fonte."""
        sid = self._l_sid if channel == "L" else self._r_sid
        if self._lost_sid.get(channel) != sid:
            self._lost_sid[channel] = sid
            self._post(_Lost(channel, sid, reason))

    def _output_lost(self, reason: str) -> None:
        """R-03: nem a fonte pedida nem a anterior abriram: o canal fica perdido (à vista como desconectado) e a reabertura
        de 5 em 5 s tenta a anterior."""
        self.R.active = True
        self._warn(f"saída sem loopback; tentando de novo a cada {REOPEN_EVERY_S:.0f} s: {reason}")
        self._mark_lost("R", reason)

    # ---------------------------------------------------------------- a reabertura (thread da interface)
    def check_devices(self) -> None:
        """Chamada periódica da interface (M-09): registra `device_lost` e tenta reabrir a cada 5 s pelo id estável. F5.3:
        o anterior fecha antes de cada reabertura (A-15); o escolhido que não está abre o padrão, com `device_change`
        (A-16); eventos e log com o id do endpoint (A-22)."""
        if self._stopped:
            return
        now = self.clock.rel()
        lost = self._snap.lost
        for st in (self.L, self.R):
            if st.name not in lost:
                continue
            if st.lost_since is None:
                st.lost_since = now
                self._warn(f"dispositivo do canal {st.name} parou de responder ({st.detail})", EventType.DEVICE_LOST)
            if now - st.last_reopen < REOPEN_EVERY_S:
                continue
            st.last_reopen = now
            self._reopen_lost(st)

    def _reopen_lost(self, st: ChannelState) -> None:
        with self._lock:
            if self._stopped:
                return
            before = self.mic_open_id if st.name == "L" else self.output_open_id
            ok = self._reopen_mic() if st.name == "L" else self._reopen_source()
            after = self.mic_open_id if st.name == "L" else self.output_open_id
        if not ok:
            return
        st.lost_since = None
        log.info("canal %s voltou (%s)", st.name, st.detail)
        self.sink.add_event(self.meeting_id, self._ms(), EventType.DEVICE_BACK,
                            {"channel": st.name, "detail": st.detail, "endpoint_id": log_id(after)})
        if after != before:                               # outro aparelho: o escolhido de volta ou o padrão
            wanted = (self.mic_device.id if self.mic_device else "") if st.name == "L" else self.source.output_id
            self._device_event(st.name, DeviceReason.BACK if wanted and after == wanted else DeviceReason.DEFAULT)

    def _reopen_mic(self) -> bool:
        """O L perdido (M-09): o pedido, sem exigir (ausente, o padrão: A-16); o escolhido que está mas não abre também cai no
        padrão (R-01: a reabertura não insiste só nele). Nada abriu: segue perdido."""
        self._drop_mic()                                  # U-043: a stream morta segurava o PortAudio (lista não relida)
        self._reread()                                    # A-01: o fone que voltou só entra relendo a lista
        err = self._try_open_mic(self.mic_device, strict=False)
        if err is not None and self.mic_device is not None:
            err = self._try_open_mic(None, strict=False)
        if err is not None:
            self._warn(f"microfone não iniciou: {err}")
            self._mark_lost("L", err)
        return err is None

    def _reopen_source(self) -> bool:
        """O R perdido (M-09): a fonte de agora de novo; nada abriu, segue perdido (R-03), à vista como desconectado."""
        self._drop_source()
        if self._open_source(self.source):
            return True
        self.R.active = True
        self._mark_lost("R", "a saída não reabriu")
        return False
