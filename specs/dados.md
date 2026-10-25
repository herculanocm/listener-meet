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
| presets | main | id, name UNIQUE, mic_device_id, mic_device_name, source_kind (`app` ou `system`), source_exe, sample_rate, is_in_room; F4: live_enabled e live_names (caixas do warm-up; nulo = padrão das Configurações) | F1, F4 | v7 |
| people | main | id, name, email, role, company, notes, is_me | F1 | v1 |
| providers | main | id PK texto, kind, api_key, model (F3: modelo de embeddings), params (JSON), is_active_llm, is_active_embed, fast_model, strong_model, models_json (cache de `list_models()`), tested_at, latency_ms, base_url (nulo, P9); linhas `gemini`, `openai`, `anthropic` (Gemini ativo por padrão, CP14) e `huggingface` (token, CP10) | F1, F2, F3 | v6 |
| meetings | main | id, title, title_llm (F3: sugestão da LLM, com `revisions`), started_at, ended_at, duration_ms, platform, preset_id, status, expected_participants, is_in_room, notes, audio_file (v3), language (v4, D-013), glossary e expected_people_json (v6: warm-up). Status: gravando, pausada, gravada, cancelada, interrompida, processada, revisada | F1, F2, F3 | v6 |
| audio_chunks | audio_AAAA_MM (um por mês; `audio` = legado) | id, meeting_id, channel (L ou R), idx, t_start_ms, frames, sample_rate, gap_frames, flac BLOB; UNIQUE(meeting_id, channel, idx); sem FK entre arquivos: órfãos só são reportados (CP3) | F1 | v1 |
| markers | main | id, meeting_id (FK cascade), t_ms, note | F1 | v1 |
| events | main | id, meeting_id (FK cascade; nulo = evento do app), t_ms, type, data JSON. Tipos: start, pause, resume, mute, unmute, marker (F4: `origin` legenda ou parcial), source_change, device_lost, device_back, alert, warning, stop, cancel, error; F2: job_start, job_step, job_done, job_error, job_cancel, profile_confirm; F4: live_started, live_degraded, live_stopped (gravados pelo processo ao vivo, com os contadores) e live_cpu (pelo app, `app_pct` e `live_pct`) | F1, F2, F4 | v1 |
| revisions | main | id, entity, entity_id, field, before, after, author (só acréscimo) | F1 | v1 |
| segments | main | id, meeting_id, channel, t_start_ms, t_end_ms, text, text_llm, cluster (rótulo local), person_id, role_at_time (D-016), confidence, attribution (pyannote, usuario, llm, canal), source (final; live na F4), words_json, cluster_id, job_id (v4) | F2 | v4 |
| captions | main | id, meeting_id (FK cascade), channel (L ou R), t_start_ms, t_end_ms, text, person_id (só exibição, CP11), label ("Nome — Cargo", "Participante" ou o usuário), confidence (cosseno do nome; nulo sem embedding), model (`faster-whisper:small:int8`), latency_ms, created_at. Gravadas uma a uma pelo processo `--live` (papel WORKER) por fim de janela, logo fora da ordem de tempo entre L e R; nunca apagadas | F4 | v7 |
| summaries | main | id, meeting_id, kind (pre_summary, minutes, executive; F4: partial, com `t_until_ms` = fim da última legenda coberta e `data_json` com `last_caption_id` e `interval_min`), t_until_ms, text_llm (Markdown do projeto), text_edited, provider, model, data_json (saída validada), prompt_version, job_id (do job pai), call_id, status (atual, superada, erro), language. "Gerar de novo" insere `atual` e marca a anterior `superada` | F3, F4 | v6 |
| action_items | main | id, meeting_id, summary_id, text, owner_person_id (só quando casa com `people`), owner_text, due (AAAA-MM-DD), t_ms e segment_id (evidência; nulo = "sem evidência"), job_id, status (aberta, feita, descartada, superada), created_at | F3 | v6 |
| voice_profiles | main | id, person_id, model, dim, centroid BLOB float32 (só cadastro e confirmacao), n_samples, threshold (calibrado, nulo = global), sim_mean, sim_std, calib_n (Welford: só similaridades medidas); único (person_id, model) | F2 | v4 |
| voice_embeddings | main | id, profile_id, meeting_id, segment_id (nulo após reprocessar), cluster_id, vector BLOB, source (cadastro, confirmacao, automatico; invalidado = confirmação corrigida, fora do centroide, nunca apagado), platform (Q9) | F2 | v4 |
| rag_chunks | main | id, meeting_id, t_start_ms, t_end_ms, text, speakers; mais `rag_fts` (FTS5) | F5 | v1 vazia |
| rag_vectors | main | chunk_id PK, model, dim, vector (sqlite-vec `vec0`, D-024; fallback BLOB + numpy) | F5 | v1 vazia |
| processing_jobs | main | id, meeting_id (FK cascade), kind (meeting, reprocess, embed_range; F3: llm, classe leve), status (pendente, rodando, pausado, concluído, erro, cancelado), step (F3: identify, minutes, executive, pre_summary, title, revise, test), progress, error, attempts, params_json (llm: tasks, parent_job_id, auto), config_json (snapshot `pipeline.*`; llm: `LlmJobConfig` sem chave), cancel_requested, heartbeat_at, worker_pid, claimed_at, started_at, finished_at, timings_json (llm: cost_usd, skipped, done) | F2, F3 | v4 |
| clusters | main | id, meeting_id (FK cascade), job_id, channel, cluster (SPK_n; L = usuário), speech_ms, centroid BLOB, model, person_id (sugestão ou confirmação), similarity, margin, status (auto, confirmado, ambíguo, sem_nome, ignorado), merged_into; F3: llm_person_id, llm_name, llm_confidence, llm_evidence_json (só em ambíguo e sem_nome, via `set_llm_suggestion`, CP11), decision_source (auto, usuario, usuario_pyannote, usuario_llm, usuario_outro) | F2, F3 | v6 |
| job_artifacts | main | job_id (FK cascade), step, idx, data BLOB (JSON zlib mais float32), PK (job_id, step, idx); dado derivado, apagado ao concluir; F3: `ok` por tarefa concluída e notas por bloco da ata hierárquica | F2, F3 | v4 |
| llm_calls | main | id, meeting_id (nulo = glossário antes da reunião ou reunião apagada: Cancelar mantém a chamada paga, para o custo do mês, sem a reunião e sem o pedido e a resposta guardados), job_id (do pai), task (F4: partial), provider, model, prompt_version, schema_hash, input_tokens, output_tokens, cached_tokens, audio_tokens, cost_usd (nulo = sem preço), latency_ms, status (ok, erro), error (redigido), strategy (direct, map_reduce:n, stream, canary), request_blob e response_blob (zlib, se `llm.store_payloads`), created_at | F3 | v6 |
| chat_messages | main | id, meeting_id (FK cascade), role (user, assistant), text, provider, model, citations_json ([{t_ms, segment_id}]), cost_usd, created_at | F3 | v6 |
| llm_prices | main | provider, model (id ou prefixo de família), input_usd_m, output_usd_m, cached_usd_m, audio_usd_m, updated_at; PK (provider, model); sementes do projeto, editáveis na sub-aba | F3 | v6 |

Índices v1: markers(meeting_id, t_ms); events(meeting_id, t_ms); idx_chunks_meeting(meeting_id, channel, t_start_ms) em
cada arquivo de áudio. v4: segments(meeting_id, t_start_ms); clusters(meeting_id); processing_jobs(status, id);
voice_embeddings(profile_id); único voice_profiles(person_id, model). v6: summaries(meeting_id, kind);
summaries(job_id); action_items(meeting_id); llm_calls(meeting_id, id); chat_messages(meeting_id, id). v7:
captions(meeting_id, t_start_ms).

Regras: nada apaga dados (CP3), salvo Cancelar da gravação em curso e Reprocessar (troca atômica dos clusters e segments
do job anterior no último passo, decisões humanas transportadas por cosseno; só o usuário dispara); `revisions` registra
toda edição humana; `*.db` nunca no git nem em pasta sincronizada. Chaves de `settings` (padrões em
`listener/config/defaults.py`): `capture.*` (inclui `echo_corr_min`), `hotkeys.*`, `log.path`, `log.level`,
`ui.widget_pos`, `ui.widget_size`, `warmup.last_*`, `pipeline.*` (F2; internas `paused_until`, `paused_manual`,
`rtf_json`), `llm.*` (F3: output_language, auto_tasks, cost_alert_usd, max_parallel, audio_second_opinion,
glossary_enabled, store_payloads, proxy_url, ca_bundle, system_certs), `live.*` (F4: enabled, names, model, threads,
summary_interval_min com 0 = parcial desligado, in_widget), `ui.live_geometry`, `ui.live_on_top`; tipo, faixa e escopo
na tabela `SPECS` (M-24).
Esquema do main: v1 (F1), v2 (datas com offset, A5), v3 (`meetings.audio_file`), v4 (F2: fila, clusters, artefatos,
colunas dos perfis), v5 (calib_n, cluster_id), v6 (F3: camada de LLM, sementes de preço e provedores), v7 (F4: colunas
de `captions` e `presets`, índice das legendas); áudio: v1 tabela, v2 datas, por arquivo; `user_version` acompanha; cada
migração roda inteira num `BEGIN IMMEDIATE`. `events.data` passa pelo `json_default` (numpy e datas; o resto é erro).
Papéis do `Store`: APP migra; WORKER (worker da F2 e processo ao vivo da F4) escreve no main sem migrar e lê o áudio em
`mode=ro` com `mmap_size` 256 MB; READONLY.
Job vigente de uma reunião (revisão, leitura, trecho de 5 s): o `concluído` mais recente entre `meeting` e `reprocess`;
sem concluído, o ativo; jobs `llm` nunca contam (F3A-04) e tudo que escrevem leva o `job_id` do pai. Chaves de API em
`providers.api_key`, nunca em `config_json`, log ou erro (redação, CP10).
Fora do SQLite, por decisão: o ponteiro `db-location.json` na
pasta de configuração do sistema (D-029) e o arquivo de log rotativo escolhido pelo usuário (D-033).
