"""Trechos do índice (F5 item 1), puro: a reunião (montada por `rag.index.gather`) vira trechos por tipo, determinísticos
pelo `text_hash`. Nada de banco nem de rede aqui.

- `transcricao`: as falas da leitura (`review.transcript_rows`, revisada quando existe, "Nome — Cargo" do dia, D-016), uma
  linha por turno ("Nome — Cargo: texto"; falas seguidas da mesma pessoa juntas). O trecho fecha em fim de turno quando passa
  de 60 s e fecha mesmo no meio do turno (entre falas) antes de passar de 120 s ou de 1.400 caracteres; uma fala sozinha
  maior que o corpo é cortada em fim de frase, com o tempo pelas palavras (`words_json`) ou proporcional. Sem sobreposição.
- `decisao`, `destaque`, `pendencia`, `proximo_passo`: cada item de lista da ata exibida (o Markdown de `render_minutes` ou a
  edição do usuário), pelo título da seção nos `HEADINGS` de `llm/render.py`, no tempo do `[mm:ss]` que o render põe no fim
  (sem carimbo, sem tempo). A seção Ações fica de fora (as ações vêm de `action_items`); o contexto e qualquer seção
  desconhecida viram `ata`.
- `acao`: as ações vigentes (abertas ou feitas) da ata atual, com dono e prazo, no tempo da evidência.
- `executivo`: o resumo executivo atual, sem tempo. `reuniao`: título, data, participantes e duração, sem tempo.

`text` guarda o corpo; o embedding recebe o cabeçalho ("título · AAAA-MM-DD", até 200 caracteres) mais o corpo
(`embed_input`). O `text_hash` cobre o que entra no FTS e no embedding: tipo, título, data, falantes e texto. Tempos,
segmentos, pessoas e job ficam fora: o diff os troca nos trechos mantidos sem perder os vetores (F5A-06)."""
from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..llm.render import HEADINGS
from ..model import ChunkKind

BODY_MAX = 1400                  # caracteres do corpo (e5: 512 tokens com o cabeçalho, F5A-23)
HEADER_MAX = 200
TURN_CLOSE_MS = 60_000           # em fim de turno, fecha a partir daqui
HARD_CLOSE_MS = 120_000          # no meio do turno (entre falas), não passa daqui
MIN_PIECE = 200                  # uma fala longa nunca vira pedaços menores que isto por causa de um rótulo comprido
SEP = "\x1f"

SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")
STAMP_RE = re.compile(r"\s*\[(?:(\d{1,2}):)?(\d{1,3}):(\d{2})\]\s*$")
NO_EVIDENCE_RE = re.compile(r"\s*_?\(\s*(?:" + "|".join(re.escape(h[8]) for h in HEADINGS.values()) + r")\s*\)_?\s*$",
                            re.IGNORECASE)                                  # a marca em qualquer idioma da ata (F5.3 A-07)
LIST_RE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+(.*)$")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
SECTION_KINDS: tuple[ChunkKind | None, ...] = (ChunkKind.MINUTES, ChunkKind.DECISION, None, ChunkKind.HIGHLIGHT, ChunkKind.PENDING,
                                               ChunkKind.NEXT_STEP)      # posição nos HEADINGS; None = Ações (de `action_items`)
ACTION_WORDS = (HEADINGS["pt"][6], HEADINGS["pt"][7])                    # "dono", "prazo"


@dataclass(frozen=True, slots=True)
class Utterance:
    """Uma fala como a leitura mostra (`TranscriptRow`)."""
    segment_id: int
    t_start_ms: int
    t_end_ms: int
    speaker: str                                  # "Nome — Cargo", "Nome" ou "Voz SPK_1"
    person_id: int | None
    text: str
    words: tuple[tuple[int, int, str], ...] = ()  # (início, fim, palavra) do `words_json`: só para cortar fala longa


@dataclass(frozen=True, slots=True)
class ActionInput:
    text: str
    owner: str | None                             # nome da pessoa casada ou o texto livre do dono
    owner_person_id: int | None
    due: str | None
    t_ms: int | None
    segment_id: int | None


@dataclass(frozen=True, slots=True)
class MeetingInput:
    title: str                                    # o do usuário, senão a sugestão da LLM, senão vazio
    date: str                                     # AAAA-MM-DD de `started_at`
    duration_ms: int | None
    participants: tuple[str, ...] = ()            # rótulos de quem fala (ou os esperados, sem transcrição)
    participant_ids: tuple[int, ...] = ()
    utterances: tuple[Utterance, ...] = ()
    minutes_md: str = ""                          # ata exibida: `text_edited`, senão `text_llm`
    actions: tuple[ActionInput, ...] = ()
    executive: str = ""


@dataclass(frozen=True, slots=True)
class Chunk:
    kind: ChunkKind
    t_start_ms: int | None
    t_end_ms: int | None
    seg_first: int | None
    seg_last: int | None
    title: str
    speakers: str
    person_ids: tuple[int, ...]
    text: str
    text_hash: str


# ---------------------------------------------------------------- utilidades
def header(title: str, date: str) -> str:
    """"Título · AAAA-MM-DD" cortado em 200 caracteres (sem título, só a data)."""
    title = " ".join((title or "").split())
    return (f"{title} · {date}" if title else date)[:HEADER_MAX]


def embed_input(title: str, date: str, text: str) -> str:
    """O que vai ao modelo de embeddings: cabeçalho e corpo."""
    return f"{header(title, date)}\n{text}"


def text_hash(kind: str, title: str, date: str, speakers: str, text: str) -> str:
    return hashlib.sha1(SEP.join((str(kind), title, date, speakers, text)).encode("utf-8")).hexdigest()  # noqa: S324


def clean(text: str) -> str:
    return " ".join((text or "").split())


def split_text(text: str, limit: int = BODY_MAX) -> list[str]:
    """Pedaços de até `limit` caracteres cortados em fim de frase; uma frase maior que o limite é cortada no último espaço."""
    text = clean(text)
    if len(text) <= limit:
        return [text] if text else []
    out: list[str] = []
    cur = ""
    for sentence in SENTENCE_RE.split(text):
        rest = sentence
        while len(rest) > limit:
            cut = rest.rfind(" ", 0, limit + 1)
            cut = cut if cut > limit // 2 else limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip()
        if not rest:
            continue
        if cur and len(cur) + 1 + len(rest) > limit:
            out.append(cur)
            cur = rest
        else:
            cur = f"{cur} {rest}" if cur else rest
    if cur:
        out.append(cur)
    return out


def _piece_times(u: Utterance, pieces: Sequence[str]) -> list[tuple[int, int]]:
    """Tempo de cada pedaço de uma fala longa: pela palavra onde o pedaço começa (as palavras são as do texto original; na
    revisada, a fração do texto vale como aproximação) ou proporcional, sem palavras."""
    total = sum(len(p) for p in pieces) + len(pieces) - 1
    fracs, pos = [], 0
    for p in pieces:
        fracs.append(pos / max(1, total))
        pos += len(p) + 1
    span = max(0, u.t_end_ms - u.t_start_ms)
    words = [w for w in u.words if w[2].strip()]
    if words:
        lengths = [len(w[2].strip()) + 1 for w in words]
        wtotal = sum(lengths)
        starts, acc = [], 0
        for n in lengths:
            starts.append(acc / wtotal)
            acc += n

        def at(f: float) -> int:
            return int(words[max(0, bisect_right(starts, f) - 1)][0])
    else:
        def at(f: float) -> int:
            return u.t_start_ms + round(span * f)
    begins = [u.t_start_ms] + [min(u.t_end_ms, max(u.t_start_ms, at(f))) for f in fracs[1:]]
    return [(b, begins[i + 1] if i + 1 < len(begins) else u.t_end_ms) for i, b in enumerate(begins)]


def _chunk(kind: ChunkKind, title: str, date: str, text: str, *, t_start: int | None = None, t_end: int | None = None,
           seg_first: int | None = None, seg_last: int | None = None, speakers: str = "", person_ids: Iterable[int] = ()) -> Chunk:
    pids = tuple(sorted({int(p) for p in person_ids}))
    return Chunk(kind, t_start, t_end, seg_first, seg_last, title, speakers, pids, text, text_hash(kind, title, date, speakers, text))


# ---------------------------------------------------------------- transcrição
@dataclass(frozen=True, slots=True)
class _Piece:
    t0: int
    t1: int
    speaker: str
    person_id: int | None
    text: str
    segment_id: int
    turn: int


def _pieces(utterances: Sequence[Utterance]) -> list[_Piece]:
    out: list[_Piece] = []
    turn, prev = -1, None
    for u in utterances:
        text = clean(u.text)
        if not text:
            continue
        if u.speaker != prev:
            turn, prev = turn + 1, u.speaker
        limit = max(MIN_PIECE, BODY_MAX - len(u.speaker) - 2)
        parts = split_text(text, limit)
        times = _piece_times(u, parts) if len(parts) > 1 else [(u.t_start_ms, u.t_end_ms)]
        out += [_Piece(t0, t1, u.speaker, u.person_id, part, u.segment_id, turn) for part, (t0, t1) in zip(parts, times, strict=True)]
    return out


def _body(pieces: Sequence[_Piece]) -> str:
    lines: list[str] = []
    turn = None
    for p in pieces:
        if p.turn == turn and lines:
            lines[-1] = f"{lines[-1]} {p.text}"
        else:
            lines.append(f"{p.speaker}: {p.text}")
            turn = p.turn
    return "\n".join(lines)


def transcript_chunks(utterances: Sequence[Utterance], title: str, date: str) -> list[Chunk]:
    pieces = _pieces(utterances)
    chunks: list[Chunk] = []
    cur: list[_Piece] = []
    size = 0

    def close() -> None:
        nonlocal cur, size
        if not cur:
            return
        speakers = ", ".join(dict.fromkeys(p.speaker for p in cur))
        chunks.append(_chunk(ChunkKind.TRANSCRIPT, title, date, _body(cur), t_start=cur[0].t0, t_end=max(p.t1 for p in cur),
                             seg_first=cur[0].segment_id, seg_last=cur[-1].segment_id, speakers=speakers,
                             person_ids=(p.person_id for p in cur if p.person_id is not None)))
        cur, size = [], 0

    for i, p in enumerate(pieces):
        same_line = bool(cur) and cur[-1].turn == p.turn
        grow = (1 + len(p.text)) if same_line else ((1 if cur else 0) + len(p.speaker) + 2 + len(p.text))
        if cur and (size + grow > BODY_MAX or p.t1 - cur[0].t0 > HARD_CLOSE_MS):
            close()
            grow = len(p.speaker) + 2 + len(p.text)
        cur.append(p)
        size += grow
        end_of_turn = i + 1 == len(pieces) or pieces[i + 1].turn != p.turn
        if end_of_turn and max(x.t1 for x in cur) - cur[0].t0 >= TURN_CLOSE_MS:
            close()
    close()
    return chunks


# ---------------------------------------------------------------- ata
def _section_kinds() -> dict[str, ChunkKind | None]:
    out: dict[str, ChunkKind | None] = {}
    for names in HEADINGS.values():
        for i, kind in enumerate(SECTION_KINDS):
            out[names[i].casefold()] = kind
    return out


SECTION_BY_HEADING = _section_kinds()


def _stamp_ms(match: re.Match[str]) -> int:
    h, m, s = match.group(1), match.group(2), match.group(3)
    return ((int(h) if h else 0) * 3600 + int(m) * 60 + int(s)) * 1000


def parse_item(raw: str) -> tuple[str, int | None]:
    """Texto de um item da ata sem marcadores de Markdown e o tempo do `[mm:ss]` (ou `[h:mm:ss]`) do fim; "sem evidência" e o
    "—" da lista vazia somem."""
    text = clean(raw)
    t_ms: int | None = None
    m = STAMP_RE.search(text)
    if m:
        t_ms = _stamp_ms(m)
        text = text[:m.start()].rstrip()
    text = NO_EVIDENCE_RE.sub("", text).strip()
    text = BOLD_RE.sub(lambda b: b.group(1) or b.group(2), text).strip()
    if text in ("—", "-", "\u2013"):
        text = ""
    return text, t_ms


def parse_minutes(md: str) -> list[tuple[ChunkKind, str, int | None]]:
    """(tipo, texto, tempo) de cada item de lista e de cada parágrafo da ata, na ordem do documento."""
    out: list[tuple[ChunkKind, str, int | None]] = []
    kind: ChunkKind | None = ChunkKind.MINUTES
    para: list[str] = []
    item: list[str] | None = None

    def flush() -> None:
        nonlocal para, item
        for block in ([" ".join(item)] if item else []) + ([" ".join(para)] if para else []):
            text, t_ms = parse_item(block)
            if kind is not None and text:
                out.append((kind, text, t_ms))
        para, item = [], None

    for line in (md or "").splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            flush()
            kind = SECTION_BY_HEADING.get(heading.group(1).strip().casefold(), ChunkKind.MINUTES)
            continue
        if not line.strip():
            flush()
            continue
        listed = LIST_RE.match(line)
        if listed:
            flush()
            item = [listed.group(1)]
        elif item is not None and line[:1].isspace():
            item.append(line.strip())                       # continuação indentada do item
        else:
            if item is not None:
                flush()
            para.append(line.strip())
    flush()
    return out


def _duration(ms: int | None) -> str:
    if not ms or ms <= 0:
        return "—"
    minutes = round(ms / 60000)
    if minutes < 1:
        return "menos de 1 min"
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


# ---------------------------------------------------------------- reunião inteira
def build_chunks(m: MeetingInput) -> list[Chunk]:
    """Todos os trechos da reunião na ordem fixa: transcrição, ata, ações, executivo e a marca da reunião."""
    title, date, pids = clean(m.title), m.date, m.participant_ids
    out = transcript_chunks(m.utterances, title, date)
    for kind, text, t_ms in parse_minutes(m.minutes_md):
        out += [_chunk(kind, title, date, part, t_start=t_ms, person_ids=pids) for part in split_text(text)]
    for a in m.actions:
        text = clean(a.text)
        if not text:
            continue
        owner, due = clean(a.owner or ""), clean(a.due or "")
        tail = "".join(f" · {label}: {value}" for label, value in zip(ACTION_WORDS, (owner, due), strict=True) if value)
        ids = (*pids, a.owner_person_id) if a.owner_person_id is not None else pids
        out += [_chunk(ChunkKind.ACTION, title, date, part, t_start=a.t_ms, seg_first=a.segment_id, seg_last=a.segment_id,
                       speakers=owner, person_ids=ids) for part in split_text(text + tail)]
    out += [_chunk(ChunkKind.EXECUTIVE, title, date, part, person_ids=pids) for part in split_text(m.executive)]
    people = ", ".join(m.participants) or "—"
    meeting = f"Reunião: {title or 'sem título'}\nData: {date}\nParticipantes: {people}\nDuração: {_duration(m.duration_ms)}"
    out.append(_chunk(ChunkKind.MEETING, title, date, meeting, person_ids=pids))
    return out
