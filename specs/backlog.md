# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Estado em 24/10/2026
- Código: F1, F1.1, F1.2 e **F2 (L1 a L6) implementadas**: 148 testes determinísticos em ~4,8 s no WSL (sem modelos, com
  fakes de transcritor, diarizador e embedder), ruff e mypy limpos (Linux e `--platform win32`), CI em três sistemas ×
  Python 3.12 e 3.13. Esquema v5. F2 passou por revisão de arquitetura antes e por revisão adversarial do diff depois
  (27 achados corrigidos, segunda passada fechou 24, os 3 restantes corrigidos). Os adaptadores reais do Whisper e do
  pyannote (L5) foram escritos às cegas: só rodam com o token (B4) no Windows.
- Git: commits de 24/10 a 25/10 (F1, F1.1, CP16, CP17, F1.2, fluxo D-040, F2 em onze commits); branch `main`; sem remoto.
  Commit a cada grande alteração (CP16).
- Specs: F0 aprovada e medida (falta pyannote, B4) · F1 implementada, G1 pendente · F1.1, F1.2 e F2 implementadas (G2
  pendente) · F3 a F6 rascunho inicial; a F3 é a próxima a passar pelo fluxo D-040 (arquiteto revisa a spec) · F7 futuro.

## Como retomar (5 minutos)
1. T0: `AGENTS.md`, `specs/01-clausulas.md`, este arquivo. Depois só a camada da tarefa.
2. Testes: `python -m pytest -q` e `ruff check .` (qualquer sistema). No Windows, venv 3.12 em
   `C:\Users\<usuário>\dev\listener-meet\.venv` e modelos em `C:\Users\<usuário>\listener-meet\models\`.
3. Antes de código: a spec da entrega em `specs/features/` está `aprovada`? (CP13)

## Bloqueios que só o dono destrava
| # | O quê | Destrava |
| --- | --- | --- |
| B1 | no Windows: `pip install -e .[dev,win32]`, `python -m listener --selftest`, depois `python -m listener`: assistente (com a página de log), gravação, fila e revisão; dizer o que está estranho | verifica F1.1, F1.2 e a parte da F2 sem modelos; a interface nunca foi vista num monitor |
| B2 | gate G1: 3 reuniões reais com headset em 2 plataformas; `python -m listener --check <pasta do banco>` (já imprime deriva e deslocamento L/R) | fecha F1 e F1.1; dá áudio real para o G2 |
| B4 | token do Hugging Face colado nas Configurações (provedor `huggingface`), com os termos do community-1 e do wespeaker aceitos; `pip install -e .[pipeline]` no venv do Windows | L5 da F2 (Whisper e pyannote reais), medição do pyannote, sonda Q8 e o G2 |
| B5 | gravação real de ~1 h com headset (ou as do G1) | medições reais de Whisper e pyannote |

Fechados: B3 (F2 aprovada, D-036) · B6 (primeiro commit) · B7 (MIT, D-034) · B8 (F1.1 aprovada, D-035) · B9 (compactação, D-031) ·
B10 (F1.2 aprovada em bloco, D-039) · B11 (F2 reaprovada, D-041).

## Ordem sugerida das próximas sessões
1. Dono roda o `--selftest` e o app no Windows (B1); LLM corrige o que aparecer (correção pequena, sem agente).
2. Dono grava as três reuniões (B2); LLM fecha o G1 em `decisoes.md` com deriva, deslocamento L/R e tamanho do banco.
3. Dono instala o extra `pipeline` e cola o token (B4); o primeiro job real verifica o L5, mede RTF e RSS por etapa e a
   sonda Q8; G2 nas gravações do G1; números em `decisoes.md`.
4. F3 pelo fluxo D-040: LLM rascunha a spec, arquiteto revisa, dono aprova, especialista implementa, revisor lê o diff.

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
