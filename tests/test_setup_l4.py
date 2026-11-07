"""F5.11 L4: antes de gravar, primeiro uso e Configurações (U-110 a U-113, U-120; A-29 a A-31, A-34, A-35).

O teste do token do Hugging Face num filho (o `serve` de verdade, em modo fake, numa thread com pipes; o comando de verdade
num subprocesso), com o token pelo stdin, o prazo e o Cancelar; a faixa "Falta configurar" com "Preparar agora", o
progresso e "Cancelar"; "Nomes ao vivo" apagado com o motivo; o assistente que conta os passos que mostra; o "Avançado" das
Configurações, que um erro abre; a pasta dos modelos com o aviso; a lista das fontes com "Novo" e o combo cortado no meio."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from listener.config.defaults import HF_PROVIDER_ID
from listener.pipeline import hf_check
from listener.pipeline.hf_check import Status
from tests.fake_live import ThreadChild

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parent.parent
HANG = threading.Event()                      # solta os filhos presos no fim de cada teste


@pytest.fixture(autouse=True)
def _release_hung() -> Any:
    HANG.clear()
    yield
    HANG.set()


def _serve(stdin: BinaryIO, stdout: BinaryIO) -> int:
    return hf_check.serve(stdin, stdout)


def _silent(stdin: BinaryIO, _stdout: BinaryIO) -> int:
    """Um filho preso (o caso da D-058): lê o token e não responde até o teste acabar."""
    stdin.readline()
    HANG.wait(10.0)
    return 0


class Spawner:
    def __init__(self, target: Any) -> None:
        self.target = target
        self.argv: list[list[str]] = []
        self.children: list[ThreadChild] = []

    def __call__(self, argv: list[str], env: Any = None) -> ThreadChild:
        self.argv.append(list(argv))
        child = ThreadChild(self.target, pid=8000 + len(self.children), argv=argv)
        self.children.append(child)
        return child


# ---------------------------------------------------------------- A-29: as mensagens e o filho
def test_the_check_messages_are_the_download_ones_and_the_fake_answers_by_the_token(monkeypatch, forget_models):
    from listener.pipeline import models

    gated = RuntimeError("403 Client Error: gated repo")
    assert hf_check.classify(gated, hf_check.EMBEDDING_REPO) == (Status.GATED, hf_check.gated_text(hf_check.EMBEDDING_REPO))
    assert str(models.classify_error(gated, "r")) == hf_check.gated_text("r")             # models.py usa as mesmas
    invalid = type("HfHubHTTPError", (Exception,), {})("401 Client Error: Unauthorized")
    assert hf_check.classify(invalid) == (Status.INVALID, hf_check.INVALID_TEXT)
    assert hf_check.classify(OSError("Max retries exceeded"))[0] == Status.NETWORK
    assert hf_check.classify(ValueError("outra coisa")) is None
    monkeypatch.setenv(hf_check.FAKE_ENV, "fake")
    for token, status in (("hf_x_ok", Status.OK), ("hf_x_gated", Status.GATED), ("hf_x_net", Status.NETWORK),
                          ("hf_token", Status.INVALID), ("", Status.INVALID)):
        out = io.BytesIO()
        code = hf_check.serve(io.BytesIO(f"{token}\n".encode()), out)
        found = json.loads(out.getvalue())
        assert found["status"] == status and code == (0 if status == Status.OK else 1)
    assert hf_check.describe(hf_check.check("hf_x_gated")) == hf_check.gated_text("wespeaker-voxceleb-resnet34-LM")   # sem dono


def test_a_check_stuck_in_native_code_becomes_no_answer_within_the_deadline(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(hf_check, "check", lambda _token: gate.wait(5.0) and {})
    found = hf_check.within("hf_x", 0.05)
    gate.set()
    assert found["status"] == Status.TIMEOUT and found["message"] == hf_check.TIMEOUT_TEXT


@pytest.mark.lento
def test_the_cli_reads_the_token_from_stdin_and_answers_one_json_line(tmp_path):
    env = {**os.environ, "LISTENER_HF": "fake"}
    done = subprocess.run([sys.executable, "-m", "listener", "--hf-check"], input=b"hf_x_gated\n", capture_output=True,
                          timeout=120, cwd=ROOT, env=env, check=False)
    found = json.loads(done.stdout.decode("utf-8").strip().splitlines()[-1])
    assert done.returncode == 1 and found["status"] == Status.GATED and found["repo"] == hf_check.EMBEDDING_REPO


# ---------------------------------------------------------------- U-113: o "Testar" nas Configurações e no assistente
def test_settings_tests_the_typed_token_before_saving_and_cancels_when_it_changes(qtbot, store, platform, monkeypatch):
    from listener.ui import hf_test
    from listener.ui.settings_min import SettingsDialog

    monkeypatch.setenv(hf_check.FAKE_ENV, "fake")
    monkeypatch.setattr(hf_test, "available", lambda: True)
    spawn = Spawner(_serve)
    monkeypatch.setattr(platform, "spawn_child", spawn)
    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    button = dlg.hf_test.button
    dlg.hf_token.setText("hf_digitado_ok")
    button.click()
    assert button.text() == hf_test.CANCEL_TEXT and dlg.hf_badge.text() == hf_test.TESTING
    qtbot.waitUntil(lambda: dlg.hf_badge.text() == "Conectado", timeout=5000)
    assert button.text() == hf_test.TEST_TEXT and dlg.hf_detail.text() == hf_check.OK_TEXT
    assert spawn.argv[0][-3:] == ["--hf-check", "--db", str(store.base_dir)]          # o token nunca na linha de comando
    assert all("hf_digitado_ok" not in " ".join(a) for a in spawn.argv) and not store.settings.provider_key(HF_PROVIDER_ID)
    dlg.hf_token.setText("hf_digitado_gated")
    assert dlg.hf_badge.text() == "Informado" and dlg.hf_detail.text() == ""              # o resultado era do outro token
    button.click()
    qtbot.waitUntil(lambda: dlg.hf_badge.text() == "Falta aceitar os termos", timeout=5000)
    assert "wespeaker-voxceleb-resnet34-LM" in dlg.hf_detail.text() and "pyannote/" not in dlg.hf_detail.text()
    spawn.target = _silent
    button.click()                                                                        # um teste preso: o filho não responde
    assert dlg.hf_test.testing and button.text() == hf_test.CANCEL_TEXT
    button.click()                                                                        # o Cancelar, pelo botão, mata o filho
    assert not dlg.hf_test.testing and spawn.children[-1].killed and dlg.hf_badge.text() == "Informado"
    button.click()
    dlg.hf_token.setText("hf_outro")                                                      # mexer no token também cancela
    assert not dlg.hf_test.testing and spawn.children[-1].killed and dlg.hf_badge.text() == "Informado"
    dlg.reject()


def test_the_token_child_dies_when_the_app_quits(qtbot, monkeypatch):
    """A-29: a saída do app (`shutdown_preparer`) mata o teste em curso pelo gancho que ele registrou (`at_shutdown`)."""
    from listener.ui import hf_test, live_prepare

    monkeypatch.setattr(live_prepare, "_shared", {})                                      # o preparador e o teste desta prova
    monkeypatch.setattr(live_prepare, "_hooks", [])
    monkeypatch.setattr(hf_test, "_shared", {})
    spawn = Spawner(_silent)
    tester = hf_test.hf_tester()
    assert tester.start("hf_x", spawn) and tester.running
    live_prepare.shutdown_preparer()
    assert spawn.children[0].killed and not tester.running and not tester.start("hf_x", spawn)


def test_the_deadline_kills_the_child_and_says_no_answer(qtbot, monkeypatch):
    from listener.ui import hf_test

    tester = hf_test.HfTokenTest()
    tester._timer.setInterval(50)
    spawn = Spawner(_silent)
    results: list[dict[str, Any]] = []
    tester.finished.connect(results.append)
    assert tester.start("hf_x", spawn) and not tester.start("hf_x", spawn)               # um teste por vez
    qtbot.waitUntil(lambda: bool(results), timeout=3000)
    assert results[0]["status"] == Status.TIMEOUT and spawn.children[0].killed and not tester.running
    tester.shutdown()
    assert not tester.start("hf_x", spawn)                                                # o app saindo: nada novo


def test_without_huggingface_hub_the_button_says_the_test_is_unavailable(qtbot, store, platform, monkeypatch):
    from listener.ui import hf_test
    from listener.ui.settings_min import SettingsDialog

    monkeypatch.setattr(hf_test, "available", lambda: False)
    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    button = dlg.hf_test.button
    assert button.text() == hf_test.UNAVAILABLE_TEXT and not button.isEnabled() and "huggingface-hub" in button.toolTip()
    dlg.reject()


def test_the_wizard_counts_the_steps_it_shows_and_tests_the_token(qtbot, tmp_path, monkeypatch):
    from listener.ui import hf_test
    from listener.ui.wizard import FirstRunWizard
    from tests.fake_platform import FakePlatform

    monkeypatch.setenv(hf_check.FAKE_ENV, "fake")
    monkeypatch.setattr(hf_test, "available", lambda: True)
    platform = FakePlatform(tmp_path)
    spawn = Spawner(_serve)
    monkeypatch.setattr(platform, "spawn_child", spawn)
    wiz = FirstRunWizard(platform, tmp_path / "db")
    qtbot.addWidget(wiz)
    steps = [wiz.page(i).subTitle() for i in wiz.pageIds()][1:]
    assert steps == [f"Passo {n} de {len(steps)}" for n in range(1, len(steps) + 1)] and len(steps) == 5
    assert "Em cinco passos:" in wiz.intro_page.text.text()
    page = wiz.keys_page
    page.hf_token.setText("hf_assistente_ok")
    page.hf_test_btn.click()
    qtbot.waitUntil(lambda: page.hf_status.text().startswith("Conectado"), timeout=5000)
    assert spawn.argv[0][-1] == "--hf-check"                                              # sem banco ainda: a pasta padrão
    spawn.target = _silent
    page.hf_test_btn.click()
    wiz.reject()                                                                          # fechar mata o teste em curso
    assert spawn.children[-1].killed and not page.hf_control.testing


# ---------------------------------------------------------------- U-111 e U-112: a faixa "Falta configurar"
def _prepare(gate: threading.Event, models: Path):
    from listener.live.protocol import encode_line

    def run(stdin: BinaryIO, stdout: BinaryIO) -> int:
        stdout.write(encode_line({"type": "progress", "model": "small", "status": "baixando", "fraction": 0.25}))
        stdout.flush()
        gate.wait(5.0)
        if stdin.closed:
            return 1
        for m in ("small", "base"):
            (models / f"faster-whisper-{m}").mkdir(parents=True, exist_ok=True)
            (models / f"faster-whisper-{m}" / "model.bin").write_bytes(b"x")
        stdout.write(encode_line({"type": "done", "models_dir": str(models)}))
        stdout.flush()
        return 0
    return run


def test_prepare_now_downloads_from_the_band_with_size_progress_and_cancel(qtbot, store, platform, monkeypatch, tmp_path):
    from listener.ui.live_prepare import ModelPreparer
    from listener.ui import warmup_notice
    from listener.ui.warmup import WarmupDialog

    monkeypatch.delenv("LISTENER_LIVE", raising=False)
    models = tmp_path / "modelos"
    store.set_setting("pipeline.models_dir", str(models))
    store.set_setting("live.enabled", True)
    prep = ModelPreparer()
    monkeypatch.setattr(warmup_notice, "preparer", lambda: prep)
    gate = threading.Event()
    monkeypatch.setattr(platform, "spawn_child", Spawner(_prepare(gate, models)))
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    note = dlg.setup_note
    assert "Modelos da legenda ao vivo (634 MB)" in note.text() and warmup_notice.RECORD_STOPS in note.text()
    note.linkActivated.emit("prepare")
    qtbot.waitUntil(lambda: "baixando small (25 %)" in note.text(), timeout=5000)
    assert warmup_notice.CANCEL in note.text() and warmup_notice.RECORD_STOPS in note.text()
    note.linkActivated.emit("cancel")                                                     # o Cancelar mata o download
    assert not prep.running and "Preparar agora" in note.text() and "falhou" not in note.text()
    note.linkActivated.emit("prepare")
    qtbot.waitUntil(lambda: "baixando" in note.text(), timeout=5000)
    gate.set()
    qtbot.waitUntil(lambda: prep.state["state"] == "done", timeout=5000)
    assert "Modelos" not in note.text() and dlg.live_check.isChecked()                    # pronto: a legenda volta ao pedido
    dlg.reject()


def test_live_names_are_off_with_the_reason_without_token_or_voice_models(qtbot, store, platform, monkeypatch, tmp_path):
    from listener.ui.warmup import WarmupDialog
    from listener.ui.warmup_live import NAMES_NEED_HF, NAMES_NEED_VOICES

    monkeypatch.delenv("LISTENER_LIVE", raising=False)
    models = tmp_path / "modelos"
    for m in ("small", "base"):
        (models / f"faster-whisper-{m}").mkdir(parents=True)
        (models / f"faster-whisper-{m}" / "model.bin").write_bytes(b"x")
    store.set_setting("pipeline.models_dir", str(models))
    store.set_setting("live.enabled", True)
    store.set_setting("live.names", True)
    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    assert dlg.live_check.isChecked() and not dlg.names_check.isEnabled() and dlg.names_check.toolTip() == NAMES_NEED_HF
    assert dlg.result_config()[1]["live_names"] is False
    store.upsert_provider(HF_PROVIDER_ID, "huggingface", api_key="hf_abc")
    dlg.notice.on_link("keys")                                                            # a volta das Configurações
    assert not dlg.names_check.isEnabled() and dlg.names_check.toolTip() == NAMES_NEED_VOICES
    (models / hf_check.PYANNOTE_READY).write_text("ok", encoding="utf-8")
    dlg.notice.on_link("keys")
    assert dlg.names_check.isEnabled() and dlg.result_config()[1]["live_names"] is True
    dlg.reject()


# ---------------------------------------------------------------- U-110: a lista das fontes e o combo do microfone
def test_an_output_that_arrives_with_the_dialog_open_is_new_and_the_mic_combo_cuts_in_the_middle(qtbot, store, platform):
    from listener.platform.base import Endpoint, Endpoints
    from listener.ui.devices import ElidedCombo
    from listener.ui.devices_panel import NEW_ROLE
    from listener.ui.warmup import WarmupDialog
    from tests.fake_platform import BASE_ENDPOINTS

    dlg = WarmupDialog(platform, store)
    qtbot.addWidget(dlg)
    lst = dlg.sources.list
    assert not any(lst.item(r).data(NEW_ROLE) for r in range(lst.count()))
    fone = Endpoint("fone-novo", "Fone de ouvido novo")
    platform.endpoints = Endpoints(BASE_ENDPOINTS.capture, (*BASE_ENDPOINTS.render, fone), BASE_ENDPOINTS.capture_default,
                                   BASE_ENDPOINTS.capture_comm, BASE_ENDPOINTS.render_default, BASE_ENDPOINTS.render_comm)
    assert dlg.sources.refresh_outputs()
    assert [lst.item(r).data(NEW_ROLE) for r in range(lst.count())] == [False, False, True]
    combo = dlg.mic_combo
    assert isinstance(combo, ElidedCombo)
    long_name = "Grupo de microfones (Tecnologia Intel Smart Sound para microfones digitais)"
    combo.addItem(long_name, "longo")
    combo.setCurrentIndex(combo.count() - 1)
    combo.setFixedWidth(300)
    head, _sep, tail = combo.shown_text().partition("…")
    assert head and tail and long_name.startswith(head) and long_name.endswith(tail) and combo.toolTip() == long_name
    dlg.reject()


# ---------------------------------------------------------------- U-120 e A-34: o "Avançado" e a pasta dos modelos
def test_advanced_group_is_closed_editable_and_an_error_inside_opens_it(qtbot, store, platform, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from listener.config.defaults import SPECS
    from listener.ui.settings_min import SettingsDialog

    dlg = SettingsDialog(store, platform, {})
    qtbot.addWidget(dlg)
    advanced = [k for k, s in SPECS.items() if s.advanced]
    assert set(advanced) == {"pipeline.whisper_hq", "pipeline.threshold", "pipeline.margin", "pipeline.ref_cos", "pipeline.max_refs",
                             "pipeline.models_dir", "pipeline.torch_threads", "pipeline.diar_window_s", "pipeline.whisper_window_s",
                             "pipeline.embedding_batch", "pipeline.echo_gate"}                                # F5.18 (PF-03, PF-07)
    assert not dlg.advanced.is_open() and all(dlg.advanced.isAncestorOf(dlg.editor(k).widget) for k in advanced)
    assert not dlg.advanced.isAncestorOf(dlg.editor("pipeline.transcriber").widget)
    chosen = tmp_path / "outra-pasta"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(chosen)))
    dlg._browse_models()
    assert dlg.models_dir.text() == str(chosen) and not dlg.models_note.isHidden()      # a pasta nova baixa tudo de novo
    assert "baixados de novo" in dlg.models_note.text()
    dlg.show_problem("Margem: acima do máximo", dlg.editor("pipeline.margin").widget)     # o erro do Salvar, dentro do grupo
    assert dlg.advanced.is_open() and dlg.nav.currentItem().text() == "Processamento"
    assert store.get_setting("pipeline.models_dir") == ""
    dlg._save()
    assert store.get_setting("pipeline.models_dir") == str(chosen)
