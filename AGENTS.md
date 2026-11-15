# AGENTS.md

Instruções para agentes de código (qualquer LLM) e para quem contribui. `CLAUDE.md` e `GEMINI.md` só apontam para cá.
Tamanho-alvo: 8 KB.

## O projeto
listener-meet é um app desktop para Windows (Python 3.12, PySide6, SQLite), de código aberto (MIT). Ele grava qualquer
reunião em dois canais (o microfone e o áudio do app da reunião), identifica quem fala por perfil de voz (pyannote),
transcreve na própria máquina (faster-whisper), mostra legenda ao vivo, gera ata e sugestões com uma LLM trocável
(Gemini, OpenAI ou Claude) e guarda tudo em SQLite para busca e perguntas. A versão está em `listener/__init__.py`.

## Regras inegociáveis
O texto completo das cláusulas (CP1 a CP17) está em `specs/01-clausulas.md`. Só o dono do projeto as altera.
- **Spec antes do código (CP13).** Nenhum código, build, dependência ou script sem uma spec `aprovada` pelo dono, e só
  o que a spec diz. Documentos podem ser escritos sem autorização.
- **Só CPU e só open source (CP14).** A única parte paga é a LLM. Dependência nova é proposta, não decisão.
- **Multiplataforma (CP15).** Nada específico de sistema operacional fora de `listener/platform/<os>/`. Nada pessoal no
  código: caminhos, nomes e dispositivos vêm da configuração.
- **Dados (CP3, CP9, CP10).** Tudo o que persiste vai para o SQLite do app. Nada é apagado sem o usuário pedir.
  Arquivos `*.db` nunca entram no git nem em pasta sincronizada: eles guardam as chaves de API em texto aberto.
- **Plataformas de reunião (CP4).** Nada é ativado nelas: sem bot, sem API da plataforma, sem UI Automation.
- **Identificação (CP11).** A LLM nunca troca um nome que o pyannote deu com alta confiança; só sugere abaixo do limiar.
- **Privacidade no log.** Nunca vão para o log nome de pessoa, fala, título de reunião ou de janela, e-mail, chave,
  token ou caminho do usuário. Vão ids, contagens, durações, modelos e estados.
- **Python (CP17).** O piso é 3.12 e só sobe. Sem retrocompatibilidade: use os recursos da versão em uso.
- **Commit (CP16).** Cada item fechado (entrega, spec aprovada, decisão, mudança grande de documentação) termina com
  um commit em PT-BR que cita a spec ou a decisão. O push é do dono. Nunca use `--no-verify` nem `push --force`.

## Ambiente
O alvo é o Windows 11 nativo: performance e experiência valem lá. No início da sessão, descubra onde está:
`python -c "import sys, platform; print(sys.platform, platform.release())"`.
- `win32`: Windows nativo. Tudo roda: áudio real, modelos, executável.
- `linux` (WSL quando a versão contém `microsoft`): só os testes com os fakes. Áudio real, modelos e
  `listener/platform/win32` não rodam. Ao fechar uma entrega, acrescente em "Verificar no Windows", no
  `specs/backlog.md`, o que só o Windows confirma.

Instalação no Windows, com as versões travadas:
```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements\windows.txt
.venv\Scripts\python.exe -m pip install -e . --no-deps
.venv\Scripts\python.exe -m listener
```
No Linux, sem a stack de ML: `python3 -m venv .venv` e
`.venv/bin/pip install -c requirements/windows.txt -e ".[dev,llm,docx]"` (a captura pede a `libportaudio2`).

## Comandos
| Para | Comando |
| --- | --- |
| Testes rápidos (a cada passo) | `python -m pytest -q` |
| Suíte inteira (antes de commitar) | `python -m pytest -q --tudo -n 4 --dist loadgroup > saida.txt 2>&1` |
| Lint e tipos | `ruff check .` · `mypy` · `mypy --platform linux` |
| Gravar alguns segundos sem interface | `python -m listener --selftest` |
| Relatório de um banco (gravações, ao vivo, índice, sugestões) | `python -m listener --check <pasta do banco>` |
| Executável e zip (só com o OK do dono) | `gerar-exe.bat` ou `python scripts\build_exe.py --zip` |

No Windows, use os executáveis do venv (`.venv\Scripts\python.exe`, `ruff.exe`, `mypy.exe`). Os testes usam fakes:
`LISTENER_PIPELINE`, `LISTENER_LLM` e `LISTENER_LIVE` com o valor `fake`, e a interface com
`QT_QPA_PLATFORM=offscreen`. A saída do pytest vai sempre para um arquivo (um teste preso é derrubado e nomeado por
`tests/hang_guard.py`). Não rode a suíte inteira nem o build durante uma gravação real: eles disputam a CPU.

## Mapa do repositório
| Caminho | O que é |
| --- | --- |
| `listener/platform/` | tudo o que depende do sistema operacional, atrás de `base.py` (`win32/`, `common/`) |
| `listener/capture/` | gravação em dois canais num relógio único; pedaços FLAC no SQLite |
| `listener/pipeline/` | pós-reunião: diarização, identificação por perfil de voz, transcrição, etapas da LLM |
| `listener/live/` | processo filho da legenda ao vivo |
| `listener/llm/` | contrato único de provedor, adaptadores (Gemini, OpenAI, Claude, local), prompts, custo |
| `listener/rag/` | índice (FTS5 e vetores), busca híbrida, chat com citações, "perguntar à base" |
| `listener/store/` | esquema, migrações, repositórios e fila de jobs no SQLite |
| `listener/ui/` | interface PySide6 (assistente, "Antes de gravar", widget, bandeja, Histórico, reunião) |
| `listener/config/` | padrões e tipos das configurações |
| `tests/` · `scripts/` · `requirements/` | suíte pytest · sondas de medição e build · locks do Windows |
| `specs/` · `memory/` | documentação de produto e de decisão · fatos do ambiente e de revisões |

## O que ler
Leia só a camada da tarefa.
| Quando | Arquivos |
| --- | --- |
| Sempre | este arquivo · `specs/01-clausulas.md` · `specs/backlog.md` (estado, pendências, dívidas) |
| Mexer em produto ou arquitetura | `ARCHITECTURE.md` · `specs/decisoes.md` (D-nnn) |
| Trabalhar numa feature | `specs/features/F<n>-*.md` · `specs/dados.md` · `specs/llm-contrato.md` · `specs/ux-melhorias.md` (U-nnn) |
| Planejar | `specs/roadmap.md` · `CHANGELOG.md` |
| Ambiente e armadilhas do Windows | `memory/maquina.md` |
| Revisar código | `memory/revisao-codigo.md` (achados já decididos: não reapontar sem fato novo) |
| Escrever documentação | `specs/README.md` (ciclo de vida da spec, modelos, regras de escrita) |

Os comentários do código citam os documentos por ID: `CPn` (cláusula), `D-nnn` (decisão), `U-nnn` (melhoria de UX) e
`F<n>` (feature). Os IDs `A-nn`, `R-nn`, `M-nn` e `PF-nn` são achados de revisão e itens de specs antigas: a regra
que ficou está na spec da feature; o detalhe, no histórico do git.

## Fluxo de uma entrega
1. **Spec.** Rascunhe em `specs/features/` pelo modelo de `specs/README.md`.
   Status: `rascunho` → `em revisão` → `aprovada` → `implementada`.
2. **Revisão de arquitetura.** Confira a spec contra o código real: lacunas, decisões de desenho com recomendação,
   riscos com mitigação. Decisão técnica entra com um padrão; só decisão de produto vira pergunta ao dono, numerada,
   com "incluir? sim/não" e o padrão assumido.
3. **Aprovação.** Só o dono aprova, na spec ou no chat (registre a data). Desvio ou descoberta volta para a spec.
4. **Implementação.** Em lotes pequenos, um commit por lote, com testes, `ruff` e `mypy` limpos. Rode a cada passo só
   os testes do que mudou e os vizinhos; a suíte inteira, uma vez, no fechamento.
5. **Revisão do diff e fechamento.** Uma revisão independente lê só o diff (bugs, corridas, cláusulas). Depois:
   condense a spec para a forma de implementada, atualize `specs/backlog.md` (o único lugar de estado),
   `specs/decisoes.md`, `CHANGELOG.md` e a versão, e commite.

Defeito achado ao verificar uma entrega aprovada vira correção pequena, com teste e commit; melhoria de UX vira
pergunta ao dono.

## Convenções de código
- Documentos, commits e interface em PT-BR. Identificadores em inglês; comentários em PT-BR.
- `ruff` com linha de 140 colunas; `mypy` nos pacotes `listener` e `tests`.
- Arquivo com até 400 linhas (teste, 500) e função com até 50: divida ao mexer num que já passou.
- Erro tratado de forma explícita; nenhuma exceção engolida; SQL sempre parametrizado.
- A interface nunca importa `torch`, modelos nem SDK de LLM no nível do módulo; o trabalho pesado roda em processos
  próprios (`ARCHITECTURE.md`).
- Teste novo leva o nome do que prova. Mudou o texto de um prompt, suba a versão dele.

## Como trabalhar com o dono
- Conversa em PT-BR, informal e direta: a conclusão primeiro; discorde quando for o caso.
- Separe o que é [fato] (com fonte ou teste), [exp] (experiência) e [chute] (a medir).
- Melhoria de UX é sempre proposta como pergunta com padrão (CP12); ele responde em bloco.
- Ele delega decisões com padrão seguro: decida, registre em `specs/decisoes.md` e siga. Ação irreversível (apagar
  dado ou arquivo, push, gerar o executável) pede o OK dele antes.
- No Windows, rode sozinho o que não depende dele (testes, sondas, medições); deixe para o fim o que depende
  (chaves, reunião real, uso da interface) e anote em `specs/backlog.md`.
- Nada de lixo: protótipo sem valor e documento superado são apagados, não arquivados. O git guarda o histórico.
