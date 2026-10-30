"""F5.9 (item 7): o eco depois de uma troca de microfone. O eco só era conferido no warm-up: trocar no meio para a webcam
com som nos alto-falantes punha "eles" no canal "você" sem aviso. Depois de cada `device_change` no L, a vigia acumula
5 s de L e R pelo desvio ao vivo (`tap`, O(1): só guarda a referência do bloco; mudo, pausa e buraco zeram a janela, que
é contígua e de áudio de verdade) e roda uma vez, numa thread própria, a correlação do warm-up (`estimate_offset`); uma
rodada por troca; sem áudio em 60 s, desiste calada. O resultado fica num campo que a interface lê (`poll`), que decide
pelo limiar `capture.echo_corr_min`. Só correlação e atraso: nenhum áudio sai daqui."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..log import get_logger
from .align import estimate_offset
from .chunker import TapFlag

log = get_logger("capture.echo")

WINDOW_S = 5.0                 # o que a correlação precisa de cada canal
GIVE_UP_S = 60.0               # sem 5 s de áudio contíguo até aqui, a rodada acaba sem resultado
SKIP = TapFlag.SILENCED | TapFlag.GAP


@dataclass(frozen=True, slots=True)
class EchoResult:
    correlation: float
    offset_ms: float


class EchoWatch:
    def __init__(self, window_s: float = WINDOW_S, give_up_s: float = GIVE_UP_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._window, self._give_up, self._clock = window_s, give_up_s, clock
        self._lock = threading.Lock()
        self._armed = False
        self._since = 0.0
        self._blocks: dict[str, list[np.ndarray]] = {"L": [], "R": []}
        self._first: dict[str, int | None] = {"L": None, "R": None}
        self._have: dict[str, int] = {"L": 0, "R": 0}
        self._result: EchoResult | None = None
        self._thread: threading.Thread | None = None
        self._round = 0                                    # a troca de agora: a thread de uma troca anterior não grava
        self.rounds = 0                                    # instrumentação: correlações rodadas (contada por último)

    def arm(self) -> None:
        """Depois de um `device_change` no L: uma rodada nova (a anterior, acumulando ou já na thread, é descartada)."""
        with self._lock:
            self._reset()
            self._round += 1
            self._armed, self._since, self._result = True, self._clock(), None

    @property
    def armed(self) -> bool:
        return self._armed

    def _reset(self) -> None:
        self._blocks, self._first, self._have = {"L": [], "R": []}, {"L": None, "R": None}, {"L": 0, "R": 0}

    def tap(self, channel: str, start_frame: int, rate: int, block: np.ndarray, flags: int) -> None:
        """Na thread de trabalho, O(1): guarda a referência do bloco; com os 5 s dos dois canais, dispara a correlação."""
        with self._lock:
            if not self._armed:
                return
            if flags & SKIP:
                self._reset()                              # mudo, pausa ou buraco: a janela recomeça
                return
            if self._first[channel] is None:
                self._first[channel] = start_frame
            self._blocks[channel].append(block)
            self._have[channel] += len(block)
            need = int(self._window * rate)
            if self._have["L"] < need or self._have["R"] < need:
                return
            self._armed = False
            args = (self._blocks, dict(self._first), rate, need, self._round)
            self._reset()
        self._thread = threading.Thread(target=self._run, args=args, name="echo-watch", daemon=True)
        self._thread.start()

    def _run(self, blocks: dict[str, list[np.ndarray]], first: dict[str, int | None], rate: int, need: int, round_no: int) -> None:
        try:
            res = estimate_offset(*_aligned(blocks, first, need), rate)
            if res is None:
                log.info("eco depois da troca de microfone: sinal insuficiente para correlacionar")
            else:
                self._store(round_no, float(res["correlation"]), float(res["offset_ms"]))
        except Exception as e:  # a rodada acaba sem resultado, com o motivo no log, e a vigia segue para a próxima troca
            log.warning("eco depois da troca de microfone: a correlação falhou: %s", e)
        finally:
            self.rounds += 1                               # por último: quem espera a rodada já acha o resultado

    def _store(self, round_no: int, correlation: float, offset_ms: float) -> None:
        with self._lock:
            if round_no != self._round:                    # outra troca no meio: o resultado é do microfone antigo
                log.info("eco depois da troca de microfone: o resultado é da troca anterior; descartado")
                return
            self._result = EchoResult(correlation, offset_ms)
        log.info("eco depois da troca de microfone: correlação %.3f, atraso %.0f ms", correlation, offset_ms)

    def poll(self) -> EchoResult | None:
        """Na thread da interface: o resultado da rodada, uma vez; a rodada sem 5 s de áudio em `give_up_s` desiste calada."""
        with self._lock:
            if self._armed and self._clock() - self._since > self._give_up:
                self._armed = False
                self._reset()
                log.debug("eco depois da troca de microfone: sem %.0f s de áudio em %.0f s; desisto", self._window, self._give_up)
            result, self._result = self._result, None
            return result


def _aligned(blocks: dict[str, list[np.ndarray]], first: dict[str, int | None], need: int) -> tuple[np.ndarray, np.ndarray]:
    """Os dois canais na mesma linha do tempo (o `start_frame` do primeiro bloco de cada um), cortados em `need` quadros."""
    l = np.concatenate(blocks["L"]).astype(np.float32, copy=False)
    r = np.concatenate(blocks["R"]).astype(np.float32, copy=False)
    shift = int(first["L"] or 0) - int(first["R"] or 0)
    if shift > 0:
        r = r[shift:]
    elif shift < 0:
        l = l[-shift:]
    n = min(need, len(l), len(r))
    return l[:n].copy(), r[:n].copy()


__all__ = ["GIVE_UP_S", "WINDOW_S", "EchoResult", "EchoWatch"]
