# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Onde paramos (25/10/2026, troca de conta do dono)
- **F3 implementada em 8 commits** (`0cea46c` a `998604f`) e revisada pelo revisor adversarial: veredito "fechar com correções
  pequenas", 14 itens (2 altas, 5 médias, 7 baixas), transcritos em `features/F3.1-correcoes-revisao.md` porque o relatório
  original ficou em pasta temporária. **O agente que ia corrigir foi interrompido antes de concluir**: as edições parciais
  (R3-01, R3-05, R3-06 em `ui/chat.py`, `controller.py`, `llm_tabs.py`, `review.py`, `transcript.py`) estão em
  `git stash list` ("F3.1 parcial do agente"); a árvore em `main` está limpa e verde (193 testes, 3 pulados sem chave).
- **Próximo passo exato:** aplicar a F3.1 (agente especialista com o briefing da própria spec, ou direto: são correções
  pequenas), `git stash drop` ou reaproveitar o stash depois de revisar, passada curta do revisor no diff, verificação
  independente (`pytest`, `ruff`, `mypy` e `mypy --platform win32`, regra de importações), atualizar a seção Implementação da
  F3, este backlog, `memory/` e commitar (CP16). Depois seguem B1, B2, B4 e o G2 e G3 no Windows, e a F4 pelo fluxo D-040.
- Os relatórios dos agentes (arquiteto, dev, revisões) vivem fora do repositório e não sobrevivem à troca de sessão; tudo
  o que importa deles já está nas specs (F1.2, F2, F3, F3.1, `llm-contrato.md`) e em `decisoes.md`.

## Estado em 25/10/2026
- Código: F1, F1.1, F1.2, F2 e **F3 (L0 a L6) implementadas** (F3.1 pendente): 193 testes em ~7 s no WSL (3 pulados sem
  chave; com `LISTENER_SDK_TESTS=1` rodam os de transporte simulado dos SDKs), ruff e mypy limpos (Linux e `--platform
  win32`), CI em três sistemas × Python 3.12 e 3.13 mais uma célula com o extra `llm`. Esquema v6. Nada do app importa
  torch nem os SDKs de LLM (teste estático). Whisper, pyannote (F2 L5) e os três adaptadores de LLM foram escritos às
  cegas ou contra transporte simulado: só rodam no Windows com o token do Hugging Face (B4) e a chave do Gemini (G3).
- Git: commits de 24/10 a 25/10 (F1, F1.1, CP16, CP17, F1.2, D-040, F2 em onze commits, F3 em oito); branch `main`; sem
  remoto; um stash documentado (F3.1 parcial). Commit a cada grande alteração (CP16).
- Specs: F0 aprovada e medida (falta pyannote, B4) · F1 implementada, G1 pendente · F1.1, F1.2 e F2 implementadas (G2
  pendente) · **F3 implementada** (D-043; contrato em `specs/llm-contrato.md`), **F3.1 aprovada e pendente** · G3 depende
  da chave do Gemini no Windows · F4 a F6 rascunho inicial · F7 futuro.

## Como retomar (5 minutos)
1. T0: `AGENTS.md`, `specs/01-clausulas.md`, este arquivo. Depois só a camada da tarefa.
2. Testes: `python -m pytest -q` e `ruff check .` (qualquer sistema). No Windows, venv 3.12 em
   `C:\Users\<usuário>\dev\listener-meet\.venv` e modelos em `C:\Users\<usuário>\listener-meet\models\`.
3. Antes de código: a spec da entrega em `specs/features/` está `aprovada`? (CP13)

## Bloqueios que só o dono destrava
| # | O quê | Destrava |
| --- | --- | --- |
| B1 | no Windows: `pip install -e .[dev,win32,llm]`, `python -m listener --selftest`, depois `python -m listener`: assistente (com a página de log), gravação, fila, revisão e Configurações › LLM; dizer o que está estranho | verifica F1.1, F1.2, a parte da F2 sem modelos e a F3 sem chave; a interface nunca foi vista num monitor |
| B2 | gate G1: 3 reuniões reais com headset em 2 plataformas; `python -m listener --check <pasta do banco>` (já imprime deriva e deslocamento L/R) | fecha F1 e F1.1; dá áudio real para o G2 |
| B4 | token do Hugging Face colado nas Configurações (provedor `huggingface`), com os termos do community-1 e do wespeaker aceitos; `pip install -e .[pipeline]` no venv do Windows | L5 da F2 (Whisper e pyannote reais), medição do pyannote, sonda Q8 e o G2 |
| B5 | gravação real de ~1 h com headset (ou as do G1) | medições reais de Whisper e pyannote |
| B13 | chave do Gemini (plano pago) colada em Configurações › LLM › Gemini e "testar conexão" ok | adaptadores reais, canário dos esquemas, latências e preços, G3 |

Fechados: B3 (F2 aprovada, D-036) · B6 (primeiro commit) · B7 (MIT, D-034) · B8 (F1.1 aprovada, D-035) · B9 (compactação, D-031) ·
B10 (F1.2 aprovada em bloco, D-039) · B11 (F2 reaprovada, D-041) · B12 (F3 aprovada, D-043).

## Ordem sugerida das próximas sessões
1. Dono roda o `--selftest` e o app no Windows (B1); LLM corrige o que aparecer (correção pequena, sem agente).
2. Dono grava as três reuniões (B2); LLM fecha o G1 em `decisoes.md` com deriva, deslocamento L/R e tamanho do banco.
3. Dono instala o extra `pipeline` e cola o token (B4); o primeiro job real verifica o L5, mede RTF e RSS por etapa e a
   sonda Q8; G2 nas gravações do G1; números em `decisoes.md`.
4. LLM aplica a F3.1 (14 correções), revisor confere o diff, LLM verifica e fecha a F3; dono cola a chave (B13) e o
   primeiro job de LLM real valida os adaptadores; G3 nas três reuniões; depois a F4 (ao vivo) pelo fluxo D-040.

## Dívidas técnicas
- Saúde dos canais começa "ok" por até 10 s sem sinal; decidir se R avisa antes quando nunca recebeu sinal.
- Deriva de relógio: só instrumentada (A7); corrigir em spec própria se o G1 mostrar mais de 100 ms por hora (P22).
- Textos da interface fixos em pt-BR; prever i18n (`tr()` do Qt ou gettext) antes da F6 (CP15).
- Pasta de modelos ainda não está nas Configurações (entra na F2).
- FTS5: confirmado no SQLite do Python 3.12 do WSL (F1.2 M-31); falta confirmar no Windows antes da F5.
- Testes de interface só cobrem abrir e fechar as telas (pytest-qt offscreen); interação real fica para a F6.
- O eco do warm-up é medido só no "Testar 3 s", não de forma contínua; o `--check` dá o número preciso.
- F2: a suíte encosta no teto de 5 s (4,3 a 5,1 s) por causa da coleta (importa scipy e PySide6 e lança dois subprocessos
  de worker); se passar com a F3, separar os testes de subprocesso num job próprio.
- F2: invalidar um vetor de confirmação tira-o do centroide, mas a amostra já entrou na média e no desvio do limiar
  (Welford não é reversível sem guardar as amostras); aceito até o G2 mostrar impacto.
- F2: durante um reprocessamento a revisão mostra o job concluído; a abertura antecipada (P1) vale para o primeiro
  processamento. `embed_range` (trecho livre) e separar perfis ficam para a F6, como a spec prevê.
- Antes de publicar o repositório: conferir que nada pessoal sobrou em `memory/` e `decisoes.md` (caminhos usam `<usuário>`; CP15).
- CP17: subir o piso e o teto do `requires-python` assim que WhisperX e torch aceitarem Python 3.14 (hoje `<3.14`; 3.13 já
  está no CI); conferir a cada atualização da stack.
- Sem lockfile (`uv` não instalado nesta máquina); gerar `uv lock` quando houver `uv`, para reprodutibilidade da stack de ML.

## Medições a registrar em `decisoes.md`
pyannote community-1 por hora em CPU (B4) · tamanho do banco por hora de reunião (G1) · tempo de `list_playing_apps`
(B1, no log) · deriva por canal e deslocamento L/R (G1, `--check`) · F2 no primeiro job real: RTF por etapa e modelo,
RSS por etapa, sonda Q8 (`speaker_embeddings` e id do modelo), similaridade intra e inter-pessoa por plataforma ·
precisão dos perfis, desvio do clique e legibilidade (G2).
