# F4 — Ao vivo

Status: implementada · Aprovada em: 2026-10-25 · Decisões: D-005, D-044 · CPs: CP3, CP5, CP9, CP10, CP11, CP15
Quando ler: ao mexer em `listener/live/`, no desvio de áudio da captura ou no resumo parcial. Tamanho-alvo: 5 KB.

## Objetivo
Durante a gravação, o usuário lê a legenda de quem fala, com nome e cargo, e um resumo parcial da LLM.

## Comportamento
- Gravar com "Legenda ao vivo" lança o filho `python -m listener --live`, fora da fila; sem os modelos, grava sem ele.
- Desvio: `ChannelChunker.write` entrega cada bloco em O(1); pausa, mudo e buraco vão marcados, sem amostras.
- Janela (VAD Silero): fecha com 500 ms de pausa depois de 4 s de fala, com 1,2 s de silêncio ou em 10 s; menos de
  0,5 s de fala é descartado; uma fila para os dois canais, a mais antiga primeiro.
- Transcrição: faster-whisper `live.model` (`small`) int8, `live.threads` (2), no idioma da reunião (F5.3).
- Nome (`live.names`): a janela de R (e de L na sala presencial) contra os perfis, com o limiar e a margem da F2; abaixo
  do limiar, "Participante"; L sem sala é o usuário.
- Eco: janela de L com correlação de `capture.echo_corr_min` (0,25) ou mais contra R, sem 0,5 s de fala própria, sai.
- Degradação: atraso acima de 20 s por 30 s troca para `base`; mais 30 s, pula as janelas pendentes menos a mais nova
  de cada canal; não volta sozinha.
- Resumo parcial (`live.partial`, desligado por padrão, F5.6): a cada `live.summary_interval_min` (5; 0 desliga), as
  legendas novas (até ~20 mil caracteres) e o parcial anterior vão ao modelo rápido; timeout de 60 s, sem retentativa;
  para quando a reunião gastou 50 % de `llm.cost_alert_usd`. Sem rede ou chave, avisa uma vez e as legendas seguem.
- Pausar: nada é transcrito. Parar: EOF no stdin e o app mata o filho em 10 s. Cancelar: `terminate()` antes de apagar
  a reunião. Filho cai: uma nova tentativa por gravação.
- Com o ao vivo, a lease pausa o worker pesado mesmo sem `pipeline.pause_on_record`.
- `--live --prepare` baixa os modelos; começar a gravar interrompe o download.

## Interface
- "Antes de gravar": caixas "Legenda ao vivo" e "Nomes ao vivo" (guardadas no preset) e "Resumo parcial".
- Widget: selo "ao vivo", "legenda atrasada" ou "ao vivo parou"; a última legenda (`live.in_widget`) só com a bandeja
  de legendas (F5.17) desligada.
- Janela "Ao vivo" (U-021): legendas com hora e nome, o parcial com "Atualizar resumo agora"; nunca abre sozinha.
- Clique numa legenda cria marcador com o texto dela (U-022); "marcar" nos itens do parcial (U-023).

## Dados
Migração v7: `captions` + `channel`, `confidence`, `label`, `model`, `latency_ms`; `presets` + `live_enabled`,
`live_names`. `summaries` kind `partial`. Eventos `live_started`, `live_degraded`, `live_cpu`, `live_stopped`. Chaves
`live.*` em `listener/config/defaults.py`; `live.enabled` nasce ligada com 8 núcleos ou mais.

## Código
- `listener/live/protocol.py`: quadros binários de ida (stdin) e linhas JSON de volta (stdout).
- `listener/live/client.py`: lado do app; `listener/live/proc.py`: o filho.
- `listener/live/vad.py`: janelas; `listener/live/names.py`: nome; `listener/live/report.py`: G4 no `--check`.
- `listener/llm/partial_task.py`: resumo parcial; `listener/ui/live.py`: janela "Ao vivo".
- `tests/test_live_child.py`, `tests/test_live_client.py`, `tests/test_live_partial.py`, `tests/fake_live.py`.

## Invariantes
- CP5, CP11: o nome ao vivo é só exibição; nada aqui grava `clusters`, `segments` nem perfis.
- CP3, CP9: legendas e parciais nunca são apagados; a F2 retranscreve tudo e só os substitui na tela.
- O desvio nunca bloqueia o gravador: deque de ~30 s por canal que descarta o mais antigo; só a thread `live-feed`
  escreve no pipe. Pipes, não socket: firewall e EDR.
- O filho aponta o descritor 1 para o stderr e fala o protocolo numa cópia: um `print` perdido não corrompe as linhas.
- Com nomes, o torch é importado antes do Whisper (a ordem inversa arrisca "OMP: Error #15"); `client.py` fica sem ML.
- CP10: Cancelar mantém as chamadas pagas do parcial em `llm_calls`, sem a reunião (custo do mês).
- CP15: o filho nasce em prioridade normal (F5.18) e desliga o EcoQoS em si mesmo, em `platform/<os>/`.

## Fora de escopo
Diarização ao vivo · legenda sobre a tela da reunião · tradução · ações automáticas a partir do parcial.

## Aceite
G4, em três reuniões reais com headset, pelo `--check`: atraso médio até 10 s e p95 até 15 s (`captions.latency_ms`) ·
CPU do app mais o ao vivo até 35 % (`live_cpu`) · nome certo em 70 % ou mais das legendas de quem tinha perfil ·
zero buracos acima de 1 s na gravação · um parcial por intervalo.
