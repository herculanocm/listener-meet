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
from listener.store.schema import MAIN_SCHEMA_VERSION as LATEST


def _flac(n=16000, value=0.0):
    return encode_flac(np.full(n, value, np.float32), 16000)


def test_monthly_audio_file_and_schema(tmp_path):
    s = Store(tmp_path / "db")
    current = s.core.current_audio_file()
    assert (tmp_path / "db" / "listener.db").exists() and (tmp_path / "db" / current).exists()
    assert re.fullmatch(r"listener-audio-\d{4}-\d{2}\.db", current) and s.audio_files() == [current]
    assert s.schema_version() == LATEST
    with s.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == LATEST
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
    assert s.meetings.set_in_room(mid, True) and s.get_meeting(mid)["is_in_room"] == 1     # F5.9 (item 7): a sala no meio
    assert s.meetings.set_in_room(mid, False) and s.get_meeting(mid)["is_in_room"] == 0 and not s.meetings.set_in_room(10**9, True)
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
    assert s2.schema_version() == LATEST
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
        assert ok.schema_version() == LATEST and "audio_file" in ok.get_meeting(ok.create_meeting(None, None, None, None))


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
        t.join(0.05)
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
    raw.execute("DELETE FROM schema_migrations WHERE version >= 4")
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
        assert ok.schema_version() == LATEST
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
    raw.execute("DELETE FROM schema_migrations WHERE version >= 5")
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


def test_v5_adds_review_columns_to_a_database_already_in_v4(tmp_path):
    """N-01: um banco que ficou em v4 (sem `calib_n` nem `cluster_id`) ganha as colunas na v5 e a confirmação funciona."""
    from listener.model import ClusterStatus
    from listener.store.segments import ClusterInsert

    with Store(tmp_path):
        pass
    raw = sqlite3.connect(tmp_path / "listener.db", autocommit=True)
    raw.execute("ALTER TABLE voice_profiles DROP COLUMN calib_n")
    raw.execute("ALTER TABLE voice_embeddings DROP COLUMN cluster_id")
    raw.execute("DELETE FROM schema_migrations WHERE version >= 5")
    raw.execute("PRAGMA user_version = 4")
    raw.close()
    with pytest.raises(RuntimeError, match="abra o app"):
        Store.open_worker(tmp_path)                                               # v4 < 5: o worker espera o app migrar
    with Store(tmp_path) as s:
        assert s.schema_version() == LATEST
        with s.connection() as c:
            assert c.execute("PRAGMA user_version").fetchone()[0] == LATEST
            assert "calib_n" in {r[1] for r in c.execute("PRAGMA table_info(voice_profiles)")}
            assert "cluster_id" in {r[1] for r in c.execute("PRAGMA table_info(voice_embeddings)")}
        mid = s.create_meeting("v5", None, None, None)
        s.finish_meeting(mid, 1000)
        job = s.jobs.enqueue(mid)
        ids = s.segments.insert_clusters([ClusterInsert(mid, job, "R", "SPK_0", 5000, np.ones(4, np.float32), "m", ClusterStatus.UNNAMED)])
        ana = s.people.create_person("Ana")
        from listener.pipeline import review

        assert review.confirm(s, ids["SPK_0"], ana) == ana
        p = s.profiles.profile_for(ana, "m")
        assert p["n_samples"] == 1 and p["calib_n"] in (None, 0)
        assert s._q("SELECT cluster_id FROM voice_embeddings")[0][0] == ids["SPK_0"]
    with Store(tmp_path) as again:                                                # reabrir não refaz nada
        assert again.schema_version() == LATEST


def test_v6_adds_llm_tables_columns_seeds_and_rolls_back_in_the_middle(tmp_path, monkeypatch):
    """F3 (v6): tabelas, colunas, índices, sementes de preço e provedores numa só transação; falha no meio desfaz tudo;
    um banco já em v5 (sem as colunas) ganha a v6 ao abrir no app; o worker recusa até lá."""
    with Store(tmp_path):
        pass
    raw = sqlite3.connect(tmp_path / "listener.db", autocommit=True)
    raw.execute("DROP INDEX idx_summaries_job")
    for table, col in (("summaries", "data_json"), ("summaries", "job_id"), ("clusters", "llm_person_id"), ("clusters", "decision_source"),
                       ("meetings", "glossary"), ("providers", "fast_model"), ("action_items", "summary_id")):
        raw.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    for table in ("llm_calls", "chat_messages", "llm_prices"):
        raw.execute(f"DROP TABLE {table}")
    raw.execute("DROP INDEX idx_summaries_meeting_kind")
    raw.execute("DELETE FROM providers")
    raw.execute("DELETE FROM schema_migrations WHERE version >= 6")
    raw.execute("PRAGMA user_version = 5")
    raw.close()
    with pytest.raises(RuntimeError, match="abra o app"):
        Store.open_worker(tmp_path)

    def boom(core, conn, schema):
        store_core._add_columns(conn, schema, store_core.MAIN_V6_COLUMNS)       # metade feita...
        raise RuntimeError("falha no meio da v6")                              # ...e cai

    monkeypatch.setitem(store_core.MAIN_PY_MIGRATIONS, 6, boom)
    with pytest.raises(RuntimeError, match="falha no meio da v6"):
        Store(tmp_path)
    raw = sqlite3.connect(tmp_path / "listener.db")
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "llm_calls" not in tables and "llm_prices" not in tables
    assert "data_json" not in {r[1] for r in raw.execute("PRAGMA table_info(summaries)")}  # o ALTER TABLE foi desfeito junto
    assert raw.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 5
    raw.close()
    monkeypatch.undo()
    with Store(tmp_path) as s:
        assert s.schema_version() == LATEST
        with s.connection() as c:
            assert c.execute("PRAGMA user_version").fetchone()[0] == LATEST
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            cols = {t: {r[1] for r in c.execute(f"PRAGMA table_info({t})")} for t in ("summaries", "action_items", "clusters", "meetings",
                                                                                      "providers")}
        assert {"llm_calls", "chat_messages", "llm_prices"} <= tables
        assert {"idx_llm_calls_meeting", "idx_chat_meeting", "idx_summaries_meeting_kind", "idx_summaries_job",
                "idx_action_items_meeting"} <= indexes
        assert {"data_json", "prompt_version", "job_id", "call_id", "status", "language"} <= cols["summaries"]
        assert {"summary_id", "segment_id", "job_id", "created_at"} <= cols["action_items"]
        assert {"llm_person_id", "llm_name", "llm_confidence", "llm_evidence_json", "decision_source"} <= cols["clusters"]
        assert {"glossary", "expected_people_json"} <= cols["meetings"]
        assert {"fast_model", "strong_model", "models_json", "tested_at", "latency_ms", "base_url"} <= cols["providers"]
        prices = s.providers.list_prices()
        assert any(p["provider"] == "gemini" and p["model"] == "gemini-2.5-flash" for p in prices) and len(prices) >= 10
        rows = s.providers.list_llm()
        assert [r["id"] for r in rows] == ["gemini", "openai", "anthropic"]                       # Gemini primeiro (CP14)
        assert s.providers.active_provider("llm")["id"] == "gemini" and s.providers.active_provider("embed")["id"] == "gemini"
        assert s.providers.active_with_key("llm") is None                                         # ativo sem chave não vale
        mid = s.create_meeting("v6", None, None, None, glossary="SRM, FIDC", expected_people_json="[1]")
        m = s.get_meeting(mid)
        assert m["glossary"] == "SRM, FIDC" and m["expected_people_json"] == "[1]"
    with Store.open_worker(tmp_path) as w:
        assert w.schema_version() == LATEST


def test_v7_adds_live_columns_and_index_and_rolls_back_in_the_middle(tmp_path, monkeypatch):
    """F4 (v7): colunas de `captions` e `presets` e o índice das legendas numa só transação; falha no meio desfaz tudo; um
    banco em v6 com legendas antigas ganha a v7 ao abrir no app sem perder linhas; o worker (e o processo ao vivo) recusa
    até lá."""
    with Store(tmp_path) as s:
        mid = s.create_meeting("v6", None, None, None)
        s._x("INSERT INTO captions(meeting_id, t_start_ms, t_end_ms, text, created_at) VALUES (?, 0, 1000, 'antiga', 'x')", (mid,))
    raw = sqlite3.connect(tmp_path / "listener.db", autocommit=True)
    raw.execute("DROP INDEX idx_captions_meeting_time")
    for table, col, _type in store_core.MAIN_V7_COLUMNS:
        raw.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    raw.execute("DELETE FROM schema_migrations WHERE version >= 7")
    raw.execute("PRAGMA user_version = 6")
    raw.close()
    with pytest.raises(RuntimeError, match="abra o app"):
        Store.open_worker(tmp_path)

    def boom(core, conn, schema):
        store_core._add_columns(conn, schema, store_core.MAIN_V7_COLUMNS[:3])    # metade feita...
        raise RuntimeError("falha no meio da v7")                                # ...e cai

    monkeypatch.setitem(store_core.MAIN_PY_MIGRATIONS, 7, boom)
    with pytest.raises(RuntimeError, match="falha no meio da v7"):
        Store(tmp_path)
    raw = sqlite3.connect(tmp_path / "listener.db")
    assert "channel" not in {r[1] for r in raw.execute("PRAGMA table_info(captions)")}       # o ALTER TABLE foi desfeito junto
    assert raw.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 6
    raw.close()
    monkeypatch.undo()
    with Store(tmp_path) as s:
        assert s.schema_version() == LATEST                                       # v7 e as seguintes (F5: v8)
        with s.connection() as c:
            caps = {r[1] for r in c.execute("PRAGMA table_info(captions)")}
            presets = {r[1] for r in c.execute("PRAGMA table_info(presets)")}
            indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"channel", "confidence", "label", "model", "latency_ms", "person_id", "created_at"} <= caps
        assert {"live_enabled", "live_names"} <= presets and "idx_captions_meeting_time" in indexes
        old = s.captions.list_for_meeting(mid)
        assert [(r["text"], r["channel"], r["label"]) for r in old] == [("antiga", None, None)]          # linha antiga intacta
        pid = s.upsert_preset("Teams", None, None, "app", "ms-teams.exe")
        preset = s.get_preset(pid)
        assert preset["live_enabled"] is None and preset["live_names"] is None                         # nulo = padrão das Configurações
    with Store.open_worker(tmp_path) as w:                                        # o processo ao vivo abre como worker
        assert w.schema_version() == LATEST


def _back_to_v7(path, *, rag_rows: bool = False) -> None:
    """Banco v8 de volta à forma da v7: `rag_*` da v1 (com linhas, se pedido), sem `rag_models`, `rag_fts`, gatilhos e
    `chat_threads`, e `chat_messages` da v6 (`meeting_id` NOT NULL), mantendo as mensagens e os ids."""
    from listener.store.schema import MAIN_V1, RAG_TRIGGERS

    raw = sqlite3.connect(path / "listener.db", autocommit=True)
    for trigger in RAG_TRIGGERS:
        raw.execute(f"DROP TRIGGER {trigger}")
    for table in ("rag_fts", "rag_vectors", "rag_models", "rag_chunks", "chat_threads"):
        raw.execute(f"DROP TABLE {table}")
    for sql in MAIN_V1:
        if "rag_" in sql:
            raw.execute(sql)
    raw.execute("CREATE TABLE chat_v6 (id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) "
                "ON DELETE CASCADE, role TEXT NOT NULL, text TEXT NOT NULL, provider TEXT, model TEXT, citations_json TEXT, "
                "cost_usd REAL, created_at TEXT NOT NULL)")
    raw.execute("INSERT INTO chat_v6 SELECT id, meeting_id, role, text, provider, model, citations_json, cost_usd, created_at "
                "FROM chat_messages")
    raw.execute("DROP TABLE chat_messages")
    raw.execute("ALTER TABLE chat_v6 RENAME TO chat_messages")
    raw.execute("CREATE INDEX idx_chat_meeting ON chat_messages(meeting_id, id)")
    if rag_rows:
        mid = raw.execute("SELECT MIN(id) FROM meetings").fetchone()[0]
        raw.execute("INSERT INTO rag_chunks(meeting_id, t_start_ms, t_end_ms, text, speakers) "
                    "VALUES (?, 0, 1000, 'trecho da v1', 'Ana')", (mid,))
        raw.execute("INSERT INTO rag_vectors(chunk_id, model, dim, vector) VALUES (1, 'antigo', 2, x'0000803f00000000')")
    raw.execute("DELETE FROM schema_migrations WHERE version >= 8")
    raw.execute("PRAGMA user_version = 7")
    raw.close()


def test_v8_rebuilds_chat_creates_rag_index_and_rolls_back_in_the_middle(tmp_path, monkeypatch):
    """F5 (v8, F5A-21): FTS5 obrigatório (sem ele, nada muda e a mensagem diz o porquê); falha no meio desfaz tudo; um banco
    em v7 com conversa da F3 e `rag_*` da v1 com linhas ganha a v8 sem perder nada: as mensagens mantêm os ids e a reunião, as
    tabelas antigas viram `*_v1` (CP3); o FTS acha sem acento e acompanha os gatilhos; apagar a reunião leva trechos, FTS e
    vetores pela cascata; `foreign_key_check` vazio."""
    from listener.model import ChatRole

    with Store(tmp_path) as s:
        mid = s.create_meeting("Orçamento 2027", None, None, None)
        other = s.create_meeting("outra", None, None, None)
        q = s.chat.add(mid, ChatRole.USER, "qual o prazo?")
        a = s.chat.add(mid, ChatRole.ASSISTANT, "sexta [00:05]", "gemini", "m", [{"t_ms": 5000, "segment_id": None}], 0.001)
    _back_to_v7(tmp_path, rag_rows=True)
    with pytest.raises(RuntimeError, match="abra o app"):
        Store.open_worker(tmp_path)
    monkeypatch.setattr(store_core, "fts5_error", lambda: "no such module: fts5")
    with pytest.raises(RuntimeError, match=r"não tem FTS5 \(no such module: fts5\); a busca da F5 precisa dele"):
        Store(tmp_path)
    monkeypatch.undo()
    real = store_core.MAIN_PY_MIGRATIONS[8]

    def boom(core, conn, schema):
        real(core, conn, schema)                                                  # a v8 inteira feita...
        raise RuntimeError("falha no fim da v8")                                  # ...e cai antes da linha de versão

    monkeypatch.setitem(store_core.MAIN_PY_MIGRATIONS, 8, boom)
    with pytest.raises(RuntimeError, match="falha no fim da v8"):
        Store(tmp_path)
    monkeypatch.undo()
    raw = sqlite3.connect(tmp_path / "listener.db")
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "rag_fts" not in tables and "chat_threads" not in tables and "rag_chunks_v1" not in tables      # tudo desfeito
    assert raw.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 7
    raw.close()
    with Store(tmp_path) as s:
        assert s.schema_version() == LATEST == 8
        with s.connection() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            triggers = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
            chat_cols = {r[1]: r for r in c.execute("PRAGMA table_info(chat_messages)")}
            v1 = c.execute("SELECT meeting_id, text, speakers FROM rag_chunks_v1").fetchall()
            v1_vec = c.execute("SELECT chunk_id, model FROM rag_vectors_v1").fetchall()
            config = c.execute("SELECT v FROM rag_fts_config WHERE k = 'rank'").fetchone()
        assert {"rag_chunks", "rag_vectors", "rag_models", "rag_fts", "chat_threads", "rag_chunks_v1", "rag_vectors_v1"} <= tables
        assert {"idx_rag_chunks_meeting", "idx_rag_vectors_model", "idx_chat_meeting", "idx_chat_thread", "idx_jobs_meeting"} <= indexes
        assert triggers == {"rag_chunks_ai", "rag_chunks_ad", "rag_chunks_au"} and config[0] == "bm25(3.0, 2.0, 1.0)"
        assert [tuple(r) for r in v1] == [(mid, "trecho da v1", "Ana")] and [tuple(r) for r in v1_vec] == [(1, "antigo")]   # CP3
        assert chat_cols["meeting_id"][3] == 0 and {"thread_id", "rating"} <= set(chat_cols)              # meeting_id aceita nulo
        old = s.chat.list_for_meeting(mid)
        assert [(m["id"], m["role"], m["meeting_id"], m["thread_id"], m["rating"]) for m in old] == [
            (q, "user", mid, None, None), (a, "assistant", mid, None, None)]                          # ids e reunião mantidos
        assert old[1]["citations_json"] == '[{"t_ms": 5000, "segment_id": null}]' and old[1]["cost_usd"] == 0.001
        thread = s.chat.create_thread("o que decidimos sobre o orçamento?")
        g = s.chat.add(None, ChatRole.USER, "o que decidimos sobre o orçamento?", thread_id=thread)
        assert s.chat.list_for_thread(thread)[0]["id"] == g and s.chat.list_for_meeting(mid)[-1]["id"] == a
        from listener.store.rag import ChunkInsert, ChunkMeta, VectorInsert

        ids = s.rag.insert_chunks([ChunkInsert(mid, "transcricao", 0, 60_000, 1, 2, "Orçamento 2027", "Ana — CFO", (1,),
                                               "O orçamento de 2027 fecha na sexta.", "h1", 3),
                                   ChunkInsert(other, "reuniao", None, None, None, None, "outra", "", (), "Reunião: outra", "h2", None)])
        s.rag.insert_vectors("m@2", 2, [VectorInsert(ids[0], "h1", b"\x00" * 8, b"\x01"),
                                        VectorInsert(ids[1], "errado", b"\x00" * 8, b"\x01")])
        assert s.rag.count_vectors("m@2") == 1                                                          # hash diferente: fica de fora
        with s.connection() as c:
            hits = [r[0] for r in c.execute("SELECT rowid FROM rag_fts WHERE rag_fts MATCH ?", ('"orcamento"',))]
            assert hits == [ids[0]]                                                                   # sem acento acha
            s.rag.update_meta([ChunkMeta(ids[0], 1000, 61_000, 5, 6, (1, 2), 4)])                    # metadado: FTS intacto
            c.execute("INSERT INTO rag_fts(rag_fts, rank) VALUES ('integrity-check', 1)")
            assert [r[0] for r in c.execute("SELECT rowid FROM rag_fts WHERE rag_fts MATCH ?", ('"sexta"',))] == [ids[0]]
            c.execute("UPDATE rag_chunks SET text = 'Sem palavra-chave agora.' WHERE id = ?", (ids[0],))
            assert not c.execute("SELECT rowid FROM rag_fts WHERE rag_fts MATCH ?", ('"sexta"',)).fetchall()   # o gatilho trocou o texto
            assert [r[0] for r in c.execute("SELECT rowid FROM rag_fts WHERE rag_fts MATCH ?", ('"palavra"',))] == [ids[0]]
        s.delete_meeting(mid)                                                     # Cancelar: cascata com a FK ligada
        with s.connection() as c:
            assert c.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0] == 1 and s.rag.count_vectors() == 0
            assert c.execute("SELECT COUNT(*) FROM rag_fts_docsize").fetchone()[0] == 1                 # o gatilho de remoção disparou
            assert not c.execute("SELECT rowid FROM rag_fts WHERE rag_fts MATCH ?", ('"palavra"',)).fetchall()
            c.execute("INSERT INTO rag_fts(rag_fts, rank) VALUES ('integrity-check', 1)")
            assert c.execute("PRAGMA foreign_key_check").fetchall() == [] and c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert s.chat.list_for_meeting(mid) == [] and s.chat.list_for_thread(thread)[0]["id"] == g     # o global fica
    with Store.open_worker(tmp_path) as w:
        assert w.schema_version() == LATEST


def test_v8_keeps_orphan_chat_messages_with_a_null_meeting(tmp_path, caplog):
    """R5-01: uma mensagem cuja reunião sumiu com a FK desligada (ferramenta de fora) não derruba a v8: fica com a reunião
    nula e o texto (CP3), e o log diz quantas eram."""
    from listener.model import ChatRole

    with Store(tmp_path) as s:
        mid = s.create_meeting("Teste", None, None, None)
        keep = s.create_meeting("Fica", None, None, None)
        orphan = s.chat.add(mid, ChatRole.USER, "pergunta antiga")
        kept = s.chat.add(keep, ChatRole.USER, "outra pergunta")
    _back_to_v7(tmp_path)
    raw = sqlite3.connect(tmp_path / "listener.db", autocommit=True)
    raw.execute("DELETE FROM meetings WHERE id = ?", (mid,))                                     # FK desligada por padrão
    raw.close()
    with caplog.at_level("WARNING", logger="listener.store"), Store(tmp_path) as s:
        assert s.schema_version() == LATEST
        rows = {int(r["id"]): dict(r) for r in s._q("SELECT * FROM chat_messages")}
        assert rows[orphan]["meeting_id"] is None and rows[orphan]["text"] == "pergunta antiga"
        assert rows[kept]["meeting_id"] == keep
        with s.connection() as c:
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert any("1 mensagem(ns) com meeting_id" in r.getMessage() for r in caplog.records)


def test_v8_ids_never_repeat_and_snapshot_reads_one_state(tmp_path):
    """R5-03: `rag_chunks` e `rag_vectors` com AUTOINCREMENT (o id de um trecho apagado não volta com outro conteúdo); a
    leitura consistente (`Store.snapshot`) vê o mesmo estado do começo ao fim, com outra conexão gravando no meio."""
    from listener.store.rag import ChunkInsert

    with Store(tmp_path) as s, Store(tmp_path) as other:
        mid = s.create_meeting("r", None, None, None)
        (a, b) = s.rag.insert_chunks([ChunkInsert(mid, "ata", None, None, None, None, "r", "", (), f"t{i}", f"h{i}", None)
                                      for i in range(2)])
        with s.transaction():
            s.rag.delete_chunks([b])
        (c,) = s.rag.insert_chunks([ChunkInsert(mid, "ata", None, None, None, None, "r", "", (), "outro", "h9", None)])
        assert c > b > a
        with s.snapshot():
            first = s._q("SELECT COUNT(*) FROM meetings")[0][0]
            other.create_meeting("gravada por outro processo", None, None, None)
            assert s._q("SELECT COUNT(*) FROM meetings")[0][0] == first
            with s.snapshot():                                                                   # reentrante
                assert s._q("SELECT COUNT(*) FROM meetings")[0][0] == first
        assert s._q("SELECT COUNT(*) FROM meetings")[0][0] == first + 1


def test_captions_repo_inserts_lists_after_id_and_cascades(store):
    from listener.store.captions import CaptionInsert

    mid = store.create_meeting("ao vivo", None, None, None)
    ana = store.people.create_person("Ana", role="CFO")
    a = store.captions.insert(CaptionInsert(mid, "R", 5200, 9800, "segundo", ana, "Ana — CFO", 0.71, "faster-whisper:small:int8", 6400))
    b = store.captions.insert(CaptionInsert(mid, "L", 1000, 4000, "primeiro"))
    rows = store.captions.list_for_meeting(mid)
    assert [r["text"] for r in rows] == ["primeiro", "segundo"]                      # ordem de tempo, não de id
    r = rows[1]
    assert (r["channel"], r["person_id"], r["label"], r["latency_ms"]) == ("R", ana, "Ana — CFO", 6400)
    assert r["model"] == "faster-whisper:small:int8"
    assert r["confidence"] == pytest.approx(0.71) and r["created_at"] and rows[0]["confidence"] is None
    assert [x["id"] for x in store.captions.list_for_meeting(mid, after_id=a)] == [b]
    assert store.captions.count(mid) == 2 and store.captions.last_id(mid) == b
    store.delete_meeting(mid)                                                          # Cancelar: a FK em cascata limpa as legendas
    assert store.captions.count(mid) == 0


def test_providers_repo_whitelist_active_and_upsert_keeps_fields(store):
    """F3A-07: `update_provider` com lista branca; `set_active` troca numa instrução; `upsert_provider` não zera `model`/`params`."""
    store.providers.update_provider("gemini", api_key="AIza-chave-secreta-123456", fast_model="gemini-2.5-flash", params={"top_p": 0.9})
    p = store.providers.get("gemini")
    assert p["api_key"] == "AIza-chave-secreta-123456" and p["fast_model"] == "gemini-2.5-flash" and p["params"] == '{"top_p": 0.9}'
    with pytest.raises(ValueError, match="desconhecidos"):
        store.providers.update_provider("gemini", is_active_llm=1)                                  # ativo só por `set_active`
    with pytest.raises(ValueError):
        store.providers.update_provider("gemini", kind="x")
    store.providers.set_active("openai", llm=True)
    assert store.providers.active_provider("llm")["id"] == "openai" and store.providers.active_provider("embed")["id"] == "gemini"
    store.providers.set_active("anthropic", embed=True)
    assert store.providers.active_provider("embed")["id"] == "anthropic"
    store.providers.set_active("openai", llm=False)
    assert store.providers.active_provider("llm") is None
    store.providers.set_active("gemini", llm=True)
    assert store.providers.active_with_key("llm")["id"] == "gemini"
    store.upsert_provider("huggingface", "huggingface", api_key="hf_1", model="m", params="p")
    store.upsert_provider("huggingface", "huggingface", api_key="hf_2")                                # só a chave muda
    hf = store.get_provider("huggingface")
    assert hf["api_key"] == "hf_2" and hf["model"] == "m" and hf["params"] == "p"
    store.upsert_provider("huggingface", "huggingface", api_key=None)                                  # None mantém
    assert store.get_provider("huggingface")["api_key"] == "hf_2"
    store.settings.upsert_provider("huggingface", "huggingface", api_key=None, clear_key=True)        # apagar é explícito
    assert store.get_provider("huggingface")["api_key"] is None
    store.providers.set_price("gemini", "gemini-2.5-flash", 0.5, 3.0)
    assert next(p for p in store.providers.list_prices("gemini") if p["model"] == "gemini-2.5-flash")["output_usd_m"] == 3.0
    assert store.providers.models("gemini") == []
    store.providers.update_provider("gemini", models_json=[{"id": "gemini-2.5-pro"}])
    assert store.providers.models("gemini") == [{"id": "gemini-2.5-pro"}]


def test_summaries_actions_llm_calls_and_chat_repos(store):
    """F3 itens 6, 10 e 13: "gerar de novo" cria `atual`, marca a anterior e as ações `superada` em `revisions`; edição humana
    em `revisions`; custo por reunião e por mês; pedido e resposta comprimidos; chat com citações."""
    from listener.model import ActionStatus, ChatRole, LlmCallStatus, SummaryKind, SummaryStatus
    from listener.store.actions import ActionInsert
    from listener.store.llm_calls import LlmCallInsert
    from listener.store.summaries import SummaryInsert

    mid = store.create_meeting("r", None, None, None)
    store.finish_meeting(mid, 1000)
    call = store.llm_calls.insert(LlmCallInsert(mid, 7, "minutes", "gemini", "gemini-2.5-pro", "minutes/1.0", "abc", 1000, 200, 100, 0,
                                                0.0125, 1200, LlmCallStatus.OK, request={"messages": ["x" * 5000]},
                                                response={"text": "ata"}))
    s1 = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, '{"context": "c"}', "# Ata", "gemini", "gemini-2.5-pro",
                                              "minutes/1.0", 7, call, "pt-BR"))
    a1, a2 = store.actions.insert_many([ActionInsert(mid, s1, "Enviar proposta", "Ana", None, "2026-10-30", 1200, None, 7),
                                        ActionInsert(mid, s1, "Revisar contrato", None, None, None, None, None, 7)])
    assert store.summaries.current(mid, SummaryKind.MINUTES)["id"] == s1 and store.summaries.has_current(mid, "minutes", 7)
    assert not store.summaries.has_current(mid, "minutes", 8) and store.summaries.current(mid, "executive") is None
    assert sorted(a["id"] for a in store.actions.list_for_meeting(mid)) == sorted([a1, a2])
    assert store.summaries.set_text_edited(s1, "# Ata editada") and not store.summaries.set_text_edited(s1, "# Ata editada")
    assert store.summaries.display_text(store.summaries.get(s1)) == "# Ata editada"
    assert store.actions.set_status(a1, ActionStatus.DONE) and store.actions.set_text(a2, "Revisar o contrato")
    s2 = store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", "# Ata 2", "openai", "gpt-5", "minutes/1.0", 9, None,
                                              "pt-BR"))
    assert store.summaries.get(s1)["status"] == SummaryStatus.SUPERSEDED and store.summaries.current(mid, "minutes")["id"] == s2
    assert {a["status"] for a in store.actions.list_for_meeting(mid, include_superseded=True)} == {ActionStatus.SUPERSEDED}
    assert store.actions.list_for_meeting(mid) == []
    rev = store._q("SELECT entity, field, before, after FROM revisions ORDER BY id")
    assert [tuple(r) for r in rev] == [("summary", "text_edited", None, "# Ata editada"), ("action_item", "status", "aberta", "feita"),
                                       ("action_item", "text", "Revisar contrato", "Revisar o contrato"),
                                       ("summary", "status", "atual", "superada")]
    assert store.summaries.stale_after_reprocess(mid, 10) == ["minutes"] and store.summaries.stale_after_reprocess(mid, 9) == []
    assert store.llm_calls.cost_for_meeting(mid) == (0.0125, 0) and store.llm_calls.cost_for_job(7) == 0.0125
    store.llm_calls.insert(LlmCallInsert(mid, 7, "title", "gemini", "x", None, None, 10, 5, 0, 0, None, 300, LlmCallStatus.OK),
                           store_payloads=False)
    assert store.llm_calls.cost_for_meeting(mid) == (0.0125, 1)
    assert store.llm_calls.month_total() == pytest.approx(0.0125) and store.llm_calls.month_total(provider="openai") == 0.0
    assert store.llm_calls.avg_latency_ms() == pytest.approx(750.0) and store.llm_calls.avg_latency_ms(task="minutes") == 1200.0
    assert store.llm_calls.avg_latency_ms(task="nada") is None
    req, resp = store.llm_calls.payloads(call)
    assert req["messages"][0] == "x" * 5000 and resp == {"text": "ata"} and store.llm_calls.payloads(call + 1) == (None, None)
    assert store._q("SELECT length(request_blob) FROM llm_calls WHERE id = ?", (call,))[0][0] < 200                  # zlib
    assert "request_blob" not in store.llm_calls.get(call) and len(store.llm_calls.list_for_meeting(mid)) == 2
    loose = store.llm_calls.insert(LlmCallInsert(None, None, "glossary", "gemini", "x", None, None, 1, 1, 0, 0, 0.0, 10, LlmCallStatus.OK))
    store.llm_calls.set_meeting(loose, mid)
    assert len(store.llm_calls.list_for_meeting(mid)) == 3
    store.chat.add(mid, ChatRole.USER, "o que ficou decidido?")
    store.chat.add(mid, ChatRole.ASSISTANT, "Foi decidido X [12:03].", "gemini", "x", [{"t_ms": 723000, "segment_id": 4}], 0.001)
    msgs = store.chat.list_for_meeting(mid)
    assert [m["role"] for m in msgs] == ["user", "assistant"] and '"segment_id": 4' in msgs[1]["citations_json"]
    store.delete_meeting(mid)                                         # cascata no chat; as chamadas pagas ficam soltas e sem conteúdo
    assert store._q("SELECT COUNT(*) FROM chat_messages")[0][0] == 0
    left = store._q("SELECT COUNT(*), COUNT(meeting_id), COUNT(request_blob), COUNT(response_blob) FROM llm_calls")[0]
    assert tuple(left) == (3, 0, 0, 0)
    assert store.llm_calls.month_total() == pytest.approx(0.0125)                                  # o custo do mês continua certo


def test_set_llm_suggestion_refuses_decided_voices_and_json_default_knows_new_types(store):
    """CP11: `llm_*` só em `ambíguo` e `sem_nome` sem mescla; `status` e `person_id` intactos. `json_default` serializa
    dataclasses, enumerações e conjuntos (F3 item 16)."""
    import json

    from listener.llm.types import Capability, Usage
    from listener.model import ClusterStatus, DecisionSource
    from listener.store.core import json_default
    from listener.store.segments import ClusterInsert

    mid = store.create_meeting("r", None, None, None)
    ana = store.people.create_person("Ana")
    ids = store.segments.insert_clusters([ClusterInsert(mid, 1, "R", "SPK_0", 5000, None, None, ClusterStatus.UNNAMED),
                                          ClusterInsert(mid, 1, "R", "SPK_1", 5000, None, None, ClusterStatus.AMBIGUOUS, ana, 0.62, 0.02),
                                          ClusterInsert(mid, 1, "R", "SPK_2", 5000, None, None, ClusterStatus.AUTO, ana, 0.9, 0.3),
                                          ClusterInsert(mid, 1, "R", "SPK_3", 5000, None, None, ClusterStatus.CONFIRMED, ana),
                                          ClusterInsert(mid, 1, "R", "SPK_4", 5000, None, None, ClusterStatus.UNNAMED)])
    store.segments.update_cluster(ids["SPK_4"], merged_into=ids["SPK_0"])
    store.segments.set_llm_suggestion(ids["SPK_0"], ana, "Ana", 0.8, '[{"segment_id": 1, "quote": "valeu, Ana"}]')
    store.segments.set_llm_suggestion(ids["SPK_1"], None, "Rodrigo", 0.7, "[]")
    for label in ("SPK_2", "SPK_3", "SPK_4"):
        with pytest.raises(ValueError, match="CP11"):
            store.segments.set_llm_suggestion(ids[label], ana, "Ana", 0.9, "[]")
    with pytest.raises(ValueError):
        store.segments.set_llm_suggestion(9999, ana, "Ana", 0.9, "[]")
    c0, c1, c2 = (store.segments.get_cluster(ids[k]) for k in ("SPK_0", "SPK_1", "SPK_2"))
    assert c0["llm_person_id"] == ana and c0["llm_name"] == "Ana" and c0["status"] == "sem_nome" and c0["person_id"] is None
    assert c1["llm_person_id"] is None and c1["llm_name"] == "Rodrigo" and c1["person_id"] == ana and c1["status"] == "ambíguo"
    assert c2["llm_person_id"] is None and c2["llm_name"] is None
    with pytest.raises(ValueError):
        store.segments.update_cluster(ids["SPK_0"], llm_name="x")                                     # só por set_llm_suggestion
    store.segments.update_cluster(ids["SPK_0"], decision_source=DecisionSource.USER_LLM)
    assert store.segments.get_cluster(ids["SPK_0"])["decision_source"] == "usuario_llm"
    store.segments.update_cluster(ids["SPK_1"], status=ClusterStatus.CONFIRMED)
    assert store.segments.clear_llm_suggestions(mid, 1) == 1                                          # só a pendente (SPK_0)
    assert store.segments.get_cluster(ids["SPK_1"])["llm_name"] == "Rodrigo"
    out = json.loads(json.dumps({"u": Usage(1, 2), "caps": frozenset({Capability.JSON, Capability.TEXT}), "k": ClusterStatus.AUTO,
                                 "b": b"xyz"}, default=json_default))
    assert out == {"u": {"input_tokens": 1, "output_tokens": 2, "cached_tokens": 0, "audio_tokens": 0,
                          "cache_write_tokens": 0}, "caps": ["json", "text"],
                   "k": "auto", "b": "<3 bytes>"}
