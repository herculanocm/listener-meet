"""Store: acesso único aos dois arquivos SQLite, com lock (threads de áudio e interface gravam)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config.defaults import DEFAULTS
from .schema import AUDIO_V1, MAIN_V1, SCHEMA_VERSION

MAIN_FILE = "listener.db"
AUDIO_FILE = "listener-audio.db"


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class Store:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.main_path = self.base_dir / MAIN_FILE
        self.audio_path = self.base_dir / AUDIO_FILE
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.main_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("ATTACH DATABASE ? AS audio", (str(self.audio_path),))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA audio.journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    # ---------------------------------------------------------------- infra
    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(MAIN_V1)
            self._conn.executescript(AUDIO_V1)
            for table in ("schema_migrations", "audio.schema_migrations"):
                self._conn.execute(f"INSERT OR IGNORE INTO {table}(version, applied_at) VALUES (?, ?)",
                                   (SCHEMA_VERSION, _now()))

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._conn.close()

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _x(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return int(cur.lastrowid or 0)

    def db_sizes(self) -> dict[str, int]:
        def size(p: Path) -> int:
            total = 0
            for suffix in ("", "-wal", "-shm"):
                f = Path(str(p) + suffix)
                if f.exists():
                    total += f.stat().st_size
            return total
        return {"main": size(self.main_path), "audio": size(self.audio_path)}

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str, default: str | None = None) -> str:
        rows = self._q("SELECT value FROM settings WHERE key = ?", (key,))
        if rows:
            return rows[0]["value"]
        if default is not None:
            return default
        return DEFAULTS.get(key, ("", "now"))[0]

    def get_int(self, key: str) -> int:
        return int(float(self.get_setting(key)))

    def get_float(self, key: str) -> float:
        return float(self.get_setting(key))

    def set_setting(self, key: str, value: Any, scope: str | None = None) -> None:
        scope = scope or DEFAULTS.get(key, ("", "now"))[1]
        self._x("INSERT INTO settings(key, value, scope, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, scope = excluded.scope, updated_at = excluded.updated_at",
                (key, str(value), scope, _now()))

    def all_settings(self) -> dict[str, str]:
        out = {k: v[0] for k, v in DEFAULTS.items()}
        for r in self._q("SELECT key, value FROM settings"):
            out[r["key"]] = r["value"]
        return out

    # ---------------------------------------------------------------- presets
    def list_presets(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM presets ORDER BY name")]

    def get_preset(self, preset_id: int) -> dict | None:
        rows = self._q("SELECT * FROM presets WHERE id = ?", (preset_id,))
        return dict(rows[0]) if rows else None

    def upsert_preset(self, name: str, mic_device_id: str | None, mic_device_name: str | None, source_kind: str,
                      source_exe: str | None, sample_rate: int = 16000, is_in_room: bool = False) -> int:
        self._x("INSERT INTO presets(name, mic_device_id, mic_device_name, source_kind, source_exe, sample_rate, is_in_room, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET mic_device_id = excluded.mic_device_id, "
                "mic_device_name = excluded.mic_device_name, source_kind = excluded.source_kind, source_exe = excluded.source_exe, "
                "sample_rate = excluded.sample_rate, is_in_room = excluded.is_in_room, updated_at = excluded.updated_at",
                (name, mic_device_id, mic_device_name, source_kind, source_exe, int(sample_rate), int(bool(is_in_room)), _now()))
        return int(self._q("SELECT id FROM presets WHERE name = ?", (name,))[0]["id"])

    def delete_preset(self, preset_id: int) -> None:
        self._x("DELETE FROM presets WHERE id = ?", (preset_id,))

    def seed_default_presets(self) -> None:
        if self.list_presets():
            return
        for name, kind, exe in (("Teams", "app", "ms-teams.exe"), ("Zoom", "app", "Zoom.exe"),
                                ("Meet (navegador)", "app", "msedge.exe"), ("Sistema inteiro", "system", None)):
            self.upsert_preset(name, None, None, kind, exe)

    # ---------------------------------------------------------------- people
    def get_me(self) -> dict | None:
        rows = self._q("SELECT * FROM people WHERE is_me = 1 ORDER BY id LIMIT 1")
        return dict(rows[0]) if rows else None

    def upsert_me(self, name: str, email: str = "", role: str = "", company: str = "") -> int:
        me = self.get_me()
        if me:
            self._x("UPDATE people SET name = ?, email = ?, role = ?, company = ?, updated_at = ? WHERE id = ?",
                    (name, email, role, company, _now(), me["id"]))
            return int(me["id"])
        return self._x("INSERT INTO people(name, email, role, company, is_me, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                       (name, email, role, company, _now(), _now()))

    # ---------------------------------------------------------------- meetings
    def create_meeting(self, title: str | None, platform: str | None, preset_id: int | None,
                       expected_participants: str | None, is_in_room: bool = False) -> int:
        return self._x("INSERT INTO meetings(title, started_at, platform, preset_id, status, expected_participants, is_in_room) "
                       "VALUES (?, ?, ?, ?, 'gravando', ?, ?)",
                       (title, _now(), platform, preset_id, expected_participants, int(bool(is_in_room))))

    def get_meeting(self, meeting_id: int) -> dict | None:
        rows = self._q("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        return dict(rows[0]) if rows else None

    def list_meetings(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM meetings ORDER BY started_at DESC")]

    def set_meeting_status(self, meeting_id: int, status: str) -> None:
        self._x("UPDATE meetings SET status = ? WHERE id = ?", (status, meeting_id))

    def finish_meeting(self, meeting_id: int, duration_ms: int) -> None:
        self._x("UPDATE meetings SET status = 'gravada', ended_at = ?, duration_ms = ? WHERE id = ?",
                (_now(), int(duration_ms), meeting_id))

    def update_title(self, meeting_id: int, title: str, author: str = "usuario") -> None:
        m = self.get_meeting(meeting_id)
        before = (m or {}).get("title")
        if before == title:
            return
        self._x("UPDATE meetings SET title = ? WHERE id = ?", (title, meeting_id))
        self.add_revision("meeting", meeting_id, "title", before, title, author)

    def mark_interrupted(self) -> int:
        """Na abertura: reuniões que ficaram 'gravando' viram 'interrompida' (pedaços preservados)."""
        with self._lock:
            cur = self._conn.execute("UPDATE meetings SET status = 'interrompida' WHERE status = 'gravando'")
            return cur.rowcount

    def delete_meeting(self, meeting_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM audio.audio_chunks WHERE meeting_id = ?", (meeting_id,))
            self._conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))

    # ---------------------------------------------------------------- chunks (audio.db)
    def insert_chunk(self, meeting_id: int, channel: str, idx: int, t_start_ms: int, frames: int,
                     sample_rate: int, gap_frames: int, flac: bytes) -> int:
        return self._x("INSERT INTO audio.audio_chunks(meeting_id, channel, idx, t_start_ms, frames, sample_rate, gap_frames, flac, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (meeting_id, channel, idx, int(t_start_ms), int(frames), int(sample_rate), int(gap_frames), sqlite3.Binary(flac), _now()))

    def list_chunks(self, meeting_id: int, channel: str | None = None) -> list[dict]:
        if channel:
            rows = self._q("SELECT id, meeting_id, channel, idx, t_start_ms, frames, sample_rate, gap_frames, length(flac) AS bytes "
                           "FROM audio.audio_chunks WHERE meeting_id = ? AND channel = ? ORDER BY idx", (meeting_id, channel))
        else:
            rows = self._q("SELECT id, meeting_id, channel, idx, t_start_ms, frames, sample_rate, gap_frames, length(flac) AS bytes "
                           "FROM audio.audio_chunks WHERE meeting_id = ? ORDER BY channel, idx", (meeting_id,))
        return [dict(r) for r in rows]

    def get_chunks_range(self, meeting_id: int, channel: str, t0_ms: int, t1_ms: int) -> list[dict]:
        rows = self._q("SELECT idx, t_start_ms, frames, sample_rate, flac FROM audio.audio_chunks "
                       "WHERE meeting_id = ? AND channel = ? AND t_start_ms < ? AND (t_start_ms + frames * 1000 / sample_rate) > ? "
                       "ORDER BY idx", (meeting_id, channel, int(t1_ms), int(t0_ms)))
        return [dict(r) for r in rows]

    def chunk_stats(self, meeting_id: int) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self._q("SELECT channel, COUNT(*) AS n, SUM(frames) AS frames, SUM(gap_frames) AS gaps, SUM(length(flac)) AS bytes, "
                         "MIN(t_start_ms) AS t0, MAX(t_start_ms + frames * 1000 / sample_rate) AS t1, MAX(sample_rate) AS sr "
                         "FROM audio.audio_chunks WHERE meeting_id = ? GROUP BY channel", (meeting_id,)):
            out[r["channel"]] = dict(r)
        return out

    def continuity(self, meeting_id: int, channel: str, tolerance_ms: int = 50) -> list[dict]:
        """Buracos ou sobreposições entre pedaços consecutivos (para o gate G1)."""
        issues = []
        prev_end = None
        for c in self.list_chunks(meeting_id, channel):
            if prev_end is not None and abs(c["t_start_ms"] - prev_end) > tolerance_ms:
                issues.append({"idx": c["idx"], "expected_ms": prev_end, "got_ms": c["t_start_ms"],
                               "delta_ms": c["t_start_ms"] - prev_end})
            prev_end = c["t_start_ms"] + int(round(c["frames"] * 1000 / c["sample_rate"]))
        return issues

    # ---------------------------------------------------------------- markers, events, revisions
    def add_marker(self, meeting_id: int, t_ms: int, note: str = "") -> int:
        return self._x("INSERT INTO markers(meeting_id, t_ms, note, created_at) VALUES (?, ?, ?, ?)",
                       (meeting_id, int(t_ms), note or "", _now()))

    def list_markers(self, meeting_id: int) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM markers WHERE meeting_id = ? ORDER BY t_ms", (meeting_id,))]

    def add_event(self, meeting_id: int | None, t_ms: int | None, type_: str, data: dict | None = None) -> int:
        return self._x("INSERT INTO events(meeting_id, t_ms, type, data, created_at) VALUES (?, ?, ?, ?, ?)",
                       (meeting_id, t_ms, type_, json.dumps(data or {}, ensure_ascii=False, default=str), _now()))

    def list_events(self, meeting_id: int) -> list[dict]:
        out = []
        for r in self._q("SELECT * FROM events WHERE meeting_id = ? ORDER BY id", (meeting_id,)):
            d = dict(r)
            try:
                d["data"] = json.loads(d["data"] or "{}")
            except ValueError:
                pass
            out.append(d)
        return out

    def add_revision(self, entity: str, entity_id: int, field: str, before: Any, after: Any, author: str) -> int:
        return self._x("INSERT INTO revisions(entity, entity_id, field, before, after, author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (entity, entity_id, field, None if before is None else str(before), None if after is None else str(after), author, _now()))
