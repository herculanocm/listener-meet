# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Onde paramos (08/11/2026, Linux nativo; tudo commitado; o push é do dono)
- **F5.21 aprovada** (08/11, `features/F5.21-tela-nas-sugestoes.md`, D-102; chat: "Aprovado, siga com implementação"):
  o print da tela mandado como contexto da sugestão do que falar, a miniatura no chat da bandeja e as telas anteriores no
  contexto (U-164 a U-170), com a revisão de arquitetura A-01 a A-42. A implementação segue os lotes L0 a L3, um commit
  por lote, com a revisão do diff por um agente novo (D-040); cada lote roda os testes novos e os dos arquivos tocados, e a
  suíte inteira roda no fechamento (D-102). Posição (`[x]` com o commit):
  - [x] L0: o `suggest_task.py` dividido em `suggest_ctx`, `suggest_dossier` e `suggest_memo_task`, sem mudar comportamento (o commit do L0).
  - [x] L1: a imagem na camada de LLM, a v11, o prompt `suggest` 1.1 e a tarefa com a tela (o commit do L1).
  - [x] L2: a plataforma, o preparo, o host, o cartão, o chat, a janela maior, o atalho, o menu e as Configurações (o commit do L2).
  - [ ] L3: a aba, o `--check`, a sonda, o `exe_probe`, os documentos e a versão 0.14.0.
- **F5.20 implementada** (08/11, `features/F5.20-sugestoes-ao-vivo.md`, D-100, D-101; versão 0.13.0): o pedido do dono no
  chat, um botão durante a reunião que sugira o que falar, com o cargo da ficha e o contexto das legendas, avaliação por
  opção e texto livre para direcionar. O "Sugerir" na bandeja de legendas, no widget, no menu da bandeja do sistema e no
  atalho `ctrl+alt+shift+d` pede 2 a 3 opções curtas pela LLM rápida, em streaming, com os fatos citados por legenda;
  "Boa", "Ruim" com o motivo e "Usei" agem no pedido seguinte e, ao parar com 10 avaliações novas, num memorando
  visível e editável na página Prompts; o direcionamento em três camadas (Prompts, o objetivo da reunião e "Como
  sugerir agora"); "Me situar"; o dossiê dos participantes; o "falaram com você"; a aba "Sugestões" da reunião e o
  bloco do `--check`; o pedido comprimido (resumo rolante e compactação local) e o teto de custo partilhado com o resumo
  parcial; tudo fora da captura de tela e ligável em uso (U-153 a U-163). Implementada no Linux nativo pelos lotes L1 a
  L4, um commit por lote, cada diff revisado por um agente novo (D-040). Falta o dono: o uso real com os números do
  Aceite (em "Verificar no Windows"), o `.exe` 0.13.0 (no Windows, com o OK dele) e o push. Posição (`[x]` com o
  commit):
  - [x] spec aprovada (D-100): `9a438f9`; L1, a v10, as tarefas, os prompts, a compactação e o leitor: `3082e1a`; a
    revisão do L1 (R-01 a R-08): `6a2e119`.
  - [x] L2, o cartão, o botão, o atalho, o menu, a caixa e o objetivo: `4ad3731`; L3, o dossiê, o teto, o nome, o
    memorando, a aba e o `--check`: `23c2b7b`; as revisões do L2 (R-01 a R-12) e do L3 (R-01 a R-08): `e9e2ef9`.
  - [x] L4, a sonda das fotos e dos tempos, a revisão do diff, os documentos, a D-101 e a versão 0.13.0: o commit do
    fechamento.
  Nesta máquina (Linux nativo, sem PortAudio nem PyInstaller), a suíte tem 13 falhas do ambiente, as mesmas de antes da
  F5.20 (o widget 1 px mais alto no offscreen, os tempos dos avisos e do cartão de chamada, as cores do estilo, o build):
  a régua de cada lote foi nenhuma falha nova (`memory/maquina.md`).
- **F5.19 implementada** (07/11, `features/F5.19-exportar-audio-e-video.md`, D-098, D-099; versão 0.12.0): o pedido
  do dono no chat, "exportar o áudio" e o "vídeo com a legenda com os nomes e títulos de cargo". O Exportar da reunião
  aberta ganhou "Salvar áudio…" (MP3, FLAC ou WAV, como o player toca, as pausas fora) e "Salvar vídeo com legendas…"
  (MP4 H.264 e AAC, 1280 × 720, a fala atual com o nome e o cargo do dia na cor do falante e a anterior acima), numa
  tarefa com o progresso e o "Cancelar" numa faixa própria, que espera parada com uma gravação em curso; nenhuma
  dependência nova (soundfile e o PyAV que já vem com o faster-whisper). Reunião 22 (55 min): o MP3 em 10,1 s (17 MB)
  e o MP4 em 61,6 s (49 MB). O `.exe` 0.12.0 e o zip saíram com o OK dele (sonda ok). Falta o dono: o uso real (em
  "Verificar no Windows") e o push. Posição (`[x]` com o commit):
  - [x] spec, a revisão de arquitetura (A-01 a A-25) e a aprovação (D-098, 8 perguntas no padrão): `a5fb5a0`.
  - [x] `export.atomic_target` (A-23): `4951786`; implementação e testes: `d79996a`.
  - [x] revisão do diff por um agente novo (R-01 a R-04, baixas, aplicadas), D-099, a suíte inteira e a spec
    implementada: o commit do fechamento.
- **F5.18 implementada** (07/11, `features/F5.18-performance.md`, D-096, D-097; versão 0.11.0): a spec de performance
  pedida pelo dono (a D-094 (3) já pedia a dívida F2 Q7 numa spec própria): cinco lotes, do pesado ceder a CPU em
  segundos (hoje em até 29 min) ao tronco do wespeaker rodado uma vez por trecho (hoje três) e ao Whisper em lote; três
  pareceres somente leitura (arquitetura, código da captura/ao vivo/pipeline, código do store/RAG/LLM/UI) e a revisão de
  arquitetura da D-040 (A-01 a A-16) incorporados. Adiados pela revisão, fora do esforço: PF-12 (afinidade P/E), PF-24
  (memória do e5 local), PF-25 (laço asyncio persistente no app) e PF-27 (`semantic.build`); voltam com os M-01, M-04 e
  M-10 e quando o e5 voltar a ser o embedder ativo (aí vale medir `sentencepiece` no lugar do `tokenizers`: ~262 MB
  residentes hoje). Aprovada no padrão em 07/11 pelo chat, com as decisões técnicas delegadas (D-064, D-096);
  implementada pelos lotes 0 a 5, um commit por lote, com a D-097 no fechamento; falta o dono: o uso real (a lista em
  "Verificar no Windows") e o push. A página privada que serviu à leitura:
  https://claude.ai/artifact/LfrmQ6Z4tDaqEPED6zNQY4 Posição da implementação (gravada a cada etapa, a pedido do dono em
  07/11; `[x]` com o commit):
  - [x] aprovação registrada: spec, D-096, U-147 a U-149 (`bf89d80`).
  - [x] lote 1 (PF-01 a PF-03) implementado, revisado (R-01 a R-05 aplicados: lote padrão 8 trechos = 24 pares; as
    guardas da prioridade no heartbeat) e commitado com os testes do lote verdes, ruff e mypy limpos nos arquivos dele;
    a suíte inteira (`--tudo`) roda no fechamento do lote 4, que ainda edita a árvore. Só o Windows com os modelos
    verifica: o `pausado dentro da janela` em até 13 s no `listener-worker.log`, o worker "Ociosa" no Gerenciador de
    Tarefas durante a gravação e o filho `--live` em "Normal", o RSS do `_end_step` com 24 pares.
  - [x] sondas do lote 0 escritas (`scripts/probe_perf.py` e `scripts/perf/`, M-01 a M-10 e o `replay`, com smokes; os
    JSON vão para `%LOCALAPPDATA%\listener-meet\perf-out`). Sinais dos smokes: pesos do wespeaker iguais nos dois
    modelos (218/218, cosseno 1,0: o PF-08 entra); o `pack` juntou 4 janelas em 2,9 s contra 16,8 separadas, mas o texto
    encolheu (atenção no PF-09); sem bilhete de sessão TLS nesta rede (o PF-25 fica adiado).
  - [x] lote 0, fase A medida: M-10, M-05 e M-08 (em `decisoes.md`, Medições, 07/11): sem bilhete TLS na rede (PF-25
    segue adiado; partilhar o `SSLContext` poupa ~50 ms por cliente novo), eco só na reunião 22 (71 %; as outras 0 a 5
    %), pesos iguais mas cosseno VBx × turno 0,88 numa voz, abaixo de 0,95 (o PF-08 NÃO substitui o embedding por turno;
    só o id do modelo deixa de sair "dict"). Faltam os de tempo (M-02, M-03, M-04, M-06, M-07, M-09, só com a máquina
    sem agentes testando) e o M-01 (~3 h, no fim; o padrão das threads fica pela fórmula até lá).
  - [x] lote 4 (PF-13 a PF-22) implementado e commitado (`store/jobs.py` em dois ramos, `store/jobs_sql.py` e
    `store/rag_signatures.py` novos, `rag/filters.py` novo, o contador `rag.vector_version`, o leve vivo 10 min com a
    saída por `user_version`, a lista em lotes, o cursor extrapolado); suíte inteira: 1.679 passaram e 2 falharam no
    `test_theme` por ordem (passam sozinhos e na rodada seguinte: a instabilidade já vista na F5.16), ruff e os dois
    mypy limpos (commit `3462394`); revisão do diff aplicada (R-01: o `acheckpoint` depois de cada grupo e o
    `cancel_requested` antes de cada bloco do `revise`, com teste; R-02: o `job_id` lido antes do `gather`; R-03 aceito:
    a dica de tempo dos jobs antigos conta pelo maior; os nomes de `jobs_sql.py` sem sublinhado).
  - [x] lote 0, fases B e C medidas na reunião 22 (07/11, em `decisoes.md`): M-02 embeddings 93 % da diarização (o PF-04
    é a alavanca); M-07 343 ms por janela (o PF-10 vale); M-06 o passe junto perde fala mesmo com timestamps (o PF-09 e
    a U-147 não entram); M-09 pragmas −21 % na carga das assinaturas (o PF-26 entra com `cache_size` e `mmap_size`);
    M-04 o filho em NORMAL cai de 17,2 para 10,2 s sob carga, sem heartbeat esfomeado; M-03 o Whisper em lote sem ganho
    (+4 %) e fora do Aceite 4, com a sonda caindo no lote 8 (o PF-05 não entra, sem a configuração). Falta só o M-01 (~3
    h), no fim, que só muda o padrão das threads se mostrar outra regra. `capture/echo.py` (a regra do eco, pura,
    partilhada pelo PF-07 e pelo ao vivo) já commitado com teste. Uma suíte travada de 14:32 ficou viva com 4 workers
    até 15:40 e pesou nas rodadas da fase C (foi encerrada).
  - [x] lote 2 commitado: `pipeline/lean_diarization.py` (o tronco do wespeaker uma vez por trecho; sonda `lean`: 5 min
    da reunião 22 de 305 para 118 s, −61 %, clusters idênticos e embeddings com diferença máxima 3,5e-7),
    `pipeline/echo_gate.py` (o gate de eco no L e no reforço do perfil, com `blocks`/`echo_blocks` no `timings_json` e
    no `job_done`, contados só depois do artefato da janela), o id do modelo sem "dict" (o Q8 segue desligado),
    `pipeline.echo_gate`, a fixture `forget_models` no `conftest`; revisão (R-01, R-04 a R-06) aplicada; R-02 e R-03 são
    desvios de texto (a fração do eco vai no `job_done`, não no `job_step`; a máscara é por etapa) para a spec e a
    D-097. Só o Windows com os modelos verifica: as 3 reuniões reprocessadas (Aceite 1), o texto do L nas reuniões do
    M-05 (Aceite 5), o RSS com o gate ligado numa reunião sem headset.
  - [x] lote 3 commitado (`bee6ae9`): `live/pacing.py` (o teto de tokens: 32 + 10·s, nunca acima de 448 − prompt − 8;
    sem tokenizer, 228 assumidos), o nome em `live-embed` antes do Whisper, o eco partilhado, a legenda lenta no log e
    `stats.capped`; revisão (R-01 a R-05) aplicada. Só o uso real verifica: o atraso p95 do R com o nome sobreposto e o
    `live_cpu` com três threads nativas na janela; `capped` no `live_stopped`.
  - [x] lote 5 commitado (`ee53ec1`): PF-23 (`poly_fir` em cache passado em `window=`, saída bit a bit; 48 kHz 143 → 77
    µs e 44,1 kHz 654 → 89 µs por bloco), PF-26 só os pragmas do main (`cache_size` 32 MiB, `mmap_size` 256 MiB), PF-28
    sem o `_write_lease` nem a contagem de temperatura (a redação e o `SSLContext` em cache, o `gather` em lotes), PF-29
    com a migração do hash no lugar (`rag/chunk_hash.py` novo; renomear a reunião não refaz os vetores da transcrição);
    revisão (R-01 a R-05) aplicada: taxas iguais sem filtro, a versão dos vetores só sobe se as pessoas mudaram, o proxy
    fora da chave do TLS; R-06 a R-08 (docs: o módulo novo, o pré-import do SDK sem registro; riscos: um binário antigo
    refaz os vetores uma vez, um embedding pago duas vezes na janela da migração) ficam para o fechamento.
  - [x] vigia de travamentos da suíte (`ed29c25`, `tests/hang_guard.py`, a pedido do dono depois de duas rodadas
    travadas em 07/11): `faulthandler` por teste (180 s; a coleta e o fim 360 s), as pilhas em
    `%TEMP%\listener-tests-hang.log`, o processo preso derrubado e o teste em curso nomeado no resumo da rodada. Regra
    de rodada: pytest sempre com a saída num arquivo (`> arquivo 2>&1`, nunca `| tail`); a cada passo só o lote e os
    vizinhos, a suíte inteira uma vez no fechamento (D-050).
  - [x] fechamento, parte 1: suíte inteira (1.719 passaram, 8 pulados, 2:30 min; `test_theme` por ordem;
    `test_rag_local` ajustado ao `passage:` sem título, `7a898c0`), ruff limpo, Implementação do lote 5 e do vigia na
    spec, R-07 e R-08 em Riscos, D-097, versão 0.11.0.
  - [x] fechamento, parte 2: `.exe` 0.11.0+bfe8ed4 (438 s, 893 MB, 4.700 arquivos; sonda `--probe` ok) e zip de 351 MB;
    M-01 feito em 90 min (12 threads é o melhor para a diarização e o Whisper: o padrão `cpu_count - 2` fica;
    `KMP_BLOCKTIME=0` sem ganho); a spec implementada, Tamanho-alvo 55 KB (o commit final).
  Como retomar: ler a spec e esta posição; o que estiver "em implementação" sem commit está no working tree (`git
  status`); um lote só fecha com a revisão do diff por um agente novo (D-040) e a suíte inteira limpa.
- Código: F1 a F5.20 implementadas (o detalhe na seção Implementação de cada spec; as decisões em `decisoes.md`); até a
  F5.15, verificadas pelo uso real do app no Windows, sem os números dos gates (D-090). F6 é rascunho e fica para
  depois (D-091); F7 é futuro. Esquema v10, versão 0.13.0 (D-081; F5.20).
- **F5.16 implementada** (05/11, D-091, D-093, `features/F5.16-sem-token-hugging-face.md`): sem o token do Hugging
  Face, a reunião sai transcrita com o R "Desconhecido" e o aviso do U-139; reprocessar sem o token não tira vozes já
  identificadas. Falta o dono: usar uma reunião sem o token (ou só seguir usando; nada muda com ele).
- **F5.17 implementada** (06/11, D-094, D-095, `features/F5.17-bandeja-de-legendas.md`): a legenda ao vivo numa
  bandeja própria, como chat, fora do widget, com minimizar e fechar; a janela Ao vivo ganhou a mesma lista (uma
  legenda nova de 73 para 2 ms com 2.000 falas). Falta o dono: usar numa reunião (Aceite 4, em "Verificar no
  Windows").
- O `.exe` atual é o 0.12.0 de 07/11 (`fd8b11d`, a F5.19, com o OK do dono no chat; 434 s, 893 MB, 4.700 arquivos;
  sonda `--probe` ok; o pacote leva os seis módulos `media_*`, o FFmpeg do PyAV com o `libx264` e o libsndfile). Para
  compartilhar, o `build\exe\listener-meet-0.12.0-win64.zip` (351 MB, a pasta inteira; D-081: `build_exe.py --zip`).
  O processamento da reunião 23 (job 145), parado em 06/11 às 23:30 para o build da F5.18, volta à fila e retoma da
  última janela salva quando o app abrir.
- Sem CI: o `ci.yml` saiu do repositório (D-090); os testes rodam na máquina, antes de cada commit, em paralelo (D-091).

## Como retomar (5 minutos)
1. T0: `AGENTS.md` (primeiro: em que ambiente a sessão está), `specs/01-clausulas.md` e este arquivo; depois só a
   camada da tarefa.
2. Testes (D-050): `python -m pytest -q` é a parte rápida; antes de commitar, a inteira em paralelo (`--tudo -n 4
   --dist loadgroup`, D-091), mais `ruff check .`, `mypy` e `mypy --platform linux`. No Windows, pelo venv do checkout:
   `.venv\Scripts\python.exe -m pytest -q --tudo -n 4 --dist loadgroup`, `.venv\Scripts\ruff.exe check .` e
   `.venv\Scripts\mypy.exe`.
3. Antes de código: a spec da entrega em `specs/features/` está `aprovada`? (CP13)

## Verificar no Windows (quando a sessão estiver no Windows nativo)
- F5.20 (o Aceite, em três reuniões reais com headset): `scripts/win_probe.py shots` claro e escuro, com as fotos `f520-*`
  sem `cortados` nem `pequenos`, e a linha `f520_tempos` (cada legenda e cada opção abaixo de 5 ms na thread da
  interface, com `pinturas` acima de zero; nesta máquina Linux a primeira opção com 2.000 falas passou de 5 ms, e a
  alavanca está na spec); o Teams compartilhando a tela sem o cartão e sem o popup do "Como sugerir agora" ou do "Objetivo…"; os
  cliques no cartão e no "Sugerir" sem tirar o foco do app de reunião; o atalho `ctrl+alt+shift+d` registrado (o aviso,
  se outro app o tiver); a primeira opção com a LLM real (p50 até 3 s e p95 até 6 s; a sugestão inteira com p95 até
  10 s), no Gemini com o JSON em streaming (A-27) e numa família do Claude de ferramenta forçada (R-01 do L1); os números
  do Aceite pelo `--check` das três reuniões (a parcela útil, a evidência, a compactação e o custo por pedido); o
  memorando depois de 10 avaliações, visível e editável na página Prompts; o `.exe` 0.13.0 (sonda `--probe`), com o OK
  do dono.
- F5.19 (Aceite 4, no uso): salvar o áudio (MP3) e o vídeo de uma reunião processada e abrir no player do Windows e
  num celular (WhatsApp ou Teams): a legenda no tempo da fala, os nomes e cargos certos, o MP3 tocando; fechar a
  janela no meio (a pergunta); começar a gravar outra reunião com uma exportação em curso (a faixa diz "Na espera").
- F5.18 (no uso, com os modelos): lote 1, o `pausado dentro da janela` em até 13 s no `listener-worker.log`, o worker
  "Ociosa" no Gerenciador de Tarefas durante a gravação e o filho `--live` em "Normal"; lote 2, as 3 reuniões
  reprocessadas (Aceite 1), o texto do L nas reuniões do M-05 (Aceite 5) e o RSS com o gate ligado numa reunião sem
  headset; lote 3, o atraso p95 do R com o nome sobreposto, o `live_cpu` com três threads nativas e o `capped` no
  `live_stopped`; lote 5, o app e o worker abrindo o banco real com `mmap_size` 256 MB (RSS na partida) e a primeira
  varredura do índice migrando os hashes da transcrição sem refazer vetor (`rag.vector_version` parado, nenhum
  `rag_embed` novo em `llm_calls`); o `.exe` 0.11.0 (sonda `--probe`).
- F5.17 (Aceite 4, no uso): a bandeja aberta, recolhida, fechada e reaberta numa reunião; a roda do mouse, as dicas e
  o clique direito (o menu fecha com o clique fora e não fecha sozinho); o Teams compartilhando a tela sem a bandeja;
  dois monitores com escalas diferentes.

Antes disso, nada pendente: em 04/11 o dono considerou feitas todas as verificações (as Partes 1 a 26 de
`specs/roteiro-teste-windows.md`) pelo uso real do app (D-090). Entrega nova feita fora do Windows põe aqui o que só o
Windows verifica (`AGENTS.md`); o dono confere no uso, e o roteiro fica como referência. Sobra da Parte 3, com o OK
dele: apagar o venv do F0 e as duas entradas vazias de 24/10 no cache do Hugging Face (D-050, D-052).

## Perguntas abertas ao dono (padrão entre parênteses; só vale com a resposta dele)
- O `.exe` 0.13.0 da F5.20 e o push são decisões do dono.
As anteriores (1, 5 a 10, 17 e 19) foram fechadas no padrão em 04/11 (D-091) e estão em `decisoes.md`, nas specs e no
git.

## Bloqueios que só o dono destrava
Nenhum: B1, B2, B5 e B15 fecharam pelo uso real, sem os números que pediam (D-090); B3, B4 e B6 a B16 já estavam
fechados (as decisões em `decisoes.md`).

## Dívidas técnicas
- F5.19, da revisão de arquitetura: (A-01, fato novo) o .docx loga o caminho inteiro, e o nome padrão traz o título,
  pela decisão do R9-03 e com teste, contra o D-053; o .md e a F5.19 logam só a pasta. Proposta: alinhar o .docx
  (duas linhas e o teste do R9-03). (A-14) Se a interface atrasar durante uma exportação, o plano B é a exportação num
  processo filho em BELOW_NORMAL, com GIL próprio.
- F5.18, das revisões: a regra dura `test_app_modules_do_not_import_models` num subprocesso (lote 2); `live/proc.py`
  cresceu 38 linhas (mover os motores para um módulo próprio ao mexer); `store/rag.py` com 474 linhas; o `test_theme`
  falha por ordem na suíte inteira (2 testes, passam sozinhos: a instabilidade da F5.16); `NetworkConfig.verify()` sem
  chamador em `listener/`; o `mypy` sobre `scripts/` (fora do gate `files` do `pyproject`) acusa 54 erros, 11 deles nas
  sondas do lote 0 (`perf/m_live.py`, `m_sqlite.py`, `m_threads.py`, `m_tls.py`, `replay.py`, `probe_perf.py`).
- D-072, reais e pequenas (sem correção por ora): a morte da thread de trabalho da captura não marca a reunião; o
  "banco não responde há N s" exagera (`pending_since`); corridas de leitura e escrita fora da transação (sugestão da
  LLM num cluster recém-confirmado, ação superada que volta, `upsert_me`); o chat em streaming sem retentativa antes do
  primeiro pedaço e o cancelado gravado como `OK` sem custo; o preparo do ao vivo sem prazo no `repo_info` e sem
  Cancelar; o `db-location.json` sem gravação atômica.
- F5.10 (D-075): o `--downgrade` com o app aberto faz o chat falhar até reabrir (nada se perde); a `memory_rss` do
  Windows mede o working set, que o sistema pode encolher, e o teto da consulta mediria menos que o real.
- Instável sob carga, fora da F5.14: `test_call_watch.py::test_the_app_exits_with_code_0_with_the_sensor_stuck_in_the_audio_system`
  falhou 1 vez na suíte do L2 da F5.14 (faltou "não parou em 1000 ms" no log) e passou nas 3 rodadas sozinho e na suíte
  seguinte.
- F5.9, das revisões: `test_call_watch.py::test_failures_degrade_with_one_warning_and_recover_with_one_info` é instável
  na suíte inteira (3 falhas em 7; 9/9 isolado; de novo em 06/11, 3/3 isolado); o `pyproject` não liga o `C901`
  (o limite de complexidade 10 é só convenção); o nativo do proctap cai no sistema inteiro calado em caminhos de
  falha (`is_process_specific()` diz quando) e somaria o sistema inteiro no canal do app sem aviso; o pedido de saída
  ambíguo (nome repetido no loopback) não fica no handle, e a vigia pode pedir `back` de novo a cada mudança de
  assinatura.
- F2 (Q7), a lease lida só entre janelas: a janela de diarização de 1.200 s (RTF 1,2 a 1,6) atravessa uma gravação
  inteira (o job 47 diarizou durante a reunião 8, e o ao vivo degradou aos 60 s, com 37 s de atraso). O pós-reunião da
  reunião 8 somou ~1.533 s para 900 s de áudio (~1,7×; o G2 pede até 1,5×, plano B 2×, D-041). Opções: janela menor
  na diarização ou baixar a prioridade do worker enquanto a lease vale; decidir com o dono antes.
  Em 06/11, de novo na reunião 22: o job 135, da 21, seguia em andamento na gravação inteira (p95 de 25 s, máximo de
  92 s; degradou e pulou janelas). Pela D-094, é esta a alavanca do atraso do ao vivo, e não o paralelismo. A spec
  F5.18 (rascunho, 07/11) a trata no lote 1 (PF-01 a PF-03: lease conferida dentro da janela e prioridades separadas).
- F5.7 (R-04 do L3): num Windows sem `WDA_EXCLUDEFROMCAPTURE`, só mudam a dica e o menu da bandeja, e o ícone fica
  igual; proposta: um selo no ícone enquanto houver chamada pendente.
- Testes: a suíte já travou antes do primeiro teste (29 e 30/10: 14 min parada com 2,4 s de CPU, e a seguinte passou em
  2 min; suspeita: a consulta WMI do `platform.machine()`); se voltar, rodar com a saída num arquivo, não por `| tail`.
  `test_rag_ui.py::test_the_system_loopback_never_opens_with_the_player_playing` falhou uma vez com `-x` (0 em 15
  depois); se voltar, guardar a saída e ver a espera do player.
- F5.11 (D-078): desfazer a voz transportada de um reprocessamento não acha o vetor (a linha "Para desfazer" não
  aparece nela).
- Suíte acima da meta de 8 s (D-050): com o `pytest-xdist` (D-091), a inteira caiu de 287 para 139 s com `-n 4
  --dist loadgroup` (os arquivos que lançam subprocesso num processo só, `SUBPROCESS_FILES` no `conftest`) e a rápida
  de 81 para 62 s; mais processos pioram nesta máquina (2 núcleos de desempenho). O resto é lógica espalhada (o
  `test_store` sozinho dá 5 s), não o EDR. Testes e sondas deixam pastas
  `listener-*` no `%TEMP%` (82 em três dias, apagadas na D-052): passar os testes para o `tmp_path` e as sondas a apagar
  o que não imprimem.
- Captura: a saúde dos canais começa "ok" por até 10 s sem sinal e o R não avisa antes (D-050: silêncio no começo é
  normal); rever se aparecer no uso. A deriva de relógio só é instrumentada (A7); corrigir em spec própria se o
  `--check` de uma reunião mostrar mais de 100 ms por hora (P22). O eco é medido no "Testar 3 s" e depois de trocar o microfone (F5.9), não de forma
  contínua; o `--check` dá o número preciso.
- F4: o eco do ao vivo usa o limiar do aviso (`capture.echo_corr_min`, 0,25); calibrar se o eco vazar sem headset.
- F2: o worker pesado carrega o CTranslate2 antes do torch, a ordem inversa à do F0; se aparecer "OMP: Error #15" (dois
  `libiomp5md.dll`), importar o torch antes, como o ao vivo já faz (a reunião 8 processou sem ele).
- F5.15: quem junta 5 notas de grupo passa a exigir ~0,75 (as notas ficam acima de 0,8 pelo corte; A-01); o L0 não
  viu, porque ninguém tinha 5. Acompanhar no uso; se a pessoa conhecida começar a cair em pendente na condição
  de sempre, a variante C (só o global) está a uma constante.
- Para depois (04/11, ideia do dono avaliada): se o uso mostrar gente conhecida caindo em pendente ou
  com o nome errado, uma spec pequena, local e por evento: (1) a referência nova refaz a sugestão das vozes pendentes
  também das reuniões antigas (hoje só na reunião aberta; nas outras, o Reprocessar); (2) uma checagem dos perfis aponta
  a referência que parece confirmação errada (amostra sozinha, longe das outras da pessoa e perto de outra), sem apagar
  nada (CP3). Uma rotina periódica com a LLM ficou de fora: a LLM não melhora a biometria, a CP11 a impede de nomear,
  e um palpite que virasse referência daria o nome errado sozinho.
- F2: durante um reprocessamento a revisão mostra o job concluído (a abertura antecipada, P1, vale só para o primeiro
  processamento); `embed_range` (trecho livre) e separar perfis ficam para a F6.
- F2: dois workers leves podem nascer no mesmo segundo (D-056): o segundo não acha job e sai ocioso em 60 s; inofensivo
  (o claim é atômico); resolver com uma marca de lançamento no banco, se valer.
- Identidade no preset sala (R-06 da F5.5): a costura numera `SPK_n` por canal, e `R/SPK_0` e `L/SPK_0` chegam à
  identidade os dois como "SPK_0"; só um dos clusters recebe a sugestão, talvez o errado. Proposta: o rótulo com o canal
  na entrada da identidade e a busca por (canal, rótulo), numa spec pequena.
- F5: o filtro de pessoa varre `rag_chunks` (~35 ms a cada 20 mil trechos) e `vector_version` conta o índice a cada
  busca: medir com o banco real.
- Os preços da geração 3 do Gemini não estão nas sementes (D-055): a estimativa usa os do 2.5 [chute]; conferir com a
  fatura e cadastrar em Configurações › LLM › Gemini. No log, o "Windows fatal exception: code 0x8001010d" é o
  faulthandler registrando uma exceção COM já tratada: ruído, não queda (D-055).
- Código acima de 400 linhas, dividir ao mexer: `controller.py`, `live/proc.py`, `player.py`, `recorder.py`,
  `llm_steps.py`, `llm/suggest_task.py` (F5.20, 509 linhas: o memorando e o dossiê podem sair) e outros; `rag/agent.py`, `ui/history_ask.py` e `rag/sandbox.py` perto do limite; o
  `MeetingWindow.__init__` com ~88 linhas efetivas.
- Interface: a 640 px o campo do título da reunião rola e esconde a primeira letra; a sonda às vezes acusa o índice do
  Histórico cortado no print `historico-abrindo` (o texto chega antes do layout do canto das abas; no print seguinte
  sai inteiro); os textos são fixos em pt-BR (prever i18n, `tr()` do Qt ou gettext, antes da F6; CP15).
- CP17: subir o piso e o teto do `requires-python` quando WhisperX e torch aceitarem Python 3.14 (hoje `<3.14`). O lock
  é só do Windows (`requirements/windows.txt`); o Linux segue com o `pyproject.toml` sem trava, e um lock do Linux pede o índice CPU do torch (o do PyPI traz CUDA, CP14).
