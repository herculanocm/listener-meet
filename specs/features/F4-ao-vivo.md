# F4 — Ao vivo: legenda leve com nome e resumo parcial

Status: aprovada · Aprovada por Herculano em: 2026-10-25 (Q1 a Q6 nos padrões; U-021 a U-023 sim; D-044) ·
Fase e gate: F4 · G4 · CPs: 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17
Quando ler: ao detalhar ou implementar esta fase (T2). Origem: D-005. Tabelas: `dados.md`. Tamanho-alvo: 10 KB.

## Objetivo
Durante a gravação, o usuário lê a legenda de quem fala (nome e cargo, 5 a 10 s de atraso) e um resumo parcial que a
LLM atualiza a cada N minutos, sem degradar o app de reunião nem a gravação.

## Escopo
1. **Processo ao vivo** (`python -m listener --live`, não `--worker`: não consome a fila): terceira classe de processo,
   isenta da regra "o pesado não sobe durante a gravação"; lançado ao começar a gravar, morre com ela.
2. **Desvio de áudio**: cópia do PCM em `ChannelChunker.write`, depois do preenchimento de buracos (mesmo relógio do
   `t_start_ms` gravado), na thread `capture-worker`, O(1); trechos de pausa e mudo vão marcados, não como zeros.
3. **VAD e janelas**: Silero VAD do faster-whisper (ONNX) incremental (estado `h`, `c` e 64 amostras de contexto
   carregados entre chamadas da sessão; idêntico à chamada única [fato, 25/10]); como o Whisper custa por janela e não
   por segundo [fato, 25/10], a janela fecha com 500 ms de pausa depois de 4 s de fala, com 1,2 s de silêncio, ou em
   10 s; trechos com menos de 0,5 s de fala são descartados; uma fila só para os dois canais, a janela mais antiga
   primeiro.
4. **Transcrição leve**: faster-whisper `small` int8 (degrada para `base`), 2 threads, `beam_size=1`; filtros de
   `no_speech` e logprob da F2 mais lista de alucinações; idioma travado após 2 janelas; `initial_prompt` = glossário
   da reunião mais a legenda anterior do canal.
5. **Nome ao vivo (best effort)**: R (e L na sala presencial, U-015) = embedding da janela com o modelo dos perfis
   (wespeaker da F2) contra os perfis por `match_one` (limiar e margem da F2, desempate pelos participantes esperados);
   nome anterior do canal mantido se a janela nova for parecida; abaixo do limiar, "Participante"; L sem sala é o
   usuário.
6. **Legendas no banco** (`captions`, CP3/CP9), gravadas pelo processo ao vivo; nunca apagadas; a passagem final da F2
   as substitui só na exibição.
7. **Resumo parcial**: a cada `live.summary_interval_min` (padrão 5), `llm/partial_task.py` (padrão do `chat_task`:
   QThread com `asyncio.run`, uma chamada por vez) manda as legendas novas e o parcial anterior ao modelo rápido e grava
   `summaries` kind `partial` (o anterior fica superado); destaca possíveis decisões e ações; para sozinho no alerta
   (Q6).
8. **Interface**: última legenda no widget e a janela "Ao vivo" (U-021).
9. **Degradação**: atraso acima de 20 s por 30 s troca para `base`; persistindo, pula janelas antigas (fica a mais
   nova); o widget mostra "legenda atrasada" e grava `live_degraded`; a gravação não é afetada.
10. **Configurações (CP8)**: ligar o ao vivo, nomes ao vivo, modelo, threads, intervalo do resumo, mostrar no widget;
    "preparar modelos do ao vivo" fora da gravação (`--live --prepare`).
11. **Medição do G4** no `--check`: atraso médio e p95, CPU do app e do processo ao vivo, acerto do nome.

## Fora de escopo
Diarização ao vivo completa · legenda sobreposta à tela da reunião · tradução · chat ao vivo · ações automáticas a
partir do parcial · GPU (CP14) · usar as legendas como entrada da passagem final (a F2 retranscreve tudo).

## Cláusulas
| CP | Como cumpre |
| --- | --- |
| CP3, CP9 | legendas e parciais no SQLite, sem expiração; o descarte é só do desvio ao vivo, nunca do áudio gravado |
| CP5, CP11 | nome ao vivo é só exibição: não grava `clusters` nem `segments.person_id`, não mexe em perfil |
| CP6, CP7 | o ao vivo nasce do warm-up (caixas guardadas no preset) e tem janela própria |
| CP8 | grupo "Ao vivo" nas Configurações; vale na próxima gravação, o intervalo do resumo vale em uso |
| CP10 | o parcial usa a chave lida na hora e a redação da F3 |
| CP12 | U-021 a U-023 abaixo; entram por padrão se aprovadas |
| CP13 | código só após a aprovação; o L0 (medição no Windows) também precisa dela |
| CP14 | só CPU e modelos open source; LLM paga só no parcial |
| CP15 | prioridade e EcoQoS em `platform/<os>/`; nada pessoal |
| CP17 | Python 3.12+, `TaskGroup`, tipos PEP 695 no código novo |

## Interface
- Warm-up: caixas "Legenda ao vivo" e "Nomes ao vivo" (padrão das Configurações; o preset guarda as duas).
- Widget: última legenda numa linha (`Ana — CFO: ...`) e selo "ao vivo", "legenda atrasada" ou "ao vivo parou".
- U-021 Janela "Ao vivo" (arrastável, sempre visível opcional): legendas rolando com hora, nome e cargo; parcial acima,
  com a hora do último e "atualizar resumo agora". incluir? sim/não
- U-022 Clique numa legenda cria um marcador naquele instante com o texto dela. incluir? sim/não
- U-023 Possíveis decisões e ações do parcial com "marcar" (vira marcador que a ata da F3 lê). incluir? sim/não

## Dados (migração v7 do main, padrão `MAIN_V7_COLUMNS` como na v6)
- `captions` (já tem `person_id`, `created_at`): + `channel`, `confidence`, `label`, `model`, `latency_ms`; índice
  `(meeting_id, t_start_ms)`.
- `summaries`: kind `partial` usa `t_until_ms`, `call_id` e `status` que já existem.
- `presets`: + `live_enabled`, `live_names`.
- `EventType`: + `live_started`, `live_degraded`, `live_cpu`, `live_stopped`. `LlmTask`: + `partial`.
- Configurações: `live.enabled`, `live.names`, `live.model` (`small`, `base`), `live.threads`,
  `live.summary_interval_min`, `live.in_widget`.

## Desenho técnico (padrões da revisão de arquitetura)
- **Lançamento**: novo `spawn_child(argv, env)` na plataforma, com pipes, `BELOW_NORMAL`, `CREATE_NO_WINDOW` e EcoQoS
  desligado no Windows; `spawn_detached` fica como está.
- **Ida (stdin)**: quadros binários `(canal, quadro inicial, taxa, n, flags)` + int16 (~64 KB/s); deque de ~30 s no app
  que descarta o mais antigo e uma thread `live-feed` escreve no pipe; EOF encerra o filho. A taxa vai no quadro
  (`capture.target_rate` aceita até 48 kHz) e o filho reamostra. Rejeitados: socket (firewall, EDR), memória
  compartilhada (sincronização), pedaços curtos no banco (inflam o banco e somam atraso).
- **Volta (stdout)**: linhas JSON (legenda, estado, atraso, CPU); thread leitora no app emite sinal do Qt; log do filho
  em `listener-live.log`, stderr descartado.
- **Atraso**: o app manda o `perf_counter` do início; cada legenda grava `latency_ms` = agora − (início + `t_end_ms`).
- **CPU**: `os.times()` no filho e no app a cada 10 s (sem psutil, que é lento com o EDR), em `live_cpu`.
- **Pesado durante a gravação**: ao começar a gravar com o ao vivo, a lease pede pausa ao pesado; a lease só é lida
  entre janelas [fato], então a degradação cobre o intervalo.
- **torch**: importado só com "nomes ao vivo", `set_num_threads(1)`; wespeaker em ONNX só se o L0 mostrar cosseno
  ≥ 0,99 contra o torch.
- **Pacote** `listener/live/`: `protocol.py` e `names.py` (puros), `client.py` (app, sem ML; entra na regra estática de
  importações), `proc.py` e `vad.py` (filho); `LISTENER_LIVE=fake` para testes sem ML.
- **Eco**: sem headset, legenda de L muito correlacionada com R é descartada (`capture.echo_corr_min`).

## Comportamento
1. Gravar com o ao vivo ligado: o controlador lança o filho (sem modelos: aviso e segue sem ao vivo) e liga o desvio.
2. Pausar: o desvio marca pausa e o filho não transcreve; mudo do mic: sem legendas de L; trocar a fonte: R segue.
3. Parar: EOF no stdin; o filho fecha a janela em curso em até 10 s e sai.
4. Cancelar: `terminate()` do filho antes de apagar a reunião (a FK em cascata limpa as legendas).
5. Filho cai: "ao vivo parou" e uma nova tentativa; a gravação continua intacta.
6. Sem rede ou sem chave: as legendas seguem; o parcial avisa uma vez e tenta no próximo intervalo.
7. Fim da reunião: a fila da F2 roda como hoje; a leitura mostra os `segments`; legendas ficam para consulta e G4.

## Aceite (G4), em três reuniões reais com headset
| Critério | Número | Como medir |
| --- | --- | --- |
| Atraso da legenda | média até 10 s, p95 até 15 s | `--check` sobre `captions.latency_ms` |
| CPU (app + processo ao vivo) | até 35 % ao lado do app de reunião | eventos `live_cpu` |
| Nome de quem tem perfil | 70 % ou mais das legendas | `--check` contra `segments.person_id` revisado no mesmo tempo |
| Gravação intacta | zero buracos acima de 1 s, zero `idx` perdidos | `--check` da F1 |
| Resumo parcial | 1 por intervalo, sem falha na reunião | `summaries` kind `partial` |

## Riscos
| Risco | Mitigação |
| --- | --- |
| Custo por janela [fato: `base` 1,25 s, `small` 3,55 s, Xeon 2,1 GHz] | janelas de 4 a 10 s; L0 mede no Windows |
| Ao vivo e pesado juntos estouram a CPU | pausa pela lease ao gravar; degradação; `live_degraded` |
| torch: 0,5 a 1 GB de RAM e 3 a 8 s para importar [chute] | "nomes ao vivo" desligável; ONNX só se o L0 aprovar |
| Pipe trava o gravador | desvio O(1) com deque; só `live-feed` bloqueia; a leitora sempre drena o stdout |
| EcoQoS joga o filho nos núcleos E [exp] | desligar o throttling do processo |
| API interna do VAD do faster-whisper muda | versão fixada; teste do adaptador no Windows |
| Janela de 3 a 8 s erra mais que o cluster | `match_one` só nomeia acima do limiar da F2; senão "Participante" |
| `.venv` do WSL sem faster-whisper nem torch [fato] | o filho real só roda no Windows; o WSL usa `LISTENER_LIVE=fake` |

## Lotes ([chute] 8,5 dias de foco; o roadmap previa 4 a 6)
- L0 medição no Windows (atraso por janela, VAD, torch, ONNX): 1 d
- L1 v7, `live.*`, `spawn_child`, `--live`, protocolo: 1 d
- L2 desvio, `live-feed`, filho (VAD, Whisper, legendas), ciclo de vida, degradação, modo fake: 2 d
- L3 nomes (`match_one`, suavização, sala presencial): 1 d
- L4 interface e U-021 a U-023: 1,5 d
- L5 resumo parcial: 1 d
- L6 `--check`, testes, verificação no Windows: 1 d

## Perguntas (padrão assumido entre parênteses)
- Q1 Ao vivo ligado por padrão? (sim, se a máquina tiver 8 núcleos ou mais; senão desligado)
- Q2 Nomes ao vivo ligados por padrão? (sim)
- Q3 Resumo parcial automático com custo? (sim)
- Q4 Modelo leve padrão? (`small` int8; `base` na degradação)
- Q5 A janela "Ao vivo" abre sozinha ao gravar? (não; a última legenda fica no widget)
- Q6 O parcial conta no mesmo alerta de custo da reunião que a ata (US$ 2)? (sim, e o parcial para primeiro, para
  sobrar orçamento para a ata)
