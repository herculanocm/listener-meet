# F3 — LLM agnóstica: identidade por contexto, título, ata, resumos, transcrição revisada, chat e revisão completa

Status: rascunho (passo 1 do fluxo D-040; segue para revisão de arquitetura e depois para o dono) · Aprovada por Herculano
em: — · Fase e gate: F3 · G3 · CPs: 1, 2, 3, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17
Quando ler: ao trabalhar na camada de LLM, na revisão completa ou no chat. Tabelas: `dados.md`. O que já existe e esta spec
usa: fila e worker da F2 (`processing_jobs.kind`, `config_json`, artefatos por janela, `JobRunner`), `segments` com
`text_llm`, `clusters` com sugestão e `Attribution.LLM`, `summaries` e `action_items` (vazias desde a v1), `providers`
(chaves no banco, CP10), revisão mínima e leitura da F2, `Task` para trabalho fora da thread do Qt, `SettingSpec`.
Tamanho-alvo: 16 KB.

## Objetivo
A LLM vira o cérebro de contexto da reunião: nomeia por pistas as vozes que o pyannote deixou abaixo do limiar, propõe
título e pré-resumo, escreve a ata (decisões; ações com dono e prazo; destaques dos marcadores com as notas do usuário),
o resumo executivo e a transcrição revisada com nomes e cargos, e responde perguntas sobre a reunião. Trocar de provedor
é só configuração (CP2); a LLM nunca substitui uma identificação de alta confiança do pyannote (CP11).

## Escopo
1. **Camada de provedores** (`listener/llm/`): `base.py` com `class LLMProvider(Protocol)` assíncrono: `complete(request)
   -> Completion`, `embed(texts, *, dim) -> Embeddings`, `list_models() -> tuple[ModelInfo, ...]`, `test_connection() ->
   ConnectionTest` (latência, modelo, erro legível); `capabilities: frozenset[Capability]` (`TEXT`, `JSON`, `AUDIO_IN`,
   `EMBEDDINGS`, `STREAMING`); `Request` e `Completion` como dataclasses `frozen, slots` (mensagens, esquema JSON opcional,
   anexos de áudio opcionais, temperatura, limite de saída; resposta com texto ou objeto validado, tokens de entrada e
   saída, modelo real, latência). Adaptadores: `gemini.py` (google-genai; inicial), `openai.py`, `anthropic.py`, cada um
   só traduzindo o contrato para o SDK; saída estruturada pelo mecanismo nativo de cada um (esquema de resposta,
   `json_schema`, uso de ferramenta) com validação do projeto por cima; `fake.py` (roteiro) para testes. `registry.py`
   cria o provedor a partir de `providers` e aplica `tenacity`-free: nova tentativa própria com backoff para erros 429,
   5xx e rede, `asyncio.timeout` por chamada (padrão 120 s; 600 s com áudio). Prompts e esquemas são do projeto, em
   `listener/llm/prompts/` (um módulo por tarefa, com `PROMPT_VERSION`), nunca do provedor. Os SDKs são leves e podem ser
   importados pelo app; torch continua proibido fora do worker.
2. **Onde roda.** As tarefas pós-reunião rodam no worker da F2 como etapas do mesmo job depois de `segments`:
   `identify` → (`title`, `pre_summary`, `minutes`, `executive` em paralelo com `asyncio.TaskGroup`) → `revise_transcript`,
   dentro de `asyncio.run()` na etapa; falha de rede não derruba o job: a etapa fica `erro` com motivo, o restante da
   reunião continua usável e "tentar de novo" refaz só as etapas de LLM (artefato por etapa). O que precisa de resposta
   na hora roda no app, numa `Task` com `asyncio.run()`: glossário no warm-up, "gerar de novo", chat e "testar conexão".
   Nunca asyncio na thread principal do Qt.
3. **Glossário no warm-up (handoff §4).** Ao preencher título ou participantes, o app pede à LLM uma lista de termos,
   siglas e nomes prováveis (entrada: título, participantes, glossário fixo das Configurações, cargos e empresas das
   pessoas cadastradas; saída JSON de até 40 termos); o usuário vê e edita os termos antes de gravar (U-018); o resultado
   vai para `meetings.glossary` e entra no `initial_prompt` do Whisper (F2 item 5) junto com o glossário fixo. Sem rede ou
   sem chave: segue sem glossário, com aviso discreto. A LLM também mapeia participantes esperados para pessoas
   cadastradas e grava `meetings.expected_people_json` (ids), que o `ProfileMatcher` usa como candidatos primeiro e como
   desempate (D-041 P4).
4. **Identidade por contexto (CP11).** Para cada cluster `sem_nome` ou `ambíguo` com fala suficiente, a LLM recebe a
   transcrição com rótulos (`SPK_n` para os pendentes; nomes e cargos para os resolvidos), a lista de participantes
   esperados com cargos, o título e os marcadores, e devolve JSON por cluster: pessoa (id de cadastrada ou nome novo),
   confiança (0 a 1), justificativa com a citação literal ("valeu, Rodrigo"), ou nulo sem pista. Segunda opinião por áudio:
   quando o provedor tem `AUDIO_IN` e `llm.audio_second_opinion` está ligado, até 3 trechos de 10 s por cluster (os turnos
   mais longos e limpos) vão junto, para a LLM comparar com trechos de referência de até 3 candidatos. Resultado em
   `clusters.llm_person_id`, `llm_confidence`, `llm_reason`; o status não muda e `person_id` não é tocado: a revisão
   mostra "a LLM sugere Rodrigo (0,8): 'valeu, Rodrigo' aos 12:03" e o usuário confirma, troca ou ignora. Clusters `auto`
   e `confirmado` nunca entram no pedido. Nome novo vira pessoa só na confirmação.
5. **Título, pré-resumo, ata, resumo executivo.** Um pedido por tarefa, em paralelo, com a transcrição revisada ou, se
   ainda não houver, a original, sempre com nome e cargo por fala e com os marcadores e notas do usuário como âncoras
   ("destaques"). Esquemas JSON do projeto: `title` (até 80 caracteres); `pre_summary` (até 600 caracteres); `minutes`
   (contexto, decisões com minuto, ações com dono, prazo e minuto, destaques dos marcadores, pendências, próximos passos);
   `executive` (até 1500 caracteres, para enviar a quem não esteve). Ações vão para `action_items` (dono ligado a
   `people` quando casar pelo nome, senão `owner_text`; `t_ms` da fala de origem; `job_id`). Tudo em `summaries` com
   `kind`, `text_llm`, `text_edited`, `provider`, `model`, `prompt_version`, `job_id`, `status`; a versão da LLM nunca é
   sobrescrita por "gerar de novo": entra uma linha nova e a anterior fica (`revisions`). Reuniões longas: a transcrição é
   enviada inteira enquanto couber na janela do modelo (medida por `count_tokens` ou estimativa de 4 caracteres por
   token); acima disso, resumo hierárquico (blocos de 30 min → síntese), registrado em `llm_calls.strategy`.
6. **Transcrição revisada.** Blocos de até 60 falas: a LLM corrige pontuação, grafia de nomes e termos do glossário sem
   mudar o sentido, devolvendo JSON `{segment_id: texto}`; vai para `segments.text_llm`; o original fica em `text`. A
   leitura mostra a revisada quando existe, com alternância para a original (P5). Falas do canal L recebem o mesmo
   tratamento. Trechos alterados em mais de 40 % dos caracteres são descartados e logados (proteção contra reescrita).
7. **Transcrição final pela LLM com áudio (D-020, F2 item 18).** `LLMAudioTranscriber` implementa `Transcriber` da F2:
   recebe a `AudioWindow` (até 10 min), envia o áudio ao provedor com `AUDIO_IN` pedindo JSON de falas com tempos
   relativos e devolve `Word`s aproximadas (uma por fala, com início e fim) para o `Segmenter`; opção `pipeline.
   transcriber = llm` habilitada nas Configurações só quando o provedor ativo tem `AUDIO_IN`; o custo estimado aparece
   antes de ligar. Precisão de palavra menor que a do Whisper: documentado na própria tela.
8. **Chat sobre uma reunião (sem RAG).** Painel na leitura: pergunta → resposta com a transcrição completa (ou o resumo
   hierárquico) no contexto, citando `[mm:ss]` clicáveis que tocam o áudio; respostas em streaming quando o provedor
   tem `STREAMING` (U-019); histórico em `chat_messages` (CP9). Chat sobre todas as reuniões é F5.
9. **Revisão completa (handoff §5).** A tela da F2 ganha: título sugerido editável com "gerar de novo"; pré-resumo editável
   com "gerar de novo"; cards de falantes com a sugestão e a justificativa da LLM e o trecho de áudio; a ata, o resumo
   executivo e as ações aparecem em abas quando prontos, editáveis (`text_edited`, `revisions`). Nada de cargo ou ficha.
10. **Configurações (CP8).** Sub-aba por provedor (Gemini, GPT, Claude): chave (texto aberto, CP10), modelo rápido e
    modelo forte escolhidos de `list_models()` (com cache em `providers.models_json`), parâmetros (temperatura, limite de
    saída), "testar conexão" com latência e modelo respondido (U-009); provedor ativo para texto e para embeddings;
    `llm.output_language` (`pt-BR` padrão; `auto` segue o idioma da reunião); `llm.audio_second_opinion`;
    `llm.cost_alert_usd` por reunião; `llm.glossary_enabled`; tabela de preços por modelo editável (`llm.prices_json`) com
    sementes do projeto. Tarefas por classe: título, glossário, pré-resumo e revisão de transcrição usam o modelo rápido;
    ata, executivo e identidade usam o forte.
11. **Custo e latência (U-009).** Toda chamada em `llm_calls` (reunião, job, tarefa, provedor, modelo, tokens de entrada e
    saída, custo estimado, latência, status, erro). Resumo de fim de processamento e `QueueDialog` mostram o custo da
    reunião; Configurações mostram o do mês; acima de `llm.cost_alert_usd` a reunião ganha aviso e as tarefas
    opcionais (executivo, transcrição revisada) pedem confirmação antes de rodar nas próximas.
12. **Embeddings (CP2, D-009, D-021).** A interface `embed` existe e é testada com o provedor ativo (`gemini-embedding-001`
    em 768 por padrão), mas a indexação é F5; aqui só "testar conexão" exercita.
13. **Privacidade e falhas.** O que sai da máquina: texto da transcrição e, se ligado, trechos de áudio; nada de chave em
    log; `llm_calls.error` guarda a mensagem do provedor sem a chave. Sem rede: as etapas de LLM ficam `erro` com
    "tentar de novo" e o app avisa uma vez por job; o resto da reunião (transcrição, perfis, leitura) não depende de rede.
14. **Estrutura.** `listener/llm/{base,types,registry,gemini,openai,anthropic,fake,costs,prompts/}.py`;
    `listener/pipeline/llm_steps.py` (etapas do job); `ui/{review,transcript,settings_min}.py` ampliadas e `ui/chat.py`.
    Regra dura mantida: nada do app importa torch; os SDKs de LLM podem ser importados pelo app, preguiçosamente.

## Fora de escopo
Resumo parcial ao vivo (F4); RAG, chat sobre todas as reuniões, exportação (F5); telas completas e separar perfis (F6);
embeddings locais (D-021, F5).

## Cláusulas
CP1 sem avisos regulatórios · CP2 contrato único, adaptadores trocáveis, prompts e esquemas do projeto, embeddings
trocáveis · CP3 "gerar de novo" acrescenta linha, nunca apaga; `text` original intacto · CP8 item 10 editável em uso ·
CP9 `summaries`, `action_items`, `llm_calls`, `chat_messages`, `meetings.glossary` no SQLite · CP10 chaves em `providers`,
visíveis na tela · CP11 a LLM só escreve `llm_*` em clusters pendentes; `auto` e `confirmado` nunca entram no pedido ·
CP12 U-018 e U-019 · CP14 só a API paga do Gemini por padrão; GPT e Claude são escolha do usuário · CP15 nada de SO ·
CP16 commit por lote · CP17 `TaskGroup`, `asyncio.timeout`, dataclasses `slots`, `StrEnum` (`Capability`, `LlmTask`,
`SummaryKind`, `JobStep` novos).

## Interface e experiência (CP7, CP12)
- Warm-up: linha "Termos prováveis" preenchida pela LLM em até 5 s, editável; "sem chave" vira link para as Configurações.
- Revisão: card de voz pendente com "LLM sugere Rodrigo (0,8) · 'valeu, Rodrigo' aos 12:03 · ▶"; Enter aceita a sugestão.
- Leitura: alternância "revisada | original"; painel de chat à direita com `[mm:ss]` clicáveis; "gerar de novo" por aba.
- Fila: etapa "LLM: ata (2 de 4)" e custo acumulado da reunião.
- **U-018** glossário proposto editável no warm-up antes de gravar. incluir? sim/não
- **U-019** streaming da resposta do chat e do "gerar de novo". incluir? sim/não
- **U-020** selo de custo por reunião no resumo de fim de processamento e no histórico. incluir? sim/não

## Dados (migração v6 do main)
`summaries` + `prompt_version`, `job_id`, `status`, `task_json` · `action_items` + `job_id`, `segment_id`, `created_at` ·
`clusters` + `llm_person_id`, `llm_name`, `llm_confidence`, `llm_reason` · `meetings` + `glossary`, `expected_people_json`
· `providers` + `models_json`, `fast_model`, `strong_model`, `params_json`, `tested_at`, `latency_ms` · novas `llm_calls`
(id, meeting_id, job_id, task, provider, model, input_tokens, output_tokens, cost_usd, latency_ms, status, error,
strategy, created_at) e `chat_messages` (id, meeting_id, role, text, provider, model, citations_json, cost_usd,
created_at) · `settings` `llm.*` · `JobStep` + `identify`, `title`, `pre_summary`, `minutes`, `executive`,
`revise_transcript`. Índices: `summaries(meeting_id, kind)`, `llm_calls(meeting_id)`, `chat_messages(meeting_id, id)`.

## Comportamento
1. Warm-up: ao sair do campo título ou participantes, `Task` pede o glossário; resposta preenche a linha; o usuário edita;
   Gravar salva `meetings.glossary` e `expected_people_json`.
2. Job da F2 chega a `segments` → `identify` (só pendentes) → `TaskGroup` com título, pré-resumo, ata e executivo →
   `revise_transcript` em blocos → `processada`. Cada etapa grava artefato e `llm_calls`; erro de rede marca a etapa e
   segue para a próxima que não depender dela; ao final, `erro` parcial com "tentar de novo (só LLM)".
3. Revisão: confirmar a sugestão da LLM grava `attribution = llm` no cluster e, na confirmação, `usuario`; os segmentos
   herdam como na F2. "Gerar de novo" cria linha nova em `summaries` e marca a anterior como superada.
4. Chat: cada pergunta é uma `Task`; a resposta chega em partes; as citações `[mm:ss]` viram links; tudo em `chat_messages`.
5. Troca de provedor: Configurações → `is_active_llm`; o próximo job e o próximo chat usam o novo; jobs em curso terminam
   com o antigo (`config_json`).
6. Sem chave em nenhum provedor: as etapas de LLM são puladas com aviso único; a reunião fica `processada` sem ata.

## Aceite (G3), nas três reuniões do G1 processadas pela F2
2 de 3 atas aceitas com edições pequenas (menos de 20 % dos caracteres alterados em `text_edited`) · identidade por
contexto acerta 70 % ou mais dos clusters pendentes que têm pista na transcrição (medido na revisão: sugestão da LLM
confirmada sem troca) · troca de provedor pela Configurações sem código, demonstrada com GPT ou Claude numa ata ·
"testar conexão" mostra latência e modelo em menos de 10 s · custo por reunião registrado em `llm_calls` e exibido ·
sem rede, a reunião termina `processada` com transcrição e perfis, e "tentar de novo (só LLM)" completa depois · suíte
sem rede (provedor falso) abaixo de 6 s.

## Riscos
Modelos e preços mudam (R-1: `list_models()` e `prices_json` editável; nomes de modelo nunca fixos no código) · janela de
contexto (R-2: resumo hierárquico; `count_tokens` quando o SDK oferecer) · alucinação na ata (R-3: toda decisão e ação
com minuto de origem; pedir citação literal; validar que o minuto existe) · custo (R-4: alerta e confirmação) · áudio
saindo da máquina (R-5: desligado por padrão para a segunda opinião? ver P3; nunca para título e ata) · SDKs pesados no
app (R-6: importação preguiçosa, medir tempo de abertura) · esquemas de saída diferentes por provedor (R-7: validação do
projeto e teste de contrato com respostas gravadas) · identidade por contexto errada (R-8: nunca aplica sozinha; sempre
revisão; CP11) · reunião em inglês (R-9: prompts bilíngues; `output_language`).

## Testes sem rede
`FakeProvider` com roteiro por tarefa e falhas injetáveis (429, timeout, JSON inválido); testes de cada prompt com
validação de esquema; etapas de LLM no `JobRunner` com o fake (ordem, artefatos, erro parcial, tentar de novo só LLM);
identidade por contexto respeitando CP11 (cluster `auto` nunca enviado); resumo hierárquico acionado por tamanho;
`llm_calls` e custo; troca de provedor ativo; chat com citações e streaming simulado; "gerar de novo" preservando a
anterior; adaptadores reais só com teste de contrato sobre respostas gravadas (sem chamadas de rede no CI). Só com chave
do dono: G3, latências, preços reais, qualidade das atas.

## Lotes ([chute] 7 a 9 dias de foco)
L1 contrato, tipos, registro, fake, custos e migração v6 (1 d) · L2 adaptadores Gemini, OpenAI, Anthropic com teste de
contrato (1,5 d) · L3 etapas no worker: identidade, título, pré-resumo, ata, executivo, transcrição revisada, erro parcial
(2 d) · L4 glossário no warm-up e Configurações por provedor com "testar conexão" e custo (1 d) · L5 revisão completa e
leitura com alternância (1 d) · L6 chat com streaming e `LLMAudioTranscriber` (1 d) · L7 G3 com o dono (0,5 d).

## Perguntas ao dono (produto)
- P1 Idioma das saídas: sempre pt-BR, ou seguir o idioma da reunião? Padrão: pt-BR, com `auto` disponível.
- P2 Alerta de custo por reunião: valor padrão? Padrão: US$ 1,00, editável.
- P3 Segunda opinião por áudio ligada por padrão (trechos de voz saem da máquina para o provedor)? Padrão: ligada, só para
  clusters pendentes, até 3 trechos de 10 s por cluster.
- P4 Transcrição revisada pela LLM exibida por padrão, com alternância para a original? Padrão: sim.
- P5 Chat nesta fase só sobre a reunião aberta (todas as reuniões com RAG na F5)? Padrão: sim.
- P6 Ata inclui "pendências e próximos passos" além de decisões, ações e destaques? Padrão: sim.

[fato] `summaries`, `action_items`, `providers` e `segments.text_llm` existem desde a v1; `processing_jobs.kind` e
`config_json` desde a v4; o Gemini aceita áudio nativo (D-020). [exp] janelas de contexto atuais comportam 2 h de
transcrição; saída estruturada nativa existe nos três SDKs. [chute] esforço dos lotes; 70 % de acerto por contexto.
