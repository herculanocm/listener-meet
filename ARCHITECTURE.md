# ARCHITECTURE.md — listener-meet v4

Quando ler: ao mexer em produto ou arquitetura (T1). Fonte da verdade: `specs/00-handoff-v4.md`; em conflito, ele vence.
Estado: `specs/backlog.md`. Decisões: `specs/decisoes.md`. Tabelas: `specs/dados.md`. Tamanho-alvo: 4,5 KB.

Em uma frase: app desktop, pessoal e local (Windows primeiro; Linux e macOS depois, CP15) que grava dois canais de áudio
de qualquer reunião, identifica quem fala por perfil de voz (pyannote), transcreve (faster-whisper ou WhisperX), gera
legenda, resumo e ata com uma LLM trocável (Gemini inicial) e guarda tudo em SQLite para consulta por RAG. Tudo em CPU.

## Camadas
| Camada | Pacote | Responsabilidade | Fase |
| --- | --- | --- | --- |
| Plataforma | `listener/platform/` | Tudo que depende do SO atrás de `base.py`: dispositivos, apps tocando, mic, loopback de app e de sistema, mudo, atalhos, processos, pastas. `common/` (pastas, pynput); `win32/` (sounddevice WASAPI, proc-tap, PyAudioWPatch, pycaw, Toolhelp32); `linux/` e `macos/` na F7. Fora daqui não há pycaw, PyAudioWPatch, `ctypes.windll` nem `C:\` | F1, F7 |
| Captura | `listener/capture/` | Dois canais num relógio único (`perf_counter`, QPC no Windows): L = mic 16 kHz; R = loopback do app (árvore de processos) ou do sistema. Mudo e pausa gravam silêncio; buracos preenchidos por timestamp; pedaços FLAC de ~60 s gravados no SQLite por uma thread de trabalho | F1 |
| Transcrição | `listener/pipeline/` | faster-whisper large-v3-turbo int8 com `word_timestamps` (D-028); wav2vec2 opcional; glossário no `initial_prompt`; idioma por reunião (D-013). Ao vivo: VAD e janelas de ~8 s (D-005) | F2, F4 |
| Identificação | `listener/pipeline/` | L = usuário (salvo preset sala, U-015). R: pyannote community-1 diariza; embedding por cluster; cosseno contra perfis persistentes (centroide por pessoa, limiar 0,60 com margem e autocalibração, D-008); confirmações reforçam o perfil | F2 |
| Inteligência | `listener/llm/` | Interface única de provedor (texto, JSON, áudio, embeddings) com adaptadores Gemini, GPT e Claude; prompts e esquemas do projeto. Glossário, resumo parcial, identidade por contexto abaixo do limiar, título, pré-resumo, ata, resumo executivo, transcrição revisada, chat e RAG | F3 |
| Base | `listener/store/` | `listener.db` e `listener-audio.db` anexados (D-003), WAL; FTS5 e sqlite-vec (D-024); modelo e dimensão por vetor (D-009) | F1, F5 |
| Interface | `listener/ui/` | PySide6: assistente de primeiro uso, warm-up, widget flutuante, bandeja, resumo, configurações mínimas; janela principal completa na F6 | F1, F6 |
| Config | `listener/config/` | Padrões das settings e o ponteiro da pasta do banco, único arquivo fora do SQLite (D-029) | F1 |

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
App Qt na thread principal. Callbacks de áudio só copiam para os chunkers. Uma thread de trabalho faz mono,
reamostragem, FLAC e grava no banco. F2: worker em subprocesso de prioridade baixa que lê e escreve só no banco (WAL,
`busy_timeout`).

## Stack
Python 3.12 (um venv) · PySide6 · sounddevice · proc-tap · PyAudioWPatch e pycaw (só win32) · soundfile · scipy ·
faster-whisper ou WhisperX (CTranslate2 int8) · pyannote.audio 4 (community-1, token do Hugging Face) · SQLite com FTS5 e
sqlite-vec · google-genai, openai, anthropic · pynput · empacotamento: venv e atalho; PyInstaller só se necessário.
Versões medidas: `memory/maquina.md`.

## Riscos de desenho
Fala sobreposta no canal R; CPU dividida com o app de reunião; banco cresce 5 a 6 GB por mês a 16 kHz (D-015; indicador
e backup na F6); codecs diferentes por plataforma deslocam embeddings (F2 Q9); chaves em texto aberto (banco fora do
OneDrive e do git). Documentos vivos: handoff https://claude.ai/artifact/L9q6Yi1znE3cxCCJLkd6YM e plano v4
https://claude.ai/artifact/8tvkwjZZhH4RwtmfYjjNUt
