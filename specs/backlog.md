# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Onde paramos (30/10/2026, Windows nativo; tudo commitado; push do dono)
- **F5.9 aprovada e em implementação** (30/10, `features/F5.9-canais-l-r-dispositivos.md`, a pedido do dono: revisão
  completa dos canais L e R, da captura ao player). Três defeitos (o R não percebe a própria morte; o canal que não
  abre no começo nunca é tentado de novo; os processos do app são fixados na abertura), três fragilidades e quatro
  melhorias, em quatro lotes. A revisão de arquitetura está aplicada (D-070); o dono aprovou pelo chat com as quatro
  perguntas no padrão (sim). **L1 implementado e revisado** (`65e790b`, `f66bf3f`, `dbe99c3`, `a96568b`; 1.147
  testes). O L2 está com o programador; depois o L3; o L4 por último. Dois itens que a revisão do L1 deixou, fora do
  lote: `tests/test_call_watch.py::test_failures_degrade_with_one_warning_and_recover_with_one_info` é instável na
  suíte inteira (3 falhas em 7, 9/9 isolado; tempo ou ordem) e o `pyproject` não liga o `C901` (o limite de
  complexidade 10 é só convenção).
- **F5.8 implementada** (30/10; o detalhe está no item do Concluído). Falta o dono gerar e usar o `.exe` numa
  reunião de verdade, pela Parte 19 do roteiro. A telemetria do pyannote foi desligada depois (`5b6496a`, pergunta 16).
- **F5.7 implementada** (29/10; o detalhe está no item do Concluído). Falta o dono verificar no Windows, pela Parte 17
  do roteiro, numa chamada de verdade.
- **Próximo passo do dono:** testar a troca de aparelho pela Parte 18 (D-067), o `.exe` pela Parte 19 (F5.8) e a
  F5.7 pela Parte 17 (reinicie o app
  antes), a F5.6 L2 pela Parte 16 (reinicie o app antes), a L1 pela Parte 15 e a L3 pela
  Parte 14, a F5.5 pela Parte 13 e a F5.4 pela Parte 12 do roteiro (claro, escuro, contraste alto, notebook, teclado), a F5.3 pela Parte 11 (idioma, fila,
  aviso, fone no meio da gravação) e a F5.2 pela Parte 10 (fone conectado com o app aberto, ficha da pessoa, a ata
  depois da identificação, log em DEBUG) e seguir as partes 1 a 9: primeiro
  uso, chaves, gravação curta de ponta a ponta, reunião aberta, busca com o Gemini, busca local, ao vivo, reuniões reais
  e situações de borda) e devolve o que cada parte pede; a LLM registra os números em `decisoes.md`, marca aqui e
  corrige o que aparecer (pedido do dono, 25/10: a LLM roda sozinha o que não depende dele e o acompanha no resto). Sem
  o dono, o próximo trabalho depende da pergunta 7 abaixo. **Em 26/10 o dono fez a Parte 1 e gravou a Parte 3 sem as
  chaves (D-053):** segue pela Parte 2, agora no grupo "Chaves de acesso", e refaz a Parte 3 numa chamada de verdade. A
  pasta do banco daquele teste (`C:\dev\base_listener_meet`) apareceu vazia às 14:21 de 26/10, fora da Lixeira e sem
  nenhum processo da LLM nela (os `rmtree` do código e dos testes só alcançam pastas do `mkdtemp`): perguntar ao dono.
- **Concluído de 25 a 29/10, no Windows** (o detalhe nas seções Implementação das specs e em `decisoes.md`):
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
  - [x] **F5.2 implementada** (27/10, `features/F5.2-pessoas-ata-dispositivos.md`, D-057): spec com a revisão de
        arquitetura (`379f6af`), L1 U-043 (`4bcc056` checkpoint do dono, `c969137`, `20cfe40`), L2 U-041 (`8b0834a`) e
        L3 U-042 (`5bc4505`) e a revisão do diff, R-01 a R-12 (`b54321a`). Falta o dono verificar no Windows (Parte
        10 do roteiro).
  - [x] D-058, do terceiro teste: o worker leve preso 52 min na verificação de certificado do Windows parava a fila; o
        vigia do laço faz o worker sair e o app o relança (`4bcc056`).
  - [x] D-059, a pedido do dono: logs para acompanhar e validar cada execução, com a convenção em `ARCHITECTURE.md`
        (`4d4102d`, `3f1cb65`); o terminal fechado com o app aberto passa a deixar a linha no log (conferido).
  - [x] **F5.3 implementada** (27 e 28/10, `features/F5.3-idioma-fila-avisos-dispositivos.md`, D-060): spec e revisão de
        arquitetura (`65fbbe7`), L1 idioma (`7f7225a`, `6f82acc`), L2 fila (`64e0a84`, `2239385`), L3 avisos (`a4e5fe9`),
        L4 dispositivos (`5532083`) e a revisão do diff, R-01 a R-11 (`1996d4e`); no caminho, o log drenado na saída de
        todo processo (`4d28d50`) e a queda 0xC0000409 na saída da suíte. Falta o dono verificar no Windows (Parte 11).
  - [x] **F5.4 implementada** (28/10, `features/F5.4-design-telas.md`, D-061): spec e revisão de arquitetura
        (`004fd83`), L1 sistema de design (`412fa97`, `f83e04d`), L2 gravação (`586dd3f`), L3 Configurações em páginas
        e fila (`dd76609`), L4 reunião, Histórico, revisão e assistente (`aa0eab1`), a revisão do diff, R-01 a R-09
        (`c4f9cc6`), a superfície do L1b sem uso (`4bd019a`) e o tique do widget (`5545e80`). Falta o dono verificar
        no Windows (Parte 12).
  - [x] **F5.5 implementada** (28/10, `features/F5.5-vozes-numeradas.md`, D-062): spec com a revisão de arquitetura
        (`579bddb`) e o código com a revisão do diff, R-01 a R-05 (`3b9320c`); também a página de chaves sem lambda
        (`7fb2df7`) e o worktree da F5.3 removido. Falta o dono verificar no Windows (Parte 13).
  - [x] **F5.6 implementada** (28 e 29/10, `features/F5.6-ata-a-pedido-prompts-excluir.md`, D-063 e D-064).
        - L3, excluir reunião: `426c25e`.
        - L1a, ata, resumo e ações só a pedido: `fe0658f`.
        - L1b, o popup e os estados: `f017f01`.
        - L2, os prompts nas Configurações: `2a8c98e`.

        Cada lote passou por revisão do diff por um agente novo, com cada achado corrigido junto de um teste. Ao todo
        são 894 testes. Falta o dono verificar no Windows (Partes 14 a 16).
  - [x] **F5.7 implementada** (29/10, `features/F5.7-aviso-reuniao-sem-gravar.md`, U-094, D-065 e D-066): avisa
        quando uma chamada começa sem gravação, sem roubar o foco e fora da tela compartilhada. "Gravar…" abre o "Antes
        de gravar" com o app como fonte, e o app nunca grava sozinho.
        - Correções à parte: `0393068`, `2b7effa` e `86dbe9a`.
        - L1, a plataforma: `92e4546`.
        - L2, as regras: `9a631ef`.
        - L3, a interface: o refactor `2aeeb1a` e o `6133722`.

        Revisão do diff por um agente novo no L1/L2 e no L3. São 1.072 testes. Falta o dono verificar no Windows
        (Parte 17).
  - [x] **D-067, o som segue o aparelho escolhido no Windows** (29 e 30/10, pedido do dono). O player passou a abrir
        pelo endpoint padrão de agora e a segui-lo tocando. A auditoria passou por todo lugar que abre áudio: o "Testar
        3 s", o medidor, o mudo e o loopback.
        - O refactor do player: `a249d04`.
        - A correção: `e7a93d0`.

        Revisão do diff por um agente novo. São 1.105 testes. Falta o dono trocar o aparelho de verdade (Parte 18).
  - [x] **F5.8 implementada** (30/10, `features/F5.8-executavel-windows.md`, U-095, D-068 e D-069): o app abre por um
        `listener-meet.exe` em pasta, gerado com `scripts/build_exe.py` em ~7 min. Ele tem o desempenho do venv e o
        ícone da marca.
        - O comparativo escolheu o PyInstaller: o Nuitka (Python compilado para C) não acelerou nada.
        - A spec é o `31e9334`, o escopo o `dc2010d` e o código o `46da67b`.

        Revisão de arquitetura e do diff por agentes novos. São 1.134 testes. Falta o dono usar o `.exe` numa reunião
        de verdade (Parte 19).
- **Perguntas abertas ao dono** (padrão entre parênteses; só vale com a resposta dele):
  1. P23, RAM da busca local: o tokenizer do e5 custa ~262 MB e o app vai a ~620 a 670 MB com o Histórico aberto
     (aceitar e medir na Parte 6 do roteiro; a alternativa é o `sentencepiece`, dependência nova).
  2. U-034: a linha do tempo sem falas não repete o aviso no rodapé (sim; feita na F5.4 L4, 28/10).
  3. U-035: "Voz 1", "Voz 2"… no lugar de "Voz SPEAKER_03" (sim; feita na F5.4 L4, 28/10, só na exibição: pergunta 13).
  4. U-036: o estado do índice com o nome legível do modelo (sim; feita na F5.4 L3, 28/10).
  5. `AGENTS.md`: "Testes: `python -m pytest -q` (a rápida); antes de commitar, `--tudo` (D-050)" (sim).
  6. `pytest-xdist`, só de desenvolvimento, para a meta de 8 s (sim, num lote pequeno).
  7. Próximo passo sem o dono: detalhar a spec da F6 e passá-la pela revisão de arquitetura para ele aprovar (sim).
  8. Sem o token do Hugging Face, transcrever mesmo assim, sem saber quem fala no R (o L é você), em vez de o
     processamento não sair (sim; muda o comportamento da F2, vai para uma spec).
  9. As janelas pretas eram os workers (D-056), e fechar uma delas no X mata o worker sem rastro (medido). Foi isso nas
     quedas do job da reunião 2 (26/10, 15:35 e 15:36) e na do terceiro teste (17:27:55)? E o app que fechou sem o "app
     encerrado": foi o terminal dele que se fechou? (padrão: sim aos dois; se não, investigar o antivírus/EDR).
  10. Além das janelas pretas (D-056) e da revisão (U-039), alguma outra janela ainda sobe por cima? (padrão: nenhuma).
  11. F5.2, pergunta 1: a transcrição revisada sai junto com a identidade, antes da ata (sim; adotado).
  12. F5.2, pergunta 2: "Nova pessoa" com um nome que já existe pergunta "é ela?" (sim; adotado).
  13. Depois do U-035, a reunião, a revisão e a citação copiada mostram "Voz 2", mas a ata, a exportação e os trechos
      da busca seguem com o rótulo interno ("Voz SPK_1"), que é o que vai para a LLM. Levar a numeração "Voz n" também
      para a entrada da LLM, a exportação e o índice. Respondida (D-062): feita na F5.5.
  14. F5.4, Aceite 5: o "Antes de gravar" e a revisão ~5 ms mais devagar que antes da F5.4. Respondida (D-062): aceito.
  15. F5.6 L1 (R-03 da revisão do L1a): numa reunião já revisada com voz sem nome, "Gerar quando todos tiverem nome"
      espera a última voz ou um novo "Concluir", e o popup avisa. Respondida (D-064): espera.
  16. Telemetria do pyannote (vista na revisão da F5.8, de antes dela). O pyannote.audio 4 vem com a telemetria
      ligada. A cada modelo e pipeline carregados, e a cada diarização, ele manda a `otel.pyannote.ai` a origem do
      modelo, a versão, um id de sessão, a duração do áudio e o número de falantes. Nunca o áudio nem nomes, mas
      sai o seu IP. Desligar com `PYANNOTE_METRICS_ENABLED=0` no `worker_env`, uma linha num commit pequeno? Respondida
      (30/10): sim, desligada no `worker_env`, que monta o ambiente do worker, do ao vivo e do preparo.
  17. F5.8, pergunta 3: uma trava de instância única, para o `.exe` aberto duas vezes, ou junto com o venv, no mesmo
      banco (não agora; uma spec pequena quando você quiser).
- **F3 e F4 implementadas** (D-043, D-044): desvios e o que só o Windows verifica nas seções Implementação de
  `features/F3.1-correcoes-revisao.md` e `features/F4-ao-vivo.md`. Toda sessão começa verificando o ambiente (D-045,
  `AGENTS.md`).

## Estado em 28/10/2026
- Código: F1, F1.1, F1.2, F2, F3, F3.1, F4, F5, F5.1, F5.2, F5.3, F5.4 e **F5.5 implementadas**, F5.6 L3 e L1: 805 testes
  mais 7 pulados no Windows com `--tudo` (85 a 110 s; 348 mais 5 no Linux de desenvolvimento no L5 da F5, ~22 s);
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
  G5 com o embedder local dependem do dono) · **F5.2 a F5.5 implementadas** (Partes 10 a 13 do roteiro com o
  dono) · F6 rascunho inicial · F7 futuro.

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
- **Saída com a thread do mudo presa** (visto na revisão do L3 da F5.7, de antes dela): o `_MutePoller` só recebe
  `interrupt()`, sem `wait`. Preso no COM na saída, ele cai na mesma 0xC0000409 que o R-01 corrigiu no sensor de
  chamada. Correção: entrar na conta do `leave_if_stuck` do `app.py`, num commit pequeno.
- **Caminho no log** (visto no L3 da F5.7, de antes dela): o `attach_log` do `app.py`, quando falha, loga o caminho do
  arquivo de log, que tem a pasta do usuário. Isso vai contra a D-053. Trocar pelo código do erro e pôr o `app.py` no
  teste de privacidade. A linha de abertura de todo log também grava `"banco": <pasta>` (`ui/app.py:136`; visto na
  revisão da F5.8).
- **F5.7, aviso sem a exclusão da captura** (R-04 da revisão do L3): num Windows sem `WDA_EXCLUDEFROMCAPTURE`, só mudam
  a dica e o menu da bandeja, e o ícone fica igual. Proposta: um selo no ícone enquanto houver chamada pendente.
- **Suíte travada antes de começar (29/10, F5.7; de novo em 30/10, ~6 min na coleta, com o build do Nuitka ao lado;
  suspeita: a consulta WMI do `platform.machine()`).** Uma execução de `pytest --tudo` ficou 14 min parada antes do
  primeiro teste, com 2,4 s de CPU. A seguinte, com o mesmo código, passou em 2 min. A causa não foi achada. Se voltar,
  rode com a saída num arquivo, não por `| tail`.
- Saúde dos canais começa "ok" por até 10 s sem sinal; R não avisa antes (D-050: silêncio no começo é normal); rever
  depois do G1.
- Deriva de relógio: só instrumentada (A7); corrigir em spec própria se o G1 mostrar mais de 100 ms por hora (P22).
- Textos da interface fixos em pt-BR; prever i18n (`tr()` do Qt ou gettext) antes da F6 (CP15).
- Pasta de modelos ainda não está nas Configurações (entra na F2).
- Testes de interface só cobrem abrir e fechar as telas (pytest-qt offscreen); interação real fica para a F6.
- O eco do warm-up é medido só no "Testar 3 s", não de forma contínua; o `--check` dá o número preciso.
- F5: o filtro de pessoa varre `rag_chunks` (~35 ms a cada 20 mil trechos) e `vector_version` conta o índice a cada
  busca: medir com o banco real. (A F5A-17, o worker caindo com a reunião apagada, foi corrigida na F5.6 L3.)
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
- F5.4: (a) feito em `7fb2df7` (a página de chaves sem lambda com `self`, com o teste estático); (b) acima de 400
  linhas seguem `ui/warmup.py` (696), `history.py` (914), `player.py` (1019) e `controller.py` (1872): dividir ao mexer
  neles; (c) a sonda às vezes acusa o índice do Histórico cortado no print `historico-abrindo` (o texto chega antes do
  layout do canto das abas; no print seguinte sai inteiro).
- Identidade no preset sala (achado R-06 da revisão da F5.5, de antes dela): a costura numera `SPK_n` por canal, e
  `R/SPK_0` e `L/SPK_0` chegam à identidade os dois como "SPK_0"; `_merge_identify` e `_apply_identify` casam pelo
  rótulo, então só um dos clusters recebe a sugestão, talvez o errado. Proposta: o rótulo com o canal na entrada da
  identidade e a busca por (canal, rótulo), numa spec pequena.
- Reunião estreita (visto nos prints do L1b da F5.6, de antes dele): a 640 px o campo do título rola e esconde a
  primeira letra; o `MeetingWindow.__init__` tem ~88 linhas efetivas (dividir ao mexer na janela).
- Teste talvez intermitente (visto na revisão do L1a da F5.6):
  `test_rag_ui.py::test_the_system_loopback_never_opens_with_the_player_playing` falhou uma vez numa rodada inteira com
  `-x`; depois, 0 falhas em 15 rodadas sozinho e nas rodadas inteiras seguintes. Se voltar a falhar, guardar a saída e
  ver a espera do player.

## Medições a registrar em `decisoes.md`
pyannote community-1 por hora em CPU (B4) · tamanho do banco por hora de reunião (G1) · deriva por canal e
deslocamento L/R (G1, `--check`) · F2 no primeiro job real: RTF por etapa e modelo,
RSS por etapa, sonda Q8 (`speaker_embeddings` e id do modelo), similaridade intra e inter-pessoa por plataforma ·
precisão dos perfis, desvio do clique e legibilidade (G2) · F4 no Windows (B15): embedding por janela (o resto do
`live_probe` medido em 25/10); G4: atraso médio e p95, CPU, acerto do nome · F5 no Windows (item 8): `rag_probe gemini`
(por lote), varredura da primeira abertura, híbrida com o vetor da consulta, `underflowed` do player com o worker pesado
de verdade; G5: citações certas em 10 perguntas · F5.1 com o dono (item 9): o G5 com o embedder local e a RAM real
com o Histórico aberto (P23).
