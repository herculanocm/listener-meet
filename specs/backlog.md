# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Onde paramos (26/10/2026, Windows nativo; tudo commitado; push do dono)
- **Próximo passo, comece aqui:** o dono testa pelo roteiro `specs/roteiro-teste-windows.md` (partes 1 a 9: primeiro
  uso, chaves, gravação curta de ponta a ponta, reunião aberta, busca com o Gemini, busca local, ao vivo, reuniões reais
  e situações de borda) e devolve o que cada parte pede; a LLM registra os números em `decisoes.md`, marca aqui e
  corrige o que aparecer (pedido do dono, 25/10: a LLM roda sozinha o que não depende dele e o acompanha no resto). Sem
  o dono, o próximo trabalho depende da pergunta 7 abaixo. **Em 26/10 o dono fez a Parte 1 e gravou a Parte 3 sem as
  chaves (D-053):** segue pela Parte 2, agora no grupo "Chaves de acesso", e refaz a Parte 3 numa chamada de verdade. A
  pasta do banco daquele teste (`C:\dev\base_listener_meet`) apareceu vazia às 14:21 de 26/10, fora da Lixeira e sem
  nenhum processo da LLM nela (os `rmtree` do código e dos testes só alcançam pastas do `mkdtemp`): perguntar ao dono.
- **Concluído em 25 e 26/10, no Windows** (o detalhe nas seções Implementação das specs e em `decisoes.md`):
  - [x] F5: R7 e R8 e o fechamento (**F5 implementada**: `af2ea3d`, `6e0f235`, `b21d675`).
  - [x] "Verificar no Windows" sem o dono: itens 1 e 2 inteiros e a parte sem o dono dos itens 3, 5, 7, 8 e 9, com seis
        defeitos corrigidos (ctypes duas vezes, corrida do worker leve, isolamento dos testes, tradução do Qt, largura
        das Configurações, setas dos menus).
  - [x] U-030 a U-033 (D-049).
  - [x] D-050: as sondas do Windows (`scripts/win_probe.py`); a suíte em rápida e lenta (`--tudo` antes de commitar; o
        CI roda tudo); o lock `requirements/windows.txt`; o `[pipeline]` no `.venv` e o large-v3-turbo na pasta de
        modelos do app; o canal R sem aviso antecipado (rever depois do G1); o repositório público como está.
  - [x] **F5.1 implementada**: L0 (`faa967b`, D-051), spec e revisão de arquitetura (`74b7ec8`, `59c550e`), extras e
        lock (`d235950`), L7 (`82772d1`, R9 `ccb0719`) e L6 (`8f28ad8`, R10 `910fe4b`) em paralelo, cada um revisado por
        um agente novo, escapes e a regra do código-fonte (`5f1776a`) e o fechamento (`6fd6db8`). Conferido sem o dono:
        a suíte, as telas a 150 %, o app real, o download do e5 interrompido e retomado, o .docx no Word e o aviso do
        U-030 com ele aberto.
  - [x] Limpeza (D-052): os worktrees e os branches da F5.1; no cache do Hugging Face, o `small` de um teste (464 MB) e
        as sobras do `base` e do Xet; o fp32 do L0 (448 MB); os temporários de testes e sondas no `%TEMP%` (82 pastas,
        com o `pytest-of-…` de 451 MB) e o scratchpad da sessão (440 MB): ~1,8 GB no total. Ficam o venv do F0 e as duas
        entradas vazias de 24/10 no cache do Hugging Face: saem juntos depois da Parte 3 do roteiro (D-050).
  - [x] D-053, do primeiro teste do dono: U-037 ("Chaves de acesso" em primeiro nas Configurações e o aviso no
        warm-up) e o log da gravação sem o título da janela da fonte (nome de terceiro, LGPD).
  - [x] D-054: U-038, o assistente pede a chave da LLM escolhida (Gemini, GPT ou Claude) e o token do Hugging Face,
        logo depois da pasta do banco (opcionais; a escolhida vira a ativa).
  - [x] D-055, do segundo teste do dono: o canal R do Teams (a captura soma a raiz do app com as sessões mais fundas;
        U-040), a revisão sem abrir sozinha quando não há voz a confirmar (U-039) e o Gemini 3 nos padrões (o 2.5 deu
        404 para conta nova). Falta a prova com uma chamada de verdade no Teams (roteiro, parte 3).
  - [x] D-056, do terceiro teste do dono: as janelas pretas eram os workers (o lançador do venv com `DETACHED_PROCESS`
        dava ao interpretador um console visível no Windows Terminal); o worker nasce sem janela e continua vivo depois
        do app. Também: o custo do Gemini 3 pela família de preço (saía nulo) e o nome da pessoa e o texto falado fora
        do log.
- **Perguntas abertas ao dono** (padrão entre parênteses; só vale com a resposta dele):
  1. P23, RAM da busca local: o tokenizer do e5 custa ~262 MB e o app vai a ~620 a 670 MB com o Histórico aberto
     (aceitar e medir na Parte 6 do roteiro; a alternativa é o `sentencepiece`, dependência nova).
  2. U-034: a linha do tempo sem falas não repete o aviso no rodapé (sim).
  3. U-035: "Voz 1", "Voz 2"… no lugar de "Voz SPEAKER_03" (sim).
  4. U-036: o estado do índice com o nome legível do modelo (sim).
  5. `AGENTS.md`: "Testes: `python -m pytest -q` (a rápida); antes de commitar, `--tudo` (D-050)" (sim).
  6. `pytest-xdist`, só de desenvolvimento, para a meta de 8 s (sim, num lote pequeno).
  7. Próximo passo sem o dono: detalhar a spec da F6 e passá-la pela revisão de arquitetura para ele aprovar (sim).
  8. Sem o token do Hugging Face, transcrever mesmo assim, sem saber quem fala no R (o L é você), em vez de o
     processamento não sair (sim; muda o comportamento da F2, vai para uma spec).
  9. As janelas pretas eram os workers (D-056), e fechar uma delas no X mata o worker sem rastro (medido). Foi isso nas
     quedas do job da reunião 2 (26/10, 15:35 e 15:36) e na do terceiro teste (17:27:55)? E o app que fechou sem o "app
     encerrado": foi o terminal dele que se fechou? (padrão: sim aos dois; se não, investigar o antivírus/EDR).
  10. Além das janelas pretas (D-056) e da revisão (U-039), alguma outra janela ainda sobe por cima? (padrão: nenhuma).
- **F3 e F4 implementadas** (D-043, D-044): desvios e o que só o Windows verifica nas seções Implementação de
  `features/F3.1-correcoes-revisao.md` e `features/F4-ao-vivo.md`. Toda sessão começa verificando o ambiente (D-045,
  `AGENTS.md`).

## Estado em 26/10/2026
- Código: F1, F1.1, F1.2, F2, F3, F3.1, F4, F5 e **F5.1 implementadas**: 460 testes mais 7 pulados no Windows com
  `--tudo` (55 a 87 s; a rápida, ~345 mais 6, ~25 s; 348 mais 5 no Linux de desenvolvimento no L5 da F5, ~22 s);
  `LISTENER_SDK_TESTS=1` liga os de transporte simulado dos SDKs e o do truststore com clientes reais; ruff e mypy
  limpos (Linux e `--platform win32`), CI em três sistemas × Python 3.12 e 3.13 mais uma célula com o extra `llm`.
  Esquema v8. Nada do app importa torch, ML, os SDKs de LLM, onnxruntime, tokenizers nem docx no nível do módulo (teste
  estático).
  Whisper, pyannote (F2 L5), os três adaptadores de LLM e o processo ao vivo real foram escritos às cegas, contra
  transporte simulado ou num venv à parte sem torch: só rodam de verdade no Windows com o token do Hugging Face (B4) e a
  chave do Gemini (B13).
- Git: commits de 24/10 a 26/10 (F1, F1.1, CP16, CP17, F1.2, D-040, F2 em onze, F3 em oito, F3.1 em três, F4 e F5 em
  lotes, F5.1 em lotes paralelos, ajustes, revisões e a verificação no Windows); o branch `main` (mais os dois
  `worktree-agent-*` da F5.1, a apagar com o OK do dono); remoto `origin` público no GitHub (o dono faz
  o push; em 26/10 o `main` do checkout do Windows, `C:\dev\listener-meet`, estava à frente do `origin/main`
  (`2e81120`), de `2e6e098` em diante: `git log --oneline origin/main..main` lista o que falta subir); sem stash. Commit a cada grande alteração (CP16).
- Specs: F0 aprovada e medida (falta pyannote, B4) · F1 implementada, G1 pendente · F1.1, F1.2 e F2 implementadas (G2
  pendente) · **F3 e F3.1 implementadas** (D-043; contrato em `specs/llm-contrato.md`) · G3 depende da chave do Gemini no
  Windows · **F4 implementada** (D-044; L0 e G4 dependem do Windows) · **F5 implementada** (D-047; o item 8 e o G5
  dependem do Windows com o dono) · **F5.1 implementada** (`features/F5.1-busca-local-linha-do-tempo.md`; o item 9 e o
  G5 com o embedder local dependem do dono) · F6 rascunho inicial · F7 futuro.

## Como retomar (5 minutos)
1. T0: `AGENTS.md` (primeiro: em que ambiente a sessão está), `specs/01-clausulas.md`, este arquivo. Depois só a camada
   da tarefa.
2. Testes (D-050): `python -m pytest -q` roda a parte rápida; **antes de commitar, a inteira: `python -m pytest -q
   --tudo`** (o CI sempre roda tudo); mais `ruff check .` e `mypy` (também com `--platform linux`). No Windows, pelo venv
   do checkout: `.venv\Scripts\python.exe -m pytest -q --tudo`, `.venv\Scripts\ruff.exe check .`,
   `.venv\Scripts\mypy.exe`. No Windows nativo, antes: "Verificar no Windows".
3. Antes de código: a spec da entrega em `specs/features/` está `aprovada`? (CP13)

## Bloqueios que só o dono destrava
| # | O quê | Destrava |
| --- | --- | --- |
| B1 | no Windows (venv, selftest e as telas a 150 % feitos pela LLM em 25/10): `python -m listener` no perfil real: assistente (com a página de log), gravação, fila, revisão e Configurações › LLM; dizer o que está estranho | verifica F1.1, F1.2, a parte da F2 sem modelos e a F3 sem chave pelo uso de verdade |
| B2 | gate G1: 3 reuniões reais com headset em 2 plataformas; `python -m listener --check <pasta do banco>` (já imprime deriva e deslocamento L/R) | fecha F1 e F1.1; dá áudio real para o G2 |
| B4 | token do Hugging Face colado nas Configurações (provedor `huggingface`), com os termos do community-1 e do wespeaker aceitos (o `[pipeline]` já está no `.venv`, D-050) | L5 da F2 (Whisper e pyannote reais), medição do pyannote, sonda Q8 e o G2 |
| B5 | gravação real de ~1 h com headset (ou as do G1) | medições reais de Whisper e pyannote |
| B13 | chave do Gemini (plano pago) colada em Configurações › LLM › Gemini e "testar conexão" ok | adaptadores reais, canário dos esquemas, latências e preços, G3 |
| B15 | no Windows (sem token, a LLM mediu Whisper, VAD e `import torch` em 25/10 e baixou `small` e `base` na pasta do app): `python scripts/live_probe.py all <gravação real>` com `HF_TOKEN` (o embedding do wespeaker) e uma gravação com a legenda ligada | o filho real, o EcoQoS, a ordem de carga torch/CTranslate2 e os nomes ao vivo; abre o G4 |

Fechados: B3 (F2 aprovada, D-036) · B6 (primeiro commit) · B7 (MIT, D-034) · B8 (F1.1 aprovada, D-035) · B9 (compactação,
D-031) · B10 (F1.2 aprovada em bloco, D-039) · B11 (F2 reaprovada, D-041) · B12 (F3 aprovada, D-043) · B14 (F4 aprovada,
D-044) · B16 (F5 aprovada, D-047). O passo a passo dos abertos: `specs/roteiro-teste-windows.md`.

## Verificar no Windows (rodar na ordem quando a sessão estiver no Windows nativo)
Tudo que foi feito no Linux ou no WSL só vale depois daqui (CP15). `[x]` = concluído (com a data); `[ ]` = falta a parte
do dono, cujo passo a passo está em `specs/roteiro-teste-windows.md` (a parte indicada). Números em `decisoes.md`
(Medições) e `memory/maquina.md`; defeito vira correção pequena (sem agente) ou item novo aqui.
1. [x] Checkout, venv 3.12 e instalação. **Concluído em 25/10:** checkout em `C:\dev\listener-meet`, `.venv` dentro
   dele (239 s com o cache do pip); desde 26/10 pelo lock `requirements/windows.txt`, com `[pipeline]`, `[local]` e
   `[docx]` (D-050).
2. [x] Suíte no Windows, com os testes só do Windows (`spawn_child` abaixo do normal, EcoQoS). **Concluído em 25/10**
   (349 e 4 pulados; ruff e mypy limpos; o pseudo-handle de `GetCurrentProcess` truncado no Windows de 64 bits
   corrigido); em 26/10, com a F5.1, 445 e 7 pulados com `--tudo`.
3. [ ] Uso real do app (B1). **Sem o dono, concluído em 25/10:** `--selftest` com código 0 (mic a 16 kHz pelo WASAPI,
   primeiro pacote em 382 ms; loopback a 48 kHz; desvio de +12 e −1 ms; sem buracos); `ui_smoke` com a plataforma real
   (atalhos, `list_playing_apps` em 36 a 66 ms, mudo, `keep_awake`); o app num perfil isolado (bandeja em 2,2 s a frio,
   142 MB, 0,23 % de um núcleo parado); três correções das telas a 150 % (`f6ea99e`). **Com o dono, 26/10:** a parte 1
   do roteiro (assistente, bandeja, atalhos) e uma gravação de 71 s sem as chaves (D-053). **Falta (dono):** roteiro,
   partes 2, 3 e 4.
4. [ ] G1 (B2): três reuniões reais com headset em duas plataformas e o `--check`. **Falta (dono):** roteiro, parte 8.
5. [ ] F2 real (B4): o primeiro job real (RTF e RAM por etapa, sonda Q8; "OMP: Error #15" pede o torch antes do
   CTranslate2) e o G2. **Sem o dono, concluído em 26/10:** o `[pipeline]` no `.venv` pelo lock (torch 2.8.0+cpu,
   faster-whisper 1.2.1, pyannote.audio 4.0.7) e o large-v3-turbo na pasta de modelos do app (carrega e transcreve).
   **Falta (dono):** roteiro, partes 2, 3 e 8; depois, apagar o venv do F0 (D-050).
6. [ ] F3 real (B13): chave do Gemini, "Testar conexão", o primeiro job de LLM (canário, latência, preço contra a
   fatura) e o G3. **Falta (dono):** roteiro, partes 2, 3, 5 e 8.
7. [ ] F4 (B15): o embedding do wespeaker no `live_probe` (com o token), a gravação com a legenda (atraso, CPU, EcoQoS e
   nomes) e o G4. **Sem o dono, concluído em 25/10:** `live_probe` sem token com o áudio TTS (a regra da janela fica,
   igual ao Linux); `small` e `base` na pasta de modelos do app. **Falta (dono):** roteiro, partes 7 e 8.
8. [ ] F5. **Sem o dono, concluído em 25 e 26/10:** `rag_probe fts` e `sig`; a v8 numa cópia de um banco de 24/10 (65
   ms, nada perdido) e o `--check` nela; o `.md` com o destino aberto (WinError 5, o anterior fica, com o aviso do
   U-030); o player no MME (0 buraco em 20 s com e sem carga, pausa em ~20 ms, primeiro som em ~0,5 s com o U-031); o
   atalho do Histórico; copiar e sair com o conteúdo na área de transferência. **Falta (dono):** roteiro, partes 4, 5,
   8 e 9.
9. [ ] F5.1. **Sem o dono, concluído em 26/10:** a suíte, as telas a 150 %, o app real (pronto em 1,43 s, 188,7 MB
   parado), o e5 de verdade no worker leve e no app, o download interrompido e retomado, o .docx de 1 h no Word 16 e o
   aviso do U-030 com ele aberto no Word. **Falta (dono):** roteiro, partes 4, 6 e 8; a metade Linux do L0 na próxima
   sessão no Linux.

## Dívidas técnicas
- Saúde dos canais começa "ok" por até 10 s sem sinal; R não avisa antes (D-050: silêncio no começo é normal); rever
  depois do G1.
- Deriva de relógio: só instrumentada (A7); corrigir em spec própria se o G1 mostrar mais de 100 ms por hora (P22).
- Textos da interface fixos em pt-BR; prever i18n (`tr()` do Qt ou gettext) antes da F6 (CP15).
- Pasta de modelos ainda não está nas Configurações (entra na F2).
- Testes de interface só cobrem abrir e fechar as telas (pytest-qt offscreen); interação real fica para a F6.
- O eco do warm-up é medido só no "Testar 3 s", não de forma contínua; o `--check` dá o número preciso.
- F5: `_fail` de um job cuja reunião foi apagada levanta IntegrityError e derruba o worker (F5A-17, para a F6, junto
  com apagar reunião); o filtro de pessoa varre `rag_chunks` (~35 ms a cada 20 mil trechos) e `vector_version` conta o
  índice a cada busca: medir com o banco real.
- Suíte acima da meta de 8 s. Dividida em 26/10 (D-050): os testes `lento` (telas por fixture, subprocesso, tempo
  real, contas pesadas; 104 com a F5.1) ficam fora do padrão local; neste notebook a rápida levava 34 a 40 s e a inteira
  52 a 58 s, e com a F5.1, à tarde, 25 s (342 testes) e 55 s (445) (no Linux de desenvolvimento a inteira era ~22 s).
  O resto é trabalho de lógica espalhado (o `test_store` sozinho dá 5 s; criar arquivo custa 1,7 ms e abrir um SQLite
  em WAL 7 ms aqui, então não é o EDR). A meta pede execução paralela (`pytest-xdist`, dependência nova: pergunta 6 ao
  dono; os subprocessos da coleta precisam nascer uma vez só).
- Testes e sondas deixam pastas `listener-*` no `%TEMP%` (perfis e bancos criados fora do `tmp_path` do pytest; 82 em
  três dias, apagadas em 26/10, D-052): passar os testes para o `tmp_path` e as sondas a apagar o que não imprimem.
- F2 no Windows (B1/B4): o worker pesado carrega o CTranslate2 (Whisper) antes do torch (pyannote), a ordem inversa à do
  F0; se aparecer "OMP: Error #15" (dois `libiomp5md.dll`), importar o torch antes, como o processo ao vivo já faz.
- F4: o eco do ao vivo usa o limiar do aviso (`capture.echo_corr_min`, 0,25); calibrar no G4 sem headset.
- F2: invalidar um vetor de confirmação tira-o do centroide, mas a amostra já entrou na média e no desvio do limiar
  (Welford não é reversível sem guardar as amostras); aceito até o G2 mostrar impacto.
- F2: durante um reprocessamento a revisão mostra o job concluído; a abertura antecipada (P1) vale para o primeiro
  processamento. `embed_range` (trecho livre) e separar perfis ficam para a F6, como a spec prevê.
- CP17: subir o piso e o teto do `requires-python` assim que WhisperX e torch aceitarem Python 3.14 (hoje `<3.14`; 3.13 já
  está no CI); conferir a cada atualização da stack.
- Lock só do Windows (`requirements/windows.txt`, 26/10, `uv pip compile` com as versões provadas aqui); o CI e o Linux
  seguem com o `pyproject.toml` sem trava. Um lock do Linux pede o índice CPU do torch (o do PyPI traz CUDA, CP14).
- D-055: (a) os preços da geração 3 do Gemini não estão nas sementes (a estimativa usa os do 2.5 [chute]: conferir com a
  fatura e cadastrar em Configurações › LLM › Gemini); (b) a captura do app pega as sessões que existem ao abrir: um
  processo novo do app que comece a tocar no meio da gravação, mais fundo que filho direto da raiz, fica de fora até a
  próxima abertura (reabrir a fonte no widget resolve); (c) o "Windows fatal exception: code 0x8001010d" no log é o
  faulthandler registrando uma exceção COM já tratada (o app segue): ruído, não queda.
- D-056: dois workers leves podem nascer no mesmo segundo (o pesado, ao terminar, e o polling do app lançam cada um o
  seu, e o intervalo de 60 s entre lançamentos vale por processo): o segundo não acha job e sai ocioso em 60 s.
  Inofensivo (o claim é atômico); custa um processo a mais. Resolver com uma marca de lançamento no banco, se valer.

## Medições a registrar em `decisoes.md`
pyannote community-1 por hora em CPU (B4) · tamanho do banco por hora de reunião (G1) · deriva por canal e
deslocamento L/R (G1, `--check`) · F2 no primeiro job real: RTF por etapa e modelo,
RSS por etapa, sonda Q8 (`speaker_embeddings` e id do modelo), similaridade intra e inter-pessoa por plataforma ·
precisão dos perfis, desvio do clique e legibilidade (G2) · F4 no Windows (B15): embedding por janela (o resto do
`live_probe` medido em 25/10); G4: atraso médio e p95, CPU, acerto do nome · F5 no Windows (item 8): `rag_probe gemini`
(por lote), varredura da primeira abertura, híbrida com o vetor da consulta, `underflowed` do player com o worker pesado
de verdade; G5: citações certas em 10 perguntas · F5.1 com o dono (item 9): o G5 com o embedder local e a RAM real
com o Histórico aberto (P23).
