# F3 — LLM agnóstica: identidade por contexto, título, ata, resumos, transcrição revisada, chat e revisão completa

Status: implementada (L0 a L6; G3 e os adaptadores reais dependem da chave no Windows) · Aprovada por
Herculano em: 2026-10-25 (P1 = opções nas Configurações; P2 a P9 nos padrões; D-043) · Fase e gate: F3 · G3 · CPs: 1, 2, 3, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17
Quando ler: ao trabalhar na camada de LLM, na revisão completa ou no chat. Desenho técnico (tipos, protocolo, esquemas,
validação, rede, custo, pinos): `specs/llm-contrato.md`. Tabelas: `dados.md`. O que já existe e esta spec usa: fila e
worker da F2 (`processing_jobs.kind`, `params_json`, `config_json`, artefatos, heartbeat, `retry`), `segments.text_llm`,
`meetings.title_llm`, `clusters`, `summaries` e `action_items` (vazias desde a v1), `providers`, revisão e leitura da F2,
`Task`, `SettingSpec`, `swap_job`, polling da fila a cada 2 s. Tamanho-alvo: 18 KB.

## Objetivo
A LLM vira o cérebro de contexto da reunião: sugere nomes por pistas para as vozes que o pyannote deixou pendentes, propõe
título e pré-resumo, escreve a ata (decisões; ações com dono e prazo; destaques dos marcadores com as notas do usuário;
pendências e próximos passos), o resumo executivo e a transcrição revisada com nomes e cargos, e responde perguntas sobre
a reunião. Trocar de provedor é só configuração (CP2); a LLM nunca substitui uma identificação de alta confiança do
pyannote (CP11); sem rede a reunião continua usável.

## Escopo
1. **Camada de provedores** (`listener/llm/`), conforme `llm-contrato.md`: contrato assíncrono único; adaptadores Gemini
   (inicial), OpenAI e Anthropic; `fake.py` para testes; `RetryingProvider`; validador do projeto; prompts e esquemas do
   projeto, versionados; capacidades por modelo; redação de chave. Os SDKs são importados preguiçosamente e só pelos
   adaptadores; torch continua proibido fora do worker pesado.
2. **Job de LLM próprio.** Ao concluir, o job da F2 (`meeting` ou `reprocess`) enfileira um job `kind = llm` para a reunião
   com `params_json = {tasks, parent_job_id}`; o job da F2 termina exatamente como hoje (reunião `processada`, aviso de
   revisão de falantes sem atraso). O job `llm` roda num **worker leve** (`--worker --light`, sem torch, log próprio
   `listener-llm.log`, sai após 60 s ocioso) numa segunda classe de fila: `claim` e `held_fresh` filtram por classe
   (pesada: meeting, reprocess, embed_range; leve: llm), um worker por classe, e o app lança o leve mesmo durante a
   gravação (rede e JSON não disputam CPU). O `LlmJobRunner` herda de uma `BaseJobRunner` extraída do `JobRunner`
   (heartbeat, checkpoint, falha, progresso) e grava artefato por tarefa e por bloco; `retry` refaz só as tarefas sem
   resultado; `progress` = tarefas prontas ÷ total. `current_job_id` passa a ignorar jobs `llm`; tudo que o job `llm`
   escreve carrega o `job_id` do pai. "Gerar de novo", "tentar de novo só LLM" e "gerar ata de uma reunião antiga" são
   jobs `llm` de uma tarefa (3 a 5 s para começar); chat, glossário e "testar conexão" rodam no app numa `Task`.
3. **Ordem e paralelismo das tarefas.** `identify` primeiro (relendo do banco só os clusters `ambíguo` ou `sem_nome` sem
   `merged_into`; o que o usuário confirmou durante o Whisper não entra); depois `minutes`; depois `executive`,
   `pre_summary` e `title` em paralelo (`asyncio.TaskGroup` sob `asyncio.Semaphore(llm.max_parallel = 2)`); por fim
   `revise` em blocos. Nenhuma tarefa levanta dentro do grupo: cada uma devolve resultado ou erro; prontas ficam
   gravadas; alguma falha → job `erro` com a lista ("ata e executivo falharam: sem rede") e `retry` refaz só as faltantes.
   Tarefas automáticas e opcionais conforme P5.
4. **Glossário no warm-up (handoff §4).** Ao sair do campo título ou participantes (debounce de 1,5 s, uma chamada em voo,
   resposta velha descartada), o app casa os participantes com `people` localmente (casefold, `difflib`) e pede à LLM os
   termos prováveis e o mapeamento do que sobrou; os termos aparecem editáveis antes de gravar (U-018); `create_meeting`
   recebe `glossary` e `expected_people_json`; o glossário da LLM entra primeiro no `initial_prompt` do Whisper (antes do
   fixo, com dedup) e os ids em `expected_people_json` são candidatos primeiro e desempate (D-041 P4). Sem chave ou sem
   rede: aviso discreto, segue sem glossário; a chamada do glossário tem `llm_calls.meeting_id` nulo até a reunião existir.
5. **Identidade por contexto (CP11).** Entrada e saída conforme o contrato; evidência conferida contra o segmento; minuto
   calculado pelo projeto. Resultado em `clusters.llm_person_id`, `llm_name`, `llm_confidence`, `llm_evidence_json`;
   `status` e `person_id` não são tocados; o repositório recusa escrever `llm_*` em `auto`, `confirmado` ou mesclado. O
   card da revisão mostra as duas sugestões com origem e confiança: Enter aceita a do pyannote (motor principal, CP5), a
   tecla `L` aceita a da LLM; card só com sugestão da LLM: Enter aceita a LLM. `clusters.decision_source` registra o que
   foi aceito (mede o G3). Nome novo vira pessoa só na confirmação. Segunda opinião por áudio: capacidade mantida (até 3
   trechos de 10 s dos turnos limpos por cluster, FLAC, mais referências de reuniões anteriores dos candidatos), padrão
   **desligado** (P3), só com modelo que tenha `AUDIO_IN`, medida à parte no G3.
6. **Ata, executivo, pré-resumo, título.** Esquemas do contrato; ata renderizada em Markdown pelo projeto; `action_items`
   uma linha por ação (`summary_id`, `segment_id` da evidência, `owner_person_id` só quando o nome casa com `people` ou
   com participantes esperados, `due_date` absoluta devolvida pela LLM a partir da data da reunião, `status`); item sem
   evidência válida fica com a marca "sem evidência". Título em `meetings.title_llm` (já existe) com `revisions`; os
   demais em `summaries` (`kind`, `data_json`, `text_llm`, `text_edited`, `provider`, `model`, `prompt_version`, `job_id`
   do pai, `call_id`, `status`, `language`). "Gerar de novo" cria linha `atual`, marca a anterior `superada` e as ações
   dela `superada`, tudo em `revisions`; botão desabilitado enquanto há job `llm` ativo para a reunião.
7. **Reuniões longas.** Gatilho por tokens: transcrição acima de 60 % da janela do modelo menos a saída → modo hierárquico
   (blocos de até 40 % da janela cortados em troca de falante; notas estruturadas por bloco em `job_artifacts`; redução
   para a ata; `identify` por bloco só com os clusters presentes); "request too large" do provedor também aciona.
   `llm_calls.strategy` registra. Para 3 h nos modelos atuais o caminho direto basta [exp].
8. **Transcrição revisada.** Blocos de 60 segmentos com 5 de contexto; `{segment_id: texto}`; proteção: descarta quando a
   semelhança (`difflib`) fica abaixo de 0,6 (0,4 para falas com menos de 20 caracteres), quando o número de dígitos
   muda ou quando o idioma parece outro; escrita em lotes numa transação por bloco, só nos segmentos do job pai;
   `words_json` fica o original (o clique toca pela primeira palavra). A leitura mostra a revisada quando existe, com
   alternância para a original (P4). Reprocessamento transporta `text_llm` para o segmento novo com o mesmo canal, tempos
   e texto, e religa `action_items.segment_id` pelo `t_ms` mais próximo.
9. **Transcrição final pela LLM com áudio (D-020).** `LLMAudioTranscriber` implementa `Transcriber`: `asyncio.run()` por
   janela com cliente próprio; FLAC 16 kHz mono (Gemini aceita até 20 MB em linha; 10 min ≈ 8 a 10 MB); provedores sem
   FLAC usam janelas de 5 min em WAV ou MP3 via soundfile; saída em falas com tempos relativos convertida em `Word`s
   **interpoladas** (uma fala de 15 s com dois falantes precisa de palavras para o `Segmenter` decidir pelo centro);
   `prepare_models` não baixa o Whisper nesse modo e `deps_key` inclui transcritor, provedor e modelo. Habilitado nas
   Configurações só quando o modelo rápido do provedor ativo tem `AUDIO_IN`; custo por hora de áudio mostrado antes.
10. **Chat sobre uma reunião.** Painel na leitura (`QSplitter`): pergunta → resposta em streaming (`StreamTask` com sinal
    por pedaço e cancelamento), transcrição como prefixo estável no prompt (aproveita cache dos provedores), citações
    `[mm:ss]` reescritas para o segmento mais próximo e clicáveis (tocam o áudio); histórico em `chat_messages` com
    `citations_json` e custo. Chat sobre todas as reuniões é F5.
11. **Revisão e leitura.** A `ReviewDialog` recarrega por assinatura que inclui `clusters.llm_*`, `summaries` e
    `meetings.title_llm`, numa só consulta; abas de título, pré-resumo, ata, executivo e ações, editáveis (`text_edited`
    → `revisions`), com "gerar de novo" por aba; selo de custo da reunião no cabeçalho da revisão, na coluna Detalhe da
    fila e na notificação ("ata pronta · US$ 0,32"); `_on_job_done` ramifica por `kind`. A leitura nunca mostra sugestão
    da LLM como nome (só `auto` e `confirmado`).
12. **Configurações (CP8).** Grupo `llm.*` gerado da tabela: `output_language` como opção (P1): `pt-BR` padrão, `auto`
    (segue `meetings.language`), `en`, `es`, mais um campo livre para outro código de idioma;
    `audio_second_opinion`, `cost_alert_usd`, `max_parallel`, `glossary_enabled`, `store_payloads`, `proxy_url`,
    `ca_bundle`, `system_certs`, `auto_tasks`. `ProviderTabs`: uma sub-aba por provedor (Gemini, GPT, Claude; a linha
    `huggingface` fica fora): chave em texto aberto (CP10), modelo rápido e modelo forte escolhidos de `list_models()`
    (cache em `providers.models_json`), parâmetros (`providers.params` como JSON), "testar conexão" com latência, modelo
    respondido e erro traduzido (DNS, proxy 407, TLS) numa `Task`; "testar pelo worker" enfileira um job `llm` de tarefa
    `test`; provedor ativo para texto e para embeddings (Anthropic não oferece embeddings); preços editáveis
    (`llm_prices`); aviso "o texto da reunião é enviado ao provedor escolhido". `update_provider` com lista branca (hoje
    `upsert_provider` zera `model` e `params` ao salvar o token HF: corrigir).
13. **Custo (U-009, U-020).** Toda chamada em `llm_calls`; estimativa prévia por tarefa; reunião acima de
    `llm.cost_alert_usd` ganha aviso e as tarefas opcionais pedem confirmação ("executivo não gerado: estimado US$ 0,40,
    acima do alerta; gerar?"). Total do mês na sub-aba.
14. **Embeddings (CP2, D-009).** `embed` com `task` (document, query) e `dim` já no contrato; a indexação é F5; aqui só
    "testar conexão" exercita o provedor ativo de embeddings.
15. **Snapshot e troca de provedor.** O job `llm` grava em `config_json` um `LlmJobConfig` (provedor, modelos, parâmetros,
    `llm.*`; nunca a chave); jobs em curso terminam com o snapshot; o próximo job e o próximo chat usam o novo ativo;
    chave apagada no meio vira erro permanente "chave ausente" sem contar tentativa; `retry` de job `llm` pode descartar o
    snapshot e usar o ativo.
16. **Estimativa de tempo e eventos.** `remaining_seconds` devolve nulo (ou média das latências de `llm_calls`) para jobs
    `llm`; marcos em `events` só com nomes e contagens, nunca texto de prompt ou resposta; `json_default` ampliado para
    as dataclasses do contrato (`asdict`, `frozenset` → lista ordenada).

## Fora de escopo
Resumo parcial ao vivo (F4); RAG, chat sobre todas as reuniões, exportação (F5); telas completas, separar perfis, override
de prompts pelo usuário (F6); embeddings locais (D-021, F5); `base_url` para serviços compatíveis com OpenAI (P9).

## Cláusulas
CP1 sem avisos regulatórios; aviso factual de que o texto sai da máquina · CP2 contrato único, adaptadores trocáveis,
prompts, esquemas e render do projeto, embeddings trocáveis · CP3 "gerar de novo" acrescenta e marca, nunca apaga; `text`
original intacto; `text_llm` transportado no reprocessamento · CP8 tudo do item 12 editável em uso · CP9 `summaries`,
`action_items`, `llm_calls`, `chat_messages`, `llm_prices`, glossário no SQLite · CP10 chaves em `providers`, visíveis ·
CP11 a LLM só escreve `llm_*` em pendentes, com recusa no repositório; Enter prefere o pyannote · CP12 U-018 a U-020 ·
CP14 Gemini pago por padrão; GPT e Claude são escolha; sem `base_url` · CP15 nada de SO; `truststore` é portável · CP16
commit por lote · CP17 `TaskGroup`, `asyncio.timeout`, `Semaphore`, dataclasses `slots`, `StrEnum`, PEP 695.

## Interface e experiência (CP7, CP12)
- Warm-up: linha "Termos prováveis" editável, preenchida em até 5 s; "sem chave" é link para as Configurações.
- Revisão: card "pyannote: Ana (0,63) · LLM: Rodrigo (0,8) 'valeu, Rodrigo' aos 12:03 ▶"; Enter aceita o pyannote, `L` a LLM.
- Leitura: alternância "revisada | original"; painel de chat com `[mm:ss]` clicáveis; "gerar de novo" por aba.
- Fila: "LLM: ata (2 de 4)" e custo acumulado; notificação por tipo de job.
- **U-018** glossário proposto editável no warm-up. incluir? sim/não · **U-019** streaming no chat e no "gerar de novo".
  incluir? sim/não · **U-020** selo de custo na revisão, na fila e na notificação. incluir? sim/não

## Dados (migração v6 do main; detalhe final em `dados.md` após a implementação)
`summaries` + `data_json`, `prompt_version`, `job_id`, `call_id`, `status`, `language` · `action_items` + `summary_id`,
`segment_id`, `job_id`, `status`, `created_at` · `clusters` + `llm_person_id`, `llm_name`, `llm_confidence`,
`llm_evidence_json`, `decision_source` · `meetings` + `glossary`, `expected_people_json` · `providers` + `fast_model`,
`strong_model`, `models_json`, `tested_at`, `latency_ms`, `base_url` (nulo) · novas `llm_calls` (meeting_id nulo
permitido, job_id, task, provider, model, prompt_version, schema_hash, input_tokens, output_tokens, cached_tokens,
audio_tokens, cost_usd, latency_ms, status, error redigido, strategy, request_blob, response_blob, created_at),
`chat_messages` (meeting_id, role, text, provider, model, citations_json, cost_usd, created_at), `llm_prices` · `JobKind.LLM`;
`JobStep` + identify, minutes, executive, pre_summary, title, revise, test · índices `summaries(meeting_id, kind)`,
`summaries(job_id)`, `action_items(meeting_id)`, `llm_calls(meeting_id, id)`, `chat_messages(meeting_id, id)`; FK cascade
em `llm_calls` e `chat_messages`. Testes de rollback no meio e de banco em v5 ganhando a v6.

## Comportamento
1. Warm-up: título ou participantes preenchidos → glossário e mapeamento → o usuário edita → Gravar salva na reunião.
2. Job da F2 conclui → enfileira job `llm` (se há provedor ativo com chave) → worker leve: identify → minutes →
   executive, pre_summary, title → revise → `concluído` ou `erro` parcial; a revisão aberta recarrega pela assinatura.
3. Revisão: Enter ou `L` grava `person_id`, `decision_source` e `revisions`; "gerar de novo" enfileira job de uma tarefa.
4. Chat: `StreamTask`; citações reescritas; tudo em `chat_messages`.
5. Reprocessar: ata, executivo e pré-resumo ficam com a marca "gerados antes do reprocessamento" e botão; `identify` roda
   para vozes novas; `revise` só nos segmentos sem `text_llm` (P7).
6. Sem chave em nenhum provedor: nenhum job `llm` é criado; aviso único; reunião `processada` com transcrição e perfis.

## Aceite (G3), nas três reuniões do G1 processadas pela F2
2 de 3 atas aceitas com edições pequenas (menos de 20 % dos caracteres alterados em `text_edited`) · identidade por
contexto: 70 % ou mais dos clusters pendentes com pista na transcrição têm `decision_source = usuario_llm` · troca de
provedor pela Configurações sem código, demonstrada numa ata com GPT ou Claude · "testar conexão" devolve latência e
modelo em menos de 10 s no app e pelo worker · custo por reunião em `llm_calls` e na tela; custo real conferido contra a
fatura · sem rede, a reunião termina `processada` e "tentar de novo" completa só o que faltou · nenhuma chave aparece em
log, erro ou `config_json` (teste) · suíte sem rede abaixo de 8 s (worker leve testado em processo).

## Riscos
Majors novas dos SDKs (R-1: pinos por major, teste de contrato com dublê, canário no testar conexão) · proxy e TLS da
máquina com EDR (R-2: `truststore`, proxy e CA configuráveis, erros traduzidos, "testar pelo worker") · limites de taxa
(R-3: semáforo, `Retry-After`, hierárquico em "request too large") · alucinação (R-4: evidência conferida, "sem
evidência", temperatura 0,2, ata editável) · custo (R-5: estimativa prévia, alerta, opcionais sob confirmação) · chave em
log (R-6: redação e testes) · falha no meio do `TaskGroup` (R-7: tarefas devolvem resultado; gravação tarefa a tarefa) ·
áudio saindo da máquina (R-8: desligado por padrão; aviso na sub-aba) · corrida app × worker (R-9: eliminada pelo job
único) · PyInstaller (R-10: prompts em módulos `.py`; `hidden imports` depois).

## Testes sem rede
`FakeProvider` com roteiro por tarefa, falhas injetáveis (429 com `Retry-After`, timeout, JSON inválido, "request too
large"), registro de todos os pedidos, streaming em pedaços, capacidades configuráveis; contrato parametrizado contra o
fake e contra cada adaptador com cliente-dublê (`importorskip`); validador e verificador do subconjunto estrito; runner
(encadeamento, CP11 nos pedidos, evidência inválida, falha parcial, `retry` só da faltante, `current_job_id` inalterado,
custo, `config_json` sem chave, hierárquico, semáforo, cancelamento, lease ignorada, pausa manual respeitada); fila por
classe com dois `Store`; revisão em blocos e proteção; reprocessamento; `LLMAudioTranscriber`; interface (sub-aba,
recarga por assinatura, Enter/`L`, abas, alternância, chat com clique, glossário com debounce); redação e importações.
Só com a chave do dono: latências, preços, qualidade das atas, acerto da identidade com e sem áudio, troca de provedor,
proxy e TLS, forma exata das chamadas nas majors, `LLMAudioTranscriber` real; `scripts/llm_smoke.py` (autorização CP13 à
parte) imprime JSON para `decisoes.md`.

## Lotes ([chute] 12 dias de foco; L0 e L1 paralelizáveis)
L0 base da fila (kind `llm`, classes, `current_job_id`, `BaseJobRunner`, `--light`, rótulos, estimativa nula) 1 d · L1
`listener/llm/` sem adaptadores (tipos, validador, registro, redação, fake, custos, migração v6, repositórios) 1,5 d · L2
adaptadores às cegas, rede e certificados, contrato com dublê, célula do CI com `.[dev,llm]`, pinos 2 d · L3 `llm_steps`
(prompts, esquemas, render, evidência, runner com `TaskGroup`, hierárquico, encadeamento, custo, transporte) 2,5 d · L4
Configurações e warm-up 1,5 d · L5 revisão e leitura 1,5 d · L6 chat e `LLMAudioTranscriber` 1,5 d · L7 G3 com o dono 0,5 d.

## Respostas do dono (25/10, D-043)
- P1 Idioma das saídas: **opções nas Configurações** (pt-BR padrão, `auto`, `en`, `es`, outro código livre).
- P2 Alerta de custo por reunião: valor padrão? Padrão revisado: US$ 2,00 (3 h com o modelo forte na ata chega a US$ 1).
- P3 Segunda opinião por áudio (voz de terceiros sai da máquina; ganho incerto): padrão desligado, ligável? Padrão: desligado.
- P4 Transcrição revisada exibida por padrão, com alternância para a original? Padrão: sim.
- P5 Geração automática: título, pré-resumo, identidade e ata para toda reunião processada; executivo e transcrição
  revisada automáticos até o alerta de custo, depois sob demanda? Padrão: sim.
- P6 "Gerar de novo" leva 3 a 5 s para começar (vai pela fila, num worker leve, e funciona com o app fechado)? Padrão: sim.
- P7 Reprocessar mantém a ata antiga com aviso e botão, refazendo só identidade e trechos sem revisão? Padrão: sim.
- P8 Guardar pedido e resposta completos de cada chamada no banco (50 a 100 KB por reunião; depuração sem rede)? Padrão: sim.
- P9 Campo de endereço do serviço no OpenAI (Azure, OpenRouter, servidor local; é serviço fora do CP14)? Padrão: não na F3.

[fato] `summaries`, `action_items`, `providers`, `segments.text_llm` e `meetings.title_llm` existem desde a v1;
`processing_jobs.kind`, `params_json` e `config_json` desde a v4; o Gemini aceita áudio em todos os modelos 2.x; `claim`
hoje recusa quando há qualquer job `rodando` fresco. [exp] janelas atuais comportam 3 h; SDKs em majors novas; proxy e
TLS corporativos quebram o `certifi` padrão. [chute] custos e esforço.

## Implementação (2026-10-25)
Commits L0 a L6 (`0cea46c`, `3450fe9`, `498cb57`, `a87386b`, `f07bb38`, `f4740e4`, `b50b02f`). Novos: `llm/{types,base,schema,
registry,redact,fake,fake_scripts,costs,models_table,adapters_common,gemini,openai,anthropic,jobconfig,evidence,render,
glossary_task,chat_task}.py`, `llm/prompts/*` (10 tarefas, `PROMPT_VERSION = 1.0`), `pipeline/{jobbase,llm_steps,llm_audio}.py`,
`store/{providers,summaries,actions,llm_calls,chat}.py`, `ui/{providers,llm_tabs,chat}.py`, `tests/test_llm_*.py`. Alterados:
`model.py`, `store/{schema,core,db,jobs,segments,meetings,settings}.py`, `pipeline/{runner,worker,models,profiles,review,
estimate}.py`, `ui/{controller,review,transcript,queue,settings_min,warmup,tasks}.py`, `log.py`, `config/defaults.py`,
`platform/common/process.py`, `pyproject.toml` (pinos por major), `ci.yml` (célula `llm`). Itens 1 a 16 feitos, com estes
desvios: (a) segunda opinião por áudio (item 5): a configuração existe (desligada, P3) e o adaptador aceita áudio, mas a
chamada de `identify` com trechos FLAC não foi escrita; medir no G3 só por texto; (b) `revise` devolve
`{"revisions": [{segment_id, text}]}` em vez de um mapa (o subconjunto estrito não aceita chaves dinâmicas); (c) U-019 só no
chat: "gerar de novo" vai pela fila, sem streaming (P6); (d) "testar pelo worker" usa a reunião mais recente (a fila é por
reunião); (e) `title` lê o pré-resumo quando já existe, senão o contexto da ata, senão as primeiras falas (o grupo roda em
paralelo); (f) `anthropic` 2.x não aceita `temperature` em `messages.create` [fato, SDK 2.3.0]: o adaptador não envia;
(g) `providers.model` guarda o modelo de embeddings; (h) o worker pesado lança o leve ao enfileirar (o app pode estar
fechado, P2); (i) o pesado nunca sobe durante a gravação, também em `launch_worker()`; (j) as tarefas puladas pelo alerta
ficam em `timings_json.llm.skipped` e a revisão oferece "gerar?"; (k) os testes com os SDKs reais (transporte HTTP simulado)
rodam só com `LISTENER_SDK_TESTS=1` (célula `llm` do CI), para a suíte ficar abaixo de 8 s; (l) `scripts/llm_smoke.py` não
foi escrito (autorização CP13 à parte). Medido no WSL: 193 testes mais 3 pulados em 6,9 a 7,7 s (148 em 5,8 s antes); ruff
e mypy limpos (Linux e `--platform win32`). Fatos novos: filtros de `logging` num logger não valem para registros
propagados pelos filhos (a redação da chave fica no `QueueHandler`); o pytest 9 pendura `LogCaptureHandler` em todo logger
sem propagação (a fixture `restore_logging` os deixa fora da foto); openai 3.x e anthropic 2.x usam `httpx2`; o anthropic
2.x autentica por `Authorization: Bearer`; o google-genai 2.33 manda `thinking_budget` em snake_case dentro de
`thinkingConfig` (a API aceita as duas grafias). Só a chave do dono no Windows verifica: latências e preços reais contra a
fatura, qualidade das atas (2 de 3), acerto da identidade por contexto, troca de provedor numa ata, proxy e TLS da máquina
com EDR (e o worker leve `CREATE_NO_WINDOW` saindo para a rede), forma exata das chamadas nas majors (canário do "testar
conexão": esquemas estritos na OpenAI, `input_schema` na Anthropic, `response_json_schema` no Gemini), `list_models()`
reais, tempo de importação dos SDKs, `LLMAudioTranscriber` numa janela real (FLAC até 20 MB), limites de taxa do tier, G3.
