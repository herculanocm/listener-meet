"""F5.13, a revisão (R-01, R-02). A saída fecha o canal do pedido já no começo: o "Sair" confirmado, antes do `rec.stop()`
(que prende o laço até ~10 s) e do `app.quit()`; o "Não" o deixa aberto; e a parada de emergência, antes de tudo. O processo
novo sem canal tenta a trava de novo até o prazo: a trava presa dá a mensagem do passo 3, e a que fica livre na espera
(o app saiu) faz dele o app, com o pedido dele; conectado e sem resposta (o app travado), a do passo 4. E o "record" traz
à frente o que abriu: o "Antes de gravar" (dentro do `exec` dele) ou, gravando, o widget. O canal é o de verdade, com um
nome por teste; a trava é a da plataforma falsa (a de verdade, em `tests/test_instance_win.py`)."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from listener.model import AppState, OpenTarget
from tests.fake_platform import FakePlatform


class Leaving(FakePlatform):
    """A trava de um app que sai: presa nas `held` primeiras tentativas e livre depois (None: presa até o fim)."""

    def __init__(self, tmp: Path, held: int | None) -> None:
        super().__init__(tmp)
        self.held = held

    def claim_instance(self, name: str) -> bool:
        self.claims.append(name)
        return self.held is not None and len(self.claims) > self.held


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """As mensagens que o processo novo mostraria (sem caixa), com o prazo de 0,5 s."""
    from listener.ui import instance

    texts: list[str] = []
    monkeypatch.setattr(instance, "show_refusal", lambda _app, _platform, text: texts.append(text))
    monkeypatch.setattr(instance, "CLIENT_TIMEOUT_S", 0.5)
    return texts


def record_front(platform: Any, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    fronted: list[int] = []

    def bring_to_front(window_id: int) -> bool:
        fronted.append(int(window_id))
        return True

    monkeypatch.setattr(platform, "bring_to_front", bring_to_front)
    return fronted


# ---------------------------------------------------------------- o processo novo, sem canal
def test_the_lock_held_without_a_channel_says_closing_by_the_deadline(qapp, tmp_path, shown, caplog):
    from listener.ui import instance

    platform = Leaving(tmp_path, held=None)
    t0 = time.monotonic()
    with caplog.at_level(logging.INFO, logger="listener.ui.instance"):
        assert instance.claim(qapp, platform, OpenTarget.QUEUE) is None
    assert 0.4 <= time.monotonic() - t0 < 3
    assert shown == [instance.CLOSING_TEXT] and platform.handovers == 1
    assert len(platform.claims) > 3                                   # tentou a trava de novo na espera
    assert "está fechando" in caplog.text and "(queue)" in caplog.text and str(tmp_path) not in caplog.text


def test_the_lock_freed_during_the_wait_makes_the_new_process_the_app(qapp, tmp_path, shown, caplog):
    from listener.ui import instance

    platform = Leaving(tmp_path, held=4)
    with caplog.at_level(logging.INFO, logger="listener.ui.instance"):
        server = instance.claim(qapp, platform, OpenTarget.QUEUE)
    try:
        assert server is not None and server.server.isListening()
        assert server.name == instance.channel_name(platform.config_dir()) and len(platform.claims) == 5
        assert shown == [] and "saiu na espera: este processo abre o app (queue)" in caplog.text
    finally:
        assert server is not None
        server.close()
        server.setParent(None)
        server.deleteLater()


def test_connected_without_a_reply_says_the_app_did_not_answer(qapp, tmp_path, shown):
    """O app travado: o sistema aceita a conexão no canal dele e ninguém responde até o prazo."""
    from PySide6.QtNetwork import QLocalServer

    from listener.ui import instance

    platform = Leaving(tmp_path, held=None)
    hung = QLocalServer()
    assert hung.listen(instance.channel_name(platform.config_dir()))
    try:
        assert instance.claim(qapp, platform, OpenTarget.HISTORY) is None
        assert shown == [instance.NO_REPLY_TEXT] and len(platform.claims) == 1
    finally:
        hung.close()


# ---------------------------------------------------------------- a saída do app que tem a trava
@pytest.fixture
def app_with_channel(qapp: Any, platform: Any, store: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """O controlador de verdade com o canal de verdade; o `app.quit` anota se o canal ainda escutava."""
    from listener.ui import controller as ctl
    from listener.ui.instance import InstanceServer

    monkeypatch.setattr(ctl, "prewarm_audio", lambda: None)
    c = ctl.Controller(qapp, platform, store)
    c.tray = None
    server = InstanceServer(f"listener-meet-teste-{uuid.uuid4().hex[:12]}", platform)
    assert server.listen()
    server.attach(c)
    events: list[tuple[str, bool]] = []
    monkeypatch.setattr(qapp, "quit", lambda: events.append(("app.quit", server.server.isListening())))
    yield SimpleNamespace(c=c, server=server, events=events)
    c.recorder = None
    if not c._windows_stopped:                                         # a parada só uma vez (o teste dela já parou)
        c.emergency_stop()
    server.close()
    server.deleteLater()


def _recording(env: Any, monkeypatch: pytest.MonkeyPatch, answer: Any) -> None:
    """Uma gravação de mentira e a resposta da pergunta do "Sair"; o `rec.stop()` anota se o canal ainda escutava."""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *_a, **_k: answer))
    env.c.recorder = SimpleNamespace(stop=lambda: env.events.append(("rec.stop", env.server.server.isListening())),
                                     muted=False, meeting_id=0, set_live_tap=lambda _tap: None)
    for name in ("_finish", "_clear_lease", "_enqueue_processing"):
        monkeypatch.setattr(env.c, name, lambda *_a: None)


def test_sair_closes_the_channel_before_the_app_quits(app_with_channel):
    app_with_channel.c.quit()
    assert app_with_channel.events == [("app.quit", False)]


def test_sair_while_recording_closes_the_channel_before_saving(app_with_channel, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    _recording(app_with_channel, monkeypatch, QMessageBox.StandardButton.Yes)
    app_with_channel.c.quit()
    assert app_with_channel.events == [("rec.stop", False), ("app.quit", False)]


def test_answering_no_to_sair_keeps_the_channel_open(app_with_channel, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    _recording(app_with_channel, monkeypatch, QMessageBox.StandardButton.No)
    app_with_channel.c.quit()
    assert app_with_channel.events == [] and app_with_channel.server.server.isListening()


def test_the_emergency_stop_closes_the_channel_first(app_with_channel):
    c, server = app_with_channel.c, app_with_channel.server
    at_emit: list[bool] = []
    c.leaving.connect(lambda: at_emit.append(c.quitting))
    c.emergency_stop()
    assert at_emit == [False] and not server.server.isListening()        # antes de a parada acender o `_quitting`


# ---------------------------------------------------------------- R-02: o "record" à frente
def test_record_brings_the_before_recording_dialog_forward(qtbot, app_with_channel, monkeypatch):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QDialog

    from listener.ui.instance import open_target

    c = app_with_channel.c
    fronted = record_front(c.platform, monkeypatch)
    opened: list[int] = []

    def open_warmup(source_exe: str | None = None) -> None:          # um modal como o "Antes de gravar"
        dlg = QDialog()
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        opened.append(int(dlg.winId()))
        QTimer.singleShot(200, dlg.accept)
        dlg.exec()
        dlg.deleteLater()

    monkeypatch.setattr(c, "open_warmup", open_warmup)
    open_target(c, OpenTarget.RECORD)
    assert len(opened) == 1 and fronted == opened


def test_record_while_recording_brings_the_widget_forward(qtbot, app_with_channel, monkeypatch):
    from PySide6.QtWidgets import QWidget

    from listener.ui.instance import open_target
    from listener.ui.tray import TrayIcon

    c = app_with_channel.c
    fronted = record_front(c.platform, monkeypatch)
    tray = TrayIcon(c)
    widget = QWidget()
    qtbot.addWidget(widget)
    c.tray, c.widget, c.state = tray, widget, AppState.RECORDING      # o clique esquerdo mostra o widget
    try:
        open_target(c, OpenTarget.RECORD)
        qtbot.waitUntil(lambda: fronted == [int(widget.winId())], timeout=2000)
    finally:
        c.tray, c.widget, c.state = None, None, AppState.IDLE
        tray.deleteLater()
