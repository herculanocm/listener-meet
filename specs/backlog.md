# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Onde paramos (25/10/2026, fim da sessão; tudo commitado, `main` à frente do `origin/main`: o push é do dono)
- **F3 fechada do lado da LLM:** L0 a L6 implementados (`0cea46c` a `998604f`) e as 14 correções da revisão adversarial
  aplicadas (F3.1: `18fa643`, `d3135e6`, mais `ec4890a` com a passada do revisor sobre o diff das correções). Desvios e
  decisões técnicas na seção Implementação de `features/F3.1-correcoes-revisao.md`.
- **F4 (ao vivo) implementada em 25/10** (D-044): L0 a L6 em sete commits (`b5cdab7` a `64ad255`), ajustes e as
  correções das duas revisões; desvios e o que só o Windows verifica na seção Implementação de `features/F4-ao-vivo.md`.
- **F5 (busca, histórico e chat global) em revisão em 25/10:** spec reescrita e revisada pelo arquiteto contra o código
  (`features/F5-rag-historico.md`: índice em dois tempos, job `index` na fila leve, FTS5 obrigatório, vetores sem
  sqlite-vec, v8); **próximo passo: o dono responde Q1 a Q11 e U-024 a U-029 (B16)**; só então o código (CP13), por
  lotes L0 a L5 (mais a F5.1 se a Q10 for sim). Correção da F4 no mesmo dia: mypy limpo de novo (`3feff42`).
- **Windows pendente por decisão do dono (D-046):** B1, B2, B4, B13, B15 e os gates G1 a G4 esperam a sessão no
  Windows, na ordem de "Verificar no Windows" (abaixo). Toda sessão começa verificando o ambiente (D-045, `AGENTS.md`).

## Estado em 25/10/2026
- Código: F1, F1.1, F1.2, F2, F3, F3.1 e **F4 implementadas**: 270 testes mais 5 pulados no Linux de desenvolvimento
  (274 com `LISTENER_SDK_TESTS=1`, que liga os de transporte simulado dos SDKs e o do truststore com clientes reais),
  ruff e mypy limpos (Linux e `--platform win32`), CI em três sistemas × Python 3.12 e 3.13 mais uma célula com o extra
  `llm`. Esquema v7. Nada do app importa torch, ML nem os SDKs de LLM (teste estático). Whisper, pyannote (F2 L5), os
  três adaptadores de LLM e o processo ao vivo real foram escritos às cegas, contra transporte simulado ou num venv à
  parte sem torch: só rodam de verdade no Windows com o token do Hugging Face (B4) e a chave do Gemini (B13).
- Git: commits de 24/10 a 25/10 (F1, F1.1, CP16, CP17, F1.2, D-040, F2 em onze, F3 em oito, F3.1 em três, F4 em lotes,
  ajustes e revisões); branch `main`; remoto `origin` público no GitHub (o dono faz o push; em 25/10 estava atrás do
  `main` local); sem stash. Commit a cada grande alteração (CP16).
- Specs: F0 aprovada e medida (falta pyannote, B4) · F1 implementada, G1 pendente · F1.1, F1.2 e F2 implementadas (G2
  pendente) · **F3 e F3.1 implementadas** (D-043; contrato em `specs/llm-contrato.md`) · G3 depende da chave do Gemini no
  Windows · **F4 implementada** (D-044; L0 e G4 dependem do Windows) · **F5 em revisão** (B16) · F6 rascunho inicial ·
  F7 futuro.

## Como retomar (5 minutos)
1. T0: `AGENTS.md` (primeiro: em que ambiente a sessão está), `specs/01-clausulas.md`, este arquivo. Depois só a camada
   da tarefa.
2. Testes: `python -m pytest -q` e `ruff check .` (qualquer sistema). No Windows nativo, antes: "Verificar no Windows".
3. Antes de código: a spec da entrega em `specs/features/` está `aprovada`? (CP13)

## Bloqueios que só o dono destrava
| # | O quê | Destrava |
| --- | --- | --- |
| B1 | no Windows: `pip install -e .[dev,win32,llm]`, `python -m listener --selftest`, depois `python -m listener`: assistente (com a página de log), gravação, fila, revisão e Configurações › LLM; dizer o que está estranho | verifica F1.1, F1.2, a parte da F2 sem modelos e a F3 sem chave; a interface nunca foi vista num monitor |
| B2 | gate G1: 3 reuniões reais com headset em 2 plataformas; `python -m listener --check <pasta do banco>` (já imprime deriva e deslocamento L/R) | fecha F1 e F1.1; dá áudio real para o G2 |
| B4 | token do Hugging Face colado nas Configurações (provedor `huggingface`), com os termos do community-1 e do wespeaker aceitos; `pip install -e .[pipeline]` no venv do Windows | L5 da F2 (Whisper e pyannote reais), medição do pyannote, sonda Q8 e o G2 |
| B5 | gravação real de ~1 h com headset (ou as do G1) | medições reais de Whisper e pyannote |
| B13 | chave do Gemini (plano pago) colada em Configurações › LLM › Gemini e "testar conexão" ok | adaptadores reais, canário dos esquemas, latências e preços, G3 |
| B16 | responder Q1 a Q11 e U-024 a U-029 de `features/F5-rag-historico.md` ("Ok para tudo" = os padrões) | libera o código da F5 (CP13) |
| B15 | no Windows: `python scripts/live_probe.py all <gravação real>` (L0 da F4; com `HF_TOKEN` para o torch) e colar as linhas JSON; depois do B1, "Preparar modelos do ao vivo" nas Configurações e uma gravação com a legenda ligada | números do Whisper, do VAD e do torch no Windows; o filho real, o EcoQoS e a ordem de carga torch/CTranslate2; abre o G4 |

Fechados: B3 (F2 aprovada, D-036) · B6 (primeiro commit) · B7 (MIT, D-034) · B8 (F1.1 aprovada, D-035) · B9 (compactação, D-031) ·
B10 (F1.2 aprovada em bloco, D-039) · B11 (F2 reaprovada, D-041) · B12 (F3 aprovada, D-043) · B14 (F4 aprovada, D-044).

## Verificar no Windows (rodar na ordem quando a sessão estiver no Windows nativo)
Tudo que foi feito no Linux ou no WSL só vale depois daqui (CP15). Item concluído ganha a data; números em `decisoes.md`
(Medições) e `memory/maquina.md`; defeito vira correção pequena (sem agente) ou item novo aqui.
1. Checkout no último `main` (o dono faz push de onde desenvolveu e pull aqui); venv 3.12 em
   `C:\Users\<usuário>\dev\listener-meet\.venv`; `pip install -e .[dev,win32,llm]` (mais `[pipeline]` nos itens 5 e 7).
2. `python -m pytest -q`: aqui rodam também os testes só do Windows (`spawn_child` abaixo do normal, EcoQoS).
3. `python -m listener --selftest`, depois `python -m listener`: assistente, gravação curta, fila, revisão e
   Configurações (B1); olhar DPI, bandeja do Windows 11 e tempo de abertura; a LLM corrige o que aparecer.
4. G1 (B2): três reuniões reais com headset em duas plataformas; `python -m listener --check <pasta do banco>` (deriva,
   deslocamento L/R e tamanho do banco em `decisoes.md`).
5. F2 real (B4): token do Hugging Face; o primeiro job mede RTF e RSS por etapa e a sonda Q8; "OMP: Error #15" pede o
   torch antes do CTranslate2 (como no ao vivo); G2 nas gravações do G1.
6. F3 real (B13): chave do Gemini, "testar conexão", primeiro job de LLM (canário, latência, preço contra a fatura); G3.
7. F4 (B15): `python scripts/live_probe.py all <gravação real>` (com `HF_TOKEN`); a regra da janela muda se o Windows
   divergir do Linux; "Preparar modelos do ao vivo"; gravação com a legenda: atraso, CPU (o processo ao vivo sem "modo
   de eficiência" no Gerenciador de Tarefas) e nomes; G4 em três reuniões pelo `--check`.

## Dívidas técnicas
- Saúde dos canais começa "ok" por até 10 s sem sinal; decidir se R avisa antes quando nunca recebeu sinal.
- Deriva de relógio: só instrumentada (A7); corrigir em spec própria se o G1 mostrar mais de 100 ms por hora (P22).
- Textos da interface fixos em pt-BR; prever i18n (`tr()` do Qt ou gettext) antes da F6 (CP15).
- Pasta de modelos ainda não está nas Configurações (entra na F2).
- FTS5: confirmado no SQLite do Python 3.12 do WSL (F1.2 M-31); falta confirmar no Windows antes da F5.
- Testes de interface só cobrem abrir e fechar as telas (pytest-qt offscreen); interação real fica para a F6.
- O eco do warm-up é medido só no "Testar 3 s", não de forma contínua; o `--check` dá o número preciso.
- Suíte acima da meta de 8 s: ~17,5 s no Linux de desenvolvimento depois da F4 (270 testes; 12,8 a 13,2 s antes, com
  208; o Xeon de 2,1 GHz pesa). Medir no Windows; se seguir acima, separar os testes de subprocesso e os de interface em
  jobs próprios e ver o custo do `gc.collect()` por teste de interface.
- F2 no Windows (B1/B4): o worker pesado carrega o CTranslate2 (Whisper) antes do torch (pyannote), a ordem inversa à do
  F0; se aparecer "OMP: Error #15" (dois `libiomp5md.dll`), importar o torch antes, como o processo ao vivo já faz.
- F4: o eco do ao vivo usa o limiar do aviso (`capture.echo_corr_min`, 0,25); calibrar no G4 sem headset.
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
precisão dos perfis, desvio do clique e legibilidade (G2) · F4 no Windows (B15): Whisper por janela (`small` e
`base`), VAD incremental, `import torch` e RSS, embedding por janela; G4: atraso médio e p95, CPU, acerto do nome.
