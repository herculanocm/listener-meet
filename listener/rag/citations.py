"""Citações do chat sobre todas as reuniões (F5 item 11, F5A-24), puro: generaliza o `[mm:ss]` do chat de uma reunião
(`llm/chat_task.py`) por apelido de reunião. A resposta cita `[R1 12:03]`; aceita `[R1]` sem tempo (liga só à reunião),
várias num colchete separadas por `;` ou `,` (`[R1 12:03; R2 05:10]`, `[R1 12:03, 15:20]`), `h:mm:ss`, traço ou vírgula
entre o apelido e o tempo e o intervalo `12:03-12:40` (vale o começo). O tempo só casa com as linhas ENVIADAS daquela
reunião, até 5 s (a mais próxima): casou, vira o link `listener://open?m=<reunião>&t=<ms>&seg=<segmento>` (o tempo navega; o
segmento é só dica, F5A-06) com um texto legível (título curto, data dd/mm e mm:ss); não casou, fica o texto legível sem
link; apelido desconhecido fica como veio. Nada some. F5.6 (P-02): a reunião excluída no Histórico vira "reunião excluída",
sem link, no redesenho da conversa e na cópia (o histórico do prompt segue só com o tempo). F5.10: o apelido aceita até 6
dígitos, porque "Perguntar à base" cita a reunião pelo id (`[R<reuniao_id> mm:ss]`, D5).

`rewrite` devolve o HTML e as citações `[{alias, meeting_id, t_ms, segment_id, cited_ms}]` que `chat_messages` guarda (`t_ms`
é o da linha casada; nulo = sem link de tempo; `cited_ms` é o que a LLM escreveu). `render_saved` redesenha a conversa a
partir delas, sem recasar segmentos, com o título atual da reunião; `history_text` é a mesma resposta para o histórico do
prompt, com as citações legíveis entre parênteses e as não resolvidas sem o apelido (R6-05)."""
from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..llm.prompts.common import stamp

MAX_DISTANCE_MS = 5000
TITLE_CHARS = 32
GONE_LABEL = "reunião excluída"      # F5.6 (P-02): a citação de uma reunião que não existe mais (o piso nunca reaproveita o id)
SCHEME = "listener"
BRACKET_RE = re.compile(r"\[\s*([Rr]\d{1,6}\b[^\[\]\n]{0,160}?)\s*\]")
TOKEN_RE = re.compile(r"\s*(?:(?P<alias>[Rr]\d{1,6})\b|(?P<time>(?:\d{1,2}:)?\d{1,3}:\d{2})\b|(?P<sep>[;,])|(?P<dash>[-\u2013\u2014]))\s*")

type Citation = dict[str, Any]


@dataclass(frozen=True, slots=True)
class CitedItem:
    alias: str                   # "R1"
    cited_ms: int | None         # o tempo escrito pela LLM; None = `[R1]`

    @property
    def raw(self) -> str:
        return self.alias if self.cited_ms is None else f"{self.alias} {stamp(self.cited_ms)}"


@dataclass(frozen=True, slots=True)
class SentLine:
    """Uma linha enviada à LLM com tempo: fala (com o segmento) ou item de ata e ação (sem segmento)."""
    t_ms: int
    segment_id: int | None = None


@dataclass(frozen=True, slots=True)
class Target:
    """O que um apelido quer dizer nesta pergunta: a reunião e as linhas enviadas dela."""
    meeting_id: int
    title: str
    date: str                    # AAAA-MM-DD
    lines: tuple[SentLine, ...] = ()

    @property
    def label(self) -> str:
        return meeting_label(self.title, self.date)


@dataclass(frozen=True, slots=True)
class OpenLink:
    meeting_id: int
    t_ms: int | None = None
    segment_id: int | None = None


def short_title(title: str | None, limit: int = TITLE_CHARS) -> str:
    text = " ".join((title or "").split()) or "Reunião sem título"
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return (text[:cut] if cut > limit // 2 else text[:limit - 1]).rstrip(" ,;:.-\u2013\u2014") + "…"


def meeting_label(title: str | None, date: str | None) -> str:
    """"Planejamento Q4, 24/10": título curto e data dd/mm (da `started_at`)."""
    d = (date or "")[:10]
    ddmm = f"{d[8:10]}/{d[5:7]}" if len(d) == 10 else ""
    return f"{short_title(title)}, {ddmm}" if ddmm else short_title(title)


def open_link(meeting_id: int, t_ms: int | None = None, segment_id: int | None = None) -> str:
    """`listener://open?m=<reunião>&t=<ms>&seg=<segmento>` (o L4 abre a reunião, rola até a fala e toca)."""
    link = f"{SCHEME}://open?m={int(meeting_id)}"
    if t_ms is not None:
        link += f"&t={int(t_ms)}"
    if segment_id is not None:
        link += f"&seg={int(segment_id)}"
    return link


def parse_open_link(url: str) -> OpenLink | None:
    """Inverso de `open_link` (aceita o `&amp;` do HTML); None para outro link."""
    parts = urlsplit(url.replace("&amp;", "&"))
    if parts.scheme != SCHEME or parts.netloc != "open":
        return None
    qs = parse_qs(parts.query)

    def num(name: str) -> int | None:
        try:
            return int(qs[name][0])
        except (KeyError, IndexError, ValueError):
            return None

    mid = num("m")
    return OpenLink(mid, num("t"), num("seg")) if mid is not None else None


def to_ms(text: str) -> int:
    """"12:03" ou "1:02:03" -> milissegundos."""
    parts = [int(p) for p in text.split(":")]
    h, m, s = ([0, *parts] if len(parts) == 2 else parts)[-3:]
    return (h * 3600 + m * 60 + s) * 1000


def parse_bracket(inner: str) -> list[CitedItem] | None:
    """O conteúdo de um colchete que começa com um apelido -> as citações; None quando sobra algo que não é apelido, tempo,
    `;`, `,` ou traço (o colchete fica como texto)."""
    items: list[list[Any]] = []
    alias: str | None = None
    prev = prev2 = ""
    pos = 0
    while pos < len(inner):
        m = TOKEN_RE.match(inner, pos)
        if m is None or m.end() == pos:
            return None
        pos = m.end()
        if m["alias"]:
            alias = f"R{int(m['alias'][1:])}"
            items.append([alias, None])
            kind = "alias"
        elif m["time"]:
            kind = "time"
            if prev == "dash" and prev2 == "time":
                pass                                     # fim do intervalo "12:03-12:40": vale o começo
            elif alias is None:
                return None
            elif items[-1][1] is None:
                items[-1][1] = to_ms(m["time"])
            else:
                items.append([alias, to_ms(m["time"])])  # "[R1 12:03, 15:20]": o mesmo apelido
        else:
            kind = "sep" if m["sep"] else "dash"
        prev2, prev = prev, kind
    return [CitedItem(a, t) for a, t in items] or None


def nearest(t_ms: int, lines: Sequence[SentLine], max_distance_ms: int = MAX_DISTANCE_MS) -> SentLine | None:
    """A linha enviada mais próxima do tempo citado (a primeira, no empate), até `max_distance_ms`."""
    best: tuple[int, SentLine] | None = None
    for ln in lines:
        d = abs(ln.t_ms - t_ms)
        if best is None or d < best[0]:
            best = (d, ln)
    return best[1] if best is not None and best[0] <= max_distance_ms else None


# (texto, link ou None, citação) de cada citação de um colchete; texto vazio = a citação não aparece
type Resolve = Callable[[CitedItem], tuple[str, str | None, Citation]]


def _apply(text: str, resolve: Resolve, as_html: bool, wrap: tuple[str, str] = ("[", "]")) -> tuple[str, list[Citation]]:
    def esc(s: str) -> str:
        return html.escape(s) if as_html else s

    out: list[str] = []
    cites: list[Citation] = []
    pos = 0
    for m in BRACKET_RE.finditer(text):
        items = parse_bracket(m.group(1))
        if not items:
            continue
        pieces: list[str] = []
        for item in items:
            label, href, cite = resolve(item)
            cites.append(cite)
            if label:
                pieces.append(f'<a href="{html.escape(href)}">{html.escape(label)}</a>' if as_html and href else esc(label))
        out.append(esc(text[pos:m.start()]))
        if pieces:
            out.append(wrap[0] + "; ".join(pieces) + wrap[1])
        pos = m.end()
    out.append(esc(text[pos:]))
    joined = "".join(out)
    return (joined.replace("\n", "<br>") if as_html else joined), cites


def citation(alias: str, meeting_id: int | None, t_ms: int | None, segment_id: int | None, cited_ms: int | None) -> Citation:
    return {"alias": alias, "meeting_id": meeting_id, "t_ms": t_ms, "segment_id": segment_id, "cited_ms": cited_ms}


def rewrite(text: str, targets: Mapping[str, Target], *, as_html: bool = True) -> tuple[str, list[Citation]]:
    """A resposta da LLM com cada citação ligada (HTML escapado, `\\n` -> `<br>`) e as citações em ordem."""
    def resolve(item: CitedItem) -> tuple[str, str | None, Citation]:
        target = targets.get(item.alias)
        if target is None:                               # apelido que a pergunta não tinha: fica como veio
            return item.raw, None, citation(item.alias, None, None, None, item.cited_ms)
        mid = target.meeting_id
        if item.cited_ms is None:
            return target.label, open_link(mid), citation(item.alias, mid, None, None, None)
        line = nearest(item.cited_ms, target.lines)
        if line is None:                                 # tempo fora das linhas enviadas: texto legível, sem link
            return f"{target.label}, {stamp(item.cited_ms)}", None, citation(item.alias, mid, None, None, item.cited_ms)
        return (f"{target.label}, {stamp(line.t_ms)}", open_link(mid, line.t_ms, line.segment_id),
                citation(item.alias, mid, line.t_ms, line.segment_id, item.cited_ms))

    return _apply(text, resolve, as_html)


type SavedResolve = Callable[[CitedItem], tuple[str | None, str | None, Citation]]


def _saved(saved: Sequence[Mapping[str, Any]], meetings: Mapping[int, tuple[str | None, str | None]]) -> SavedResolve:
    """A i-ésima citação do texto com a i-ésima gravada, se o apelido confere: (texto legível com o título e a data atuais,
    ou None quando não resolve: apelido desconhecido, reunião apagada ou citação que não confere; link; citação)."""
    seq = list(saved)
    count = 0

    def resolve(item: CitedItem) -> tuple[str | None, str | None, Citation]:
        nonlocal count
        cite = dict(seq[count]) if count < len(seq) else {}
        count += 1
        mid = cite.get("meeting_id") if cite.get("alias") == item.alias else None
        info = meetings.get(int(mid)) if mid is not None else None
        if mid is None or info is None:
            return None, None, cite
        label = meeting_label(*info)
        t_ms = cite.get("t_ms")
        if t_ms is not None:
            seg = cite.get("segment_id")
            return f"{label}, {stamp(int(t_ms))}", open_link(int(mid), int(t_ms), int(seg) if seg is not None else None), cite
        if item.cited_ms is None:
            return label, open_link(int(mid)), cite
        return f"{label}, {stamp(item.cited_ms)}", None, cite

    return resolve


def render_saved(text: str, saved: Sequence[Mapping[str, Any]], meetings: Mapping[int, tuple[str | None, str | None]], *,
                 as_html: bool = True) -> str:
    """Redesenha uma resposta guardada a partir das citações gravadas, sem recasar segmentos, com o título e a data atuais
    (`meetings`: id -> (título, AAAA-MM-DD)); a citação que não confere fica como veio e a da reunião que não está em
    `meetings` (excluída) vira "reunião excluída", com o tempo e sem link (F5.6, P-02)."""
    base = _saved(saved, meetings)

    def resolve(item: CitedItem) -> tuple[str, str | None, Citation]:
        label, href, cite = base(item)
        if label is None and cite.get("alias") == item.alias and cite.get("meeting_id") is not None:
            t = cite.get("t_ms") if cite.get("t_ms") is not None else item.cited_ms
            label = GONE_LABEL if t is None else f"{GONE_LABEL}, {stamp(int(t))}"
        return (label if label is not None else item.raw), href, cite

    return _apply(text, resolve, as_html)[0]


def history_text(text: str, saved: Sequence[Mapping[str, Any]], meetings: Mapping[int, tuple[str | None, str | None]]) -> str:
    """A resposta guardada para o histórico do prompt (R6-05): as citações resolvidas legíveis entre parênteses ("(Orçamento
    2027, 24/10, 12:03)": um formato que a LLM não confunde com o `[R1 12:03]` das linhas novas e que o `BRACKET_RE` não lê) e
    as não resolvidas sem o apelido (só o tempo, quando havia): um apelido velho poderia casar com outra reunião na pergunta
    seguinte."""
    base = _saved(saved, meetings)

    def resolve(item: CitedItem) -> tuple[str, str | None, Citation]:
        label, _href, cite = base(item)
        if label is None:
            label = stamp(item.cited_ms) if item.cited_ms is not None else ""
        return label, None, cite

    return _apply(text, resolve, False, ("(", ")"))[0]


def saved_meeting_ids(saved: Sequence[Mapping[str, Any]]) -> set[int]:
    return {int(c["meeting_id"]) for c in saved if c.get("meeting_id") is not None}


__all__ = ["GONE_LABEL", "MAX_DISTANCE_MS", "CitedItem", "OpenLink", "SentLine", "Target", "history_text", "meeting_label", "nearest",
           "open_link", "parse_bracket", "parse_open_link", "render_saved", "rewrite", "saved_meeting_ids", "short_title", "to_ms"]
