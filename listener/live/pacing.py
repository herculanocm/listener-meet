"""O ritmo de uma janela do ao vivo (F5.18, lote 3), puro e sem os modelos: o teto de tokens do Whisper (PF-11) e a conta
dos tokens que o faster-whisper gasta com o `initial_prompt`.

PF-11: sem `max_new_tokens`, uma janela alucinando decodifica até os 448 tokens do modelo (`WhisperModel.max_length`); o
teto é `32 + 10 x ceil(segundos da janela)`, limitado a `448 - tokens do prompt - 8` (o faster-whisper levanta `ValueError`
quando prompt mais `max_new_tokens` passam de 448 [fato: `transcribe.py::generate_with_fallback`]), nunca abaixo de 1.

Tokens do prompt, a mesma conta do faster-whisper 1.2.1: o texto vira `tokenizer.encode(" " + prompt.strip())`
(`WhisperModel.transcribe`), entra no prompt cortado às últimas `448 // 2 - 1 = 223` posições depois de `<|startofprev|>`
(`get_prompt`), e seguem `<|startoftranscript|>`, o idioma, a tarefa e `<|notimestamps|>` (`sot_sequence` com
`without_timestamps=True`): 5 tokens de controle com texto, 4 sem ele. Sem o tokenizer do modelo (ou com ele falhando), o
prompt conta como o corte inteiro, 223 + 5 = 228 tokens (revisão R-01: o glossário com acentos dá 4,1 tokens por palavra e
os números 9,7; uma conta por palavra estourava os 448): o teto fica em 212 em 25 s e a janela de 10 s (teto 142) não sente;
uma linha DEBUG uma vez. Nada do texto do prompt vai ao log (D-059)."""
from __future__ import annotations

import math
from collections.abc import Callable, Sized
from typing import Any

from ..log import get_logger

log = get_logger("live")

MAX_LENGTH = 448                             # `WhisperModel.max_length`: prompt mais tokens novos
TOKENS_BASE = 32                             # tokens novos de uma janela curta
TOKENS_PER_S = 10                            # mais estes por segundo de janela (a fala cabe em 3 a 5 por segundo [exp])
TOKEN_SLACK = 8                              # folga sobre a conta dos tokens do prompt
PROMPT_TEXT_MAX = MAX_LENGTH // 2 - 1        # `get_prompt`: só as últimas 223 posições do texto entram
PROMPT_CONTROL_TOKENS = 5                    # <|startofprev|>, <|startoftranscript|>, idioma, tarefa, <|notimestamps|>
PROMPT_TOKENS_MAX = PROMPT_TEXT_MAX + PROMPT_CONTROL_TOKENS     # 228: o que um prompt com texto pode custar, no máximo
CACHE_MAX = 64                               # prompts guardados (o prompt muda a cada janela pelas 30 palavras anteriores)


def token_budget(seconds: float, prompt_tokens: int) -> int:
    """`max_new_tokens` de uma janela: `min(32 + 10 x ceil(s), 448 - tokens do prompt - 8)`, nunca abaixo de 1."""
    by_length = TOKENS_BASE + TOKENS_PER_S * math.ceil(max(0.0, float(seconds)))
    return max(1, min(by_length, MAX_LENGTH - max(0, int(prompt_tokens)) - TOKEN_SLACK))


class PromptTokens:
    """Quantos tokens o faster-whisper gasta com um `initial_prompt`: o texto cortado a `PROMPT_TEXT_MAX` mais os de
    controle, num cache pequeno por texto. `encode` é o do tokenizer do modelo (devolve a lista de ids) ou None, e então
    todo prompt com texto conta `PROMPT_TOKENS_MAX`; um tokenizer que falha passa a contar assim dali em diante."""

    def __init__(self, encode: Callable[[str], Sized] | None):
        self._encode = encode
        self._cache: dict[str, int] = {}
        self._noted = False

    @classmethod
    def from_hf(cls, tokenizer: Any) -> PromptTokens:
        """Do `hf_tokenizer` do `WhisperModel` (um `tokenizers.Tokenizer`: `encode(texto, add_special_tokens=False).ids`);
        sem ele, o máximo."""
        encode = getattr(tokenizer, "encode", None)
        if not callable(encode):
            return cls(None)
        return cls(lambda text: encode(text, add_special_tokens=False).ids)

    def __call__(self, prompt: str) -> int:
        text = prompt.strip()
        if not text:
            return PROMPT_CONTROL_TOKENS - 1                 # sem `initial_prompt`, o <|startofprev|> não entra
        n = self._cache.get(text)
        if n is None:
            if len(self._cache) >= CACHE_MAX:
                self._cache.clear()
            n = self._cache[text] = min(self._count(text), PROMPT_TEXT_MAX) + PROMPT_CONTROL_TOKENS
        return n

    def _count(self, text: str) -> int:
        if self._encode is not None:
            try:
                n = len(self._encode(" " + text))            # a mesma entrada de `WhisperModel.transcribe`
            except Exception as e:                           # tokenizer estranho: o máximo daqui em diante
                self._encode = None
                self._note(f"tokenizer do modelo falhou ({type(e).__name__})")
            else:
                return n
        self._note("sem o tokenizer do modelo")
        return PROMPT_TEXT_MAX

    def _note(self, why: str) -> None:
        if not self._noted:
            self._noted = True
            log.debug("%s: tokens do prompt assumidos no máximo (%d: o texto cortado a %d mais %d de controle)", why, PROMPT_TOKENS_MAX,
                      PROMPT_TEXT_MAX, PROMPT_CONTROL_TOKENS)


__all__ = ["MAX_LENGTH", "PROMPT_CONTROL_TOKENS", "PROMPT_TEXT_MAX", "PROMPT_TOKENS_MAX", "TOKEN_SLACK", "PromptTokens", "token_budget"]
