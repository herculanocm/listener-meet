"""Repositório do main: presets, reuniões, marcadores, eventos e revisões (linhas como `TypedDict`). Pessoas: `people.py`."""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, TypedDict, cast

from ..config.language import whisper_code
from ..log import get_logger
from ..model import PIPELINE_JOB_KINDS, EventType, JobStatus, MeetingStatus
from .core import Core, _now, audio_file_for, json_default
from .floors import MEETINGS, next_id_sql

log = get_logger("store.meetings")
# F5.6 (A-32): o que a cascata da reunião leva (contadas para o log da exclusão, A-46); `job_artifacts` e `rag_vectors` vão
# pela cascata das filhas e o `rag_fts` pelo gatilho
CASCADE_TABLES = ("processing_jobs", "clusters", "segments", "summaries", "action_items", "captions", "markers", "events",
                  "rag_chunks", "chat_messages")
# A-33: as revisões das entidades da reunião (as de `voice_profile` e `person` ficam)
REVISION_ENTITIES = (("cluster", "clusters"), ("summary", "summaries"), ("action_item", "action_items"))


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
    language: str | None               # F5.3: código do Whisper (pt ou en), escolhido no warm-up (A-01)
    glossary: str | None               # F3: termos prováveis do warm-up (LLM, editados pelo usuário)
    expected_people_json: str | None   # F3: ids de `people` casados com os participantes esperados


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
    live_enabled: int | None           # F4: caixas do warm-up guardadas no preset (nulo = padrão das Configurações)
    live_names: int | None


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
                      source_exe: str | None, sample_rate: int = 16000, is_in_room: bool = False, *,
                      live_enabled: bool | None = None, live_names: bool | None = None) -> int:
        """F4: `live_enabled` e `live_names` são as caixas do warm-up; None grava nulo (padrão das Configurações) num preset
        novo e mantém o valor guardado num preset existente."""
        flags = tuple(None if v is None else int(bool(v)) for v in (live_enabled, live_names))
        with self.core.transaction():
            self.core.execute(
                "INSERT INTO presets(name, mic_device_id, mic_device_name, source_kind, source_exe, sample_rate, is_in_room, live_enabled, "
                "live_names, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "mic_device_id = excluded.mic_device_id, mic_device_name = excluded.mic_device_name, source_kind = excluded.source_kind, "
                "source_exe = excluded.source_exe, sample_rate = excluded.sample_rate, is_in_room = excluded.is_in_room, "
                "live_enabled = COALESCE(excluded.live_enabled, live_enabled), live_names = COALESCE(excluded.live_names, live_names), "
                "updated_at = excluded.updated_at",
                (name, mic_device_id, mic_device_name, str(source_kind), source_exe, int(sample_rate), int(bool(is_in_room)), *flags,
                 _now()))
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
                       expected_participants: str | None, is_in_room: bool = False, glossary: str | None = None,
                       expected_people_json: str | None = None, language: str | None = None) -> int:
        """Cria a reunião já apontando para o arquivo de áudio do mês (criado e migrado aqui, antes do primeiro pedaço).
        F3: `glossary` e `expected_people_json` vêm do warm-up (F3A-11). F5.3 (A-01): `language` é o idioma escolhido no
        warm-up, gravado como código do Whisper; sem ele, fica nulo e o runner usa o padrão."""
        started = _now()
        audio_file = audio_file_for(started)
        self.core.attach(audio_file)
        return self.core.insert(                         # F5.6 (A-34): o id acima do piso, nunca o de uma reunião excluída
            "INSERT INTO meetings(id, title, started_at, platform, preset_id, status, expected_participants, is_in_room, audio_file, "  # noqa: S608
            f"glossary, expected_people_json, language) VALUES ({next_id_sql(MEETINGS[0])}, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (MEETINGS[1], title, started, platform, preset_id, MeetingStatus.RECORDING, expected_participants, int(bool(is_in_room)),
             audio_file, glossary, expected_people_json, whisper_code(language) if language else None))

    def get_meeting(self, meeting_id: int) -> MeetingRow | None:
        rows = self.core.q("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        return cast(MeetingRow, dict(rows[0])) if rows else None

    def list_meetings(self) -> list[MeetingRow]:
        return [cast(MeetingRow, dict(r)) for r in self.core.q("SELECT * FROM meetings ORDER BY id DESC")]

    def overview(self, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
        """Lista do Histórico (F5 item 8) numa consulta: a reunião, o custo somado das chamadas de LLM dela e o job vigente
        (o pipeline concluído mais recente; sem ele, o maior já gravado nos clusters), da mais recente para a mais antiga;
        período pela data da reunião (`AAAA-MM-DD`, inclusivo)."""
        where, params = "", []
        if date_from:
            where += " AND substr(m.started_at, 1, 10) >= ?"
            params.append(date_from)
        if date_to:
            where += " AND substr(m.started_at, 1, 10) <= ?"
            params.append(date_to)
        rows = self.core.q(
            "SELECT m.id, m.title, m.title_llm, m.started_at, m.duration_ms, m.status, m.expected_participants, "  # noqa: S608
            "(SELECT SUM(l.cost_usd) FROM llm_calls l WHERE l.meeting_id = m.id) AS cost_usd, "
            "COALESCE((SELECT MAX(j.id) FROM processing_jobs j WHERE j.meeting_id = m.id AND j.kind IN (?, ?) AND j.status = ?), "
            "(SELECT MAX(c.job_id) FROM clusters c WHERE c.meeting_id = m.id)) AS job_id "
            f"FROM meetings m WHERE 1 = 1{where} ORDER BY m.started_at DESC, m.id DESC", (*PIPELINE_JOB_KINDS, JobStatus.DONE, *params))
        return [dict(r) for r in rows]

    def finish_meeting(self, meeting_id: int, duration_ms: int, status: MeetingStatus = MeetingStatus.RECORDED) -> None:
        self.core.execute("UPDATE meetings SET status = ?, ended_at = ?, duration_ms = ? WHERE id = ?",
                          (status, _now(), int(duration_ms), meeting_id))

    def set_in_room(self, meeting_id: int, in_room: bool) -> bool:
        """F5.9 (item 7, pergunta 2): liga ou desliga "Sala de reunião" na reunião em curso; o runner lê a reunião no começo
        do job, então vale para o pipeline final (o ao vivo segue como começou). True quando a reunião existe."""
        return self.core.execute("UPDATE meetings SET is_in_room = ? WHERE id = ?", (int(bool(in_room)), meeting_id)) > 0

    def set_status(self, meeting_id: int, status: MeetingStatus) -> None:
        self.core.execute("UPDATE meetings SET status = ? WHERE id = ?", (status, meeting_id))

    def mark_processed(self, meeting_id: int, force: bool = False) -> None:
        """Fim do job: `processada`, sem rebaixar `revisada` (o usuário pode concluir a revisão durante o Whisper, R2-11)
        salvo com `force` (vozes novas pendentes)."""
        self.core.execute("UPDATE meetings SET status = ? WHERE id = ? AND (status != ? OR ?)",
                          (MeetingStatus.PROCESSED, meeting_id, MeetingStatus.REVIEWED, int(force)))

    def set_language(self, meeting_id: int, language: str | None) -> None:
        """O idioma do processamento numa reunião que nasceu sem ele (anterior à F5.3: o padrão, A-01); só preenche se ainda
        não houver. A troca pelo usuário é `change_language`."""
        self.core.execute("UPDATE meetings SET language = COALESCE(language, ?) WHERE id = ?", (language, meeting_id))

    def change_language(self, meeting_id: int, language: str, author: str = "usuario") -> bool:
        """F5.3 (A-05): o idioma trocado na reunião aberta, gravado no fim do reprocessamento (código do Whisper), com
        `revisions`; False quando já era esse."""
        m = self.get_meeting(meeting_id)
        if m is None:
            return False
        before, after = m.get("language"), whisper_code(language)
        if before == after:
            return False
        with self.core.transaction():
            self.core.execute("UPDATE meetings SET language = ? WHERE id = ?", (after, meeting_id))
            self.add_revision("meeting", meeting_id, "language", before, after, author)
        return True

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

    def set_title_llm(self, meeting_id: int, title: str, author: str = "llm") -> bool:
        """Título proposto pela LLM (F3 item 6): sobrescreve `title_llm` com `revisions`; o `title` do usuário fica."""
        m = self.get_meeting(meeting_id)
        if m is None:
            return False
        before = m.get("title_llm")
        if before == title:
            return False
        with self.core.transaction():
            self.core.execute("UPDATE meetings SET title_llm = ? WHERE id = ?", (title, meeting_id))
            self.add_revision("meeting", meeting_id, "title_llm", before, title, author)
        return True

    def set_glossary(self, meeting_id: int, glossary: str | None, expected_people_json: str | None) -> None:
        self.core.execute("UPDATE meetings SET glossary = ?, expected_people_json = ? WHERE id = ?",
                          (glossary, expected_people_json, meeting_id))

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

    def cascade_counts(self, meeting_id: int) -> dict[str, int]:
        """F5.6 (A-46): quantas linhas a cascata da reunião vai levar, por tabela (só para o log: ids e contagens)."""
        counts = {t: int(self.core.q(f"SELECT COUNT(*) FROM {t} WHERE meeting_id = ?", (meeting_id,))[0][0])  # noqa: S608
                  for t in CASCADE_TABLES}
        return {t: n for t, n in counts.items() if n}

    def delete_revisions(self, meeting_id: int) -> int:
        """F5.6 (A-33): as revisões da reunião e das vozes, atas e ações dela (esses ids, sem piso, voltam em outras reuniões:
        o histórico apagado não pode grudar nelas); as do perfil de voz e da pessoa ficam. Antes da linha (depois, as filhas
        já sumiram)."""
        n = self.core.execute("DELETE FROM revisions WHERE entity = 'meeting' AND entity_id = ?", (meeting_id,))
        for entity, table in REVISION_ENTITIES:          # nomes fixos deste módulo
            n += self.core.execute(f"DELETE FROM revisions WHERE entity = ? AND entity_id IN (SELECT id FROM {table} WHERE meeting_id = ?)",  # noqa: S608
                                   (entity, meeting_id))
        return n

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
        return self._events(self.core.q("SELECT * FROM events WHERE meeting_id = ? ORDER BY id", (meeting_id,)))

    def list_events_of(self, meeting_id: int, types: Iterable[EventType | str]) -> list[EventRow]:
        """Só os eventos dos tipos pedidos, em ordem (F5.6 A-15: uma leitura por recarga, repartida entre as funções)."""
        kinds = tuple(str(t) for t in types)
        return self._events(self.core.q(f"SELECT * FROM events WHERE meeting_id = ? AND type IN ({','.join('?' * len(kinds))}) "  # noqa: S608
                                        "ORDER BY id", (meeting_id, *kinds)))

    @staticmethod
    def _events(rows: Iterable[Any]) -> list[EventRow]:
        out: list[EventRow] = []
        for r in rows:
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
