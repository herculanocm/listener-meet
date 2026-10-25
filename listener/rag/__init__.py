"""Base consultável (F5, `specs/features/F5-rag-historico.md`): trechos, FTS5, vetores, busca híbrida e chat sobre todas
as reuniões. `chunker`, `vectors`, `fts` e `citations` são puros (sem banco); `index` lê e grava pelo `Store` (`store.rag`);
`search` (busca, vetor da consulta e cache das assinaturas) e `chat_global` (chat, conversas e avaliação) leem o banco e
chamam o provedor por `run_async`, fora da thread da interface. Nada aqui importa torch, SDK de LLM ou Qt no nível do
módulo."""
