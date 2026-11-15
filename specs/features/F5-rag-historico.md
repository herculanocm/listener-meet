# F5 — Base consultável: busca, histórico e chat sobre todas as reuniões

Status: implementada · Aprovada em: 2026-10-25 · Decisões: D-009, D-012, D-047, D-048 · CPs: CP2, CP3, CP9, CP15
Quando ler: ao mexer no índice (`listener/rag/`), na busca, no Histórico, na reunião aberta ou no player.
Tamanho-alvo: 6,5 KB.

## Objetivo
O usuário acha o que foi dito em qualquer reunião, por palavra ou por sentido, abre a reunião no ponto exato e ouve, e
pergunta à LLM sobre todas as reuniões, com citações clicáveis.

## Comportamento
- Trechos (`rag_chunks`) por tipo: `transcricao` (o que a leitura mostra; fecha em fim de turno depois de 60 s e sempre
  antes de 120 s ou 1.400 caracteres, sem sobreposição), cada item da ata exibida no tempo do `[mm:ss]` dele, `acao`,
  `executivo` e `reuniao`.
- Busca por palavra: FTS5 sem acento, pesos 3, 2 e 1 para título, falantes e texto; E implícito e `*` na última
  palavra (3 letras ou mais); com menos de 10 resultados, refaz com OU.
- Busca por sentido: só os vetores do modelo ativo; max(200, 1 % de n) candidatos (até 2 mil) pela assinatura
  binária, reordenados pelo cosseno exato.
- Busca híbrida: as duas listas (50 cada) fundidas por RRF (k = 60), com os filtros de pessoa, período e reunião antes
  do corte e teto de 5 trechos por reunião no Histórico. Sem vetor (15 s, 2 tentativas), só por palavra, com o selo.
- Índice em dois tempos: o job pesado grava trechos e FTS ao concluir; o job `index` (classe leve, um pendente por
  reunião, depois dos `llm`) refaz os trechos e pede só os vetores que faltam, em lotes de 64.
- O `index` entra no fim do job `llm`, na revisão de falantes e nas edições de título, ata e ações (espera de 30 s,
  renovada a cada edição); na abertura, uma varredura pega as reuniões sem trechos ou sem vetor do modelo ativo.
- Sem embedder pronto ou sem rede, o `index` conclui com o FTS e a nota "N trechos sem vetor: motivo", sem retentativa.
- `rag.auto_index` (sim) só liga os vetores automáticos; trechos e FTS entram sempre.
- Embeddings pagos em 768 dimensões (D-009): Gemini `gemini-embedding-001` ou OpenAI `text-embedding-3-small`; o local
  é da F5.1. Ao trocar de modelo, os vetores do anterior ficam (voltar não custa).
- Perguntar às reuniões: até `rag.top_k` (12) trechos, `rag.max_per_meeting` (3) por reunião; a citação `[R1 12:03]`
  liga à reunião e à linha enviada mais próxima, até 5 s, ou fica como texto. A pergunta passa pelo agente da F5.10.
- Player único: toca do ponto com L e R misturados, -10 s e +10 s em tempo de áudio, pulando as pausas da gravação; um
  som por vez. Gravando o sistema inteiro, pede confirmação antes de tocar (o som entraria no canal R).
- Exportar só por ação do usuário (D-012): .md e área de transferência.

## Interface
- Histórico (bandeja e `hotkeys.history`, `ctrl+alt+shift+h`, U-028): abas "Reuniões" (lista, filtros e, com busca, os
  trechos por reunião com "abrir neste ponto", U-024), "Ações abertas" (U-026) e "Perguntar às reuniões" (conversas
  guardadas e "Citação certa?", U-027). "Copiar com citação" nos trechos e nas respostas (U-029).
- Reunião aberta: uma janela por reunião, no lugar da "Transcrição"; abre num ponto, rola até a fala e toca.
- Configurações, página Busca: as `rag.*`, "Completar índice" e "Reindexar tudo…", com estimativa e confirmação.

## Dados
Migração v8, que exige o FTS5: `rag_chunks`, `rag_vectors`, `rag_models`, `rag_fts` (mantida por gatilhos) e
`chat_threads`; `chat_messages` reconstruída (`meeting_id` nulo = conversa global, `thread_id`, `rating`).
`JobKind.INDEX`. Chaves `rag.auto_index`, `rag.top_k`, `rag.max_per_meeting`, `hotkeys.history`.

## Código
- `listener/rag/chunker.py`: trechos; `listener/rag/index.py`: `sync_text`, `fill_vectors`, varredura.
- `listener/rag/fts.py`, `listener/rag/vectors.py`, `listener/rag/search.py`: palavra, sentido e fusão.
- `listener/rag/chat_global.py`, `listener/rag/citations.py`: recuperação, conversas e citações.
- `listener/rag/export.py`: exportar; `listener/rag/report.py`: G5 no `--check`.
- `listener/pipeline/index_steps.py`: `IndexJobRunner`; `listener/store/rag.py`: SQL do índice.
- `listener/ui/history.py`, `listener/ui/meeting.py`, `listener/ui/player.py`: as telas e o player.
- `tests/test_rag_index.py`, `tests/test_rag_search.py`, `tests/test_rag_chat.py`, `tests/test_player.py`.

## Invariantes
- CP3: o índice é derivado e refeito; apagar a reunião leva trechos, FTS e vetores pela cascata.
- `text_hash` cobre tipo, data, falantes, texto e título (este, fora nos trechos `transcricao`, F5.18), nunca o tempo:
  o trecho mantido não perde o vetor. A navegação usa o tempo, porque o reprocessamento troca os ids dos segmentos.
- `rag_chunks` e `rag_vectors` com AUTOINCREMENT: o cache das assinaturas conta com o id nunca voltar.
- CP15: sem sqlite-vec (D-048), só numpy; a assinatura é o sinal de v menos a média do modelo, que congela em 2 mil
  vetores.
- O `index` nunca espera a lease da gravação (seguraria a fila leve), fica fora de `PIPELINE_JOB_KINDS` e não grava
  `job_start` nem `job_done` em `events`.
- O vetor só entra se o trecho segue com o mesmo hash; normaliza sempre (a OpenAI não normaliza).
- A citação só casa com as linhas enviadas à LLM; a que não casa fica sem link e nunca some.
- A consulta do usuário nunca chega ao FTS5 como sintaxe; texto do usuário vai às telas como texto puro (R7-07).
- Player: quem escreve no dispositivo nunca espera decodificação (R7-02); o gravador chama `before_system_loopback`
  antes de abrir o loopback do sistema, e o player pausa (R8-01).
- A saída cancela chat e vetor da consulta em voo e espera as `Task`s antes de fechar o banco (R7-03).

## Fora de escopo
Reranker · busca no áudio sem transcrição · legendas ao vivo no índice · PDF.

## Aceite
G5, em reuniões reais: citação certa em 8 de 10 perguntas sobre 5 ou mais reuniões (U-027, `--check`) · 10 de 10
cliques começam até 1 s antes da fala · busca por palavra em até 300 ms com 50 reuniões (`--check`) · híbrida em até
1,5 s com o Gemini · vetores de uma reunião de 1 h até 1 min depois da ata.
