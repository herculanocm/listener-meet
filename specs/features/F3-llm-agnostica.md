# F3 — LLM agnóstica

Status: implementada · Aprovada em: 2026-10-25 · Decisões: D-020, D-043 · CPs: CP1, CP2, CP3, CP8, CP10, CP11, CP14
Quando ler: ao mexer no job `llm`, na identidade por contexto, na ata ou no chat da reunião. Tamanho-alvo: 6 KB.
Tipos, protocolo, esquemas, retentativa e custo da camada: `specs/llm-contrato.md`.

## Objetivo
A LLM sugere o nome das vozes pendentes, o título, a ata, os resumos e a transcrição revisada, e responde perguntas
sobre a reunião; trocar de provedor (Gemini, OpenAI, Anthropic) é só configuração.

## Comportamento
- Ao concluir, o job pesado enfileira um job `llm` (`params_json = {tasks, parent_job_id}`) no worker leve (`--worker
  --light`, sem torch, sai após 600 s ocioso), que roda durante a gravação e com o app fechado.
- Automático (`llm.auto_tasks`): `identify`, `title` e `revise`; após reprocessar, `identify` e `revise`. Ata, executivo
  e pré-resumo só a pedido (F5.6); a saída sai no idioma da reunião (F5.3).
- Falha parcial: o que ficou pronto fica gravado, o job vira `erro` com a lista e "tentar de novo" refaz só o resto.
- Sem provedor com chave ou sem falas, nenhum job `llm`: a reunião fica `processada`; sem chave, um aviso por sessão.
- Job cujo pai não é o job vigente, ou sem segmentos: erro permanente, sem chamada; ao encadear, os jobs `llm` pendentes
  de pai antigo são cancelados e o pedido manual passa ao job novo (R3-02).
- Identidade: só vozes `ambíguo` ou `sem_nome` não mescladas, com a evidência conferida contra o segmento.
- Ata: uma `action_items` por ação, com o segmento da evidência, prazo absoluto e dono só quando o nome casa com
  `people` ou com os esperados; sem evidência válida, a marca "sem evidência".
- "Gerar de novo" vai pela fila, cria a versão `atual` e marca a anterior e as ações dela `superada`; desabilitado com
  job `llm` ativo. Após reprocessar, a ata antiga fica, com aviso.
- Transcrição acima de 60 % da janela do modelo, ou "request too large": modo hierárquico, em blocos de 40 % da janela.
- `revise`: blocos de 60 segmentos com 5 de contexto; descarta a fala com semelhança (`difflib`) abaixo de 0,6 (0,4 com
  menos de 20 caracteres), número de dígitos diferente ou outro idioma.
- Glossário no warm-up (`llm.glossary_enabled`): 1,5 s de debounce, uma chamada em voo, resposta velha descartada; os
  termos entram primeiro no `initial_prompt` do Whisper.
- `pipeline.transcriber = llm` (D-020) só com `AUDIO_IN` no modelo rápido: janelas em FLAC 16 kHz mono.
- Acima de `llm.cost_alert_usd` (2,00, com o que a reunião já gastou), `executive` e `revise` saem do job automático
  (`timings_json.llm.skipped`) e a tela oferece gerá-los.

## Interface
- Revisão: o card mostra a sugestão pela voz e a pelo contexto; Enter aceita a da voz (sem ela, a da LLM) e `L`, a da
  LLM; `clusters.decision_source` guarda o aceito. A leitura só mostra nome `auto` ou `confirmado`.
- Ata, resumo e ações editáveis (`text_edited`); glossário editável (U-018); selo de custo na revisão, na fila e no
  aviso (U-020); transcrição revisada com a caixa para ver a original.
- Chat com streaming e cancelamento (U-019); cada `[mm:ss]` liga ao segmento mais próximo, até 5 s, e o clique toca.
- Configurações: sub-aba por provedor (modelos rápido e forte, preços, gasto do mês), "Testar conexão", provedor ativo
  para texto e para embeddings, aviso de que o texto vai ao provedor (CP1).

## Dados
Migração v6: `llm_calls`, `chat_messages`, `llm_prices`; colunas `llm_*` e `decision_source` em `clusters`, `glossary` e
`expected_people_json` em `meetings`, modelos em `providers`. Chaves `llm.*` em `listener/config/defaults.py`.

## Código
- `listener/llm/`: contrato, adaptadores, prompts, redação, custos, `glossary_task.py`, `chat_task.py`.
- `listener/pipeline/llm_steps.py`: `LlmJobRunner`, encadeamento, alerta de custo, modo hierárquico.
- `listener/pipeline/llm_audio.py`: `LLMAudioTranscriber`; `listener/pipeline/worker.py`: as classes de fila.
- `listener/ui/providers.py`, `listener/ui/llm_tabs.py`, `listener/ui/chat.py`: as telas.
- `tests/test_llm_steps.py`, `tests/test_llm_core.py`, `tests/test_llm_adapters.py`, `tests/test_llm_review.py`.

## Invariantes
- CP11: `set_llm_suggestion` é o único ponto que escreve `llm_*` e recusa voz `auto`, `confirmado` ou mesclada.
- CP10: chave lida na hora, fora de `config_json`, blobs, eventos, erros e log; a redação cobre o traceback encadeado
  e a senha do `llm.proxy_url` (R3-04).
- CP3: `segments.text` intacto; o reprocessamento leva `text_llm` ao segmento de mesmo canal, tempo e texto.
- CP2: job em curso termina com o snapshot (`LlmJobConfig`); o próximo usa o provedor ativo.
- O job `llm` escreve com o `job_id` do pai e não muda o `current_job_id`.
- A recarga nunca apaga edição não salva, só acende "recarregar?"; "Salvar edição" grava sobre a versão atual (R3-01).
- `expected_people_json` não depende da LLM; a resposta tardia do glossário religa a chamada à reunião (R3-11).
- Timeout por pedido (cliente: 600 s); resposta cortada reenvia uma vez com o teto dobrado e soma o uso (R3-03, R3-07).
- `providers.model` é o de embeddings e nunca vira o modelo rápido (R3-09).
- Limite aceito: `llm.audio_second_opinion` existe, mas `identify` não envia áudio.

## Fora de escopo
`base_url` para serviços compatíveis com a OpenAI (CP14).

## Aceite
Nas três reuniões do G1: 2 de 3 atas aceitas com menos de 20 % dos caracteres alterados em `text_edited` · 70 % ou mais
das vozes pendentes com pista na transcrição com `decision_source = usuario_llm` · uma ata com outro provedor só pelas
Configurações · "Testar conexão" em menos de 10 s · nenhuma chave em log, erro ou `config_json` (teste).
