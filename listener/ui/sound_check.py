"""Quem teve som na gravação (F5.11 L5: U-114, U-115, A-32, P-04), lido do `signal_ms` por canal que o gravador põe no
evento `stop` (o tempo com sinal acima do limiar de silêncio) e dos pedaços gravados de cada canal (sem pedaço, o canal
não abriu). Sem Qt: o resumo da gravação e a reunião aberta usam as mesmas regras.

- U-115 (P-04): "Gravação salva com falha" só quando a saída ficou sem som ou um canal não abriu (e, como antes, com
  pedaço não gravado no banco); o microfone sem som vira aviso na linha dele. Na sala presencial (decisão da orquestradora,
  por delegação), é o contrário: a saída muda é aviso (sem ninguém remoto, ela fica muda de propósito) e o microfone sem
  som, que grava a sala, é a falha.
- U-114: a reunião processada sem fala abre na Transcrição dizendo qual canal ficou sem som e qual fonte estava escolhida;
  a de antes desta entrega (o `stop` sem `signal_ms`) mostra o texto genérico."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..model import EventType, MeetingStatus, SourceKind

if TYPE_CHECKING:
    from ..store import Store

NO_SOUND_MS = 500                     # [chute] menos que isto de sinal na gravação inteira é "sem som"
CHANNELS = ("L", "R")
GENERIC = ("Nenhuma fala nesta reunião: a transcrição não achou voz no áudio gravado. Toque pelo player para conferir se "
           "ficou sem som.")
SYSTEM_TEXT = "Sistema inteiro"


class Sound(StrEnum):
    OK = "som"
    SILENT = "sem som"
    CLOSED = "não abriu"


def channel_states(signal_ms: Mapping[str, Any] | None, chunks: Mapping[str, Any]) -> dict[str, Sound] | None:
    """O estado de cada canal; None quando a gravação não tem o `signal_ms` (de antes da F5.11)."""
    if not isinstance(signal_ms, Mapping):
        return None
    out: dict[str, Sound] = {}
    for ch in CHANNELS:
        if not chunks.get(ch):
            out[ch] = Sound.CLOSED
        else:
            out[ch] = Sound.OK if int(signal_ms.get(ch) or 0) >= NO_SOUND_MS else Sound.SILENT
    return out


def channel_fails(ch: str, state: Sound, in_room: bool = False) -> bool:
    """O canal que não abriu falha sempre; sem som, falha a saída fora da sala e o microfone na sala presencial."""
    return state == Sound.CLOSED or (state == Sound.SILENT and ch == ("L" if in_room else "R"))


def failed(states: Mapping[str, Sound] | None, unsaved: object, in_room: bool = False) -> bool:
    """P-04: a falha do resumo (um canal que falha, `channel_fails`, ou pedaço fora do banco)."""
    if unsaved:
        return True
    return states is not None and any(channel_fails(ch, state, in_room) for ch, state in states.items())


def source_text(events: Iterable[Mapping[str, Any]]) -> str:
    """A fonte do R no fim da gravação (o último `start` ou `source_change`): "Sistema inteiro" ou o app ("ms-teams")."""
    kind, exe = str(SourceKind.SYSTEM), ""
    for e in events:
        data = e.get("data") or {}
        if e.get("type") in (EventType.START, EventType.SOURCE_CHANGE):
            kind, exe = str(data.get("kind") or data.get("source") or kind), str(data.get("exe") or "")
    return exe.removesuffix(".exe") if kind == SourceKind.APP and exe else SYSTEM_TEXT


def reason(states: Mapping[str, Sound], source: str) -> str:
    """U-114: o porquê, canal por canal ("o microfone teve som; a saída escolhida (ms-teams) ficou sem som")."""
    mic = {Sound.OK: "o microfone teve som", Sound.SILENT: "o microfone ficou sem som",
           Sound.CLOSED: "o microfone não abriu"}[states["L"]]
    out = {Sound.OK: "teve som", Sound.SILENT: "ficou sem som", Sound.CLOSED: "não abriu"}[states["R"]]
    text = f"Nenhuma fala nesta reunião: {mic}; a saída escolhida ({source}) {out}."
    if states["L"] == Sound.OK and states["R"] == Sound.OK:
        text += " A transcrição não achou voz: música, outro idioma ou volume baixo?"
    return text


def no_speech_text(store: Store, meeting_id: int) -> str:
    """O texto da Transcrição de uma reunião processada sem fala; vazio quando ela ainda não foi processada."""
    meeting = store.get_meeting(meeting_id) or {}
    if meeting.get("status") not in (MeetingStatus.PROCESSED, MeetingStatus.REVIEWED):
        return ""
    events = store.meetings.list_events_of(meeting_id, (EventType.START, EventType.SOURCE_CHANGE, EventType.STOP))
    stop = next((e.get("data") or {} for e in reversed(events) if e.get("type") == EventType.STOP), {})
    states = channel_states(stop.get("signal_ms"), store.chunk_stats(meeting_id))
    return reason(states, source_text(events)) if states is not None else GENERIC


__all__ = ["GENERIC", "NO_SOUND_MS", "Sound", "channel_fails", "channel_states", "failed", "no_speech_text", "reason",
           "source_text"]
