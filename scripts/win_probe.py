"""Verificar no Windows sem o dono (D-045; backlog, itens 3 e 8): sondas do app de verdade no Windows nativo, usadas em 25
e 26/10 (D-050). Não fazem parte do produto. Cada subcomando roda no seu processo (um QApplication por processo) e imprime
linhas JSON; os números vão para specs/decisoes.md (Medições).

  python scripts/win_probe.py app [--seconds 20] [--hotkey]   o app real num perfil isolado: abertura, bandeja, RAM e CPU
                                                              parado, o atalho do Histórico e a saída pelo "Sair"
  python scripts/win_probe.py shots [--out PASTA]             as telas no DPI real sem aparecer na tela do usuário: um PNG
                                                              por tela e por aba (F5.3: a fila nos seis estados e a Ajuda;
                                                              um aviso de cada tipo e o grupo "Idioma e avisos"; o widget
                                                              com o botão 🎧 e a linha "novo: … Usar", e o painel dos
                                                              dispositivos com os aparelhos reais desta máquina)
  python scripts/win_probe.py player [--seconds 20]           o player no dispositivo de saída padrão tocando SILÊNCIO:
                                                              primeiro som a frio e pré-aquecido, buracos sem carga e com
                                                              a CPU ocupada abaixo do normal, pausa confirmada
  python scripts/win_probe.py export                          .md com o destino aberto por outro processo e copiar e sair

app     APPDATA e LOCALAPPDATA apontam para uma pasta temporária antes de o app ler qualquer pasta: nada do perfil do
        usuário é lido nem escrito. O ícone aparece na bandeja durante a medida. A CPU parada é medida depois do
        pré-aquecimento do áudio (U-031). Com --hotkey, Ctrl+Alt+Shift+H é simulado (keybd_event; o RegisterHotKey do app
        consome a combinação) e o Histórico abre na frente por uns segundos.
shots   janelas reais em (-10000, -10000) com WA_ShowWithoutActivating: o DPI vem da tela de verdade e nada rouba o foco
        (com WA_DontShowOnScreen os diálogos saem em DPR 1; a plataforma offscreen não tem as fontes do Windows).
player  a reunião é silêncio (nada audível) em pedaços de 60 s; o primeiro som roda em processos novos, um frio e um com o
        `prewarm_audio` antes; os buracos vêm da linha "player: reunião N: … buraco(s)" do log; a carga são processos
        `while True: pass` abaixo do normal, como o worker pesado.
export  o destino aberto sem e com FILE_SHARE_DELETE (Word e Excel seguram o arquivo); copiar e sair num processo filho que
        sai pelo laço de eventos, como o app; o texto da área de transferência do usuário é guardado e devolvido.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
SR = 16000


def out(stage: str, **kw) -> None:
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False, default=str), flush=True)


def silent_meeting(store, seconds: int, chunk_s: int = 60) -> int:
    """Uma reunião de silêncio (L e R zerados) em pedaços de `chunk_s`, como o app grava."""
    import numpy as np

    from listener.capture.audio import encode_flac

    mid = store.create_meeting("Silêncio", None, None, None)
    for ch in ("L", "R"):
        for idx in range(seconds // chunk_s):
            x = np.zeros(SR * chunk_s, np.float32)
            store.insert_chunk(mid, ch, idx, idx * chunk_s * 1000, len(x), SR, 0, encode_flac(x, SR))
    store.finish_meeting(mid, seconds * 1000)
    return mid


def discard(w) -> None:
    """R-06: a janela da foto é apagada, não só fechada: sem dono e viva até a derrubada do PySide, seria destruída fora de
    ordem na saída (0xC0000409)."""
    w.close()
    w.deleteLater()


def devices_shots(p, shot) -> None:
    """F5.3 L4: com os aparelhos reais desta máquina. O "novo" do painel marca a última saída (como se tivesse chegado depois
    do começo da gravação) só para a foto; a linha do widget mostra o nome do microfone padrão."""
    from listener.capture.recorder import SourceSpec
    from listener.ui.devices_panel import DevicesPanel, PanelView, mic_rows, output_rows, panel_notes
    from listener.ui.widget import RecordingWidget

    reading = p.audio_endpoints(refresh_names=True)
    mic = reading.mic(reading.capture_default) if reading is not None else None
    rw = RecordingWidget()
    rw.update_state(125, {"L": 0.1, "R": 0.05}, {"L": "ok", "R": "ok", "db": "ok"}, False, False, 0)
    rw.show_alert(f"novo: {mic.name if mic is not None else 'Fone de ouvido (Hands-Free)'}", "Usar", "use")
    shot(rw, "widget-novo-usar")
    discard(rw)
    start = reading.ids() - {reading.render[-1].id} if reading is not None and reading.render else frozenset()
    for name, source in (("painel-dispositivos", SourceSpec("system", label="Sistema inteiro")),
                         ("painel-dispositivos-app", SourceSpec("app", pid=1, exe="ms-teams.exe", label="ms-teams"))):
        mics = mic_rows(reading, p.list_input_devices() if reading is None else [], None, frozenset(start))
        panel = DevicesPanel()
        panel.show_view(PanelView(mics, output_rows(reading, source, frozenset(start)), panel_notes(reading, source, mics)))
        panel.set_status("Microfone trocado.")
        panel.adjustSize()
        shot(panel, name)
        discard(panel)


def queue_jobs(store, first_mid: int) -> None:
    """A fila nos seis estados para a foto (F5.3 L2, U-047): concluído, erro, cancelado (hachurado), pausado, rodando a 42 %
    e pendente devolvido à fila com 30 %, mais a linha agregada do índice rodando a 55 %. Cada tipo de job só tem um claim
    vivo por vez: um tipo por claim."""
    from listener.model import JobKind, JobStatus, JobStep

    ids = [first_mid]
    for title in ("Diretoria", "Planejamento", "Reunião de vendas"):
        mid = store.create_meeting(title, "teams", None, None)
        store.finish_meeting(mid, 1_800_000)
        ids.append(mid)
    a, b, c, d = ids
    timings = {"diarize": {"audio_s": 1800.0, "speech_s": 0.0, "wall_s": 610.4, "model": "pyannote"},
               "transcribe_r": {"audio_s": 1800.0, "speech_s": 1100.0, "wall_s": 737.2, "model": "large-v3-turbo"}}
    done = store.jobs.enqueue(a)
    store.jobs.claim(pid=1, kinds=("meeting",))
    store.jobs.finish(done, JobStatus.DONE, timings=timings)
    error = store.jobs.enqueue(a, JobKind.LLM, {"tasks": ["identify", "minutes", "executive", "title"]})
    store.jobs.claim(pid=2, kinds=("llm",))
    store.jobs.set_progress(error, JobStep.MINUTES, 0.25, {"identify": {"wall_s": 21.5}, "llm": {"cost_usd": 0.02, "done": ["identify"]}})
    store.jobs.finish(error, JobStatus.ERROR, error="ata falhou: sem rede")
    cancelled = store.jobs.enqueue(b, JobKind.REPROCESS)
    store.jobs.claim(pid=3, kinds=("reprocess",))
    store.jobs.set_progress(cancelled, JobStep.TRANSCRIBE_R, 0.6, timings)
    store.jobs.finish(cancelled, JobStatus.CANCELLED)
    paused = store.jobs.enqueue(b, JobKind.LLM, {"tasks": ["minutes", "title"]})
    store.jobs.claim(pid=4, kinds=("llm",))
    store.jobs.set_progress(paused, JobStep.MINUTES, 0.5)
    store.jobs.set_status(paused, JobStatus.PAUSED, JobStep.WAITING)
    running = store.jobs.enqueue(c)
    store.jobs.claim(pid=5, kinds=("meeting",))
    store.jobs.set_progress(running, JobStep.TRANSCRIBE_R, 0.42, timings)
    pending = store.jobs.enqueue(d, JobKind.REPROCESS)
    store.jobs.claim(pid=6, kinds=("reprocess",))
    store.jobs.set_progress(pending, JobStep.DIARIZE, 0.3)
    store.jobs.finish(pending, JobStatus.ERROR, error="worker parou sem concluir")
    store.jobs.retry(pending)
    index = store.jobs.enqueue_index(a, "pipeline")
    store.jobs.enqueue_index(b, "pipeline")
    store.jobs.claim(pid=7, kinds=("index",))
    store.jobs.set_progress(index, JobStep.INDEX_VECTORS, 0.55)


# ---------------------------------------------------------------------------- app
def cmd_app(args) -> None:
    profile = Path(tempfile.mkdtemp(prefix="listener-win-probe-"))
    os.environ["APPDATA"] = str(profile / "roaming")
    os.environ["LOCALAPPDATA"] = str(profile / "local")
    os.environ.pop("QT_QPA_PLATFORM", None)
    t0 = time.perf_counter()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from listener.config import bootstrap
    from listener.log import set_crash_notifier, setup
    from listener.platform import get_platform
    from listener.store import Store
    from listener.ui.app import attach_log
    from listener.ui.common import install_translations

    t_import = time.perf_counter()
    setup()
    platform = get_platform()
    platform.app_init()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("listener-meet")
    app.setQuitOnLastWindowClosed(False)
    translated = install_translations(app) is not None
    db_dir = profile / "db"
    with Store(db_dir) as seed:
        seed.upsert_me("Teste")
        seed.seed_default_presets()
    bootstrap.write_db_dir(platform.config_dir(), db_dir)
    t_seed = time.perf_counter()
    store = Store(bootstrap.read_db_dir(platform.config_dir()))
    attach_log(store)
    from listener.ui.controller import PREWARM_AUDIO_MS, Controller

    t_store = time.perf_counter()
    controller = Controller(app, platform, store, first_run=False)
    set_crash_notifier(controller.notify)
    app.aboutToQuit.connect(controller.emergency_stop)
    controller.start()
    t_ready = time.perf_counter()
    result: dict = {"profile": str(profile), "translations": translated, "import_s": round(t_import - t0, 2),
                    "store_and_controller_import_s": round(t_store - t_seed, 2), "controller_s": round(t_ready - t_store, 2),
                    "ready_s": round(t_ready - t0, 2), "tray_available": QSystemTrayIcon.isSystemTrayAvailable(),
                    "tray": controller.tray is not None, "schema": store.schema_version(),
                    "screens": [{"name": s.name(), "dpr": s.devicePixelRatio(), "size": [s.size().width(), s.size().height()]}
                                for s in app.screens()]}
    marks: dict = {}

    def sample(name: str) -> None:
        marks[name] = (time.perf_counter(), time.process_time(), platform.memory_rss())

    def press_history_hotkey() -> None:
        u = ctypes.windll.user32
        for vk in (0x11, 0x12, 0x10):                     # Ctrl, Alt, Shift
            u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(0x48, 0, 0, 0)                      # H
        u.keybd_event(0x48, 0, 2, 0)
        for vk in (0x10, 0x12, 0x11):
            u.keybd_event(vk, 0, 2, 0)
        marks["hotkey_sent"] = time.perf_counter()

    def check_history() -> None:
        from PySide6.QtWidgets import QApplication as QApp

        win = getattr(controller, "_history", None)
        fg = ctypes.windll.user32.GetForegroundWindow()
        focus = QApp.focusWidget()
        result["hotkey"] = {"history_open": bool(win is not None and win.isVisible()),
                            "foreground_is_history": bool(win is not None and int(fg) == int(win.winId())),
                            "focus_widget": type(focus).__name__ if focus else None}
        if win is not None:
            win.hide()

    def finish() -> None:
        sample("idle_end")
        (w0, c0, r0), (w1, c1, r1) = marks["idle_start"], marks["idle_end"]
        result.update({"rss_mb_start": round((r0 or 0) / 1e6, 1), "rss_mb_end": round((r1 or 0) / 1e6, 1),
                       "idle_wall_s": round(w1 - w0, 1), "idle_cpu_pct_of_one_core": round(100 * (c1 - c0) / (w1 - w0), 2)})
        marks["quit_at"] = time.perf_counter()
        controller.quit()

    idle_from_ms = PREWARM_AUDIO_MS + 2000                 # depois do pré-aquecimento do áudio (U-031)
    QTimer.singleShot(idle_from_ms, lambda: sample("idle_start"))
    if args.hotkey:
        QTimer.singleShot(idle_from_ms + 1000, press_history_hotkey)
        QTimer.singleShot(idle_from_ms + 3000, check_history)
    QTimer.singleShot(int(idle_from_ms + args.seconds * 1000), finish)
    code = app.exec()
    result["exit_code"] = code
    result["quit_to_return_s"] = round(time.perf_counter() - marks.get("quit_at", time.perf_counter()), 2)
    out("app", **result)


# ---------------------------------------------------------------------------- shots
def cmd_shots(args) -> None:
    os.environ.pop("QT_QPA_PLATFORM", None)
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication, QGroupBox, QTabWidget

    app = QApplication(sys.argv[:1])
    from listener.platform import get_platform
    from listener.store import Store
    from listener.ui.common import install_translations
    from listener.ui.history import HistoryWindow
    from listener.ui.meeting import MeetingWindow
    from listener.ui.notices import Notice, NoticeAction, NoticeKind, Target
    from listener.ui.player import Player
    from listener.ui.queue import BUTTON_HELP, COLUMN_HELP, QueueDialog
    from listener.ui.queue_help import QueueHelpDialog
    from listener.ui.settings_min import SettingsDialog
    from listener.ui.summary import SummaryDialog
    from listener.ui.toast import Toast
    from listener.ui.tray import STATE_COLORS, make_icon
    from listener.ui.warmup import WarmupDialog
    from listener.ui.widget import MarkerPopup, RecordingWidget
    from listener.ui.wizard import FirstRunWizard

    install_translations(app)
    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="listener-shots-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    screen = app.primaryScreen()
    shots: list[dict] = []

    def shot(w, name: str) -> None:
        w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        w.move(-10000, -10000)
        w.show()
        w.move(-10000, -10000)
        app.processEvents()
        deadline = time.perf_counter() + 1.0
        while w.devicePixelRatioF() != screen.devicePixelRatio() and time.perf_counter() < deadline:
            time.sleep(0.02)
            app.processEvents()
        pix = w.grab()
        path = out_dir / f"{len(shots):02d}-{name}.png"
        pix.save(str(path))
        shots.append({"file": path.name, "logical": [w.width(), w.height()], "pixels": [pix.width(), pix.height()],
                      "dpr": w.devicePixelRatioF()})

    def tabs_of(w, name: str) -> None:
        for tw in w.findChildren(QTabWidget)[:1]:
            for i in range(tw.count()):
                tw.setCurrentIndex(i)
                app.processEvents()
                shot(w, f"{name}-{tw.tabText(i).replace(' ', '_').replace('/', '_')}")

    def part(w, name: str) -> None:
        """Um pedaço de uma janela que já está na foto (F5.3: o grupo "Idioma e avisos" das Configurações)."""
        pix = w.grab()
        path = out_dir / f"{len(shots):02d}-{name}.png"
        pix.save(str(path))
        shots.append({"file": path.name, "logical": [w.width(), w.height()], "pixels": [pix.width(), pix.height()],
                      "dpr": w.devicePixelRatioF()})

    p = get_platform()
    p.app_init()
    store = Store(Path(tempfile.mkdtemp(prefix="listener-shots-db-")))
    store.upsert_me("Teste")
    store.seed_default_presets()
    for make, name in ((lambda: FirstRunWizard(p, None), "assistente"), (lambda: WarmupDialog(p, store), "warmup"),
                       (lambda: MarkerPopup(12.0, p), "marcador")):
        w = make()
        shot(w, name)
        discard(w)
    wiz = FirstRunWizard(p, None)                         # U-038: a página das chaves, aberta direto (sem validar as anteriores)
    wiz.setStartId(wiz.pageIds()[[wiz.page(i) for i in wiz.pageIds()].index(wiz.keys_page)])
    wiz.restart()
    shot(wiz, "assistente-chaves")
    discard(wiz)
    rw = RecordingWidget()
    rw.update_state(65, {"L": 0.1, "R": 0.01}, {"L": "ok", "R": "warn", "db": "ok"}, False, False, 2)
    rw.show_alert("teste de alerta", "Dispositivos…", "devices")
    shot(rw, "widget")
    discard(rw)
    devices_shots(p, shot)                                # F5.3 L4: o widget com "novo: … Usar" e o painel
    sd = SettingsDialog(store, p, {})
    shot(sd, "configuracoes")
    tabs_of(sd, "configuracoes")
    part(next(g for g in sd.findChildren(QGroupBox) if g.title() == "Idioma e avisos"), "configuracoes-idioma-e-avisos")
    discard(sd)
    mid = store.create_meeting("Comitê de investimentos", "sistema", None, None)
    store.finish_meeting(mid, 1000)
    su = SummaryDialog(store, mid, {"duration_s": 1.0, "warnings": []})
    shot(su, "resumo-gravacao")
    discard(su)
    player = Player(store)
    mw = MeetingWindow(store, mid, player=player)
    shot(mw, "reuniao")
    tabs_of(mw, "reuniao")
    discard(mw)
    hw = HistoryWindow(store)
    shot(hw, "historico")
    tabs_of(hw, "historico")
    hw.page.shutdown()
    discard(hw)
    queue_jobs(store, mid)                                # F5.3 L2: a barra em cada estado, a faixa do rodando e o índice
    qd = QueueDialog(store)
    shot(qd, "fila")
    hd = QueueHelpDialog(BUTTON_HELP, COLUMN_HELP, qd)
    shot(hd, "fila-ajuda")
    discard(hd)
    qd._timer.stop()
    discard(qd)
    ready = Notice(NoticeKind.READY, "Comitê de investimentos: ata pronta · US$ 0,32", "Ata pronta · US$ 0,32",
                   NoticeAction(Target.MEETING, mid))
    for name, notice, count, private in (                 # F5.3 L3 (U-048): um aviso de cada tipo e o gravando só com o tipo
            ("aviso-pronto", ready, 1, False),
            ("aviso-informacao", Notice(NoticeKind.INFO, "2 reunião(ões) processada(s) aguardando revisão de falantes."), 3, False),
            ("aviso-erro", Notice(NoticeKind.ERROR, "Planejamento: erro na LLM (etapa: ata); veja o motivo na fila.",
                                  "Erro na LLM (etapa: ata); veja o motivo na fila.", NoticeAction(Target.QUEUE)), 1, False),
            ("aviso-gravando-so-o-tipo", ready, 1, True)):
        toast = Toast(notice, 6000, count)
        toast.set_private(private)
        shot(toast, name)
        discard(toast)
    player.shutdown()
    for state, color in STATE_COLORS.items():
        make_icon(color).pixmap(32, 32).save(str(out_dir / f"tray-{state}.png"))
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)   # R-06: as janelas apagadas antes do banco fechar
    store.close()
    out("shots", folder=str(out_dir), screen={"name": screen.name(), "dpr": screen.devicePixelRatio()}, shots=shots)


# ---------------------------------------------------------------------------- player
def cmd_first(args) -> None:
    """(processo novo) Tempo do play até a posição andar, a frio ou com o pré-aquecimento do app antes."""
    from listener.store import Store
    from listener.ui.player import PlaybackEngine, PlayRequest, prewarm_audio, sounddevice_output

    store = Store(Path(tempfile.mkdtemp(prefix="listener-first-")))
    mid = silent_meeting(store, 60)
    prewarm_s = None
    if args.prewarm:
        t0 = time.perf_counter()
        prewarm_audio().join(30)
        prewarm_s = round(time.perf_counter() - t0, 3)
    eng = PlaybackEngine(store, sounddevice_output)
    t_play = time.perf_counter()
    eng.play(PlayRequest(mid, 0, title="Silêncio"))
    first = None
    while time.perf_counter() - t_play < 15.0:
        if eng.snapshot().t_ms > 0:
            first = round(time.perf_counter() - t_play, 3)
            break
        time.sleep(0.002)
    eng.stop()
    eng.shutdown()
    store.close()
    print(json.dumps({"prewarm": bool(args.prewarm), "prewarm_s": prewarm_s, "first_audio_s": first}), flush=True)


def cmd_player(args) -> None:
    import logging

    from listener.log import setup
    from listener.store import Store
    from listener.ui.player import PlaybackEngine, PlayRequest, sounddevice_output

    for prewarm in (False, True):
        argv = [sys.executable, str(Path(__file__).resolve()), "_first", *(["--prewarm"] if prewarm else [])]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603
        line = (done.stdout.strip().splitlines() or ["{}"])[-1]
        out("player_first_sound", **json.loads(line))

    class Holes(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.lines: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            if "player" in record.name:
                self.lines.append(record.getMessage())

    setup("INFO")
    holes = Holes()
    logging.getLogger("listener").addHandler(holes)
    store = Store(Path(tempfile.mkdtemp(prefix="listener-player-")))
    mid = silent_meeting(store, 180)

    def run(label: str, start_ms: int) -> None:
        eng = PlaybackEngine(store, sounddevice_output)
        holes.lines.clear()
        eng.play(PlayRequest(mid, start_ms, title="Silêncio"))
        time.sleep(args.seconds)
        t = time.perf_counter()
        playing, confirmed = eng.pause_and_wait("sonda", 0.5)
        pause_ms = round((time.perf_counter() - t) * 1000, 1)
        eng.stop()
        clean = eng.shutdown()
        out("player_play", load=label, seconds=args.seconds, was_playing=playing, pause_confirmed=confirmed, pause_wait_ms=pause_ms,
            shutdown_clean=clean, log=holes.lines[-2:])

    run("sem carga", 0)
    burners = [subprocess.Popen([sys.executable, "-c", "while True: pass"], creationflags=BELOW_NORMAL_PRIORITY_CLASS)  # noqa: S603
               for _ in range(max(1, (os.cpu_count() or 4) - 2))]
    try:
        time.sleep(1.0)
        run(f"{len(burners)} processos abaixo do normal", 60_000)
    finally:
        for b in burners:
            b.kill()
        for b in burners:
            b.wait()
    store.close()


# ---------------------------------------------------------------------------- export
HOLD = r"""
import ctypes, sys, time
from ctypes import wintypes
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
share = 0x1 | 0x2 | (0x4 if sys.argv[2] == "delete" else 0)
h = k32.CreateFileW(sys.argv[1], 0x80000000, share, None, 3, 0x80, None)
print("held" if h and h != wintypes.HANDLE(-1).value else f"falhou {ctypes.get_last_error()}", flush=True)
time.sleep(30)
"""


def cmd_copy_exit(_args) -> None:
    """(processo filho) Copia texto e HTML pelo `copy_rich` do app e sai pelo laço de eventos, como o app sai."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    from listener.ui.common import copy_rich

    QTimer.singleShot(0, lambda: copy_rich("listener-meet: copiar e sair", "<p><b>listener-meet</b>: copiar e sair</p>"))
    QTimer.singleShot(150, app.quit)
    sys.exit(app.exec())


def cmd_export(_args) -> None:
    from listener.rag import export

    root = Path(tempfile.mkdtemp(prefix="listener-export-"))
    for mode in ("sem-share-delete", "delete"):
        target = root / f"2026-10-25 Comitê ({mode}).md"
        target.write_text("versão anterior\n", encoding="utf-8")
        holder = subprocess.Popen([sys.executable, "-c", HOLD, str(target), mode], stdout=subprocess.PIPE, text=True)  # noqa: S603
        state = holder.stdout.readline().strip() if holder.stdout else ""
        try:
            export.write_atomic(target, "# Comitê\n\nversão nova\n")
            outcome, message = "salvou", None
        except OSError as e:
            outcome, message = type(e).__name__, export.save_error_text(target, e)
        holder.kill()
        holder.wait()
        out("export_locked", holder=state, mode=mode, outcome=outcome, user_message=message,
            previous_kept=target.read_text(encoding="utf-8").strip() == "versão anterior",
            leftover_tmp=[p.name for p in root.glob(".*.tmp")])

    os.environ.pop("QT_QPA_PLATFORM", None)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    board = QApplication.clipboard()
    saved = board.text()
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_copy_exit"], capture_output=True, text=True,  # noqa: S603
                           timeout=60, check=False)
    time.sleep(0.3)
    data = board.mimeData()
    out("copy_exit", exit_code=child.returncode, text_after_exit=board.text(), html_after_exit=bool(data and data.hasHtml()),
        qthread_destroyed="QThread: Destroyed" in child.stderr)
    board.setText(saved)                                   # devolve o texto do usuário; sair pelo laço o mantém lá
    QTimer.singleShot(150, app.quit)
    app.exec()
    out("clipboard_restored", ok=True, had_text=bool(saved))


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8", errors="replace")
    if sys.platform != "win32":
        sys.exit("win_probe: só no Windows nativo (D-045)")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("app")
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--hotkey", action="store_true")
    p = sub.add_parser("shots")
    p.add_argument("--out", default=None)
    p = sub.add_parser("player")
    p.add_argument("--seconds", type=float, default=20.0)
    sub.add_parser("export")
    p = sub.add_parser("_first", help=argparse.SUPPRESS)
    p.add_argument("--prewarm", action="store_true")
    sub.add_parser("_copy_exit", help=argparse.SUPPRESS)
    args = ap.parse_args()
    out("env", python=sys.version.split()[0], platform=sys.platform, cores=os.cpu_count())
    {"app": cmd_app, "shots": cmd_shots, "player": cmd_player, "export": cmd_export, "_first": cmd_first,
     "_copy_exit": cmd_copy_exit}[args.cmd](args)


if __name__ == "__main__":
    main()
