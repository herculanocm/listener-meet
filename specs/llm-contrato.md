# Contrato da camada de LLM (CP2) — dono único do desenho técnico da F3

Quando ler: ao implementar ou alterar `listener/llm/` ou `pipeline/llm_steps.py` (T2). A spec `features/F3-llm-agnostica.md`
diz o que o produto faz; este arquivo diz como a camada é construída. Tamanho-alvo: 9 KB. Origem: revisão de arquitetura
de 25/10/2026 (F3A-30 a F3A-49). Aviso: os SDKs instalados no Windows (google-genai 2.33, openai 3.37, anthropic 2.3) são
majors mais novas do que a documentação conhecida; a forma exata das chamadas é [exp] e se confirma pelo teste de
contrato e pelo canário do "testar conexão".

## Tipos (`llm/types.py`, tudo `frozen, slots`, `StrEnum` para enumerações)
`Role` (system, user, assistant) · `AudioPart(data: bytes, mime: str, label: str)` · `Message(role, text, audio=())` ·
`Request(task: LlmTask, model: str, messages, json_schema: Mapping | None, temperature=0.2, max_output_tokens=8192,
reasoning: Reasoning = NONE, timeout_s=120.0, cache_prefix=False)` · `Usage(input_tokens, output_tokens, cached_tokens=0,
audio_tokens=0)` · `Completion(text, data: Mapping | None, usage, model, latency_ms, finish, raw_id)` · `ModelInfo(id,
display_name, input_limit, output_limit, capabilities, price_ref)` · `ConnectionTest(ok, latency_ms, model, error)` ·
`Capability`: TEXT, JSON, AUDIO_IN, EMBEDDINGS, STREAMING, COUNT_TOKENS, CACHE · `LlmTask`: glossary, identify, minutes,
executive, pre_summary, title, revise, transcribe, chat, test, partial (F4), rag_embed e rag_chat (F5) · `Reasoning`:
none, low, high · `ProviderKind`: gemini,
openai, anthropic · `SummaryKind`: pre_summary, minutes, executive, partial (F4) · `SummaryStatus`: atual, superada, erro ·
`ActionStatus`: aberta, feita, descartada, superada · `DecisionSource`: auto, usuario, usuario_pyannote, usuario_llm,
usuario_outro.

## Protocolo (`llm/base.py`)
`LLMProvider`: `id`, `kind`, `capabilities`; `async complete(req) -> Completion`; `stream(req) -> AsyncIterator[str]` (só
texto; resultado final em `stream.result`); `async embed(texts, *, model, dim, task: document | query) -> Embeddings`;
`async count_tokens(req) -> int | None` (None = o projeto estima: caracteres ÷ 3,5 em pt-BR); `async list_models()`;
`async test_connection(model) -> ConnectionTest` (um JSON mínimo com esquema: é o canário das majors); `async aclose()`.
Capacidade efetiva = `provider.capabilities ∩ model.capabilities`; a tabela de capacidades por família de modelo mora em
`llm/models_table.py` (dado do projeto) e é complementada por `list_models()` (Gemini informa limites e ações; OpenAI e
Anthropic não). Anthropic não tem embeddings nem áudio nativo [fato até as versões conhecidas]; OpenAI tem áudio só em
modelos específicos [exp]; Gemini tem áudio em todos os 2.x [fato].

## Decoradores e registro (`llm/registry.py`)
`RetryingProvider` sobre qualquer provedor: `asyncio.timeout(req.timeout_s)`; 3 tentativas com backoff exponencial e
jitter (1, 3, 9 s) para 429, 5xx e rede, respeitando `Retry-After`; reenvio único com a mensagem de erro quando o JSON não
valida; "request too large" não é retentado: sinaliza ao runner para o modo hierárquico. Os clientes dos SDKs nascem com
`max_retries = 0` e timeout explícito, para não somar retentativas. O registro cria o provedor a partir de `providers`
(chave lida na hora, nunca copiada para `config_json`), aplica proxy e certificados (`llm.proxy_url`, `llm.ca_bundle`,
`llm.system_certs` com `truststore.inject_into_ssl()` antes de importar qualquer SDK) e carrega o adaptador por nome
(importação preguiçosa; `llm/registry.py`, `types.py`, `schema.py`, `prompts/*` e `ui/*` nunca importam `google`,
`openai`, `anthropic`, `httpx` nem `truststore` no nível do módulo; teste estático garante). Clientes assíncronos vivem
dentro do event loop que os criou: cada `asyncio.run()` cria e fecha o seu.

## Saída estruturada e validação (`llm/schema.py`)
Um só esquema JSON por tarefa, escrito no subconjunto estrito aceito pelos três SDKs: `additionalProperties: false`,
todas as chaves em `required`, nulo como união `["string", "null"]`, sem `format`, `minLength` nem `pattern`. Tradução:
Gemini `response_mime_type = application/json` + esquema de resposta; OpenAI formato `json_schema` estrito; Anthropic uso
de ferramenta com `input_schema` e `tool_choice` forçado (resposta em `content[].input`). Validação sempre do projeto
(~120 linhas): `type`, `required`, `enum`, `maxLength`, `items`, união com nulo, coerção int/float, erro com caminho;
dataclass `frozen, slots` montada do dicionário validado. Sem pydantic nem msgspec no código do projeto nem na suíte
(pydantic vem com os SDKs, mas não entra no WSL nem no CI base). `SCHEMA_VERSION` por tarefa; hash gravado em `llm_calls`.

## Prompts (`llm/prompts/<task>.py`)
`PROMPT_VERSION = "1.0"`, `SCHEMA`, `build(ctx) -> tuple[Message, ...]`. Instruções em inglês com `output_language`
injetado e exemplos em pt-BR; glossário, nomes próprios e siglas nunca traduzidos; `summaries.prompt_version` e
`llm_calls.prompt_version` gravam `"minutes/1.0"`; mudar o texto do prompt sobe a versão (regra de revisão de diff).
Classe por tarefa (`TASK_CLASS`): rápido = glossary, title, pre_summary, revise, transcribe, chat; forte = identify,
minutes, executive. `Reasoning.NONE` no rápido (Gemini Flash com orçamento de pensamento zero), `LOW` no forte.

## Esquemas (resumo; o dicionário completo fica no módulo da tarefa)
- `identify`: por cluster `{cluster, person_id | null, new_name | null, confidence 0..1, evidence: [{segment_id,
  quote}]}`. Entrada: linhas `#<segment_id> [mm:ss] <rótulo>: <texto>` (nome e cargo para resolvidos e canal L; `SPK_n`
  para pendentes), cabeçalho com título, participantes esperados com cargo e empresa, marcadores com nota, candidatos
  cadastrados (id, nome). Inteira até 60 % da janela do modelo; acima, só os segmentos dos pendentes com ±3 vizinhos.
- `minutes`: `{context, decisions: [{text, segment_ids}], actions: [{text, owner_text, owner_person_id | null, due_text,
  due_date AAAA-MM-DD | null, segment_ids}], highlights: [{marker_id, text, segment_ids}], open_points, next_steps}`.
  A LLM recebe a data da reunião e devolve datas absolutas; o projeto só valida o formato.
- `executive` (texto até 1500 caracteres) e `pre_summary` (até 600) recebem a ata validada, não a transcrição; `title`
  (até 80) recebe o pré-resumo. Ordem: minutes → executive, pre_summary, title em paralelo.
- `revise`: `{"revisions": [{"segment_id", "text"}]}` restrito aos ids do bloco (60 segmentos, 5 de contexto antes e depois);
  o mapa `{"<segment_id>": texto}` não cabe no subconjunto estrito (chaves dinâmicas) [fato, implementação].
- `glossary`: `{terms: [str] (≤ 40), people: [{name, person_id | null}]}`.
- `transcribe` (LLMAudioTranscriber): `{language, utterances: [{start_s, end_s, text}]}` relativo ao início da janela.
- `chat`: texto livre com `[mm:ss]` da transcrição enviada; o projeto reescreve cada `[mm:ss]` para o segmento mais
  próximo (≤ 5 s) ou remove o link, nunca a citação.
- `rag_chat` (F5, chat global, rápido): trechos de até `rag.top_k` por reunião com apelido (`[R1] título — data —
  participantes`, linhas `#segment_id [mm:ss] Nome — Cargo: texto` dos segmentos vigentes); a resposta cita `[R1 12:03]`
  e o projeto liga à reunião e à linha enviada mais próxima (≤ 5 s); o histórico volta ao prompt sem apelidos.
- Embeddings (F5): `embed` passa pela retentativa e pelo timeout do `RetryingProvider`; o índice normaliza sempre (a
  OpenAI não normaliza); documentos com `task=document` em lotes de 64, consulta com `task=query`; `rag_embed` em
  `llm_calls` por lote.

## Evidência verificável
Toda decisão, ação, destaque e sugestão de identidade carrega `segment_ids` ou `{segment_id, quote}`; o projeto confere
que o segmento existe na reunião e que a citação está contida no texto dele (casefold, sem pontuação); evidência inválida
é descartada e a confiança multiplicada por 0,5; item sem evidência válida é mantido com a marca "sem evidência". O minuto
exibido vem sempre de `segments.t_start_ms`, nunca do texto da LLM. Render Markdown da ata é do projeto (`llm/render.py`),
determinístico a partir do JSON; `text_llm` é esse Markdown e `text_edited` a edição humana.

## Rede, segurança e concorrência
`llm.max_parallel` (padrão 2) como `asyncio.Semaphore` por processo; nunca `await` dentro de `store.transaction()`
(reentrância por thread: outra corrotina escreveria dentro da transação alheia); resultados gravados só depois de o
`Completion` chegar, tarefa a tarefa. `llm/redact.py` substitui a chave inteira e seus 8 últimos caracteres em toda
mensagem gravada (`llm_calls.error`, `jobs.error`, logs do pacote); `config_json` do job nunca contém chave (teste).
Áudio sai da máquina só com `llm.audio_second_opinion` ligado ou `pipeline.transcriber = llm`; o texto sai sempre que há
provedor ativo, e a sub-aba diz isso.

## Custo (`llm/costs.py`)
Tabela `llm_prices(provider, model, input_usd_m, output_usd_m, cached_usd_m, audio_usd_m, updated_at)` com sementes do
projeto por família, editável na sub-aba; custo = `input × p_in + cached × p_cached + output × p_out + audio × p_audio`;
sem linha de preço, custo nulo e "sem preço cadastrado" na tela. Estimativa prévia por tarefa a partir de
`count_tokens` ou caracteres ÷ 3,5. Projeção [chute]: reunião de 2 h ≈ US$ 0,30 a 0,50 (rápido nas leves, forte na
ata); 3 h ≈ US$ 0,50 a 0,80. Pedido e resposta completos comprimidos (`zlib`) em `llm_calls` quando `llm.store_payloads`
estiver ligado (padrão sim, P-N4).

## O que a implementação fixou além do texto acima (2026-10-25, medido com os SDKs instalados)
`stream()` devolve `LlmStream` (iterador assíncrono com `.result`); `Embeddings.vectors` é `np.ndarray` float32 normalizado;
`AudioPart.seconds` estima a duração pelo tamanho e tipo. Os adaptadores recebem o esquema inteiro, menos as chaves que o
modo estrito da OpenAI pode recusar (`maxLength`, `minimum`, `maximum`, `minItems`, `maxItems`: `schema.provider_schema`); a
validação do projeto usa o esquema inteiro. anthropic 2.x: `messages.create` não aceita `temperature`, `top_p` nem `top_k`
(não são enviados); pensamento estendido só sem ferramenta forçada; `Authorization: Bearer`; openai 3.x e anthropic 2.x
usam `httpx2` (o `http_client` próprio só entra com proxy ou PEM configurados). google-genai 2.33: timeout em ms,
`retry_options.attempts = 1`, `response_json_schema`, `thinking_budget = 0` no rápido (não no Pro, que não desliga),
`client.aio.aclose()`. OpenAI: `input_audio` só wav e mp3 (o `LLMAudioTranscriber` manda WAV em fatias de 5 min fora do
Gemini); `reasoning.effort` e sem `temperature` nas famílias que raciocinam. `title` lê o pré-resumo quando já existe, senão
o `context` da ata, senão as primeiras falas. Erros traduzidos pela forma (`status_code`/`code`, nome da classe,
`Retry-After`, texto de "too large"), sem importar SDK. `RetryingProvider` reenvia a resposta inválida como turno
`assistant` mais a mensagem de erro em `user`.

## Pinos de versão (extra `llm`)
`google-genai>=2.33,<3`, `openai>=3.37,<4`, `anthropic>=2.3,<3`, `truststore>=0.9`; uma célula do CI (Ubuntu, 3.12)
instala `.[dev,llm]` para os testes de contrato com cliente-dublê; as demais pulam por `importorskip`.
