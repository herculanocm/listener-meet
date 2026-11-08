"""F5.19 na reunião aberta (U-150 a U-152): os itens do Exportar, o áudio e o vídeo numa tarefa com o progresso e o
"Cancelar" numa faixa própria (A-15), os avisos sem diálogo (sem áudio, gravando, sem o arquivo de áudio, sem transcrição),
o item do vídeo desabilitado sem o PyAV, a espera parada com uma gravação em curso (A-07), uma exportação de mídia por app
(A-17), o destino aberto em outro programa avisado antes de gerar (A-19), o cancelamento sem arquivo, a pergunta do fechar
e a saída do app que cancela e espera. Os diálogos modais ficam trocados por registros: nada abre de verdade."""
from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from listener.ui import media_save as msave
from listener.ui.media_audio import ExportCancelledError
from tests.test_media_export import _audio_meeting, _speech_meeting

pytest.importorskip("PySide6")


@pytest.fixture(autouse=True)
def no_modal(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Nenhuma caixa modal nos testes: os avisos vão para a lista, e fechar uma janela no meio cancela (o teste que quer a
    outra resposta troca o `ask_close` da instância)."""
    from PySide6.QtWidgets import QMessageBox

    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **_k: warned.append(str(a[2]))))
    monkeypatch.setattr(msave.MediaSaver, "ask_close", lambda _self, _text: True)
    yield warned
    msave._running.clear()                                                   # nada vaza para o próximo teste


def _window(qtbot: Any, store: Any, mid: int, recording: Any = None) -> Any:
    from listener.ui.meeting import MeetingWindow

    win = MeetingWindow(store, mid, recording=recording)
    qtbot.addWidget(win)
    return win


def _wav(win: Any) -> Any:
    return next(f for f in win.media.formats if f.key == "wav")


def _finished(task: Any) -> bool:
    """A tarefa terminou (a já apagada pelo `release` também)."""
    try:
        return bool(task.isFinished())
    except RuntimeError:
        return True


def _slow(_store: Any, _plan: Any, _target: Path, _fmt: Any, tick: Any) -> Path:
    """Uma gravação que só termina quando alguém cancela (o cancelamento sem corrida de tempo)."""
    while not tick("write", 0.5):
        time.sleep(0.01)
    raise ExportCancelledError


def test_export_menu_has_both_items_and_the_audio_saves_in_a_task(qtbot, store, tmp_path):
    win = _window(qtbot, store, _speech_meeting(store))
    actions = win.btn_export.menu().actions()
    assert win.media.act_audio in actions and win.media.act_video in actions
    assert win.media.act_audio.text() == "Salvar áudio…" and win.media.act_video.text() == "Salvar vídeo com legendas…"
    assert win.media.act_video.isEnabled() == msave.video_available() and "pausas ficam fora" in win.media.act_audio.toolTip()
    assert not win.media.bar.isVisibleTo(win)
    task = win.media.save_audio(str(tmp_path / "reuniao"), _wav(win))
    assert task is not None and win.media.busy and not win.media.act_audio.isEnabled() and not win.media.act_video.isEnabled()
    assert win.media.bar.isVisibleTo(win) and win.media.bar.action.text() == "Cancelar"
    assert win.media.save_audio(str(tmp_path / "outra.wav")) is None                    # uma por vez
    qtbot.waitUntil(lambda: win.media.task is None, timeout=20000)
    out = tmp_path / "reuniao.wav"
    assert out.exists() and win.exported[-1] == str(out) and win.message.text() == f"Salvo em {out}."
    assert win.media.act_audio.isEnabled() and not win.media.bar.isVisibleTo(win)


def test_the_video_reports_progress_by_stage_in_its_own_bar_and_saves_the_mp4(qtbot, store, tmp_path, monkeypatch):
    pytest.importorskip("av")
    win = _window(qtbot, store, _speech_meeting(store))
    seen: list[str] = []
    real = win.media._progress

    def progress(step: object) -> None:
        real(step)
        seen.append(win.media.bar.label.text())

    monkeypatch.setattr(win.media, "_progress", progress)
    win.show_message("Ata copiada (texto e formatação).")                    # o aviso de outra ação fica na faixa comum
    task = win.media.save_video(str(tmp_path / "reuniao"))
    assert task is not None
    qtbot.waitUntil(lambda: win.media.task is None, timeout=60000)
    out = tmp_path / "reuniao.mp4"
    assert out.exists() and out.stat().st_size > 0 and win.message.text() == f"Salvo em {out}."
    assert any(s.startswith("Preparando o áudio…") for s in seen) and "Gerando o vídeo… 100 %" in seen


def test_warnings_without_audio_while_recording_without_the_file_or_transcript(qtbot, store, tmp_path):
    recording = store.create_meeting("Gravando", None, None, None)
    assert store.get_meeting(recording)["status"] == "gravando"
    win = _window(qtbot, store, recording)
    assert win.media.save_audio(str(tmp_path / "a.wav")) is None and win.message.text() == msave.RECORDING

    silent = store.create_meeting("Sem áudio", None, None, None)
    store.finish_meeting(silent, 5000)
    win = _window(qtbot, store, silent)
    assert win.media.save_audio(str(tmp_path / "a.wav")) is None and win.message.text() == msave.NO_AUDIO

    moved = _audio_meeting(store)
    store._q("UPDATE meetings SET audio_file = ? WHERE id = ?", ("listener-audio-1999-01.db", moved))
    store.core.forget_meeting(moved)
    win = _window(qtbot, store, moved)
    assert win.media.save_audio(str(tmp_path / "a.wav")) is None
    assert win.message.text() == "Sem o arquivo de áudio desta reunião na pasta do banco (listener-audio-1999-01.db)."
    assert not (store.base_dir / "listener-audio-1999-01.db").exists()       # o anexo não criou um arquivo vazio

    if msave.video_available():
        untranscribed = _audio_meeting(store)
        win = _window(qtbot, store, untranscribed)
        assert win.media.save_video(str(tmp_path / "v.mp4")) is None and win.message.text() == msave.NO_SPEECH
    assert sorted(p.name for p in tmp_path.iterdir()) == ["db"]               # nenhum arquivo nem diálogo


def test_the_video_item_is_disabled_with_the_tip_without_pyav(qtbot, store, monkeypatch):
    monkeypatch.setattr(msave, "video_available", lambda: False)
    win = _window(qtbot, store, _speech_meeting(store))
    assert not win.media.act_video.isEnabled() and win.media.act_video.toolTip() == msave.VIDEO_MISSING
    assert win.media.save_video("x.mp4") is None and win.media.act_audio.isEnabled()


def test_cancel_from_its_bar_leaves_no_file_and_closing_in_the_middle_asks(qtbot, store, tmp_path, monkeypatch):
    monkeypatch.setattr(msave, "write_audio", _slow)
    win = _window(qtbot, store, _speech_meeting(store))
    win.show()
    task = win.media.save_audio(str(tmp_path / "reuniao.wav"), _wav(win))
    assert task is not None
    qtbot.waitUntil(lambda: win.media.bar.label.text().startswith("Gravando o áudio…"), timeout=10000)
    win.media.bar.action.click()                                              # o "Cancelar" da faixa da exportação
    qtbot.waitUntil(lambda: win.media.task is None, timeout=10000)
    assert win.message.text() == "Exportação cancelada." and not (tmp_path / "reuniao.wav").exists()

    asked: list[str] = []
    answer = {"stop": False}

    def ask(text: str) -> bool:
        asked.append(text)
        return answer["stop"]

    monkeypatch.setattr(win.media, "ask_close", ask)
    running = win.media.save_audio(str(tmp_path / "reuniao.wav"), _wav(win))
    assert running is not None
    win.close()                                                               # "Continuar gerando": a janela fica
    assert win.isVisible() and asked and "Fechar a janela cancela a exportação" in asked[-1] and win.media.busy
    answer["stop"] = True
    win.close()                                                               # "Cancelar e fechar"
    assert not win.isVisible()
    qtbot.waitUntil(lambda: _finished(running), timeout=10000)
    qtbot.waitUntil(lambda: not msave._running, timeout=5000)                 # o fim solta a vaga do app mesmo sem a janela
    assert not (tmp_path / "reuniao.wav").exists()


def test_a_recording_in_progress_makes_the_export_wait_then_it_goes_on(qtbot, store, tmp_path):
    state = {"recording": True}
    win = _window(qtbot, store, _speech_meeting(store), recording=lambda: state["recording"])
    task = win.media.save_audio(str(tmp_path / "reuniao.wav"), _wav(win))
    assert task is not None
    qtbot.waitUntil(lambda: win.media.bar.label.text() == msave.WAITING, timeout=10000)
    time.sleep(0.3)
    assert win.media.busy and not (tmp_path / "reuniao.wav").exists()        # parada enquanto a gravação segue
    state["recording"] = False
    qtbot.waitUntil(lambda: win.media.task is None, timeout=20000)
    assert (tmp_path / "reuniao.wav").exists() and win.message.text().startswith("Salvo em ")


def test_one_media_export_per_app_and_a_destination_in_use_warns_before_generating(qtbot, store, tmp_path, monkeypatch, no_modal):
    monkeypatch.setattr(msave, "write_audio", _slow)
    first = _window(qtbot, store, _speech_meeting(store))
    second = _window(qtbot, store, _speech_meeting(store))
    task = first.media.save_audio(str(tmp_path / "um.wav"), _wav(first))
    assert task is not None
    assert second.media.save_audio(str(tmp_path / "dois.wav"), _wav(second)) is None
    assert second.message.text().startswith("Outra exportação de áudio ou vídeo está em curso") and not second.media.busy
    first.media.cancel()
    qtbot.waitUntil(lambda: not msave._running, timeout=10000)
    monkeypatch.setattr(msave, "held_by_another", lambda _target: True)      # o player segura o arquivo que já existe
    assert second.media.save_audio(str(tmp_path / "dois.wav"), _wav(second)) is None
    assert "aberto em outro programa (um player de áudio ou de vídeo)" in no_modal[-1] and not second.media.busy


def test_the_app_exit_cancels_and_waits_for_the_export(qtbot, store, tmp_path, monkeypatch):
    monkeypatch.setattr(msave, "write_audio", _slow)
    win = _window(qtbot, store, _speech_meeting(store))
    task = win.media.save_audio(str(tmp_path / "reuniao.wav"), _wav(win))
    assert task is not None
    win.shutdown()                                                            # a saída do app: cancela e espera antes do banco
    assert _finished(task) and win.media.task is None and win.media.confirm_close()


def test_the_task_rechecks_the_audio_file_and_frees_the_app_slot_even_when_it_ends_at_once(qtbot, store, tmp_path, monkeypatch):
    """A-08: o arquivo de áudio some entre o clique e a tarefa (o diálogo não tem prazo); a tarefa confere de novo antes de
    ler, avisa e termina na hora, e a vaga do app sai mesmo assim (ela é posta antes de a tarefa nascer)."""
    win = _window(qtbot, store, _speech_meeting(store))
    real = msave.missing_audio
    calls: list[int] = []

    def gone_later(store_: Any, mid: int) -> str | None:
        calls.append(mid)
        return real(store_, mid) if len(calls) == 1 else "Sem o arquivo de áudio desta reunião na pasta do banco (x.db)."

    monkeypatch.setattr(msave, "missing_audio", gone_later)
    task = win.media.save_audio(str(tmp_path / "a.wav"), _wav(win))
    assert task is not None
    qtbot.waitUntil(lambda: win.media.task is None, timeout=10000)
    assert win.message.text().startswith("Sem o arquivo de áudio") and not msave._running and len(calls) == 2
    assert not (tmp_path / "a.wav").exists()
