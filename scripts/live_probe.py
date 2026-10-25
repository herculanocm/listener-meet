"""F4 L0 — medição do ao vivo no Windows (autorizada na aprovação da F4, D-044; spec specs/features/F4-ao-vivo.md).

Roda no venv Python 3.12 do Windows (extra `pipeline` instalado). Não faz parte do produto e não importa nada dele.

  python scripts/live_probe.py whisper AUDIO [--models small,base] [--threads 2] [--offset 0] [--repeat 3] [--language pt]
  python scripts/live_probe.py vad AUDIO [--seconds 120]
  python scripts/live_probe.py torch AUDIO [--repeat 3]
  python scripts/live_probe.py all AUDIO            torch primeiro (RSS limpo), depois vad e whisper; é também a ordem
                                                    do processo ao vivo com nomes (torch e CTranslate2 trazem cada um o
                                                    seu libiomp5md.dll; se aparecer "OMP: Error #15", anote a ordem)

AUDIO: wav/flac/mp3 (lido com soundfile; reamostrado para 16 kHz mono). Use uma gravação real de reunião com fala
logo no começo (ou passe --offset em segundos). Cada medida imprime uma linha JSON; os números vão para
specs/decisoes.md e memory/maquina.md.

whisper  tempo por janela de 3, 5, 8 e 10 s cortadas do AUDIO a partir de --offset, faster-whisper int8 com --threads
         threads (padrão 2, como o `live.threads`), beam 1, sem timestamps, temperatura 0, sem VAD interno; carga do
         modelo; o texto decodificado de cada janela; uma janela de 5 s também com idioma automático (detecção). Baixa o
         modelo para a pasta de modelos se faltar.
vad      Silero v6 do faster-whisper incremental (estado h, c e 64 amostras de contexto carregados entre chamadas da
         sessão ONNX) em blocos de 3 quadros de 512 amostras (~96 ms, o ritmo da captura): vazão em vezes o tempo real,
         custo em % de um núcleo e diferença máxima contra a chamada única.
torch    tempo de `import torch`, RSS antes e depois; carga do wespeaker do pyannote e tempo do embedding por janela de
         5 s com torch.set_num_threads(1), como no processo ao vivo. O token do Hugging Face vem de HF_TOKEN (nunca é
         impresso); sem ele, pula com aviso. O wespeaker em ONNX não é medido (sai "não medido").

Modelos: baixados com `output_dir` (cópia, sem links simbólicos: WinError 1314 sem Modo de Desenvolvedor) para
~/listener-meet/models (ou LISTENER_MODELS_DIR, ou --models-dir), como em scripts/medir.py.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

SR = 16000
WINDOWS_S = (3, 5, 8, 10)
VAD_FRAME = 512
VAD_CONTEXT = 64
VAD_BLOCK_FRAMES = 3                      # ~96 ms por chamada: o ritmo dos blocos da captura
EMBEDDING_REPO = "pyannote/wespeaker-voxceleb-resnet34-LM"


def out(stage: str, **kw) -> None:
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False, default=str), flush=True)


def models_dir(args) -> Path:
    if getattr(args, "models_dir", None):
        return Path(args.models_dir)
    return Path(os.environ.get("LISTENER_MODELS_DIR") or (Path.home() / "listener-meet" / "models"))


def rss_mb() -> float | None:
    """RSS do próprio processo em MB: psapi no Windows (psutil é lento com o EDR), /proc no Linux, pico no macOS."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = (("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t))

            c = Counters()
            c.cb = ctypes.sizeof(c)
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
            return round(c.WorkingSetSize / 1e6, 1) if ok else None
        if sys.platform == "linux":
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            return round(pages * os.sysconf("SC_PAGE_SIZE") / 1e6, 1)
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 1)       # macOS: pico, em bytes
    except Exception:
        return None


def load_audio(path: str):
    """float32 mono a 16 kHz."""
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    audio = data.mean(axis=1)
    if sr != SR:
        from math import gcd

        from scipy.signal import resample_poly
        g = gcd(SR, sr)
        audio = resample_poly(audio, SR // g, sr // g).astype("float32")
    return np.ascontiguousarray(audio, dtype=np.float32)


def disable_hf_symlinks() -> None:
    try:
        import huggingface_hub.file_download as fd
        fd.are_symlinks_supported = lambda *_a, **_k: False
    except Exception as e:
        out("aviso", message=f"não desliguei os symlinks do Hugging Face: {e}")


# ----------------------------------------------------------------------------
def transcribe(model, window, language):
    """Uma janela como o processo ao vivo faz: beam 1, sem timestamps, temperatura 0, sem VAD interno."""
    t0 = time.perf_counter()
    segs, info = model.transcribe(window, language=language, beam_size=1, vad_filter=False, word_timestamps=False,
                                  condition_on_previous_text=False, without_timestamps=True, temperature=0.0)
    segs = list(segs)                                   # o gerador só roda aqui
    return time.perf_counter() - t0, segs, info


def cmd_whisper(args) -> None:
    from faster_whisper import WhisperModel, download_model

    audio = load_audio(args.audio)
    start = int(args.offset * SR)
    need = start + max(WINDOWS_S) * SR
    if len(audio) < need:
        out("whisper", error=f"áudio curto: precisa de {need / SR:.0f} s a partir do começo (tem {len(audio) / SR:.1f} s)")
        return
    out("audio", path=args.audio, seconds=round(len(audio) / SR, 1), offset_s=args.offset)
    language = args.language or None
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        target = models_dir(args) / f"faster-whisper-{model}"
        if not (target / "model.bin").exists():
            disable_hf_symlinks()
            t0 = time.perf_counter()
            download_model(model, output_dir=str(target))
            out("whisper_download", model=model, path=str(target), download_s=round(time.perf_counter() - t0, 1))
        rss0 = rss_mb()
        t0 = time.perf_counter()
        m = WhisperModel(str(target), device="cpu", compute_type="int8", cpu_threads=args.threads, num_workers=1)
        out("whisper_load", model=model, threads=args.threads, load_s=round(time.perf_counter() - t0, 2), rss_before_mb=rss0,
            rss_after_mb=rss_mb())

        for secs in WINDOWS_S:
            window = audio[start:start + secs * SR]
            runs, text = [], ""
            for _ in range(max(1, args.repeat)):
                dt, segs, _info = transcribe(m, window, language)
                runs.append(round(dt, 3))
                text = " ".join(s.text.strip() for s in segs)
            out("whisper_window", model=model, window_s=secs, language=language, runs_s=runs, min_s=min(runs),
                median_s=round(statistics.median(runs), 3), text=text,
                no_speech=[round(s.no_speech_prob, 3) for s in segs], logprob=[round(s.avg_logprob, 3) for s in segs])
        dt, segs, info = transcribe(m, audio[start:start + 5 * SR], None)
        out("whisper_window_detect", model=model, window_s=5, s=round(dt, 3), language=info.language,
            language_probability=round(info.language_probability, 3), text=" ".join(s.text.strip() for s in segs))
        del m


def cmd_vad(args) -> None:
    import numpy as np
    from faster_whisper.vad import get_vad_model

    audio = load_audio(args.audio)[: int(args.seconds * SR)]
    audio = audio[: (len(audio) // VAD_FRAME) * VAD_FRAME]
    model = get_vad_model()
    t0 = time.perf_counter()
    ref = np.asarray(model(audio.copy())).reshape(-1)          # a chamada única zera parte da entrada: cópia
    single_s = time.perf_counter() - t0
    h = np.zeros((1, 1, 128), np.float32)
    c = np.zeros((1, 1, 128), np.float32)
    ctx = np.zeros(VAD_CONTEXT, np.float32)
    probs = []
    step = VAD_FRAME * VAD_BLOCK_FRAMES
    t0 = time.perf_counter()
    for i in range(0, len(audio), step):
        b = audio[i:i + step].reshape(-1, VAD_FRAME)
        contexts = np.concatenate([ctx[None, :], b[:-1, -VAD_CONTEXT:]], 0)
        batch = np.concatenate([contexts, b], 1).astype(np.float32)
        p, h, c = model.session.run(None, {"input": batch, "h": h, "c": c})
        probs.append(np.asarray(p).reshape(-1))
        ctx = b[-1, -VAD_CONTEXT:].copy()
    wall = time.perf_counter() - t0
    inc = np.concatenate(probs)
    secs = len(audio) / SR
    out("vad_incremental", audio_s=round(secs, 1), calls=len(probs), wall_s=round(wall, 3), x_realtime=round(secs / wall, 1),
        core_pct=round(wall / secs * 100, 2), single_call_s=round(single_s, 3),
        # a chamada única zera as 64 últimas amostras da entrada antes de usá-las: o último quadro fica fora da comparação
        max_diff_vs_single=float(np.max(np.abs(ref[: len(inc) - 1] - inc[:-1]))) if len(inc) > 1 else None,
        speech_pct=round(float(np.mean(inc >= 0.5)) * 100, 1) if len(inc) else None)


def cmd_torch(args) -> None:
    rss0 = rss_mb()
    t0 = time.perf_counter()
    try:
        import torch
    except ImportError as e:
        out("torch_import", error=f"torch ausente ({e}): instale o extra pipeline")
        out("wespeaker_onnx", status="não medido")
        return
    import_s = time.perf_counter() - t0
    out("torch_import", version=torch.__version__, import_s=round(import_s, 2), rss_before_mb=rss0, rss_after_mb=rss_mb(),
        threads_default=torch.get_num_threads())
    torch.set_num_threads(1)
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        out("pyannote_embedding", skipped="HF_TOKEN não definido: defina e aceite os termos do wespeaker no Hugging Face para medir")
    else:
        try:
            disable_hf_symlinks()
            from pyannote.audio import Inference, Model

            audio = load_audio(args.audio)
            t0 = time.perf_counter()
            try:
                m = Model.from_pretrained(EMBEDDING_REPO, token=token)
            except TypeError:
                m = Model.from_pretrained(EMBEDDING_REPO, use_auth_token=token)
            inf = Inference(m, window="whole")
            load_s = time.perf_counter() - t0
            runs, dim = [], None
            for i in range(max(1, args.repeat)):
                a = int(i * 5 * SR) % max(1, len(audio) - 5 * SR)
                wave = torch.from_numpy(audio[a:a + 5 * SR].copy())[None, :]
                t1 = time.perf_counter()
                vec = inf({"waveform": wave, "sample_rate": SR})
                runs.append(round(time.perf_counter() - t1, 3))
                dim = getattr(vec, "shape", None)
            out("pyannote_embedding", model=EMBEDDING_REPO, threads=1, load_s=round(load_s, 2), window_s=5, runs_s=runs,
                median_s=round(statistics.median(runs), 3), dim=dim, rss_after_mb=rss_mb())
        except Exception as e:
            out("pyannote_embedding", error=f"{type(e).__name__}: {e}")
    out("wespeaker_onnx", status="não medido")


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("whisper", "vad", "torch", "all"):
        p = sub.add_parser(name)
        p.add_argument("audio")
        p.add_argument("--models", default="small,base")
        p.add_argument("--threads", type=int, default=2)
        p.add_argument("--offset", type=float, default=0.0)
        p.add_argument("--repeat", type=int, default=3)
        p.add_argument("--language", default="pt", help="idioma travado (vazio = detecção a cada janela)")
        p.add_argument("--seconds", type=float, default=120.0)
        p.add_argument("--models-dir", default=None)
    args = ap.parse_args()
    out("env", python=sys.version.split()[0], platform=sys.platform, cores=os.cpu_count())
    if args.cmd in ("torch", "all"):
        cmd_torch(args)
    if args.cmd in ("vad", "all"):
        cmd_vad(args)
    if args.cmd in ("whisper", "all"):
        cmd_whisper(args)


if __name__ == "__main__":
    main()
