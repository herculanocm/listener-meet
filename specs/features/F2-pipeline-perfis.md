# F2 — Pós-reunião: transcrição, diarização e perfis de voz

Status: implementada (L1 a L6; L5 e o G2 dependem do token, B4, e do Windows) · Aprovada por
Herculano em: 2026-10-24 (Q1 a Q9, D-036; reaprovada no mesmo dia após a revisão de arquitetura, P1 a P6 nos padrões,
D-041) · Fase e gate: F2 · G2 ·
CPs: 3, 5, 8, 9, 10, 11 (parte "depois da reunião", sem a LLM), 12, 13, 14, 15, 16, 17
Quando ler: ao trabalhar no pipeline pós-reunião. Tabelas: `dados.md`. Código que já existe e esta spec usa: Store com
conexão por thread e `transaction()`, `open_readonly`, áudio mensal por reunião, `json_default`, `MemorySink`, `_Task`,
`log.attach_file`, `SettingSpec` com `phase`, `playback.decode_range`. Tamanho-alvo: 18 KB.

## Objetivo
Depois do Parar, transformar o áudio em falas com texto, tempo e pessoa: L é o usuário; R é diarizado pelo pyannote e
nomeado por perfis de voz persistentes. Abaixo do limiar vai para uma revisão mínima; cada confirmação reforça o perfil.
A LLM entra na F3 sem mudar este desenho.

## Decisões já tomadas (D-036)
Worker em subprocesso (Q1) · token HF em `providers` (Q2) · limiar 0,60, margem 0,05, autocalibração em [0,45; 0,75]
(Q3) · large-v3-turbo int8 padrão, small e medium (Q4) · clusters abaixo de 10 s só na revisão (Q5) · leitura mínima
nesta fase (Q6) · fila pausa em gravação (Q7) · embeddings do pipeline se expostos e com o mesmo modelo (Q8) · coluna
`platform` (Q9).

## Escopo
1. **Estrutura.** `listener/pipeline/`: `types.py` (dataclasses `frozen, slots`: `Word`, `Turn`, `AudioWindow`,
   `TranscriptWindow`, `DiarizationWindow`, `ClusterData`, `MatchResult`, `JobConfig`), `base.py` (Protocols `Transcriber`,
   `Diarizer`, `Embedder`; `PipelineDeps` com fábricas preguiçosas), `windows.py`, `align.py` (`Segmenter`), `profiles.py`
   (`ProfileMatcher`), `stitch.py` (costura), `runner.py` (`JobRunner`), `worker.py` (processo), `models.py` (pasta de
   modelos, download, sonda Q8), `whisper.py`, `pyannote.py`; `tests/fake_pipeline.py`. Regra dura: nenhum módulo
   importado pelo app (`ui/`, `controller.py`) importa torch, `whisper.py`, `pyannote.py` ou `models.py`; a suíte roda sem
   torch (como hoje no WSL).
2. **Fila e worker.** `processing_jobs` (id, meeting_id, kind `meeting | reprocess | embed_range`, status, step, progress,
   error, attempts, params_json, config_json, cancel_requested, heartbeat_at, worker_pid, claimed_at, started_at,
   finished_at, timings_json). Criado em `Controller._on_stopped` com a reunião já `gravada`. O app lança o worker só se há
   `pendente`, nenhum `rodando` com heartbeat fresco e o `Popen` anterior terminou; comando único `worker_command()` →
   `[sys.executable, "-m", "listener", "--worker"]` (ou `[sys.executable, "--worker"]` congelado); flag `--worker [--db DIR]
   [--once]` em `__main__.py`. `Platform.spawn_detached(argv, env)` (win32: `CREATE_NO_WINDOW |
   BELOW_NORMAL_PRIORITY_CLASS`, sem `DETACHED_PROCESS` desde a D-056; posix: `start_new_session`) e `Platform.lower_own_priority()` (win32: `SetPriorityClass`;
   posix: `os.nice(10)`), implementação portável em `platform/common/process.py`; nunca `preexec_fn`. Claim atômico numa só
   transação: devolve a `pendente` (`attempts + 1`) todo `rodando` com `heartbeat_at` vencido há mais de 60 s; pega o
   primeiro `pendente` com `attempts < 3` se não houver `rodando` fresco; `UPDATE ... RETURNING id, meeting_id, kind` em
   `BEGIN IMMEDIATE`. Heartbeat escrito antes de carregar modelos e renovado a cada 15 s por thread própria. `attempts ≥ 3`
   vira `erro`. Store no papel `WORKER`: main em escrita sem VACUUM e sem migrar (quem migra é o app; versão menor que a do
   código recusa), áudio anexado `mode=ro` com `mmap_size` 256 MB. Worker sai após 60 s ocioso (libera 1 a 2 GB). Modelos
   carregados uma vez por processo, preguiçosos por etapa; `torch.set_num_threads` e `WhisperModel(cpu_threads=n,
   num_workers=1)` com `pipeline.torch_threads`; `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `CT2_VERBOSE=0` no `env`. Log próprio
   `listener-worker.log` na mesma pasta do log do app, com versões de torch, ctranslate2, faster-whisper e pyannote.audio.
   Regras de banco: nunca transação em volta de computação; `fetchall()` por janela; escritas em lotes de até 1000 linhas
   (`itertools.batched`); os marcos vão para `events` (`JOB_START`, `JOB_STEP`, `JOB_DONE`, `JOB_ERROR`, `JOB_CANCEL`).
3. **Pausa por lease (Q7).** Ao iniciar a gravação o app escreve `pipeline.paused_until = agora + 10 min` e renova no `_tick`
   a cada 60 s; limpa ao parar, cancelar e na parada de emergência. O worker lê entre janelas; vencido, segue. Pausa manual:
   `pipeline.paused_manual = 1`. Enquanto espera, o job fica `pausado` com `step` "aguardando fim da gravação".
4. **Janelas de áudio.** `windows.iter_windows(store, meeting_id, channel, window_s, overlap_s) -> Iterator[AudioWindow]` sobre
   `decode_range` (buracos viram silêncio; reamostra para 16 kHz se a taxa gravada for outra). Whisper: 600 s nominais,
   corte no maior silêncio (RMS abaixo de `capture.silence_rms` por 300 ms ou mais) a ±15 s do ponto, sem sobreposição;
   sem silêncio, corte seco com 2 s de sobreposição e dedup pelo centro da palavra; janela sem fala (RMS) é pulada sem
   chamar o modelo. pyannote: janelas de até `pipeline.diar_window_s` (padrão 1200; 300 a 7200) com 30 s de
   sobreposição e costura entre elas (3 emendas por hora; a janela corrente termina em cerca de 16 min de relógio a
   0,8x). Nunca mais que isso em memória (77 MB por janela em float32). Sem `blobopen`.
5. **Transcrição (D-028).** faster-whisper com `word_timestamps`, `vad_filter` (`min_silence_duration_ms=500`), `beam_size=5`,
   `condition_on_previous_text=False`, `hallucination_silence_threshold=2.0`; descartar segmentos com `no_speech_prob`
   acima de 0,6 ou `avg_logprob` abaixo de −1,0. `initial_prompt` = glossário fixo + participantes esperados + título,
   cortado a ~150 tokens, mais as últimas ~60 palavras da janela anterior (o Whisper só guarda 224 tokens). Idioma detectado
   na primeira janela com fala e fixado em `meetings.language` (D-013). Opção "alta precisão" com wav2vec2; modelo
   escolhível (turbo padrão; small, medium).
6. **Diarização de R.** pyannote community-1 em CPU, saída exclusiva mais lista de sobreposições; `min_speakers = 1` e
   `max_speakers = n + 1` quando há participantes esperados (contados por vírgula ou ponto e vírgula); nunca `num_speakers`
   exato. Costura entre janelas (`stitch.py`): cosseno entre centroides das janelas consecutivas, `linear_sum_assignment`
   maximizando; par aceito com cosseno de 0,5 ou mais ou quando os dois rótulos ocupam o mesmo trecho da sobreposição
   (tempo manda); rótulo sem par ganha id novo. Turnos por cluster (`SPK_n`) válidos só na reunião.
7. **Embeddings e perfis (CP5).** Centroide do cluster = média dos embeddings L2-normalizados dos turnos limpos (1,5 s ou
   mais, sem sobreposição), no máximo 20 turnos, os mais longos; embeddings do pipeline (Q8) só se `model` for o mesmo
   (`models.py` sonda e loga). Perfis: `S = C @ P.T`, `linear_sum_assignment(maximize=True)`; depois, por cluster: sim ≥
   limiar do perfil → `auto`; margem (melhor − segundo da linha) < 0,05 → `ambíguo`; `speech_ms < 10 000` → nunca `auto`
   (Q5); senão `sem_nome`. Autocalibração (Q3) por Welford de `sim_mean` e `sim_std` com a similaridade antes da
   atualização; `threshold = clip(sim_mean − 1,5·sim_std, 0,45, 0,75)` só com `n_samples ≥ 5`. Centroide do perfil só com
   `source in (cadastro, confirmacao)`; `automatico` fica gravado sem entrar até o G2 dizer o contrário. `UNIQUE(person_id,
   model)` em `voice_profiles`. Lista de participantes esperados como desempate quando a margem for menor que 0,05 (P4;
   não é LLM, não fere CP11).
8. **Segmentos.** Cada palavra entra no turno exclusivo que contém seu centro; palavra fora de turno vai para o mais próximo
   até 500 ms, senão `cluster_id = NULL`. Segmento = palavras consecutivas do mesmo cluster, cortado em pausa acima de 1 s,
   em 30 s, ou após `.?!` com pausa acima de 400 ms. `t_start_ms` e `t_end_ms` na linha do tempo da sessão. Canal L:
   `person_id = me`, `attribution = canal` (novo valor; "usuário" seria mentira); preset sala (U-015): L segue o caminho do
   R. `segments.cluster` guarda o rótulo local; `cluster_id` é a chave. `role_at_time` copiado de `people.role` (D-016).
9. **Ordem das etapas (P1).** diarizar R → embeddings → casar perfis → revisão de falantes já disponível → transcrever R →
   transcrever L → segmentos. A revisão só precisa de cluster, nome sugerido e 5 s de áudio; o usuário confirma enquanto o
   Whisper roda; `meetings.status` fica `gravada` até o fim e a revisão abre quando há `clusters` do job.
10. **Artefatos e retomada.** `job_artifacts(job_id, step, idx, data BLOB, created_at)`: resultado de cada janela gravado logo
    após ela (palavras e turnos em JSON com `zlib`, embeddings em bytes float32); na retomada, `idx` presentes são pulados;
    a etapa `segments` lê só do banco; ao concluir, os artefatos do job são apagados (dado derivado; `segments.words_json`
    guarda as palavras). Perda máxima em qualquer queda: uma janela.
11. **Cancelar, erro, tentar de novo.** `cancel_requested` lido entre janelas → `cancelado`, artefatos apagados, evento.
    Exceção → `erro` com a última linha do traceback (inteiro no log), `attempts + 1`; "tentar de novo" volta a `pendente`
    mantendo artefatos. Erros permanentes (token ausente, termos de cada repositório gated não aceitos, extra `pipeline`
    não instalado, sem rede) não contam tentativa e têm mensagem própria.
12. **Reprocessar.** Job `kind = reprocess`; `segments` e `clusters` têm `job_id`; o novo job escreve os seus e, no último
    passo, numa só transação, apaga os do anterior e atualiza a reunião. Antes, as decisões humanas (pessoa, status,
    mesclas) são transportadas por cosseno entre centroides do mesmo modelo; o que não casar vira `revisions` com nota.
    `voice_embeddings` de confirmação ficam (`segment_id` posto a NULL). Só o usuário dispara (CP3).
13. **Revisão mínima (prévia da F3).** Card por cluster: nome sugerido, similaridade e margem, minutos de fala, botão de 5 s
    decodificado só no clique numa `_Task`. Teclado (U-006): Enter confirma, setas navegam, "outra pessoa", "nova pessoa",
    "é a mesma que o cluster X" (`clusters.merged_into`; segmentos mantêm `cluster_id`), "ignorar". Cada confirmação insere
    `voice_embeddings`, recalcula o centroide, registra `revisions`. "Perfil de um trecho" nesta fase = um turno já
    embedado pelo job (P6); trecho livre `[t0, t1]` fica para a F6 como job `embed_range`. Separar perfis: F6. Nada de cargo
    ou ficha. Reforço automático do perfil do usuário com o L de cada reunião, `source = automatico` (P3).
14. **Leitura mínima (Q6).** `QListView` com modelo (não milhares de widgets): `[mm:ss] Nome — Cargo: texto`; clique toca a
    partir do início da primeira palavra menos 150 ms.
15. **Fila na interface.** `QTimer` no controlador lê `processing_jobs` a cada 2 s com job ativo e a cada 30 s sem;
    `QueueDialog` mínimo pela bandeja (reunião, etapa, porcentagem, estimativa, pausar, retomar, cancelar, reprocessar,
    tentar de novo); tooltip "processando 2/6 · 43 %"; `concluído` novo → notificação "pronta para revisão" e abre a revisão;
    no início, aviso de reuniões `processada` sem revisão. Estimativa: `pipeline.rtf_json` = média móvel (α 0,3) do RTF por
    (etapa, modelo) de `timings_json` (audio_s, speech_s, wall_s); sementes Whisper 0,67× sobre fala, pyannote 0,8× sobre
    duração, embeddings 0,05×; "sem estimativa" quando não há medição.
16. **Configurações (CP8, `phase = F2`).** `pipeline.whisper_model`, `whisper_hq`, `glossary`, `threshold` (0,45 a 0,75),
    `margin` (0,01 a 0,20), `models_dir` (padrão `data_dir_suggestion()/models`), `torch_threads` (padrão `cpu_count − 2`,
    mínimo 1), `auto_start`, `pause_on_record`, `diar_window_s`, `whisper_window_s`; internas `paused_until`, `paused_manual`,
    `rtf_json`. Token HF em `providers` (`id = huggingface`). Grupo "Processamento" no diálogo atual, gerado da tabela.
    `config_json` do job guarda o snapshot das `pipeline.*` no claim ("modelo vale para o próximo job").
17. **Modelos.** `models.py`: `HF_HOME = <models_dir>/hf`, symlink desligado antes de importar, `HF_HUB_OFFLINE=1` quando os
    arquivos existem, download como etapa do job com progresso ("baixando modelo…") e nova tentativa; mensagens próprias
    por falha. Memória logada por etapa (`getrusage` no posix, `GetProcessMemoryInfo` no win32, atrás da plataforma).
18. **Ponto de extensão da F3.** `Transcriber` é Protocol; `LLMAudioTranscriber` recebe a mesma `AudioWindow` e devolve
    `Word`s do provedor, `name` começando por `llm:`; jobs `kind = llm` ou etapas extras depois de `segments`;
    `segments.text_llm` já existe. A Configurações já mostra a escolha do transcritor com a opção LLM desabilitada.

## Fora de escopo
LLM e ata (F3); ao vivo (F4); RAG, histórico e chat (F5); telas completas, separar perfis, trecho livre (F6); Linux e
macOS (F7, mas `platform/common/process.py` já é portável).

## Cláusulas
CP3 reprocessar troca atômica só por ação do usuário; artefatos de job são dado derivado · CP5 pyannote diariza e dá
embeddings; perfis persistentes e refinados · CP8 item 16 editável; `config_json` prova "vale para o próximo job" · CP9
artefatos e tudo o mais no SQLite; modelos na pasta própria (exceção registrada) · CP10 token em `providers` · CP11
parte "depois da reunião" sem LLM; a LLM nunca sobrescreverá alta confiança · CP12 U-005, U-006, U-007 parcial · CP14
CPU int8 · CP15 `spawn_detached` e `lower_own_priority` em `platform/` · CP16 commit por lote · CP17 `slots`, PEP 695,
`match` nas mensagens do runner, `itertools.batched`, `autocommit`.

## Dados (migração v4 do main, em Python, idempotente, dentro de `BEGIN IMMEDIATE` com a versão)
Novas: `processing_jobs` (item 2), `clusters` (id, meeting_id, job_id, channel, cluster, speech_ms, centroid BLOB,
person_id, similarity, margin, status, merged_into), `job_artifacts` (item 10). Colunas: `segments` + `words_json`,
`cluster_id`, `job_id`; `voice_profiles` + `threshold`, `sim_mean`, `sim_std`; `voice_embeddings` + `platform`;
`meetings` + `language`. Índices: `segments(meeting_id, t_start_ms)`, `clusters(meeting_id)`, `processing_jobs(status,
id)`, `voice_embeddings(profile_id)`, único `voice_profiles(person_id, model)`. Enums novos em `model.py`: `JobStep`,
`ClusterStatus`, `EmbeddingSource`, `SegmentSource`, `Attribution.CHANNEL`, eventos de job e `PROFILE_CONFIRM`.
Repositórios: `store/jobs.py`, `store/profiles.py`, `store/people.py`, `store/segments.py`; `Core(role=APP | WORKER |
READONLY)`. Detalhe em `dados.md` após a implementação.

## Comportamento
1. Parar → `_on_stopped` cria o job → lança o worker se couber. 2. Worker: prioridade baixa, log próprio, claim,
heartbeat, etapas na ordem do item 9 com artefato por janela; lease e cancelamento lidos entre janelas. 3. Fim:
`processada`; sem cluster abaixo do limiar, a revisão abre "tudo confirmado". 4. Revisão concluída: `revisada`; perfis
atualizados. 5. Reprocessar: item 12. 6. Queda: a próxima execução retoma pelos artefatos; logoff mata o worker
(retomada) e o app fechado deixa o worker terminar a fila (P2).

## Aceite (G2), nas três reuniões do G1
80 % ou mais do tempo de fala de R com a pessoa certa após uma rodada de confirmações · na terceira reunião, quem foi
confirmado nas duas anteriores é nomeado sozinho em 70 % ou mais do tempo · tempo de relógio do job ÷ duração da reunião
(de `timings_json`) até 1,5× com o app de reunião fechado; a estimativa usa a taxa de fala do VAD; se o pyannote medir
acima de 0,8× no B4, vale o plano B de P5 · clique numa fala toca o áudio com desvio abaixo de 200 ms · texto pt-BR
legível sem edição em 9 de 10 falas ao acaso · memória do worker (RSS logado) abaixo de 2,5 GB numa reunião de 3 h ·
suíte de testes abaixo de 5 s sem modelos.

## Riscos
pyannote não medido (R-1: sonda de RTF no primeiro job; plano B) · memória do clustering cresce com o quadrado em áudio
longo (R-2: janela de 20 min, descarregar ocioso) · dois processos no SQLite (R-3: WAL, `busy_timeout`, transações
curtas, áudio `mode=ro`, até 8 anexos) · modelos sem rede ou token (R-4: item 17) · codec por plataforma (R-5: Q9, medir
no G2) · fala sobreposta (R-6: só turnos limpos; `confidence` baixa marcada) · gravação com worker rodando (R-8: lease,
janela corrente termina em até 7 min de relógio no Whisper e em cerca de 16 min na diarização) · interface congelando (R-9: só metadados; áudio no clique) ·
PyInstaller (R-10: `worker_command` resolve `sys.frozen`; venv e atalho continuam) · alucinação em silêncio (R-11: VAD,
pular janela sem fala) · dois workers (R-12: claim com `NOT EXISTS` fresco; `poll()`) · clique com desvio (R-14:
primeira palavra − 150 ms).

## Testes sem modelos (WSL e CI)
`ScriptedTranscriber` (roteiro `(t0, t1, texto)`), `ToneDiarizer` (frequência dominante por 500 ms) e `ToneEmbedder`
(vetor determinístico de 256 dimensões) sobre os tons de 440, 220 e 330 Hz da `FakePlatform`; `LISTENER_PIPELINE=fake`
para o worker em subprocesso real. Cobrir: v4 com rollback no meio; `JobsRepo` (claim com dois `Store`, heartbeat
vencido, `attempts`, cancel, artefatos); `iter_windows` (buraco, corte em silêncio, reamostragem, `tracemalloc` abaixo
de 2× janela); `Segmenter`; `ProfileMatcher` (LSA, limiar, margem, 10 s, Welford, clip); `stitch` (rótulos trocados;
falante novo); runner de ponta a ponta com retomada, cancel e lease; reprocessar mantém decisões e falha mantém os
antigos; lógica da revisão; `--worker --once`; `QueueDialog`, revisão por teclado e leitura com clique (pytest-qt);
controlador lança o worker e renova a lease. Só com token e áudio real (B4, B5, G1): RTF, RSS, Q8, similaridades intra e
inter-pessoa, precisão, desvio do clique, legibilidade.

## Lotes de implementação ([chute] 8,5 a 10 dias de foco; testes verdes a cada commit)
L1 dados (v4, enums, repositórios, `StoreRole.WORKER`, `pipeline.*`) 1 d · L2 núcleo puro (`types`, `base`, `windows`,
`align`, `profiles`, `stitch`, fakes) 1,5 d, paralelo a L1 · L3 runner 1,5 d · L4 processo e app (`--worker`,
`worker.py`, `models.py`, plataforma, controlador, fila na bandeja) 1,5 d · L5 adaptadores reais (`whisper.py`,
`pyannote.py`, sonda Q8; verificados no Windows com token) 1 d mais B4 · L6 revisão e leitura 1,5 d · L7 G2 nas três
gravações 0,5 d mais o dono.

## Respostas do dono (24/10, D-041): tudo nos padrões
- P1 Diarizar e identificar antes de transcrever, com a revisão de falantes aberta enquanto o Whisper roda? Padrão: sim.
- P2 O worker continua processando depois que o app é fechado, até terminar a fila (logoff encerra)? Padrão: sim.
- P3 Reforçar automaticamente o perfil do próprio usuário com o canal L de cada reunião (`automatico`, sem entrar no
  centroide até uma confirmação)? Padrão: sim.
- P4 Participantes esperados como desempate quando a margem for menor que 0,05? Padrão: sim.
- P5 Se o pyannote medir acima de 0,8× no B4: aceitar até 2× (processamento noturno) ou Whisper `small` no canal L? Padrão:
  aceitar até 2× e registrar no G2.
- P6 "Perfil de um trecho" na F2 é um turno já identificado pelo job; o trecho livre fica para a F6? Padrão: sim.

[fato] faster-whisper turbo int8 com palavras 0,67× em fala contínua; `RETURNING` no SQLite 3.45 e 3.49; o Whisper
guarda 224 tokens de prompt; a `FakePlatform` gera tons distintos por canal. [exp] cosseno da mesma voz entre 0,7 e 0,9
no mesmo áudio; o usuário fala 20 a 40 % do tempo e o R 50 a 70 %; pyannote entre 0,6× e 1,0× em CPU. [chute] esforço
dos lotes.

## Implementação (2026-10-24)
Novos: `store/{jobs,profiles,people,segments}.py`,
`pipeline/{types,base,windows,align,profiles,stitch,runner,worker,models,estimate,review,whisper,pyannote}.py`,
`platform/common/process.py`, `ui/{queue,review,transcript,tasks}.py`, `tests/fake_pipeline.py` e
`tests/test_{jobs,profiles,pipeline_core,runner,worker,adapters,review}.py`. Alterados: `model.py`,
`store/{schema,core,db,meetings,settings}.py`, `config/defaults.py`, `platform/{base,win32/platform}.py`, `__main__.py`,
`ui/{controller,tray,settings_min}.py`, `tests/{conftest,fake_platform,test_store,test_ui}.py`. Itens 1 a 18 feitos.
Desvios pequenos: `clusters.model` (transporte e Q8 precisam do modelo do centroide) e a melhor sugestão guardada também
em `sem_nome` ("parecida com"); cluster curto acima do limiar vira `ambíguo`; embeddings do R na mesma passagem da
diarização (uma decodificação por janela, tempos separados por etapa); "tentar de novo" zera `attempts` (o limite de 3 é
para quedas); o app relança o worker pelo heartbeat fresco mais 60 s de espera (não guarda o `Popen`); o cluster `L` do
usuário nasce `confirmado` e fica fora da revisão; o corte de janela cai no ponto do silêncio mais perto do nominal; o
pyannote baixa pelo cache em `HF_HOME` dentro da pasta de modelos, com marca `.ready` e modo offline após a primeira
carga completa. Medido no WSL: `iter_windows` com janela de 600 s tem pico de 1,35x a janela (51,9 MB) e 5x com janela
de 60 s sobre pedaços de 60 s (19 MB; a busca de silêncio puxa o pedaço seguinte); runner falso em 13 ms para 3 s de
Revisão adversarial do diff (25/10, R2-01 a R2-27 e segunda passada N-01 a N-03), tudo corrigido com teste por item: o
app relança o worker quando há job ativo sem heartbeat fresco; `uri=True` em todas as conexões; segmentos numa só
transação com os parciais apagados; clusters relidos do banco antes dos segmentos (confirmação durante o Whisper chega a
`segments.person_id`); transporte não sobrescreve decisão tomada no reprocessamento; um vetor `automatico` por reunião;
`resolve_person` só com `auto` ou `confirmado` (leitura e mescla); correção de pessoa invalida o vetor anterior (`source
= invalidado`, `cluster_id` em `voice_embeddings`); cancelar sem decisão limpa clusters, com decisão guarda diarização,
embed do L e costura; `revisada` só volta a `processada` quando um reprocessamento deixa vozes novas pendentes; Sair
gravando enfileira; token no `deps_key`; `request_cancel` atômico; `auto` só para o melhor da linha e desempate sem
repetir pessoa; job vigente = concluído mais recente; Welford com `calib_n`; offline no módulo vivo; progresso por canal
no preset sala; retomada sem decodificar janelas prontas; `job_id` nulo preservado na troca; dimensão do embedding pelo
primeiro vetor; reuniões sem job entram na fila ao abrir; notas de transporte estáveis; `reload` da revisão só quando os
clusters mudam. Migração v5 (`voice_profiles.calib_n`, `voice_embeddings.cluster_id`): colunas da revisão em versão
própria, para um banco já em v4 ganhá-las. Desvio de spec decidido pelo dono: `pipeline.diar_window_s` padrão 1200 s
(era 3600; R-8: a janela corrente termina em cerca de 16 min a 0,8x; 3 emendas por hora pela costura já testada); item
4, R-2 e R-8 alinhados.
áudio; suíte de 128 testes em 4,1 s (85 em 3,1 s antes), ruff e mypy limpos (Linux e `--platform win32`). Só o Windows
com token (B4) verifica: carga e RTF do faster-whisper e do pyannote community-1, `speaker_embeddings` e o id do modelo
de embedding (Q8, logado na primeira execução), wespeaker por turno, download com `HF_HOME` sem symlink, erros
permanentes reais (termos não aceitos, token inválido, sem rede), `CREATE_NO_WINDOW |
BELOW_NORMAL_PRIORITY_CLASS` (sem `DETACHED_PROCESS`, D-056), `SetPriorityClass`, `GetProcessMemoryInfo`, o worker vivo após fechar o app (P2) e o G2.