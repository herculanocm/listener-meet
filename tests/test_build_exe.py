"""F5.8: as partes puras de `scripts/build_exe.py`, sem empacotar. O comando do PyInstaller (em pasta, sem console, os
pacotes que a análise não vê e os fakes de fora); a recusa em duas camadas (o `.exe` aberto, com `opener` injetado, e a
pasta antiga trocada de lado e devolvida, também quando o build cai, e o `.old` que sobrou); o ICO escrito à mão; a versão do
arquivo, o commit carimbado e o zip para compartilhar (D-081); o lançador; e a sonda sem caminho do usuário nos erros."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
PNG = b"\x89PNG\r\n\x1a\n"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_exe = _load("build_exe")
exe_probe = _load("exe_probe")


def test_the_command_is_a_windowed_folder_without_the_fakes():
    cmd = build_exe.pyinstaller_command(Path("launcher.py"), Path("icone.ico"), Path("versao.txt"), ["numpy", "torch"])
    for flag in ("--onedir", "--windowed", "--clean", "--noconfirm", "--exclude-module=tests", "--collect-submodules=listener",
                 "--name=listener-meet", "--icon=icone.ico", "--version-file=versao.txt", "--hidden-import=truststore",
                 "--hidden-import=exe_probe", "--copy-metadata=numpy", "--copy-metadata=torch", "--python-option=X utf8"):
        assert flag in cmd
    for package in ("av", "filelock", "pyannote.audio"):
        assert f"--collect-submodules={package}" in cmd
    for package in ("listener.ui.icons", "faster_whisper", "pyannote.audio", "lightning"):
        assert f"--collect-data={package}" in cmd
    assert "--onefile" not in cmd and cmd[-1] == "launcher.py" and cmd[1:3] == ["-m", "PyInstaller"]


def test_the_refusal_when_the_old_exe_is_open(tmp_path):
    dist = tmp_path / "listener-meet"
    assert build_exe.check_not_running(dist) is None                          # primeiro build: nada a recusar
    dist.mkdir()
    (dist / "listener-meet.exe").write_bytes(b"MZ")

    def busy(path: Path, mode: str) -> Any:
        raise PermissionError(13, "o arquivo está em uso por outro processo")

    assert build_exe.check_not_running(dist, opener=busy) == build_exe.REFUSAL
    assert build_exe.check_not_running(dist) is None                          # fechado: abre para escrita
    assert "Gerenciador de Tarefas" in build_exe.REFUSAL and "feche o listener-meet" in build_exe.REFUSAL


def _folder(path: Path, marker: str) -> Path:
    path.mkdir(parents=True)
    (path / "listener-meet.exe").write_text(marker, encoding="utf-8")
    return path


def test_the_old_folder_goes_aside_and_leaves_when_the_build_works(tmp_path):
    dist = _folder(tmp_path / "listener-meet", "antigo")
    old = build_exe.move_aside(dist)
    assert old == tmp_path / "listener-meet.old" and not dist.exists()
    assert (old / "listener-meet.exe").read_text(encoding="utf-8") == "antigo"
    _folder(dist, "novo")
    assert build_exe.settle(dist, old, ok=True) is None
    assert not old.exists() and (dist / "listener-meet.exe").read_text(encoding="utf-8") == "novo"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["listener-meet"]          # nem `.old` nem `.apagar`
    assert build_exe.move_aside(tmp_path / "nada") is None and build_exe.settle(dist, None, ok=False) is None


def test_the_old_folder_comes_back_when_the_build_fails(tmp_path):
    dist = _folder(tmp_path / "listener-meet", "antigo")
    old = build_exe.move_aside(dist)
    _folder(dist, "incompleto")                                                 # o PyInstaller parou no meio
    assert build_exe.settle(dist, old, ok=False) is None
    assert (dist / "listener-meet.exe").read_text(encoding="utf-8") == "antigo"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["listener-meet"]


def test_a_folder_with_something_open_is_never_half_deleted(tmp_path, monkeypatch):
    """R-02: apagar é renomear antes; com algo aberto lá dentro a renomeação falha e a pasta fica inteira, com o aviso (o
    dono abriu o `.exe` antigo no meio do build)."""
    dist = _folder(tmp_path / "listener-meet", "antigo")
    old = build_exe.move_aside(dist)
    _folder(dist, "novo")
    rename = Path.rename

    def busy(self: Path, target: Any) -> Path:
        if str(target).endswith(".apagar"):
            raise PermissionError(13, "em uso")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", busy)
    warning = build_exe.settle(dist, old, ok=True)
    assert warning is not None and "listener-meet.old" in warning and str(tmp_path) not in warning
    assert old is not None and (old / "listener-meet.exe").read_text(encoding="utf-8") == "antigo"
    warning = build_exe.settle(dist, old, ok=False)                            # a parcial também não sai pela metade
    assert warning is not None and (dist / "listener-meet.exe").read_text(encoding="utf-8") == "novo"


def test_an_old_folder_left_alone_by_a_killed_build_comes_back_first(tmp_path):
    """Um build morto (o processo, não o Python) deixou só o `.old`: ele é a pasta boa, volta e segue o caminho normal."""
    _folder(tmp_path / "listener-meet.old", "bom")
    dist = tmp_path / "listener-meet"
    old = build_exe.move_aside(dist)
    assert old == tmp_path / "listener-meet.old" and not dist.exists()
    assert (old / "listener-meet.exe").read_text(encoding="utf-8") == "bom"


def test_an_old_folder_next_to_a_new_one_is_the_owners_choice(tmp_path, monkeypatch, capsys):
    """O `.old` e uma pasta nova lado a lado: só o dono sabe qual vale. O build recusa e não mexe em nenhuma das duas."""
    old = _folder(tmp_path / "dist" / "listener-meet.old", "bom")
    dist = _folder(tmp_path / "dist" / "listener-meet", "parcial")

    def never(*_: Any) -> Any:
        raise AssertionError("não devia preparar nada")

    monkeypatch.setattr(build_exe, "prepare", never)
    assert build_exe.build("0.4.0", dist=dist, out=tmp_path, run=lambda cmd: 0) == 2
    assert build_exe.INTERRUPTED in capsys.readouterr().err
    assert (old / "listener-meet.exe").read_text(encoding="utf-8") == "bom"
    assert (dist / "listener-meet.exe").read_text(encoding="utf-8") == "parcial"


def _prepared(failed: list[str]) -> Any:
    return build_exe.Prepared(Path("l.py"), Path("i.ico"), Path("v.txt"), ["numpy"], failed)


def test_an_interrupted_pyinstaller_gives_the_old_folder_back(tmp_path, monkeypatch):
    """R-01: Ctrl+C (ou exceção) no meio do PyInstaller: o `finally` tira a pasta parcial e devolve a antiga."""
    dist = _folder(tmp_path / "dist" / "listener-meet", "antigo")
    monkeypatch.setattr(build_exe, "prepare", lambda *_: _prepared([]))

    def ctrl_c(_cmd: list[str]) -> int:
        _folder(dist, "parcial")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        build_exe.build("0.4.0", dist=dist, out=tmp_path, run=ctrl_c)
    assert (dist / "listener-meet.exe").read_text(encoding="utf-8") == "antigo"
    assert sorted(p.name for p in dist.parent.iterdir()) == ["listener-meet"]


def test_a_build_that_works_leaves_one_folder_and_the_probe_failures(tmp_path, monkeypatch):
    """R-09: os módulos que a sondagem da metadata não importou vão ao build-info.json (o stderr os mostra no `prepare`)."""
    dist = _folder(tmp_path / "dist" / "listener-meet", "antigo")
    monkeypatch.setattr(build_exe, "prepare", lambda *_: _prepared(["listener.algum"]))

    def works(_cmd: list[str]) -> int:
        _folder(dist, "novo")
        return 0

    assert build_exe.build("0.4.0", dist=dist, out=tmp_path, run=works) == 0
    assert (dist / "listener-meet.exe").read_text(encoding="utf-8") == "novo"
    assert sorted(p.name for p in dist.parent.iterdir()) == ["listener-meet"]
    info = json.loads((tmp_path / "build-info.json").read_text(encoding="utf-8"))
    assert info["exit"] == 0 and info["metadata_probe_failed"] == ["listener.algum"] and info["files"] == 1


def test_a_build_without_the_exe_counts_as_failed(tmp_path, monkeypatch):
    dist = _folder(tmp_path / "dist" / "listener-meet", "antigo")
    monkeypatch.setattr(build_exe, "prepare", lambda *_: _prepared([]))
    assert build_exe.build("0.4.0", dist=dist, out=tmp_path, run=lambda cmd: 0) == 1     # saiu 0 sem o .exe
    assert (dist / "listener-meet.exe").read_text(encoding="utf-8") == "antigo"


def test_ico_bytes_writes_one_png_per_size():
    pngs = {256: PNG + b"g" * 30, 16: PNG + b"p", 48: PNG + b"m" * 5}
    data = build_exe.ico_bytes(pngs)
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind, count) == (0, 1, 3)
    seen = []
    for i in range(count):
        width, height, colors, _r, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert data[offset:offset + size].startswith(PNG) and (colors, planes, bits) == (0, 1, 32)
        seen.append((width, height, size))
    assert seen == [(16, 16, len(pngs[16])), (48, 48, len(pngs[48])), (0, 0, len(pngs[256]))]     # 256 px vira 0
    assert len(data) == 6 + 16 * 3 + sum(len(p) for p in pngs.values())


def test_the_file_version_has_four_integer_parts():
    assert build_exe.version_tuple("0.4.0") == (0, 4, 0, 0)
    assert build_exe.version_tuple("1.2.3.4.5") == (1, 2, 3, 4)
    assert build_exe.version_tuple("0.5.0rc1") == (0, 5, 0, 0)
    assert build_exe.version_tuple("2") == (2, 0, 0, 0)


def test_the_launcher_dispatches_the_app_the_probe_and_the_multiprocessing_child():
    code = build_exe.LAUNCHER
    compile(code, "listener_meet.py", "exec")
    assert "from listener.__main__ import main" in code and 'sys.argv[1:2] == ["--probe"]' in code
    assert '"--multiprocessing-fork" in sys.argv' in code and "freeze_support()" in code


def test_the_script_refuses_outside_windows(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    assert build_exe.main() == 2 and "só para o Windows" in capsys.readouterr().err


def test_the_version_file_carries_the_name_and_the_version():
    text = build_exe.VERSION_INFO.format(v=build_exe.version_tuple("0.5.0"), s="0.5.0", ps="0.5.0+45fb4ad", name="listener-meet",
                                         exe="listener-meet.exe")
    assert "filevers=(0, 5, 0, 0)" in text and "'FileDescription', 'listener-meet'" in text and "'FileVersion', '0.5.0'" in text
    assert "'ProductVersion', '0.5.0+45fb4ad'" in text                         # D-081: a de produto leva o commit


def _git(head: str, changed: str = "", code: int = 0) -> Callable[..., Any]:
    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, code, stdout=head, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=changed, stderr="")
    return run


def test_the_stamp_is_the_short_commit_dirty_with_changes_or_no_git(tmp_path):
    """D-081: o hash curto; `.dirty` com arquivo rastreado mudado sem commit; "sem-git" fora de um repositório ou sem git."""
    assert build_exe.git_stamp(tmp_path, run=_git("45fb4ad\n")) == "45fb4ad"
    assert build_exe.git_stamp(tmp_path, run=_git("45fb4ad\n", " M listener/__init__.py\n")) == "45fb4ad.dirty"
    assert build_exe.git_stamp(tmp_path, run=_git("", code=128)) == "sem-git"

    def no_git(*_: Any, **__: Any) -> Any:
        raise FileNotFoundError(2, "git")

    assert build_exe.git_stamp(tmp_path, run=no_git) == "sem-git"
    assert re.fullmatch(r"[0-9a-f]{7,40}(\.dirty)?|sem-git", build_exe.git_stamp(ROOT))     # o git de verdade, aqui


def test_the_build_stamps_the_commit_in_the_package_and_the_product_version(tmp_path, monkeypatch):
    """D-081: o carimbo chega ao `prepare`, entra no pacote `listener` pelo `--add-data` e vai à versão de produto."""
    dist = tmp_path / "dist" / "listener-meet"
    stamp_file = tmp_path / "stamp" / build_exe.STAMP
    seen: dict[str, Any] = {}

    def prepare(version: str, stamp: str) -> Any:
        seen["stamp"] = stamp
        return build_exe.Prepared(Path("l.py"), Path("i.ico"), Path("v.txt"), ["numpy"], [], stamp_file)

    def works(cmd: list[str]) -> int:
        seen["cmd"] = cmd
        _folder(dist, "novo")
        return 0

    monkeypatch.setattr(build_exe, "prepare", prepare)
    assert build_exe.build("0.5.0", dist=dist, out=tmp_path, run=works, stamp="45fb4ad") == 0
    assert seen["stamp"] == "45fb4ad" and f"--add-data={stamp_file}{os.pathsep}listener" in seen["cmd"]
    info = json.loads((tmp_path / "build-info.json").read_text(encoding="utf-8"))
    assert (info["version"], info["product_version"]) == ("0.5.0", "0.5.0+45fb4ad")


def test_the_zip_has_the_version_in_the_name_and_the_folder_at_the_root(tmp_path):
    """D-081 (`--zip`): o que se compartilha é a pasta inteira (o `.exe` sozinho não roda), com o nome dela na raiz."""
    dist = _folder(tmp_path / "dist" / "listener-meet", "novo")
    (dist / "_internal").mkdir()
    (dist / "_internal" / "python312.dll").write_bytes(b"MZ")
    _folder(tmp_path / "dist" / "listener-meet.old", "antigo")                 # a vizinha fica fora
    target = build_exe.make_zip(dist, tmp_path, "0.5.0")
    assert target == tmp_path / "listener-meet-0.5.0-win64.zip"
    with zipfile.ZipFile(target) as archive:
        names = sorted(n for n in archive.namelist() if not n.endswith("/"))
    assert names == ["listener-meet/_internal/python312.dll", "listener-meet/listener-meet.exe"]


def test_the_zip_needs_a_commit_and_refuses_before_building(monkeypatch, capsys):
    """D-081: o zip compartilhado sempre corresponde a um commit; a recusa vem antes dos ~7 min do build."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(build_exe, "build", lambda *_, **__: pytest.fail("não devia construir"))
    for stamp in ("45fb4ad.dirty", "sem-git"):
        monkeypatch.setattr(build_exe, "git_stamp", lambda _root, stamp=stamp: stamp)
        assert build_exe.main(["--zip"]) == 2 and stamp in capsys.readouterr().err


def test_the_probe_errors_carry_no_user_path():
    """R-05: o JSON da sonda é para ser colado: a pasta do `.exe` vira `<app>` e a do usuário `~`, a mais específica antes
    (a do app pode estar dentro da do usuário), sem diferença de maiúsculas."""
    home = r"C:\Users\Fulano"
    app = home + r"\Apps\listener-meet"
    text = (r"ModuleNotFoundError: c:\users\fulano\apps\listener-meet\_internal\x.pyd; "
            r"C:\Users\Fulano\AppData\Local\y; C:\Outro\z")
    assert exe_probe.scrub(text, app, home) == r"ModuleNotFoundError: <app>\_internal\x.pyd; ~\AppData\Local\y; C:\Outro\z"
    assert exe_probe.scrub("sem caminho", "", "") == "sem caminho"
