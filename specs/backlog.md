# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Onde paramos (25/10/2026, à noite no Windows nativo; tudo commitado; push do dono)
- **F5 implementada** (D-047): L0 a L5 e as correções das revisões R5 a R8 (as últimas `af2ea3d` e `6e0f235`, mais a
  corrida do worker leve `9e95f60`); desvios e decisões na seção Implementação de `features/F5-rag-historico.md`. A F5.1
  (embeddings locais, linha do tempo, destaque contínuo, .docx) vem depois, em entrega própria.
- **Próximo passo, comece aqui:** "Verificar no Windows" (abaixo). Pedido do dono (25/10): a LLM roda tudo o que não
  depende dele e ele executa o resto no fim (chaves, reuniões reais, olhar e usar a interface). Itens 1 e 2 feitos; dos
  itens 3, 7 e 8, a parte sem o dono em andamento.
- **F3 e F4 implementadas** (D-043, D-044): desvios e o que só o Windows verifica nas seções Implementação de
  `features/F3.1-correcoes-revisao.md` e `features/F4-ao-vivo.md`.
- **Com o dono, no Windows:** B1, B2, B4, B13, B15 e os gates G1 a G5. Toda sessão começa verificando o ambiente (D-045,
  `AGENTS.md`).

## Estado em 25/10/2026
- Código: F1, F1.1, F1.2, F2, F3, F3.1, F4 e **F5 implementadas**: 366 testes mais 4 pulados no Windows (~45 s; 348
  mais 5 no Linux de desenvolvimento no L5, ~22 s); `LISTENER_SDK_TESTS=1` liga os de
  transporte simulado dos SDKs e o do truststore com clientes reais; ruff e mypy limpos (Linux e `--platform win32`),
  CI em três sistemas × Python 3.12 e 3.13 mais uma célula com o extra `llm`. Esquema v8. Nada do app importa torch, ML nem os SDKs de LLM (teste estático).
  Whisper, pyannote (F2 L5), os três adaptadores de LLM e o processo ao vivo real foram escritos às cegas, contra
  transporte simulado ou num venv à parte sem torch: só rodam de verdade no Windows com o token do Hugging Face (B4) e a
  chave do Gemini (B13).
- Git: commits de 24/10 a 25/10 (F1, F1.1, CP16, CP17, F1.2, D-040, F2 em onze, F3 em oito, F3.1 em três, F4 e F5 em
  lotes, ajustes e revisões); branch `main`; remoto `origin` público no GitHub (o dono faz o push; em 25/10 à noite o
  checkout do Windows estava igual ao `origin/main`, em `45a4f3d`); sem stash. Commit a cada grande alteração (CP16).
- Specs: F0 aprovada e medida (falta pyannote, B4) · F1 implementada, G1 pendente · F1.1, F1.2 e F2 implementadas (G2
  pendente) · **F3 e F3.1 implementadas** (D-043; contrato em `specs/llm-contrato.md`) · G3 depende da chave do Gemini no
  Windows · **F4 implementada** (D-044; L0 e G4 dependem do Windows) · **F5 implementada** (D-047; o item 8 e o G5
  dependem do Windows com o dono) · F5.1 aprovada, separada · F6 rascunho inicial · F7 futuro.

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
| B15 | no Windows: `python scripts/live_probe.py all <gravação real>` (L0 da F4; com `HF_TOKEN` para o torch) e colar as linhas JSON; depois do B1, "Preparar modelos do ao vivo" nas Configurações e uma gravação com a legenda ligada | números do Whisper, do VAD e do torch no Windows; o filho real, o EcoQoS e a ordem de carga torch/CTranslate2; abre o G4 |

Fechados: B3 (F2 aprovada, D-036) · B6 (primeiro commit) · B7 (MIT, D-034) · B8 (F1.1 aprovada, D-035) · B9 (compactação,
D-031) · B10 (F1.2 aprovada em bloco, D-039) · B11 (F2 reaprovada, D-041) · B12 (F3 aprovada, D-043) · B14 (F4 aprovada,
D-044) · B16 (F5 aprovada, D-047).

## Verificar no Windows (rodar na ordem quando a sessão estiver no Windows nativo)
Tudo que foi feito no Linux ou no WSL só vale depois daqui (CP15). Item concluído ganha a data; números em `decisoes.md`
(Medições) e `memory/maquina.md`; defeito vira correção pequena (sem agente) ou item novo aqui.
1. Checkout no último `main` (o dono faz push de onde desenvolveu e pull aqui); venv 3.12; `pip install -e .[dev,win32,llm]`
   (mais `[pipeline]` nos itens 5 e 7). **Feito em 25/10:** checkout em `C:\dev\listener-meet`, venv novo em `.venv` dentro
   dele, sem ML, como o CI (239 s com o cache do pip). Com `[pipeline]` no mesmo venv, o teste do worker sem o extra
   (R2-27) deixa de valer: ajustar no item 5.
2. `python -m pytest -q`: aqui rodam também os testes só do Windows (`spawn_child` abaixo do normal, EcoQoS). **Feito em
   25/10:** 349 passaram e 4 pulados (os de `LISTENER_SDK_TESTS`, que ligados também passam, truststore incluso) em
   ~35 s (50 s a frio); ruff e mypy limpos. Duas falhas reais corrigidas: o pseudo-handle de `GetCurrentProcess` chegava
   truncado no Windows de 64 bits e o worker ficava sem prioridade baixa e sem RSS por etapa.
3. `python -m listener --selftest`, depois `python -m listener`: assistente, gravação curta, fila, revisão e
   Configurações (B1); olhar DPI, bandeja do Windows 11 e tempo de abertura; a LLM corrige o que aparecer. **Sem o dono,
   25/10:** `--selftest` com código 0 (mic a 16 kHz pelo WASAPI, primeiro pacote em 382 ms; loopback a 48 kHz; desvio de
   +12 e −1 ms; sem buracos).
4. G1 (B2): três reuniões reais com headset em duas plataformas; `python -m listener --check <pasta do banco>` (deriva,
   deslocamento L/R e tamanho do banco em `decisoes.md`).
5. F2 real (B4): token do Hugging Face; o primeiro job mede RTF e RSS por etapa e a sonda Q8; "OMP: Error #15" pede o
   torch antes do CTranslate2 (como no ao vivo); G2 nas gravações do G1.
6. F3 real (B13): chave do Gemini, "testar conexão", primeiro job de LLM (canário, latência, preço contra a fatura); G3.
7. F4 (B15): `python scripts/live_probe.py all <gravação real>` (com `HF_TOKEN`); a regra da janela muda se o Windows
   divergir do Linux; "Preparar modelos do ao vivo"; gravação com a legenda: atraso, CPU (o processo ao vivo sem "modo
   de eficiência" no Gerenciador de Tarefas) e nomes; G4 em três reuniões pelo `--check`. **Sem o dono, 25/10:**
   `live_probe` sem token no venv do F0 com o áudio TTS (igual ao Linux: a regra da janela fica; `decisoes.md`); `small` e
   `base` já na pasta de modelos do app. Falta: o embedding do wespeaker (`HF_TOKEN`), a gravação real e a legenda.
8. F5: **sem o dono, 25/10:** `rag_probe fts` e `sig` (FTS5 ok no SQLite 3.49.1; `decisoes.md`); a v8 numa cópia de um
   banco de 24/10 (65 ms, nada perdido) e o `--check` nela; `.md` com o destino aberto por outro processo: falha segura
   com WinError 5 (o anterior fica, nenhum temporário sobra), mas a mensagem é técnica. Do R7 e do R8, só o real
   verifica: o `underflowed` no MME; se 0,5 s basta para o MME descartar o bloco em voo antes de o loopback do sistema
   abrir (o log avisa "não confirmou a pausa"); o Q9 com o loopback do app falhando e caindo para o sistema; a saída com
   o glossário preso na rede real; um dispositivo de 44,1 kHz; o primeiro som do processo (import a frio do
   `scipy.signal`, ~1,5 s). Com o dono: `gemini` com `GEMINI_API_KEY` (B13: latência por lote, dimensão, cosseno e
   recall reais); v8 no banco real (conversas preservadas, tempo); primeira abertura (varredura das reuniões antigas:
   tempo e custo; vetores até 1 min depois da ata); o leve lançado pelo pesado e pelo app; espera de 30 s das edições;
   vetor da consulta atrás do proxy e do EDR (meta de 1,5 s da híbrida); chat global com o Gemini real (obedece
   `[R1 12:03]`?); player no MME (taxa, saída mono, latência, `underflowed` no log com e sem o pesado, posição até 1 s antes
   da fala); Q9 numa gravação do sistema inteiro; atalho `Ctrl+Alt+Shift+H` e foco; clique nos toasts do Windows 11 e na
   central; DPI; .md salvo com o arquivo aberto num editor e em Documentos no OneDrive; colar no Outlook, no Teams e no
   Bloco de Notas; saída do app depois de copiar e no logoff; `python -m listener --check` e o G5 com 5 ou mais reuniões
   reais.

## Dívidas técnicas
- Saúde dos canais começa "ok" por até 10 s sem sinal; decidir se R avisa antes quando nunca recebeu sinal.
- Deriva de relógio: só instrumentada (A7); corrigir em spec própria se o G1 mostrar mais de 100 ms por hora (P22).
- Textos da interface fixos em pt-BR; prever i18n (`tr()` do Qt ou gettext) antes da F6 (CP15).
- Pasta de modelos ainda não está nas Configurações (entra na F2).
- Testes de interface só cobrem abrir e fechar as telas (pytest-qt offscreen); interação real fica para a F6.
- O eco do warm-up é medido só no "Testar 3 s", não de forma contínua; o `--check` dá o número preciso.
- F5: `_fail` de um job cuja reunião foi apagada levanta IntegrityError e derruba o worker (F5A-17, para a F6, junto
  com apagar reunião); o filtro de pessoa varre `rag_chunks` (~35 ms a cada 20 mil trechos) e `vector_version` conta o
  índice a cada busca: medir com o banco real.
- Suíte acima da meta de 8 s: ~22 s no Linux de desenvolvimento no L5 da F5 (348 testes; ~17,5 s com 270 na F4; o Xeon
  de 2,1 GHz pesa) e ~45 s no Windows (366 testes depois do R8, 25/10; os de tempo real do player e os dois scripts de
  saída somam ~8 s). Separar os testes de subprocesso e os de interface em jobs próprios e ver o custo do `gc.collect()`
  por teste de interface.
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
precisão dos perfis, desvio do clique e legibilidade (G2) · F4 no Windows (B15): embedding por janela (o resto do
`live_probe` medido em 25/10); G4: atraso médio e p95, CPU, acerto do nome · F5 no Windows (item 8): `rag_probe gemini`
(por lote), varredura da primeira abertura, híbrida com o vetor da consulta, `underflowed` do player; G5: citações
certas em 10 perguntas.
