# Revisão do código: achados já decididos (não reapontar)

Quando ler: antes de revisar código ou propor correção (T3). Origem: a revisão do código inteiro de 30/10/2026 (D-072),
com sete leitores e dois revisores: 27 achados conferidos no código e, quando dava, por experimento ("provado"). Um item
daqui só volta com fato novo: o código mudou ou apareceu um caso real que a explicação não cobre. Achado novo confirmado
entra aqui ao fechar a revisão. Critério usado: corrigir só com caminho concreto de dano no uso real (perda de dado,
queda, travamento, erro visível, CI quebrado). Depois, a revisão do diff da F5.10 (D-075, 31/10), na seção própria.
Tamanho-alvo: 8 KB.

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

## Revisão do diff da F5.10, "Perguntar à base" (D-075, 31/10)
Corrigidos: a junção com `USING` ou `NATURAL` lia a lista de negação, porque o SQLite não chama o autorizador para a
coluna da junção (a chave de API e o e-mail vazavam; provado), fechada em três camadas (as palavras recusadas, `people`
fora das visões e o `EXPLAIN` que só deixa abrir as páginas-raiz das visões) com os nomes sem caixa, `e516141`; a
memória sem teto dentro do prazo (~2,5 GB/s), o prazo e o Cancelar ~5 s atrasados, `4f1c501`; o custo da resposta com o
preço do modelo forte e o `too_large` que mandava a resposta sem dado, `3d1a610`; a busca por palavra que logava a
expressão, `18c62b7`.
Tentados e fechados (não reapontar sem fato novo): CTE com o nome de uma visão sobre tabela negada; subconsulta;
`sqlite_schema` e `sqlite_temp_master`; `pragma_*`, também em maiúsculas; `load_extension`, `fts3_tokenizer` e `fts5`;
`ATTACH` com `mode=rwc`; `WITH … INSERT` em TEMP, `CREATE TEMP VIEW`, `DROP VIEW`, `PRAGMA query_only = 0` e `VACUUM
INTO`; duas instruções numa string; `dbstat` e `sqlite_dbpage` (não compilados); LIKE e GLOB patológicos; a resposta
escapada (sem link `listener://` injetável); a sandbox e o "Ver dados" abertos e fechados na mesma thread; a pergunta
nova e o "Ver dados" barrados com outra em curso; `params` que não são texto; o cache refeito nos sete caminhos de
escrita.
Aceitos: o `--downgrade` com o app aberto (o chat falha até reabrir; nada se perde); o FTS lido sem visão e sem filtro
e a CTE com nome de visão que ignora o filtro (D2: sentido, não sigilo); a contagem sem coluna de uma tabela que uma
visão lê ignora o filtro (mesma classe); `llm_calls` no `main` sob o autorizador (as colunas negadas guardam pedido,
resposta e erro, sem chave); a resposta cancelada gravada sem custo (como o chat global); a impressão digital que não vê
estado, duração nem sala (validade de 10 min); `custos_llm` com filtro esconde as chamadas sem reunião.

## Revisão de arquitetura da F5.11 (01/11)
Corrigido: o Enter na confirmação do descarte apagava a gravação (`QMessageBox.question` sem botão padrão; provado com
a caixa real), D-076. Os outros achados (A-01 a A-41) estão na spec da F5.11, não no código de hoje.

## Revisão do diff da F5.11 (02/11)
Corrigidos em `7aee204`: R-01, o Cancelar do download no "Antes de gravar" virava aviso de erro (provado); R-02, Espaço
e setas quase nunca chegavam ao player (o foco abria no título e a lista de falas estava excluída; provado). Também: a
voz confirmada mesclada numa sem pessoa perde a pessoa; o título gravado no fechamento sem pular o `closed`; o token
inválido só no 401 de verdade; os testes do "Testar" (Cancelar e saída). Conferidos sem achado: o token só pelo stdin,
o filho morto no prazo, no Cancelar e na saída, CP3, CP9, CP11, CP15, D-053, F5.6, D-067 e D-076. Em aberto
(dívidas): desfazer a voz transportada de um reprocessamento (D-078); o `--hf-check` atrás da inspeção TLS da empresa.

## Revisão do diff da F5.12 (02/11)
Corrigidos em `03b16a0`: R-01, uma doação informada pela bandeja ou pelo Sobre era desfeita pelo cartão à vista;
R-02, o convite aparecia numa chamada que a F5.7 não vigia (provados). Também: "Fechar" como padrão no Sobre (o Enter
abria o Pix), o `parse_when` com `OSError`, o interruptor que fecha o cartão e o Enter apertado de verdade nos testes.
Conferidos sem achado: CP9, CP15 (o e-mail só em `about.py`), D-053, a cópia para suporte sem caminhos, nenhuma rede.

## Revisão do diff da F5.13 (02/11)
Corrigidos em `49f6b19`: R-01, no "Sair" de verdade o processo novo não recebia o "fechando" (o `rec.stop()` síncrono e
o `emergency_stop` prendiam o laço com o canal aberto; provado): o canal fecha no começo da saída e, sem canal, o
processo novo tenta a trava de novo até 5 s; R-02 [chute], o "Gravar…" do Jump List sem o `bring_to_front`. Também: o
teste do modal que não abria diálogo e o comentário do `removeServer`, que só é seguro com trava de verdade. Aceito
(D-083): no `.exe`, o log do processo novo não vai a lugar nenhum. Conferidos sem achado: as vtables, os GUIDs e a
PROPVARIANT do Jump List, o DACL do pipe, a trava (o handle, o acesso negado, o nome ocupado), os 256 bytes e o JSON
inválido sem o conteúdo no log, os filhos e as sondas fora da trava, CP15 e D-053. Os seis desvios declarados (D-083)
não são defeito.

## Revisão do diff da F5.14 (03/11)
Corrigidos em `7a2032e`: R-01, provado: a recusa no streaming virava resposta vazia gravada como OK nos três chats, e a
pergunta seguinte mandava um bloco só com espaço, que a API recusa; agora a recusa vira `RefusalError`, e o pedido nunca
leva mensagem em branco nem começa por resposta. R-02, provado: o reenvio da resposta cortada quase não crescia nas
tarefas pequenas; agora cresce ao menos 8.192 tokens. R-03: o `xhigh` no Opus 4.6 e no Sonnet 4.6 vira `high`.
Conferidos sem achado:
- a forma do pedido por família contra a API (nenhum 5.5 com ferramenta forçada nem `budget_tokens`) e os 10 esquemas
  depois do `provider_schema`;
- o SDK 2.3.0: o `stream` com `betas` e `fallbacks`, o `count_tokens` com `output_config` e o evento `error` como
  `APIStatusError` 200;
- a soma das partes do fallback; o `translate_error` e o `_status_of` com os erros do Gemini e da OpenAI; o
  `ssl.create_default_context` do PEM;
- as sementes ao abrir: idempotentes; com o banco travado, o app abre em ~5,5 s e grava na abertura seguinte;
- a chave e o conteúdo fora do log e da sonda, as conexões Qt (D-062) e o laço do worker (D-058).
Os doze desvios declarados (D-085) não são defeito.

## Vistos e deixados de fora
Sem efeito no uso, ou decisão de UX: código morto e duplicado, arquivos acima de 400 linhas (dividir ao mexer, backlog),
SQL curto na thread da interface, as listas de apps de reunião em `platform.py` e `activity.py`, riscos sem chamador
(`transaction` dentro de `snapshot`, `Core.close` com conexões de outras threads) e acessibilidade (medidor e linha do
tempo sem leitor de tela). Reapontar só com medida ou caso real.
