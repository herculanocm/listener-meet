# F5 — Base consultável: busca, histórico e chat sobre todas as reuniões

Status: em revisão (revisão de arquitetura de 25/10 incorporada; esperando as respostas do dono) · Aprovada por
Herculano em: — · Fase e gate: F5 · G5 (mais a F5.1 depois do G5, Q10) · CPs: 2, 3, 7, 8, 9, 10, 12, 13, 14, 15, 17
Quando ler: ao detalhar ou implementar esta fase (T2). Origem: handoff §3 (Base) e §5 (Histórico), D-009, D-012, D-021,
D-024. Tabelas: `dados.md`. Tamanho-alvo: 20 KB (24 KB com a Implementação).

## Objetivo
O usuário acha qualquer coisa dita em qualquer reunião, por palavra ou por sentido, abre a reunião no ponto exato e
ouve, e pergunta à LLM sobre todas as reuniões, com respostas que citam reunião, quem falou e [mm:ss] clicáveis.

## Escopo
Marcado "F5.1" = vai para a entrega seguinte se a Q10 for sim.
1. **Índice** (`rag_chunks`, determinístico pelo `text_hash`), por tipo:
   - `transcricao`: 60 a 120 s cortados em fim de turno, corpo até 1.400 caracteres e cabeçalho (título e data) até 200,
     sem sobreposição; fala longa cortada em fim de frase (tempo do `words_json` ou proporcional); falas e rótulos de
     `review.transcript_rows` ("Nome — Cargo" do dia, D-016; transcrição revisada quando existe: o que a leitura
     mostra);
   - `decisao`, `destaque`, `pendencia`, `proximo_passo`: uma linha de lista da ata exibida (`text_edited`, senão
     `text_llm`), no tempo do `[mm:ss]` que o render põe no fim (sem carimbo = sem tempo; seção desconhecida = `ata`);
   - `acao`: de `action_items` vigentes, com dono e prazo; `executivo`: o resumo executivo, sem tempo;
   - `reuniao`: título, data, participantes e duração (busca por título e marca de reunião indexada).
2. **Busca por palavra**: FTS5 (`rag_fts`) sem acento ("orcamento" acha "orçamento"), título, falantes e texto pesados
   no `bm25`; termos entre aspas com E implícito e `*` no último; com menos de 10 resultados, OU; trecho destacado por
   `snippet()`. O usuário nunca escreve sintaxe do FTS5.
3. **Busca por sentido**: um vetor por trecho e modelo (modelo e dimensão por vetor, D-009); só os do modelo ativo
   contam.
4. **Busca híbrida**: BM25 e cosseno fundidos por RRF (k = 60); filtros (pessoa, período, reunião) aplicados antes do
   top-k; sem vetores do modelo ativo (sem chave, sem rede, índice incompleto), só BM25, com o selo "busca por
   palavras".
5. **Indexação em dois tempos**: o worker pesado, ao concluir, grava trechos e FTS na hora (a busca por palavra acha a
   reunião antes da ata); o job `index` (classe leve, um pendente por reunião, depois dos jobs `llm`) refaz os trechos e
   pede só os vetores que faltam, uma vez, sobre a transcrição já revisada; entra no fim do job `llm`, na revisão de
   falantes e nas edições de título, ata e ações. Na abertura, varredura: pipeline concluído sem trechos e, com embedder
   pronto, trechos sem vetor do modelo ativo (pega as reuniões anteriores à F5).
6. **Provedor de embeddings (CP2)**: o "ativo para embeddings" das Configurações: Gemini `gemini-embedding-001` em 768
   (D-009, padrão), OpenAI `text-embedding-3-small` em 768 ou local (item 7); trocar oferece "reindexar tudo" com
   estimativa (trechos, US$, minutos) e progresso; a busca por palavra segue durante a troca.
7. **Embeddings locais (D-021; F5.1)**: provedor `local` com ONNX Runtime e tokenizers (extra novo `local`, sem torch),
   `intfloat/multilingual-e5-small` int8 (118 MB [fato, API do Hugging Face, 25/10]; Q5) baixado pelo preparo do ao
   vivo; prefixos `query:` e `passage:`; corta em 512 tokens e conta os cortes.
8. **Histórico** (janela própria, aberta por "Histórico…" na bandeja e pelo atalho U-028; vira página da janela
   principal na F6), com abas "Reuniões" (lista com data, título, duração, participantes, status e custo; busca; filtros
   por pessoa e período; com busca, os trechos achados, U-024), "Ações abertas" (U-026) e "Perguntar às reuniões" (item
   11).
9. **Reunião aberta** (uma janela por reunião; substitui a janela "Transcrição", Q6): cabeçalho com título editável,
   data, duração, participantes e custo; abas Ata, Resumo (executivo e pré-resumo), Ações, Transcrição (revisada ou
   original), Marcadores (com nota), Linha do tempo (U-008, F5.1) e Chat (o da F3, sobre a reunião inteira); "Revisar
   falantes…" e "Exportar…"; abre num ponto: rola até a fala e toca.
10. **Player** (um só no app): clicar numa fala, num marcador, num trecho da busca ou numa citação toca daquele ponto
    com L e R misturados até parar; tocar/pausar, ±10 s e posição; pausas da gravação puladas (D-011); os trechos de 5 s
    da revisão também passam por ele (nunca dois sons ao mesmo tempo).
11. **Chat sobre todas as reuniões**: a pergunta (com a anterior do usuário, para seguimento) vai à busca híbrida com os
    filtros da tela; até `rag.top_k` (12) trechos, no máximo `rag.max_per_meeting` (3) por reunião, cada reunião com
    apelido (`R1`, `R2`), no modelo rápido (Q11); a resposta cita `[R1 12:03]` (aceita `[R1]`, várias separadas por `;`
    e h:mm:ss) e o projeto liga à reunião e ao segmento mais próximo, até 5 s, só entre as linhas enviadas, ou deixa o
    texto sem link, nunca remove; conversas guardadas com "nova conversa" (Q3). O chat de uma reunião segue o da F3.
12. **Exportar (U-011, D-012)**, só por ação do usuário: ata e transcrição em .md e na área de transferência (texto e
    HTML); .docx na F5.1, com `python-docx`; escreve num temporário e troca no fim.
13. **Medição do G5** no `--check`: bloco `rag` (desenho técnico).

## Fora de escopo
Apagar reunião pelo Histórico (Q8; F6) · reescrita da pergunta pela LLM (filtros tirados do texto) · reranker · tradução
· busca no áudio sem transcrição · relatório de várias reuniões · janela principal, perfis e configurações completas
(F6) · PDF · GPU (CP14).

## Cláusulas
| CP | Como cumpre |
| --- | --- |
| CP2 | embeddings pelo contrato da F3 (`embed`), trocáveis por configuração, com reindexação; prompts do projeto |
| CP3, CP9 | índice, vetores e conversas no SQLite; índice derivado, refeito; a v8 não apaga tabela com linhas |
| CP7, CP12 | Histórico e reunião aberta; U-024 a U-029 abaixo |
| CP8 | grupo "Busca": índice automático, trechos por pergunta e por reunião, modelo local, reindexar |
| CP10 | chave lida na hora e redigida, como na F3; o provedor local não tem chave |
| CP13, CP14 | código só após a aprovação; CPU; modelo local open source; Gemini pago |
| CP15 | busca vetorial em numpy puro, sem extensão nativa; player pelo sounddevice; nada fora de `platform/<os>/` |
| CP17 | `np.bitwise_count` (numpy 2), `TaskGroup`, tipos PEP 695 no código novo |

## Interface
- **U-024** Trechos da busca agrupados por reunião, com o termo destacado e "abrir neste ponto" (abre a reunião, rola
  até a fala e toca). incluir? sim/não
- **U-025** Destaque contínuo: a fala que está tocando acompanha o áudio na transcrição e na linha do tempo (F5.1).
  incluir? sim/não
- **U-026** "Ações abertas" de todas as reuniões: dono, prazo, reunião de origem; atrasadas em destaque; marcar feita; o
  clique abre a evidência. incluir? sim/não
- **U-027** "Citação certa?" sim/não em cada resposta do chat global (mede o G5 sem planilha). incluir? sim/não
- **U-028** Atalho global para abrir o Histórico com o cursor na busca (`ctrl+alt+shift+h`, configurável). incluir?
  sim/não
- **U-029** "Copiar com citação": fala, trecho ou resposta copiados com reunião, data e [mm:ss] (texto e HTML). incluir?
  sim/não

## Dados (migração v8 do main)
- `rag_chunks` e `rag_vectors` (vazias desde a v1, nunca escritas [fato]) são recriadas; se tiverem linhas, viram
  `*_v1`.
- `rag_chunks`: id, meeting_id (FK cascade), kind (item 1 do Escopo), t_start_ms (nulo = sem tempo), t_end_ms, seg_first
  e seg_last (só dica: a navegação usa o tempo, porque o reprocessamento troca os segmentos), title, speakers,
  person_ids (JSON), text, text_hash, job_id (pipeline vigente), created_at; índice (meeting_id, t_start_ms).
- `rag_fts`: FTS5 (title, speakers, text), conteúdo externo, `unicode61 remove_diacritics 2`, `rank` com pesos; gatilhos
  de inserção, remoção e `UPDATE OF title, speakers, text`; a cascata da reunião dispara o de remoção mesmo com
  `recursive_triggers` desligado [fato, testado em 25/10]. O FTS5 vira requisito: sem ele a v8 falha.
- `rag_vectors`: id, chunk_id (FK cascade), model (`gemini/gemini-embedding-001@768`), dim, vector (float32
  normalizado), sig (sinal de v − μ, 1 bit por dimensão), created_at; único (chunk_id, model); índice (model, id).
- `rag_models`: model, dim, mu (média dos vetores do modelo), mu_n, updated_at.
- `chat_threads`: id, title (primeira pergunta), created_at. `chat_messages` reconstruída (hoje `meeting_id` é NOT NULL
  [fato]): meeting_id nulo = chat global; + thread_id (FK cascade) e rating (U-027: 1, 0 ou nulo); `citations_json` =
  `[{alias, meeting_id, t_ms, segment_id}]`.
- `JobKind.INDEX` (classe leve; nunca em `PIPELINE_JOB_KINDS`, logo fora do job vigente), etapas `index_text` e
  `index_vectors`; `LlmTask` + `rag_embed` e `rag_chat` (rápido); `ProviderKind.LOCAL` e a linha `local` em `providers`.
- `llm_calls`: embeddings do índice por lote, com a reunião e o job do pipeline vigente; consulta e chat global com
  reunião nula.
- Configurações: `rag.auto_index` (sim), `rag.top_k` (12; 4 a 30), `rag.max_per_meeting` (3; 1 a 10), `rag.local_model`
  (F5.1), `hotkeys.history`, `ui.history_geometry`, `ui.meeting_geometry`, `ui.history_filters` (interna).
- Na implementação: `dados.md`, `ARCHITECTURE.md` e o `pyproject.toml` (sai o extra `store`, do sqlite-vec) acompanham;
  D-047 registra a troca do `vec0` depois do L0.

## Desenho técnico (padrões da revisão de arquitetura)
- **Pacote**: `rag/{chunker,fts,vectors,search,citations,export}.py` puros, `rag/index.py`, `llm/prompts/rag_chat.py`,
  `llm/local_embed.py` (adaptador por nome no registro), `store/rag.py`, `ui/{history,meeting,player,timeline}.py`.
- **Fila**: o leve despacha por `kind` (hoje tudo vai para `run_llm_job` [fato]); claim por `(kind = 'index'), id` (o
  `llm` primeiro; o claim é serial por classe [fato]); enfileirar = uma instrução com `NOT EXISTS` de `index` pendente
  da reunião, mais `params_json.reason`; a fila agrega "Índice: N reuniões (M com erro)"; `_on_job_done` com `index` só
  recarrega janelas (hoje abriria a revisão [fato]); "tentar de novo" escolhe o worker pela classe; estimativa só na
  classe pesada; `--once` não lança o leve.
- **Encadeamento**: `JobRunner._finish_ok` chama `rag.sync_text` e `enqueue_index` (erro só no log);
  `LlmJobRunner._finish_ok` e `_fail_partial` com alguma tarefa (salvo `test`) ok chamam `enqueue_index`; no app,
  `on_edited(mid)` na revisão e nas abas de título, textos e ações; parar a gravação chama `enqueue_missing()`.
- **Diff**: `sync_text` numa transação, casando por (kind, `text_hash`, ocorrência): trechos mantidos ganham tempos,
  segmentos, job e pessoas novos sem perder os vetores; `fill_vectors` fora da transação, lotes de 64 num `TaskGroup`
  sob `llm.max_parallel`, grava só se o trecho segue com o mesmo hash; normaliza sempre (a OpenAI não normaliza [fato]);
  `RetryingProvider.embed` ganha retentativa e timeout (hoje só repassa [fato]); fake com falha em `embed` e vetores por
  saco de palavras (busca híbrida testável sem rede).
- **Lease**: o `index` nunca espera a lease (seguraria a fila leve); embedder local gravando: só o FTS, "vetores
  adiados".
- **Vetores sem sqlite-vec (revê D-024)**: o `vec0` 0.1 também varre tudo [exp] e exigiria a extensão em toda conexão
  que apaga reunião. Assinatura = sinal de v − μ (μ por modelo, refeito sempre que os vetores dobram, até 2 mil, e então
  congelado; refazer regrava as assinaturas sem rede); o app guarda as assinaturas do modelo ativo (cache por modelo,
  `COUNT` e `MAX(id)`), pega max(200, 1 % de n) candidatos por Hamming (`np.bitwise_count`), até 2 mil, e reordena pelo
  cosseno exato. [exp, arquiteto, sintético anisotrópico: 200 mil × 768 = 19,2 MB e 15,5 ms; recall@10 de 0,935 sem
  centralizar e 1,000 centralizando; em 384, 0,805 e 0,996]
- **Provedor local (F5.1)**: `providers.active_embedder()`; `load_provider` sem chave para `local` (hoje exige [fato]);
  `rag.embedder_status(store)` -> (pronto, motivo, modelo) único; pronto = `.onnx` e `tokenizer.json` com sha256 na
  pasta de modelos, revisão fixada, baixados por `--live --prepare --what rag` no `ModelPreparer`; pooling médio com
  máscara e L2; no app, `onnxruntime` só dentro de uma `Task`, 1 thread, sessão aberta com o Histórico e solta após 10
  min; `onnxruntime` e `tokenizers` nas listas proibidas dos testes estáticos.
- **Busca no app**: `Task` com contador de geração; vetor da consulta no Enter ou 600 ms depois de parar de digitar, com
  cache; marcadores do `snippet()` trocados por negrito depois do `html.escape`; tempos no log.
- **Citações**: `rewrite_citations` generalizado por apelido; link `listener://open?m=&t=&seg=`; o chat é redesenhado
  das citações gravadas.
- **Player**: thread com `OutputStream.write` em blocos de ~0,25 s; 60 s de L e R por vez (`decode_range`), ganho ½,
  reamostragem com estado para a taxa do dispositivo; posição pelos quadros escritos menos a latência (`QTimer` de 100
  ms).
- **Reunião aberta**: `LlmTabsGroup` extraído da revisão e usado pelas duas; `TranscriptDialog` vira `TranscriptTab`;
  `ChatPanel` sem mudança; `open_meeting(mid, aba, t_ms)`, com `open_transcript` como apelido; `messageClicked` ligado
  (hoje não [fato]); lista num `QAbstractTableModel` carregado numa `Task`; participantes pelos clusters; atalho
  `history` em `HOTKEY_ACTIONS`, foco por `platform.bring_to_front`.
- **`--check`**: bloco `rag` por reunião (trechos por tipo, vetores por modelo, faltantes, último `index`) e geral
  (cobertura, `rag_fts_docsize` contra `rag_chunks`, tempo de 5 consultas fixas, U-027); banco em v7: "esquema anterior
  à F5"; nada de ML.
- **Saída**: `quit` e `emergency_stop` param o player, cancelam o chat global e esperam as `Task`s antes de fechar o
  banco; `fmt_usd` com 4 casas abaixo de um centavo.

## Comportamento
1. O pesado conclui: trechos e FTS na hora; os vetores vêm no `index`, depois do job `llm`.
2. Revisão de falantes, edição de título, ata ou ações: `index` da reunião.
3. Sem embedder pronto (sem chave, Anthropic, local sem modelo ou sem `onnxruntime`) ou sem rede: o job conclui com o
   FTS e anota "N trechos sem vetor: motivo"; a varredura da próxima abertura tenta de novo, sem laço de retentativa.
4. Trocar o modelo de embeddings: estimativa e "reindexar agora"; os vetores do modelo anterior ficam (voltar não
   custa).
5. Gravando: busca, histórico e chat funcionam e nada abre sozinho; com a fonte "sistema inteiro", o player pede
   confirmação antes de tocar (o som entraria no canal R, Q9); com o embedder local, os vetores esperam o fim.
6. Apagar reunião não existe na F5 (Q8); hoje só o Cancelar da gravação apaga, e trechos, FTS e vetores saem em cascata.
7. Chat global sem provedor de LLM com chave: aviso com o caminho nas Configurações; sem trecho relevante, a LLM diz que
   não achou.
8. Player sem dispositivo de saída ou sem o arquivo de áudio do mês: aviso, nada quebra; outro clique troca o ponto;
   fechar a reunião para o som.

## Aceite (G5), em reuniões reais
| Critério | Número | Como medir |
| --- | --- | --- |
| Citação certa no chat global | 8 de 10 perguntas sobre 5 ou mais reuniões | U-027 e `--check` |
| Clique toca o ponto certo | 10 de 10 cliques começam até 1 s antes da fala | dono, 10 cliques; teste da posição |
| Busca por palavra | até 300 ms com 50 reuniões | tempos no log; `--check` |
| Busca híbrida | até 1,5 s com Gemini (com a rede) | tempos no log |
| Índice | vetores de uma reunião de 1 h até 1 min depois da ata | `processing_jobs` |
| Interface | reindexar 50 reuniões sem travar | visual e teste offscreen |

## Riscos
| Risco | Mitigação |
| --- | --- |
| Recuperação ruim em pt-BR com erro de transcrição | texto revisado, nome, cargo e título no trecho; G5 mede |
| Assinatura binária perde qualidade com vetores reais | μ por modelo; recall com Gemini (B13) e e5 (F5.1); senão `vec0` |
| FTS5 com termo muito frequente: 100 a 127 ms com 60 mil trechos [exp] | `LIMIT 50`; tempos no log; G5 mede |
| e5 local: 0,2 a 0,5 s por trecho no Xeon [chute] | 2 threads, prioridade baixa, mais novas primeiro, estimativa |
| e5 int8 sem AVX-512 satura [exp] | Windows compara int8 e fp32 (cosseno médio >= 0,99) |
| Main cresce 300 a 400 MB por ano [chute] | float16 no BLOB se o L0 aprovar; tamanho no `--check` |
| A LLM inventa citação | link só para as linhas enviadas (até 5 s); U-027 mede |
| Suíte já em ~17,5 s | lógica pura sem Qt; telas só abrir, fechar e um clique |
| Windows: FTS5, DLL do onnxruntime, foco, taxa do MME, .docx aberto no Word | "Verificar no Windows" |
| Custo [chute]: ~US$ 0,003 por hora indexada e por pergunta | selo de custo; soma no mês |

## Perguntas (padrão entre parênteses)
- Q1 Índice automático ao processar e ao revisar, com o custo dos embeddings (~US$ 0,003 por hora)? (sim)
- Q2 Busca e chat global incluem ata, ações e resumo executivo, além da transcrição? (sim)
- Q3 Guardar as conversas do chat global, com "nova conversa"; apagar só pelo usuário? (sim)
- Q4 Legendas ao vivo de reunião ainda não processada entram na busca? (não)
- Q5 Modelo local `multilingual-e5-small` (118 MB, 384 dimensões), com o `base` (279 MB, 768) como opção? (sim)
- Q6 A reunião aberta substitui a janela "Transcrição": a fila, a revisão e o clique na notificação abrem a reunião; a
  revisão de falantes fica como está? (sim)
- Q7 Dependências novas: `python-docx` (MIT) e o extra `local` (onnxruntime, tokenizers e huggingface-hub, os mesmos que
  o faster-whisper já traz)? (sim)
- Q8 Apagar reunião pelo Histórico, com confirmação, entra agora? (não: F6)
- Q9 Gravando com a fonte "sistema inteiro", o player avisa e só toca com confirmação? (sim; com a fonte "app", direto)
- Q10 Dividir: F5 com o G5 (busca, histórico, chat global, player, .md e área de transferência) e F5.1 depois
  (embeddings locais, linha do tempo U-008, destaque contínuo U-025 e .docx)? (sim)
- Q11 Chat global com o modelo forte (citação melhor, ~5 vezes o custo por pergunta)? (não: o rápido)

## Lotes ([chute] F5 8,5 dias e F5.1 3 dias de foco; o roadmap previa 4 a 5 para tudo)
- L0 medição no Linux: FTS5 com 60 mil trechos; assinatura binária com sintético anisotrópico; o que só o Windows mede
  (FTS5 do python.org, Gemini com a chave) vai para "Verificar no Windows": 0,5 d
- L1 v8, enumerações, repositórios, claim, fila e controlador: 1,5 d
- L2 chunker (transcrição, ata, ações, reunião), sincronia e diff, vetores, retentativa, varredura: 1,5 d
- L3 busca e chat global (citações, conversas, U-027): 2 d
- L4 Histórico, reunião aberta, player, U-024, U-026, U-028, U-029, .md e área de transferência: 2 d
- L5 grupo "Busca", reindexar com estimativa, `--check` do G5: 1 d
- F5.1: L6 provedor local e preparo (L0 do e5 int8 contra fp32 no Linux e no Windows): 1,5 d · L7 linha do tempo,
  destaque contínuo e .docx: 1,5 d
