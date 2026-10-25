# F5 — Base consultável, histórico e chat com RAG

Status: rascunho inicial (aprovação do Herculano antes de codar)
Fase e gate: F5 · G5
Cláusulas pétreas envolvidas: CP2, CP3, CP9, CP12, CP15
Quando ler: ao detalhar ou implementar esta fase (T2). Tabelas: `dados.md`.
Nota (F1.2 M-31): FTS5 e JSON1 presentes [fato] no SQLite 3.45.1 do Python 3.12 do WSL; confirmar no Windows e no macOS
(o Python do macOS às vezes vem sem `enable_load_extension`, necessário ao `vec0`); o fallback BLOB + numpy continua previsto.

## Escopo
1. `rag_chunks` de 1 a 2 minutos por turnos de fala, com nome e cargo no texto; `rag_fts` (FTS5; checar
   disponibilidade no SQLite do Python) e `rag_vectors` (sqlite-vec, D-024; modelo e dimensão por vetor).
   Embeddings: gemini-embedding-001 em 768 (D-009) ou modelo local multilingual-e5 (D-021), escolhível.
2. Busca híbrida: BM25 + cosseno, fundidos por reciprocal rank fusion. Reindexação com progresso ao trocar o
   modelo de embeddings (CP2).
3. Histórico: lista com busca e filtros por pessoa, data e título; reunião aberta com ata, resumo,
   transcrição com nome e cargo, marcadores com anotações e linha do tempo com faixas por falante (U-008);
   clicar em qualquer frase toca o áudio.
4. Chat com a LLM sobre uma reunião ou sobre todas, com citações clicáveis (reunião, quem falou, [mm:ss]).
5. Exportar ata e transcrição para .md, .docx e área de transferência, sob demanda (U-011, D-012).

## Gate G5
Dez perguntas sobre cinco ou mais reuniões, oito respondidas com a citação certa; o clique toca o ponto certo.
