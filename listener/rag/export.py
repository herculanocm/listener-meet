"""Exportar a reunião (F5 item 12, U-011 parcial, D-012) e "copiar com citação" (U-029), puro salvo `gather` (que só lê o
banco): o Markdown determinístico da reunião (título, data, duração e participantes; a ata exibida, o resumo executivo e
o pré-resumo, as ações com dono, prazo e estado, os marcadores e a transcrição "[mm:ss] Nome — Cargo: texto", revisada
quando existe, como a leitura mostra); a ata e a transcrição separadas para a área de transferência; o nome padrão do
arquivo ("AAAA-MM-DD título.md" ou ".docx", sem caracteres que o Windows recusa) e a escrita atômica de texto ou bytes
(temporário na mesma pasta e `os.replace` no fim: um erro no meio nunca deixa meio arquivo no lugar do anterior).
Exportar só por ação do usuário (D-012: não fere a CP9).

F5.1 (U-011, A-18): a ficha, a linha da ação e a do marcador saem de funções que o .md e o .docx dividem; o parser puro do
Markdown da ata e dos resumos (`parse_markdown`, em `rag/markdown.py`) aparece aqui também; o escritor do .docx é o
`rag/docx_export.py`.

F5.3 (A-07, pergunta 8): os textos fixos do .md e do .docx (seções, ficha, rótulos da ação, estados, marcas de vazio) saem
no idioma da ata (`summaries.language`; sem ata, o da reunião), pela tabela `TEXTS`."""
from __future__ import annotations

import contextlib
import html
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config.language import EN, PT, meeting_language, output_code
from ..llm.prompts.common import stamp
from ..model import ActionStatus, SummaryKind
from . import citations
from .markdown import MdBlock, MdRun, parse_inline, parse_markdown

if TYPE_CHECKING:
    from ..store import Store

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})
NAME_MAX = 120                   # o nome sem a extensão: caminhos longos do Windows ainda cabem em pastas fundas
HEADING_RE = re.compile(r"^(#{1,5})(\s)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ExportTexts:
    """Os textos fixos do .md e do .docx num idioma (F5.3 A-07)."""
    minutes: str
    executive: str
    pre_summary: str
    actions: str
    markers: str
    transcript: str
    date: str
    duration: str
    participants: str
    owner: str
    due: str
    no_note: str
    no_minutes: str
    no_transcript: str
    meeting: str                  # "Reunião 7" quando não há título
    status: Mapping[str, str]     # estado da ação


TEXTS: dict[str, ExportTexts] = {
    PT: ExportTexts("Ata", "Resumo executivo", "Pré-resumo", "Ações", "Marcadores", "Transcrição", "Data", "Duração", "Participantes",
                    "dono", "prazo", "(sem nota)", "_(ata ainda não gerada)_", "_(sem transcrição)_", "Reunião",
                    {ActionStatus.OPEN: "aberta", ActionStatus.DONE: "feita", ActionStatus.DISCARDED: "descartada"}),
    EN: ExportTexts("Minutes", "Executive summary", "Pre-summary", "Actions", "Markers", "Transcript", "Date", "Duration",
                    "Participants", "owner", "due", "(no note)", "_(minutes not generated yet)_", "_(no transcript)_", "Meeting",
                    {ActionStatus.OPEN: "open", ActionStatus.DONE: "done", ActionStatus.DISCARDED: "discarded"}),
}


@dataclass(frozen=True, slots=True)
class ExportAction:
    text: str
    owner: str
    due: str
    status: str


@dataclass(frozen=True, slots=True)
class ExportLine:
    t_ms: int
    speaker: str                 # "Nome — Cargo", "Nome" ou "Voz SPK_1"
    text: str


@dataclass(frozen=True, slots=True)
class ExportMeeting:
    meeting_id: int
    title: str                   # o do usuário, senão o da LLM; vazio sem nenhum
    started_at: str              # ISO com offset
    duration_ms: int | None
    participants: tuple[str, ...] = ()
    minutes_md: str = ""         # a ata exibida (`text_edited`, senão `text_llm`)
    executive: str = ""
    pre_summary: str = ""
    actions: tuple[ExportAction, ...] = ()
    markers: tuple[tuple[int, str], ...] = ()
    transcript: tuple[ExportLine, ...] = ()
    language: str = PT           # F5.3 (A-07): o idioma da ata, que escolhe os textos fixos e o `w:lang` do .docx

    @property
    def texts(self) -> ExportTexts:
        return texts_for(self.language)

    @property
    def shown_title(self) -> str:
        return self.title or f"{self.texts.meeting} {self.meeting_id}"


def texts_for(language: str | None) -> ExportTexts:
    return TEXTS[output_code(language)]


# ---------------------------------------------------------------- formatos
def fmt_date(iso: str | None) -> str:
    """"2026-10-24T14:03:00-03:00" -> "24/10/2026"; vazio quando não é data."""
    d = str(iso or "")[:10]
    return f"{d[8:10]}/{d[5:7]}/{d[:4]}" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else ""


def fmt_datetime(iso: str | None) -> str:
    """"24/10/2026 14:03" (hora local gravada na própria data, D-F1.1 A5)."""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return fmt_date(iso)
    return dt.strftime("%d/%m/%Y %H:%M")


def fmt_duration(ms: int | None) -> str:
    """"1 h 05 min", "35 min", "45 s"; vazio sem duração."""
    if ms is None or ms < 0:
        return ""
    s = int(ms) // 1000
    if s < 60:
        return f"{s} s"
    h, m = divmod(s // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def shift_headings(md: str, by: int = 1) -> str:
    """Desce os títulos do Markdown um nível ("## Decisões" -> "### Decisões") para caber debaixo de uma seção."""
    return HEADING_RE.sub(lambda m: "#" * min(6, len(m.group(1)) + by) + m.group(2), md)


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


# ---------------------------------------------------------------- leitura
def gather(store: Store, meeting_id: int, prefer_llm: bool = True) -> ExportMeeting | None:
    """A reunião como a leitura mostra (transcrição revisada quando existe, "Nome — Cargo" do dia, a ata exibida e as ações
    vigentes da ata atual). None quando a reunião não existe."""
    from ..pipeline import review

    m = store.get_meeting(meeting_id)
    if m is None:
        return None
    people = {int(p["id"]): str(p["name"]) for p in store.people.list_people()}
    minutes = store.summaries.current(meeting_id, SummaryKind.MINUTES)
    language = output_code(str((minutes or {}).get("language") or "") or meeting_language(store, m))
    texts = texts_for(language)
    actions: list[ExportAction] = []
    if minutes is not None:
        for a in store.actions.list_for_meeting(meeting_id, summary_id=int(minutes["id"])):
            pid = a.get("owner_person_id")
            owner = people.get(int(pid), "") if pid is not None else str(a.get("owner_text") or "")
            status = str(a.get("status") or ActionStatus.OPEN)
            actions.append(ExportAction(_clean(str(a.get("text") or "")), owner, str(a.get("due") or ""),
                                        texts.status.get(status, status)))
    rows = review.transcript_rows(store, meeting_id, prefer_llm)
    lines = tuple(ExportLine(r.t_start_ms, f"{r.name} — {r.role}" if r.role else r.name, _clean(r.shown_text)) for r in rows)
    return ExportMeeting(int(meeting_id), str(m.get("title") or m.get("title_llm") or ""), str(m.get("started_at") or ""),
                         m.get("duration_ms"), tuple(review.participants(store, meeting_id)), store.summaries.display_text(minutes),
                         store.summaries.display_text(store.summaries.current(meeting_id, SummaryKind.EXECUTIVE)),
                         store.summaries.display_text(store.summaries.current(meeting_id, SummaryKind.PRE_SUMMARY)), tuple(actions),
                         tuple((int(k["t_ms"]), str(k.get("note") or "")) for k in store.list_markers(meeting_id)), lines, language)


# ---------------------------------------------------------------- Markdown
def header_facts(m: ExportMeeting) -> list[tuple[str, str]]:
    """A ficha da reunião (data e hora, duração, participantes), só o que existe: a mesma no .md e no .docx."""
    t = m.texts
    facts = [(t.date, fmt_datetime(m.started_at)), (t.duration, fmt_duration(m.duration_ms)),
             (t.participants, ", ".join(m.participants))]
    return [(label, value) for label, value in facts if value]


def header_markdown(m: ExportMeeting) -> str:
    """Título e a ficha da reunião (data e hora, duração, participantes)."""
    out = [f"# {m.shown_title}", ""]
    out += [f"- **{label}:** {value}" for label, value in header_facts(m)]
    return "\n".join(out).rstrip() + "\n"


def _section(title: str, body: str) -> str:
    body = body.strip()
    return f"## {title}\n\n{body}\n" if body else ""


def action_text(a: ExportAction, language: str = PT) -> str:
    """"Mandar a planilha · dono: Bia · prazo: 2026-10-31 · aberta" (a linha da ação no .md e no .docx)."""
    t = texts_for(language)
    tail = "".join(f" · {label}: {value}" for label, value in ((t.owner, a.owner), (t.due, a.due)) if value)
    return f"{a.text}{tail} · {a.status}"


def marker_text(t_ms: int, note: str, language: str = PT) -> str:
    return f"[{stamp(t_ms)}] {_clean(note) or texts_for(language).no_note}"


def actions_markdown(actions: Sequence[ExportAction], language: str = PT) -> str:
    return "\n".join(f"- {action_text(a, language)}" for a in actions)


def transcript_lines(lines: Sequence[ExportLine]) -> str:
    """Uma fala por parágrafo ("[mm:ss] Nome — Cargo: texto"): cada uma numa linha própria no Markdown e no HTML."""
    return "\n\n".join(f"[{stamp(ln.t_ms)}] {ln.speaker}: {ln.text}" for ln in lines)


def minutes_markdown(m: ExportMeeting) -> str:
    """"Copiar ata": a ficha e a ata exibida (os títulos dela um nível abaixo)."""
    t = m.texts
    parts = [header_markdown(m), _section(t.minutes, shift_headings(m.minutes_md) if m.minutes_md.strip() else t.no_minutes)]
    return "\n".join(p for p in parts if p)


def transcript_markdown(m: ExportMeeting) -> str:
    """"Copiar transcrição": a ficha e as falas."""
    body = transcript_lines(m.transcript) or m.texts.no_transcript
    return "\n".join((header_markdown(m), _section(m.texts.transcript, body)))


def meeting_markdown(m: ExportMeeting) -> str:
    """O arquivo .md inteiro, determinístico (mesmos dados, mesmo texto): ficha, ata, resumos, ações, marcadores e
    transcrição."""
    t = m.texts
    parts = [header_markdown(m),
             _section(t.minutes, shift_headings(m.minutes_md)),
             _section(t.executive, shift_headings(m.executive)),
             _section(t.pre_summary, shift_headings(m.pre_summary)),
             _section(t.actions, actions_markdown(m.actions, m.language)),
             _section(t.markers, "\n".join(f"- {marker_text(time, note, m.language)}" for time, note in m.markers)),
             _section(t.transcript, transcript_lines(m.transcript))]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- arquivo
def safe_filename(name: str, limit: int = NAME_MAX) -> str:
    """Nome de arquivo que o Windows aceita: sem `<>:"/\\|?*` e controles, sem ponto ou espaço no fim, sem nome reservado
    (CON, NUL, COM1...), espaços simples e até `limit` caracteres."""
    text = " ".join(INVALID_CHARS.sub(" ", name).split())[:limit].rstrip(" .")
    if not text:
        return "reuniao"
    if text.split(".")[0].upper() in RESERVED_NAMES:
        text = f"_{text}"
    return text


def default_filename(m: ExportMeeting, ext: str = ".md") -> str:
    """"2026-10-24 Orçamento 2027.md" (ou ".docx": `ext` com ou sem o ponto)."""
    date = m.started_at[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.started_at[:10]) else ""
    return safe_filename(f"{date} {m.shown_title}".strip()) + (ext if ext.startswith(".") else f".{ext}")


def save_error_text(path: str | Path, error: OSError) -> str:
    """O aviso de um .md ou .docx que não salvou (U-030). `PermissionError` é, no Windows, o arquivo aberto em outro programa
    (o Word e o Excel seguram o arquivo e a troca do temporário falha com "acesso negado", visto em 25/10) e, nos outros
    sistemas, a pasta sem permissão de escrita: a frase cobre os dois, sem o erro cru (que vai para o log); o resto mostra o
    erro."""
    if isinstance(error, PermissionError):
        return (f"Não consegui salvar o arquivo:\n{path}\n\nEle está aberto em outro programa (Word, Excel…) ou a pasta não "
                "aceita gravação. Feche o arquivo e tente de novo, ou salve com outro nome.")
    return f"Não consegui salvar o arquivo:\n{path}\n\n{error}"


def write_atomic(path: str | Path, data: str | bytes) -> Path:
    """Escreve num temporário na mesma pasta (texto em UTF-8 com quebras `\\n`, ou os bytes como vieram, o .docx) e troca no
    fim com `os.replace`; em erro o temporário sai e o arquivo anterior fica como estava. `OSError` sobe para quem chamou
    avisar."""
    target = Path(path)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(prefix=f".{target.stem[:40]}-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp).replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
    return target


# ---------------------------------------------------------------- copiar com citação (U-029)
def quote(text: str, speaker: str, title: str, date: str | None, t_ms: int | None) -> tuple[str, str]:
    """`"<fala>" — <Nome — Cargo>, <título> (dd/mm/aaaa) [mm:ss]` em texto e o mesmo em HTML (escapado; nome em negrito,
    título em itálico). Sem falante, sem tempo ou sem data, a parte some."""
    body = _clean(text)
    when = fmt_date(date)
    t = f" [{stamp(t_ms)}]" if t_ms is not None else ""
    d = f" ({when})" if when else ""
    who = f"{speaker}, " if speaker else ""
    plain = f'"{body}" — {who}{title}{d}{t}'
    who_html = f"<b>{html.escape(speaker)}</b>, " if speaker else ""
    rich = f"“{html.escape(body)}” — {who_html}<i>{html.escape(title)}</i>{html.escape(d)}{html.escape(t)}"
    return plain, rich


def quote_answer(text: str, saved: Sequence[Mapping[str, Any]], meetings: Mapping[int, tuple[str | None, str | None]]) -> tuple[str, str]:
    """Uma resposta do chat sobre todas as reuniões com as citações legíveis ("[Orçamento 2027, 24/10, 12:03]": reunião, data e
    [mm:ss]) em texto e em HTML sem links (o `listener://` não abre fora do app)."""
    plain = citations.render_saved(text, saved, meetings, as_html=False).strip()
    return plain, html.escape(plain).replace("\n", "<br>")


__all__ = ["TEXTS", "ExportAction", "ExportLine", "ExportMeeting", "ExportTexts", "MdBlock", "MdRun", "action_text", "default_filename",
           "fmt_date", "fmt_datetime", "fmt_duration", "gather", "header_facts", "header_markdown", "marker_text", "meeting_markdown",
           "minutes_markdown", "parse_inline", "parse_markdown", "quote", "quote_answer", "safe_filename", "shift_headings",
           "texts_for", "transcript_markdown", "write_atomic"]
