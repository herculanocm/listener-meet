"""Carga dos modelos como o worker (`pipeline/pyannote.py`, `pipeline/whisper.py`) e o filho ao vivo (`live/proc.py`) fazem:
torch importado antes do CTranslate2 em todo processo com os dois (o `libiomp5md.dll` em dobro, maquina.md), a pasta de
modelos do app e o token do banco real. Também o glossário de teste e o prompt do runner (`runner.build_prompt`), e os
filtros de segmento do adaptador (`no_speech_prob`, `avg_logprob`)."""
from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import numpy as np

NO_SPEECH_MAX, LOGPROB_MIN = 0.6, -1.0                 # os mesmos de `pipeline/whisper.py`
# ~20 termos genéricos de reunião corporativa (nenhum nome de pessoa, D-059) e ~60 palavras de contexto neutras
TEST_GLOSSARY = ("fundo de investimento, cota sênior, cota subordinada, cedente, sacado, duplicata, recebível, spread, taxa DI, "
                 "custodiante, administrador fiduciário, gestor, auditoria, compliance, due diligence, FIDC, CVM, regulamento, "
                 "assembleia de cotistas, provisão para devedores duvidosos")
CONTEXT_TEXT = ("então a gente fechou o relatório mensal e a carteira ficou dentro do limite previsto no regulamento do fundo "
                "o comitê aprovou a proposta com duas ressalvas sobre o prazo de liquidação e a taxa de administração "
                "e o próximo passo é revisar a minuta do contrato com o jurídico antes da assembleia marcada para a semana que vem")


def import_torch(threads: int | None = None) -> Any:
    """`import torch` primeiro (antes do faster-whisper) e as threads do torch; devolve o módulo e o tempo da importação."""
    t0 = time.perf_counter()
    import torch

    if threads is not None:
        torch.set_num_threads(max(1, int(threads)))
    return torch, round(time.perf_counter() - t0, 2)


def diarizer(token: str, threads: int) -> tuple[Any, float]:
    """`PyannoteDiarizer` carregado (community-1 em CPU) e os segundos da carga."""
    from listener.pipeline.pyannote import PyannoteDiarizer

    d = PyannoteDiarizer(token, torch_threads=threads)
    t0 = time.perf_counter()
    d._load()
    return d, round(time.perf_counter() - t0, 2)


def embedder(token: str, threads: int) -> tuple[Any, float]:
    """`PyannoteEmbedder` (wespeaker, `Inference(window="whole")`) carregado e os segundos da carga."""
    from listener.pipeline.pyannote import PyannoteEmbedder

    e = PyannoteEmbedder(token, torch_threads=threads)
    t0 = time.perf_counter()
    e._load()
    return e, round(time.perf_counter() - t0, 2)


def transcriber(config: Any, threads: int) -> tuple[Any, float]:
    """`FasterWhisperTranscriber` (large-v3-turbo int8, como o runner) carregado da pasta de modelos e os segundos da carga."""
    from listener.pipeline.models import whisper_dir
    from listener.pipeline.whisper import FasterWhisperTranscriber

    t = FasterWhisperTranscriber(whisper_dir(config), config.whisper_model, cpu_threads=threads)
    t0 = time.perf_counter()
    t._load()
    return t, round(time.perf_counter() - t0, 2)


def live_whisper(config: Any, model: str, threads: int) -> tuple[Any, float]:
    """O `WhisperLive` do filho ao vivo (`small` int8, beam 1, sem timestamps) e os segundos da carga."""
    from listener.live.client import live_models_dir, whisper_model_dir
    from listener.live.proc import WhisperLive

    path = whisper_model_dir(live_models_dir(config.models_dir), model)
    if not (path / "model.bin").exists():
        raise SystemExit(f"modelo {model} do ao vivo ausente na pasta de modelos: prepare nas Configurações (Ao vivo)")
    t0 = time.perf_counter()
    w = WhisperLive(path, model, threads)
    return w, round(time.perf_counter() - t0, 2)


def live_prompt(previous: str = "") -> str:
    from listener.live.proc import build_prompt, glossary_terms

    return build_prompt(glossary_terms(TEST_GLOSSARY), previous)


def runner_prompt(with_context: bool) -> str:
    """O prompt do runner: glossário de teste (~20 termos) mais, com `with_context`, as 60 palavras de contexto."""
    from listener.pipeline.runner import build_prompt
    from listener.pipeline.types import Word

    previous = tuple(Word(i, i + 1, w) for i, w in enumerate(CONTEXT_TEXT.split())) if with_context else ()
    return build_prompt(TEST_GLOSSARY, None, None, previous)


def keep_segment(seg: Any) -> bool:
    """Os filtros do adaptador: `no_speech_prob > 0,6` ou `avg_logprob < -1,0` descartam o segmento."""
    return not (float(getattr(seg, "no_speech_prob", 0.0)) > NO_SPEECH_MAX or float(getattr(seg, "avg_logprob", 0.0)) < LOGPROB_MIN)


def segment_words(segments: Sequence[Any]) -> tuple[list[tuple[int, int, str]], int]:
    """(início ms, fim ms, texto) das palavras dos segmentos que passam nos filtros e a fala em ms (soma dos segmentos)."""
    words: list[tuple[int, int, str]] = []
    speech_ms = 0
    for seg in segments:
        if not keep_segment(seg):
            continue
        speech_ms += max(0, round((float(seg.end) - float(seg.start)) * 1000))
        for w in getattr(seg, "words", None) or ():
            text = str(w.word).strip()
            if text:
                words.append((round(float(w.start) * 1000), round(float(w.end) * 1000), text))
    return words, speech_ms


def window_words(tw: Any) -> list[tuple[int, int, str]]:
    """As palavras de um `TranscriptWindow` do adaptador, no mesmo formato."""
    return [(int(w.start_ms), int(w.end_ms), str(w.text)) for w in tw.words]


def normalized(words: Sequence[tuple[int, int, str]]) -> list[str]:
    """Palavras sem caixa nem pontuação, para o diff (a pontuação muda com o prompt e não é o que se mede)."""
    import re
    import unicodedata

    out: list[str] = []
    for _a, _b, text in words:
        plain = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
        plain = re.sub(r"[^\w]", "", plain)
        if plain:
            out.append(plain)
    return out


def compare_words(base: Sequence[tuple[int, int, str]], other: Sequence[tuple[int, int, str]]) -> dict[str, Any]:
    """Contra a referência: razão de palavras, `SequenceMatcher.ratio()` sobre as palavras normalizadas e os deltas dos
    tempos de início das palavras casadas (blocos iguais do SequenceMatcher), em ms."""
    import difflib

    from .common import stats

    a, b = normalized(base), normalized(other)
    kept_a = [w for w in base if normalized([w])]
    kept_b = [w for w in other if normalized([w])]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    deltas = [float(kept_b[j + k][0] - kept_a[i + k][0]) for i, j, n in sm.get_matching_blocks() for k in range(n)]
    abs_deltas = [abs(d) for d in deltas]
    return {"words_ratio": round(len(b) / len(a), 4) if a else None, "text_ratio": round(sm.ratio(), 4), "matched_words": len(deltas),
            "start_delta_ms": stats(abs_deltas, 1), "start_bias_ms": round(float(np.mean(deltas)), 1) if deltas else None}
