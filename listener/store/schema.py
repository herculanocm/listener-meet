"""Esquema do banco: migrações numeradas por arquivo (F1.1 A6, F1.2 M-01/M-02). Tabelas em specs/dados.md.

main (`listener.db`): v1 = F1; v2 = datas com fuso (A5); v3 = `meetings.audio_file` (áudio mensal, M-19/D-039);
v4 = fila de processamento, clusters, artefatos de job e colunas dos perfis (F2); v5 = `voice_profiles.calib_n` e
`voice_embeddings.cluster_id` (correções da revisão da F2); v6 = camada de LLM (F3): `llm_calls`, `chat_messages`,
`llm_prices` (com sementes), colunas em `summaries`, `action_items`, `clusters`, `meetings` e `providers`, índices;
v7 = ao vivo (F4): colunas em `captions` (canal, confiança, rótulo, modelo, atraso) e em `presets` (caixas do warm-up),
índice das legendas por reunião e tempo; v8 = base consultável (F5): `rag_chunks` e `rag_vectors` recriadas (as da v1, se
tiverem linhas, viram `*_v1`, CP3), `rag_models`, `rag_fts` (FTS5 com conteúdo externo e gatilhos; requisito), `chat_threads`
e `chat_messages` reconstruída com `meeting_id` nulo (chat global), `thread_id` e `rating`.
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

# F4 (v7): legendas ao vivo gravadas pelo processo `--live` e as caixas "Legenda ao vivo" e "Nomes ao vivo" do preset
# (nulo = padrão das Configurações); só colunas e índice, em Python (`MAIN_V7_COLUMNS`, `MAIN_V7_INDEXES`)
MAIN_V7_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("captions", "channel", "TEXT"), ("captions", "confidence", "REAL"), ("captions", "label", "TEXT"),
    ("captions", "model", "TEXT"), ("captions", "latency_ms", "INTEGER"),
    ("presets", "live_enabled", "INTEGER"), ("presets", "live_names", "INTEGER"),
)
MAIN_V7_INDEXES: tuple[str, ...] = (          # depois do ALTER, como na v6
    "CREATE INDEX IF NOT EXISTS idx_captions_meeting_time ON captions(meeting_id, t_start_ms)",
)

# F5 (v8): base consultável. Tudo em Python (`core._main_v8_rag`): a guarda da CP3 antes de recriar as tabelas da v1 e a
# reconstrução de `chat_messages` (o `meeting_id` deixa de ser NOT NULL) não cabem numa lista fixa de statements.
# `rag_chunks` e `rag_vectors` com AUTOINCREMENT: um id nunca volta com outro conteúdo (o cache das assinaturas e a
# reordenação da busca casam por id). `rag_models.backfill` = o modelo foi confirmado para completar as reuniões antigas
# ("reindexar agora", L5). O índice de `processing_jobs` por reunião serve à varredura e ao `current_job_id` (R5-02).
RAG_FTS_WEIGHTS: tuple[float, float, float] = (3.0, 2.0, 1.0)      # bm25 de título, falantes e texto (F5 item 2)
RAG_TABLES: tuple[str, ...] = ("rag_vectors", "rag_models", "rag_chunks")   # ordem da guarda: filhas antes de `rag_chunks`
RAG_TRIGGERS: tuple[str, ...] = ("rag_chunks_ai", "rag_chunks_ad", "rag_chunks_au")
MAIN_V8_RAG: tuple[str, ...] = (
    ("CREATE TABLE rag_chunks ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, kind TEXT NOT NULL,"
    "  t_start_ms INTEGER, t_end_ms INTEGER, seg_first INTEGER, seg_last INTEGER, title TEXT, speakers TEXT, person_ids TEXT,"
    "  text TEXT NOT NULL, text_hash TEXT NOT NULL, job_id INTEGER, created_at TEXT NOT NULL)"),
    "CREATE INDEX idx_rag_chunks_meeting ON rag_chunks(meeting_id, t_start_ms)",
    ("CREATE TABLE rag_vectors ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id INTEGER NOT NULL REFERENCES rag_chunks(id) ON DELETE CASCADE, model TEXT NOT NULL,"
    "  dim INTEGER NOT NULL, vector BLOB NOT NULL, sig BLOB NOT NULL, created_at TEXT NOT NULL, UNIQUE (chunk_id, model))"),
    "CREATE INDEX idx_rag_vectors_model ON rag_vectors(model, id)",
    ("CREATE TABLE rag_models ("
    "  model TEXT PRIMARY KEY, dim INTEGER NOT NULL, mu BLOB, mu_n INTEGER NOT NULL DEFAULT 0, backfill INTEGER NOT NULL DEFAULT 0,"
    "  updated_at TEXT NOT NULL)"),
    ("CREATE VIRTUAL TABLE rag_fts USING fts5(title, speakers, text, content='rag_chunks', content_rowid='id',"
    "  tokenize='unicode61 remove_diacritics 2')"),
    f"INSERT INTO rag_fts(rag_fts, rank) VALUES ('rank', 'bm25({', '.join(str(w) for w in RAG_FTS_WEIGHTS)})')",  # noqa: S608
    ("CREATE TRIGGER rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN"
    "  INSERT INTO rag_fts(rowid, title, speakers, text) VALUES (new.id, new.title, new.speakers, new.text); END"),
    ("CREATE TRIGGER rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN"
    "  INSERT INTO rag_fts(rag_fts, rowid, title, speakers, text) VALUES ('delete', old.id, old.title, old.speakers, old.text); END"),
    # só o que entra no FTS dispara: o diff troca tempos, segmentos, job e pessoas dos trechos mantidos sem reindexar o texto
    ("CREATE TRIGGER rag_chunks_au AFTER UPDATE OF title, speakers, text ON rag_chunks BEGIN"
    "  INSERT INTO rag_fts(rag_fts, rowid, title, speakers, text) VALUES ('delete', old.id, old.title, old.speakers, old.text);"
    "  INSERT INTO rag_fts(rowid, title, speakers, text) VALUES (new.id, new.title, new.speakers, new.text); END"),
    "CREATE TABLE IF NOT EXISTS chat_threads (id INTEGER PRIMARY KEY, title TEXT, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_meeting ON processing_jobs(meeting_id, kind, status)",
)
# `chat_messages` reconstruída: criar a nova, copiar com os ids, apagar a antiga, renomear e recriar os índices
CHAT_V8_TABLE = ("CREATE TABLE chat_messages_v8 ("
                 "  id INTEGER PRIMARY KEY, meeting_id INTEGER REFERENCES meetings(id) ON DELETE CASCADE,"
                 "  thread_id INTEGER REFERENCES chat_threads(id) ON DELETE CASCADE, role TEXT NOT NULL, text TEXT NOT NULL,"
                 "  provider TEXT, model TEXT, citations_json TEXT, cost_usd REAL, rating INTEGER, created_at TEXT NOT NULL)")
CHAT_V8_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_chat_meeting ON chat_messages(meeting_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_thread ON chat_messages(thread_id, id)",
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
    (7, None),        # F4: colunas de `captions` e `presets` e o índice das legendas (MAIN_V7_COLUMNS, MAIN_V7_INDEXES)
    (8, None),        # F5: base consultável (MAIN_V8_RAG, CHAT_V8_*), com a guarda da CP3 e o FTS5 como requisito
]
AUDIO_MIGRATIONS: list[Migration] = [
    (1, AUDIO_V1),
    (2, None),        # datas com offset (A5)
]
MAIN_SCHEMA_VERSION = MAIN_MIGRATIONS[-1][0]
AUDIO_SCHEMA_VERSION = AUDIO_MIGRATIONS[-1][0]
SCHEMA_VERSION = MAIN_SCHEMA_VERSION
