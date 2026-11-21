# ARCHITECTURE.md — listener-meet

Quando ler: ao mexer em produto ou arquitetura. Cláusulas: `specs/01-clausulas.md`. Decisões: `specs/decisoes.md`.
Tabelas: `specs/dados.md`. Camada de LLM: `specs/llm-contrato.md`. Tamanho-alvo: 12 KB.

## Visão geral
App desktop, pessoal e local (Windows primeiro; Linux e macOS depois, CP15). Ele grava dois canais de áudio de
qualquer reunião, identifica quem fala por perfil de voz (pyannote), transcreve (faster-whisper), gera legenda, resumo,
ata e sugestões com uma LLM trocável e guarda tudo em SQLite, para busca e perguntas. Tudo roda em CPU (CP14).

Princípios de produto:
- Funciona sozinho, no computador do usuário, sem depender de admin, de TI ou de recursos das plataformas de reunião.
- As bibliotecas locais (pyannote e Whisper) fazem o trabalho pesado; a LLM é o cérebro de contexto.
- Tudo é configurável pela interface, a qualquer momento.
- A experiência vem primeiro: toda melhoria de UX aprovada sai pronta de fábrica.

## Camadas
| Camada | Pacote | Responsabilidade |
| --- | --- | --- |
| Plataforma | `listener/platform/` | Tudo o que depende do sistema, atrás de `base.py`: dispositivos, apps tocando, microfone, loopback de app e de sistema, mudo, atalhos, processos, pastas, instância única, captura da tela. `win32/` usa sounddevice (WASAPI), proc-tap, PyAudioWPatch, pycaw, `RegisterHotKey`, Toolhelp32 e ctypes; `common/` tem as pastas e o pynput. Fora daqui não há pycaw, PyAudioWPatch, `ctypes.windll` nem `C:\`. |
| Captura | `listener/capture/` | Dois canais num relógio único (`perf_counter`): L é o microfone a 16 kHz; R é o loopback do app (a árvore de processos) ou do sistema. Mudo e pausa gravam silêncio; buracos são preenchidos pelo timestamp; pedaços FLAC de ~60 s vão para o SQLite por uma thread de trabalho. Os canais se reabrem sozinhos quando um aparelho cai (F5.9). |
| Transcrição | `listener/pipeline/` | `JobRunner` em janelas de 600 s cortadas no silêncio; faster-whisper large-v3-turbo int8 com `word_timestamps` (D-028); glossário e contexto no `initial_prompt`; idioma escolhido por reunião, sem detecção (D-060); um artefato por janela, para retomar. |
| Identificação | `listener/pipeline/` | L é o usuário (salvo no preset de sala). No R, o pyannote community-1 diariza em janelas de até 20 min, costuradas; cada voz é comparada por cosseno com as referências de voz de cada pessoa (limiar 0,60, margem 0,05; D-008, F5.15); a revisão reforça o perfil a cada confirmação. Sem o token do Hugging Face, o job só transcreve (F5.16). |
| Ao vivo | `listener/live/` | Processo próprio (D-044): VAD Silero incremental, janelas de 4 a 10 s, Whisper `small` int8 (degrada para `base`), nome por embedding só para exibição (CP11). |
| Tela gravada | `listener/screen_rec/` | F5.27, sem Qt e sem torch: o laço da gravação (`ScreenRecorder`, 1 a 5 quadros por segundo no relógio da sessão), os pedaços de 60 s em MP4 com H.264 de CPU (`SegmentEncoder`, o PyAV só dentro das funções), o filho `--screen` e o protocolo com o app (`client.py`), o bloco do `--check`. O host Qt é `ui/screen_rec_host.py`. |
| Inteligência | `listener/llm/`, `pipeline/llm_steps.py` | Contrato único de provedor (texto, JSON validado pelo projeto, áudio, imagem, embeddings, streaming) com adaptadores Gemini, OpenAI e Claude carregados sob demanda, e um provedor local de embeddings (e5-small em ONNX). Prompts, esquemas, validação de evidência e render da ata são do projeto. Tarefas: glossário, identidade por contexto, ata, resumo executivo, pré-resumo, título, transcrição revisada, resumo parcial, chats e sugestões ao vivo. |
| Base | `listener/store/` | `listener.db`, um `listener-audio-AAAA-MM.db` e um `listener-screen-AAAA-MM.db` (F5.27) por mês, anexados sob demanda na mesma fila de até 8 por conexão (D-003, D-039), em WAL. Conexão por thread, migrações atômicas, `Store` como fachada dos repositórios com os papéis APP, WORKER e READONLY, fila de jobs com claim atômico e artefatos no banco. |
| Busca | `listener/rag/` | Trechos de 60 a 120 s e itens da ata com nome e cargo; FTS5 obrigatório; vetores float32 com modelo e dimensão por vetor (D-009); busca por assinatura binária em numpy, sem sqlite-vec (D-048); híbrida por RRF com teto por reunião; chat com citações; "perguntar à base" numa sandbox SQL somente leitura (F5.10). |
| Interface | `listener/ui/` | PySide6: assistente de primeiro uso, "Antes de gravar", widget flutuante, bandeja do sistema, bandeja de legendas, revisão de falantes, Histórico, reunião aberta (ata, transcrição, linha do tempo, player, exportação), fila, Configurações e Sobre. |
| Config | `listener/config/` | Padrões e tipos das configurações (`SPECS`) e o ponteiro da pasta do banco (D-029). |
| Log | `listener/log.py` | stderr sempre; arquivo rotativo escolhido pelo usuário, com nível configurável (D-033). |

## Fluxo de uma reunião
1. **Antes de gravar.** Microfone, fonte (um app tocando ou o sistema), medidores e teste; título, participantes e
   objetivo opcionais; a LLM monta o glossário.
2. **Gravação.** Widget com tempo, medidores, saúde dos canais, pausar, marcador com nota, mudo global (D-004), parar
   e cancelar. Ao vivo: legenda, resumo parcial e sugestões do que falar, a pedido.
3. **Processamento.** Começa sozinho ao parar (D-014), num subprocesso: diarização e identificação do R, Whisper em L
   e R. Acima do limiar, o nome é direto; abaixo, a LLM propõe por contexto; sem pista, a voz fica sem nome.
4. **Revisão.** O usuário confirma as vozes de baixa confiança ouvindo um trecho; cada confirmação reforça o perfil.
   O banco guarda a versão da LLM e a editada.
5. **Depois.** Ata, ações e resumo a pedido (D-063); Histórico, busca e chat com citações [mm:ss] que tocam o áudio.

## Processos
| Processo | Comando | Prioridade | Papel |
| --- | --- | --- | --- |
| App | `python -m listener` | normal | Interface, captura, player, chats, sugestões e exportações. Nunca importa `torch`. |
| Worker pesado | `--worker` | abaixo do normal; ociosa durante uma gravação | Diarização, identificação e transcrição. Solto do app (`Platform.spawn_detached`): termina a fila mesmo com o app fechado e sai após 60 s ocioso. |
| Worker leve | `--worker --light` | abaixo do normal | Jobs `llm` e `index`, sem `torch`. Pode rodar durante a gravação. Fica até 10 min ocioso. |
| Ao vivo | `--live` | normal | Legenda. Filho do app (`Platform.spawn_child`, pipes binários, sem janela), preso à gravação: o fim do stdin o encerra. Não consome a fila. |
| Tela | `--screen` | abaixo do normal (rebaixa-se ao nascer) | F5.27: grava o monitor escolhido. Filho do app como o ao vivo, preso à tela da gravação: linhas JSON nos dois sentidos, e o fim do stdin fecha o último pedaço e o encerra. Papel WORKER: anexa só o arquivo da tela, em escrita, sem criar. |

Regras dos processos:
- **Fila.** O app cria o job ao parar e lança o worker. O worker faz o claim atômico, bate o heartbeat numa thread
  própria e grava um artefato por janela. A interface só lê a fila por polling (2 s com job ativo, 30 s sem).
- **Lease da gravação.** Enquanto há gravação, o worker pesado pausa. Ele confere a lease, a pausa manual e o
  cancelamento dentro da janela (`in_window_checkpoint`, chamado pelo gancho do pyannote a cada lote e pelo gerador do
  Whisper a cada segmento) e cai para a classe ociosa enquanto a lease vale (F5.18).
- **Worker leve.** O `LlmJobRunner` roda as tarefas num `asyncio.run()` com `TaskGroup` e `Semaphore`
  (`llm.max_parallel`). O cliente de rede nasce e morre dentro do laço. Nunca há `await` dentro de transação. Um vigia
  derruba o worker se o laço ficar parado por 120 s (D-058), e o app o relança. O job `index` roda depois dos `llm`,
  pede os vetores em lotes fora de transação e os adia enquanto há gravação.
- **Ao vivo.** O desvio de PCM do chunker enche um deque de ~30 s por canal; a thread `live-feed` escreve quadros no
  stdin do filho e a `live-read` lê linhas JSON do stdout e as vira sinal do Qt. O filho grava `captions` (papel
  WORKER), calcula o embedding do nome numa thread própria enquanto o Whisper transcreve e limita os tokens novos pelo
  tamanho do prompt (`live/pacing.py`).
- **Tela.** O app cria o arquivo da tela do mês e lança o filho com o `t0_perf` da sessão. O filho pede os pixels
  físicos antes de ler os monitores, roda o laço numa thread e grava os pedaços em ordem noutra (`screen-write`), com
  nova tentativa em "database is locked". A parada da reunião espera o filho sair (até 10 s) antes de a reunião entrar
  na fila; a primeira queda relança, a segunda para só a tela.
- **Eco.** Fora do preset de sala, os blocos do L que são eco do R são zerados para o Whisper e para o reforço do
  perfil (`pipeline/echo_gate.py`); a regra é a de `capture/echo.py`, a mesma do ao vivo.
- **Instância única.** Um mutex da plataforma e um canal local entregam ao app que já roda o pedido de um segundo
  processo (F5.13).

## Threads e tarefas no app
- O Qt fica na thread principal. Parada, mudo do sistema e atalhos têm QThreads próprias.
- Os callbacks de áudio só enfileiram blocos imutáveis. Uma thread de trabalho, não daemon, faz mono, reamostragem
  com estado, pedaços, FLAC e gravação com retentativa, e publica um snapshot imutável para a interface.
- Uma conexão SQLite por thread.
- Trabalho que demora roda numa `Task` (QThread) ou, com progresso, numa `StreamTask`: decodificar áudio, busca (com
  contador de geração e cache das assinaturas do modelo ativo), chats, glossário, resumo parcial (uma chamada por vez),
  .docx, exportação de áudio e vídeo (uma por vez no app; espera parada durante uma gravação) e sugestões ao vivo (uma
  por vez, canceláveis, com prazo total de 30 s).
- As tarefas de exportação e de sugestão têm prioridade normal: uma thread de prioridade baixa segurando o GIL
  travaria a interface e a captura.
- O player usa duas threads: a alimentadora decodifica numa fila de até 3 s e a escritora só escreve no
  `OutputStream`. Antes de o gravador abrir o loopback do sistema inteiro, um gancho pausa o player.
- O embedder local é uma sessão ONNX por processo, sob lock, que calcula na thread de quem chama; no app, ele é
  carregado ao abrir o Histórico e solto 10 min depois do último uso.
- A captura da tela para as sugestões (alvo, print e preparo da imagem) roda dentro da tarefa do pedido; a thread da
  interface só lê as configurações.
- Na saída, o app cancela e espera todas as tarefas antes de fechar o banco.
- `from __future__ import annotations` fica em todo módulo até o piso do Python subir para 3.14.

## Logs
Cinco arquivos na pasta do log: `listener.log` (o app), `listener-worker.log` (o pesado), `listener-llm.log` (o leve),
`listener-live.log` (o ao vivo) e `listener-screen.log` (a tela, F5.27). A categoria é o logger `listener.<módulo>`.
| Nível | O que registra |
| --- | --- |
| DEBUG | o detalhe de cada chamada, lote ou decisão; configuração mudada; apps com som, só quando mudam |
| INFO | marcos e ações do usuário: gravação, pausa, mudo, marcador, fonte, janelas, revisão, player, fila, worker lançado ou não e por quê, fim de cada chamada à LLM com latência e tokens, exportação |
| WARNING | o recuperável: job devolvido à fila, chamada que falhou de vez, terminal fechado com o app aberto |
| ERROR | falha: tentativas esgotadas, vigia do laço |

O faulthandler grava a queda nativa num `<log>-crash.log` ao lado de cada log, sem rotação (no mesmo arquivo, o
Windows recusava a rotação; D-072). Nunca vão para o log: nome de pessoa, fala, título de reunião ou de janela,
e-mail, chave, token ou caminho do usuário (D-053, D-056, D-092). Vão ids, contagens, durações, modelos e estados.

## Stack
Python 3.12 ou mais novo (CP17; teto `<3.14` enquanto o WhisperX e o torch o exigirem) · PySide6 · sounddevice ·
proc-tap · PyAudioWPatch e pycaw (só no Windows) · soundfile · scipy · faster-whisper (CTranslate2 int8) ·
pyannote.audio 4 (community-1, com o token do Hugging Face) · SQLite com FTS5 · numpy · google-genai, openai e
anthropic · onnxruntime e tokenizers (busca local) · python-docx · PyAV (exportação de vídeo; vem com o
faster-whisper) · pynput (Linux e macOS) · PyInstaller em pasta para o `.exe` (D-069). As versões travadas estão em
`requirements/windows.txt`.

## Riscos de desenho
- Fala sobreposta no canal R reduz a precisão da diarização.
- A CPU é dividida com o app de reunião: por isso o pesado cede durante a gravação.
- O banco cresce de 5 a 6 GB por mês a 16 kHz (D-015); o indicador de tamanho e o backup ficam para a F6.
- Codecs diferentes por plataforma deslocam os embeddings de voz: as referências por pessoa atenuam (F5.15).
- As chaves ficam em texto aberto no banco (CP10): a pasta do banco fora do git e de pasta sincronizada.
