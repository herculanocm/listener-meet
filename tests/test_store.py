import re
import sqlite3
import threading

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.store import Store
from listener.store import core as store_core
from listener.store.core import LEGACY_AUDIO_FILE, AUDIO_PAGE_SIZE, audio_file_for, json_default, schema_for, with_offset
from listener.store.playback import decode_range


def _flac(n=16000, value=0.0):
    return encode_flac(np.full(n, value, np.float32), 16000)


def test_monthly_audio_file_and_schema(tmp_path):
    s = Store(tmp_path / "db")
    current = s.core.current_audio_file()
    assert (tmp_path / "db" / "listener.db").exists() and (tmp_path / "db" / current).exists()
    assert re.fullmatch(r"listener-audio-\d{4}-\d{2}\.db", current) and s.audio_files() == [current]
    assert s.schema_version() == 4
    with s.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 4
        assert c.execute(f"PRAGMA {schema_for(current)}.page_size").fetchone()[0] == AUDIO_PAGE_SIZE
        assert c.execute(f"PRAGMA {schema_for(current)}.journal_mode").fetchone()[0] == "wal"
        assert c.execute(f"PRAGMA {schema_for(current)}.synchronous").fetchone()[0] == 1
        assert c.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert s.get_setting("capture.chunk_seconds") == "60.0"
    s.set_setting("capture.chunk_seconds", 30)
    assert s.get_int("capture.chunk_seconds") == 30
    assert s.all_settings()["hotkeys.start"] == "ctrl+alt+shift+r"
    s.close()
    with Store(tmp_path / "db") as s2:           # reabrir migra sem erro e mantém dados
        assert s2.get_int("capture.chunk_seconds") == 30
    with pytest.raises(RuntimeError):
        s2.list_meetings()                        # fechado


def test_audio_file_names():
    assert audio_file_for("2026-10-24T18:03:12.345-03:00") == "listener-audio-2026-10.db"
    assert schema_for("listener-audio-2026-10.db") == "audio_2026_10" and schema_for(LEGACY_AUDIO_FILE) == "audio"
    with pytest.raises(ValueError):
        schema_for("listener-audio-x.db")


def test_people_presets(tmp_path):
    s = Store(tmp_path)
    assert s.get_me() is None
    me = s.upsert_me("Fulano", "f@x.com", "CTO", "ACME")
    assert s.get_me()["id"] == me and s.get_me()["role"] == "CTO"
    s.upsert_me("Fulano B")
    assert s.get_me()["name"] == "Fulano B" and len(s._q("SELECT * FROM people")) == 1
    s.seed_default_presets()
    names = [p["name"] for p in s.list_presets()]
    assert "Teams" in names and "Sistema inteiro" in names
    pid = s.upsert_preset("Meu", "3", "Mic", "app", "zoom.exe", is_in_room=True)
    assert s.get_preset(pid)["is_in_room"] == 1
    assert s.delete_preset(pid) is True and s.delete_preset(pid) is False
    assert s.get_preset(pid) is None
    s.close()


def test_meeting_chunks_markers_events_and_cascade(tmp_path):
    s = Store(tmp_path)
    mid = s.create_meeting("reunião", "teams", None, "Ana, Bia")
    assert s.get_meeting(mid)["status"] == "gravando" and s.get_meeting(mid)["audio_file"] == s.core.current_audio_file()
    tone = (0.2 * np.sin(np.linspace(0, 100, 16000))).astype(np.float32)
    for idx in range(3):
        s.insert_chunk(mid, "L", idx, idx * 1000, 16000, 16000, 0, encode_flac(tone, 16000))
    s.insert_chunk(mid, "R", 0, 0, 16000, 16000, 4000, _flac())
    s.add_marker(mid, 1500, "ponto")
    s.add_event(mid, 0, "start", {"source": "system", "conf": np.float32(0.5), "n": np.int64(3)})
    s.add_event(mid, 2900, "stop", {})
    s.finish_meeting(mid, 3000)
    st = s.chunk_stats(mid)
    assert st["L"]["n"] == 3 and st["L"]["frames"] == 48000 and st["R"]["gaps"] == 4000
    assert s.continuity(mid, "L") == []
    samples, sr = decode_range(s, mid, "L", 500, 2500)
    assert sr == 16000 and len(samples) == 32000
    assert s.list_markers(mid)[0]["note"] == "ponto"
    assert [e["type"] for e in s.list_events(mid)] == ["start", "stop"]
    assert s.list_events(mid)[0]["data"] == {"source": "system", "conf": 0.5, "n": 3}    # numpy vira número, não texto
    s.update_title(mid, "novo título")
    assert s.get_meeting(mid)["title"] == "novo título"
    assert s._q("SELECT * FROM revisions")[0]["after"] == "novo título"
    assert s.list_meetings()[0]["id"] == mid
    s.delete_meeting(mid)
    assert s.get_meeting(mid) is None and s.list_chunks(mid) == [] and s.list_markers(mid) == []
    assert s.orphan_report() == {}
    s.close()


def test_json_default_refuses_unknown_types():
    with pytest.raises(TypeError):
        json_default(object())
    assert json_default(np.arange(2)) == [0, 1]


def test_continuity_detects_gap_and_interrupted_gets_duration(tmp_path):
    s = Store(tmp_path)
    mid = s.create_meeting(None, None, None, None)
    s.insert_chunk(mid, "L", 0, 0, 16000, 16000, 0, _flac())
    s.insert_chunk(mid, "L", 1, 2500, 16000, 16000, 0, _flac())
    issues = s.continuity(mid, "L")
    assert len(issues) == 1 and issues[0]["delta_ms"] == 1500
    assert s.mark_interrupted() == 1
    m = s.get_meeting(mid)
    assert m["status"] == "interrompida" and m["duration_ms"] == 3500 and m["ended_at"]
    s.close()


def test_decode_range_positions_non_contiguous_chunks_by_time(tmp_path):
    s = Store(tmp_path)
    mid = s.create_meeting(None, None, None, None)
    s.insert_chunk(mid, "L", 0, 0, 16000, 16000, 0, _flac(value=0.5))
    s.insert_chunk(mid, "L", 1, 2500, 16000, 16000, 0, _flac(value=0.5))   # buraco de 1,5 s
    samples, sr = decode_range(s, mid, "L", 0, 3500)
    assert sr == 16000 and len(samples) == 56000
    assert abs(samples[8000] - 0.5) < 1e-3 and samples[24000] == 0.0 and abs(samples[48000] - 0.5) < 1e-3
    assert decode_range(s, mid, "L", 1000, 2000)[0].size == 0             # pedaço termina exatamente em t0: fora
    assert decode_range(s, mid, "L", 999, 1001)[0].size == 32              # um ms dentro: entra
    s.close()


def test_dates_have_offset_and_migration_v2_rewrites_old_rows(tmp_path):
    s = Store(tmp_path)
    me = s.upsert_me("Fulano")
    assert re.search(r"[+-]\d\d:\d\d$", s.get_me()["created_at"])
    # simula um banco da F1: data sem fuso e versão 1 no main
    s._x("UPDATE people SET created_at = '2026-10-24T18:03:12.345' WHERE id = ?", (me,))
    s._x("DELETE FROM schema_migrations WHERE version > 1")
    s.close()
    s2 = Store(tmp_path)
    assert s2.schema_version() == 4
    created = s2.get_me()["created_at"]
    assert created.startswith("2026-10-24T18:03:12.345") and re.search(r"[+-]\d\d:\d\d$", created)
    assert with_offset("") == "" and with_offset(None) is None and with_offset("lixo") == "lixo"
    assert with_offset("2026-10-24T18:03:12.345-03:00") == "2026-10-24T18:03:12.345-03:00"
    s2.close()


def test_store_refuses_newer_schema(tmp_path):
    s = Store(tmp_path)
    s._x("INSERT INTO schema_migrations(version, applied_at) VALUES (99, 'x')")
    s.close()
    with pytest.raises(RuntimeError, match="atualize o app"):
        Store(tmp_path)


def test_legacy_audio_file_is_adopted_vacuumed_and_kept(tmp_path):
    """Banco da F1 (v2, um só arquivo de áudio com páginas de 4 KB): v3 aponta as reuniões antigas para ele."""
    main = sqlite3.connect(tmp_path / "listener.db", autocommit=True)
    main.execute("PRAGMA journal_mode = WAL")
    main.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    main.execute("INSERT INTO schema_migrations VALUES (1, 'x'), (2, 'x')")
    for sql in store_core.MAIN_MIGRATIONS[0][1]:
        main.execute(sql)
    main.execute("INSERT INTO meetings(id, started_at, status) VALUES (7, '2026-09-01T10:00:00.000-03:00', 'gravada')")
    main.close()
    legacy = sqlite3.connect(tmp_path / LEGACY_AUDIO_FILE, autocommit=True)
    legacy.execute("PRAGMA journal_mode = WAL")
    legacy.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    legacy.execute("INSERT INTO schema_migrations VALUES (1, 'x'), (2, 'x')")
    for sql in store_core.AUDIO_MIGRATIONS[0][1]:
        legacy.execute(sql.format(s="main"))
    legacy.execute("INSERT INTO audio_chunks(meeting_id, channel, idx, t_start_ms, frames, sample_rate, gap_frames, flac, created_at) "
                   "VALUES (7, 'L', 0, 0, 16000, 16000, 0, ?, 'x'), (99, 'L', 0, 0, 16000, 16000, 0, ?, 'x')", (_flac(), _flac()))
    assert legacy.execute("PRAGMA page_size").fetchone()[0] == 4096
    legacy.close()
    with Store(tmp_path) as s:
        assert s.get_meeting(7)["audio_file"] == LEGACY_AUDIO_FILE
        assert s.chunk_stats(7)["L"]["n"] == 1
        assert s.orphan_report() == {LEGACY_AUDIO_FILE: [99]}          # só reporta (CP3)
        with s.connection() as c:
            assert c.execute("PRAGMA audio.page_size").fetchone()[0] == AUDIO_PAGE_SIZE   # VACUUM do arquivo pequeno
            assert c.execute("PRAGMA audio.journal_mode").fetchone()[0] == "wal"
        new = s.create_meeting(None, None, None, None)
        s.insert_chunk(new, "L", 0, 0, 16000, 16000, 0, _flac())
        assert s.get_meeting(new)["audio_file"] != LEGACY_AUDIO_FILE
        assert set(s.audio_files()) == {LEGACY_AUDIO_FILE, s.core.current_audio_file()}
    assert (tmp_path / LEGACY_AUDIO_FILE).exists()


def test_audio_file_without_table_is_a_clear_error(tmp_path):
    """Arquivo de áudio restaurado pela metade (versão certa, tabela ausente): erro claro ao abrir, não reunião muda."""
    s = Store(tmp_path)
    current = s.core.current_audio_file()
    s.close()
    (tmp_path / current).unlink()
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"{current}{suffix}").unlink(missing_ok=True)
    bad = sqlite3.connect(tmp_path / current, autocommit=True)
    bad.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    bad.execute("INSERT INTO schema_migrations VALUES (1, 'x'), (2, 'x')")
    bad.close()
    with pytest.raises(RuntimeError, match="audio_chunks"):
        Store(tmp_path)


def test_missing_audio_file_is_recreated_and_reported(tmp_path):
    s = Store(tmp_path)
    mid = s.create_meeting(None, None, None, None)
    s.insert_chunk(mid, "L", 0, 0, 16000, 16000, 0, _flac())
    current = s.core.current_audio_file()
    s.close()
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / f"{current}{suffix}").unlink(missing_ok=True)
    with Store(tmp_path) as s2:
        assert s2.missing_audio_files() == []                 # o arquivo do mês foi recriado vazio
        assert s2.list_chunks(mid) == []
        new = s2.create_meeting(None, None, None, None)
        s2.insert_chunk(new, "L", 0, 0, 16000, 16000, 0, _flac())
        assert s2.chunk_stats(new)["L"]["n"] == 1
        s2._x("UPDATE meetings SET audio_file = 'listener-audio-2001-01.db' WHERE id = ?", (mid,))
        assert s2.missing_audio_files() == ["listener-audio-2001-01.db"]


def test_migration_failure_rolls_back_whole_step(tmp_path, monkeypatch):
    s = Store(tmp_path)
    s.close()
    raw = sqlite3.connect(tmp_path / "listener.db", autocommit=True)
    raw.execute("ALTER TABLE meetings DROP COLUMN audio_file")
    raw.execute("DELETE FROM schema_migrations WHERE version >= 3")
    raw.execute("PRAGMA user_version = 2")
    raw.close()

    def boom(core, conn, schema):
        raise RuntimeError("falha no meio da v3")

    monkeypatch.setitem(store_core.MAIN_PY_MIGRATIONS, 3, boom)
    with pytest.raises(RuntimeError, match="falha no meio"):
        Store(tmp_path)
    raw = sqlite3.connect(tmp_path / "listener.db")
    cols = [r[1] for r in raw.execute("PRAGMA table_info(meetings)")]
    assert "audio_file" not in cols                                        # o ALTER TABLE foi desfeito junto
    assert raw.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 2
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
    raw.close()
    monkeypatch.undo()
    with Store(tmp_path) as ok:
        assert ok.schema_version() == 4 and "audio_file" in ok.get_meeting(ok.create_meeting(None, None, None, None))


def test_two_connections_wait_for_each_other(tmp_path):
    """Dois `Store` (como dois processos): leitura durante a escrita do outro; escrita espera o COMMIT (busy_timeout)."""
    a = Store(tmp_path)
    b = Store(tmp_path)
    mid = a.create_meeting(None, None, None, None)
    done = threading.Event()
    order = []
    with a.transaction():
        a.add_event(mid, 0, "start", {})
        assert b.list_meetings()[0]["id"] == mid                          # WAL: leitor não espera

        def writer():
            b.add_event(mid, 1, "marker", {})
            order.append("b")
            done.set()

        t = threading.Thread(target=writer)
        t.start()
        t.join(0.15)
        assert t.is_alive()                                                # está esperando o lock
        order.append("a")
    assert done.wait(5.0) and order == ["a", "b"]
    assert [e["type"] for e in a.list_events(mid)] == ["start", "marker"]
    a.close()
    b.close()


def test_readonly_store_reads_and_refuses_writes(tmp_path):
    with Store(tmp_path) as s:
        mid = s.create_meeting("ro", None, None, None)
        s.insert_chunk(mid, "L", 0, 0, 16000, 16000, 0, _flac())
    ro = Store.open_readonly(tmp_path)
    assert ro.readonly and ro.get_meeting(mid)["title"] == "ro" and ro.chunk_stats(mid)["L"]["n"] == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.add_event(mid, 0, "x", {})
    ro.close()
    with pytest.raises(FileNotFoundError):
        Store.open_readonly(tmp_path / "nada")


def test_per_thread_connections_are_closed(tmp_path):
    s = Store(tmp_path)
    main_conn = s.core.conn()
    seen = []

    def worker():
        seen.append(s.core.conn() is main_conn)
        s.close_thread()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen == [False] and len(s.core._conns) == 1
    s.close()


def test_v4_creates_queue_tables_and_rolls_back_in_the_middle(tmp_path, monkeypatch):
    """v4 (F2): tabelas, colunas e índices; uma falha no meio desfaz tudo (CREATE TABLE incluído); reaplicar é idempotente."""
    s = Store(tmp_path)
    s.close()
    raw = sqlite3.connect(tmp_path / "listener.db", autocommit=True)
    raw.execute("DROP TABLE processing_jobs")
    raw.execute("DROP TABLE clusters")
    raw.execute("DROP TABLE job_artifacts")
    raw.execute("DELETE FROM schema_migrations WHERE version = 4")
    raw.execute("PRAGMA user_version = 3")
    raw.close()                                   # as colunas novas de segments/meetings ficam: a v4 precisa tolerar isso

    def boom(core, conn, schema):
        raise RuntimeError("falha no meio da v4")

    monkeypatch.setitem(store_core.MAIN_PY_MIGRATIONS, 4, boom)
    with pytest.raises(RuntimeError, match="falha no meio da v4"):
        Store(tmp_path)
    raw = sqlite3.connect(tmp_path / "listener.db")
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "processing_jobs" not in tables and "clusters" not in tables          # o CREATE TABLE foi desfeito junto
    assert raw.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3
    raw.close()
    monkeypatch.undo()
    with Store(tmp_path) as ok:
        assert ok.schema_version() == 4
        with ok.connection() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            segs = {r[1] for r in c.execute("PRAGMA table_info(segments)")}
            profs = {r[1] for r in c.execute("PRAGMA table_info(voice_profiles)")}
        assert {"processing_jobs", "clusters", "job_artifacts"} <= tables
        assert {"idx_jobs_status", "idx_clusters_meeting", "idx_segments_meeting_time", "idx_embeddings_profile",
                "idx_profiles_person_model"} <= indexes
        assert {"words_json", "cluster_id", "job_id"} <= segs and {"threshold", "sim_mean", "sim_std"} <= profs
        assert "language" in ok.get_meeting(ok.create_meeting(None, None, None, None))
        pid = ok.people.create_person("Ana")
        ok.profiles.ensure_profile(pid, "m", 4)
        with pytest.raises(sqlite3.IntegrityError):                              # único por (pessoa, modelo)
            ok._x("INSERT INTO voice_profiles(person_id, model, dim, centroid, n_samples) VALUES (?, 'm', 4, x'00', 0)", (pid,))


def test_worker_role_writes_main_reads_audio_and_refuses_old_schema(tmp_path):
    from listener.model import StoreRole
    from listener.store.core import AUDIO_MMAP_BYTES

    with Store(tmp_path) as app:
        mid = app.create_meeting("w", None, None, None)
        app.insert_chunk(mid, "L", 0, 0, 16000, 16000, 0, _flac(value=0.25))
        app.finish_meeting(mid, 1000)
    w = Store.open_worker(tmp_path)
    assert w.role == StoreRole.WORKER and not w.readonly
    job = w.jobs.enqueue(mid)                                                     # main em escrita
    assert w.jobs.get(job)["status"] == "pendente"
    assert abs(decode_range(w, mid, "L", 0, 500)[0][100] - 0.25) < 1e-3          # áudio legível
    with pytest.raises(sqlite3.OperationalError):
        w.insert_chunk(mid, "L", 1, 1000, 16000, 16000, 0, _flac())              # mas só leitura
    schema = w.core.audio_schema_for_meeting(mid)
    with w.connection() as c:
        assert c.execute(f"PRAGMA {schema}.mmap_size").fetchone()[0] == AUDIO_MMAP_BYTES
    w.close()
    raw = sqlite3.connect(tmp_path / "listener.db", autocommit=True)
    raw.execute("DELETE FROM schema_migrations WHERE version = 4")
    raw.close()
    with pytest.raises(RuntimeError, match="abra o app"):                        # o worker nunca migra
        Store.open_worker(tmp_path)
    with pytest.raises(FileNotFoundError):
        Store.open_worker(tmp_path / "nada")


def test_every_connection_is_opened_with_uri_support(tmp_path, monkeypatch):
    """R2-02: o ATTACH do áudio usa `file:...?mode=ro`; só vale se a conexão foi aberta com `uri=True` (fora do Ubuntu a
    biblioteca não tem SQLITE_USE_URI). Vale para os três papéis."""
    from listener.model import StoreRole

    seen: list[tuple[str, bool]] = []
    real_connect = store_core.sqlite3.connect

    def spy(database, *args, **kwargs):
        seen.append((str(database), bool(kwargs.get("uri", False))))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(store_core.sqlite3, "connect", spy)
    with Store(tmp_path) as app:
        mid = app.create_meeting("u", None, None, None)
        app.insert_chunk(mid, "L", 0, 0, 16000, 16000, 0, _flac(value=0.1))
    with Store.open_worker(tmp_path) as w:
        assert w.role == StoreRole.WORKER and w.chunk_stats(mid)["L"]["n"] == 1
        with w.connection() as c:
            files = {r[1]: r[2] for r in c.execute("PRAGMA database_list")}
        assert all("?" not in f and not f.startswith("file:") for f in files.values())      # URI resolvida em caminho real
    Store.open_readonly(tmp_path).close()
    assert seen and all(uri for _, uri in seen), seen
