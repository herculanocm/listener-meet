# Revisão do código: achados já decididos (não reapontar)

Quando ler: antes de revisar código ou propor correção (T3). Origem: a revisão do código inteiro de 30/10/2026 (D-072),
com sete leitores e dois revisores: 27 achados conferidos no código e, quando dava, por experimento ("provado"). Um item
daqui só volta com fato novo: o código mudou ou apareceu um caso real que a explicação não cobre. Achado novo confirmado
entra aqui ao fechar a revisão. Critério usado: corrigir só com caminho concreto de dano no uso real (perda de dado,
queda, travamento, erro visível, CI quebrado). Tamanho-alvo: 5 KB.

## Corrigidos (D-072)
| Achado | Correção | Commit |
| --- | --- | --- |
| Log parava de vez aos 5 MB no Windows e apagava os antigos: o faulthandler segurava o arquivo e o `rename` da rotação falhava (WinError 32; provado) | faulthandler em `<log>-crash.log`, sem rotação | `7726866` |
| Pausa manual da fila derrubava o worker leve: `time.sleep` no laço do job `llm`, e o vigia da D-058 disparava aos 120 s (provado) | `acheckpoint` espera numa thread | `f5ddb74` |
| Logoff ou reinício cancelado deixava o app vivo sem banco, sem atalhos e sem aviso | o app sai depois das paradas do `commitDataRequest` | `2beecb9` |
| `_MutePoller` preso no COM na saída (0xC0000409) | `abandon`, na conta do `leave_if_stuck` | `2beecb9` |
| `ci.yml` era YAML inválido desde 24/10 (nenhum teste rodou no CI); o Windows sem o extra `win32` | aspas e `.[dev,win32]` | `db7ba12` |
| `dados.md` ("nada apaga dados") e `llm-contrato.md` atrás do código | docs | `04d663a` |

## Não são defeito (o código já trata)
| Achado | Por quê |
| --- | --- |
| `--once` processa mais de um job (`worker.py`, `continue` no `None`) | o `None` só vem do pesado sem dependência; nada usa `--once` em produção |
| `processes.py` muda `argtypes` do `windll.kernel32` compartilhado | nenhuma função em comum: o resto do app usa ali só `SetThreadExecutionState` e `GetCurrentThreadId`; as bibliotecas têm `WinDLL` próprio ou não usam o kernel32 |
| `Core.attach()` sem conferir `in_tx` (ATTACH dentro de transação) | sem chamador em transação: a exclusão apaga o áudio numa conexão própria antes; player, `--check` e `orphan_report` leem fora |
| `diar_overlap_s` e `whisper_overlap_s` não lidos das configurações | não são configuração (nem em `SPECS` nem em tela) |
| `JOB_DONE` gravado depois do `finish` com a reunião excluída | a cascata já levou o job; o `_event` engole o erro da FK |
| `ValueError` de `encode_audio` tratado como pipe quebrado (`live/client.py`) | o desvio entrega bloco mono 1-D; o `ValueError` ali é o do arquivo fechado |
| `done` de `Task`/`StreamTask` ligado a lambda ou `partial` | roda na thread da interface (provado no PySide6 6.12) |
| `QTimer.singleShot(3000, self._finish_test)` sem contexto (`warmup.py`) | método de QObject vira o contexto: fechado antes, nada dispara (provado) |
| Cancelar do assistente deixa o microfone aberto | o Cancelar encerra o app na hora |
| `stop()` da captura com `join()` sem prazo congela a interface | a parada normal roda numa `Task`; só "Sair" e a emergência esperam (teto de 10 s) |
| Lambdas com `self` em `controller.py` (via `_run_task`) e `history.py` (menu) contra o R-06 | nenhum prende widget sem dono (provado no do menu); o teste estático pega só `.connect(lambda …)` direto, de propósito |
| `register` dos atalhos com prazo de 2 s; `_errors` em duas threads | estourar pede 2 s sem o GIL; operações de dict são atômicas e o `Event` ordena |
| Pausar, marcar ou mutar depois do `stop` do gravador | o controlador barra no estado SAVING; só o popup do marcador já aberto grava depois (inofensivo) |
| `IndexJobRunner` com a pausa dentro do laço | ele espera fora do laço (`rag/index.py`) |
| A limpeza fora de `finally` em três testes explicaria o instável do `test_call_watch` | os três rodam depois dele (ordem alfabética) e só pulam a limpeza quando falham |

## Reais e pequenos, aceitos por ora
Estão em "Dívidas técnicas" do `specs/backlog.md`; não são novidade. A morte da thread da captura não marca a reunião; o
"banco não responde há N s" exagera; corridas de leitura e escrita fora da transação (sugestão da LLM, ação superada,
`upsert_me`; janela abaixo de 1 ms); o chat em streaming sem retentativa antes do primeiro pedaço (prazo existe: 120 s
de leitura ociosa nos três adaptadores) e o cancelado gravado como `OK` sem custo; o preparo do ao vivo sem prazo no
`repo_info` e sem Cancelar; o `db-location.json` sem gravação atômica; "Apagar preset" sem confirmação (UX: perguntar ao
dono); o assistente diz "cinco passos" e mostra seis.

## Vistos e deixados de fora
Sem efeito no uso, ou decisão de UX: código morto e duplicado, arquivos acima de 400 linhas (dividir ao mexer, backlog),
SQL curto na thread da interface, as listas de apps de reunião em `platform.py` e `activity.py`, riscos sem chamador
(`transaction` dentro de `snapshot`, `Core.close` com conexões de outras threads) e acessibilidade (medidor e linha do
tempo sem leitor de tela). Reapontar só com medida ou caso real.
