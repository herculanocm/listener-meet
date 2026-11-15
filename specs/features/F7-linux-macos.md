# F7 — Linux e macOS

Status: futuro (sem data; só depois da F6)
CPs: CP15
Quando ler: ao detalhar ou implementar esta fase. Tamanho-alvo: 2 KB.

## Objetivo
O app grava, processa e mostra reuniões também no Linux e no macOS, com o mesmo código fora da camada de plataforma.

## Escopo previsto
- `listener/platform/linux/`: microfone pelo sounddevice (PulseAudio ou PipeWire); loopback do sistema pelos
  dispositivos "monitor"; loopback por app pelo proc-tap; mudo pelo `pactl`; processos pelo `/proc`; pastas XDG (já
  existem em `platform/common/paths.py`).
- `listener/platform/macos/`: microfone pelo sounddevice (CoreAudio); loopback por app pelo proc-tap
  (ScreenCaptureKit, que exige a permissão de gravação de tela); loopback do sistema por um driver virtual, a decidir;
  mudo pelo CoreAudio; pastas em `Library/Application Support`.
- Já são portáveis: atalhos (pynput), bandeja e interface (PySide6), banco, pipeline e LLM.
- Empacotamento por sistema, e os testes da plataforma simulada rodando em cada um.

## Aceite
G7: os testes da plataforma simulada passam nos três sistemas; uma reunião real gravada e processada em cada um.

## Perguntas
- O loopback do sistema no macOS, sem driver extra, é limitado: aceitar a dependência de um driver virtual open source?
  (padrão: sim, como opção documentada)
