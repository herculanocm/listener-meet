# F7 — Linux e macOS (futuro, CP15)

Status: futuro (sem data; só depois do G6)
Cláusulas pétreas envolvidas: CP15
Quando ler: ao detalhar ou implementar esta fase (T2). Tabelas: `dados.md`.

## Escopo previsto
- `listener/platform/linux/`: microfone via sounddevice (PulseAudio/PipeWire), loopback do sistema pelos
  dispositivos "monitor", loopback por app via proc-tap (PipeWire/PulseAudio), mudo via `pactl`, processos
  via `/proc`, pastas XDG (já existem em `platform/common/paths.py`).
- `listener/platform/macos/`: microfone via sounddevice (CoreAudio), loopback por app via proc-tap
  (ScreenCaptureKit, exige permissão de gravação de tela), loopback do sistema (BlackHole ou equivalente,
  a decidir), mudo via CoreAudio, pastas em Library/Application Support.
- Atalhos (pynput), bandeja e interface (PySide6), banco, pipeline e LLM já são portáveis.
- Empacotamento por sistema e testes da plataforma simulada em cada um.

## Perguntas para quando chegar a hora
- Loopback do sistema no macOS sem driver extra é limitado; aceitar dependência de um driver virtual open source?
