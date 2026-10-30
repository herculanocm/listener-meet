"""F5.8: o modo empacotado no código, sem empacotar. O comando do worker, do ao vivo e do preparo com `sys.frozen` (relança o
próprio `.exe`); a entrada do microfone no caminho longo (`entry_name`) e as do próprio app (`own_entries`); o
`setup()` do log sem stderr (o `.exe` sem console); a exceção que escapa do `main()` no log antes do `shutdown`; os logs de
lançamento sem caminho; e as flags de criação dos filhos no Windows (sem o cursor de "carregando")."""
from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import listener.__main__ as entry
from listener import log as applog
from listener.platform.common import process
from listener.platform.win32 import consent

EXE = r"C:\Programas\listener-meet\listener-meet.exe"


def test_the_packaged_app_relaunches_its_own_exe_with_the_flags(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", EXE)
    assert process.worker_command() == [EXE, "--worker"]
    assert process.worker_command(light=True) == [EXE, "--worker", "--light"]
    assert process.live_command("D", 7) == [EXE, "--live", "--db", "D", "--meeting", "7"]
    assert process.live_prepare_command(what="rag") == [EXE, "--live", "--prepare", "--what", "rag"]


def test_the_venv_still_runs_the_package(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert process.worker_command() == [sys.executable, "-m", "listener", "--worker"]


def test_entry_name_falls_back_to_the_raw_path_when_it_cannot_resolve(monkeypatch):
    def unreadable(self: Path, strict: bool = False) -> Path:
        raise OSError("sem acesso")

    monkeypatch.setattr(Path, "resolve", unreadable)
    assert consent.entry_name(r"C:\Pasta\app.exe") == "C:#Pasta#app.exe"


def test_entry_name_turns_a_short_path_into_the_long_one(tmp_path):
    """O registro guarda o caminho longo; o `.exe` aberto por um caminho 8.3 (o `%TEMP%` desta máquina é `HERCUL~1.CON`)
    tem que dar a mesma entrada. Pulado onde o volume não gera nomes 8.3."""
    if sys.platform != "win32":
        pytest.skip("caminho 8.3 só existe no Windows")
    folder = tmp_path / "pasta com um nome bem comprido"
    folder.mkdir()
    exe = folder / "listener-meet.exe"
    exe.write_bytes(b"")
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW(str(exe), buf, len(buf))
    long_path = str(exe.resolve())
    if not n or buf.value.lower() == long_path.lower():
        pytest.skip("o volume não gera nomes 8.3")
    assert "~" in buf.value
    assert consent.entry_name(buf.value) == consent.entry_name(long_path) == long_path.replace("\\", "#")
    assert consent.entry_name(buf.value, resolve=False) == buf.value.replace("\\", "#")


def test_own_entries_hold_the_raw_and_the_resolved_path(tmp_path, monkeypatch):
    """O `.exe` aberto por um caminho 8.3: não se sabe se o Windows guarda o cru ou o resolvido (unidade mapeada, `subst`,
    junção), então os dois tiram o próprio app."""
    if sys.platform != "win32":
        pytest.skip("caminho 8.3 só existe no Windows")
    folder = tmp_path / "outra pasta com nome comprido"
    folder.mkdir()
    exe = folder / "listener-meet.exe"
    exe.write_bytes(b"")
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW(str(exe), buf, len(buf))
    if not buf.value or buf.value.lower() == str(exe.resolve()).lower():
        pytest.skip("o volume não gera nomes 8.3")
    consent.own_entries.cache_clear()
    try:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", buf.value)
        assert consent.own_entries() == {buf.value.replace("\\", "#").lower(), str(exe.resolve()).replace("\\", "#").lower()}
    finally:
        consent.own_entries.cache_clear()


def test_own_entries_are_the_exe_when_packaged_and_are_kept(monkeypatch):
    consent.own_entries.cache_clear()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", EXE)
    monkeypatch.setattr(sys, "_base_executable", r"C:\Python312\python.exe", raising=False)
    try:
        assert consent.own_entries() == {"c:#programas#listener-meet#listener-meet.exe"}
        monkeypatch.setattr(sys, "executable", r"C:\Outro\outro.exe")
        assert consent.own_entries() == {"c:#programas#listener-meet#listener-meet.exe"}    # guardado no processo
    finally:
        consent.own_entries.cache_clear()


def test_setup_without_stderr_logs_only_to_the_file(tmp_path, monkeypatch):
    """O `.exe` aberto pelo Explorer não tem stderr (`sys.stderr` None): nenhum handler para ele, o arquivo segue."""
    applog.shutdown()
    monkeypatch.setattr(sys, "stderr", None)
    applog.setup("INFO")
    listener = applog._state.listener
    assert listener is not None and [h.name for h in listener.handlers] == []
    path = applog.attach_file(tmp_path / "logs" / "listener.log")
    applog.get_logger("teste").info("sem console")
    applog.flush()
    assert "sem console" in path.read_text(encoding="utf-8")


def test_the_versions_line_marks_the_exe(tmp_path, monkeypatch):
    applog.setup("INFO")
    path = applog.attach_file(tmp_path / "l.log")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    applog.log_versions()
    monkeypatch.delattr(sys, "frozen")
    applog.log_versions()
    applog.flush()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if "listener-meet" in ln]
    assert f", {sys.platform}, exe, sqlite" in lines[0] and ", exe," not in lines[1]


def test_main_logs_the_escaping_exception_before_the_shutdown(tmp_path, monkeypatch):
    """Antes o `finally` tirava o handler e o `excepthook` rodava depois, sem arquivo: o traceback se perdia (F5.8)."""
    applog.setup("INFO")
    path = applog.attach_file(tmp_path / "l.log")

    def broken(_argv: Any) -> int:
        raise RuntimeError("módulo que faltou no build")

    monkeypatch.setattr(entry, "_dispatch", broken)
    with pytest.raises(RuntimeError):
        entry.main([])
    text = path.read_text(encoding="utf-8")
    assert "exceção não tratada" in text and "Traceback" in text and "RuntimeError: módulo que faltou no build" in text


def test_the_escaping_exception_reaches_stderr_once(capsys, monkeypatch):
    """R-03: o `main` loga a exceção (o stderr do venv) e o `excepthook`, que roda depois do `shutdown`, não a repete pelo
    `lastResort`; o aviso de queda continua indo ao notificador, se houver."""
    applog.shutdown()
    applog.setup("INFO")                                            # o handler de stderr no stderr capturado
    notes: list[str] = []
    applog.set_crash_notifier(notes.append)

    def broken(_argv: Any) -> int:
        raise RuntimeError("caiu no despacho")

    monkeypatch.setattr(entry, "_dispatch", broken)
    try:
        with pytest.raises(RuntimeError) as info:
            entry.main([])
        applog.handle_exception(RuntimeError, info.value, info.value.__traceback__)    # o excepthook, depois do main
    finally:
        applog.set_crash_notifier(None)
    err = capsys.readouterr().err
    assert err.count("exceção não tratada") == 1 and err.count("Traceback") == 1 and err.count("caiu no despacho") == 2
    assert notes == ["RuntimeError: caiu no despacho"]


def test_launch_logs_keep_only_the_exe_name_and_the_flags(tmp_path):
    exe, db = str(tmp_path / "listener-meet.exe"), str(tmp_path / "banco")
    assert applog.argv_text([exe, "--live", "--prepare", "--what", "rag", "--db", db]) == \
        "listener-meet.exe --live --prepare --what rag --db"
    assert applog.argv_text([exe, "--live", "--db", db, "--meeting", "7"]) == "listener-meet.exe --live --db --meeting 7"
    assert applog.argv_text(process.worker_command()).endswith(" -m listener --worker") and applog.argv_text([]) == ""


def test_launch_logs_drop_the_value_of_path_flags_also_inline_and_relative(tmp_path):
    """R-06: `--db=PASTA` sai inteiro só como `--db`; o valor das flags de caminho sai mesmo relativo e sem barra."""
    exe, db = str(tmp_path / "listener-meet.exe"), str(tmp_path / "banco")
    assert applog.argv_text([exe, "--live", f"--db={db}", "--meeting=7"]) == "listener-meet.exe --live --db --meeting=7"
    assert applog.argv_text([exe, "--worker", "--db", "banco", "--once"]) == "listener-meet.exe --worker --db --once"
    assert applog.argv_text([exe, "--selftest", "--app", "Teams.exe", "--check=relatorio"]) == \
        "listener-meet.exe --selftest --app --check"
    assert applog.argv_text([exe, str(tmp_path / "script.py"), "--worker", "listener"]) == \
        "listener-meet.exe --worker listener"


def test_the_children_start_without_the_busy_cursor(monkeypatch):
    """Com o `.exe` os filhos são processos de janela: sem `STARTF_FORCEOFFFEEDBACK` o Windows mostraria o cursor de
    "carregando" a cada worker e a cada ao vivo. As flags de antes (sem janela, abaixo do normal) seguem."""
    if sys.platform != "win32":
        pytest.skip("as flags de criação só existem no Windows")
    from listener.platform.win32 import platform as win_platform

    seen: list[dict[str, Any]] = []

    class FakePopen:
        pid = 4321

        def __init__(self, argv: list[str], **kw: Any) -> None:
            seen.append(kw)

    monkeypatch.setattr(win_platform.subprocess, "Popen", FakePopen)
    plat = win_platform.Win32Platform()
    assert plat.spawn_detached(["x.exe", "--worker"]) == 4321
    plat.spawn_child(["x.exe", "--live"])
    for kw in seen:
        assert kw["startupinfo"].dwFlags & win_platform.STARTF_FORCEOFFFEEDBACK
        assert kw["creationflags"] == win_platform.CREATE_NO_WINDOW | win_platform.BELOW_NORMAL_PRIORITY_CLASS
    assert seen[0]["stdout"] == subprocess.DEVNULL and seen[1]["stdout"] == subprocess.PIPE
