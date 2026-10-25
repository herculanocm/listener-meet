"""Base consultável (F5, `specs/features/F5-rag-historico.md`): trechos, FTS5, vetores e (L3 em diante) busca e chat sobre
todas as reuniões. `chunker` e `vectors` são puros (sem banco); `index` lê e grava pelo `Store` (`store.rag`). Nada aqui
importa torch, SDK de LLM ou Qt no nível do módulo."""
