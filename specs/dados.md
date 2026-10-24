# Modelo de dados canônico (CP9)

Quando ler: ao tocar em qualquer tabela (T2). Dono único das tabelas; as specs citam a tabela e só descrevem o que muda.
O que existe está em `listener/store/schema.py` (v1). Dois arquivos SQLite anexados (D-003): `listener.db` (main) e
`listener-audio.db` (schema `audio`); WAL nos dois; FK ligadas; `schema_migrations(version, applied_at)` em cada um.
Datas em ISO 8601 local, com offset a partir da migração v2 (F1.1 A5). Tamanho-alvo: 4 KB.

| Tabela | Arquivo | Colunas principais | Fase | Estado |
| --- | --- | --- | --- | --- |
| settings | main | key PK, value, scope (`now` ou `next_recording`), updated_at | F1 | v1 |
| presets | main | id, name UNIQUE, mic_device_id, mic_device_name, source_kind (`app` ou `system`), source_exe, sample_rate, is_in_room | F1 | v1 |
| people | main | id, name, email, role, company, notes, is_me | F1 | v1 |
| providers | main | id PK texto, kind, api_key, model, params, is_active_llm, is_active_embed | F1 vazia; F2 linha `huggingface`; F3 | v1 |
| meetings | main | id, title, title_llm, started_at, ended_at, duration_ms, platform, preset_id, status, expected_participants, is_in_room, notes. Status: gravando, pausada, gravada, cancelada, interrompida; F2: processada, revisada | F1 | v1 |
| audio_chunks | audio | id, meeting_id, channel (L ou R), idx, t_start_ms, frames, sample_rate, gap_frames, flac BLOB; UNIQUE(meeting_id, channel, idx) | F1 | v1 |
| markers | main | id, meeting_id (FK cascade), t_ms, note | F1 | v1 |
| events | main | id, meeting_id (FK cascade; nulo = evento do app), t_ms, type, data JSON. Tipos: start, pause, resume, mute, unmute, marker, source_change, device_lost, alert, warning, stop, cancel, error | F1 | v1 |
| revisions | main | id, entity, entity_id, field, before, after, author (só acréscimo) | F1 | v1 |
| segments | main | id, meeting_id, channel, t_start_ms, t_end_ms, text, text_llm, cluster, person_id, role_at_time (D-016), confidence, attribution (pyannote, usuario, llm), source; F2 acrescenta words_json, cluster_id | F2 | v1 vazia |
| captions | main | id, meeting_id, t_start_ms, t_end_ms, text, person_id | F4 | v1 vazia |
| summaries | main | id, meeting_id, kind, t_until_ms, text_llm, text_edited, provider, model | F3, F4 | v1 vazia |
| action_items | main | id, meeting_id, text, owner_person_id, owner_text, due, t_ms, status | F3 | v1 vazia |
| voice_profiles | main | id, person_id, model, dim, centroid BLOB, n_samples; F2 acrescenta threshold, sim_mean, sim_std | F2 | v1 vazia |
| voice_embeddings | main | id, profile_id, meeting_id, segment_id, vector BLOB, source (cadastro, confirmacao, automatico); F2 Q9 acrescenta platform | F2 | v1 vazia |
| rag_chunks | main | id, meeting_id, t_start_ms, t_end_ms, text, speakers; mais `rag_fts` (FTS5) | F5 | v1 vazia |
| rag_vectors | main | chunk_id PK, model, dim, vector (sqlite-vec `vec0`, D-024; fallback BLOB + numpy) | F5 | v1 vazia |
| processing_jobs | main | id, meeting_id, status (pendente, rodando, pausado, concluído, erro, cancelado), step, progress, error, attempts, started_at, finished_at, timings_json | F2 | planejada |
| clusters | main | id, meeting_id, channel, cluster, speech_ms, centroid BLOB, person_id, similarity, margin, status (auto, confirmado, ambíguo, sem nome, ignorado) | F2 | planejada |

Índices v1: markers(meeting_id, t_ms); events(meeting_id, t_ms); audio.idx_chunks_meeting(meeting_id, channel, t_start_ms).

Regras: nada apaga dados (CP3), salvo Cancelar da gravação em curso e Reprocessar (só clusters e segments da reunião);
`revisions` registra toda edição humana; `*.db` nunca no git nem em pasta sincronizada. Chaves de `settings` (padrões em
`listener/config/defaults.py`): `capture.*` (inclui `echo_corr_min`), `hotkeys.*`, `log.path`, `log.level`, `ui.widget_pos`,
`ui.widget_size`, `warmup.last_*`; F2: `pipeline.*`. Esquema atual: v2 (datas com offset, F1.1 A5).
Fora do SQLite, por decisão: o ponteiro `db-location.json` na pasta de configuração do sistema (D-029) e o arquivo de log
rotativo escolhido pelo usuário (D-033).
