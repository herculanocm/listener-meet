"""F5.18 lote 3, PF-11: o teto de tokens do Whisper ao vivo (`live/pacing.py`): a fórmula pela duração da janela, a conta dos
tokens do prompt como o faster-whisper a faz (texto cortado a 223 mais os de controle), o cache, o máximo assumido sem o
tokenizer (R-01) e o `WhisperLive` passando `max_new_tokens` a um modelo falso e marcando as janelas que bateram no teto
(`stats.capped`), sem carregar o faster-whisper. Com o `tokenizer.json` do `small` já baixado nesta máquina (só o
`tokenizers`), a conta real do prompt máximo em 25 s fica dentro dos 448 do modelo: o `ValueError` do
`generate_with_fallback` nunca dispara."""
from __future__ import annotations

import io
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from listener.live import pacing, proc
from listener.live import protocol as pr
from listener.live.client import live_models_dir, whisper_model_dir
from listener.live.vad import SAMPLE_RATE
from tests.fake_live import FakeEngines, tone

SR = SAMPLE_RATE
TOKENIZER_JSON = whisper_model_dir(live_models_dir(""), "small") / "tokenizer.json"


def _words(n: int) -> str:
    return " ".join(f"palavra{i}" for i in range(n))


def _prompt_len(text_tokens: int) -> int:
    """O prompt que `WhisperModel.get_prompt` monta (faster-whisper 1.2.1): <|startofprev|>, as últimas 223 posições do texto
    do `initial_prompt`, <|startoftranscript|>, idioma, tarefa e <|notimestamps|> (`without_timestamps=True`); sem texto, só
    os quatro últimos. O `generate_with_fallback` recusa `len(prompt) + max_new_tokens > 448`."""
    return 1 + min(text_tokens, 223) + 4 if text_tokens else 4


class _Encoder:
    """Dois tokens por palavra, contando as chamadas (o cache evita repetir); `fail` simula um tokenizer quebrado."""

    def __init__(self, fail: bool = False):
        self.calls = 0
        self.fail = fail

    def __call__(self, text: str) -> list[int]:
        self.calls += 1
        if self.fail:
            raise ValueError("tokenizer quebrado")
        return [1] * (2 * len(text.split()))


class _FakeModel:
    """Um `WhisperModel` de mentira: guarda os argumentos de cada `transcribe` e devolve um segmento com `tokens` ids (None:
    segmento sem o atributo); o `hf_tokenizer` dá dois tokens por palavra, como o `_Encoder`; sem ele, o `WhisperLive`
    assume o máximo."""

    def __init__(self, tokenizer: bool = True, tokens: int | None = 5):
        self.calls: list[dict] = []
        self.tokens = tokens
        if tokenizer:
            self.hf_tokenizer = SimpleNamespace(
                encode=lambda text, add_special_tokens: SimpleNamespace(ids=[1] * (2 * len(text.split()) if not add_special_tokens else 0)))

    def transcribe(self, audio, **kw):
        self.calls.append({"n": len(audio), **kw})
        seg = SimpleNamespace(text=" fala ", no_speech_prob=0.1, avg_logprob=-0.2, compression_ratio=1.1)
        if self.tokens is not None:
            seg.tokens = list(range(self.tokens))
        return iter([seg]), SimpleNamespace(language="pt", language_probability=0.9)


def test_token_budget_follows_the_window_length_and_the_prompt_ceiling():
    assert [pacing.token_budget(s, 0) for s in (2, 10, 25)] == [52, 132, 282]
    assert pacing.token_budget(2.1, 0) == 62 and pacing.token_budget(0, 0) == 32            # segundos arredondados para cima
    assert pacing.token_budget(25, 225) == 215                                              # 448 - 225 - 8
    assert pacing.token_budget(25, pacing.PROMPT_TOKENS_MAX) == 212                         # o pior prompt: 448 - 228 - 8
    assert pacing.token_budget(10, pacing.PROMPT_TOKENS_MAX) == 132                         # a janela de 10 s não sente o teto
    assert pacing.token_budget(25, 440) == 1 and pacing.token_budget(25, 500) == 1           # nunca abaixo de 1
    assert pacing.token_budget(-3, -5) == 32                                                # entradas estranhas não quebram


def test_prompt_tokens_count_like_faster_whisper_cache_the_text_and_assume_the_maximum_without_a_tokenizer(caplog):
    enc = _Encoder()
    count = pacing.PromptTokens(enc)
    assert count("") == 4 and count("   ") == 4 and enc.calls == 0                          # sem prompt: sem <|startofprev|>
    assert count(_words(70)) == 2 * 70 + 5                                                   # o texto mais os 5 de controle
    assert count(" " + _words(70) + " ") == 145 and enc.calls == 1                           # o mesmo texto: do cache
    assert count(_words(300)) == 223 + 5 == pacing.PROMPT_TOKENS_MAX                          # o faster-whisper corta a 223
    for i in range(pacing.CACHE_MAX + 1):                                                     # cache cheio: esvazia e segue
        count(f"prompt {i}")
    assert count("prompt 0") == 2 * 2 + 5 and enc.calls == 2 + (pacing.CACHE_MAX + 1) + 1
    with caplog.at_level(logging.DEBUG, logger="listener.live"):
        guess = pacing.PromptTokens(None)                                                     # R-01: sem tokenizer, o máximo
        assert guess(_words(70)) == guess(_words(3)) == guess("12 345 6789") == 228 and guess("") == 4
        broken = pacing.PromptTokens(_Encoder(fail=True))                                     # tokenizer que lança: idem
        assert broken(_words(10)) == 228 and broken(_words(11)) == 228
    notes = [r.getMessage() for r in caplog.records if "tokens do prompt assumidos no máximo" in r.getMessage()]
    assert len(notes) == 2 and "sem o tokenizer" in notes[0] and "ValueError" in notes[1]    # uma linha por contador
    assert "palavra0" not in caplog.text and "prompt 0" not in caplog.text                    # D-059: nada do prompt no log


def test_from_hf_uses_the_model_tokenizer_or_the_maximum_without_one():
    class Hf:
        def encode(self, text, add_special_tokens=True):
            assert add_special_tokens is False and text.startswith(" ")
            return SimpleNamespace(ids=list(range(3 * len(text.split()))))

    assert pacing.PromptTokens.from_hf(Hf())(_words(10)) == 30 + 5
    assert pacing.PromptTokens.from_hf(None)(_words(10)) == pacing.PROMPT_TOKENS_MAX
    assert pacing.PromptTokens.from_hf(object())(_words(1)) == pacing.PROMPT_TOKENS_MAX


def test_whisper_live_passes_the_token_ceiling_to_the_model_and_never_exceeds_the_model_length(caplog):
    model = _FakeModel()
    live = proc.WhisperLive(Path("nenhum"), "small", 2, engine=model)
    assert live.name == "faster-whisper:small:int8" and live.model == "small"
    glossary = proc.build_prompt(["SRM", "FIDC", "Cotas"], "")
    for seconds in (2, 10, 25):
        out = live.transcribe(np.zeros(seconds * SR, np.float32), language="pt", prompt=glossary)
        assert [s.text for s in out.segments] == [" fala "] and out.language == "pt" and out.language_prob == 0.9 and not out.capped
    assert [c["max_new_tokens"] for c in model.calls] == [52, 132, 282]                    # 3 palavras: longe do teto
    first = model.calls[0]
    assert first["initial_prompt"] == glossary and first["beam_size"] == 1 and first["without_timestamps"] and first["n"] == 2 * SR
    longest = proc.build_prompt([f"termo{i}" for i in range(100)], _words(100))             # 40 + 30 palavras: o prompt máximo
    live.transcribe(np.zeros(25 * SR, np.float32), language="pt", prompt=longest)
    assert len(longest.split()) == 70 and model.calls[-1]["max_new_tokens"] == pacing.token_budget(25, 2 * 70 + 5) == 282
    live.transcribe(np.zeros(25 * SR, np.float32), language="pt", prompt=_words(300))       # 600 tokens: o corte a 223 e o teto
    budget = model.calls[-1]["max_new_tokens"]
    assert budget == 448 - 228 - 8 == 212 and _prompt_len(600) + budget <= 448
    live.transcribe(np.zeros(SR // 2, np.float32), language="pt", prompt="")
    assert model.calls[-1]["initial_prompt"] is None and model.calls[-1]["max_new_tokens"] == 42
    for words in (0, 1, 10, 70, 150, 300):                                                   # a regra do faster-whisper, sempre
        for seconds in (1, 5, 10, 25, 30):
            live.transcribe(np.zeros(seconds * SR, np.float32), language="pt", prompt=_words(words))
            assert _prompt_len(2 * words) + model.calls[-1]["max_new_tokens"] <= pacing.MAX_LENGTH
    model.tokens = 52                                                                         # 2 s: teto 52, e a janela o gastou todo
    assert live.transcribe(np.zeros(2 * SR, np.float32), language="pt", prompt=glossary).capped is True
    model.tokens = 51
    assert live.transcribe(np.zeros(2 * SR, np.float32), language="pt", prompt=glossary).capped is False
    model.tokens = None                                                                       # segmento sem `tokens`: nunca conta
    assert live.transcribe(np.zeros(2 * SR, np.float32), language="pt", prompt=glossary).capped is False
    guess_model = _FakeModel(tokenizer=False)                                                 # sem tokenizer: o máximo (R-01)
    with caplog.at_level(logging.DEBUG, logger="listener.live"):
        proc.WhisperLive(Path("nenhum"), "small", 2, engine=guess_model).transcribe(np.zeros(25 * SR, np.float32), language="pt",
                                                                                   prompt=longest)
    assert guess_model.calls[-1]["max_new_tokens"] == 448 - 228 - 8 == 212
    assert sum("tokens do prompt assumidos no máximo" in r.getMessage() for r in caplog.records) == 1


def test_capped_windows_are_counted_in_the_live_summary(store):
    """`stats.capped` (sugestão da revisão): quantas janelas bateram no teto de tokens, no `live_stopped` e no `bye`."""
    mid = store.create_meeting("teto", None, None, None)
    for capped, expected in ((True, 2), (False, 0)):
        engines = FakeEngines(capped=capped)
        session = proc.LiveSession(io.BytesIO(b""), io.BytesIO(), store, mid, engines, clock=lambda: 1001.0)
        session.hello = pr.Hello(1000.0, names=False)
        session.transcriber = engines.transcriber("small", 2)
        for i in range(2):
            session.queue.put(proc.LiveWindow("R", i * SR, (i + 1) * SR, SR, tone(1.0)))
        session.queue.close()
        session._loop()
        assert session.stats.capped == expected and session.stats.as_dict(0)["capped"] == expected and session.stats.captions == 2


@pytest.fixture
def forget_tokenizers():
    """O `tokenizers` (e os submódulos) sai do `sys.modules` ao fim: a regra dura (test_runner) confere que o app não o puxa;
    `live/pacing.py` só recebe o `hf_tokenizer` do modelo e nunca o importa."""
    yield
    for name in [m for m in sys.modules if m == "tokenizers" or m.startswith("tokenizers.")]:
        sys.modules.pop(name, None)


@pytest.mark.skipif(not TOKENIZER_JSON.exists(), reason="o tokenizer do small só existe com os modelos do ao vivo baixados")
def test_the_real_small_tokenizer_fits_the_maximal_prompt_in_a_25_s_window(forget_tokenizers):
    """Com o `tokenizer.json` do `small` desta máquina (só o `tokenizers`, nunca o faster-whisper): o prompt máximo do ao vivo
    (40 palavras de glossário e 30 de contexto, jargão longo) e o teto de 25 s cabem nos 448 do modelo, e o teto nunca
    desce de 212 tokens novos (448 - 228 - 8)."""
    import tokenizers

    hf = tokenizers.Tokenizer.from_file(str(TOKENIZER_JSON))
    count = pacing.PromptTokens.from_hf(hf)
    terms = ["Desconsolidação", "Securitização", "Subordinação", "FIDC", "Sênior", "Cedente", "Sacado", "Inadimplência",
             "Provisionamento", "Recompra", "Enquadramento", "Mezanino", "Cessão", "Lastro", "Duplicata", "Pré-pagamento",
             "Amortização", "Benchmark", "Spread", "Waterfall", "Covenants", "Diligência", "Compliance", "Rating", "Custodiante",
             "Administradora", "Gestora", "Consultoria", "Assembleia", "Regulamento", "Suplemento", "Prospecto", "Debênture",
             "Escrituração", "Liquidação", "Conciliação", "Rentabilidade", "Volatilidade", "Alavancagem", "Reestruturação",
             "Garantia", "Fiança"]
    previous = " ".join(["inadimplência", "securitização", "desconsolidação", "provisionamento", "reestruturação"] * 6)
    prompt = proc.build_prompt(terms, previous)
    assert len(prompt.split()) == proc.PROMPT_GLOSSARY_WORDS + proc.PROMPT_PREVIOUS_WORDS
    raw = len(hf.encode(" " + prompt.strip(), add_special_tokens=False).ids)
    n = count(prompt)
    assert n == min(raw, pacing.PROMPT_TEXT_MAX) + pacing.PROMPT_CONTROL_TOKENS
    budget = pacing.token_budget(25, n)
    assert _prompt_len(raw) + budget <= pacing.MAX_LENGTH and 212 <= budget <= 282
