"""F5.20 (U-161), dividido da `llm/suggest_task.py` no L0 da F5.21 (A-27), sem mudar nada: o dossiê dos participantes, uma
leitura do banco por gravação, sem LLM."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import Store

DOSSIER_MAX_CHARS = 1_500
DOSSIER_MAX_ACTIONS = 12
DOSSIER_MAX_MEETINGS = 3


def dossier_text(store: Store, meeting_id: int) -> str:
    """U-161: as ações abertas cujo dono é um esperado ou "Você" e as três últimas reuniões com os esperados; vazio sem nada;
    cortado no fim de uma linha em 1.500 caracteres. Só leitura; nenhuma chamada à LLM."""
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        return ""
    try:
        expected = [int(x) for x in json.loads(str(meeting.get("expected_people_json") or "[]"))]
    except (ValueError, TypeError):
        expected = []
    me = store.get_me()
    owners = set(expected) | ({int(me["id"])} if me else set())
    lines: list[str] = []
    if owners:
        for a in store.actions.list_open():
            if a.get("owner_person_id") not in owners:
                continue
            title = a.get("meeting_title") or a.get("meeting_title_llm") or f"reunião {a['meeting_id']}"
            owner = a.get("owner_name") or a.get("owner_text") or "sem dono"
            lines.append(f"- Ação aberta: {a.get('text') or ''} · dono: {owner} · prazo: {a.get('due') or 'sem prazo'} · reunião: {title} "
                         f"({str(a.get('started_at') or '')[:10]})")
            if len(lines) >= DOSSIER_MAX_ACTIONS:
                break
    if expected:
        ids: set[int] = set()
        for pid in expected:
            ids.update(store.people.meetings_of(pid))
        ids.discard(meeting_id)
        parts = []
        for mid in sorted(ids, reverse=True)[:DOSSIER_MAX_MEETINGS]:
            m = store.get_meeting(mid)
            if m is not None:
                parts.append(f"{m.get('title') or m.get('title_llm') or f'reunião {mid}'} ({str(m.get('started_at') or '')[:10]})")
        if parts:
            lines.append("Últimas reuniões com estes participantes: " + "; ".join(parts))
    out: list[str] = []
    total = 0
    for line in lines:
        extra = len(line) + (1 if out else 0)
        if total + extra > DOSSIER_MAX_CHARS:
            break
        out.append(line)
        total += extra
    return "\n".join(out)


__all__ = ["DOSSIER_MAX_ACTIONS", "DOSSIER_MAX_CHARS", "DOSSIER_MAX_MEETINGS", "dossier_text"]
