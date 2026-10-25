"""Fachada `Store`: os repositórios por domínio (`settings`, `meetings`, `audio`, `people`; F2: `jobs`, `profiles`,
`segments`; F3: `providers`, `summaries`, `actions`, `llm_calls`, `chat`; F4: `captions`) atrás dos nomes de método da F1. Uma conexão por
thread (M-02/M-18); `with Store(dir) as s:` fecha ao sair; `Store.open_readonly(dir)` para o `--check`;
`Store.open_worker(dir)` para o worker da F2 (`StoreRole`)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from sqlite3 import Connection
from typing import TYPE_CHECKING, Any, Self

from ..log import get_logger
from ..model import EventType, MeetingStatus, StoreRole
from .actions import ActionsRepo
from .audio import AudioRepo, ChannelStats, ChunkData, ChunkMeta, ContinuityIssue
from .captions import CaptionsRepo
from .chat import ChatRepo
from .core import LEGACY_AUDIO_FILE, MAIN_FILE, Core, with_offset
from .jobs import JobsRepo
from .llm_calls import LlmCallsRepo
from .meetings import EventRow, MarkerRow, MeetingRow, MeetingsRepo, PresetRow
from .people import PeopleRepo, PersonRow
from .profiles import ProfilesRepo
from .providers import ProvidersRepo
from .segments import SegmentsRepo
from .settings import SettingsRepo
from .summaries import SummariesRepo

if TYPE_CHECKING:
    from ..capture.sinks import EncodedChunk
    from ..config.defaults import SettingSpec

__all__ = ["LEGACY_AUDIO_FILE", "MAIN_FILE", "Store", "with_offset"]

log = get_logger("store")


class Store:
    def __init__(self, base_dir: str | Path, role: StoreRole = StoreRole.APP):
        self.core = Core(base_dir, role=role)
        self.settings = SettingsRepo(self.core)
        self.meetings = MeetingsRepo(self.core)
        self.audio = AudioRepo(self.core)
        self.people = PeopleRepo(self.core)
        self.jobs = JobsRepo(self.core)
        self.profiles = ProfilesRepo(self.core)
        self.segments = SegmentsRepo(self.core)
        self.providers = ProvidersRepo(self.core)
        self.summaries = SummariesRepo(self.core)
        self.actions = ActionsRepo(self.core)
        self.llm_calls = LlmCallsRepo(self.core)
        self.chat = ChatRepo(self.core)
        self.captions = CaptionsRepo(self.core)

    @classmethod
    def open_readonly(cls, base_dir: str | Path) -> Self:
        """Abre com `mode=ro` (nada é migrado nem escrito): diagnóstico (`--check`) e leitores de outro processo."""
        return cls(base_dir, role=StoreRole.READONLY)

    @classmethod
    def open_worker(cls, base_dir: str | Path) -> Self:
        """Worker da F2: main em escrita sem migrar nem VACUUM; áudio só leitura com mmap (recusa banco não migrado)."""
        return cls(base_dir, role=StoreRole.WORKER)

    # ---------------------------------------------------------------- infra
    @property
    def base_dir(self) -> Path:
        return self.core.base_dir

    @property
    def main_path(self) -> Path:
        return self.core.main_path

    @property
    def readonly(self) -> bool:
        return self.core.readonly

    @property
    def role(self) -> StoreRole:
        return self.core.role

    def schema_version(self) -> int:
        return self.core.main_version

    def default_log_path(self) -> Path:
        return self.base_dir / "logs" / "listener.log"

    def transaction(self) -> Any:
        return self.core.transaction()

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        yield self.core.conn()

    def close(self) -> None:
        self.core.close()

    def close_thread(self) -> None:
        self.core.close_thread()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _q(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        return self.core.q(sql, params)

    def _x(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        return self.core.execute(sql, params)

    def db_sizes(self) -> dict[str, Any]:
        return self.core.db_sizes()

    def audio_files(self) -> list[str]:
        return self.core.audio_files()

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str, default: str | None = None) -> str:
        return self.settings.get_setting(key, default)

    def get[T](self, spec: SettingSpec[T]) -> T:
        return self.settings.get(spec)

    def get_int(self, key: str) -> int:
        return self.settings.get_int(key)

    def get_float(self, key: str) -> float:
        return self.settings.get_float(key)

    def set_setting(self, key: str, value: Any, scope: str | None = None) -> None:
        self.settings.set_setting(key, value, scope)

    def all_settings(self) -> dict[str, str]:
        return self.settings.all_settings()

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        return self.settings.get_provider(provider_id)

    def upsert_provider(self, provider_id: str, kind: str, api_key: str | None = None, model: str | None = None,
                        params: str | None = None) -> None:
        self.settings.upsert_provider(provider_id, kind, api_key, model, params)

    # ---------------------------------------------------------------- presets e pessoas
    def list_presets(self) -> list[PresetRow]:
        return self.meetings.list_presets()

    def get_preset(self, preset_id: int) -> PresetRow | None:
        return self.meetings.get_preset(preset_id)

    def upsert_preset(self, name: str, mic_device_id: str | None, mic_device_name: str | None, source_kind: str,
                      source_exe: str | None, sample_rate: int = 16000, is_in_room: bool = False) -> int:
        return self.meetings.upsert_preset(name, mic_device_id, mic_device_name, source_kind, source_exe, sample_rate, is_in_room)

    def delete_preset(self, preset_id: int) -> bool:
        return self.meetings.delete_preset(preset_id)

    def seed_default_presets(self) -> None:
        self.meetings.seed_default_presets()

    def get_me(self) -> PersonRow | None:
        return self.people.get_me()

    def upsert_me(self, name: str, email: str = "", role: str = "", company: str = "") -> int:
        return self.people.upsert_me(name, email, role, company)

    # ---------------------------------------------------------------- reuniões
    def create_meeting(self, title: str | None, platform: str | None, preset_id: int | None,
                       expected_participants: str | None, is_in_room: bool = False, glossary: str | None = None,
                       expected_people_json: str | None = None) -> int:
        return self.meetings.create_meeting(title, platform, preset_id, expected_participants, is_in_room, glossary, expected_people_json)

    def get_meeting(self, meeting_id: int) -> MeetingRow | None:
        return self.meetings.get_meeting(meeting_id)

    def list_meetings(self) -> list[MeetingRow]:
        return self.meetings.list_meetings()

    def finish_meeting(self, meeting_id: int, duration_ms: int, status: MeetingStatus = MeetingStatus.RECORDED) -> None:
        self.meetings.finish_meeting(meeting_id, duration_ms, status)

    def update_title(self, meeting_id: int, title: str, author: str = "usuario") -> None:
        self.meetings.update_title(meeting_id, title, author)

    def mark_interrupted(self) -> int:
        """Na abertura: reuniões que ficaram 'gravando' viram 'interrompida', com a duração dos pedaços (M-05)."""
        n = 0
        for mid in self.meetings.interrupted_candidates():
            try:
                covered = self.audio.covered_ms(mid)
            except Exception as e:
                log.warning("reunião %s: não li os pedaços para calcular a duração: %s", mid, e)
                covered = None
            self.meetings.set_interrupted(mid, covered)
            n += 1
        return n

    def delete_meeting(self, meeting_id: int) -> None:
        """Pedaços primeiro, depois a linha: uma falha no meio deixa uma reunião sem áudio, nunca áudio órfão."""
        self.core.audio_schema_for_meeting(meeting_id)     # anexa fora da transação
        with self.core.transaction():
            self.audio.delete_chunks(meeting_id)
            self.meetings.delete_meeting_row(meeting_id)
        self.core.forget_meeting(meeting_id)

    # ---------------------------------------------------------------- pedaços
    def insert_chunk(self, meeting_id: int, channel: str, idx: int, t_start_ms: int, frames: int,
                     sample_rate: int, gap_frames: int, flac: bytes) -> int:
        return self.audio.insert_chunk(meeting_id, channel, idx, t_start_ms, frames, sample_rate, gap_frames, flac)

    def write_chunk(self, chunk: EncodedChunk) -> None:
        self.audio.write_chunk(chunk)

    def list_chunks(self, meeting_id: int, channel: str | None = None) -> list[ChunkMeta]:
        return self.audio.list_chunks(meeting_id, channel)

    def get_chunks_range(self, meeting_id: int, channel: str, t0_ms: int, t1_ms: int) -> list[ChunkData]:
        return self.audio.get_chunks_range(meeting_id, channel, t0_ms, t1_ms)

    def chunk_stats(self, meeting_id: int) -> dict[str, ChannelStats]:
        return self.audio.chunk_stats(meeting_id)

    def continuity(self, meeting_id: int, channel: str, tolerance_ms: int = 50) -> list[ContinuityIssue]:
        return self.audio.continuity(meeting_id, channel, tolerance_ms)

    def orphan_report(self) -> dict[str, list[int]]:
        return self.audio.orphan_report()

    def missing_audio_files(self) -> list[str]:
        """Arquivos de áudio citados por reuniões e ausentes da pasta (apagados ou não restaurados): só reporta (CP3)."""
        return [f for f in self.meetings.audio_files_referenced() if not (self.base_dir / f).exists()]

    # ---------------------------------------------------------------- marcadores, eventos, revisões
    def add_marker(self, meeting_id: int, t_ms: int, note: str = "") -> int:
        return self.meetings.add_marker(meeting_id, t_ms, note)

    def list_markers(self, meeting_id: int) -> list[MarkerRow]:
        return self.meetings.list_markers(meeting_id)

    def add_event(self, meeting_id: int | None, t_ms: int | None, type_: EventType | str, data: dict[str, Any] | None = None) -> int:
        return self.meetings.add_event(meeting_id, t_ms, type_, data)

    def list_events(self, meeting_id: int) -> list[EventRow]:
        return self.meetings.list_events(meeting_id)

    def add_revision(self, entity: str, entity_id: int, field: str, before: Any, after: Any, author: str) -> int:
        return self.meetings.add_revision(entity, entity_id, field, before, after, author)
