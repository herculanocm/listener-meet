# F2 — Pós-reunião: transcrição, diarização e perfis de voz

Status: aprovada (código depois do G1, ordem do backlog) · Aprovada por Herculano em: 2026-10-24 (Q1 a Q9 nos padrões, D-036) · Fase e gate: F2 · G2 ·
CPs: 3, 5, 8, 9, 10, 11 (parte "depois da reunião", sem a LLM), 12, 13, 14, 15
Quando ler: ao trabalhar no pipeline pós-reunião. Tabelas: `dados.md`. Tamanho-alvo: 8 KB.

## Objetivo
Depois do Parar, transformar o áudio em falas com texto, tempo e pessoa: L é o usuário; R é diarizado pelo pyannote e
nomeado por perfis de voz persistentes. Abaixo do limiar vai para uma revisão mínima; cada confirmação reforça o perfil.
A LLM entra na F3 sem mudar este desenho.

## Escopo
1. **Fila.** Um job por reunião (`processing_jobs`), criado ao Parar e iniciado sozinho (D-014), com etapas retomáveis:
   transcrever L, transcrever R, diarizar R, embeddings, casar perfis, montar segmentos. Progresso, pausar, retomar,
   cancelar e reprocessar (U-005). Roda num subprocesso de prioridade baixa (`python -m listener.pipeline.worker`) que lê
   e escreve só no banco (Q1). Pausa sozinha durante gravação e retoma ao Parar (Q7).
2. **Transcrição (D-028).** faster-whisper large-v3-turbo int8 com `word_timestamps`, `vad_filter`, idioma por reunião
   (D-013) e glossário fixo no `initial_prompt`; opção "alta precisão" com o wav2vec2 do WhisperX; modelo escolhível
   (turbo padrão; small e medium). Modelos baixados para a pasta de modelos configurável com `local_dir`, sem symlink.
3. **Diarização de R.** pyannote community-1 em CPU, saída exclusiva; `num_speakers` (ou mínimo e máximo) quando há
   participantes esperados; turnos por cluster (`SPK_n`) válidos só na reunião.
4. **Embeddings.** Modelo de embedding do pyannote (wespeaker-voxceleb-resnet34-LM, 256 dimensões) sobre trechos limpos
   de cada cluster (1,5 s ou mais, sem sobreposição); centroide por cluster. Ver Q8.
5. **Perfis persistentes (CP5).** `voice_profiles` (centroide por pessoa e modelo) e `voice_embeddings` (vetores por
   trecho; origem cadastro, confirmação ou automático). Casamento por cosseno com atribuição sem repetição
   (`linear_sum_assignment`). Acima do limiar (0,60) nome direto (`attribution = pyannote`); margem abaixo de 0,05 entre o
   melhor e o segundo marca "ambíguo" e vai para a revisão; abaixo do limiar fica sem nome (a F3 sugere por contexto).
   Clusters com menos de 10 s nunca são nomeados sozinhos (Q5). Autocalibração: média e desvio das similaridades
   confirmadas ajustam o limiar do perfil dentro de [0,45; 0,75]. Criar perfil de um trecho [t0, t1]: vira perfil novo ou
   soma a um existente.
6. **Segmentos.** As palavras do Whisper entram no turno do pyannote que cobre o centro de cada uma; `segments` com
   texto, tempos, canal, cluster, pessoa, `role_at_time` (D-016), confiança, atribuição, `source = final` e `words_json`.
   L recebe sempre `is_me`; com o preset sala (U-015) o L também é diarizado.
7. **Revisão mínima** (prévia da tela da F3): clusters com nome sugerido, similaridade, margem e 5 s de áudio; teclado
   (U-006): Enter confirma, setas navegam, "outra pessoa", "nova pessoa", "é a mesma que o cluster X" (mescla), "ignorar".
   Cada confirmação insere `voice_embeddings`, recalcula o centroide e registra em `revisions`. Nada de cargo ou ficha.
8. **Leitura mínima da transcrição** (`[mm:ss] Nome — Cargo: texto`; clicar toca) para medir o G2 (Q6).
9. **Ponto de extensão** `Transcriber`: `FasterWhisperTranscriber` agora; `LLMAudioTranscriber` na F3 (D-020); a
   Configurações já mostra a escolha, com a opção LLM desabilitada.
10. **Configurações (CP8):** modelo e precisão do Whisper, glossário fixo, limiar e margem, pasta de modelos, threads do
    torch (núcleos menos 2), processar ao parar, pausar a fila em gravação, token do Hugging Face (Q2).

## Fora de escopo
LLM e ata (F3); ao vivo (F4); RAG, histórico e chat (F5); telas completas (F6); Linux e macOS (F7).

## Cláusulas
CP3 só acrescenta (reprocessar apaga só clusters e segments da reunião) · CP5 pyannote diariza e dá embeddings; perfis
persistentes e refinados; perfil de um trecho · CP8 item 10 editável; modelo vale para o próximo job · CP9 tudo nas
tabelas; modelos na pasta de modelos; cache do HF não é usado · CP10 token HF em `providers` · CP11 parte "depois da
reunião" sem LLM; a LLM nunca sobrescreverá alta confiança · CP12 U-005, U-006 e U-007 parcial · CP14 CPU int8, threads
limitadas · CP15 pipeline portável; prioridade do subprocesso pela camada de plataforma (`BELOW_NORMAL_PRIORITY_CLASS`
no Windows, `nice` nos outros).

## Interface
Bandeja e janela principal mostram a fila (reunião, etapa, porcentagem, tempo estimado pelas medições; pausar, retomar,
cancelar, reprocessar). Ao terminar: notificação "pronta para revisão". Revisão: um card por cluster (nome, similaridade
e margem em cinza, botão de 5 s, minutos de fala); Enter confirma e avança; "Concluir" grava e abre a leitura. Erro: card
vermelho com "tentar de novo"; o áudio nunca é tocado pelo erro.

## Dados
Novas: `processing_jobs`, `clusters`; `segments` ganha `words_json` e `cluster_id`; `voice_profiles` ganha `threshold`,
`sim_mean` e `sim_std`; `voice_embeddings` ganha `platform` (Q9); `providers` linha `huggingface`; `settings` `pipeline.*`;
`revisions` para toda mudança de pessoa. Detalhe em `dados.md`.

## Comportamento
1. Parar cria o job pendente; o app inicia o worker se não houver gravação. 2. O worker roda etapa a etapa gravando
`step` e `progress`; se cair, retoma da etapa gravada; baixa o modelo que faltar. 3. Fim: `meetings.status = processada`;
sem cluster abaixo do limiar, a revisão abre "tudo confirmado". 4. Revisão concluída: `revisada`; perfis atualizados.
5. Reprocessar: apaga só `clusters` e `segments` da reunião e cria novo job. Dois processos no mesmo banco:
`busy_timeout` de 5 s e transações curtas nos dois lados.

## Aceite (G2), nas três reuniões do G1
80 % ou mais do tempo de fala de R com a pessoa certa após uma rodada de confirmações · na terceira reunião, quem foi
confirmado nas duas anteriores é nomeado sozinho em 70 % ou mais do tempo · processamento até 1,5× a duração em CPU com
o app de reunião fechado · clicar numa fala toca o áudio com desvio abaixo de 200 ms · texto pt-BR legível sem edição
em 9 de 10 falas ao acaso.

## Riscos
pyannote em CPU não medido (B4): acima de 0,8× a duração, o G2 só fecha com Whisper menor ou processamento noturno ·
fala sobreposta e clusters curtos derrubam a precisão · codec e microfone deslocam o embedding (autocalibração; Q9) ·
torch e modelos ocupam 1 a 2 GB (por isso subprocesso) · pegadinhas medidas: symlink do cache HF, torchcodec sem
FFmpeg, proc-tap `frames = -1` (`memory/maquina.md`).

## Perguntas
- Q1 Worker em subprocesso ou thread? Padrão: subprocesso.
- Q2 Token HF em `providers` com campo na Configurações (CP10)? Padrão: sim.
- Q3 Limiar 0,60, margem 0,05, autocalibração em [0,45; 0,75]? Padrão: sim.
- Q4 large-v3-turbo int8 padrão, small e medium como alternativas? Padrão: sim.
- Q5 Clusters com menos de 10 s só na revisão? Padrão: sim.
- Q6 Leitura mínima da transcrição entra na F2? Padrão: sim.
- Q7 Fila pausa em gravação e retoma ao parar? Padrão: sim.
- Q8 Usar os `speaker_embeddings` do próprio pipeline community-1 como centroide do cluster (uma passada a menos),
  deixando o modelo separado para "perfil de um trecho"? Padrão: sim, se a versão instalada expuser o atributo (o card
  público mostra `speaker_diarization` e `exclusive_speaker_diarization`; confirmar com o token, B4) e com o mesmo
  modelo nos dois caminhos.
- Q9 Coluna `platform` em `voice_embeddings` para centroides por (pessoa, plataforma) se o G2 pedir? Padrão: sim.

[fato] faster-whisper turbo int8 com `word_timestamps`: 0,67× em CPU; WhisperX com wav2vec2: 1,11×; a identificação
pronta da pyannoteAI é paga. [exp] cosseno da mesma pessoa com wespeaker entre 0,6 e 0,8; de pessoas diferentes abaixo
de 0,45. [chute] 6 a 8 dias de foco.
