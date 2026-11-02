"""F5.13 (L1, Aceite 1 e 2): a instância única. O nome do canal (sem o nome do usuário), a linha do pedido e as recusas e
o comando do `--open`; o servidor de verdade (um nome por teste) com o "segundo processo" à parte: os quatro pedidos
entregues, a linha acima de 256 bytes, o JSON inválido, o valor desconhecido e a linha que não termina sem abrir nada, o
"fechando" de uma conexão lida com o app saindo, e o diálogo modal que só vem à frente, antes e depois do controlador; o
pedido no controlador (o caminho do clique no aviso e o do clique esquerdo na bandeja); dois processos de verdade pelo `run`
(o segundo entrega o pedido e sai com 0); e os filhos e os diagnósticos, que despacham sem pedir a trava. A saída e a
espera pela trava sem canal (R-01, R-02) estão em `tests/test_instance_exit.py`."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import listener.__main__ as entry
from listener.model import OpenTarget
from listener.platform.common import paths, process
from tests.fake_platform import FakePlatform

ROOT = Path(__file__).resolve().parent.parent
EXE = r"C:\Programas\listener-meet\listener-meet.exe"


def unique_name() -> str:
    """Um nome por teste: nunca o do app de verdade (o do perfil de quem roda), nem o de outro teste."""
    from listener.ui.instance import channel_name

    name = f"listener-meet-teste-{uuid.uuid4().hex[:12]}"
    assert name != channel_name(paths.config_dir())
    return name


# ---------------------------------------------------------------- partes puras
def test_the_channel_name_is_the_app_and_a_digest_without_the_user_name():
    from listener.ui.instance import channel_name

    folder = Path("C:/Users/Fulano da Silva/AppData/Roaming/listener-meet")
    name = channel_name(folder)
    assert name.startswith("listener-meet-") and len(name) == len("listener-meet-") + 12
    assert all(c in "0123456789abcdef" for c in name.removeprefix("listener-meet-"))
    assert "fulano" not in name.casefold() and "users" not in name.casefold()
    assert channel_name(Path(str(folder).upper())) == name                     # sem diferença de maiúsculas
    assert channel_name(folder.parent / "outro-perfil") != name                # outro perfil, outra instância


def test_each_request_goes_as_one_json_line_and_comes_back_the_same():
    from listener.ui.instance import parse_request, request_line

    assert request_line(OpenTarget.HISTORY) == b'{"open": "history"}\n'
    for target in OpenTarget:
        line = request_line(target)
        assert line.endswith(b"\n") and len(line) <= 256
        assert parse_request(line.removesuffix(b"\n")) == target


@pytest.mark.parametrize("line", [b'{"open": "' + b"h" * 300 + b'"}', b"{", b"\xff\xfe", b'{"open": "desktop"}', b'{"open": 1}',
                                  b'["history"]', b'{"open": "history", "e": 1}', b'"history"', b'{"open": "HISTORY"}'])
def test_anything_else_is_refused_with_a_reason_that_does_not_carry_the_line(line):
    from listener.ui.instance import parse_request

    with pytest.raises(ValueError, match=r"^(linha acima de 256 bytes|JSON inválido|pedido desconhecido)$") as found:
        parse_request(line)
    assert "desktop" not in str(found.value) and "hhh" not in str(found.value)


def test_the_new_process_says_closing_or_no_reply_and_nothing_on_ok():
    from listener.ui.instance import CLAIMED, CLOSING_TEXT, LOCKED, NO_REPLY_TEXT, refusal_text

    assert refusal_text("ok") is None and refusal_text(CLAIMED) is None            # a trava pega na espera: vira o app
    assert refusal_text("fechando") == refusal_text(LOCKED) == CLOSING_TEXT         # R-01: a trava presa sem canal
    assert CLOSING_TEXT == "O listener-meet está fechando. Abra de novo em alguns segundos."
    assert refusal_text(None) == refusal_text("outra coisa") == NO_REPLY_TEXT
    assert NO_REPLY_TEXT == ("O listener-meet já está aberto, mas não respondeu. Espere um pouco ou feche-o pelo Gerenciador "
                             "de Tarefas.")


def test_the_log_of_the_lock_and_the_channel_carries_no_path_nor_name():
    """D-053: nenhum caminho (a pasta de configuração traz o nome do usuário), nome ou título no log da trava e do canal."""
    from tests.log_privacy import leaks

    for module in ("listener/ui/instance.py", "listener/platform/win32/taskbar.py"):
        assert leaks(ROOT / module) == [], module


def test_the_open_command_is_the_exe_when_packaged_and_the_package_in_the_venv(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", EXE)
    assert process.open_command(OpenTarget.QUEUE) == [EXE, "--open", "queue"]
    monkeypatch.delattr(sys, "frozen")
    monkeypatch.setattr(sys, "executable", "python")
    assert process.open_command("record") == ["python", "-m", "listener", "--open", "record"]


# ---------------------------------------------------------------- Aceite 2: o despacho
def _no_app(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """O `run` do app (o único que pede a trava) anota a chamada em vez de abrir; os filhos e os diagnósticos viram falsos."""
    from listener.ui import app as ui_app

    calls: list[Any] = []

    def run(**kw: Any) -> int:
        calls.append(kw)
        return 0

    def child(module: str) -> Callable[[list[str]], int]:
        def main(argv: list[str]) -> int:
            calls.append((module, argv))
            return 7
        return main

    def diagnostic(name: str) -> Callable[[Any], int]:
        def fn(_args: Any) -> int:
            calls.append(name)
            return 5
        return fn

    monkeypatch.setattr(ui_app, "run", run)
    for module in ("listener.pipeline.worker", "listener.live.proc", "listener.pipeline.hf_check"):
        monkeypatch.setitem(sys.modules, module, SimpleNamespace(main=child(module)))
    for name in ("selftest", "check", "downgrade"):
        monkeypatch.setattr(entry, name, diagnostic(name))
    return calls


def test_the_children_and_the_diagnostics_dispatch_without_asking_for_the_lock(monkeypatch):
    calls = _no_app(monkeypatch)
    assert entry._dispatch(["--worker", "--db", "D", "--light"]) == 7
    assert entry._dispatch(["--live", "--db", "D", "--meeting", "3"]) == 7
    assert entry._dispatch(["--hf-check", "--db", "D"]) == 7
    assert calls == [("listener.pipeline.worker", ["--db", "D", "--light"]), ("listener.live.proc", ["--db", "D", "--meeting", "3"]),
                     ("listener.pipeline.hf_check", ["--db", "D"])]
    calls.clear()
    assert [entry._dispatch(a) for a in (["--selftest"], ["--check", "D"], ["--downgrade", "D", "8"])] == [5, 5, 5]
    assert calls == ["selftest", "check", "downgrade"]                                      # o `run` nunca foi chamado


def test_open_reaches_the_app_with_the_screen_and_refuses_an_unknown_one(monkeypatch, capsys):
    calls = _no_app(monkeypatch)
    assert entry._dispatch(["--open", "queue"]) == 0 and entry._dispatch([]) == 0
    assert calls == [{"open_target": OpenTarget.QUEUE}, {"open_target": None}]
    with pytest.raises(SystemExit) as stop:
        entry._dispatch(["--open", "desktop"])
    assert stop.value.code == 2 and "invalid choice" in capsys.readouterr().err


# ---------------------------------------------------------------- o servidor de verdade e o processo novo à parte
def client(qtbot: Any, *args: str, timeout_ms: int = 20_000) -> dict[str, Any]:
    """O "segundo processo" de verdade (`tests/instance_client.py`): no mesmo processo, o cliente bloqueante seguraria o GIL e
    o servidor daqui só responderia depois do prazo. O laço daqui roda enquanto ele espera."""
    env = os.environ | {"PYTHONUTF8": "1", "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "instance_client.py"), *args], cwd=str(ROOT), env=env,  # noqa: S603
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        qtbot.waitUntil(lambda: proc.poll() is not None, timeout=timeout_ms)
    finally:
        if proc.poll() is None:
            proc.kill()
    out, err = proc.communicate()
    assert proc.returncode == 0, err[-2000:]
    return dict(json.loads(out.strip().splitlines()[-1]))


def record_front(platform: Any, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """As janelas que a plataforma trouxe à frente."""
    fronted: list[int] = []

    def bring_to_front(window_id: int) -> bool:
        fronted.append(int(window_id))
        return True

    monkeypatch.setattr(platform, "bring_to_front", bring_to_front)
    return fronted


def stub_controller() -> Any:
    """O mínimo do controlador que o canal usa (um QObject: o contexto do `QTimer` do `open_later`), anotando o que abriria."""
    from PySide6.QtCore import QObject, Signal

    class StubController(QObject):
        leaving = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.quitting = False
            self.tray = None
            self.widget = None                                       # R-02: o que o "record" traz à frente gravando
            self.opened: list[Any] = []

        def open_target(self, action: Any) -> None:
            self.opened.append(action.target)

        def open_warmup(self) -> None:
            self.opened.append("warm-up")

    return StubController()


@pytest.fixture
def channel(qapp: Any, tmp_path: Path) -> Any:
    from listener.ui.instance import InstanceServer

    platform = FakePlatform(tmp_path)
    server = InstanceServer(unique_name(), platform)
    assert server.listen()
    stub = stub_controller()
    server.attach(stub)
    yield SimpleNamespace(server=server, platform=platform, stub=stub)
    server.close()
    server.deleteLater()
    stub.deleteLater()


def test_the_second_process_delivers_each_of_the_four_requests(qtbot, channel):
    from listener.ui.notices import Target

    for target in OpenTarget:
        assert client(qtbot, "request", channel.server.name, str(target)) == {"reply": "ok", "handovers": 1}   # o foco antes
    qtbot.waitUntil(lambda: len(channel.stub.opened) == 4, timeout=2000)
    assert channel.stub.opened == [Target.HISTORY, Target.QUEUE, Target.SETTINGS, "warm-up"]   # sem bandeja: o "Antes de gravar"


@pytest.mark.parametrize("data", [b'{"open": "' + b"h" * 300 + b'"}\n', b'{"open": "history"' + b" " * 300, b"{\n",
                                  b'{"open": "desktop"}\n', b"\xff\n"])
def test_a_bad_line_opens_nothing_and_gets_no_reply(qtbot, channel, caplog, data):
    with caplog.at_level(logging.INFO, logger="listener.ui.instance"):
        assert client(qtbot, "raw", channel.server.name, data.hex()) == {"got": "", "closed": True}
        qtbot.wait(50)
    assert channel.stub.opened == []
    assert "pedido ignorado" in caplog.text and "desktop" not in caplog.text


def test_a_line_that_never_ends_is_closed_after_the_wait(qtbot, channel, monkeypatch, caplog):
    from listener.ui import instance

    monkeypatch.setattr(instance, "READ_TIMEOUT_MS", 300)
    with caplog.at_level(logging.INFO, logger="listener.ui.instance"):
        assert client(qtbot, "raw", channel.server.name, b'{"open": "history"}'.hex(), "8000") == {"got": "", "closed": True}
    assert channel.stub.opened == [] and "sem a linha inteira em 300 ms" in caplog.text


def test_a_request_read_while_the_app_leaves_gets_closing_and_opens_nothing(qtbot, controller):
    """Uma conexão aceita antes de o canal fechar e lida já com o app saindo (no logoff, o laço gira uma vez depois da parada
    de emergência): "fechando", e nada abre. O "Sair" e a parada fecham o canal antes (`tests/test_instance_exit.py`)."""
    from listener.ui.instance import REPLY_CLOSING, InstanceServer

    server = InstanceServer(unique_name(), controller.platform)
    server.attach(controller)
    controller._quitting = True                                       # o que a parada de emergência acende
    try:
        assert server.answer(OpenTarget.QUEUE) == REPLY_CLOSING
        qtbot.wait(50)
        assert controller._queue_dialog is None and controller._history is None
    finally:
        controller._quitting = False
        server.close()
        server.deleteLater()


def test_with_a_modal_dialog_open_only_it_comes_to_the_front(qtbot, channel, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    fronted = record_front(channel.platform, monkeypatch)
    dlg = QDialog()
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    qtbot.addWidget(dlg)
    dlg.show()
    try:
        assert client(qtbot, "request", channel.server.name, "history")["reply"] == "ok"
        qtbot.wait(50)
        assert channel.stub.opened == [] and fronted == [int(dlg.winId())]
    finally:
        dlg.close()


def test_before_the_controller_only_an_open_dialog_comes_forward(qtbot, channel, monkeypatch):
    """Sem `attach` (o assistente da primeira vez, o banco que não abre): sem diálogo aberto, "ok" e nada abre; com um (o
    assistente), só ele vem à frente."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    from listener.ui.instance import InstanceServer

    fronted = record_front(channel.platform, monkeypatch)
    early = InstanceServer(unique_name(), channel.platform)
    assert early.listen()
    dlg = QDialog()
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    qtbot.addWidget(dlg)
    try:
        assert client(qtbot, "request", early.name, "queue")["reply"] == "ok"
        qtbot.wait(50)
        assert fronted == [] and channel.stub.opened == []
        dlg.show()
        assert client(qtbot, "request", early.name, "history")["reply"] == "ok"
        qtbot.wait(50)
        assert fronted == [int(dlg.winId())] and channel.stub.opened == []
    finally:
        dlg.close()
        early.close()
        early.deleteLater()


def test_the_first_process_takes_the_lock_and_opens_the_channel(qapp, tmp_path):
    from listener.ui import instance

    platform = FakePlatform(tmp_path)
    server = instance.claim(qapp, platform, None)
    try:
        assert server is not None and server.server.isListening()
        assert server.name == instance.channel_name(platform.config_dir()) == platform.claims[0]
        assert platform.handovers == 0
    finally:
        assert server is not None
        server.close()
        server.setParent(None)
        server.deleteLater()


# ---------------------------------------------------------------- o pedido no controlador
@pytest.fixture
def controller(qapp: Any, platform: Any, store: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from listener.ui import controller as ctl

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    yield c
    c.emergency_stop()


def test_requests_follow_the_notice_click_and_the_tray_left_click(qtbot, controller, monkeypatch):
    from listener.ui.instance import open_target
    from listener.ui.tray import TrayIcon

    c = controller
    opened: list[Any] = []

    def open_settings(**kw: Any) -> None:
        opened.append(("configurações", kw))

    def open_warmup(source_exe: str | None = None) -> None:
        opened.append(("antes de gravar", source_exe))

    monkeypatch.setattr(c, "open_settings", open_settings)
    monkeypatch.setattr(c, "open_warmup", open_warmup)
    fronted = record_front(c.platform, monkeypatch)
    open_target(c, OpenTarget.HISTORY)
    assert c._history is not None and c._history.isVisible() and fronted == [int(c._history.winId())]
    open_target(c, OpenTarget.QUEUE)
    queue = c._queue_dialog
    assert queue is not None and queue.isVisible() and fronted[-1] == int(queue.winId())
    queue.close()                                                      # a leitura da fila para antes do banco fechar
    c._history.close()
    open_target(c, OpenTarget.SETTINGS)
    open_target(c, OpenTarget.RECORD)                                  # sem bandeja: o "Antes de gravar"
    assert opened == [("configurações", {"focus": True}), ("antes de gravar", None)]
    tray = TrayIcon(c)
    c.tray = tray
    try:
        record: list[str] = []
        tray.set_call_handlers(record.append, lambda _exe: None)
        tray.set_call("ms-teams.exe", "Teams")
        open_target(c, OpenTarget.RECORD)                              # F5.7: a reunião pendente, como o clique esquerdo
        assert record == ["ms-teams.exe"]
        c._quitting = True
        open_target(c, OpenTarget.HISTORY)
        open_target(c, OpenTarget.RECORD)
        assert record == ["ms-teams.exe"] and len(opened) == 2           # saindo: nada abre
    finally:
        c._quitting = False
        c.tray = None
        tray.deleteLater()


# ---------------------------------------------------------------- dois processos pelo `run` (o caminho do app)
@pytest.mark.lento
def test_two_processes_the_second_hands_over_and_leaves_with_0(tmp_path):
    """Os dois pelo `listener.ui.app.run` com a plataforma falsa (`tests/instance_scenario.py`): o primeiro abre o app (o
    Histórico na abertura) e o segundo, sem a trava, entrega "queue" e sai com 0; o primeiro abre a fila e sai."""
    env = {k: v for k, v in os.environ.items() if k != "LISTENER_LLM"} | {"PYTHONUTF8": "1", "QT_QPA_PLATFORM": "offscreen"}
    script = str(ROOT / "tests" / "instance_scenario.py")
    first = subprocess.Popen([sys.executable, script, "primary", str(tmp_path)], cwd=str(ROOT), env=env,  # noqa: S603
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert first.stdout is not None
        ready = first.stdout.readline()
        assert json.loads(ready) == {"ready": True}, first.stderr.read() if first.stderr else ""
        second = subprocess.run([sys.executable, script, "secondary", str(tmp_path), "queue"], cwd=str(ROOT), env=env,  # noqa: S603
                                capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        out, err = first.communicate(timeout=60)
    finally:
        if first.poll() is None:
            first.kill()
    assert second.returncode == 0, second.stderr[-3000:]
    lines = [json.loads(line) for line in second.stdout.splitlines() if line.strip()]
    assert lines == [{"code": 0}], second.stdout
    assert "outra instância já roda: pedido entregue (queue)" in second.stderr
    assert first.returncode == 0, err[-3000:]
    assert [json.loads(line) for line in out.splitlines() if line.strip()] == [{"opened": "history"}, {"opened": "queue"}]
    assert "pedido recebido: queue" in err
