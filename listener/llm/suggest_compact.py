"""F5.20 (C6, pergunta 14): a compactação local das legendas de uma sugestão ao vivo, pura (sem Qt, sem banco, sem SDK):
menos caracteres para a LLM sem nenhuma chamada a mais. Em cinco passos, nesta ordem:

1. o texto com espaços simples; o vazio (ou só pontuação) fora;
2. as interjeições curtas de uma lista fechada fora ("uhum", "ok", "tá bom"); nunca "sim", "não" nem números;
3. a repetição exata em até 10 s fora, do mesmo falante (a alucinação repetida do Whisper) ou entre o L e o R (o eco),
   nunca "sim", "não" nem um número (R-04: dois participantes concordando ficam os dois);
4. cada fala em até 300 caracteres, cortada no último espaço, com "…";
5. as falas seguidas do mesmo falante e canal, até 60 s depois do começo da linha, numa linha só com o id da primeira,
   enquanto couberem em 300 caracteres (uma linha nova leva o id dela: a citação segue precisa).

As comparações dos passos 2 e 3 usam `evidence.normalize` (sem acento, pontuação nem caixa): o Whisper põe ponto final e
maiúscula ("Ok."). Os ids de todas as legendas mantidas, também as juntadas, ficam citáveis. A medida (os caracteres crus e os
compactados das legendas que ficaram na janela) vai à sugestão, ao `--check` e ao log. Na sala presencial (R-06), o L é de
quem estiver falando na sala: sem rótulo, "Participante", como no R. `first_name` e `mentions` servem ao "falaram com você"
(U-160, A-22), sem banco."""
from __future__ import annotations

import re
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .evidence import normalize
from .prompts.common import Line

INTERJECTIONS = frozenset({"uhum", "aham", "hum", "hmm", "ok", "okay", "tá", "ta", "tá bom", "certo", "entendi", "beleza", "legal",
                           "oi", "alô", "olá", "bom dia", "boa tarde", "boa noite"})
INTERJECTION_MAX = 12            # só a fala curta sai: "Certo, mas o prazo é sexta" fica
DUP_WINDOW_MS = 10_000
MERGE_GAP_MS = 60_000
LINE_MAX = 300
PARTICIPANT = "Participante"     # o rótulo do filho abaixo do limiar (`live/names.py`)
ELLIPSIS = "…"
MIN_NAME = 3                     # "falaram com você": nomes com menos letras acendem à toa
_INTERJECTION_KEYS = frozenset(normalize(x) for x in INTERJECTIONS)
_KEEP_KEYS = frozenset({"sim", "nao"})        # R-04: nunca saem como repetição (nem os números)


@dataclass(frozen=True, slots=True)
class Compacted:
    lines: tuple[Line, ...] = ()
    ids: frozenset[int] = frozenset()          # todas as legendas mantidas, também as juntadas (a LLM pode citar qualquer uma)
    raw_chars: int = 0                          # as legendas cruas da janela que ficou (depois do `trim`)
    input_chars: int = 0                        # o texto das linhas enviadas
    until_caption_id: int = 0                   # o maior id lido (todas as legendas da entrada)
    t_until_ms: int = 0                         # o maior fim de legenda da entrada
    line_ids: tuple[tuple[int, ...], ...] = ()  # os ids de cada linha (o `trim` tira junto)
    raws: tuple[tuple[int, int], ...] = ()      # (início, caracteres crus) de cada legenda com texto, para a medida do `trim`


def speaker(row: Mapping[str, Any], me_label: str, in_room: bool = False) -> str:
    """O rótulo da legenda; sem ele (nomes desligados), quem pede no canal L e "Participante" no R (a regra do parcial); na sala
    presencial, o L sem rótulo também é "Participante" (R-06: ali o L é a sala, como no `you_label` da tela)."""
    label = str(row.get("label") or "").strip()
    return label or (me_label if row.get("channel") == "L" and not in_room else PARTICIPANT)


def _repeat(key: str, who: str, channel: str, recent: deque[tuple[int, str, str, str]]) -> bool:
    """R-04: a mesma fala de novo em 10 s, do mesmo falante (alucinação) ou do outro canal (eco); "sim", "não" e números nunca."""
    if key in _KEEP_KEYS or key.replace(" ", "").isdigit():
        return False
    return any(k == key and (w == who or c != channel) for _t, k, w, c in recent)


def clip(text: str, limit: int = LINE_MAX) -> str:
    """Até `limit` caracteres, cortado no último espaço antes do limite, com "…"."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip() + ELLIPSIS


def compact(rows: Sequence[Mapping[str, Any]], me_label: str, in_room: bool = False) -> Compacted:
    """As legendas em ordem de tempo (`t_start_ms`, `id`) viram as linhas do pedido, nos cinco passos do módulo."""
    if not rows:
        return Compacted()
    until_id = t_until = 0
    raws: list[tuple[int, int]] = []
    kept: list[tuple[Mapping[str, Any], str]] = []
    recent: deque[tuple[int, str, str, str]] = deque()      # (início, chave, falante, canal) das mantidas nos últimos 10 s
    for row in rows:
        t0 = int(row.get("t_start_ms") or 0)
        until_id = max(until_id, int(row.get("id") or 0))
        t_until = max(t_until, int(row.get("t_end_ms") or t0))
        text = " ".join(str(row.get("text") or "").split())
        key = normalize(text)
        if not key:
            continue                                # passo 1: vazio ou só pontuação
        raws.append((t0, len(text)))
        if len(text) < INTERJECTION_MAX and key in _INTERJECTION_KEYS:
            continue                                # passo 2
        who, channel = speaker(row, me_label, in_room), str(row.get("channel") or "")
        while recent and t0 - recent[0][0] > DUP_WINDOW_MS:
            recent.popleft()
        if _repeat(key, who, channel, recent):
            continue                                # passo 3
        recent.append((t0, key, who, channel))
        kept.append((row, clip(text)))              # passo 4
    lines: list[Line] = []
    groups: list[list[int]] = []
    heads: list[tuple[str, str, int]] = []          # (falante, canal, início da primeira) de cada linha
    for row, text in kept:                          # passo 5
        who, channel = speaker(row, me_label, in_room), str(row.get("channel") or "")
        t0, rid = int(row.get("t_start_ms") or 0), int(row["id"])
        if lines:
            last, (s, c, first) = lines[-1], heads[-1]
            joined = f"{last.text} {text}"
            if s == who and c == channel and t0 - first <= MERGE_GAP_MS and len(joined) <= LINE_MAX:
                lines[-1] = Line(last.segment_id, last.t_start_ms, last.speaker, joined)
                groups[-1].append(rid)
                continue
        lines.append(Line(rid, t0, who, text))
        groups.append([rid])
        heads.append((who, channel, t0))
    return Compacted(tuple(lines), frozenset(i for g in groups for i in g), sum(n for _t, n in raws), sum(len(ln.text) for ln in lines),
                     until_id, t_until, tuple(tuple(g) for g in groups), tuple(raws))


def trim(c: Compacted, max_chars: int) -> Compacted:
    """Tira linhas do começo (as mais antigas) até caber em `max_chars`, mantendo ao menos uma; os ids e a medida crua passam a
    ser só os da janela que ficou."""
    if c.input_chars <= max_chars or len(c.lines) <= 1:
        return c
    lines, groups, total = list(c.lines), list(c.line_ids), c.input_chars
    while len(lines) > 1 and total > max_chars:
        total -= len(lines.pop(0).text)
        groups.pop(0)
    start = lines[0].t_start_ms
    return replace(c, lines=tuple(lines), ids=frozenset(i for g in groups for i in g), input_chars=total, line_ids=tuple(groups),
                   raw_chars=sum(n for t, n in c.raws if t >= start))


def first_name(name: str) -> str:
    """A primeira palavra do nome, sem pontuação ("Carla Dias" -> "Carla")."""
    words = re.findall(r"\w+", name or "")
    return words[0] if words else ""


def mentions(text: str, first: str) -> bool:
    """O primeiro nome como palavra inteira na legenda, sem acento nem caixa; nunca com menos de 3 letras."""
    key = normalize(first)
    if len(key) < MIN_NAME:
        return False
    return re.search(rf"\b{re.escape(key)}\b", normalize(text)) is not None


__all__ = ["DUP_WINDOW_MS", "ELLIPSIS", "INTERJECTIONS", "INTERJECTION_MAX", "LINE_MAX", "MERGE_GAP_MS", "PARTICIPANT", "Compacted",
           "clip", "compact", "first_name", "mentions", "speaker", "trim"]
