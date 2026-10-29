"""As telas e a CPU da F5.7 L3 (U-094): o aviso de reunião sem gravação. Não faz parte do produto.

  python scripts/probe_f57.py shots [--out PASTA] [--escuro]   as telas no DPI real, fora da tela do usuário
  python scripts/probe_f57.py cpu [--seconds 300]             a CPU do sensor ocioso, ligado e desligado (um processo cada)

shots  No molde do `win_probe shots` (o `shot` e os relatórios `cortados` e `pequenos` de `probe_ui`), mas à parte: o
       `win_probe.py` está no teto de linhas, e o `shots` completo dele abre o microfone de verdade no "Antes de gravar". Aqui
       as janelas usam a plataforma falsa dos testes (o microfone, os apps tocando e a captura de tela são de mentira; a
       lista de fábrica é a do Windows) e o tema vem da plataforma real, como no app. As janelas ficam em (-10000, -10000)
       com `WA_ShowWithoutActivating` e o `fit_to_screen` troca pelo cálculo sem mover: nada aparece nem rouba o foco. As
       telas: o cartão, o lembrete, o cartão com um rótulo longo, o "Antes de gravar" pelo aviso (com o app tocando e sem
       ele), o cartão das Configurações ligado (um app acrescentado, um desmarcado e um desligado pela bandeja) e desligado, o
       erro de validação na página e o menu da bandeja com a chamada pendente.
cpu    O sensor (a thread e a cola) com a plataforma real, só lendo: as sessões de áudio e o indicador de microfone, sem
       abrir microfone nem tocar som. A interface é a `offscreen` (um aviso de uma chamada de verdade nunca aparece na tela) e
       o banco é temporário. Cada fase roda num processo novo pelo tempo pedido: "ligado" (`detect.enabled`) e "desligado"
       (a thread parada). Sai o tempo de CPU do processo, antes e depois, em ponto percentual de um núcleo; com `--warmup N`,
       a medida começa N s depois de ligar (fora dela, a primeira leitura a frio: a carga do comtypes e o retrato de processos)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                    # os falsos de `tests/` (a plataforma e o sensor de mentira)


def out(stage: str, **kw) -> None:
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False, default=str), flush=True)


def discard(w) -> None:
    w.close()
    w.deleteLater()


# ---------------------------------------------------------------------------- telas
def probe_platform(tmp: Path):
    """A plataforma falsa com a lista de fábrica do Windows e o Teams e o Chrome tocando."""
    from listener.platform.base import PlayingApp
    from listener.platform.win32.activity import WATCHED
    from tests.fake_platform import FakePlatform

    class ProbePlatform(FakePlatform):
        def watched_apps(self):
            return WATCHED

        def list_playing_apps(self):
            return [PlayingApp(4242, "ms-teams.exe", "Reunião de alinhamento", 0.42, True), PlayingApp(5300, "chrome.exe", "", 0.08)]

    return ProbePlatform(tmp)


def make_shooter(app, out_dir: Path):
    """O `shot` do `win_probe` (fora da tela, no DPR da tela) com os relatórios de `probe_ui`."""
    from PySide6.QtCore import Qt

    from probe_ui import layout_report

    shots: list[dict] = []

    def shot(w, name: str) -> None:
        w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        w.move(-10000, -10000)
        w.show()
        w.move(-10000, -10000)
        app.processEvents()
        deadline = time.perf_counter() + 1.0
        while w.devicePixelRatioF() != app.primaryScreen().devicePixelRatio() and time.perf_counter() < deadline:
            time.sleep(0.02)
            app.processEvents()
        for _ in range(3):
            app.processEvents()
        pix = w.grab()
        path = out_dir / f"{len(shots):02d}-{name}.png"
        pix.save(str(path))
        cut, small = layout_report(w)
        shots.append({"file": path.name, "logical": [w.width(), w.height()], "pixels": [pix.width(), pix.height()],
                      "dpr": w.devicePixelRatioF(), "cortados": cut, "pequenos": small})

    def part(w, name: str) -> None:
        pix = w.grab()
        path = out_dir / f"{len(shots):02d}-{name}.png"
        pix.save(str(path))
        cut, small = layout_report(w)
        shots.append({"file": path.name, "logical": [w.width(), w.height()], "pixels": [pix.width(), pix.height()],
                      "dpr": w.devicePixelRatioF(), "cortados": cut, "pequenos": small})

    return shot, part, shots


def card_shots(shot) -> None:
    from listener.ui.call_prompt import CallPrompt

    tip = 'Abre o "Antes de gravar" com o Teams como fonte (Ctrl+Alt+Shift+R)'
    for name, card in (("f57-aviso", CallPrompt("Teams", 8, False, tip)),
                       ("f57-lembrete", CallPrompt("Teams", 134, True, tip)),
                       ("f57-aviso-rotulo-longo", CallPrompt("Google Meet no Chrome", 11, False, tip))):
        shot(card, name)
        discard(card)


def warmup_shots(store, fake, shot, app) -> None:
    from listener.ui.warmup import WarmupDialog

    from PySide6.QtCore import Qt

    for name, exe in (("f57-antes-de-gravar-pelo-aviso", "ms-teams.exe"), ("f57-antes-de-gravar-app-sem-som", "zoom.exe")):
        dlg = WarmupDialog(fake, store, source_hint=exe)
        dlg.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        dlg.move(-10000, -10000)                          # fora da tela já no primeiro `show` (a lista de apps chega nele)
        dlg.show()
        deadline = time.perf_counter() + 3.0
        while dlg.sources.selected_key() is None or dlg._pending_source is not None:       # a primeira lista de apps
            if time.perf_counter() > deadline:
                break
            app.processEvents()
            time.sleep(0.02)
        shot(dlg, name)
        out("antes_de_gravar", tela=name, fonte=dlg.sources.selected_spec().log_text(), faixa=dlg.hint_bar.kind,
            foco=type(dlg.focusWidget()).__name__, fora_da_captura=int(dlg.winId()) in fake.excluded)
        discard(dlg)


def settings_shots(store, fake, shot, part, app) -> None:
    from listener.config.defaults import validate_setting
    from listener.ui.settings_detect import DETECT_TITLE
    from listener.ui.settings_min import SettingsDialog

    store.set_setting("detect.apps", "Discord.exe")
    store.set_setting("detect.off", "zoom.exe,WhatsApp.Root.exe")
    for name, enabled in (("f57-configuracoes-ligado", True), ("f57-configuracoes-desligado", False)):
        sd = SettingsDialog(store, fake, {})
        sd.nav.setCurrentRow(list(sd.pages).index("Idioma e avisos"))
        page = sd.page("Idioma e avisos")
        card = page.cards[DETECT_TITLE]
        sd.editor("detect.enabled").box.setChecked(enabled)
        shot(sd, name)
        page.ensureWidgetVisible(card)
        app.processEvents()
        shot(sd, f"{name}-rolado")
        part(card, f"{name}-cartao")
        if enabled:
            seconds = sd.editor("detect.seconds")
            sd.show_problem(validate_setting("detect.seconds", "200"), seconds.widget)     # U-072: o erro na página
            app.processEvents()
            shot(sd, "f57-configuracoes-erro")
        discard(sd)


def tray_shot(part, app) -> None:
    from listener.ui.tray import TrayIcon

    class Host:
        state = "idle"

        def __getattr__(self, _name):
            return lambda *_a, **_k: None

    tray = TrayIcon(Host())                       # nunca mostrada: só o menu vai à foto
    tray.set_call("ms-teams.exe", "Teams")
    tray.menu.ensurePolished()
    tray.menu.adjustSize()
    app.processEvents()
    part(tray.menu, "f57-bandeja-menu")           # sem abrir o menu (um popup pegaria o mouse e o teclado)
    out("bandeja", dica=tray.toolTip(), primeiro=tray.menu.actions()[0].text(), padrao=tray.menu.defaultAction().text())
    tray.deleteLater()


def cmd_shots(args) -> None:
    os.environ.pop("QT_QPA_PLATFORM", None)
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication

    from probe_ui import apply_theme, fit_offscreen, release_theme

    app = QApplication(sys.argv[:1])
    scheme = Qt.ColorScheme.Dark if args.escuro else None
    if scheme is not None:
        app.styleHints().setColorScheme(scheme)
    from listener.platform import get_platform
    from listener.store import Store
    from listener.ui import settings_min, warmup
    from listener.ui.common import install_translations

    install_translations(app)
    themed = apply_theme(app, get_platform(), scheme)       # o tema da plataforma real; as janelas, da falsa
    for module in (warmup, settings_min):
        module.fit_to_screen = fit_offscreen                # nada vem para a tela do usuário
    tmp = Path(tempfile.mkdtemp(prefix="listener-f57-"))
    out_dir = Path(args.out) if args.out else tmp / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    fake = probe_platform(tmp)
    store = Store(tmp / "db")
    store.upsert_me("Teste")
    store.seed_default_presets()
    shot, part, shots = make_shooter(app, out_dir)
    card_shots(shot)
    warmup_shots(store, fake, shot, app)
    settings_shots(store, fake, shot, part, app)
    tray_shot(part, app)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    store.close()
    release_theme(app)
    out("shots", folder=str(out_dir), themed=themed, scheme=str(app.styleHints().colorScheme()), style=app.style().name(),
        screen={"dpr": app.primaryScreen().devicePixelRatio()}, shots=shots)
    for report in ("cortados", "pequenos"):
        found = {s["file"]: s[report] for s in shots if s[report]}
        out(report, total=sum(len(v) for v in found.values()), shots=found)


# ---------------------------------------------------------------------------- CPU
class CpuHost:
    """O controlador da cola na medida: ocioso, sem bandeja; um aviso de verdade só conta (a tela é a `offscreen`)."""
    busy = False
    tray = None

    def __init__(self) -> None:
        self.opened = 0
        self.notices = 0
        self.toasts = self

    def avoid_also(self, _fn) -> None:
        pass

    def relayout(self) -> None:
        pass

    def open_warmup(self, _source_exe=None) -> None:
        self.opened += 1

    def notify(self, _message) -> None:
        self.notices += 1


def cmd_cpu_child(args) -> None:
    """(processo filho) Uma fase: o tempo de CPU do processo em `seconds` com o sensor ligado ou desligado."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from listener.platform import get_platform
    from listener.store import Store
    from listener.ui.call_watch import CallWatch

    app = QApplication(sys.argv[:1])
    platform = get_platform()
    store = Store(Path(tempfile.mkdtemp(prefix="listener-f57-cpu-")) / "db")
    store.set_setting("detect.enabled", "True" if args.phase == "ligado" else "False")
    calls = CallWatch(CpuHost(), platform, store)
    modes: dict[str, int] = {}
    calls.start()
    sample = QTimer()
    sample.timeout.connect(lambda: modes.__setitem__(calls.sensor.mode, modes.get(calls.sensor.mode, 0) + 1))
    sample.start(1000)
    start: dict[str, float] = {}

    def begin() -> None:
        start.update(cpu=time.process_time(), wall=time.perf_counter(), reads=calls.sensor.reads)
        QTimer.singleShot(int(args.seconds * 1000), app.quit)

    QTimer.singleShot(int(args.warmup * 1000), begin)
    app.exec()
    cpu, wall = time.process_time() - start["cpu"], time.perf_counter() - start["wall"]
    reads, has_watch = calls.sensor.reads - int(start["reads"]), calls.sensor.has_watch
    calls.shutdown()
    store.close()
    print(json.dumps({"phase": args.phase, "cpu_s": round(cpu, 3), "wall_s": round(wall, 1),
                      "core_pct": round(100 * cpu / wall, 3), "reads": reads, "has_watch": has_watch, "modes_s": modes}),
          flush=True)


def cmd_cpu(args) -> None:
    results = {}
    for phase in ("ligado", "desligado"):
        argv = [sys.executable, str(Path(__file__).resolve()), "_cpu", "--phase", phase, "--seconds", str(args.seconds),
                "--warmup", str(args.warmup)]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=args.seconds + args.warmup + 120, check=False,  # noqa: S603
                              cwd=ROOT)
        line = next((l for l in reversed(done.stdout.splitlines()) if l.startswith("{")), "")
        results[phase] = json.loads(line) if line else {"erro": done.stderr[-2000:]}
        out("cpu_fase", **results[phase])
    on, off = results["ligado"].get("core_pct"), results["desligado"].get("core_pct")
    if on is not None and off is not None:
        out("cpu", ligado_pct=on, desligado_pct=off, delta_pp=round(on - off, 3), meta_pp=0.2, seconds=args.seconds,
            warmup=args.warmup)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("shots")
    s.add_argument("--out")
    s.add_argument("--escuro", action="store_true")
    c = sub.add_parser("cpu")
    c.add_argument("--seconds", type=float, default=300.0)
    c.add_argument("--warmup", type=float, default=0.0)
    child = sub.add_parser("_cpu")
    child.add_argument("--phase", choices=("ligado", "desligado"), required=True)
    child.add_argument("--seconds", type=float, default=300.0)
    child.add_argument("--warmup", type=float, default=0.0)
    args = ap.parse_args()
    {"shots": cmd_shots, "cpu": cmd_cpu, "_cpu": cmd_cpu_child}[args.cmd](args)


if __name__ == "__main__":
    main()
