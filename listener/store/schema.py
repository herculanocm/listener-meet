"""Esquema v1 (F1). Tabelas das fases seguintes já existem vazias para as migrações serem só acréscimo."""
MAIN_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'now', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS presets (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, mic_device_id TEXT, mic_device_name TEXT,
  source_kind TEXT NOT NULL DEFAULT 'system', source_exe TEXT, sample_rate INTEGER NOT NULL DEFAULT 16000,
  is_in_room INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, role TEXT, company TEXT, notes TEXT,
  is_me INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, api_key TEXT, model TEXT, params TEXT,
  is_active_llm INTEGER NOT NULL DEFAULT 0, is_active_embed INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS meetings (
  id INTEGER PRIMARY KEY, title TEXT, title_llm TEXT, started_at TEXT NOT NULL, ended_at TEXT, duration_ms INTEGER,
  platform TEXT, preset_id INTEGER, status TEXT NOT NULL, expected_participants TEXT,
  is_in_room INTEGER NOT NULL DEFAULT 0, notes TEXT);
CREATE TABLE IF NOT EXISTS markers (
  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  t_ms INTEGER NOT NULL, note TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_markers_meeting ON markers(meeting_id, t_ms);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, meeting_id INTEGER REFERENCES meetings(id) ON DELETE CASCADE,
  t_ms INTEGER, type TEXT NOT NULL, data TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_meeting ON events(meeting_id, t_ms);
CREATE TABLE IF NOT EXISTS revisions (
  id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL, field TEXT NOT NULL,
  before TEXT, after TEXT, author TEXT NOT NULL, created_at TEXT NOT NULL);
-- fases seguintes (vazias na F1)
CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, channel TEXT,
  t_start_ms INTEGER, t_end_ms INTEGER, text TEXT, text_llm TEXT, cluster TEXT, person_id INTEGER,
  role_at_time TEXT, confidence REAL, attribution TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS captions (
  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  t_start_ms INTEGER, t_end_ms INTEGER, text TEXT, person_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS summaries (
  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, kind TEXT,
  t_until_ms INTEGER, text_llm TEXT, text_edited TEXT, provider TEXT, model TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS action_items (
  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, text TEXT,
  owner_person_id INTEGER, owner_text TEXT, due TEXT, t_ms INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS voice_profiles (
  id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES people(id), model TEXT NOT NULL, dim INTEGER NOT NULL,
  centroid BLOB NOT NULL, n_samples INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS voice_embeddings (
  id INTEGER PRIMARY KEY, profile_id INTEGER REFERENCES voice_profiles(id), meeting_id INTEGER, segment_id INTEGER,
  vector BLOB NOT NULL, source TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS rag_chunks (
  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  t_start_ms INTEGER, t_end_ms INTEGER, text TEXT, speakers TEXT);
CREATE TABLE IF NOT EXISTS rag_vectors (chunk_id INTEGER PRIMARY KEY, model TEXT, dim INTEGER, vector BLOB);
"""

AUDIO_V1 = """
CREATE TABLE IF NOT EXISTS audio.schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audio.audio_chunks (
  id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL, channel TEXT NOT NULL, idx INTEGER NOT NULL,
  t_start_ms INTEGER NOT NULL, frames INTEGER NOT NULL, sample_rate INTEGER NOT NULL,
  gap_frames INTEGER NOT NULL DEFAULT 0, flac BLOB NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (meeting_id, channel, idx));
CREATE INDEX IF NOT EXISTS audio.idx_chunks_meeting ON audio_chunks(meeting_id, channel, t_start_ms);
"""

SCHEMA_VERSION = 1
