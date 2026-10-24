# F3 — LLM agnóstica: ata, resumo, identidade por contexto e revisão completa

Status: rascunho inicial (detalhar depois do G2; aprovação do Herculano antes de codar)
Fase e gate: F3 · G3
Cláusulas pétreas envolvidas: CP2, CP8, CP9, CP10, CP11, CP12, CP13, CP14, CP15
Quando ler: ao detalhar ou implementar esta fase (T2). Tabelas: `dados.md`.

## Objetivo
A LLM vira o cérebro de contexto: glossário no warm-up, nomes por contexto abaixo do limiar, título,
pré-resumo, ata (decisões; ações com dono e prazo; destaques dos marcadores), resumo executivo e
transcrição revisada com nomes e cargos. Trocar de provedor é só configuração (CP2).

## Escopo
1. Interface única de provedor em `listener/llm/base.py`: `complete(messages, schema=None, audio=None)`,
   `embed(texts)`, `capabilities` (texto, JSON estruturado, áudio, embeddings), `test_connection()`.
   Adaptadores: `gemini.py` (google-genai, inicial), `openai.py`, `anthropic.py`. Prompts e esquemas de
   saída (JSON) em `listener/llm/prompts/`, do projeto, não do provedor.
2. Configurações com uma sub-aba por provedor: chave (no banco, CP10), modelo, parâmetros, testar conexão
   com latência; provedor ativo para texto e para embeddings; custo estimado por reunião e por mês (U-009).
3. Funções: glossário do warm-up (título, participantes, glossário fixo); identidade por contexto para
   clusters abaixo do limiar ou ambíguos (pistas na transcrição, lista de participantes, cargo), com
   justificativa, e segunda opinião por áudio quando o provedor aceitar; título sugerido; pré-resumo; ata;
   resumo executivo; transcrição revisada. A LLM nunca sobrescreve um nome de alta confiança do pyannote (CP11).
4. Tela de revisão completa (seção 5 do handoff): título e pré-resumo editáveis com "gerar de novo";
   confirmação dos falantes com sugestão e justificativa da LLM e trecho de áudio; o banco guarda a versão da
   LLM e a editada (`summaries.text_llm` e `text_edited`).
5. `LLMAudioTranscriber` (D-020): transcrição final pela LLM com áudio como opção ao WhisperX.
6. Chat sobre uma reunião (sem RAG ainda): perguntas respondidas com a transcrição completa no contexto.

## Gate G3
Em três reuniões: 2 de 3 atas aceitas com edições pequenas; troca de provedor pela Configurações sem tocar em
código, demonstrada com GPT ou Claude; identidade por contexto acerta pelo menos 70% quando há pista.

## Perguntas abertas
- Modelo inicial do Gemini (padrão: o modelo "flash" mais recente para resumo parcial e o "pro" para a ata).
- Idioma das saídas: sempre pt-BR, mesmo em reunião em inglês? (padrão: pt-BR)
- Limite de custo por reunião e alerta (padrão: aviso acima de um valor configurável).
