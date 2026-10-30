"""Respostas padrão do `FakeProvider` por tarefa, derivadas do próprio pedido (contrato "Testes sem rede"): `identify` devolve
a pessoa cujo nome aparece na transcrição com a citação literal; `minutes` cita segmentos reais e os marcadores; `revise`
devolve os segmentos do bloco com pontuação; `glossary`, `title`, `executive`, `pre_summary`, `transcribe` e `chat` têm saídas
estáveis; `partial` (F4) mantém os itens do parcial anterior e acrescenta uma decisão na primeira legenda nova e uma ação na
última; `rag_chat` (F5) cita as primeiras falas dos trechos enviados por apelido. Tudo válido nos esquemas do projeto, para os
testes do runner e o worker leve com `LISTENER_LLM=fake`."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .prompts.common import Line, parse_lines
from .types import LlmTask, Request, Role

CANDIDATE_RE = re.compile(r"\((\d+)\) ([^;—\n]+?)(?: —[^;\n]*)?(?:;|$)", re.MULTILINE)
MARKER_RE = re.compile(r"marcador (\d+) em \[(\d+):(\d{2})\]")


def _user_text(req: Request) -> str:
    return "\n\n".join(m.text for m in req.messages if m.role == Role.USER)


def _after(text: str, key: str) -> str:
    i = text.find(key)
    return text[i + len(key):] if i >= 0 else ""


def _lines(req: Request) -> list[Line]:
    return parse_lines(_user_text(req))


def identify(req: Request) -> dict[str, Any]:
    text = _user_text(req)
    pending = [p.strip() for p in _after(text, "Falantes por resolver:").split("\n", 1)[0].split(",") if p.strip()]
    cands_block = _after(text, "Candidatos (person_id) nome").split("\n", 1)[0]
    candidates = [(int(i), n.strip()) for i, n in CANDIDATE_RE.findall(cands_block + ";")]
    lines = _lines(req)
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for label in pending:
        hit = None
        for pid, name in candidates:
            if pid in used:
                continue
            ln = next((ln for ln in lines if name.casefold() in ln.text.casefold()), None)
            if ln is not None:
                hit = (pid, name, ln)
                break
        if hit is None:
            out.append({"cluster": label, "person_id": None, "new_name": None, "confidence": 0.0, "evidence": []})
            continue
        pid, name, ln = hit
        used.add(pid)
        out.append({"cluster": label, "person_id": pid, "new_name": None, "confidence": 0.8,
                    "evidence": [{"segment_id": ln.segment_id, "quote": name}]})
    return {"speakers": out}


def minutes(req: Request) -> dict[str, Any]:
    text = _user_text(req)
    lines = _lines(req)
    if "Notas do bloco " in text:                                  # redução: junta as notas dos blocos
        merged: dict[str, Any] = {"context": "", "decisions": [], "actions": [], "highlights": [], "open_points": [], "next_steps": []}
        for raw in re.findall(r"Notas do bloco \d+:\n(\{.*?\})(?=\n\nNotas do bloco |\Z)", text, re.DOTALL):
            try:
                note = json.loads(raw)
            except ValueError:
                continue
            merged["context"] = (merged["context"] + " " + str(note.get("context") or "")).strip()[:1000]
            for key in ("decisions", "actions", "highlights", "open_points", "next_steps"):
                merged[key] += list(note.get(key) or [])
        return merged
    title = _after(text, "Título: ").split("\n", 1)[0].strip() or "a reunião"
    first = lines[0] if lines else None
    last = lines[-1] if lines else None
    from ..pipeline.voices import VOICE_PREFIX

    speaker = next((ln.speaker for ln in lines if not ln.speaker.startswith(("SPK_", VOICE_PREFIX))), None)   # F5.5 (A-11)
    highlights = []
    for mid, mm, ss in MARKER_RE.findall(text):
        t_ms = (int(mm) * 60 + int(ss)) * 1000
        near = min(lines, key=lambda ln: abs(ln.t_start_ms - t_ms)) if lines else None
        highlights.append({"marker_id": int(mid), "text": "trecho marcado pelo usuário", "segment_ids": [near.segment_id] if near else []})
    return {
        "context": f"Reunião simulada sobre {title}.",
        "decisions": [{"text": f"Decidido: {first.text}", "segment_ids": [first.segment_id]}] if first else [],
        "actions": [{"text": "Enviar a ata aos participantes", "owner_text": speaker.split(" (")[0] if speaker else None,
                     "owner_person_id": None, "due_text": "sexta", "due_date": "2026-10-30",
                     "segment_ids": [last.segment_id] if last else []},
                    {"text": "Ação sem evidência", "owner_text": None, "owner_person_id": None, "due_text": None, "due_date": None,
                     "segment_ids": [999999]}],
        "highlights": highlights,
        "open_points": [],
        "next_steps": [{"text": "Marcar a próxima reunião", "segment_ids": []}],
    }


def summary_text(prefix: str, limit: int) -> Callable[[Request], dict[str, Any]]:
    def build(req: Request) -> dict[str, Any]:
        text = _user_text(req)
        ctx = ""
        try:
            data = json.loads(_after(text, "Ata (JSON):\n").strip() or "{}")
            ctx = str(data.get("context") or "")
        except ValueError:
            ctx = ""
        return {"text": f"{prefix} {ctx}".strip()[:limit]}
    return build


def title(req: Request) -> dict[str, Any]:
    text = (_after(_user_text(req), "Resumo:\n") or _after(_user_text(req), "Abertura da transcrição:\n")).strip()
    words = re.findall(r"\w+", text)[:5]
    return {"title": (" ".join(words) or "Reunião").capitalize()[:80]}


def revise(req: Request) -> dict[str, Any]:
    text = _user_text(req)
    block = _after(text, "Segmentos a editar (#segment_id [mm:ss] falante: texto):\n").split("\n\nContexto depois", 1)[0]
    out = []
    for ln in parse_lines(block):
        new = ln.text[:1].upper() + ln.text[1:]
        if new and new[-1] not in ".?!":
            new += "."
        out.append({"segment_id": ln.segment_id, "text": new})
    return {"revisions": out}


def glossary(req: Request) -> dict[str, Any]:
    text = _user_text(req)
    title_text = _after(text, "Título: ").split("\n", 1)[0]
    raw_names = _after(text, "Nomes de participantes não resolvidos:").split("\n", 1)[0].split(",")
    unresolved = [n.strip() for n in raw_names if n.strip() and n.strip() != "(nenhum)"]
    terms = [w for w in re.findall(r"[A-ZÀ-Ú][\wÀ-ú-]+", title_text) if len(w) > 2][:40]
    return {"terms": terms, "people": [{"name": n, "person_id": None} for n in unresolved]}


def transcribe(req: Request) -> dict[str, Any]:
    seconds = max(1.0, req.audio_seconds)
    return {"language": "pt", "utterances": [{"start_s": 0.0, "end_s": round(seconds / 2, 2), "text": "fala simulada um dois três"},
                                             {"start_s": round(seconds / 2, 2), "end_s": round(seconds, 2), "text": "quatro cinco seis"}]}


def partial(req: Request) -> dict[str, Any]:
    text = _user_text(req)
    lines = parse_lines(_after(text, "Legendas novas (formato: #caption_id [mm:ss] falante: texto):\n"))
    try:
        previous = json.loads(_after(text, "Resumo parcial anterior (JSON):\n").split("\n\nLegendas novas", 1)[0])
    except ValueError:
        previous = {}
    previous = previous if isinstance(previous, dict) else {}
    decisions = list(previous.get("decisions") or [])
    actions = list(previous.get("actions") or [])
    if lines:
        decisions.append({"text": f"Decidido: {lines[0].text}"[:300], "caption_id": lines[0].segment_id})
        owner = lines[-1].speaker if lines[-1].speaker not in ("Participante",) else None
        actions.append({"text": f"Retomar: {lines[-1].text}"[:300], "caption_id": lines[-1].segment_id, "owner": owner})
    summary = (str(previous.get("summary") or "") + f" Parcial simulado com {len(lines)} legenda(s) nova(s).").strip()
    return {"summary": summary[-1000:], "decisions": decisions[-15:], "actions": actions[-15:]}


def chat(req: Request) -> str:
    lines = _lines(req)
    stamp = lines[0].stamp if lines else "00:00"
    return f"Resposta simulada com citação [{stamp}] e outra fora da transcrição [59:59]."


BLOCK_RE = re.compile(r"^\[(R\d+)\] ", re.MULTILINE)


def rag_chat(req: Request) -> str:
    """F5: cita a primeira fala de R1 (e de R2 no mesmo colchete, quando há), R1 sem tempo e um apelido que não existe; sem
    trechos, diz que não achou."""
    excerpts = next((m.text for m in req.messages if m.role == Role.USER and m.text.startswith("Trechos")), "")
    heads = list(BLOCK_RE.finditer(excerpts))
    if not heads:
        return "Não encontrei isso nas reuniões."
    cites: list[str] = []
    for i, head in enumerate(heads[:2]):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(excerpts)
        lines = parse_lines(excerpts[head.end():end])
        cites.append(f"{head.group(1)} {lines[0].stamp}" if lines else head.group(1))
    return (f"Resposta simulada sobre {len(heads)} reunião(ões) [{'; '.join(cites)}]; ver também [{heads[0].group(1)}] "
            "e [R99 00:00].")


DEFAULTS: dict[LlmTask, Callable[[Request], Any]] = {
    LlmTask.IDENTIFY: identify, LlmTask.MINUTES: minutes, LlmTask.EXECUTIVE: summary_text("Resumo executivo simulado:", 1500),
    LlmTask.PRE_SUMMARY: summary_text("Pré-resumo simulado:", 600), LlmTask.TITLE: title, LlmTask.REVISE: revise,
    LlmTask.GLOSSARY: glossary, LlmTask.TRANSCRIBE: transcribe, LlmTask.CHAT: chat, LlmTask.PARTIAL: partial,
    LlmTask.RAG_CHAT: rag_chat,
}
