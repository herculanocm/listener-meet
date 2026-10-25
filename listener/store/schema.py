"""Esquema do banco: migrações numeradas por arquivo (F1.1 A6, F1.2 M-01/M-02). Tabelas em specs/dados.md.

main (`listener.db`): v1 = F1; v2 = datas com fuso (A5); v3 = `meetings.audio_file` (áudio mensal, M-19/D-039);
v4 = fila de processamento, clusters, artefatos de job e colunas dos perfis (F2); v5 = `voice_profiles.calib_n` e
`voice_embeddings.cluster_id` (correções da revisão da F2); v6 = camada de LLM (F3): `llm_calls`, `chat_messages`,
`llm_prices` (com sementes), colunas em `summaries`, `action_items`, `clusters`, `meetings` e `providers`, índices.
Cada arquivo de áudio (`listener-audio.db` legado ou `listener-audio-AAAA-MM.db`): v1 = tabela; v2 = datas com fuso.
Cada migração é uma lista de statements (não um script), executada inteira num `BEGIN IMMEDIATE` junto com a linha de
versão e o `PRAGMA user_version`; `{s}` é o nome do schema anexado. Migrações em Python ficam em `core.py`.
"""
from __future__ import annotations

MAIN_V1: tuple[str, ...] = (
    ("CREATE TABLE IF NOT EXISTS settings ("
    "  key TEXT PRIMARY KEY, value TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'now', updated_at TEXT NOT NULL)"),
    ("CREATE TABLE IF NOT EXISTS presets ("
    "  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, mic_device_id TEXT, mic_device_name TEXT,"
    "  source_kind TEXT NOT NULL DEFAULT 'system', source_exe TEXT, sample_rate INTEGER NOT NULL DEFAULT 16000,"
    "  is_in_room INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)"),
    ("CREATE TABLE IF NOT EXISTS people ("
    "  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, role TEXT, company TEXT, notes TEXT,"
    "  is_me INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"),
    ("CREATE TABLE IF NOT EXISTS providers ("
    "  id TEXT PRIMARY KEY, kind TEXT NOT NULL, api_key TEXT, model TEXT, params TEXT,"
    "  is_active_llm INTEGER NOT NULL DEFAULT 0, is_active_embed INTEGER NOT NULL DEFAULT 0, updated_at TEXT)"),
    ("CREATE TABLE IF NOT EXISTS meetings ("
    "  id INTEGER PRIMARY KEY, title TEXT, title_llm TEXT, started_at TEXT NOT NULL, ended_at TEXT, duration_ms INTEGER,"
    "  platform TEXT, preset_id INTEGER, status TEXT NOT NULL, expected_participants TEXT,"
    "  is_in_room INTEGER NOT NULL DEFAULT 0, notes TEXT)"),
    ("CREATE TABLE IF NOT EXISTS markers ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,"
    "  t_ms INTEGER NOT NULL, note TEXT, created_at TEXT NOT NULL)"),
    "CREATE INDEX IF NOT EXISTS idx_markers_meeting ON markers(meeting_id, t_ms)",
    ("CREATE TABLE IF NOT EXISTS events ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER REFERENCES meetings(id) ON DELETE CASCADE,"
    "  t_ms INTEGER, type TEXT NOT NULL, data TEXT, created_at TEXT NOT NULL)"),
    "CREATE INDEX IF NOT EXISTS idx_events_meeting ON events(meeting_id, t_ms)",
    ("CREATE TABLE IF NOT EXISTS revisions ("
    "  id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL, field TEXT NOT NULL,"
    "  before TEXT, after TEXT, author TEXT NOT NULL, created_at TEXT NOT NULL)"),
    # fases seguintes (vazias na F1)
    ("CREATE TABLE IF NOT EXISTS segments ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, channel TEXT,"
    "  t_start_ms INTEGER, t_end_ms INTEGER, text TEXT, text_llm TEXT, cluster TEXT, person_id INTEGER,"
    "  role_at_time TEXT, confidence REAL, attribution TEXT, source TEXT)"),
    ("CREATE TABLE IF NOT EXISTS captions ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,"
    "  t_start_ms INTEGER, t_end_ms INTEGER, text TEXT, person_id INTEGER, created_at TEXT)"),
    ("CREATE TABLE IF NOT EXISTS summaries ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, kind TEXT,"
    "  t_until_ms INTEGER, text_llm TEXT, text_edited TEXT, provider TEXT, model TEXT, created_at TEXT)"),
    ("CREATE TABLE IF NOT EXISTS action_items ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, text TEXT,"
    "  owner_person_id INTEGER, owner_text TEXT, due TEXT, t_ms INTEGER, status TEXT)"),
    ("CREATE TABLE IF NOT EXISTS voice_profiles ("
    "  id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES people(id), model TEXT NOT NULL, dim INTEGER NOT NULL,"
    "  centroid BLOB NOT NULL, n_samples INTEGER NOT NULL DEFAULT 0, updated_at TEXT)"),
    ("CREATE TABLE IF NOT EXISTS voice_embeddings ("
    "  id INTEGER PRIMARY KEY, profile_id INTEGER REFERENCES voice_profiles(id), meeting_id INTEGER, segment_id INTEGER,"
    "  vector BLOB NOT NULL, source TEXT, created_at TEXT)"),
    ("CREATE TABLE IF NOT EXISTS rag_chunks ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,"
    "  t_start_ms INTEGER, t_end_ms INTEGER, text TEXT, speakers TEXT)"),
    "CREATE TABLE IF NOT EXISTS rag_vectors (chunk_id INTEGER PRIMARY KEY, model TEXT, dim INTEGER, vector BLOB)",
)

# F2: tabelas novas (idempotentes); as colunas novas em tabelas existentes entram por `ALTER TABLE` em `core.py`
MAIN_V4: tuple[str, ...] = (
    ("CREATE TABLE IF NOT EXISTS processing_jobs ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,"
    "  kind TEXT NOT NULL DEFAULT 'meeting', status TEXT NOT NULL DEFAULT 'pendente', step TEXT,"
    "  progress REAL NOT NULL DEFAULT 0, error TEXT, attempts INTEGER NOT NULL DEFAULT 0, params_json TEXT, config_json TEXT,"
    "  cancel_requested INTEGER NOT NULL DEFAULT 0, heartbeat_at TEXT, worker_pid INTEGER, claimed_at TEXT,"
    "  started_at TEXT, finished_at TEXT, timings_json TEXT, created_at TEXT NOT NULL)"),
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON processing_jobs(status, id)",
    ("CREATE TABLE IF NOT EXISTS clusters ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,"
    "  job_id INTEGER NOT NULL, channel TEXT NOT NULL, cluster TEXT NOT NULL, speech_ms INTEGER NOT NULL DEFAULT 0,"
    "  centroid BLOB, model TEXT, person_id INTEGER, similarity REAL, margin REAL, status TEXT NOT NULL,"
    "  merged_into INTEGER, created_at TEXT NOT NULL)"),
    "CREATE INDEX IF NOT EXISTS idx_clusters_meeting ON clusters(meeting_id)",
    ("CREATE TABLE IF NOT EXISTS job_artifacts ("
    "  job_id INTEGER NOT NULL REFERENCES processing_jobs(id) ON DELETE CASCADE, step TEXT NOT NULL, idx INTEGER NOT NULL,"
    "  data BLOB NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (job_id, step, idx))"),
    "CREATE INDEX IF NOT EXISTS idx_segments_meeting_time ON segments(meeting_id, t_start_ms)",
    "CREATE INDEX IF NOT EXISTS idx_embeddings_profile ON voice_embeddings(profile_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_person_model ON voice_profiles(person_id, model)",
)
# (tabela, coluna, tipo) acrescentadas pela v4 se faltarem (`ALTER TABLE ADD COLUMN` não é idempotente)
MAIN_V4_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("segments", "words_json", "TEXT"), ("segments", "cluster_id", "INTEGER"), ("segments", "job_id", "INTEGER"),
    ("voice_profiles", "threshold", "REAL"), ("voice_profiles", "sim_mean", "REAL"), ("voice_profiles", "sim_std", "REAL"),
    ("voice_embeddings", "platform", "TEXT"), ("meetings", "language", "TEXT"),
)
# v5: um banco que já estava em v4 não ganharia colunas acrescentadas à lista da v4 (migração é versionada)
MAIN_V5_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("voice_profiles", "calib_n", "INTEGER"), ("voice_embeddings", "cluster_id", "INTEGER"),
)

# F3 (v6): tabelas novas; colunas em tabelas existentes e índices sobre elas em Python (`MAIN_V6_COLUMNS`, `MAIN_V6_INDEXES`)
MAIN_V6: tuple[str, ...] = (
    ("CREATE TABLE IF NOT EXISTS llm_calls ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER REFERENCES meetings(id) ON DELETE CASCADE, job_id INTEGER, task TEXT NOT NULL,"
    "  provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT, schema_hash TEXT, input_tokens INTEGER NOT NULL DEFAULT 0,"
    "  output_tokens INTEGER NOT NULL DEFAULT 0, cached_tokens INTEGER NOT NULL DEFAULT 0, audio_tokens INTEGER NOT NULL DEFAULT 0,"
    "  cost_usd REAL, latency_ms INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, error TEXT, strategy TEXT,"
    "  request_blob BLOB, response_blob BLOB, created_at TEXT NOT NULL)"),
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_meeting ON llm_calls(meeting_id, id)",
    ("CREATE TABLE IF NOT EXISTS chat_messages ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, role TEXT NOT NULL,"
    "  text TEXT NOT NULL, provider TEXT, model TEXT, citations_json TEXT, cost_usd REAL, created_at TEXT NOT NULL)"),
    "CREATE INDEX IF NOT EXISTS idx_chat_meeting ON chat_messages(meeting_id, id)",
    ("CREATE TABLE IF NOT EXISTS llm_prices ("
    "  provider TEXT NOT NULL, model TEXT NOT NULL, input_usd_m REAL NOT NULL DEFAULT 0, output_usd_m REAL NOT NULL DEFAULT 0,"
    "  cached_usd_m REAL NOT NULL DEFAULT 0, audio_usd_m REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,"
    "  PRIMARY KEY (provider, model))"),
    "CREATE INDEX IF NOT EXISTS idx_summaries_meeting_kind ON summaries(meeting_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_action_items_meeting ON action_items(meeting_id)",
)
MAIN_V6_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("summaries", "data_json", "TEXT"), ("summaries", "prompt_version", "TEXT"), ("summaries", "job_id", "INTEGER"),
    ("summaries", "call_id", "INTEGER"), ("summaries", "status", "TEXT"), ("summaries", "language", "TEXT"),
    ("action_items", "summary_id", "INTEGER"), ("action_items", "segment_id", "INTEGER"), ("action_items", "job_id", "INTEGER"),
    ("action_items", "created_at", "TEXT"),
    ("clusters", "llm_person_id", "INTEGER"), ("clusters", "llm_name", "TEXT"), ("clusters", "llm_confidence", "REAL"),
    ("clusters", "llm_evidence_json", "TEXT"), ("clusters", "decision_source", "TEXT"),
    ("meetings", "glossary", "TEXT"), ("meetings", "expected_people_json", "TEXT"),
    ("providers", "fast_model", "TEXT"), ("providers", "strong_model", "TEXT"), ("providers", "models_json", "TEXT"),
    ("providers", "tested_at", "TEXT"), ("providers", "latency_ms", "INTEGER"), ("providers", "base_url", "TEXT"),
)
MAIN_V6_INDEXES: tuple[str, ...] = (          # sobre colunas que a própria v6 acrescenta: depois do ALTER
    "CREATE INDEX IF NOT EXISTS idx_summaries_job ON summaries(job_id)",
)

AUDIO_V1: tuple[str, ...] = (
    ("CREATE TABLE IF NOT EXISTS {s}.audio_chunks ("
    "  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL, channel TEXT NOT NULL, idx INTEGER NOT NULL,"
    "  t_start_ms INTEGER NOT NULL, frames INTEGER NOT NULL, sample_rate INTEGER NOT NULL,"
    "  gap_frames INTEGER NOT NULL DEFAULT 0, flac BLOB NOT NULL, created_at TEXT NOT NULL,"
    "  UNIQUE (meeting_id, channel, idx))"),
    "CREATE INDEX IF NOT EXISTS {s}.idx_chunks_meeting ON audio_chunks(meeting_id, channel, t_start_ms)",
)

# Colunas de data reescritas pela migração v2 (hora local com offset, F1.1 A5)
MAIN_DATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "settings": ("updated_at",), "presets": ("updated_at",), "people": ("created_at", "updated_at"),
    "providers": ("updated_at",), "meetings": ("started_at", "ended_at"), "markers": ("created_at",),
    "events": ("created_at",), "revisions": ("created_at",), "captions": ("created_at",),
    "summaries": ("created_at",), "voice_profiles": ("updated_at",), "voice_embeddings": ("created_at",),
    "schema_migrations": ("applied_at",),
}
AUDIO_DATE_COLUMNS: dict[str, tuple[str, ...]] = {"audio_chunks": ("created_at",), "schema_migrations": ("applied_at",)}

# (versão, statements) para migrações só de esquema; (versão, None) quando a migração é código Python em core.py
type Migration = tuple[int, tuple[str, ...] | None]
MAIN_MIGRATIONS: list[Migration] = [
    (1, MAIN_V1),
    (2, None),        # datas com offset (A5)
    (3, None),        # meetings.audio_file (ALTER TABLE idempotente em Python) preenchido com o arquivo legado (M-19)
    (4, MAIN_V4),     # F2: tabelas da fila e dos clusters; colunas novas em Python (MAIN_V4_COLUMNS)
    (5, None),        # F2 revisão: calib_n e voice_embeddings.cluster_id (MAIN_V5_COLUMNS)
    (6, MAIN_V6),     # F3: llm_calls, chat_messages, llm_prices; colunas, índices, sementes e provedores em Python
]
AUDIO_MIGRATIONS: list[Migration] = [
    (1, AUDIO_V1),
    (2, None),        # datas com offset (A5)
]
MAIN_SCHEMA_VERSION = MAIN_MIGRATIONS[-1][0]
AUDIO_SCHEMA_VERSION = AUDIO_MIGRATIONS[-1][0]
SCHEMA_VERSION = MAIN_SCHEMA_VERSION
