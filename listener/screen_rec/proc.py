"""F5.27 (lote 2): o filho da gravação da tela, `python -m listener --screen --db DIR --meeting ID`.

Lançado pelo app (`Platform.spawn_child`) ao começar a gravar a tela; conversa pelo protocolo de `client.py` (linhas JSON nos dois
sentidos) e morre com a tela (EOF no stdin). Ao nascer: rebaixa-se a abaixo do normal (`lower_own_priority`: o `spawn_child` do
Windows lança em NORMAL, a classe do ao vivo), pede os pixels físicos (`use_physical_pixels`, antes de `monitors()`: sem o Qt, um
monitor com escala sairia em pixels lógicos), abre o `Store` no papel WORKER e solta os anexos da abertura (`release_audio`: o
WORKER nasce com o áudio do mês anexado, e aqui nada lê áudio). Log próprio (`listener-screen.log`, na pasta do log do app, como o
do ao vivo) com ids, contagens, tamanhos, tempos e o número do monitor; nunca imagem, título, chave do monitor nem caminho. O
stdout do protocolo é uma cópia do descritor 1, que passa a apontar para o stderr (descartado pelo app): um `print` perdido ou o
FFmpeg não corrompem as linhas.

Threads (o `ScreenRecorder` roda numa só, a principal):
- `screen-stdin` lê os comandos e só os põe numa fila, acordando a principal; EOF ou `stop` = parar.
- A principal aplica os comandos, confere `monitors()` a cada `CHECK_S` e roda o `step` do recorder (relógio: `perf_counter`
  menos o `t0_perf` do `hello`; no Windows, o `perf_counter` vale para o sistema inteiro desde o Python 3.10). A pausa do recorder
  é derivada de dois motivos independentes, a reunião pausada e o monitor perdido: retomar a reunião com o monitor fora não volta
  a capturar, nem o monitor que volta com a reunião pausada. Monitor ausente: fecha o pedaço e emite `lost`; o mesmo monitor de
  volta (com o tamanho que tiver): retoma sozinho e emite `back`; nunca troca de monitor sozinho.
- `screen-write` grava os pedaços em ordem (`ChunkWriter`, em `chunk_writer.py`), cada um numa instrução própria, com nova tentativa em
  `OperationalError` ("database is locked": a conexão do app pode segurar a trava de escrita até 5 s); nenhum pedaço é descartado
  por isso. O `idx` começa no maior já gravado da reunião mais 1 (a tela relançada, recomeçada) e, num índice repetido (um filho
  anterior ainda gravando), relê o maior e segue. Depois do EOF, as novas tentativas têm até `DRAIN_S`, menos que o prazo do app
  antes do kill (`client.CLOSE_GRACE_S`); o `bye` sai depois disso, com os pedaços gravados e os não gravados.
`LISTENER_SCREEN=fake` troca a tela pela de `tests/fake_screen.py` (sem BitBlt), para o teste do processo de verdade."""
from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from ..config import bootstrap
from ..log import attach_file, get_logger, log_versions, setup
from ..platform.common import paths, process
from ..platform.screen_types import Frame, Monitor, ScreenRect
from ..store import Store
from .client import (BACK, BYE, ERROR, LOST, PARAMS, PAUSE, RESUME, STARTED, STATS, STATUS, STOP, SWITCH, SWITCHED,
                     ProtocolError, ScreenHello, decode_line, encode_line, find_monitor)
from .chunk_writer import DRAIN_S, RETRY_BASE_S, ChunkWriter, Emitter
from .recorder import WAIT_CAP_MS, ScreenRecorder
from .segment import Segment

log = get_logger("screen_rec.proc")

SCREEN_LOG = "listener-screen.log"
CHECK_S = 5.0                    # a spec: `monitors()` conferido a cada 5 s
PLACEHOLDER = ScreenRect(0, 0, 2, 2)  # o recorder nasce com um retângulo; com o monitor ausente desde o início, ele fica pausado


class ScreenSource(Protocol):
    def monitors(self) -> tuple[Monitor, ...]: ...
    def grab_screen(self, rect: ScreenRect) -> Frame | None: ...


class ScreenSession:
    """Uma tela do `hello` ao `bye` (`run`)."""

    def __init__(self, stdin: BinaryIO, stdout: BinaryIO, store: Store, meeting_id: int, screen: ScreenSource, *,
                 clock: Callable[[], float] = time.perf_counter, monotonic: Callable[[], float] = time.monotonic,
                 check_s: float = CHECK_S, drain_s: float = DRAIN_S, retry_base_s: float = RETRY_BASE_S):
        self.stdin = stdin
        self.out = Emitter(stdout)
        self.store, self.meeting_id, self.screen = store, meeting_id, screen
        self.clock, self.monotonic = clock, monotonic
        self.check_s, self.drain_s = check_s, drain_s
        self.writer = ChunkWriter(store, meeting_id, self.out, retry_base_s=retry_base_s)
        self.t0 = 0.0
        self.key = ""
        self.number = 0                       # o número do monitor na última leitura (o do log e das mensagens)
        self.meeting_paused = False
        self.lost = False
        self.rec: ScreenRecorder | None = None
        self._commands: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self._wake = threading.Event()
        self._stop = threading.Event()

    # ---- ciclo
    def run(self) -> int:
        if self.store.get_meeting(self.meeting_id) is None:
            return self._fail(f"reunião {self.meeting_id} não existe", 2)
        try:
            hello = ScreenHello.from_msg(decode_line(self.stdin.readline() or b"{}"))
        except ProtocolError as e:
            return self._fail(str(e), 2)
        self.t0, self.key, self.meeting_paused = hello.t0_perf, hello.monitor_key, hello.paused
        found = find_monitor(self.screen.monitors(), self.key)
        try:
            self.rec = ScreenRecorder(self.screen.grab_screen, found[1].rect if found else PLACEHOLDER, self._now_ms,
                                      on_segment=self._on_segment, on_status=self._on_status, fps=hello.fps, max_side=hello.max_side,
                                      chunk_s=hello.chunk_s)
        except ValueError as e:
            return self._fail(str(e), 2)
        self.writer.start()
        threading.Thread(target=self._read_loop, name="screen-stdin", daemon=True).start()
        if found is None:
            self._lose()
        else:
            self.number = found[0]
            self._hold()
            log.info("reunião %s: tela do monitor %d, %dx%d, %d quadro(s) por segundo, lado até %d px", self.meeting_id, self.number,
                     found[1].rect.width, found[1].rect.height, hello.fps, hello.max_side)
            self.out.emit({"type": STARTED, "pid": os.getpid(), "monitor": self.number, "width": found[1].rect.width,
                           "height": found[1].rect.height, "t_ms": self._now_ms()})
        try:
            self._loop(self.rec)
        except Exception as e:                                      # o codificador ou o print: grava o que fechou e sai com erro
            log.exception("reunião %s: a gravação da tela parou", self.meeting_id)
            self.out.emit({"type": ERROR, "message": f"{type(e).__name__}: {e}", "retry": True})
            self._finish(self.rec)
            return 1
        return self._finish(self.rec)

    def _loop(self, rec: ScreenRecorder) -> None:
        next_check = self.monotonic() + self.check_s
        while not self._stop.is_set():
            self._apply_commands(rec)
            if self.monotonic() >= next_check:
                self._check_monitor(rec)
                next_check = self.monotonic() + self.check_s
            delay = rec.step()
            self._wake.wait(min(delay, WAIT_CAP_MS) / 1000)
            self._wake.clear()

    def _finish(self, rec: ScreenRecorder) -> int:
        try:
            rec.stop()                                              # o último pedaço vai para o `screen-write`
        except Exception:
            log.exception("reunião %s: o último pedaço da tela não fechou", self.meeting_id)
        self.writer.finish(self.drain_s)
        summary = rec.stats.summary()
        log.info("reunião %s: tela encerrada: %d pedaço(s) gravado(s), %d não gravado(s), %s", self.meeting_id, self.writer.written,
                 self.writer.unwritten, summary)
        self.out.emit({"type": BYE, "stats": summary, "written": self.writer.written, "unwritten": self.writer.unwritten,
                       "t_ms": self._now_ms()})
        return 0

    def _fail(self, message: str, code: int) -> int:
        log.error("reunião %s: %s", self.meeting_id, message)
        self.out.emit({"type": ERROR, "message": message, "retry": False})
        return code

    def _now_ms(self) -> int:
        return int((self.clock() - self.t0) * 1000)

    # ---- comandos (`screen-stdin` só enfileira)
    def _read_loop(self) -> None:
        try:
            for line in iter(self.stdin.readline, b""):
                try:
                    msg = decode_line(line)
                except ProtocolError:
                    log.debug("comando ilegível (%d bytes)", len(line))
                    continue
                if msg["type"] == STOP:
                    break
                self._commands.put(msg)
                self._wake.set()
        except (OSError, ValueError) as e:
            log.debug("leitura dos comandos terminou: %s", type(e).__name__)
        self._stop.set()                                            # EOF = parar
        self._wake.set()

    def _apply_commands(self, rec: ScreenRecorder) -> None:
        while True:
            try:
                msg = self._commands.get_nowait()
            except queue.Empty:
                return
            kind = msg["type"]
            if kind in (PAUSE, RESUME):
                self.meeting_paused = kind == PAUSE
                self._hold()
            elif kind == SWITCH:
                self._switch(rec, str(msg.get("monitor_key") or ""))
            elif kind == PARAMS:
                try:
                    rec.set_params(int(msg.get("fps", rec.fps)), int(msg.get("max_side", rec.max_side)))
                    log.info("tela: %s quadro(s) por segundo, lado até %s px no próximo pedaço", msg.get("fps"), msg.get("max_side"))
                except (TypeError, ValueError) as e:
                    log.warning("parâmetros da tela recusados: %s", e)
            else:
                log.debug("comando desconhecido: %s", kind)

    def _hold(self) -> None:
        """A pausa do recorder: a reunião pausada ou o monitor fora."""
        if self.rec is None:
            return
        if self.meeting_paused or self.lost:
            self.rec.pause()
        else:
            self.rec.resume()

    # ---- monitor
    def _check_monitor(self, rec: ScreenRecorder) -> None:
        found = find_monitor(self.screen.monitors(), self.key)
        if found is None:
            if not self.lost:
                self._lose()
            return
        self.number, monitor = found
        if monitor.rect != rec.rect:
            rec.switch(monitor.rect)                                # o mesmo monitor em outra resolução ou posição
        if self.lost:
            self.lost = False
            self._hold()
            log.info("tela: o monitor %d voltou, %dx%d", self.number, monitor.rect.width, monitor.rect.height)
            self.out.emit({"type": BACK, "monitor": self.number, "width": monitor.rect.width, "height": monitor.rect.height,
                           "t_ms": self._now_ms()})

    def _lose(self) -> None:
        self.lost = True
        self._hold()                                                # o pedaço fecha no próximo `step`, já
        log.info("tela: o monitor %d saiu", self.number)
        self.out.emit({"type": LOST, "monitor": self.number or None, "t_ms": self._now_ms()})

    def _switch(self, rec: ScreenRecorder, key: str) -> None:
        if not key or (key == self.key and not self.lost):
            return
        self.key = key
        found = find_monitor(self.screen.monitors(), key)
        if found is None:
            self.number = 0
            self._lose()                                            # o pedido fica valendo: o monitor que aparecer é retomado
            return
        self.number, monitor = found
        rec.switch(monitor.rect)
        self.lost = False
        self._hold()
        log.info("tela: trocada para o monitor %d, %dx%d", self.number, monitor.rect.width, monitor.rect.height)
        self.out.emit({"type": SWITCHED, "monitor": self.number, "width": monitor.rect.width, "height": monitor.rect.height,
                       "t_ms": self._now_ms()})

    # ---- saídas do recorder (thread principal)
    def _on_segment(self, segment: Segment) -> None:
        """Nunca levanta: o recorder já soltou o pedaço (o encoder zerado antes da chamada). Só enfileira; as novas tentativas
        e a contagem dos não gravados são do `screen-write`."""
        self.writer.put(segment)
        try:
            if self.rec is not None:
                self.out.emit({"type": STATS, "stats": self.rec.stats.summary()})
        except Exception as e:
            log.debug("estatísticas da tela não emitidas: %s", type(e).__name__)

    def _on_status(self, state: str) -> None:
        self.out.emit({"type": STATUS, "state": state, "t_ms": self._now_ms()})


def serve(stdin: BinaryIO, stdout: BinaryIO, store: Store, meeting_id: int, screen: ScreenSource, **kw: Any) -> int:
    """Uma tela; `kw` vai para `ScreenSession` (os testes trocam o relógio, o intervalo da conferência e os prazos)."""
    return ScreenSession(stdin, stdout, store, meeting_id, screen, **kw).run()


def screen_log_path(store: Store) -> Path:
    base = store.get_setting("log.path") or str(store.default_log_path())
    return Path(base).expanduser().parent / SCREEN_LOG


def _source() -> Any:
    """A plataforma (o BitBlt e os monitores) ou, com `LISTENER_SCREEN=fake`, a tela de `tests/fake_screen.py`;
    `NotImplementedError` sem tela nesta plataforma."""
    if os.environ.get("LISTENER_SCREEN", "").strip().lower() == "fake":
        try:
            from tests.fake_screen import FakeScreenSource
        except ImportError as e:
            raise RuntimeError("LISTENER_SCREEN=fake exige a pasta tests/ do repositório no caminho") from e
        return FakeScreenSource()
    from ..platform import get_platform

    return get_platform()


def _protocol_stdout(sync_handle: Callable[[], None]) -> BinaryIO:
    """Cópia do descritor 1 para o protocolo; o 1 passa a apontar para o stderr, e o handle padrão do SO acompanha."""
    with suppress(Exception):
        sys.stdout.flush()
    fd = os.dup(1)
    os.dup2(2, 1)
    try:
        sync_handle()
    except Exception as e:
        log.debug("handle padrão não sincronizado: %s", e)
    return os.fdopen(fd, "wb")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="listener --screen", description="filho da gravação da tela (F5.27)")
    ap.add_argument("--db", help="pasta do banco (padrão: o ponteiro da configuração do sistema)")
    ap.add_argument("--meeting", type=int, help="reunião em gravação")
    args = ap.parse_args(argv)
    db_dir = Path(args.db) if args.db else bootstrap.read_db_dir(paths.config_dir())
    setup("INFO")
    if db_dir is None or args.meeting is None:
        print("screen: informe --db DIR e --meeting ID", file=sys.stderr)  # noqa: T201
        return 2
    try:
        source = _source()
    except (NotImplementedError, RuntimeError) as e:
        out = _protocol_stdout(process.sync_stdout_handle)
        with suppress(Exception):
            out.write(encode_line({"type": ERROR, "message": f"sem tela nesta plataforma: {e}", "retry": False}))
            out.flush()
        return 1
    out = _protocol_stdout(source.sync_stdout_handle)
    lowered, physical = source.lower_own_priority(), source.use_physical_pixels()   # os pixels físicos antes de `monitors()`
    try:
        store = Store.open_worker(db_dir)
    except Exception as e:
        print(f"screen: não abri o banco: {type(e).__name__}", file=sys.stderr)  # noqa: T201
        with suppress(Exception):                                # banco ocupado passa: o app relança uma vez
            out.write(encode_line({"type": ERROR, "message": f"banco indisponível: {e}", "retry": True}))
            out.flush()
        return 3
    with store:
        store.core.release_audio()                               # os anexos da abertura, nesta conexão (a principal)
        with suppress(Exception):
            attach_file(screen_log_path(store), store.get_setting("log.level"))
        log_versions({"papel": "screen", "pid": str(os.getpid()), "reunião": str(args.meeting),   # D-092: sem a pasta do banco
                      "abaixo do normal": str(lowered), "pixels físicos": str(physical),
                      **({"modo": "fake"} if os.environ.get("LISTENER_SCREEN") == "fake" else {})})
        return serve(sys.stdin.buffer, out, store, int(args.meeting), source)
