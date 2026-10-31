# AGENTS.md — leia isto primeiro

Spec-Driven Development: primeiro a spec, depois a aprovação do dono (Herculano), só então o código. O dono decide;
a LLM propõe, pergunta e executa o que foi aprovado. Tamanho-alvo deste arquivo: 3 KB.

## Começo de toda sessão: em que ambiente estou?
O app existe para o Windows: performance e experiência no Windows nativo vêm primeiro (CP15). Desenvolver no Linux ou no
WSL está certo, mas nada está pronto antes de rodar no Windows. Antes de qualquer tarefa, rode
`python -c "import sys, platform; print(sys.platform, platform.release())"` (no Windows sem `python` no PATH:
`py -3.12 -c ...`) e diga ao dono, em uma linha, onde a sessão está:
- `win32` = **Windows nativo**, o alvo. Antes da tarefa pedida, abra "Verificar no Windows" em `specs/backlog.md`, diga
  o que está pendente e proponha rodar na ordem (gravar reunião e usar chave pedem o OK do dono). Meça tempo, CPU e RAM
  e anote em `specs/decisoes.md` (Medições) e `memory/maquina.md`.
- `linux` com `microsoft` na versão = **WSL** (Ubuntu dentro do Windows); `linux` sem isso = **Linux nativo** (Ubuntu).
  Nos dois, testes só com os fakes (`LISTENER_PIPELINE`, `LISTENER_LLM` e `LISTENER_LIVE` = `fake`; interface com
  `QT_QPA_PLATFORM=offscreen`): áudio real, modelos, EDR e `listener/platform/win32` não rodam. Ao fechar cada entrega,
  acrescente em "Verificar no Windows" o que só o Windows verifica e lembre o dono do que está pendente lá.

## Leitura em camadas (leia a camada da tarefa, não tudo)
| Camada | Quando | Arquivos |
| --- | --- | --- |
| T0 | sempre | este arquivo · `specs/01-clausulas.md` (CP1 a CP17) · `specs/backlog.md` (estado, bloqueios, o que fazer agora) |
| T1 | mexer em produto ou arquitetura | `specs/00-handoff-v4.md` (mestre; em conflito, vence) · `ARCHITECTURE.md` · `specs/decisoes.md` |
| T2 | trabalhar numa fase | `specs/features/F<n>-*.md` (só o que está nela é escopo) · `specs/dados.md` · `specs/ux-melhorias.md` · `specs/roadmap.md` |
| T3 | ambiente desta máquina ou preferências do dono | `memory/dono.md` · `memory/maquina.md` |
| T3 | revisar código ou propor correção | `memory/revisao-codigo.md` (achados já decididos: não reapontar sem fato novo) |

Como escrever aqui: `specs/README.md` (ciclo de vida, template, regras de escrita para IA). Quem edita: o handoff, só o
Herculano; `AGENTS.md` e `specs/README.md`, a LLM propõe e ele aprova; o resto, a LLM dentro do que foi aprovado.

## Regras inegociáveis
- CP13: nenhum código, build, dependência ou script sem autorização explícita dele, por entrega. Documentos podem.
- CP14: só CPU; só open source (Python) e a LLM paga (Gemini inicial). O resto é proposta.
- CP15: open source e multiplataforma; nada de SO fora de `listener/platform/<os>/`; nada pessoal no código.
- Nunca: ativar recursos das plataformas de reunião (CP4); apagar dados sozinho (CP3); persistir em arquivos soltos (CP9).
- CP16: toda grande alteração ou item importante fecha com um commit (entrega implementada, spec aprovada, decisão,
  reestruturação de docs, gate medido); a LLM commita ao fechar, sem pedir; mensagem em PT-BR citando a spec ou a
  decisão. Nenhuma sessão termina com mudança grande sem commit. `*.db` nunca no git nem em pasta sincronizada.
- CP17: Python 3.12 é o piso e só sobe; sem retrocompatibilidade com versões anteriores; usar os recursos e otimizações
  da versão em uso, performance primeiro.

## Fluxo por entrega (D-040)
1. **Spec.** A LLM orquestradora rascunha a spec em `specs/features/` com o template de `specs/README.md`.
   Status: `rascunho` → `em revisão` → `aprovada` → `implementada` → `verificada`.
2. **Revisão de arquitetura.** Um agente arquiteto, somente leitura, revisa a spec contra o código real e devolve lacunas,
   decisões de desenho com recomendação e riscos com mitigação. A orquestradora incorpora tudo: decisão técnica entra com
   padrão; só decisão de produto vira pergunta ao dono, como item numerado "incluir? sim/não" (ele responde em bloco).
3. **Aprovação.** Só `aprovada` ("Aprovada por Herculano em AAAA-MM-DD" na spec, ou dito no chat com data) libera código
   (CP13), e só o que a spec diz. Desvio ou descoberta volta para a spec, não para o código.
4. **Implementação.** Um agente especialista implementa com briefing completo (spec, decisões já tomadas, restrições);
   testes, ruff e mypy verdes antes de cada commit (CP16); um implementador por vez quando os arquivos se cruzam;
   relatório final com o que só o Windows verifica (vai para "Verificar no Windows" no backlog). Correção abaixo de duas
   horas, a orquestradora faz direto, sem agente.
5. **Revisão e fechamento.** Um agente revisor novo lê só o diff (bugs, corridas, cláusulas); o implementador corrige com o
   contexto que já tem; a orquestradora verifica por conta própria (testes, lint, tipos, telas), atualiza `specs/backlog.md`
   (único lugar de estado), `specs/decisoes.md` e `memory/` se houver fato novo, e commita. O dono verifica no Windows e
   mede o gate em reunião real (`specs/roadmap.md`), anotado em `specs/decisoes.md`.

Pontos de contato do dono por entrega: aprovar a spec, verificar no Windows, medir o gate.

## Convenções
PT-BR em documentos, commits e interface; código com nomes em inglês e comentários em PT-BR. Stack fixa pelo handoff:
Python 3.12 ou mais novo (CP17) num venv, PySide6, SQLite (`listener.db` e `listener-audio.db`), faster-whisper ou WhisperX, pyannote, LLM
agnóstica com Gemini inicial. Separe sempre [fato], [exp] e [chute]. Testes: `python -m pytest -q`.

## Ferramentas
Claude Code lê `CLAUDE.md` e Gemini CLI lê `GEMINI.md`; os dois só apontam para cá. Codex, Cursor e similares leem este
arquivo direto. Outra LLM: cole este arquivo no início da conversa.
