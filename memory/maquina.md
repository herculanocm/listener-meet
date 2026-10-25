# Ambientes: Windows 11 corporativo (EDR, sem GPU; o alvo), o WSL dele e um Linux Ubuntu nativo

Tudo [fato] medido em 24/10/2026, salvo indicação. Caminhos com `<usuário>` são desta máquina, não do produto.
Onde estou (começo de sessão, `AGENTS.md`): `sys.platform` `win32` = Windows; `linux` com `microsoft` em
`platform.release()` (e `/mnt/c`) = WSL; `linux` sem isso = Linux nativo (em 25/10, o Xeon abaixo).

## Ambiente
- Windows: Python 3.12.10 (`py install 3.12`, sem admin, em `%LOCALAPPDATA%\Python\pythoncore-3.12-64`; o `python` do
  PATH é o alias do instalador em WindowsApps e abre esse mesmo); checkout em `C:\dev\listener-meet` com o venv do projeto
  em `.venv` dentro dele (`[dev,win32,llm]`, sem ML, 25/10); o venv do F0 em `C:\Users\<usuário>\dev\listener-meet\.venv`
  tem torch, whisperx, pyannote e sqlite-vec, mas não o projeto; modelos em `C:\Users\<usuário>\listener-meet\models\`;
  áudio sintético de calibração (15 min, duas vozes TTS, pt-BR e en, gerado por `gerar.ps1`) em
  `C:\Users\<usuário>\listener-meet\f0\`. SQLite do Python do python.org: 3.49.1 com FTS5 (25/10).
- CPU Intel Core Ultra 5 235U (família 6, modelo 181): 12 núcleos, 14 threads, 2,0 GHz base; 31,5 GB de RAM; torch
  2.8.0+cpu com 12 threads; sem CUDA. Suíte: ~35 s com 349 testes (50 s a frio, 25/10). Importar os SDKs a quente
  (`-X importtime`): google.genai 0,8 s, openai 0,9 s, anthropic 4,0 s.
- Versões: whisperx 3.8.6, faster-whisper 1.2.1, ctranslate2 4.8.3, pyannote.audio 4.0.7, PySide6 6.12.0, sqlite-vec 0.1.9
  (SQLite 3.49.1), proc-tap 1.1.1, sounddevice 0.5.6, soundfile 0.14.0, PyAudioWPatch 0.2.12.9, pynput 1.8.2,
  pycaw 20260927, google-genai 2.33.0, openai 3.37.0, anthropic 2.3.0.
- Linux de desenvolvimento: em 25/10 a sessão rodou num Ubuntu 24.04 **nativo** (Xeon Silver 4208 de 2,1 GHz, 32 threads,
  18 GB de RAM, ~6,7 GB livres em disco, sem `/mnt/c`), não no WSL; a suíte fica mais lenta pelo clock baixo (270 testes
  em ~17,5 s depois da F4).
  Para medir o Whisper ou o filho `--live` real, use um venv à parte com faster-whisper 1.2.1 (sem torch). O de 25/10
  ficou na pasta temporária da sessão e sumiu: recriar com `python3 -m venv <pasta>` e `pip install
  faster-whisper==1.2.1 soundfile scipy`. No `.venv` do projeto ele quebraria o teste do worker "real" (que baixaria o
  `large-v3-turbo`).
- WSL (Ubuntu 24.04): `.venv/` com Python 3.12.3, numpy, scipy, soundfile, pytest, ruff, mypy, pytest-qt, PySide6 e, desde
  a F3, google-genai 2.33, openai 3.37, anthropic 2.3, truststore e httpx nas versões do Windows: roda os 193 testes em ~7 s
  (interface em `QT_QPA_PLATFORM=offscreen`; `LISTENER_SDK_TESTS=1` liga os de transporte simulado dos SDKs, sem rede) e
  `scripts/ui_smoke.py --fake`; não roda o selftest, o app de verdade nem torch. `uv` não está instalado. A sessão do
  Claude Code guarda relatórios de agentes em `/tmp/claude-1000/...` (não sobrevivem à troca de sessão).
- Git: reiniciado limpo em 24/10 (D-032) e primeiro commit no mesmo dia; branch `main`. Remoto `origin` no GitHub,
  **público** (visto em 25/10): o dono faz o push quando quer; o agente só commita. Tudo que entra num commit vira
  público (CP10, CP15).

## Áudio e processos (medido com o protótipo v3 e o selftest da F1)
- proc-tap captura um processo e sua árvore (`INCLUDE_TARGET_PROCESS_TREE`): 48 kHz, 2 canais, float32; primeiro pacote em
  ~40 ms; o callback passa `frames = -1` (contar pelos bytes, 8 por frame) e entrega ~10 ms por chamada (juntar ~100 ms).
  Backends por SO (PyPI 1.1.1): Windows WASAPI 20H1+; Linux PipeWire ou PulseAudio por processo; macOS 13+ via
  ScreenCaptureKit, por bundle id.
- Loopback WASAPI (de processo e de endpoint) não entrega nenhum pacote em silêncio: 8 s sem áudio = 0 callbacks.
  Preencher por timestamp e até o fim da sessão ao parar.
- Microfone: o padrão do PortAudio no Windows é MME (44,1 kHz, sem timestamp do ADC); abrir o mesmo mic pela host API
  "Windows WASAPI" com `WasapiSettings(auto_convert=True)` a 16 kHz mono; aí `inputBufferAdcTime` funciona.
- psutil leva ~4 s para enumerar processos (o EDR gancha `OpenProcess`); snapshot Toolhelp32 via ctypes leva ~30 ms.
  pycaw `GetAllSessions()` usa psutil em `session.Process`; a sessão expõe `ProcessId` (F1.1 A4).
- Dispositivos vistos: mic "Grupo de microfones (Tecnologia Intel Smart Sound...)" (WASAPI); loopback "Altofalantes
  (Realtek(R) Audio) [Loopback]" (48 kHz, 2 canais).
- Console do Windows não é UTF-8 por padrão: `sys.stdout.reconfigure(encoding="utf-8")` (feito em `__main__.py`).
- ctypes no Windows de 64 bits: sem `restype`, `GetCurrentProcess()` volta `c_long` (-1) e o pseudo-handle chega
  truncado (0xFFFFFFFF), com erro 6 (handle inválido). Sempre `WinDLL` próprio com `restype` e `argtypes` em `HANDLE` (25/10).
- Selftest da F1 (mic + loopback do sistema): L e R contínuos, desvio de 19 ms na duração, sem buracos; cinco atalhos
  registram; sessões de áudio e mudo do mic respondem.

## Modelos e bibliotecas
- Cache do Hugging Face usa symlink e falha no Windows sem Modo de Desenvolvedor (`WinError 1314`), mesmo no
  huggingface_hub 0.36.2: baixar com `local_dir` (`faster_whisper.download_model(size, output_dir=...)`,
  `snapshot_download(repo, local_dir=...)`) e carregar pelo caminho local (`whisperx.load_model(<pasta>)`,
  `load_align_model(..., model_name=<pasta>)`). Vale para o pyannote.
- torchcodec não carrega sem FFmpeg: a decodificação embutida do pyannote falha e `whisperx.load_audio` chama o ffmpeg.
  Decodificar com soundfile e passar `{"waveform": tensor (1, N), "sample_rate": 16000}` ao pyannote e numpy float32 ao
  Whisper (D-025).
- WhisperX carrega em 18 s; faster-whisper direto em 3,5 s. Tempos por hora de áudio: `specs/decisoes.md` (Medições).
- Model card do pyannote community-1 é gated: sem token o README não vem; a página pública mostra
  `output.speaker_diarization` e `exclusive_speaker_diarization`, não `speaker_embeddings` (F2 Q8).
- Python 3.14 instala a captura, mas WhisperX exige < 3.14 e torch ~2.8 (D-019). PySide6 6.12 tem wheel abi3.

## Atalhos e janelas (verificado em 24/10 em documentação pública)
- `Ctrl+Shift+M` muta o Teams; `Ctrl+Shift+R` recarrega a aba em Chrome, Edge e Firefox; `Ctrl+Shift+S` é captura no
  Edge; `Ctrl+Shift+P` imprime em Chrome e Edge; `Ctrl+Alt+letra` é AltGr+letra no ABNT2. pynput `GlobalHotKeys` não
  engole a tecla; `RegisterHotKey` é exclusivo e consome (F1.1 A1).
- Ferramentas de IA: ler arquivos grandes em lotes menores que 30 KB (limite de saída do Bash no Claude Code). Agente do
  Claude Code com worktree isolado nasce do `origin/main`, não do `main` local (25/10): para um revisor ler commits
  locais enquanto outro agente escreve, extrair `git archive <sha>` numa pasta à parte e rodar com `PYTHONPATH` nela (o
  pacote está instalado em modo editável apontando para o diretório principal).
- No WSL, `ruff`, `mypy` e `pytest` só existem no venv: `source .venv/bin/activate` antes (25/10).
