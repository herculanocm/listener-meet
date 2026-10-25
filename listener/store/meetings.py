"""Repositório do main: presets, reuniões, marcadores, eventos e revisões (linhas como `TypedDict`). Pessoas: `people.py`."""
from __future__ import annotations

import json
from typing import Any, TypedDict, cast

from ..log import get_logger
from ..model import EventType, MeetingStatus
from .core import Core, _now, audio_file_for, json_default

log = get_logger("store.meetings")


class MeetingRow(TypedDict, total=False):
    id: int
    title: str | None
    title_llm: str | None
    started_at: str
    ended_at: str | None
    duration_ms: int | None
    platform: str | None
    preset_id: int | None
    status: str
    expected_participants: str | None
    is_in_room: int
    notes: str | None
    audio_file: str | None
    language: str | None


class PresetRow(TypedDict, total=False):
    id: int
    name: str
    mic_device_id: str | None
    mic_device_name: str | None
    source_kind: str
    source_exe: str | None
    sample_rate: int
    is_in_room: int
    updated_at: str


class EventRow(TypedDict, total=False):
    id: int
    meeting_id: int | None
    t_ms: int | None
    type: str
    data: Any
    created_at: str


class MarkerRow(TypedDict, total=False):
    id: int
    meeting_id: int
    t_ms: int
    note: str | None
    created_at: str


class MeetingsRepo:
    def __init__(self, core: Core):
        self.core = core

    # ---------------------------------------------------------------- presets
    def list_presets(self) -> list[PresetRow]:
        return [cast(PresetRow, dict(r)) for r in self.core.q("SELECT * FROM presets ORDER BY name")]

    def get_preset(self, preset_id: int) -> PresetRow | None:
        rows = self.core.q("SELECT * FROM presets WHERE id = ?", (preset_id,))
        return cast(PresetRow, dict(rows[0])) if rows else None

    def upsert_preset(self, name: str, mic_device_id: str | None, mic_device_name: str | None, source_kind: str,
                      source_exe: str | None, sample_rate: int = 16000, is_in_room: bool = False) -> int:
        with self.core.transaction():
            self.core.execute(
                "INSERT INTO presets(name, mic_device_id, mic_device_name, source_kind, source_exe, sample_rate, is_in_room, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET mic_device_id = excluded.mic_device_id, "
                "mic_device_name = excluded.mic_device_name, source_kind = excluded.source_kind, source_exe = excluded.source_exe, "
                "sample_rate = excluded.sample_rate, is_in_room = excluded.is_in_room, updated_at = excluded.updated_at",
                (name, mic_device_id, mic_device_name, str(source_kind), source_exe, int(sample_rate), int(bool(is_in_room)), _now()))
            return int(self.core.q("SELECT id FROM presets WHERE name = ?", (name,))[0]["id"])

    def delete_preset(self, preset_id: int) -> bool:
        return self.core.execute("DELETE FROM presets WHERE id = ?", (preset_id,)) > 0

    def seed_default_presets(self) -> None:
        if self.list_presets():
            return
        for name, kind, exe in (("Teams", "app", "ms-teams.exe"), ("Zoom", "app", "Zoom.exe"),
                                ("Meet (navegador)", "app", "msedge.exe"), ("Sistema inteiro", "system", None)):
            self.upsert_preset(name, None, None, kind, exe)

    # ---------------------------------------------------------------- meetings
    def create_meeting(self, title: str | None, platform: str | None, preset_id: int | None,
                       expected_participants: str | None, is_in_room: bool = False) -> int:
        """Cria a reunião já apontando para o arquivo de áudio do mês (criado e migrado aqui, antes do primeiro pedaço)."""
        started = _now()
        audio_file = audio_file_for(started)
        self.core.attach(audio_file)
        return self.core.insert(
            "INSERT INTO meetings(title, started_at, platform, preset_id, status, expected_participants, is_in_room, audio_file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, started, platform, preset_id, MeetingStatus.RECORDING, expected_participants, int(bool(is_in_room)), audio_file))

    def get_meeting(self, meeting_id: int) -> MeetingRow | None:
        rows = self.core.q("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        return cast(MeetingRow, dict(rows[0])) if rows else None

    def list_meetings(self) -> list[MeetingRow]:
        return [cast(MeetingRow, dict(r)) for r in self.core.q("SELECT * FROM meetings ORDER BY id DESC")]

    def finish_meeting(self, meeting_id: int, duration_ms: int, status: MeetingStatus = MeetingStatus.RECORDED) -> None:
        self.core.execute("UPDATE meetings SET status = ?, ended_at = ?, duration_ms = ? WHERE id = ?",
                          (status, _now(), int(duration_ms), meeting_id))

    def set_status(self, meeting_id: int, status: MeetingStatus) -> None:
        self.core.execute("UPDATE meetings SET status = ? WHERE id = ?", (status, meeting_id))

    def set_language(self, meeting_id: int, language: str | None) -> None:
        """Idioma detectado na primeira janela com fala (D-013); só preenche se ainda não houver."""
        self.core.execute("UPDATE meetings SET language = COALESCE(language, ?) WHERE id = ?", (language, meeting_id))

    def list_by_status(self, *statuses: MeetingStatus) -> list[MeetingRow]:
        marks = ",".join("?" * len(statuses))
        rows = self.core.q(f"SELECT * FROM meetings WHERE status IN ({marks}) ORDER BY id DESC", tuple(str(s) for s in statuses))  # noqa: S608
        return [cast(MeetingRow, dict(r)) for r in rows]

    def update_title(self, meeting_id: int, title: str, author: str = "usuario") -> None:
        m = self.get_meeting(meeting_id)
        before = m.get("title") if m else None
        if before == title:
            return
        with self.core.transaction():
            self.core.execute("UPDATE meetings SET title = ? WHERE id = ?", (title, meeting_id))
            self.add_revision("meeting", meeting_id, "title", before, title, author)

    def audio_files_referenced(self) -> list[str]:
        """Arquivos de áudio citados por alguma reunião (para achar os que sumiram da pasta)."""
        if self.core.main_version < 3:
            return []
        return [str(r["audio_file"]) for r in self.core.q("SELECT DISTINCT audio_file FROM meetings WHERE audio_file IS NOT NULL "
                                                           "ORDER BY audio_file")]

    def interrupted_candidates(self) -> list[int]:
        return [int(r["id"]) for r in self.core.q("SELECT id FROM meetings WHERE status = ?", (MeetingStatus.RECORDING,))]

    def set_interrupted(self, meeting_id: int, duration_ms: int | None) -> None:
        """`gravando` -> `interrompida` com a duração calculada pelos pedaços (M-05); pedaços preservados (CP3)."""
        self.core.execute("UPDATE meetings SET status = ?, ended_at = COALESCE(ended_at, ?), duration_ms = COALESCE(?, duration_ms) "
                          "WHERE id = ? AND status = ?",
                          (MeetingStatus.INTERRUPTED, _now(), duration_ms, meeting_id, MeetingStatus.RECORDING))

    def delete_meeting_row(self, meeting_id: int) -> None:
        self.core.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))

    # ---------------------------------------------------------------- markers, events, revisions
    def add_marker(self, meeting_id: int, t_ms: int, note: str = "") -> int:
        return self.core.insert("INSERT INTO markers(meeting_id, t_ms, note, created_at) VALUES (?, ?, ?, ?)",
                                (meeting_id, int(t_ms), note or "", _now()))

    def list_markers(self, meeting_id: int) -> list[MarkerRow]:
        return [cast(MarkerRow, dict(r)) for r in self.core.q("SELECT * FROM markers WHERE meeting_id = ? ORDER BY t_ms", (meeting_id,))]

    def add_event(self, meeting_id: int | None, t_ms: int | None, type_: EventType | str, data: dict[str, Any] | None = None) -> int:
        payload = json.dumps(data or {}, ensure_ascii=False, default=json_default)
        return self.core.insert("INSERT INTO events(meeting_id, t_ms, type, data, created_at) VALUES (?, ?, ?, ?, ?)",
                                (meeting_id, t_ms, str(type_), payload, _now()))

    def list_events(self, meeting_id: int) -> list[EventRow]:
        out: list[EventRow] = []
        for r in self.core.q("SELECT * FROM events WHERE meeting_id = ? ORDER BY id", (meeting_id,)):
            d = cast(EventRow, dict(r))
            try:
                d["data"] = json.loads(d.get("data") or "{}")
            except ValueError:
                log.debug("evento %s com data ilegível", d.get("id"))
            out.append(d)
        return out

    def add_revision(self, entity: str, entity_id: int, field: str, before: Any, after: Any, author: str) -> int:
        return self.core.insert(
            "INSERT INTO revisions(entity, entity_id, field, before, after, author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entity, entity_id, field, None if before is None else str(before), None if after is None else str(after), author, _now()))
