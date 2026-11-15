# Revisão de código: achados já decididos (não reapontar)

Quando ler: antes de revisar código ou propor correção. Um item daqui só volta com fato novo: o código mudou, ou
apareceu um caso real que a explicação não cobre. Achado novo confirmado entra aqui ao fechar a revisão. Critério:
corrigir só com caminho concreto de dano no uso real (perda de dado, queda, travamento, erro visível). "Provado" quer
dizer conferido por experimento. Tamanho-alvo: 9 KB.

## Não são defeito (o código já trata)
| Achado | Por quê |
| --- | --- |
| `--once` processa mais de um job (`worker.py`, `continue` no `None`) | o `None` só vem do pesado sem dependência; nada usa `--once` em produção |
| `processes.py` muda `argtypes` do `windll.kernel32` compartilhado | nenhuma função em comum com o resto do app; as bibliotecas têm `WinDLL` próprio |
| `Core.attach()` sem conferir `in_tx` | nenhum chamador em transação: a exclusão apaga o áudio numa conexão própria antes |
| `diar_overlap_s` e `whisper_overlap_s` não lidos das configurações | não são configuração (nem em `SPECS` nem em tela) |
| `JOB_DONE` gravado depois do `finish` com a reunião excluída | a cascata já levou o job; o `_event` engole o erro da FK |
| `ValueError` de `encode_audio` tratado como pipe quebrado (`live/client.py`) | o desvio entrega bloco mono 1-D; o `ValueError` ali é o do arquivo fechado |
| `done` de `Task` ou `StreamTask` ligado a lambda ou `partial` | roda na thread da interface (provado no PySide6 6.12) |
| `QTimer.singleShot(3000, self._finish_test)` sem contexto (`warmup.py`) | o método de QObject vira o contexto: fechado antes, nada dispara (provado) |
| Cancelar do assistente deixa o microfone aberto | o Cancelar encerra o app na hora |
| `stop()` da captura com `join()` sem prazo | a parada normal roda numa `Task`; só "Sair" e a emergência esperam, com teto de 10 s |
| Lambdas com `self` em `controller.py` (via `_run_task`) e `history.py` (menu) | nenhum prende widget sem dono (provado); o teste estático pega só `.connect(lambda …)` direto, de propósito |
| `register` dos atalhos com prazo de 2 s; `_errors` em duas threads | operações de dict são atômicas, e o `Event` ordena |
| Pausar, marcar ou mutar depois do `stop` do gravador | o controlador barra no estado SAVING; só o popup do marcador já aberto grava depois (inofensivo) |
| `IndexJobRunner` com a pausa dentro do laço | ele espera fora do laço (`rag/index.py`) |
| A limpeza fora de `finally` em três testes explicaria o instável do `test_call_watch` | os três rodam depois dele e só pulam a limpeza quando falham |

## De propósito (correções que alguém desfaria)
- O faulthandler grava em `<log>-crash.log`, sem rotação: no mesmo arquivo, ele segurava o log, e a rotação falhava
  no Windows (WinError 32; provado).
- A espera da pausa manual do job `llm` usa uma thread (`acheckpoint`), não `time.sleep` no laço: o vigia de 120 s
  derrubava o worker leve (provado).
- Depois de um logoff ou reinício cancelado, o app sai: antes ficava vivo sem banco, sem atalhos e sem aviso.
- `QMessageBox` de confirmação destrutiva sempre com o "Não" como botão padrão: o Enter apagava a gravação (provado).
- No chat do Claude, a recusa no streaming vira `RefusalError`, e o pedido nunca leva mensagem em branco nem começa
  por resposta: a API recusa (provado).
- O reenvio da resposta cortada cresce ao menos 8.192 tokens: antes quase não crescia nas tarefas pequenas (provado).
- O pedido longo do Claude Haiku é cobrado por chamada (`Usage.parts`), não pela soma das tentativas.
- Na instância única, o canal fecha no começo da saída, e o processo novo tenta a trava de novo por até 5 s: o
  `stop()` síncrono prendia o laço com o canal aberto (provado).
- Uma doação informada pela bandeja ou pelo Sobre não é desfeita pelo cartão à vista; o convite não aparece numa
  chamada que o aviso de reunião não vigia (provados).
- Na reunião aberta, Espaço e setas chegam ao player porque o foco não abre no título (provado).

## Reais e pequenos, aceitos por ora
Estão em "Dívidas técnicas", no `specs/backlog.md`. Não são novidade.

## Sandbox de "perguntar à base" (F5.10)
Corrigidos: a junção com `USING` ou `NATURAL` lia a lista de negação, porque o SQLite não chama o autorizador para a
coluna da junção (a chave de API e o e-mail vazavam; provado). Foi fechada em três camadas: as palavras recusadas,
`people` fora das visões e o `EXPLAIN`, que só deixa abrir as páginas-raiz das visões. Também: a memória sem teto
dentro do prazo, o Cancelar atrasado, o custo da resposta com o preço do modelo errado e a busca por palavra que
logava a expressão.

Tentados e fechados: CTE com o nome de uma visão sobre tabela negada; subconsulta; `sqlite_schema` e
`sqlite_temp_master`; `pragma_*`, também em maiúsculas; `load_extension`, `fts3_tokenizer` e `fts5`; `ATTACH` com
`mode=rwc`; `WITH … INSERT` em TEMP, `CREATE TEMP VIEW`, `DROP VIEW`, `PRAGMA query_only = 0` e `VACUUM INTO`; duas
instruções numa string; `dbstat` e `sqlite_dbpage` (não compilados); LIKE e GLOB patológicos; a resposta escapada (sem
link `listener://` injetável); a sandbox e o "Ver dados" abertos e fechados na mesma thread; a pergunta nova barrada
com outra em curso; `params` que não são texto; o cache refeito nos sete caminhos de escrita.

Aceitos: o `--downgrade` com o app aberto (o chat falha até reabrir; nada se perde); o FTS lido sem visão e sem filtro
e a CTE com nome de visão que ignora o filtro (o filtro é de sentido, não de sigilo); a contagem sem coluna de uma
tabela que uma visão lê ignora o filtro; `llm_calls` no `main` sob o autorizador; a resposta cancelada gravada sem
custo; a impressão digital que não vê estado, duração nem sala (validade de 10 min); `custos_llm` com filtro esconde
as chamadas sem reunião.

## Conferidos sem achado, por área
- **Revisão de vozes e token do Hugging Face (F5.11):** o token só pelo stdin; o filho morto no prazo, no Cancelar e
  na saída; CP3, CP9, CP11, CP15; privacidade do log.
- **Sobre e convite (F5.12):** CP9; CP15 (o e-mail só em `about.py`); a cópia para suporte sem caminhos; nenhuma rede.
- **Barra de tarefas (F5.13):** as vtables, os GUIDs e a PROPVARIANT do Jump List; o DACL do pipe; a trava (o handle,
  o acesso negado, o nome ocupado); os 256 bytes e o JSON inválido sem o conteúdo no log; os filhos e as sondas fora
  da trava. Aceito: no `.exe`, o log do processo novo não vai a lugar nenhum.
- **Claude (F5.14):** a forma do pedido por família (nenhum modelo 5.5 com ferramenta forçada nem `budget_tokens`); os
  10 esquemas depois do `provider_schema`; o `stream` com `betas` e `fallbacks`; a soma das partes do fallback; o
  `translate_error` com os erros dos três provedores; as sementes de preço idempotentes ao abrir; a chave e o conteúdo
  fora do log; nenhum parâmetro de amostragem enviado.
- **Busca ao escolher a pessoa (F5.25):** a escolha pelo id da linha; o Enter sem linha selecionada (também depois do
  clique no título ou do Ctrl+clique); o `deleteLater` no `finally` com o `exec` aninhado na saída; o filtro de tecla
  pelo `__dict__`; nada persiste; o log sem nome nem texto digitado. Aceitos, como na caixa de antes: o polling que
  recarrega a revisão com a janela aberta e o Enter segurado que confirma a voz seguinte.

## Vistos e deixados de fora
Sem efeito no uso, ou decisão de UX: código morto e duplicado; arquivos acima de 400 linhas (dividir ao mexer); SQL
curto na thread da interface; as listas de apps de reunião em `platform.py` e `activity.py`; riscos sem chamador
(`transaction` dentro de `snapshot`, `Core.close` com conexões de outras threads); acessibilidade (medidor e linha do
tempo sem leitor de tela). Reapontar só com medida ou caso real.
