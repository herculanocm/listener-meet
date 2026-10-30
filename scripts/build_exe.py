"""Empacota o listener-meet num executável Windows em PASTA (F5.8, D-069): PyInstaller `--onedir --windowed`, sem console.
Nunca arquivo único: ele se descompacta a cada abertura e o app abre mais devagar.

  .venv\\Scripts\\python.exe scripts\\build_exe.py         ~7 min, sem rede: build\\exe\\dist\\listener-meet\\listener-meet.exe

Antes, uma vez (e de novo sempre que o lock do app mudar): `.venv\\Scripts\\python.exe -m pip install -r
requirements/build-windows.txt` (só de build, D-068; o app nunca importa o PyInstaller).

- Prioridade "abaixo do normal" para o próprio processo (`get_platform().lower_own_priority()`): o PyInstaller herda.
- Recusa em duas camadas antes de mexer em qualquer coisa: o `.exe` antigo aberto para escrita dá erro de compartilhamento
  se o app ou um worker estiver vivo; depois, a pasta antiga vira `listener-meet.old`, apagada quando o build sai certo e
  devolvida quando ele falha ou cai no meio (exceção, Ctrl+C). Apagar é sempre renomear antes: com algo aberto dentro, a
  pasta fica inteira, com um aviso. Um `.old` que sobrou de um build morto volta sozinho se for a única pasta; com uma pasta
  nova ao lado, o build recusa e a escolha é do dono.
- O lançador, gerado em `build/exe/launcher/`, chama `listener.__main__.main`; `--probe` roda `scripts/exe_probe.py`.
- Leva o que a análise do PyInstaller não vê (medido em 30/10): os submódulos de `listener`, os dados dos ícones, do VAD
  do faster-whisper, do pyannote.audio e do lightning, os pacotes inteiros `av`, `filelock` e `pyannote.audio`, o
  `truststore` e a metadata de cada distribuição que o app carrega; os fakes (`tests`) ficam fora.
- Modo UTF-8 no executável (`--python-option X utf8`): o Python embutido ignora o `PYTHONUTF8` dos filhos.
- Ícone (U-095): um ICO com PNGs de 16 a 256 px da marca da bandeja no estado ocioso, desenhados num subprocesso Qt
  offscreen. Versão do arquivo: `listener.__version__`.
- `build/exe/build-info.json`: tempo, tamanho, número de arquivos, as versões do PyInstaller e do hooks-contrib e os
  módulos que a sondagem da metadata não importou (também no stderr, só os nomes).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from importlib import metadata
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "build" / "exe"
NAME = "listener-meet"
EXE = f"{NAME}.exe"
DIST = OUT / "dist" / NAME
REFUSAL = ("feche o listener-meet; se já fechou, um processamento em segundo plano ainda roda: espere ou encerre pelo "
           "Gerenciador de Tarefas")
INTERRUPTED = ("sobrou listener-meet.old de um build anterior ao lado de listener-meet: se esse build terminou certo, a "
               "boa é listener-meet; se ele foi interrompido, a boa é a .old. Apague a que não vale e rode de novo")
# as distribuições do log de versões (log.py, worker.py, live/proc.py), além das que a sondagem achar carregadas
METADATA = ("numpy", "scipy", "soundfile", "sounddevice", "proc-tap", "PySide6", "PyAudioWPatch", "pycaw", "faster-whisper",
            "ctranslate2", "onnxruntime", "torch", "torchaudio", "pyannote-audio", "huggingface_hub", "google-genai", "openai",
            "anthropic", "truststore", "httpx", "tokenizers", "lightning", "torchmetrics")
METADATA_SKIP = frozenset({"listener-meet"})       # a do próprio app (instalado editável) traria o caminho do repositório
# dados lidos em tempo de execução: os ícones, o VAD (onnx) do faster-whisper, a configuração da telemetria do pyannote
# (lida na importação) e o version.info do lightning
DATA_PACKAGES = ("listener.ui.icons", "faster_whisper", "pyannote.audio", "lightning")
# importações que a análise estática não vê: o PyAV importa submódulos Cython de dentro do C (`av._cyutil`); o filelock 4
# importa por nome num `__getattr__` preguiçoso (`filelock._soft`); o pyannote.audio acha as classes pelo nome do YAML
WHOLE_PACKAGES = ("av", "filelock", "pyannote.audio")
HIDDEN = ("truststore", "exe_probe")               # o truststore entra por nome (llm/registry.py); a sonda pelo lançador
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
# o Python embutido ignora o `PYTHONUTF8` do `worker_env` (medido em 30/10: `utf8_mode` 0 no filho): o modo UTF-8 vai no
# próprio executável, para o app e para todos os filhos, como os filhos do venv já rodam
UTF8_OPTION = "X utf8"

CLOSURE_PROBE = """
import importlib, json, pkgutil, sys
import listener
failed = []
for m in pkgutil.walk_packages(listener.__path__, "listener.", onerror=failed.append):
    if not m.name.endswith("__main__"):
        try:
            importlib.import_module(m.name)
        except Exception:
            failed.append(m.name)
for name in ("torch", "pyannote.audio", "faster_whisper", "ctranslate2", "onnxruntime", "google.genai", "openai", "anthropic"):
    try:
        importlib.import_module(name)
    except Exception:
        failed.append(name)
from importlib.metadata import packages_distributions
dists = packages_distributions()
loaded = sorted({d for m in list(sys.modules) for d in dists.get(m.split(".")[0], [])})
print(json.dumps({"dists": loaded, "failed": sorted(set(failed))}))
"""
ICON_PROBE = """
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
from listener.model import AppState
from listener.ui.tray import icon_pixmap
out = Path(sys.argv[1])
for size in map(int, sys.argv[2:]):
    if not icon_pixmap(AppState.IDLE, size).save(str(out / f"{size}.png"), "PNG"):
        sys.exit(f"não salvei o ícone de {size} px")
"""
LAUNCHER = '''"""Entrada do listener-meet.exe, gerada por scripts/build_exe.py (F5.8): o mesmo despacho de `python -m listener`;
`--probe` roda a sonda do build (scripts/exe_probe.py)."""
import sys

if "--multiprocessing-fork" in sys.argv:    # filho do multiprocessing (o app não usa hoje): o freeze_support assume e sai
    import multiprocessing

    multiprocessing.freeze_support()
if sys.argv[1:2] == ["--probe"]:
    import exe_probe

    sys.exit(exe_probe.main(sys.argv[2:]))
from listener.__main__ import main

sys.exit(main())
'''
VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}),
  kids=[StringFileInfo([StringTable('041604B0', [
    StringStruct('FileDescription', '{name}'), StringStruct('ProductName', '{name}'),
    StringStruct('FileVersion', '{s}'), StringStruct('ProductVersion', '{s}'),
    StringStruct('InternalName', '{name}'), StringStruct('OriginalFilename', '{exe}')])]),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])])
"""


# ---------------------------------------------------------------- partes puras (testadas sem empacotar)
def version_tuple(version: str) -> tuple[int, int, int, int]:
    """As partes inteiras da versão, completadas com zeros até quatro ("0.4.0" -> (0, 4, 0, 0); "0.5.0rc1" -> (0, 5, 0, 0))."""
    numbers: list[int] = []
    for part in version.split("."):
        match = re.match(r"\d+", part)
        if match is None:
            break
        numbers.append(int(match.group()))
    a, b, c, d = [*numbers, 0, 0, 0, 0][:4]
    return a, b, c, d


def ico_bytes(pngs: Mapping[int, bytes]) -> bytes:
    """Um ICO com um PNG por tamanho (o formato que o Windows aceita do Vista em diante): o diretório de 6 bytes, uma
    entrada de 16 por imagem (256 px vira 0 na largura e na altura) e os PNGs em seguida, do menor ao maior."""
    sizes = sorted(pngs)
    offset = 6 + 16 * len(sizes)
    entries: list[bytes] = []
    for size in sizes:
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(pngs[size]), offset))
        offset += len(pngs[size])
    return struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + b"".join(pngs[s] for s in sizes)


def pyinstaller_command(launcher: Path, icon: Path, version_file: Path, dists: Sequence[str]) -> list[str]:
    """O comando do PyInstaller, em pasta e sem console."""
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", f"--name={NAME}",
           f"--distpath={OUT / 'dist'}", f"--workpath={OUT / 'work'}", f"--specpath={OUT}", f"--paths={ROOT}",
           f"--paths={ROOT / 'scripts'}", f"--icon={icon}", f"--version-file={version_file}", "--collect-submodules=listener",
           "--exclude-module=tests", f"--python-option={UTF8_OPTION}", "--log-level=WARN"]
    cmd += [f"--collect-data={p}" for p in DATA_PACKAGES]
    cmd += [f"--collect-submodules={p}" for p in WHOLE_PACKAGES]
    cmd += [f"--hidden-import={m}" for m in HIDDEN]
    cmd += [f"--copy-metadata={d}" for d in dists]
    return [*cmd, str(launcher)]


def check_not_running(dist: Path, opener: Callable[..., Any] = open) -> str | None:
    """Camada 1: o `.exe` de um app ou worker vivo não abre para escrita (erro de compartilhamento). None = pode seguir."""
    exe = dist / EXE
    if not exe.exists():
        return None
    try:
        with opener(exe, "r+b"):
            pass
    except OSError:
        return REFUSAL
    return None


class InterruptedBuildError(Exception):
    """Sobrou o `.old` de um build anterior junto com uma pasta nova (o build caiu, ou terminou certo sem conseguir apagar
    o `.old`): só o dono sabe qual das duas vale."""


def move_aside(dist: Path) -> Path | None:
    """Camada 2: a pasta antiga vira `.old` (renomear falha se algo dela estiver aberto: o OSError sobe e quem chama
    recusa). Um `.old` que já existe é de um build interrompido e nunca sai sem decidir qual vale: sozinho, ele é a pasta
    boa e volta antes; junto com uma pasta nova, a escolha é do dono (`InterruptedBuildError`). None sem pasta antiga."""
    old = dist.with_name(dist.name + ".old")
    if old.exists():
        if dist.exists():
            raise InterruptedBuildError(INTERRUPTED)
        old.rename(dist)
    if not dist.exists():
        return None
    dist.rename(old)
    return old


def discard(path: Path) -> None:
    """Tira uma pasta do caminho sem nunca deixar metade dela: primeiro a renomeação, que falha (OSError) com algo aberto lá
    dentro e deixa a pasta inteira; só então o apagar, num nome que nada mais abre (a sobra de uma vez anterior sai antes)."""
    doomed = path.with_name(path.name + ".apagar")
    if doomed.exists():
        shutil.rmtree(doomed, ignore_errors=True)
    path.rename(doomed)
    shutil.rmtree(doomed, ignore_errors=True)


def settle(dist: Path, old: Path | None, ok: bool) -> str | None:
    """Depois do build, também quando ele cai (exceção, Ctrl+C: roda no `finally`): certo, o `.old` sai; com falha, a pasta
    nova (incompleta) sai e a antiga volta. Devolve um aviso quando não deu (algo aberto numa delas), com as pastas inteiras
    onde ficaram."""
    if old is None:
        return None
    try:
        if ok:
            discard(old)
            return None
        if dist.exists():
            discard(dist)
        old.rename(dist)
    except OSError:
        where = "a pasta antiga ficou em" if ok else "a pasta antiga não voltou; está em"
        return f"{where} {old.name} (algo aberto): feche e apague ou renomeie à mão"
    return None


# ---------------------------------------------------------------- etapas com efeito
def subprocess_env() -> dict[str, str]:
    return dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM="offscreen", PYANNOTE_METRICS_ENABLED="0",
                HF_HUB_OFFLINE="1", PYTHONUTF8="1")


def loaded_distributions() -> tuple[list[str], list[str]]:
    """`METADATA` mais as distribuições que um processo que importa o app inteiro e a pilha pesada deixou carregadas, e os
    módulos que ele não conseguiu importar (só os nomes: a metadata deles pode faltar no build)."""
    done = subprocess.run([sys.executable, "-c", CLOSURE_PROBE], env=subprocess_env(), capture_output=True,  # noqa: S603
                          text=True, encoding="utf-8", check=True)
    probe = json.loads(done.stdout.strip().splitlines()[-1])
    return sorted((set(METADATA) | set(probe["dists"])) - METADATA_SKIP), list(probe["failed"])


def write_icon(path: Path) -> None:
    """Os PNGs da marca desenhados num subprocesso Qt offscreen (o build não abre janela) e juntados num ICO."""
    with tempfile.TemporaryDirectory(prefix="listener-icon-") as tmp:
        subprocess.run([sys.executable, "-c", ICON_PROBE, tmp, *map(str, ICON_SIZES)], env=subprocess_env(),  # noqa: S603
                       check=True)
        pngs = {size: (Path(tmp) / f"{size}.png").read_bytes() for size in ICON_SIZES}
    path.write_bytes(ico_bytes(pngs))


class Prepared(NamedTuple):
    launcher: Path
    icon: Path
    version_file: Path
    dists: list[str]
    failed: list[str]                                  # os módulos que a sondagem da metadata não importou


def prepare(version: str) -> Prepared:
    """O lançador, o ícone, o arquivo de versão e a lista da metadata, todos em `build/exe/`."""
    (OUT / "launcher").mkdir(parents=True, exist_ok=True)
    launcher = OUT / "launcher" / "listener_meet.py"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    icon = OUT / f"{NAME}.ico"
    write_icon(icon)
    version_file = OUT / "version-info.txt"
    version_file.write_text(VERSION_INFO.format(v=version_tuple(version), s=version, name=NAME, exe=EXE), encoding="utf-8")
    dists, failed = loaded_distributions()
    if failed:
        print(f"build_exe: a sondagem da metadata não importou: {', '.join(failed)}", file=sys.stderr)
    return Prepared(launcher, icon, version_file, dists, failed)


def folder_stats(folder: Path) -> dict[str, float]:
    files = [p for p in folder.rglob("*") if p.is_file()]
    return {"files": len(files), "mb": round(sum(p.stat().st_size for p in files) / 1024 / 1024, 1)}


def write_build_info(out: Path, dist: Path, code: int, seconds: float, version: str, failed: list[str]) -> dict[str, Any]:
    info: dict[str, Any] = {"exit": code, "seconds": round(seconds, 1), "version": version,
                            "pyinstaller": metadata.version("pyinstaller"),
                            "hooks_contrib": metadata.version("pyinstaller-hooks-contrib"), "python": sys.version.split()[0],
                            "metadata_probe_failed": failed, **(folder_stats(dist) if code == 0 and dist.exists() else {})}
    (out / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def run_pyinstaller(cmd: list[str]) -> int:
    return subprocess.run(cmd, cwd=str(ROOT), check=False).returncode  # noqa: S603


def refuse(message: str) -> int:
    print(f"build_exe: {message}", file=sys.stderr)
    return 2


def build(version: str, dist: Path = DIST, out: Path = OUT, run: Callable[[list[str]], int] = run_pyinstaller) -> int:
    """Recusa antes de mexer, troca a pasta de lado, prepara e roda o PyInstaller; a pasta antiga se acerta no `finally`,
    também se o build cair no meio (exceção, Ctrl+C)."""
    refusal = check_not_running(dist)
    if refusal:
        return refuse(refusal)
    try:
        old = move_aside(dist)
    except InterruptedBuildError as e:
        return refuse(str(e))
    except OSError:
        return refuse(REFUSAL)
    t0 = time.perf_counter()
    code, ok, failed = 1, False, []
    try:
        prepared = prepare(version)
        failed = prepared.failed
        code = run(pyinstaller_command(prepared.launcher, prepared.icon, prepared.version_file, prepared.dists))
        ok = code == 0 and (dist / EXE).exists()
    finally:
        warning = settle(dist, old, ok)
        if warning:
            print(f"build_exe: {warning}", file=sys.stderr)
    result = 0 if ok else (code or 1)
    print(json.dumps(write_build_info(out, dist, result, time.perf_counter() - t0, version, failed), ensure_ascii=False))
    return result


def main() -> int:
    for stream in (sys.stdout, sys.stderr):              # mensagens em PT-BR também com a saída num pipe (cp1252 por padrão)
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    if sys.platform != "win32":
        print("build_exe: o executável é só para o Windows (F5.8; Linux e macOS ficam para a F7)", file=sys.stderr)
        return 2
    from listener import __version__
    from listener.platform import get_platform

    get_platform().lower_own_priority()
    OUT.mkdir(parents=True, exist_ok=True)
    return build(__version__)


if __name__ == "__main__":
    sys.exit(main())
