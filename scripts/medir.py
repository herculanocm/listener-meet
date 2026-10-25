"""F0 — script de medição descartável (autorizado em 24/10/2026, D-022; spec specs/features/F0-preparacao.md).

Roda no venv Python 3.12 do Windows. Não faz parte do produto.

  python scripts/medir.py env                         versões, CPU, sqlite-vec, alinhamento pt, HF_TOKEN presente?
  python scripts/medir.py models [--model large-v3-turbo]   baixa Whisper e alinhamento pt para a pasta de modelos
  python scripts/medir.py whisperx AUDIO [--minutes N] [--model large-v3-turbo]
  python scripts/medir.py fw AUDIO [--minutes N]            faster-whisper direto, word_timestamps=True (sem wav2vec2)
  python scripts/medir.py pyannote AUDIO [--minutes N]
  python scripts/medir.py all AUDIO [--minutes N]

AUDIO: wav/flac/mp3 (lido com soundfile; reamostrado para 16 kHz mono). --minutes corta o começo do áudio
para uma medição rápida. Cada etapa imprime uma linha JSON; os números vão para specs/decisoes.md e memory/.
O token do Hugging Face vem da variável de ambiente HF_TOKEN (nunca é impresso).

Modelos: baixados com `local_dir` (cópia, sem links simbólicos) para ~/listener-meet/models (ou
LISTENER_MODELS_DIR). O cache padrão do Hugging Face usa symlink e falha no Windows sem Modo de
Desenvolvedor (WinError 1314), medido em 24/10/2026.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ALIGN_PT = "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese"


def models_dir() -> Path:
    return Path(os.environ.get("LISTENER_MODELS_DIR") or (Path.home() / "listener-meet" / "models"))


def hf_sem_symlinks() -> None:
    """Faz o cache do Hugging Face copiar arquivos em vez de criar links simbólicos.

    No Windows sem Modo de Desenvolvedor, os.symlink falha (WinError 1314) e o
    huggingface_hub 0.36 ainda tenta usá-lo. Com isto, qualquer download (pyannote,
    transformers, faster-whisper) cai no caminho de cópia. Chamar antes de baixar.
    """
    try:
        import huggingface_hub.file_download as fd
        fd.are_symlinks_supported = lambda *_a, **_k: False
    except Exception as e:
        print(f"aviso: não desliguei os symlinks do Hugging Face: {e}")


def whisper_path(model: str) -> Path:
    return models_dir() / f"faster-whisper-{model}"


def align_path() -> Path:
    return models_dir() / ALIGN_PT.rsplit("/", 1)[-1]


def out(stage: str, **kw) -> None:
    print(json.dumps({"stage": stage, **kw}, ensure_ascii=False, default=str), flush=True)


def version(mod: str) -> str:
    try:
        m = __import__(mod)
        return getattr(m, "__version__", "?")
    except Exception as e:
        return f"ausente ({type(e).__name__})"


# ----------------------------------------------------------------------------
def cmd_env(_args) -> None:
    out("python", version=sys.version.split()[0], exe=sys.executable, cpu=platform.processor(), cores=os.cpu_count())
    out("versoes", **{m: version(m) for m in ("numpy", "soundfile", "sounddevice", "proctap", "PySide6", "torch",
                                             "torchaudio", "whisperx", "faster_whisper", "ctranslate2",
                                             "pyannote.audio", "sqlite_vec", "google.genai", "openai", "anthropic")})
    try:
        import torch
        out("torch", threads=torch.get_num_threads(), cuda=torch.cuda.is_available())
    except Exception as e:
        out("torch", error=str(e))
    # sqlite-vec
    try:
        import sqlite3
        import sqlite_vec
        db = sqlite3.connect(":memory:")
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        v = db.execute("select vec_version()").fetchone()[0]
        db.execute("create virtual table t using vec0(embedding float[4])")
        db.execute("insert into t(rowid, embedding) values (1, '[1,0,0,0]'), (2, '[0,1,0,0]')")
        r = db.execute("select rowid, distance from t where embedding match '[1,0,0,0]' order by distance limit 1").fetchone()
        out("sqlite_vec", ok=True, version=v, knn_rowid=r[0], sqlite=sqlite3.sqlite_version)
    except Exception as e:
        out("sqlite_vec", ok=False, error=f"{type(e).__name__}: {e}", fallback="BLOB + numpy")
    # alinhamento pt do WhisperX
    try:
        from whisperx.alignment import DEFAULT_ALIGN_MODELS_HF
        out("whisperx_align_pt", model=DEFAULT_ALIGN_MODELS_HF.get("pt"))
    except Exception as e:
        out("whisperx_align_pt", error=str(e))
    out("hf_token", presente=bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")))


# ----------------------------------------------------------------------------
def cmd_models(args) -> None:
    hf_sem_symlinks()
    from faster_whisper import download_model
    from huggingface_hub import snapshot_download

    d = models_dir()
    d.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    wp = download_model(args.model, output_dir=str(whisper_path(args.model)))
    out("model_whisper", model=args.model, path=wp, download_s=round(time.perf_counter() - t0, 1))
    t0 = time.perf_counter()
    ap = snapshot_download(ALIGN_PT, local_dir=str(align_path()),
                           ignore_patterns=["*.h5", "*.msgpack", "*.ot", "*.tflite", "*.onnx"])
    out("model_align_pt", repo=ALIGN_PT, path=ap, download_s=round(time.perf_counter() - t0, 1))


# ----------------------------------------------------------------------------
def load_audio(path: str, minutes: float | None):
    """Devolve (float32 mono 16 kHz, segundos)."""
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    audio = data.mean(axis=1)
    if minutes:
        audio = audio[: int(minutes * 60 * sr)]
    if sr != 16000:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(16000, sr)
        audio = resample_poly(audio, 16000 // g, sr // g).astype("float32")
    return np.ascontiguousarray(audio), len(audio) / 16000.0


def cmd_whisperx(args) -> None:
    import whisperx

    audio, secs = load_audio(args.audio, args.minutes)
    out("audio", path=args.audio, seconds=round(secs, 1))
    model_arg = str(whisper_path(args.model)) if whisper_path(args.model).exists() else args.model
    t0 = time.perf_counter()
    try:
        model = whisperx.load_model(model_arg, "cpu", compute_type="int8", language="pt", vad_method="silero")
    except TypeError:
        model = whisperx.load_model(model_arg, "cpu", compute_type="int8", language="pt")
    t_load = time.perf_counter() - t0
    t0 = time.perf_counter()
    result = model.transcribe(audio, batch_size=args.batch, language="pt")
    t_tr = time.perf_counter() - t0
    segs = result.get("segments", [])
    out("whisperx_transcribe", model=args.model, compute="int8", batch=args.batch, load_s=round(t_load, 1),
        transcribe_s=round(t_tr, 1), rtf=round(t_tr / secs, 3), segments=len(segs),
        amostra=[s.get("text", "")[:80] for s in segs[:3]])
    t0 = time.perf_counter()
    align_kw = {"model_name": str(align_path())} if align_path().exists() else {}
    align_model, meta = whisperx.load_align_model(language_code="pt", device="cpu", **align_kw)
    aligned = whisperx.align(segs, align_model, meta, audio, "cpu", return_char_alignments=False)
    t_al = time.perf_counter() - t0
    words = sum(len(s.get("words", [])) for s in aligned.get("segments", []))
    out("whisperx_align", align_s=round(t_al, 1), rtf=round(t_al / secs, 3), words=words)
    out("whisperx_total", total_s=round(t_tr + t_al, 1), rtf=round((t_tr + t_al) / secs, 3),
        minutos_por_hora_de_audio=round((t_tr + t_al) / secs * 60, 1))


def cmd_fw(args) -> None:
    """Alternativa ao alinhamento wav2vec2: timestamps por palavra do próprio faster-whisper."""
    from faster_whisper import WhisperModel

    audio, secs = load_audio(args.audio, args.minutes)
    out("audio", path=args.audio, seconds=round(secs, 1))
    model_arg = str(whisper_path(args.model)) if whisper_path(args.model).exists() else args.model
    t0 = time.perf_counter()
    model = WhisperModel(model_arg, device="cpu", compute_type="int8")
    t_load = time.perf_counter() - t0
    t0 = time.perf_counter()
    segments, _info = model.transcribe(audio, language="pt", word_timestamps=True, vad_filter=True, beam_size=5)
    segs = list(segments)  # o gerador só roda aqui
    t_tr = time.perf_counter() - t0
    words = sum(len(s.words or []) for s in segs)
    out("fw_word_timestamps", model=args.model, compute="int8", load_s=round(t_load, 1), transcribe_s=round(t_tr, 1),
        rtf=round(t_tr / secs, 3), minutos_por_hora_de_audio=round(t_tr / secs * 60, 1), segments=len(segs), words=words,
        amostra=[s.text[:80] for s in segs[:3]])


# ----------------------------------------------------------------------------
def cmd_pyannote(args) -> None:
    import torch

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        out("pyannote", error="HF_TOKEN não definido no ambiente; defina e aceite os termos dos modelos no Hugging Face")
        return
    hf_sem_symlinks()
    from pyannote.audio import Pipeline

    audio, secs = load_audio(args.audio, args.minutes)
    out("audio", path=args.audio, seconds=round(secs, 1))
    wave = torch.from_numpy(audio)[None, :]
    t0 = time.perf_counter()
    try:
        pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=token)
    except TypeError:
        pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", use_auth_token=token)
    t_load = time.perf_counter() - t0
    t0 = time.perf_counter()
    result = pipe({"waveform": wave, "sample_rate": 16000})
    t_di = time.perf_counter() - t0
    ann = getattr(result, "speaker_diarization", None) or getattr(result, "exclusive_speaker_diarization", None) or result
    try:
        labels = ann.labels()
        speech = sum(seg.duration for seg, _, _ in ann.itertracks(yield_label=True))
    except Exception:
        labels, speech = [], None
    emb = getattr(result, "speaker_embeddings", None)
    out("pyannote_diarize", load_s=round(t_load, 1), diarize_s=round(t_di, 1), rtf=round(t_di / secs, 3),
        minutos_por_hora_de_audio=round(t_di / secs * 60, 1), speakers=len(labels), speech_s=round(speech, 1) if speech else None,
        embeddings_shape=getattr(emb, "shape", None), result_type=type(result).__name__)
    # Embedding por trecho (base dos perfis de voz): 10 s do começo
    try:
        from pyannote.audio import Inference, Model
        t0 = time.perf_counter()
        try:
            m = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM", token=token)
        except TypeError:
            m = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM", use_auth_token=token)
        inf = Inference(m, window="whole")
        t_load = time.perf_counter() - t0
        t0 = time.perf_counter()
        vec = inf({"waveform": wave[:, : 16000 * 10], "sample_rate": 16000})
        t_emb = time.perf_counter() - t0
        out("pyannote_embedding", model="wespeaker-voxceleb-resnet34-LM", load_s=round(t_load, 1),
            embed_10s_s=round(t_emb, 2), dim=getattr(vec, "shape", None))
    except Exception as e:
        out("pyannote_embedding", error=f"{type(e).__name__}: {e}")


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("env").set_defaults(fn=cmd_env)
    pm = sub.add_parser("models"); pm.add_argument("--model", default="large-v3-turbo"); pm.set_defaults(fn=cmd_models)
    for name in ("whisperx", "fw", "pyannote", "all"):
        p = sub.add_parser(name)
        p.add_argument("audio")
        p.add_argument("--minutes", type=float, default=None)
        p.add_argument("--model", default="large-v3-turbo")
        p.add_argument("--batch", type=int, default=8)
        p.set_defaults(fn={"whisperx": cmd_whisperx, "fw": cmd_fw, "pyannote": cmd_pyannote}.get(name))
    args = ap.parse_args()
    if args.cmd == "all":
        cmd_env(args)
        cmd_whisperx(args)
        cmd_pyannote(args)
    else:
        args.fn(args)


if __name__ == "__main__":
    main()
