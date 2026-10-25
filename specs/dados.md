# Modelo de dados canônico (CP9)

Quando ler: ao tocar em qualquer tabela (T2). Dono único das tabelas; as specs citam a tabela e só descrevem o que muda.
O que existe está em `listener/store/schema.py` e `core.py`. `listener.db` (main) mais um arquivo de áudio por mês de
`meetings.started_at`, `listener-audio-AAAA-MM.db` (schema `audio_AAAA_MM`; o do mês corrente anexado na abertura, os
outros sob demanda, até 8 por conexão; D-003, D-039), cada um com `audio_chunks` e `schema_migrations` próprios; o
`listener-audio.db` da F1 segue lido como schema `audio`. WAL em todos; FK só no main. `page_size` 32 KB no áudio (M-19;
legado pequeno recebe VACUUM). Datas em ISO 8601 local, com offset a partir da migração v2 (F1.1 A5). Tamanho-alvo: 4
KB.

| Tabela | Arquivo | Colunas principais | Fase | Estado |
| --- | --- | --- | --- | --- |
| settings | main | key PK, value, scope (`now` ou `next_recording`), updated_at | F1 | v1 |
| presets | main | id, name UNIQUE, mic_device_id, mic_device_name, source_kind (`app` ou `system`), source_exe, sample_rate, is_in_room | F1 | v1 |
| people | main | id, name, email, role, company, notes, is_me | F1 | v1 |
| providers | main | id PK texto, kind, api_key, model, params, is_active_llm, is_active_embed; F2: linha `huggingface` (token, CP10) | F1, F2, F3 | v1 |
| meetings | main | id, title, title_llm, started_at, ended_at, duration_ms, platform, preset_id, status, expected_participants, is_in_room, notes, audio_file (v3), language (v4: idioma detectado, D-013). Status: gravando, pausada, gravada, cancelada, interrompida, processada, revisada | F1, F2 | v4 |
| audio_chunks | audio_AAAA_MM (um por mês; `audio` = legado) | id, meeting_id, channel (L ou R), idx, t_start_ms, frames, sample_rate, gap_frames, flac BLOB; UNIQUE(meeting_id, channel, idx); sem FK entre arquivos: órfãos só são reportados (CP3) | F1 | v1 |
| markers | main | id, meeting_id (FK cascade), t_ms, note | F1 | v1 |
| events | main | id, meeting_id (FK cascade; nulo = evento do app), t_ms, type, data JSON. Tipos: start, pause, resume, mute, unmute, marker, source_change, device_lost, device_back, alert, warning, stop, cancel, error; F2: job_start, job_step, job_done, job_error, job_cancel, profile_confirm | F1, F2 | v1 |
| revisions | main | id, entity, entity_id, field, before, after, author (só acréscimo) | F1 | v1 |
| segments | main | id, meeting_id, channel, t_start_ms, t_end_ms, text, text_llm, cluster (rótulo local), person_id, role_at_time (D-016), confidence, attribution (pyannote, usuario, llm, canal), source (final; live na F4), words_json, cluster_id, job_id (v4) | F2 | v4 |
| captions | main | id, meeting_id, t_start_ms, t_end_ms, text, person_id | F4 | v1 vazia |
| summaries | main | id, meeting_id, kind, t_until_ms, text_llm, text_edited, provider, model | F3, F4 | v1 vazia |
| action_items | main | id, meeting_id, text, owner_person_id, owner_text, due, t_ms, status | F3 | v1 vazia |
| voice_profiles | main | id, person_id, model, dim, centroid BLOB float32 (só cadastro e confirmacao), n_samples, threshold (calibrado, nulo = global), sim_mean, sim_std (Welford); único (person_id, model) | F2 | v4 |
| voice_embeddings | main | id, profile_id, meeting_id, segment_id (nulo após reprocessar), vector BLOB, source (cadastro, confirmacao, automatico), platform (Q9) | F2 | v4 |
| rag_chunks | main | id, meeting_id, t_start_ms, t_end_ms, text, speakers; mais `rag_fts` (FTS5) | F5 | v1 vazia |
| rag_vectors | main | chunk_id PK, model, dim, vector (sqlite-vec `vec0`, D-024; fallback BLOB + numpy) | F5 | v1 vazia |
| processing_jobs | main | id, meeting_id (FK cascade), kind (meeting, reprocess, embed_range), status (pendente, rodando, pausado, concluído, erro, cancelado), step, progress, error, attempts, params_json, config_json (snapshot `pipeline.*` no claim), cancel_requested, heartbeat_at, worker_pid, claimed_at, started_at, finished_at, timings_json | F2 | v4 |
| clusters | main | id, meeting_id (FK cascade), job_id, channel, cluster (SPK_n; L = usuário), speech_ms, centroid BLOB, model, person_id (sugestão ou confirmação), similarity, margin, status (auto, confirmado, ambíguo, sem_nome, ignorado), merged_into | F2 | v4 |
| job_artifacts | main | job_id (FK cascade), step, idx, data BLOB (JSON zlib mais float32), PK (job_id, step, idx); dado derivado, apagado ao concluir | F2 | v4 |

Índices v1: markers(meeting_id, t_ms); events(meeting_id, t_ms); idx_chunks_meeting(meeting_id, channel, t_start_ms) em
cada arquivo de áudio. v4: segments(meeting_id, t_start_ms); clusters(meeting_id); processing_jobs(status, id);
voice_embeddings(profile_id); único voice_profiles(person_id, model).

Regras: nada apaga dados (CP3), salvo Cancelar da gravação em curso e Reprocessar (troca atômica dos clusters e segments
do job anterior no último passo, decisões humanas transportadas por cosseno; só o usuário dispara); `revisions` registra
toda edição humana; `*.db` nunca no git nem em pasta sincronizada. Chaves de `settings` (padrões em
`listener/config/defaults.py`): `capture.*` (inclui `echo_corr_min`), `hotkeys.*`, `log.path`, `log.level`,
`ui.widget_pos`, `ui.widget_size`, `warmup.last_*`, `pipeline.*` (F2; internas `paused_until`, `paused_manual`,
`rtf_json`); tipo, faixa e escopo na tabela `SPECS` (M-24). Esquema do main: v1 (F1), v2 (datas com offset, A5), v3
(`meetings.audio_file`), v4 (F2: fila, clusters, artefatos, colunas dos perfis); áudio: v1 tabela, v2 datas, por
arquivo; `user_version` acompanha; cada migração roda inteira num `BEGIN IMMEDIATE`. `events.data` passa pelo
`json_default` (numpy e datas; o resto é erro). Papéis do `Store`: APP migra; WORKER (F2) escreve no main sem migrar e
lê o áudio em `mode=ro` com `mmap_size` 256 MB; READONLY. Fora do SQLite, por decisão: o ponteiro `db-location.json` na
pasta de configuração do sistema (D-029) e o arquivo de log rotativo escolhido pelo usuário (D-033).