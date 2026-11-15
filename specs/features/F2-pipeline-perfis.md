# F2 — Pós-reunião: transcrição, diarização e perfis de voz

Status: implementada · Aprovada em: 2026-10-24 · Decisões: D-028, D-036, D-041, D-056 ·
CPs: CP3, CP5, CP8, CP9, CP10, CP11, CP12, CP14, CP15
Quando ler: ao mexer na fila, no worker pesado, na diarização, nos perfis de voz ou na transcrição.
Tamanho-alvo: 6 KB.

## Objetivo
Depois do Parar, a gravação vira falas com texto, tempo e pessoa: L é o usuário; R é diarizado e nomeado por perfis
de voz persistentes, que cada confirmação reforça.

## Comportamento
- Parar enfileira um job `meeting` (`pipeline.auto_start`) e o app lança o worker pesado (`--worker`): solto, sem
  janela, em prioridade baixa, fora da gravação e sem outro vivo. Ele sai após 60 s ocioso e sobrevive ao app.
- Heartbeat a cada 15 s; vencido há mais de 60 s, o job volta a `pendente`; esgotadas 3 tentativas, vira `erro`.
- Gravando, a fila pausa por lease: `pipeline.paused_until` = agora + 10 min, renovada a cada 60 s
  (`pipeline.pause_on_record`); vencida, o job segue. Pausa manual: `pipeline.paused_manual`.
- Ordem: diarizar R com os embeddings, casar perfis (a revisão já abre), transcrever R, transcrever L, segmentos.
  A reunião vira `processada` e, concluída a revisão, `revisada`.
- Diarização: pyannote community-1 em CPU, em janelas de `pipeline.diar_window_s` (1200) com 30 s de sobreposição,
  costuradas pelos centroides (cosseno de 0,5); com n participantes esperados, 1 a n + 1 falantes, nunca exato.
- Transcrição: faster-whisper int8 em CPU, `pipeline.whisper_model` (`large-v3-turbo`; `small`, `medium`), em
  janelas de `pipeline.whisper_window_s` (600) cortadas no silêncio; janela sem fala não chama o modelo; prompt com
  `pipeline.glossary`, participantes e título; idioma da reunião (F5.3).
- Perfis: similaridade no limiar (`pipeline.threshold` 0,60 ou o da pessoa, de 0,45 a 0,75) dá `auto`; margem
  abaixo de `pipeline.margin` (0,05) dá `ambíguo`, desempatado pelos participantes esperados; voz com menos de 10 s
  de fala nunca é `auto`; o resto é `sem_nome`. Referências e limiar por pessoa: F5.15.
- L é o usuário (`attribution = canal`) e reforça o perfil dele como `automatico`, fora do centroide; na sala
  presencial (U-015), L é diarizado como o R.
- Cada janela vira artefato ao terminar: a queda perde no máximo uma. Cancelar é atendido entre janelas; "Tentar de
  novo" zera as tentativas e mantém os artefatos. Token ausente, termos não aceitos, extra `pipeline` ausente e
  falta de rede não contam tentativa (sem token: F5.16).
- Reprocessar: o job `reprocess` grava com `job_id` novo, transporta as decisões humanas por cosseno de 0,8 e troca
  tudo numa transação no fim; se falha, valem os antigos.
- Confirmar uma voz grava um vetor `confirmacao` e refaz o perfil; a correção deixa o anterior como `invalidado`.
- `config_json` guarda as `pipeline.*` no claim: a mudança vale no próximo job. Trecho livre (`embed_range`): F6.

## Interface
- Fila, pela bandeja (U-005): reunião, etapa, porcentagem, estimativa (`pipeline.rtf_json`); pausar, retomar,
  cancelar, tentar de novo, reprocessar. O app a lê a cada 2 s com job ativo e a cada 30 s sem.
- Revisão de falantes (U-006), por teclado: um cartão por voz, com a sugestão e 5 s de áudio; mesclar usa
  `clusters.merged_into`. Desenho atual: F5.4 e F5.11.
- Transcrição: `[mm:ss] Nome — Cargo: texto`; o clique toca da primeira palavra menos 150 ms.

## Dados
- Main v4: `processing_jobs`, `clusters`, `job_artifacts`; `segments` + `words_json`, `cluster_id`, `job_id`;
  `voice_profiles` + `threshold`, `sim_mean`, `sim_std`; `voice_embeddings` + `platform`; `meetings` + `language`;
  único `voice_profiles(person_id, model)`. v5: `voice_profiles.calib_n`, `voice_embeddings.cluster_id`.
- Chaves `pipeline.*`; eventos `job_*` e `profile_confirm`.

## Código
- `listener/pipeline/runner.py`, `jobbase.py`, `worker.py`: etapas do job, heartbeat e pausa, processo.
- `listener/pipeline/windows.py`, `align.py`, `stitch.py`, `profiles.py`, `review.py`: lógica pura.
- `listener/pipeline/whisper.py`, `pyannote.py`, `models.py`: adaptadores e download.
- `listener/store/jobs.py`, `profiles.py`, `segments.py`: fila e artefatos, perfis, falas.
- `listener/ui/queue.py`, `review.py`, `transcript.py`: telas.
- Testes: `tests/test_runner.py`, `tests/test_jobs.py`, `tests/test_profiles.py`.

## Invariantes
- CP3: reprocessar só por ação do usuário, com troca atômica; artefato de job é dado derivado, apagado ao concluir.
- CP5: um perfil por pessoa e modelo; só `cadastro` e `confirmacao` entram no centroide.
- CP9 e CP10: token do Hugging Face em `providers` (`id = huggingface`); modelos em `pipeline.models_dir`.
- CP11: nenhuma LLM aqui; `Transcriber` é Protocol, o ponto de extensão da F3.
- CP15: o worker nasce por `spawn_detached`, sem `DETACHED_PROCESS`, que abria uma janela preta por worker (D-056).
- Nada que o app importa pode importar torch, `whisper.py`, `pyannote.py` ou `models.py`: a suíte roda sem torch
  (`LISTENER_PIPELINE=fake`).
- Nunca transação em volta de computação; uma janela de áudio em memória por vez.
- O worker não migra o banco (versão diferente, recusa), abre o áudio em `mode=ro` e tem log próprio.
- Os clusters são relidos antes dos segmentos: a confirmação feita durante o Whisper chega às falas (R2-04).

## Aceite
- 80 % ou mais do tempo de fala de R com a pessoa certa após uma rodada de confirmações; na terceira reunião, quem
  foi confirmado nas duas anteriores é nomeado sozinho em 70 % ou mais do tempo.
- Tempo do job ÷ duração da reunião (`timings_json`) até 1,5×, com o app de reunião fechado.
- O clique numa fala toca o áudio com desvio abaixo de 200 ms; texto legível sem edição em 9 de 10 falas ao acaso.
- RSS do worker abaixo de 2,5 GB numa reunião de 3 h.
