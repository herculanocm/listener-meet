# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Estado em 24/10/2026
- Código: F1 implementada; **F1.1 implementada** (A1 a A17, U-016, U-017; D-035): 41 testes com plataforma simulada
  passam no WSL, ruff limpo, CI no GitHub Actions. O que só o Windows verifica (`RegisterHotKey`, endpoint do mudo,
  id de endpoint, `AppUserModelID`, suspensão, foco do popup, lista de apps sem psutil) ainda não rodou: `--selftest` e B1.
- Git: commits em 24/10 (F1 com docs compactos; F1.1; CP16); branch `main`; sem remoto. Commit a cada grande alteração (CP16).
- Specs: F0 aprovada e medida (falta pyannote, B4) · F1 implementada, G1 pendente · F1.1 implementada · F2 **aprovada**
  (D-036), código depois do G1 · F3 a F6 rascunho inicial · F7 futuro. Licença MIT (D-034).

## Como retomar (5 minutos)
1. T0: `AGENTS.md`, `specs/01-clausulas.md`, este arquivo. Depois só a camada da tarefa.
2. Testes: `python -m pytest -q` e `ruff check .` (qualquer sistema). No Windows, venv 3.12 em
   `C:\Users\<usuário>\dev\listener-meet\.venv` e modelos em `C:\Users\<usuário>\listener-meet\models\`.
3. Antes de código: a spec da entrega em `specs/features/` está `aprovada`? (CP13)

## Bloqueios que só o dono destrava
| # | O quê | Destrava |
| --- | --- | --- |
| B1 | no Windows: `python -m listener --selftest` e depois `python -m listener`, passar pelo assistente (agora com a página de log), dizer o que está estranho | verifica a F1.1 nativa e a interface, nunca vista num monitor |
| B2 | gate G1: 3 reuniões reais com headset em 2 plataformas; `python -m listener --check <pasta do banco>` (já imprime deriva e deslocamento L/R) | fecha F1 e F1.1; dá áudio real para o G2 |
| B4 | `HF_TOKEN` no Windows com os termos dos modelos pyannote aceitos | medir pyannote (F0) e toda a F2 |
| B5 | gravação real de ~1 h com headset (ou as do G1) | medições reais de Whisper e pyannote |

Fechados: B3 (F2 aprovada, D-036) · B6 (primeiro commit) · B7 (MIT, D-034) · B8 (F1.1 aprovada, D-035) · B9 (compactação, D-031).

## Ordem sugerida das próximas sessões
1. Dono roda o `--selftest` e o app no Windows (B1); LLM corrige o que aparecer.
2. Dono grava as três reuniões (B2); LLM fecha o G1 em `decisoes.md` com deriva, deslocamento L/R e tamanho do banco.
3. Dono define o token (B4); LLM mede o pyannote, registra, e implementa a F2 como aprovada; G2 nas gravações do G1.
4. Detalhar e aprovar a F3; e assim por diante.

## Dívidas técnicas
- Saúde dos canais começa "ok" por até 10 s sem sinal; decidir se R avisa antes quando nunca recebeu sinal.
- Deriva de relógio: só instrumentada (A7); corrigir em spec própria se o G1 mostrar mais de 100 ms por hora (P22).
- Textos da interface fixos em pt-BR; prever i18n (`tr()` do Qt ou gettext) antes da F6 (CP15).
- Pasta de modelos ainda não está nas Configurações (entra na F2).
- FTS5: confirmar no SQLite do Python em Windows e Linux antes da F5.
- Sem testes de interface; avaliar `pytest-qt` na F6. `scripts/ui_smoke.py` só roda no venv do Windows.
- Testes do gravador usam relógio real e threads: podem flutuar sob carga; repetir antes de procurar bug.
- O eco do warm-up é medido só no "Testar 3 s", não de forma contínua; o `--check` dá o número preciso.
- `Recorder.stop()` espera o último pedaço na thread da interface (até 1 s de tela congelada); revisar na F2.
- Antes de publicar o repositório: conferir que nada pessoal sobrou em `memory/` e `decisoes.md` (caminhos usam `<usuário>`; CP15).

## Medições a registrar em `decisoes.md`
pyannote community-1 por hora em CPU (B4) · tamanho do banco por hora de reunião (G1) · tempo de `list_playing_apps`
(B1, no log) · deriva por canal e deslocamento L/R (G1, `--check`) · precisão dos perfis (G2).
