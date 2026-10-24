# F1 — Captura generalizada, SQLite, warm-up e widget

Status: implementada (G1 pendente) · Aprovada por Herculano em: 2026-10-24 (D-029) · Implementada em: 2026-10-24 ·
Fase e gate: F1 · G1 · CPs: 3, 4, 6, 7, 8 (parcial), 9, 10 (só a tabela), 12, 13, 14, 15
Quando ler: ao mexer em captura, banco, warm-up, widget, bandeja ou configurações mínimas. Correções: `F1.1`.
Tabelas: `dados.md`. Tamanho-alvo: 7,5 KB.

## Objetivo
Gravar qualquer reunião (Teams, Meet no navegador, Zoom ou o sistema inteiro) em dois canais sincronizados, direto nos
dois arquivos SQLite, escolhendo as fontes num warm-up e controlando tudo por um widget flutuante com pausa, marcador com
nota e mudo global; provar isso em três reuniões reais.

## Escopo
| # | Item | Código |
| --- | --- | --- |
| 1 | Fontes: microfones WASAPI com nome amigável; "apps tocando agora" (sessão de áudio ativa, nome, título, pico ao vivo, U-001); "sistema inteiro"; app de reunião conhecido primeiro | `platform/win32/platform.py`, `ui/sources.py` |
| 2 | Captura: L = mic 16 kHz mono com timestamp do ADC; R = loopback por processo (proc-tap, árvore do app) ou do sistema; relógio único; silêncio quando mudo; buracos preenchidos por timestamp e no fim; FLAC de 60 s (configurável) em `audio_chunks`; 16 kHz mono por padrão (D-015) | `capture/` |
| 3 | Banco: dois arquivos, ATTACH, WAL, migração numerada; tabelas da F1 e as futuras vazias; pasta configurável, padrão dados locais do sistema, aviso se sincronizada | `store/` |
| 4 | Warm-up (CP6): mic e fonte; medidores; "testar 3 s"; lembra a última escolha; presets Teams, Zoom, Meet e Sistema editáveis e "salvar como preset"; título e participantes opcionais; Gravar grande; Enter confirma | `ui/warmup.py` |
| 5 | Widget: sempre no topo, sem moldura, arrastável, posição lembrada; tempo, medidores L e R, saúde (U-004); Pausar/Retomar, Marcador com nota (U-003), Mudo global (U-002), Parar, Cancelar com confirmação; alerta L mudo 15 s e R 20 s, com "trocar fonte" sem parar | `ui/widget.py`, `ui/controller.py` |
| 6 | Atalhos globais configuráveis: iniciar (abre o warm-up com o último preset), pausar, parar, marcar, mudo; funcionam com o app escondido | `platform/common/hotkeys.py` |
| 7 | Bandeja com estado (ocioso, gravando, pausado) e menu Gravar, Parar, Mudo, Configurações, Sair | `ui/tray.py` |
| 8 | Configurações mínimas (CP8 parcial): captura, atalhos, presets, pasta do banco; cada item marcado "aplica agora" ou "na próxima gravação" | `ui/settings_min.py`, `config/defaults.py` |
| 9 | Reprodução a partir do banco: qualquer trecho (reunião, canal, instante) | `store/playback.py` |
| 10 | Fim da gravação: status `gravada`; resumo (duração, tamanho, pedaços, marcadores); título editável vai para `revisions` | `ui/summary.py` |
| 11 | Primeira inicialização (D-026): nome, e-mail, cargo, empresa; pasta do banco com aviso de OneDrive, Dropbox, Google Drive e iCloud; teste do mic; cria o banco; o único arquivo fora do banco é o ponteiro da pasta | `ui/wizard.py`, `config/bootstrap.py` |
| 12 | Camada de plataforma (D-027, CP15): interfaces em `platform/base.py`; Windows em `platform/win32/`; proc-tap e pynput são multiplataforma; pycaw e PyAudioWPatch só no adaptador | `platform/` |

## Fora de escopo
Transcrição, legenda ao vivo, pyannote, perfis, LLM, revisão, histórico, RAG, notificação de reunião (U-012, F6), backup e
tamanho do banco (U-010, F6), tema além de seguir o sistema, implementações Linux e macOS.

## Cláusulas
CP3 nada apaga (Cancelar descarta só a gravação em curso, confirmada) · CP4 só áudio do sistema ou do app e mic; sessões e
títulos são do Windows · CP6 warm-up antes de toda gravação, inclusive pelo atalho · CP7 PySide6, tema do sistema ·
CP8 o que existe é editável; o que não pode mudar em gravação é marcado · CP9 tudo no SQLite, inclusive presets, posição
do widget e atalhos; modelos de ML em pasta própria · CP12 U-001 a U-004 e U-015 · CP14 sem GPU; pycaw é open source ·
CP15 item 12.

## Interface
- Warm-up: duas colunas (Microfone | Saída), cada uma com lista, medidor e estado ("ouvindo", "sem sinal há 5 s"); abaixo
  preset, título, participantes e Gravar. App escolhido parou de tocar por 5 s: aviso discreto; nenhum app tocando:
  "Sistema inteiro" pré-selecionado.
- Widget: cerca de 320×96 px, canto inferior direito, fundo escuro translúcido, tempo grande, dois medidores, cinco botões
  com atalho no tooltip; duplo clique abre as Configurações; flags "always on top" e tool window.
- Marcador: popup onde está o cursor com [mm:ss]; nota salva no marcador; lista ao passar o mouse no contador.
- Mudo global: um botão; muta o dispositivo via Core Audio (D-004); se o usuário desmutar pelo app de reunião, o Windows
  continua mudo e o widget avisa (estado do sistema lido a cada 2 s).
- Saúde: verde (sinal nos últimos 10 s), âmbar (10 a 20 s), vermelho (acima); vermelho do R abre "trocar fonte" sem parar.
- Preset "sala de reunião" (U-015): só marca `is_in_room`; a F2 usa para diarizar o L.

## Dados
Tabelas de `dados.md` marcadas F1. Pessoa `is_me` criada no assistente. Eventos: start, pause, resume, mute, unmute,
marker, source_change, device_lost, alert, warning, stop, cancel, error.

## Comportamento
1. Iniciar abre o warm-up com o último preset; Gravar cria a reunião, abre os dois arquivos, inicia L e R e mostra o
   widget; t = 0 do relógio da sessão.
2. Pausar: canais abertos, grava silêncio, evento; linha do tempo contínua (D-011). Retomar: evento.
3. Marcador: evento mais linha em `markers`. 4. Mudo: evento; L grava silêncio.
5. Mic some: evento `device_lost`, alerta, silêncio até voltar; app fecha: evento, alerta, R em silêncio e "trocar fonte".
6. Parar: fecha os pedaços até o instante da parada (silêncio preenchido), status `gravada`, resumo. Cancelar:
   confirmação; apaga a reunião e os pedaços.
7. Falha do app: na próxima abertura, `gravando` vira `interrompida`, pedaços preservados.
8. Trocar fonte R em gravação: para a captura, inicia na nova, preenche o buraco, evento `source_change`; app que falha ao
   abrir cai para o sistema inteiro com aviso.

## Aceite (G1), em três reuniões reais com headset, em duas plataformas
- Continuidade: por canal, frames = duração × taxa com desvio abaixo de 100 ms; nenhum buraco acima de 1 s no índice.
- Alinhamento: numa gravação sem headset, correlação cruzada L×R com deslocamento abaixo de 100 ms.
- R tem sinal enquanto os outros falam e L enquanto você fala (RMS acima do limiar em 5 pontos de cada reunião).
- Alerta de canal mudo disparou num teste provocado (trocar a saída do app de reunião no meio).
- Atalhos funcionam com o app escondido; pausa, marcador e mudo em `events` com desvio abaixo de 1 s do relógio de parede.
- Reproduzir um trecho do banco toca o áudio certo. Tamanho do banco por hora anotado em `decisoes.md`.

## Riscos
Meet: o áudio sai de um processo filho do navegador; proc-tap captura a árvore (testar Edge e Chrome) · proc-tap entrega
~10 ms por callback com `frames = -1` (contar pelos bytes) · mudo via Core Audio exige o dispositivo certo (F1.1 A2) ·
"sempre no topo" não é garantido sobre tela cheia exclusiva · atalhos colidem com Teams e navegadores (F1.1 A1) ·
FLAC 16 kHz mono de 25 a 30 MB por hora por canal [exp].

## Perguntas respondidas em 24/10
Q1 warm-up sempre, Enter confirma o último preset: sim · Q2 trocar fonte R sem parar: sim · Q3 nome vindo do Windows:
não, assistente (D-026) · Q4 widget 320×96 no canto inferior direito: sim · Q5 pycaw só no adaptador Windows: sim ·
Q6 pasta do banco escolhida pelo usuário, com aviso de pasta sincronizada: sim.
