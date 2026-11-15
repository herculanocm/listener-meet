# Ambiente de desenvolvimento e armadilhas conhecidas

Quando ler: ao montar o ambiente, medir algo ou mexer em áudio, processos, modelos e interface no Windows. Tudo é
[fato] medido entre 2026-10-24 e 2026-11-08, salvo indicação. Regras desta pasta: só o que não está em spec nem em
código; fato com data ou fonte; nenhum segredo; o que for superado é apagado. Tamanho-alvo: 9 KB.

## Máquinas
- **Windows 11 (o alvo).** Corporativo, com EDR. Intel Core Ultra 5 235U (12 núcleos, 14 threads, 2 de desempenho),
  31,5 GB de RAM. GPU: só a Intel Graphics integrada e a NPU; sem NVIDIA e sem CUDA (`torch.cuda.is_available()`
  falso; onnxruntime só com `CPUExecutionProvider`). Python 3.12.10 pelo `py -3.12`; o venv do projeto fica em `.venv`,
  no checkout, instalado pelo lock. Modelos em `%LOCALAPPDATA%\listener-meet\models` (large-v3-turbo, `small`, `base`).
  Monitores mudam com o lugar (4K a 150 %, 1080p a 100 %, notebook a 150 %). O terminal padrão é o Windows Terminal.
- **Linux de desenvolvimento.** Ubuntu 24.04 nativo ou WSL, sem PortAudio, sem PyInstaller e sem sudo. Venv com
  `pip install -c requirements/windows.txt -e ".[dev,llm,docx]"`, sem o extra `[pipeline]`. A suíte inteira tem ~13
  falhas do ambiente (o build do `.exe`, o widget 1 px mais alto no offscreen, os tempos dos avisos e do cartão de
  chamada, as cores do estilo, o cabeçalho dos preços cortado, o app que sai com o sensor preso): a régua de cada
  lote é nenhuma falha nova. Para medir o Whisper ou o `--live` de verdade, use um venv à parte com
  `faster-whisper==1.2.1 soundfile scipy`, sem torch.
- **Git.** Branch `main`; o remoto `origin` é um repositório público no GitHub: tudo o que entra num commit vira
  público. O dono faz o push.

## Versões travadas (lock do Windows)
torch 2.8.0+cpu · faster-whisper 1.2.1 · ctranslate2 4.8.3 · pyannote.audio 4.0.7 · onnxruntime 1.31.0 · PySide6 6.12.0
· SQLite 3.49.1 com FTS5 · proc-tap 1.1.1 · sounddevice 0.5.6 · soundfile 0.14.0 · PyAudioWPatch 0.2.12.9 ·
pycaw 20260927 · pynput 1.8.2 · google-genai 2.33.0 · openai 3.37.0 · anthropic 2.3.0. Importar os SDKs a quente:
google.genai 0,8 s, openai 0,9 s, anthropic 4,0 s.

## Áudio no Windows
- O proc-tap captura um processo e a árvore dele a 48 kHz, 2 canais, float32. O callback passa `frames = -1` (conte
  pelos bytes, 8 por frame) e entrega ~10 ms por chamada.
- O loopback de processo pega o processo e os filhos diretos, não os netos. O Teams novo toca a chamada num WebView2
  neto do `ms-teams.exe` raiz; WhatsApp e Outlook novo também usam WebView2. Por isso a captura soma a raiz com as
  sessões de áudio mais fundas (D-055).
- O loopback WASAPI não entrega nenhum pacote em silêncio: preencha pelo timestamp, e até o fim da sessão ao parar.
- O padrão do PortAudio no Windows é o MME (44,1 kHz, sem timestamp do ADC). Abra o microfone pela host API "Windows
  WASAPI" com `WasapiSettings(auto_convert=True)` a 16 kHz mono; aí o `inputBufferAdcTime` funciona. O primeiro pacote
  do microfone pode chegar ~380 a 900 ms depois do início.
- O PortAudio lê os dispositivos só ao iniciar. Reiniciá-lo sem stream aberto leva ~61 ms; a assinatura dos
  endpoints pelo Core Audio (pycaw) leva ~19 ms.
- O `GetAllSessions()` do pycaw usa o psutil e só vê a saída padrão; a sessão expõe o `ProcessId`.

## Processos e console no Windows
- O psutil leva ~4 s para enumerar processos (o EDR intercepta o `OpenProcess`); um snapshot Toolhelp32 por ctypes,
  ~30 ms.
- ctypes em 64 bits: sem `restype`, um handle volta truncado; sempre um `WinDLL` próprio, com `restype` e `argtypes`
  em `HANDLE`.
- Um processo de console que nasce sem console para herdar abre uma janela preta do Windows Terminal; fechá-la mata o
  processo com 0xC000013A, sem log. Só o `CREATE_NO_WINDOW`, sem `DETACHED_PROCESS`, dá um console sem janela (D-056).
- O console não é UTF-8 por padrão: `sys.stdout.reconfigure(encoding="utf-8")`, feito em `__main__.py`.
- `os.replace` sobre um arquivo aberto por outro processo (o Word, por exemplo) dá WinError 5.
- Um aperto de mão TLS já ficou preso 52 min dentro da verificação de certificado do Windows (truststore), de forma
  pontual. O vigia do laço corta em 120 s (D-058).

## Modelos e bibliotecas
- O cache do Hugging Face usa symlink e falha no Windows sem o Modo de Desenvolvedor (WinError 1314): baixe com
  `local_dir` (`faster_whisper.download_model(size, output_dir=...)`, `snapshot_download(repo, local_dir=...)`) e
  carregue pelo caminho local. Vale para o pyannote.
- O torchcodec não carrega sem o FFmpeg: decodifique com o soundfile e passe ao pyannote
  `{"waveform": tensor (1, N), "sample_rate": 16000}` e, ao Whisper, numpy float32 (D-025).
- O WhisperX carrega em 18 s; o faster-whisper direto, em 3,5 s.
- O model card do pyannote community-1 é restrito: sem o token, nada vem.
- O Python 3.14 instala a captura, mas o WhisperX exige menos que 3.14 e o torch ~2.8. O PySide6 6.12 tem wheel abi3.

## Interface (Qt)
- A plataforma `offscreen` não tem as fontes do sistema (cai num "Sans Serif" cerca de duas vezes mais largo): meça
  layout só com a plataforma real. Uma caixa modal aberta no `closeEvent` trava o encerramento do teste.
- Para capturar uma tela no DPI real sem aparecer para o usuário: janela em (-10000, -10000) com
  `WA_ShowWithoutActivating` e `grab()` (`scripts/win_probe.py shots`). Com `WA_DontShowOnScreen`, o diálogo sai em
  DPR 1.
- A área de transferência só sobrevive à saída quando o app sai pelo laço de eventos.
- Um diálogo num ciclo de referências, coletado pelo GC em outra thread, derruba o processo: `deleteLater()` no
  código e `gc.collect()` nos testes. Um `QMimeData` criado pelo Python derrubava o app na saída: ele nasce no C++.
- Um widget sem dono, preso por um lambda com `self` numa conexão, derruba a saída do processo (0xC0000409): use
  método, não lambda (D-062).

## Atalhos
- `Ctrl+Shift+M` muta o Teams; `Ctrl+Shift+R` recarrega a aba nos navegadores; `Ctrl+Alt+letra` é AltGr+letra no
  teclado ABNT2. Por isso os atalhos do app são `ctrl+alt+shift+letra`.
- O `GlobalHotKeys` do pynput não engole a tecla; o `RegisterHotKey` é exclusivo e a consome.

## Ferramentas
- No Git Bash do Windows, `python3` é o atalho da Microsoft Store e trava: use `.venv/Scripts/python.exe`.
- No PowerShell 5.1, um here-string passado a um executável vira argumento: mensagem de commit por arquivo
  (`git commit -F <arquivo>`), gravado em UTF-8 sem BOM.
- Medidas de tempo comparam sempre com a base da fase, com as duas cópias na mesma pasta e `--runs 7`: o ruído do
  Teams e do EDR chega a 40 %.
- Comando longo de medição roda sem `timeout` e com `python -u`.
