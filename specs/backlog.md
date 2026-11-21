# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre. É o único lugar de estado: atualize ao fim de cada sessão e apague o que foi concluído.
Tamanho-alvo: 12 KB.

## Estado (2026-11-15)
- Versão 0.15.0, esquema do banco v11. F1 a F5.21 e F5.25 implementadas (`CHANGELOG.md`); F6 é rascunho e F7 é
  futuro.
- A `main` é a referência. O branch `claude/speakers-search-typeahead-a2d9ae` (F5.23, busca ao escolher a pessoa
  na revisão, e F5.24, remoção da legenda ao vivo e das sugestões) foi descartado pelo dono em 15/11 (D-109): a
  legenda ao vivo, a bandeja de legendas, o resumo parcial e as sugestões ao vivo ficam no produto.
- O código é a referência (D-109): documento que diverge do código é ajustado ao código.
- O `.exe` 0.14.0 e o zip foram gerados em 08/11; o 0.15.0 não foi gerado. A sonda `--probe` e o push são do dono.
- Sem CI (D-090): os testes rodam na máquina, antes de cada commit.
- Documentação reestruturada em 15/11 (D-108): specs implementadas condensadas, handoff e roteiro de teste apagados.
  A spec F5.22 (GPU), que estava suspensa, foi apagada (D-109); a CP14 vale como está.

## Próximos passos
1. O dono confere no uso real o que está em "Verificar no Windows".
2. F5.26 (o vídeo só com as vozes decididas) e F5.27 (gravar a tela) aprovadas em 20/11 (D-112, D-113), em
   implementação. A F5.27 começa pela sonda de go/no-go (CPU, disco, EDR).
3. Depois: F6 (telas completas), pelo fluxo do `AGENTS.md`.

## Verificar no Windows (uso real; os critérios e os números estão no Aceite de cada spec)
Até a F5.15, tudo foi dado por verificado pelo uso real (D-090). Entrega feita fora do Windows acrescenta aqui o que
só o Windows confirma.
- **F5.25** (três reuniões revisadas): toda escolha por "Outra pessoa…" sai sem rolar a lista; o tempo de abertura
  no banco real (o plano B da spec, guardar as referências entre aberturas, só se passar de 150 ms).
- **F5.21** (três reuniões com a tela compartilhada): `scripts/win_probe.py shots` nos temas claro e escuro, com as
  fotos `f521-*` sem `cortados` nem `pequenos`, e a linha `f521_tempos`; `scripts/win_probe.py tela` nos monitores
  reais (tempos do print e do preparo, a janela excluída da captura, a janela de outro processo por cima em preto, o
  contexto de DPI) e `tela --guardar PASTA` com uma planilha no 4K, para julgar se o texto pequeno se lê; a janela da
  reunião achada no Teams novo, no Zoom e no Meet do Chrome, também com a fonte "sistema inteiro"; monitores com
  escalas diferentes; o conteúdo protegido, que não vai, e o slide escuro, que vai; o atalho `ctrl+alt+shift+t`; a
  primeira opção com a tela nos três provedores; o bloco `screen` do `--check`; o Teams compartilhando a tela sem a
  miniatura nem a janela maior.
- **F5.20** (três reuniões com headset): `win_probe.py shots` com as fotos `f520-*` e a linha `f520_tempos`; o Teams
  compartilhando a tela sem o cartão nem os popups; os cliques no cartão sem tirar o foco do app de reunião; o atalho
  `ctrl+alt+shift+d`; a primeira opção com a LLM real no Gemini (JSON em streaming) e numa família do Claude de
  ferramenta forçada; os números do `--check` (parcela útil, evidência, compactação, custo por pedido); o memorando
  depois de 10 avaliações.
- **F5.19**: salvar o MP3 e o MP4 de uma reunião processada e abrir no player do Windows e num celular; fechar a
  janela no meio; começar a gravar com uma exportação em curso (a faixa diz "Na espera").
- **F5.18** (com os modelos): o `pausado dentro da janela` em até 13 s no `listener-worker.log`; o worker "Ociosa" no
  Gerenciador de Tarefas durante a gravação e o filho `--live` em "Normal"; três reuniões reprocessadas; o texto do L
  numa reunião sem headset com o gate de eco; o atraso p95 do R com o nome sobreposto; o banco real aberto com
  `mmap_size` de 256 MB; a primeira varredura do índice migrando os hashes sem refazer vetor.
- **F5.17**: a bandeja aberta, recolhida, fechada e reaberta numa reunião; a roda do mouse, as dicas e o clique
  direito; o Teams compartilhando a tela sem a bandeja; dois monitores com escalas diferentes.
- **F5.16**: uma reunião processada sem o token do Hugging Face.
- **`.exe` 0.14.0**: a sonda `--probe`.

## Perguntas abertas ao dono (o padrão entre parênteses só vale com a resposta dele)
- F5.21: as miniaturas da aba "Sugestões" ficam na janela da reunião, que aparece na captura quando é compartilhada
  (padrão: ficam, como o resto da reunião; a cópia maior abre fora da captura).
- F5.19: o .docx loga o caminho inteiro do arquivo salvo, contra a regra de privacidade do log (D-053); o .md e a
  exportação de mídia logam só a pasta (padrão: alinhar o .docx).
- Limpeza: apagar o venv antigo do preparo (F0) e as duas entradas vazias no cache do Hugging Face (padrão: apagar).

## Dívidas técnicas
Reais e pequenas, aceitas por ora. Corrigir ao mexer na área, ou quando aparecerem no uso.

**Captura e áudio**
- A morte da thread de trabalho da captura não marca a reunião.
- A saúde dos canais começa "ok" por até 10 s sem sinal, e o R não avisa antes (D-050).
- A deriva de relógio entre os canais só é medida (P22).
- O eco é medido no "Testar 3 s" e depois de trocar o microfone, não de forma contínua. O ao vivo usa o limiar do
  aviso (`capture.echo_corr_min`, 0,25): calibrar se o eco vazar sem headset.
- O loopback por processo cai calado no sistema inteiro em caminhos de falha (`is_process_specific()` diz quando) e
  somaria o sistema inteiro no canal do app sem aviso; o pedido de saída ambíguo (nome repetido no loopback) não fica
  no handle; a vigia pode pedir a volta ao app de novo a cada mudança de assinatura dos aparelhos.
- Um consumidor do desvio de áudio que lança exceção tira o bloco do consumidor seguinte.
- Dois dispositivos com o mesmo nome caem no índice, com aviso.

**Processamento e perfis de voz**
- O worker pesado carrega o CTranslate2 antes do torch; se aparecer "OMP: Error #15", importar o torch antes, como o
  ao vivo faz.
- Durante um reprocessamento, a revisão mostra o job concluído anterior. Trecho livre (`embed_range`) e separar
  perfis ficam para a F6.
- Dois workers leves podem nascer no mesmo segundo (D-056): o segundo não acha job e sai. Inofensivo.
- No preset de sala, `R/SPK_0` e `L/SPK_0` chegam à identidade com o mesmo rótulo, e só um recebe a sugestão.
  Proposta: o canal no rótulo e a busca por (canal, rótulo).
- Quem junta 5 notas de grupo passa a exigir ~0,75 de similaridade (F5.15). Se gente conhecida começar a cair em
  pendente, a variante do limiar só global está a uma constante.
- Desfazer a voz transportada de um reprocessamento não acha o vetor (D-078).
- O recálculo das referências de voz roda na thread da interface, dentro da transação ([chute] 20 a 60 ms com 200
  pessoas).

**LLM e busca**
- O chat em streaming não tem retentativa antes do primeiro pedaço, e o cancelado é gravado como `OK` sem custo.
- Corridas de leitura e escrita fora da transação, com janela abaixo de 1 ms: a sugestão da LLM num cluster
  recém-confirmado, a ação superada que volta e o `upsert_me`.
- O preparo do ao vivo não tem prazo no `repo_info` nem Cancelar.
- "Perguntar à base": o `--downgrade` com o app aberto faz o chat falhar até reabrir (nada se perde); a
  `memory_rss` do Windows mede o working set, que o sistema pode encolher.
- Os preços da geração 3 do Gemini não estão nas sementes: a estimativa usa os da 2.5 [chute]. Conferir com a fatura
  e cadastrar em Configurações.
- Adiados na F5.18, sem medida nova: afinidade de núcleos, memória do e5 local, laço asyncio persistente no app e
  `semantic.build`. Voltam se o e5 virar o embedder ativo.
- `NetworkConfig.verify()` não tem chamador; `run_global_chat` só é chamado pelos testes desde a F5.10; a chave
  `llm.audio_second_opinion` existe, mas nenhum passo a lê (a segunda opinião por áudio nunca foi escrita).
- Dez cópias de `title or title_llm` poderiam usar `effective_title`.
- Em "perguntar à base", cada rodada paga os resultados acumulados: o `cache_prefix` só marca a primeira mensagem.
- A estimativa de custo da ata usa 250 tokens por minuto e 20 caracteres por fala [chute]: calibrar no banco real.
- O PyAV não é declarado no `pyproject`: vem com o faster-whisper.

**Interface**
- O aviso "banco não responde há N s" exagera o tempo (`pending_since`).
- O `db-location.json` não tem gravação atômica.
- Num Windows sem a exclusão da captura de tela, o aviso de reunião sem gravação só muda a dica e o menu da bandeja.
  Proposta: um selo no ícone enquanto houver chamada pendente.
- Se a interface atrasar durante uma exportação de mídia, o plano B é exportar num processo filho. Uma pasta de
  rede lenta como destino segura a saída do app.
- O título sublinhado (setext) do Markdown não é lido na exportação para .docx.
- A falha fora do esquema numa tarefa automática não oferece "Restaurar o prompt padrão" na reunião, só na página.
- Pegar a trava da instância única antes dos imports pesados cortaria ~1 s do clique no ícone fixado.
- Acessibilidade: o medidor e a linha do tempo não têm leitor de tela.
- A 640 px de largura, o campo do título da reunião rola e esconde a primeira letra.
- Os textos são fixos em pt-BR (`specs/roadmap.md`).
- No log, o "Windows fatal exception: code 0x8001010d" é o faulthandler registrando uma exceção COM já tratada: ruído.

**Testes**
- Instáveis sob carga, passam sozinhos: dois testes de `test_call_watch.py`
  (`test_failures_degrade_with_one_warning_and_recover_with_one_info` e
  `test_the_app_exits_with_code_0_with_the_sensor_stuck_in_the_audio_system`), dois de `test_theme` por ordem e,
  uma vez, `test_rag_ui.py::test_the_system_loopback_never_opens_with_the_player_playing`.
- A suíte inteira leva ~140 s com `-n 4 --dist loadgroup` e a rápida ~62 s (a meta era 8 s); mais processos pioram
  numa máquina com 2 núcleos de desempenho.
- Testes e sondas deixam pastas `listener-*` na pasta temporária: passar os testes para o `tmp_path`.
- A regra `test_app_modules_do_not_import_models` deveria rodar num subprocesso.
- O `mypy` sobre `scripts/` (fora do gate do `pyproject`) acusa 54 erros; o `pyproject` não liga o `C901`, e o limite
  de complexidade 10 é só convenção.

**Hipóteses por confirmar no uso [chute]**
- O Teams pré-aquecendo o microfone dispararia o aviso de reunião ao abrir; o Teams no mudo, fechando o microfone,
  perderia o aviso. Os títulos do Teams e do Zoom no navegador não foram medidos.
- Uma saída de 6 ou 8 canais pode cair em "sem sinal".
- O SmartScreen pode barrar um build local do `.exe`.
- Se o atalho fixado na barra não levar o AppUserModelID, o Jump List pede um atalho no menu Iniciar.
- Claude: o esquema com nulo como lista de tipos pode ser recusado (a correção prevista é traduzir para `anyOf`); a
  ata no modelo forte pode passar do prazo de 240 s; conta nova pode pedir `llm.max_parallel` em 1.
- O EDR pode bloquear o print da tela (BitBlt); o texto pequeno de uma planilha em 4K, reduzido a 1.600 px, pode não
  se ler.

**Tamanho do código**
- Acima de 400 linhas, dividir ao mexer: `ui/controller.py`, `live/proc.py`, `ui/player.py`, `capture/recorder.py`,
  `pipeline/llm_steps.py`, `store/rag.py`. Perto do limite: `rag/agent.py`, `ui/history_ask.py`, `rag/sandbox.py`,
  `tests/test_win32_devices.py`.
  Funções longas: `MeetingWindow.__init__` (~88 linhas) e `ProviderTab.__init__` (68).

**Stack**
- CP17: subir o piso e o teto do `requires-python` quando o WhisperX e o torch aceitarem o Python 3.14.
- O lock é só do Windows (`requirements/windows.txt`). Um lock do Linux pede o índice de CPU do torch, porque o do
  PyPI traz CUDA.
