# F1 — Captura em dois canais, SQLite, warm-up e widget

Status: implementada · Aprovada em: 2026-10-24 · Decisões: D-004, D-011, D-026, D-027, D-029, D-033, D-039 ·
CPs: CP3, CP4, CP6, CP7, CP8, CP9, CP12, CP14, CP15
Quando ler: ao mexer em captura, banco, atalhos, warm-up, widget, bandeja ou log. Tamanho-alvo: 6 KB.

## Objetivo
Gravar qualquer reunião (um app ou o sistema inteiro) em dois canais sincronizados, direto no SQLite, escolhendo as
fontes num warm-up e controlando por widget, bandeja e atalhos globais.

## Comportamento
- L = microfone; R = loopback de um app (proc-tap) ou do sistema inteiro; um relógio de sessão, t = 0 no Gravar.
- Mono a `capture.target_rate` (16000 Hz), em pedaços FLAC de `capture.chunk_seconds` (60; 5 a 600) em
  `audio_chunks`; as duas valem na próxima gravação.
- Buraco acima de 250 ms vira silêncio pelo timestamp; a parada preenche o canal até o instante dela.
- Pausar grava silêncio nos dois canais; a linha do tempo não para (D-011).
- Mudo: muta no Windows o endpoint do microfone escolhido (D-004; sem achá-lo, o padrão, com aviso) e L grava
  silêncio; o mudo do sistema, lido a cada 2 s, comanda o do gravador.
- Saúde por canal: ok até 10 s sem sinal acima de `capture.silence_rms` (0,004); alerta em
  `capture.l_alert_seconds` (15) e `capture.r_alert_seconds` (20).
- Aparelho ou app que some: `device_lost`, silêncio, reabertura a cada 5 s, `device_back`; app cujo loopback não
  abre cai no sistema inteiro; trocar a fonte do R não para a gravação (detalhe: F5.3 e F5.9).
- Parar espera o banco até 120 s: `gravada`, ou `interrompida` se algum pedaço não entrou; o resumo edita o título
  (`revisions`). Descartar pede confirmação e apaga a reunião e os pedaços.
- Logoff, desligamento ou Sair gravando: para e salva sem diálogo (até 10 s). Após uma queda, `gravando` vira
  `interrompida` na abertura.
- Atalhos globais editáveis: `hotkeys.start`, `pause`, `stop`, `mark`, `mute` = `ctrl+alt+shift+r`, `p`, `s`, `m`,
  `u`; o que não registra aparece nas Configurações e num aviso.
- O sistema não suspende enquanto grava. Datas em hora local com offset. Log rotativo (5 MB, 5 cópias) em
  `log.path` (vazio = `logs/listener.log` na pasta do banco).

## Interface
- Warm-up (CP6, antes de toda gravação): microfone, fonte ("apps tocando agora", U-001, ou o sistema inteiro),
  "Testar 3 s" com aviso de eco (U-016, `capture.echo_corr_min` 0,25), presets, sala presencial (U-015); Enter grava.
- Widget sempre no topo, arrastável, preso a um monitor: tempo, medidores, saúde (U-004), Pausar, Marcador com nota
  (U-003), Mudo (U-002), Parar, Descartar. Bandeja com o estado e menu.
- Assistente inicial (D-026): usuário, pasta do banco com aviso de pasta sincronizada, log, teste do microfone.
- Configurações (CP8): cada item diz "aplica agora" ou "na próxima gravação". Desenho das telas: F5.4.

## Dados
- `listener.db` e um `listener-audio-AAAA-MM.db` por mês de `meetings.started_at` (`meetings.audio_file`, main v3);
  o `listener-audio.db` antigo segue lido.
- Main v1: as tabelas de base; v2 (main e áudio): datas com offset. Chaves: `capture.*`, `hotkeys.*`, `log.*`,
  `ui.widget_pos`, `warmup.last_*`.

## Código
- `listener/capture/recorder.py`, `chunker.py`: gravador e pedaços.
- `listener/store/core.py`, `schema.py`, `playback.py`: conexões, migrações, trecho tocável.
- `listener/config/defaults.py`: tabela `SPECS`. `listener/model.py`: estados.
- `listener/platform/base.py`, `win32/hotkeys.py`, `win32/mute.py`, `common/hotkeys.py`: plataforma e atalhos.
- `listener/ui/controller.py`, `warmup.py`, `widget.py`, `tray.py`, `wizard.py`: telas.
- `listener/__main__.py`: `--selftest`, `--check`. Testes: `tests/test_recorder.py`, `tests/test_store.py`.

## Invariantes
- CP3: nenhum pedaço é descartado: o banco que recusa é tentado de novo após 1, 2, 4... 30 s, e o que não entrou
  fica retido e listado no resumo (M-04).
- CP9: fora do SQLite só o ponteiro `db-location.json`, o log (D-033) e os modelos. CP15: o que é Windows fica em
  `platform/win32/`.
- Atalhos no Windows por `RegisterHotKey`, em thread com laço de mensagens: exclusivo, consome a tecla; o pynput só
  observa e fica para Linux e macOS. `ctrl+alt` sozinho é AltGr no ABNT2 (A1).
- Aparelho salvo pelo id do endpoint, nunca pelo índice do PortAudio, que muda ao plugar (A3).
- Callbacks de áudio só enfileiram; uma thread não daemon faz mono, reamostragem com estado, FLAC e INSERT; a
  interface lê um snapshot imutável (M-16) e nunca espera o COM (A4, M-07).
- Silêncio sai de buffer fixo e de um FLAC em cache: buraco longo não aloca memória (M-03).
- Uma conexão SQLite por thread, `transaction()` com `BEGIN IMMEDIATE`, WAL; só o papel APP migra (M-02, M-18).
- Migração por arquivo, numa transação com a versão; banco mais novo que o app é recusado; arquivo de áudio sem
  `audio_chunks` é erro claro, nunca reunião muda (M-01). Áudio em páginas de 32 KB, até 8 anexos (M-19).
- Limites aceitos: a deriva de relógio é só medida (`stop` e `--check`); "sempre no topo" não cobre tela cheia.

## Aceite
- `--check`: por canal, frames = duração × taxa com desvio abaixo de 100 ms; nenhum buraco acima de 1 s; sem
  headset, deslocamento L×R abaixo de 100 ms; deriva abaixo de 100 ms por hora.
- Pausa, marcador e mudo em `events` a menos de 1 s do relógio de parede.
- Com o Teams em foco, os cinco atalhos disparam só no app; o mudo muta o headset escolhido, mesmo fora do padrão.
- Buraco de 30 min no R com pico abaixo de 10 MB (`tracemalloc`); INSERT que falha 3 vezes não perde pedaço.
