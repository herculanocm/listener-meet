# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Estado em 24/10/2026
- Código: F1 implementada (`listener/`: platform, capture, store, ui, config); 21 testes com plataforma simulada passam no
  WSL; `--selftest` real no Windows ok; telas vistas só em modo offscreen. Nenhuma linha de código alterada desde então.
- Git: reiniciado limpo em 24/10 (D-032); primeiro commit no mesmo dia com a F1 e a documentação compacta; branch
  `main`; sem remoto. Commits seguintes só quando ele pedir.
- Specs: F0 aprovada e medida (falta pyannote, B4) · F1 implementada, G1 pendente · F1.1 rascunho (B8) · F2 rascunho (B3) ·
  F3 a F6 rascunho inicial · F7 futuro.

## Como retomar (5 minutos)
1. T0: `AGENTS.md`, `specs/01-clausulas.md`, este arquivo. Depois só a camada da tarefa.
2. Testes: `python -m pytest -q` (qualquer sistema). No Windows, venv 3.12 em `C:\Users\<usuário>\dev\listener-meet\.venv`
   e modelos em `C:\Users\<usuário>\listener-meet\models\` (caminhos desta máquina, não do produto).
3. Antes de código: a spec da entrega em `specs/features/` está `aprovada`? (CP13)

## Bloqueios que só o dono destrava
| # | O quê | Destrava |
| --- | --- | --- |
| B1 | abrir o app num monitor (`python -m listener`), passar pelo assistente, dizer o que está estranho | interface da F1 nunca foi vista |
| B2 | gate G1: 3 reuniões reais com headset em 2 plataformas; `python -m listener --check <pasta do banco>` | fecha a F1; dá áudio real para o G2 |
| B3 | aprovar `features/F2-pipeline-perfis.md` (Q1 a Q9) | código da F2 |
| B4 | `HF_TOKEN` no Windows com os termos dos modelos pyannote aceitos | medir pyannote (F0) e toda a F2 |
| B5 | gravação real de ~1 h com headset (ou as do G1) | medições reais de Whisper e pyannote |
| B7 | licença (P18; padrão MIT) | arquivo LICENSE |
| B8 | aprovar `features/F1.1-ajustes-pre-g1.md` (A1 a A17, U-016, U-017; Q1 a Q3) | correções antes do G1 |

Fechados: B6 (primeiro commit, 24/10) · B9 (compactação aprovada e executada, D-031).

## Ordem sugerida das próximas sessões
1. Dono responde a F1.1 (B8) em bloco; LLM implementa exatamente o aprovado ([chute] um dia), roda os testes e o `--selftest`.
2. Dono roda o app (B1) e as três reuniões (B2); LLM corrige o apontado e fecha o G1 em `decisoes.md` (deriva e
   deslocamento L/R pelo `--check`).
3. Dono responde Q1 a Q9 da F2 (B3) e define o token (B4); LLM mede o pyannote e registra.
4. LLM implementa a F2 como aprovada; G2 nas gravações do G1. Depois F3 em diante.

## Dívidas técnicas fora da F1.1
- Saúde dos canais começa "ok" por até 10 s sem sinal; decidir se R avisa antes quando nunca recebeu sinal.
- Textos da interface fixos em pt-BR; prever i18n (`tr()` do Qt ou gettext) antes da F6 (CP15).
- Pasta de modelos ainda não está nas Configurações (entra na F2).
- FTS5: confirmar no SQLite do Python em Windows e Linux antes da F5.
- Sem testes de interface; avaliar `pytest-qt` na F6. `scripts/ui_smoke.py` só roda no venv do Windows.
- Testes do gravador usam relógio real e threads: podem flutuar sob carga; repetir antes de procurar bug.
- Teste do mic no warm-up toca no dispositivo de saída padrão (pode ser a caixa, não o fone).
- `Recorder.stop()` espera o último pedaço na thread da interface (até 1 s de tela congelada); revisar na F2.
- Antes de publicar o repositório: conferir que nada pessoal sobrou em `memory/` e `decisoes.md` (caminhos usam `<usuário>`; CP15).

## Medições a registrar em `decisoes.md`
pyannote community-1 por hora em CPU (B4) · tamanho do banco por hora de reunião (G1) · tempo de `list_playing_apps` (B1) ·
deriva por canal e deslocamento L/R (G1) · precisão dos perfis (G2).
