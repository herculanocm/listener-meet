"""F5.27: o laço da gravação da tela, independente de processo. O print (`grab`), o relógio da sessão (`now_ms`) e as saídas
(`on_segment`, `on_status`) chegam por parâmetro: o filho `--screen` passa o BitBlt da plataforma e o `perf_counter` menos o
`t0_perf` da sessão; os testes, fakes.

Regras (a seção Comportamento da spec):
- Um quadro a cada `1000 / fps` ms, numa grade fixa. Atrasou (o print ou a codificação passou do intervalo): sai um quadro
  já, e os outros instantes da grade que passaram são pulados e contados em `stats.skipped`, nunca enfileirados.
- O instante do quadro é o do relógio logo antes do print.
- O pedaço abre no primeiro quadro e fecha `chunk_s` segundos depois, conferido em todo `step`, com ou sem quadro novo.
  Fecham também: o primeiro print que falha, a pausa, a troca de monitor (`switch`), o quadro de outro tamanho e o `stop`.
  O pedaço termina (`t_end_ms`) no instante em que fechou; o seguinte começa no quadro seguinte. Assim, entre o fim de um
  pedaço e o começo do outro, ou não passou tempo (a virada dos 60 s, a troca de tamanho ou de monitor) ou não houve tela
  (a falha, a pausa): a exportação nunca estica um quadro velho sobre um buraco. O pedaço fechado sai por `on_segment`; sem
  nenhum quadro, nada sai.
- `set_params(fps, max_side)` vale a partir do próximo pedaço.
- O print que falha (None ou `OSError`) deixa o trecho sem quadros e é tentado de novo a cada `FAIL_RETRY_MS`, sem parar a
  gravação; essas esperas não contam como pulados. Com `FAIL_WARN_MS` desde a primeira falha seguida, `on_status("falhando")`
  sai uma vez; o primeiro quadro bom depois dele dá `on_status("ok")` (o "ok" só sai depois de um "falhando"). A pausa e a
  troca de monitor zeram a contagem do tempo.

Threads: `pause`, `resume`, `switch` e `set_params` podem vir de qualquer thread (o leitor do pipe do filho): são pedidos,
aplicados no próximo `step`, e acordam o `run`. `step`, `stop` e os callbacks rodam só na thread do laço (a do `run`, ou a
do teste que chama `step`). O log leva contagens, tamanhos e tempos, nunca a imagem nem o monitor por chave."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..log import get_logger
from ..platform.screen_types import Frame, ScreenRect
from .segment import Segment, SegmentEncoder

log = get_logger("screen_rec")

FAIL_RETRY_MS = 5_000            # o print que falha é tentado de novo a cada 5 s
FAIL_WARN_MS = 60_000            # 60 s sem quadro: "falhando", uma vez
WAIT_CAP_MS = 250                # a espera do `run` acorda ao menos assim, para ver o `stop_event`
MAX_FPS = 5                      # a spec: de 1 a 5 quadros por segundo
SAMPLES = 3_600                  # os tempos guardados para os percentis (uma hora a 1 quadro por segundo)
FAILING, OK = "falhando", "ok"


def percentile(samples: Any, q: float) -> float | None:
    """O percentil `q` (0 a 100) pela posição mais próxima, ou None sem amostras."""
    ordered = sorted(samples)
    if not ordered:
        return None
    return float(ordered[min(len(ordered) - 1, max(0, math.ceil(q / 100 * len(ordered)) - 1))])


@dataclass(slots=True)
class RecorderStats:
    """As contagens da gravação, para a sonda e o `--check`. `grab_ms` e `encode_ms` (a redução, a conversão e a
    codificação de um quadro) e `cycle_ms` (os dois, nos quadros bons) guardam as últimas `SAMPLES` medidas."""
    frames: int = 0
    skipped: int = 0
    failures: int = 0
    segments: int = 0
    bytes: int = 0
    grab_ms: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLES))
    encode_ms: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLES))
    cycle_ms: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLES))     # o print mais a codificação do quadro

    def summary(self) -> dict[str, Any]:
        def ms(v: float | None) -> float | None:
            return None if v is None else round(v, 1)

        return {"frames": self.frames, "skipped": self.skipped, "failures": self.failures, "segments": self.segments,
                "bytes": self.bytes, "grab_ms_p50": ms(percentile(self.grab_ms, 50)), "grab_ms_p95": ms(percentile(self.grab_ms, 95)),
                "encode_ms_p50": ms(percentile(self.encode_ms, 50)), "encode_ms_p95": ms(percentile(self.encode_ms, 95)),
                "cycle_ms_p50": ms(percentile(self.cycle_ms, 50)), "cycle_ms_p95": ms(percentile(self.cycle_ms, 95))}


def _check_params(fps: int, max_side: int) -> None:
    if not 1 <= fps <= MAX_FPS or max_side < 2:
        raise ValueError(f"parâmetros inválidos: {fps} quadros por segundo, lado máximo {max_side}")


class ScreenRecorder:
    """Grava o retângulo `rect` (pixels físicos) em pedaços de `chunk_s` segundos. Comece com `run(stop_event)` numa thread
    ou chame `step()` em laço (devolve os ms até o próximo compromisso); termine com `stop()`, que fecha o último pedaço
    (o `run` o chama ao sair)."""

    def __init__(self, grab: Callable[[ScreenRect], Frame | None], rect: ScreenRect, now_ms: Callable[[], int], *,
                 on_segment: Callable[[Segment], None], on_status: Callable[[str], None] = lambda _s: None,
                 fps: int = 1, max_side: int = 1920, chunk_s: int = 60):
        _check_params(fps, max_side)
        self._grab, self._now, self._on_segment, self._on_status = grab, now_ms, on_segment, on_status
        self._rect, self._fps, self._max_side, self._chunk_ms = rect, fps, max_side, chunk_s * 1000
        self.stats = RecorderStats()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._want: dict[str, Any] = {}           # os pedidos de outras threads, aplicados no `step`
        self._want_params = (fps, max_side)
        self._encoder: SegmentEncoder | None = None
        self._chunk_open_ms = 0
        self._next_ms: float | None = None        # o próximo instante da grade; None = já
        self._fail_since: int | None = None
        self._warned = False
        self._paused = False
        self._stopped = False

    # ------------------------------------------------------------ estado e pedidos (qualquer thread)
    @property
    def rect(self) -> ScreenRect:
        return self._rect

    @property
    def fps(self) -> int:
        """A taxa do pedaço em gravação (a pedida por `set_params` vale no próximo)."""
        return self._fps

    @property
    def max_side(self) -> int:
        return self._max_side

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._request("pause", True)

    def resume(self) -> None:
        self._request("pause", False)

    def switch(self, rect: ScreenRect) -> None:
        """Outro monitor: fecha o pedaço e grava `rect` num pedaço novo, já no próximo `step`."""
        self._request("rect", rect)

    def set_params(self, fps: int, max_side: int) -> None:
        """A taxa e o lado máximo do próximo pedaço; `ValueError` fora de 1 a `MAX_FPS` ou com lado menor que 2."""
        _check_params(fps, max_side)
        self._request("params", (fps, max_side))

    def _request(self, key: str, value: Any) -> None:
        with self._lock:
            self._want[key] = value
        self._wake.set()

    # ------------------------------------------------------------ o laço (só a thread do laço)
    def run(self, stop_event: threading.Event) -> None:
        """Grava até `stop_event`; a espera é um `Event.wait` que os pedidos acordam. Fecha o último pedaço ao sair."""
        try:
            while not stop_event.is_set():
                delay = self.step()
                self._wake.wait(min(delay, WAIT_CAP_MS) / 1000)
                self._wake.clear()
        finally:
            self.stop()

    def step(self) -> int:
        """Aplica os pedidos e, se é a hora, tira e codifica um quadro; o pedaço vencido fecha nesse quadro, e o seguinte abre no
        mesmo instante. Devolve os ms até o próximo quadro."""
        if self._stopped:
            return WAIT_CAP_MS
        now = self._now()
        self._apply_requests(now)
        if self._paused:
            return WAIT_CAP_MS
        if self._next_ms is None or now >= self._next_ms:
            self._tick(now)
            now = self._now()
        return self._delay(now)

    def stop(self) -> None:
        """Fecha e entrega o último pedaço; depois disso o `step` não faz nada. Idempotente."""
        if self._stopped:
            return
        self._close(self._now())
        self._stopped = True

    def _delay(self, now: int) -> int:
        due = self._next_ms if self._next_ms is not None else now
        return max(1, int(due - now))

    def _apply_requests(self, now: int) -> None:
        with self._lock:
            want, self._want = self._want, {}
        if "params" in want:
            self._want_params = want["params"]
        if "rect" in want and want["rect"] != self._rect:
            self._close(now)
            self._rect = want["rect"]
            self._restart()
            log.info("tela: monitor trocado, %dx%d", self._rect.width, self._rect.height)
        if "pause" in want and want["pause"] != self._paused:
            self._paused = want["pause"]
            if self._paused:
                self._close(now)
            self._restart()
            log.info("tela: %s", "pausada" if self._paused else "retomada")

    def _restart(self) -> None:
        """O próximo quadro já, e a contagem do tempo sem quadro do zero: a falha que continua avisa de novo aos 60 s."""
        self._next_ms = None
        self._fail_since = None
        self._warned = False           # a falha que segue depois de pausar ou trocar avisa de novo aos 60 s (revisão do diff)

    def _tick(self, now: int) -> None:
        period = 1000 / self._fps
        if self._next_ms is not None and self._fail_since is None:
            missed = int((now - self._next_ms) // period)      # os instantes da grade que passaram sem quadro
            self.stats.skipped += missed
            self._next_ms += (missed + 1) * period
        else:
            self._next_ms = now + period
        t0 = time.perf_counter()
        frame = self._grab_frame()
        grab_ms = (time.perf_counter() - t0) * 1000
        self.stats.grab_ms.append(grab_ms)
        if frame is None:
            self._failed(now)
            return
        self._recovered()
        self.stats.cycle_ms.append(grab_ms + self._encode(frame, now))

    def _grab_frame(self) -> Frame | None:
        try:
            return self._grab(self._rect)
        except OSError as e:                                   # o print falhou: buraco e nova tentativa, nunca a queda
            log.debug("tela: print falhou: %s", type(e).__name__)
            return None

    def _failed(self, now: int) -> None:
        self.stats.failures += 1
        if self._fail_since is None:
            self._fail_since = now
            self._close(now)                                   # o buraco fica entre dois pedaços, nunca dentro de um
        elif not self._warned and now - self._fail_since >= FAIL_WARN_MS:
            self._warned = True
            log.info("tela: %d s sem quadro", (now - self._fail_since) // 1000)
            self._on_status(FAILING)
        self._next_ms = now + FAIL_RETRY_MS

    def _recovered(self) -> None:
        self._fail_since = None
        if self._warned:
            self._warned = False
            log.info("tela: quadros de volta")
            self._on_status(OK)

    def _encode(self, frame: Frame, now: int) -> float:
        """Codifica o quadro no pedaço (abrindo outro se preciso) e devolve os ms de `add`."""
        if self._encoder is not None and self._encoder.src_size != (frame.width, frame.height):
            self._close(now)                                   # o monitor mudou de resolução: outro pedaço
        elif self._encoder is not None and now - self._chunk_open_ms >= self._chunk_ms:
            self._close(now)                                   # a virada dos 60 s num quadro: o seguinte abre no mesmo instante
        if self._encoder is None:
            new_fps, self._max_side = self._want_params
            if new_fps != self._fps:
                self._fps = new_fps
                self._next_ms = now + 1000 / new_fps
            self._encoder = SegmentEncoder(frame.width, frame.height, self._max_side, self._fps)
            self._chunk_open_ms = now
        t0 = time.perf_counter()
        self._encoder.add(frame, now)
        elapsed = (time.perf_counter() - t0) * 1000
        self.stats.encode_ms.append(elapsed)
        self.stats.frames += 1
        return elapsed

    def _close(self, now: int) -> None:
        encoder, self._encoder = self._encoder, None
        if encoder is None:
            return
        segment = encoder.close(now)
        if segment is None:
            return
        self.stats.segments += 1
        self.stats.bytes += len(segment.data)
        log.info("tela: pedaço %d, %d quadros, %dx%d, %d kB, %d a %d ms", self.stats.segments, segment.frames, segment.width,
                 segment.height, len(segment.data) // 1024, segment.t_start_ms, segment.t_end_ms)
        self._on_segment(segment)


__all__ = ["FAILING", "FAIL_RETRY_MS", "FAIL_WARN_MS", "MAX_FPS", "OK", "RecorderStats", "ScreenRecorder", "percentile"]
