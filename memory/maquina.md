# Esta máquina: Windows 11 corporativo (EDR), sem GPU, e o WSL

Tudo [fato] medido em 24/10/2026, salvo indicação. Caminhos com `<usuário>` são desta máquina, não do produto.

## Ambiente
- Windows: Python 3.12.10 (`py install 3.12`, sem admin, em `%LOCALAPPDATA%\Python\pythoncore-3.12-64`); venv em
  `C:\Users\<usuário>\dev\listener-meet\.venv`; modelos em `C:\Users\<usuário>\listener-meet\models\`; áudio sintético de
  calibração (15 min, duas vozes TTS, pt-BR e en, gerado por `gerar.ps1`) em `C:\Users\<usuário>\listener-meet\f0\`.
- CPU Intel 14 núcleos (família 6, modelo 181); torch 2.8.0+cpu com 12 threads; sem CUDA.
- Versões: whisperx 3.8.6, faster-whisper 1.2.1, ctranslate2 4.8.3, pyannote.audio 4.0.7, PySide6 6.12.0, sqlite-vec 0.1.9
  (SQLite 3.49.1), proc-tap 1.1.1, sounddevice 0.5.6, soundfile 0.14.0, PyAudioWPatch 0.2.12.9, pynput 1.8.2,
  pycaw 20260927, google-genai 2.33.0, openai 3.37.0, anthropic 2.3.0.
- WSL (Ubuntu 24.04): `.venv/` com Python 3.12.3 e só numpy, scipy, soundfile e pytest: roda os 21 testes de lógica; não
  roda a interface nem o selftest (sem PySide6 e sounddevice).
- Git: reiniciado limpo em 24/10 (D-032) e primeiro commit no mesmo dia; branch `main`; sem remoto.

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
- Ferramentas de IA: ler arquivos grandes em lotes menores que 30 KB (limite de saída do Bash no Claude Code).
