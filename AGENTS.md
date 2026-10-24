# AGENTS.md — leia isto primeiro

Spec-Driven Development: primeiro a spec, depois a aprovação do dono (Herculano), só então o código. O dono decide;
a LLM propõe, pergunta e executa o que foi aprovado. Tamanho-alvo deste arquivo: 3 KB.

## Leitura em camadas (leia a camada da tarefa, não tudo)
| Camada | Quando | Arquivos |
| --- | --- | --- |
| T0 | sempre | este arquivo · `specs/01-clausulas.md` (CP1 a CP15) · `specs/backlog.md` (estado, bloqueios, o que fazer agora) |
| T1 | mexer em produto ou arquitetura | `specs/00-handoff-v4.md` (mestre; em conflito, vence) · `ARCHITECTURE.md` · `specs/decisoes.md` |
| T2 | trabalhar numa fase | `specs/features/F<n>-*.md` (só o que está nela é escopo) · `specs/dados.md` · `specs/ux-melhorias.md` · `specs/roadmap.md` |
| T3 | ambiente desta máquina ou preferências do dono | `memory/dono.md` · `memory/maquina.md` |

Como escrever aqui: `specs/README.md` (ciclo de vida, template, regras de escrita para IA). Quem edita: o handoff, só o
Herculano; `AGENTS.md` e `specs/README.md`, a LLM propõe e ele aprova; o resto, a LLM dentro do que foi aprovado.

## Regras inegociáveis
- CP13: nenhum código, build, dependência ou script sem autorização explícita dele, por entrega. Documentos podem.
- CP14: só CPU; só open source (Python) e a LLM paga (Gemini inicial). O resto é proposta.
- CP15: open source e multiplataforma; nada de SO fora de `listener/platform/<os>/`; nada pessoal no código.
- Nunca: ativar recursos das plataformas de reunião (CP4); apagar dados sozinho (CP3); persistir em arquivos soltos (CP9).
- Commits só quando ele pedir; mensagem em PT-BR citando a spec. `*.db` nunca no git nem em pasta sincronizada.

## Fluxo
1. Ache a spec da entrega em `specs/features/`; se não existir, escreva uma com o template de `specs/README.md`.
   Status: `rascunho` → `em revisão` → `aprovada` → `implementada` → `verificada`.
2. Toda melhoria (UI/UX, técnica, dados) é item numerado "incluir? sim/não" na spec; ele responde em bloco.
3. Só `aprovada` ("Aprovada por Herculano em AAAA-MM-DD" na spec, ou dito no chat com data) libera código, e só o que a
   spec diz. Desvio ou descoberta volta para a spec, não para o código.
4. Cada fase fecha com o gate medido em reunião real (`specs/roadmap.md`), anotado em `specs/decisoes.md`.
5. Ao encerrar a sessão: atualizar `specs/backlog.md` (único lugar de estado), o status da spec e `memory/` se houver
   fato novo sobre a máquina ou sobre o dono.

## Convenções
PT-BR em documentos, commits e interface; código com nomes em inglês e comentários em PT-BR. Stack fixa pelo handoff:
Python 3.12 num venv, PySide6, SQLite (`listener.db` e `listener-audio.db`), faster-whisper ou WhisperX, pyannote, LLM
agnóstica com Gemini inicial. Separe sempre [fato], [exp] e [chute]. Testes: `python -m pytest -q`.

## Ferramentas
Claude Code lê `CLAUDE.md` e Gemini CLI lê `GEMINI.md`; os dois só apontam para cá. Codex, Cursor e similares leem este
arquivo direto. Outra LLM: cole este arquivo no início da conversa.
