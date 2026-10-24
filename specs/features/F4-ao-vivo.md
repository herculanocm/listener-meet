# F4 — Ao vivo: legenda leve e resumo parcial

Status: rascunho inicial (aprovação do Herculano antes de codar)
Fase e gate: F4 · G4
Cláusulas pétreas envolvidas: CP5, CP8, CP11 ("durante a reunião"), CP12, CP14, CP15
Quando ler: ao detalhar ou implementar esta fase (T2). Tabelas: `dados.md`.

## Objetivo
Durante a reunião, legenda com nome e cargo com atraso de 5 a 10 s e um resumo parcial a cada N minutos,
sem roubar a CPU do app de reunião (D-005).

## Escopo
1. Modo leve: VAD (Silero) nos dois canais, janelas de ~8 s, faster-whisper small ou turbo int8 em threads
   limitadas; `captions` no banco (CP3) e exibidas no widget (última frase) e num painel opcional.
2. Nome ao vivo "best effort": embedding do trecho contra os perfis (sem diarização completa); abaixo do
   limiar mostra "Participante".
3. Resumo parcial: a cada intervalo configurável (padrão 5 min), a LLM recebe o trecho novo e atualiza o
   resumo visível, sinalizando possíveis decisões e ações.
4. Configurações: ligar/desligar o modo ao vivo, modelo leve, intervalo do resumo, teto de CPU.
5. A passagem final da F2 substitui as legendas na exibição; as legendas ficam guardadas.

## Gate G4
Atraso médio da legenda ≤ 10 s; CPU do app ≤ 35% ao lado do app de reunião; nome ao vivo certo em ≥ 70% dos
trechos de quem tem perfil; nenhuma perda de áudio na gravação enquanto o modo ao vivo roda.

## Perguntas abertas
- O painel de legenda ao vivo é uma janela à parte (arrastável) ou parte do widget? (padrão: janela à parte, opcional)
