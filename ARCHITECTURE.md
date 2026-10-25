# ARCHITECTURE.md — listener-meet v4

Quando ler: ao mexer em produto ou arquitetura (T1). Fonte da verdade: `specs/00-handoff-v4.md`; em conflito, ele vence.
Estado: `specs/backlog.md`. Decisões: `specs/decisoes.md`. Tabelas: `specs/dados.md`. Tamanho-alvo: 4,5 KB.

Em uma frase: app desktop, pessoal e local (Windows primeiro; Linux e macOS depois, CP15) que grava dois canais de áudio
de qualquer reunião, identifica quem fala por perfil de voz (pyannote), transcreve (faster-whisper ou WhisperX), gera
legenda, resumo e ata com uma LLM trocável (Gemini inicial) e guarda tudo em SQLite para consulta por RAG. Tudo em CPU.

## Camadas
| Camada | Pacote | Responsabilidade | Fase |
| --- | --- | --- | --- |
| Plataforma | `listener/platform/` | Tudo que depende do SO atrás de `base.py`: dispositivos, apps tocando, mic, loopback de app e de sistema, mudo, atalhos, processos, pastas. `common/` (pastas, pynput para Linux e macOS); `win32/` (sounddevice WASAPI, proc-tap, PyAudioWPatch, pycaw, `RegisterHotKey`, Toolhelp32, identidade do processo, suspensão e foco); `linux/` e `macos/` na F7. Fora daqui não há pycaw, PyAudioWPatch, `ctypes.windll` nem `C:\` | F1, F7 |
| Captura | `listener/capture/` | Dois canais num relógio único (`perf_counter`, QPC no Windows): L = mic 16 kHz; R = loopback do app (árvore de processos) ou do sistema. Mudo e pausa gravam silêncio; buracos preenchidos por timestamp; pedaços FLAC de ~60 s gravados no SQLite por uma thread de trabalho | F1 |
| Transcrição | `listener/pipeline/` | `JobRunner` em janelas de 600 s cortadas no silêncio; faster-whisper large-v3-turbo int8 com `word_timestamps` (D-028; wav2vec2 opcional), glossário e contexto no `initial_prompt`, idioma por reunião (D-013); palavras viram segmentos por turno; artefato por janela e retomada. Ao vivo (`listener/live/`, D-044): processo próprio com VAD Silero incremental, janelas de 4 a 10 s e `small` int8 (degrada para `base`) | F2, F4 |
| Identificação | `listener/pipeline/` | L = usuário (salvo preset sala, U-015). R: pyannote community-1 em janelas de até 20 min (`pipeline.diar_window_s`, 1200 s) costuradas; centroide por cluster e atribuição sem repetição contra perfis persistentes (limiar 0,60 calibrado por perfil, margem 0,05, D-008); a revisão mínima reforça o perfil a cada confirmação. Ao vivo: `match_one` por janela contra os perfis, só exibição (CP11) | F2, F4 |
| Inteligência | `listener/llm/`, `pipeline/llm_steps.py` | Contrato único de provedor (`base.py`, `types.py`; texto, JSON estrito validado pelo projeto, áudio, embeddings, streaming) com adaptadores Gemini, GPT e Claude importados preguiçosamente pelo `registry` (retentativa própria, `max_retries = 0`, proxy, PEM e truststore, chave redigida); prompts, esquemas e render da ata do projeto, versionados. Job `kind = llm` encadeado ao fim do pipeline e rodado pelo worker leve: identidade por contexto só nas vozes pendentes (CP11), ata com evidência conferida, executivo, pré-resumo, título e transcrição revisada; glossário no warm-up, chat com citações clicáveis e `LLMAudioTranscriber` (D-020) no app; resumo parcial ao vivo no app a cada N minutos (F4). RAG na F5 | F3, F4 |
| Base | `listener/store/` | `listener.db` mais um `listener-audio-AAAA-MM.db` por mês, anexado sob demanda (D-003, D-039), WAL; conexão por thread, migrações atômicas por arquivo, `Store` como fachada dos repositórios por domínio com papéis APP, WORKER e READONLY; fila com claim atômico e artefatos de job no banco; FTS5 e sqlite-vec (D-024); modelo e dimensão por vetor (D-009) | F1, F2, F5 |
| Interface | `listener/ui/` | PySide6: assistente de primeiro uso, warm-up, widget flutuante, bandeja, resumo, configurações mínimas, janela "Ao vivo" (F4); janela principal completa na F6 | F1, F4, F6 |
| Config | `listener/config/` | Padrões das settings e o ponteiro da pasta do banco (D-029) | F1 |
| Log | `listener/log.py` | stderr sempre; arquivo rotativo escolhido pelo usuário, nível configurável (D-033) | F1.1 |

## Fluxo de uma reunião (handoff §4)
1. Warm-up: mic, fonte (app tocando ou sistema), medidores, teste; título e participantes opcionais; a LLM monta glossário
   e lista de perfis candidatos.
2. Gravação: widget com tempo, medidores, saúde dos canais, pausar, marcador com nota, mudo global (D-004), parar,
   cancelar. Ao vivo (F4): legenda leve e resumo parcial a cada N minutos.
3. Processamento (fila automática ao parar, D-014; subprocesso): Whisper em L e R, pyannote em R, cosseno contra perfis.
   Acima do limiar, nome direto; abaixo, a LLM propõe por contexto; sem pista, sem nome. A LLM gera título, pré-resumo,
   ata, resumo executivo e transcrição revisada; nunca sobrescreve alta confiança.
4. Revisão: título, pré-resumo e falantes de baixa confiança com trecho de áudio; cada confirmação reforça o perfil; o
   banco guarda a versão da LLM e a editada.
5. Histórico, perfis e chat com RAG citando [mm:ss]; clique toca o áudio.

## Processos e threads
App Qt na thread principal; parada, mudo do sistema e atalhos em QThreads próprias. Callbacks de áudio só enfileiram
blocos imutáveis; uma thread de trabalho (não daemon) faz mono, reamostragem com estado, pedaços, FLAC e grava com
retentativa, publicando um snapshot imutável para a interface; uma conexão SQLite por thread (F1.2). F2: worker em
subprocesso de prioridade baixa que lê e escreve só no banco (WAL, `busy_timeout`). `from __future__ import annotations`
fica em todo módulo até o piso subir para 3.14 (PEP 649); sai em bloco. F2: ao parar, o app cria o job e lança `python
-m listener --worker` solto (`Platform.spawn_detached`, prioridade baixa, log próprio); o worker faz o claim atômico com
heartbeat em thread própria, roda as etapas em janelas com artefato por janela, lê a lease da gravação e o cancelamento
entre janelas e sai após 60 s ocioso; a interface só lê a fila por polling (2 s com job ativo, 30 s sem), nunca importa
torch e decodifica áudio sob demanda numa `Task` (QThread). F3: segunda classe de fila com um worker leve (`python -m
listener --worker --light`), sem torch, log próprio (`listener-llm.log`), lançado pelo app mesmo durante a gravação (ou
pelo worker pesado ao enfileirar); o `LlmJobRunner` roda as tarefas num `asyncio.run()` com `TaskGroup` e `Semaphore`
(`llm.max_parallel`), cliente de rede criado e fechado dentro do loop, nunca `await` dentro de transação; chat e
glossário rodam no app numa `Task`/`StreamTask` com `run_async()` próprio (um `asyncio.run` que não espera o executor
padrão na saída). F4: terceira classe de processo, `python -m listener --live` (não consome a fila; isento da regra "o
pesado não sobe gravando"), lançado ao gravar por `Platform.spawn_child` (pipes binários, prioridade abaixo do normal,
sem janela; o próprio filho desliga o EcoQoS) e preso à gravação: o desvio de PCM do chunker (O(1), na thread de
trabalho da captura) enche um deque de ~30 s por canal, `live-feed` escreve quadros binários no stdin, `live-read` lê
linhas JSON do stdout e as vira sinal do Qt; o filho grava `captions` (papel WORKER) e log próprio
(`listener-live.log`); EOF encerra; com ele, a lease pausa o pesado. O resumo parcial roda no app numa `StreamTask`, uma
chamada por vez.

## Stack
Python 3.12 ou mais novo, sem retrocompatibilidade (CP17; teto `<3.14` só enquanto WhisperX e torch exigirem) · PySide6
· sounddevice · proc-tap · PyAudioWPatch e pycaw (só win32) · soundfile · scipy · faster-whisper ou WhisperX
(CTranslate2 int8) · pyannote.audio 4 (community-1, token do Hugging Face) · SQLite com FTS5 e sqlite-vec ·
google-genai, openai, anthropic · pynput · empacotamento: venv e atalho; PyInstaller só se necessário. Versões medidas:
`memory/maquina.md`.

## Riscos de desenho
Fala sobreposta no canal R; CPU dividida com o app de reunião; banco cresce 5 a 6 GB por mês a 16 kHz (D-015; indicador
e backup na F6); codecs diferentes por plataforma deslocam embeddings (F2 Q9); chaves em texto aberto (banco fora do
OneDrive e do git). Documentos vivos: handoff https://claude.ai/artifact/L9q6Yi1znE3cxCCJLkd6YM e plano v4
https://claude.ai/artifact/8tvkwjZZhH4RwtmfYjjNUt
