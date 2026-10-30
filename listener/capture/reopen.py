"""F5.9 (L1): a vida dos canais, fora de `recorder.py`. O canal cuja stream morreu (R-02, R-03) é marcado por `_mark_lost`,
que posta um `_Lost` pela fonte que a thread de trabalho conhece e guarda o motivo (`LostReason`); a vigia de 1 s
(`_poll_alive`, item 1) pergunta aos handles se a stream segue viva, porque o driver não avisa o app que saiu nem o
endpoint invalidado; o `check_devices` da interface (M-09) registra `device_lost` com o motivo e tenta reabrir de 5 em 5 s
pelo id estável: o anterior fecha antes (A-15), o escolhido que não está abre o padrão com `device_change` (A-16), eventos e
log com o id do endpoint, nunca o nome do aparelho (A-22). Um mixin do `Recorder` (D-070: um objeto à parte tocaria 26
nomes privados dele); o que vem do gravador está declarado abaixo, como em `ui/warmup_source.py`."""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ..log import get_logger
from ..model import DeviceReason, EventType, LostReason, SourceKind
from ..platform.base import AudioDevice, CaptureHandle, Platform, log_id
from .clock import Clock
from .sinks import RecorderSink

if TYPE_CHECKING:
    from .recorder import CaptureSnapshot, ChannelState, SourceSpec, _Msg

log = get_logger("capture")

REOPEN_EVERY_S = 5.0
ALIVE_EVERY_S = 1.0             # F5.9 (item 1): a prova de vida dos handles, na thread da interface (o tique é de 200 ms)


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
    platform: Platform
    clock: Clock
    sink: RecorderSink
    meeting_id: int
    mic_device: AudioDevice | None
    mic_open_id: str
    output_open_id: str
    source: SourceSpec
    _l_handle: CaptureHandle | None
    _r_handle: CaptureHandle | None
    _l_sid: int
    _r_sid: int
    _lost_sid: dict[str, int]
    _lock: threading.Lock
    _stopped: bool
    _snap: CaptureSnapshot
    _alive_at: float = -1e9                           # a última prova de vida (tempo da sessão)
    _fallback_at: float = -1e9                        # F5.9 (item 4): a última procura da raiz nova do app
    _fallback_pid: int | None = None                  # a raiz do app que falhou (não é tentada de novo)

    def _post(self, msg: _Msg) -> None:
        raise NotImplementedError

    def switch_source(self, spec: SourceSpec, reason: str = DeviceReason.USER) -> bool:
        raise NotImplementedError

    def _source_event(self, reason: str) -> None:
        raise NotImplementedError

    def _warn(self, msg: str, type_: EventType = EventType.WARNING, **data: Any) -> None:
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
    def _mark_lost(self, channel: str, reason: str, kind: str = LostReason.STREAM_STOPPED) -> None:
        """R-02 e R-03: o canal ficou sem stream; a thread de trabalho o marca perdido e o `check_devices` (M-09) reabre de 5
        em 5 s. Pela fonte que ela conhece (a do último `_Open`), uma vez por fonte. F5.9: `kind` é o motivo (`LostReason`)
        do episódio, guardado na primeira marca; uma reabertura que falha não o troca."""
        st = self.L if channel == "L" else self.R
        if st.lost_since is None:
            st.lost_reason = kind
        sid = self._l_sid if channel == "L" else self._r_sid
        if self._lost_sid.get(channel) != sid:
            self._lost_sid[channel] = sid
            self._post(_Lost(channel, sid, reason))

    def _channel_back(self, st: ChannelState) -> None:
        """O canal abriu de novo (a reabertura de 5 s, uma troca manual ou da vigia, o anterior que voltou numa troca que
        falhou): o episódio perdido acaba aqui, e a próxima perda ganha motivo e `device_lost` novos (revisão do L1). O
        `last_reopen` fica como cada caminho o deixa (a primeira reabertura do R depois de uma troca é imediata, R-03)."""
        st.lost_since, st.lost_reason = None, ""

    def _output_lost(self, reason: str, kind: str = LostReason.STREAM_STOPPED) -> None:
        """R-03: nem a fonte pedida nem a anterior abriram: o canal fica perdido (à vista como desconectado) e a reabertura
        de 5 em 5 s tenta a anterior."""
        self.R.active = True
        self._warn(f"saída sem loopback; tentando de novo a cada {REOPEN_EVERY_S:.0f} s: {reason}")
        self._mark_lost("R", reason, kind)

    def _poll_alive(self, now: float) -> None:
        """F5.9 (item 1): a cada `ALIVE_EVERY_S`, pergunta aos handles se a stream segue viva (o driver não avisa o app que
        saiu nem o endpoint invalidado); False vira o canal perdido com o motivo, e a reabertura de 5 s faz o resto. Na
        thread da interface, sem bloquear; o handle pode ser None entre uma queda e a abertura seguinte."""
        if now - self._alive_at < ALIVE_EVERY_S:
            return
        self._alive_at = now
        lost = self._snap.lost
        for st, handle in ((self.L, self._l_handle), (self.R, self._r_handle)):
            if handle is None or st.name in lost or handle.alive() is not False:
                continue
            app = st.name == "R" and self.source.kind == SourceKind.APP
            self._mark_lost(st.name, "a stream morreu sem aviso do driver",
                            LostReason.PROCESS_EXIT if app else LostReason.STREAM_STOPPED)

    def _lost_event(self, st: ChannelState) -> None:
        """`device_lost` com o motivo (F5.9): o canal que nunca abriu "não iniciou" (item 2); os demais "parou de responder".
        O `on_error` do driver não passa por `_mark_lost`: sem motivo guardado, vale `stream_stopped`."""
        st.lost_reason = st.lost_reason or LostReason.STREAM_STOPPED
        what = "não iniciou" if st.lost_reason == LostReason.NEVER_OPENED else "parou de responder"
        where = f" ({st.detail})" if st.detail else ""
        self._warn(f"dispositivo do canal {st.name} {what}{where}", EventType.DEVICE_LOST, reason=str(st.lost_reason))

    # ---------------------------------------------------------------- a reabertura (thread da interface)
    def check_devices(self) -> None:
        """Chamada periódica da interface (M-09): a prova de vida (F5.9), o `device_lost` e a reabertura a cada 5 s pelo id
        estável, e a volta ao app depois do fallback (item 4). F5.3: o anterior fecha antes de cada reabertura (A-15); o
        escolhido que não está abre o padrão, com `device_change` (A-16); eventos e log com o id do endpoint (A-22)."""
        if self._stopped:
            return
        now = self.clock.rel()
        self._poll_alive(now)
        lost = self._snap.lost
        for st in (self.L, self.R):
            if st.name not in lost:
                continue
            if st.lost_since is None:
                st.lost_since = now
                self._lost_event(st)
            if now - st.last_reopen < REOPEN_EVERY_S:
                continue
            st.last_reopen = now
            self._reopen_lost(st)
        self._check_fallback(now)

    def _fallback_app(self) -> SourceSpec | None:
        """F5.9 (item 4): a fonte de app a tentar de volta: só gravando o sistema no lugar dele (`fallback_exe`) e com a raiz
        nova lá (`root_pid`); a raiz que já falhou não é tentada de novo, porque cada tentativa fecha o sistema inteiro
        antes (A-15) e seria um buraco a cada 5 s."""
        exe = self.source.fallback_exe
        if not exe:
            return None
        pid = self.platform.root_pid(exe)
        if pid is None or pid == self._fallback_pid:
            return None
        self._fallback_pid = pid
        return replace(self.source, kind=SourceKind.APP, pid=pid, exe=exe, label=exe.removesuffix(".exe"), output_id="",
                       fallback_exe="")

    def _check_fallback(self, now: float) -> None:
        """F5.9 (item 4, pergunta 1): a cada 5 s procura a raiz nova do app; achou, volta ao app com `source_change{back}`
        (`switch_source` fecha o sistema inteiro antes, A-15, fora desta trava). Com o R perdido é a reabertura que tenta o
        app. A troca manual apaga `fallback_exe` e encerra a volta (D-067)."""
        if not self.source.fallback_exe or "R" in self._snap.lost or now - self._fallback_at < REOPEN_EVERY_S:
            return
        self._fallback_at = now
        app = self._fallback_app()
        if app is not None and self.switch_source(app, DeviceReason.BACK):
            log.info("reunião %s: o app %s voltou (pid %s): a fonte do R de volta a ele", self.meeting_id, app.exe, app.pid)

    def _reopen_lost(self, st: ChannelState) -> None:
        with self._lock:
            if self._stopped:
                return
            before = self.mic_open_id if st.name == "L" else self.output_open_id
            ok = self._reopen_mic() if st.name == "L" else self._reopen_source()
            after = self.mic_open_id if st.name == "L" else self.output_open_id
        if not ok:
            return
        self._channel_back(st)
        log.info("canal %s voltou (%s)", st.name, st.detail)
        self.sink.add_event(self.meeting_id, self._ms(), EventType.DEVICE_BACK,
                            {"channel": st.name, "detail": st.detail, "endpoint_id": log_id(after)})
        if before and after and after != before:          # outro aparelho: o escolhido de volta ou o padrão (o que nunca
            # abriu e abre agora não é troca, e o app de volta no lugar do sistema não tem endpoint: `source_change`)
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
        """O R perdido (M-09): a fonte de agora de novo; F5.9 (item 4): gravando o sistema no lugar do app, o app primeiro,
        se a raiz nova dele está lá. Nada abriu, segue perdido (R-03), à vista como desconectado."""
        self._drop_source()
        app = self._fallback_app()
        if app is not None and self._open_source(app):
            if self.source.kind == SourceKind.APP:        # o app mesmo, e não o sistema de novo por dentro
                self._source_event(DeviceReason.BACK)
            return True
        if self._open_source(self.source):
            return True
        self.R.active = True
        self._mark_lost("R", "a saída não reabriu")
        return False
