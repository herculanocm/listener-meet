"""F5.27 L1 (D-113): o banco da tela. O arquivo mensal da tela criado e migrado só pelo app, no mês da reunião, com as páginas
grandes, o `auto_vacuum` incremental e o WAL, e anexado sob demanda pela reunião; as leituras de uma reunião sem tela não criam
arquivo; o filho (WORKER) grava no arquivo que o app criou, sem criar nem migrar, e o app e o `--check` leem; a v12 com a
volta, que deixa os arquivos e religa as reuniões na ida seguinte; excluir a reunião apaga os pedaços dela, e só os dela;
apagar a tela não toca o áudio e devolve o disco; os tamanhos; o limite de anexos dividido entre áudio e tela; os órfãos só
reportados; as chaves, os eventos e o PyAV declarado."""
from __future__ import annotations

import os
import sqlite3
import tomllib
from contextlib import closing
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from listener.config.defaults import SPECS, validate_setting
from listener.model import EventType
from listener.pipeline import delete
from listener.store import Store, space
from listener.store import core as store_core
from listener.store import downgrade as down
from listener.store.core import MAX_ATTACHED
from listener.store.schema import MAIN_MIGRATIONS
from listener.store.screen import ScreenDeleted, attach
from listener.store.screen_file import SCREEN_SCHEMA_VERSION, screen_file_for, screen_schema_for

ROOT = Path(__file__).resolve().parent.parent
MARCH = "2026-03-10T10:00:00.000-03:00"
FILE = "listener-screen-2026-03.db"


def _meeting(store: Store, started_at: str = MARCH) -> int:
    mid = store.create_meeting("Comitê", None, None, None)
    store._x("UPDATE meetings SET started_at = ? WHERE id = ?", (started_at, mid))
    store.finish_meeting(mid, 120_000)                                       # gravando, a exclusão e o espaço recusam
    return mid


def _chunk(store: Store, mid: int, idx: int, data: bytes = b"mp4") -> int:
    return store.screen.insert_chunk(mid, idx=idx, t_start_ms=idx * 60_000, t_end_ms=(idx + 1) * 60_000, frames=60, width=1920,
                                     height=1080, codec="libx264", data=data)


def _rows(folder: Path, file: str = FILE) -> list[tuple[int, int]]:
    with closing(sqlite3.connect(folder / file)) as c:
        return [(int(r[0]), int(r[1])) for r in c.execute("SELECT meeting_id, idx FROM screen_chunks ORDER BY meeting_id, idx")]


# ---------------------------------------------------------------- o arquivo
def test_the_app_creates_the_screen_file_of_the_meeting_month_and_reads_attach_on_demand(store):
    mid, other = _meeting(store), _meeting(store)
    assert store.screen.file_for(mid) is None and store.screen.list_chunks(mid) == [] and store.screen.total_bytes(mid) == 0
    assert store.screen.get_chunk(mid, 0) is None and store.screen.files() == []                # ler não cria arquivo
    assert store.screen.ensure_file(mid) == FILE == screen_file_for(MARCH) and store.get_meeting(mid)["screen_file"] == FILE
    assert store.screen.ensure_file(mid) == FILE and store.screen.ensure_file(other) == FILE                    # um por mês
    with closing(sqlite3.connect(store.base_dir / FILE)) as c:
        assert c.execute("PRAGMA page_size").fetchone()[0] == 32768 and c.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == SCREEN_SCHEMA_VERSION
    store.core.release_audio()                                                 # sem anexo: a leitura anexa de novo
    _chunk(store, mid, 0, b"a" * 10)
    assert screen_schema_for(FILE) in store.core._local.attached and store.screen.total_bytes(mid) == 10
    with pytest.raises(LookupError):
        store.screen.ensure_file(999)
    with pytest.raises(ValueError, match="inesperado"):
        screen_schema_for("listener-screen-2026-03;drop.db")


def test_the_worker_writes_in_the_file_the_app_created_and_the_app_and_the_check_read(store):
    mid, bare = _meeting(store), _meeting(store)
    store.screen.ensure_file(mid)
    with Store.open_worker(store.base_dir) as w:
        assert _chunk(w, mid, 1, b"segundo") and _chunk(w, mid, 0, b"primeiro")
        with pytest.raises(sqlite3.IntegrityError):
            _chunk(w, mid, 0, b"de novo")                                      # nada é sobrescrito
        with pytest.raises(ValueError, match="inválido"):
            w.screen.insert_chunk(mid, idx=2, t_start_ms=10, t_end_ms=5, frames=1, width=10, height=10, codec="x", data=b"")
        with pytest.raises(RuntimeError, match="só o app"):
            w.screen.ensure_file(bare)
        with pytest.raises(sqlite3.OperationalError, match="não tem arquivo da tela"):
            _chunk(w, bare, 0)
    chunks = store.screen.list_chunks(mid)
    assert [(c["idx"], c["t_start_ms"], c["t_end_ms"], c["bytes"], c["codec"]) for c in chunks] == [
        (0, 0, 60_000, 8, "libx264"), (1, 60_000, 120_000, 7, "libx264")]
    assert store.screen.get_chunk(mid, 1) == b"segundo" and store.screen.get_chunk(mid, 5) is None
    with Store.open_readonly(store.base_dir) as ro:
        assert [c["idx"] for c in ro.screen.list_chunks(mid)] == [0, 1] and ro.screen.total_bytes(mid) == 15


def test_the_worker_never_creates_a_screen_file(store):
    mid = _meeting(store)
    store._x("UPDATE meetings SET screen_file = ? WHERE id = ?", (FILE, mid))           # a reunião aponta, o arquivo sumiu
    with Store.open_worker(store.base_dir) as w:
        with pytest.raises(sqlite3.OperationalError):
            _chunk(w, mid, 0)
        with pytest.raises(FileNotFoundError, match="ausente"):
            attach(w.core, FILE)
    assert not (store.base_dir / FILE).exists() and store.screen.missing_files() == [FILE]


# ---------------------------------------------------------------- a v12
def test_v12_on_a_v11_database_the_way_back_keeps_the_files_and_the_way_up_relinks(tmp_path, monkeypatch):
    monkeypatch.setattr(store_core, "MAIN_MIGRATIONS", MAIN_MIGRATIONS[:11])
    monkeypatch.setattr(store_core, "MAIN_SCHEMA_VERSION", 11)
    with Store(tmp_path) as old:
        mid = _meeting(old)
        assert old.screen.file_for(mid) is None and old.screen.list_chunks(mid) == []        # a v11 não tem a coluna
    monkeypatch.undo()
    with Store(tmp_path) as s:
        assert s.schema_version() == 12 and s.get_meeting(mid)["screen_file"] is None
        s.screen.ensure_file(mid)
        _chunk(s, mid, 0)
    assert down.downgrade(tmp_path, 11) == [12]
    with closing(sqlite3.connect(tmp_path / "listener.db")) as c:
        assert "screen_file" not in {r[1] for r in c.execute("PRAGMA table_info(meetings)")}
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 11
    assert _rows(tmp_path) == [(mid, 0)]                                     # os pedaços ficam na pasta (CP3)
    with Store(tmp_path) as again:
        assert again.schema_version() == 12 and again.screen.file_for(mid) == FILE
        assert [c["idx"] for c in again.screen.list_chunks(mid)] == [0]


# ---------------------------------------------------------------- apagar
def test_deleting_the_meeting_deletes_its_screen_chunks_and_only_its_own(store):
    mid, other = _meeting(store), _meeting(store)
    for m in (mid, other):
        store.screen.ensure_file(m)
        _chunk(store, m, 0)
        _chunk(store, m, 1)
    out = delete.delete_meeting(store, mid)
    assert out.deleted and store.get_meeting(mid) is None
    assert _rows(store.base_dir) == [(other, 0), (other, 1)] and FILE in space.pending(store.core)


def test_deleting_the_screen_keeps_the_audio_and_gives_the_disk_back(store):
    mid = _meeting(store)
    store.insert_chunk(mid, "R", 0, 0, 16000, 16000, 0, b"flac")
    audio_before = store.list_chunks(mid)
    store.screen.ensure_file(mid)
    for idx in range(3):
        _chunk(store, mid, idx, os.urandom(1_000_000))
    assert space.file_bytes(store.base_dir / FILE) > 3_000_000
    assert store.screen.delete_for_meeting(mid) == ScreenDeleted(3, 3_000_000)
    assert store.screen.list_chunks(mid) == [] and store.screen.file_for(mid) == FILE and store.get_meeting(mid) is not None
    assert store.list_chunks(mid) == audio_before and store.screen.delete_for_meeting(mid) == ScreenDeleted()
    store.core.release_audio()                                               # como a interface antes do espaço (R-02)
    freed = {r.file: r for r in delete.free_pending(store, recording=lambda: False)}
    assert freed[FILE].outcome == space.SpaceOutcome.FREED and freed[FILE].freed_bytes > 2_000_000
    assert space.file_bytes(store.base_dir / FILE) < 1_000_000


def test_the_sizes_count_the_screen_files(store):
    mid = _meeting(store)
    assert store.db_sizes()["screen"] == 0 and store.db_sizes()["screen_files"] == {}
    store.screen.ensure_file(mid)
    _chunk(store, mid, 0, b"x" * 50_000)
    sizes = store.db_sizes()
    on_disk = sum(p.stat().st_size for p in store.base_dir.glob(f"{FILE}*"))
    assert sizes["screen_files"] == {FILE: on_disk} and sizes["screen"] == on_disk >= 50_000
    assert store.screen.total_bytes(mid) == 50_000 and set(sizes["audio_files"]).isdisjoint(sizes["screen_files"])


# ---------------------------------------------------------------- o limite de anexos
def _attached(store: Store) -> set[str]:
    return {str(r[1]) for r in store.core.conn().execute("PRAGMA database_list")} - {"main", "temp"}


def test_audio_and_screen_share_the_attach_limit_and_the_export_gets_both(store):
    mid = store.create_meeting("Exportar", None, None, None)                # o mês corrente: o áudio e a tela da reunião
    store.insert_chunk(mid, "R", 0, 0, 16000, 16000, 0, b"flac")
    store.screen.ensure_file(mid)
    _chunk(store, mid, 0)
    for month in range(1, MAX_ATTACHED + 1):                                 # oito meses de áudio empurram os dois para fora
        store.core.attach(f"listener-audio-2025-{month:02d}.db")
        assert len(_attached(store)) <= MAX_ATTACHED
    assert len(_attached(store)) == MAX_ATTACHED and screen_schema_for(store.screen.file_for(mid)) not in _attached(store)
    a = store.core.audio_schema_for_meeting(mid)
    s = attach(store.core, store.screen.file_for(mid))
    assert {a, s} <= _attached(store) and len(_attached(store)) == MAX_ATTACHED
    row = store._q(f"SELECT (SELECT COUNT(*) FROM {a}.audio_chunks WHERE meeting_id = ?), "  # noqa: S608
                   f"(SELECT COUNT(*) FROM {s}.screen_chunks WHERE meeting_id = ?)", (mid, mid))[0]
    assert tuple(row) == (1, 1)
    store.core.release_audio()                                               # o worker ocioso solta também a tela
    assert _attached(store) == set()
    with store.snapshot(), pytest.raises(RuntimeError, match="fora de transação"):
        store.screen.list_chunks(mid)                                        # anexar dentro da leitura consistente: recusado
    assert store.screen.total_bytes(mid) == 3
    with store.snapshot():
        assert [c["idx"] for c in store.screen.list_chunks(mid)] == [0]      # já anexado: lê


# ---------------------------------------------------------------- diagnóstico, chaves, eventos e PyAV
def test_orphans_are_only_reported(store):
    mid = _meeting(store)
    store.screen.ensure_file(mid)
    _chunk(store, mid, 0)
    with closing(sqlite3.connect(store.base_dir / FILE)) as c, c:
        c.execute("INSERT INTO screen_chunks(meeting_id, idx, t_start_ms, t_end_ms, frames, width, height, codec, mp4) "
                  "VALUES (999, 0, 0, 1, 1, 1, 1, 'x', x'00')")
    assert store.screen.orphan_report() == {FILE: [999]} and _rows(store.base_dir) == [(mid, 0), (999, 0)]


def test_the_settings_the_events_and_pyav_declared():
    fps, side, monitor = SPECS["screen_rec.fps"], SPECS["screen_rec.max_side"], SPECS["screen_rec.monitor"]
    assert (fps.default, fps.scope, side.default, side.scope) == (1, "now", 1920, "now") and not fps.internal and not side.internal
    assert validate_setting("screen_rec.fps", "6") and validate_setting("screen_rec.fps", "5") is None
    assert validate_setting("screen_rec.max_side", "800") and validate_setting("screen_rec.max_side", "3840") is None
    assert monitor.default == "" and monitor.internal
    assert [e.value for e in (EventType.SCREEN_START, EventType.SCREEN_STOP, EventType.SCREEN_LOST, EventType.SCREEN_SWITCH)] == [
        "screen_start", "screen_stop", "screen_lost", "screen_switch"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    (pin,) = [d for d in project["project"]["optional-dependencies"]["video"] if d.startswith("av")]
    lock = [line.split("==")[1].strip() for line in (ROOT / "requirements/windows.txt").read_text(encoding="utf-8").splitlines()
            if line.startswith("av==")]
    assert lock == ["19.0.2"] and SpecifierSet(pin.removeprefix("av")).contains(lock[0])
