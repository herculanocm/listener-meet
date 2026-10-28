# Roadmap — fases e gates

Quando ler: ao planejar ou fechar uma fase (T2). Ordem do handoff §10 (captura → SQLite → pyannote com perfis → LLM →
telas), com warm-up e widget já na F1 porque os gates são medidos em reunião real (CP6). Esforço em dias de foco é
[chute]; no calendário de gestor, o triplo (F0 a F6: 32 a 44 dias de foco, 4 a 6 meses). Status: `backlog.md`.
Tamanho-alvo: 2,5 KB.

| Fase | Entrega | Gate medido em reunião real | Esforço |
| --- | --- | --- | --- |
| F0 Preparo | venv 3.12 com tudo; token HF; chave Gemini; medir 1 h de áudio em CPU | G0: tudo instala; tempos de Whisper e pyannote medidos; decisões registradas | 2 d |
| F1 Captura, SQLite, warm-up, widget | captura de qualquer app ou do sistema; FLAC em blobs; eventos, marcadores, pausa, mudo; atalhos; warm-up e widget | G1: 3 reuniões reais em 2 plataformas com headset; L/R alinhados abaixo de 100 ms; nenhum buraco acima de 1 s; alerta de canal mudo disparou; pedaços tocáveis do banco | 5 a 7 d |
| F1.1 Ajustes pré-G1 | correções da revisão de código de 24/10 | G1 mais os seis critérios da spec | 1 d |
| F2 Perfis de voz | fila pós-reunião: Whisper em L e R, pyannote em R, embeddings, cosseno; perfil de um trecho; revisão mínima; opção LLM com áudio (D-020) | G2: 80 % ou mais do tempo de fala de R com a pessoa certa em 3 reuniões após uma rodada de confirmações; processamento até 1,5× a duração (plano B até 2×, D-041) | 8,5 a 10 d (revisto) |
| F3 LLM agnóstica | provedores Gemini, GPT e Claude; glossário; identidade por contexto; título, pré-resumo, ata, resumo executivo, transcrição revisada; testar conexão | G3: 2 de 3 atas aceitas com edições pequenas; troca de provedor sem código; identidade por contexto 70 % ou mais quando há pista | 4 a 6 d |
| F4 Ao vivo | legenda leve com nome; resumo parcial a cada N minutos | G4: atraso até 10 s; CPU do app até 35 %; nome certo em 70 % ou mais para quem tem perfil | 8,5 d (revisto na revisão de arquitetura de 25/10) |
| F5 RAG e histórico | FTS5 e vetores, busca híbrida, chat com citações, histórico com player por frase | G5: 10 perguntas sobre 5 ou mais reuniões, 8 com citação certa; clique toca o ponto certo | 8,5 d (revisto em 25/10) |
| F5.1 Busca local e linha do tempo | embeddings locais (D-021), linha do tempo (U-008), destaque contínuo (U-025), .docx | G5 com o embedder local | 3 d |
| F5.4 Design das telas | sistema visual do Windows 11 nos dois temas, ícones próprios e as telas revistas (D-061) | uso real (B1): telas claras e escuras a 150 % sem texto cortado, nenhuma janela mais lenta | 4 a 5 d [chute] |
| F6 Telas e UX de fábrica | janela principal, perfis, configurações totais, presets, notificação de reunião, backup, tema escolhido à mão (o do sistema veio na F5.4), primeiro uso | G6: 1 semana de uso diário sem terminal; todo o CP8 editável em uso | 7 a 10 d |
| F7 Linux e macOS | adaptadores de plataforma e empacotamento (CP15) | G7: testes da plataforma simulada passam nos 3 sistemas; uma reunião real em cada | sem estimativa |
