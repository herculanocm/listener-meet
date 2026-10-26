# Roteiro de teste no Windows (o que só o dono faz)

Quando ler: quando o dono for testar no Windows. Cada parte diz o que precisa antes, o que fazer, o que esperar e o que
devolver à LLM. As caixas são para você acompanhar; o estado oficial fica no `backlog.md`, que a LLM marca quando você
devolve o resultado, junto com os números em `decisoes.md` e a correção do que aparecer. Origem: itens 3 a 9 de
"Verificar no Windows" e os bloqueios B1, B2, B4, B5, B13 e B15 do `backlog.md` (26/10). O que a LLM já conferiu sem o
dono não está aqui. Tamanho-alvo: 11 KB.

## Antes de começar
- Terminal: PowerShell em `C:\dev\listener-meet`. App: `.venv\Scripts\python.exe -m listener` (depois, pelo ícone da
  bandeja). Sair: bandeja › Sair.
- Logs na pasta `logs` do banco: `listener.log` (o app), `listener-worker.log` (processamento), `listener-llm.log`
  (LLM e índice) e `listener-live.log` (ao vivo). Chave e token não vão para o log.
- Devolver: cole no chat o que a parte pede (saída de comando, números, print da tela estranha) ou o caminho do log.
  Nunca cole a chave nem o token.
- Ordem: as partes 1 a 6 numa sessão (~2 h); a 7 quando der; a 8 e a 9 nas reuniões reais da semana.
- Para ver a RAM e a CPU de cada processo: Gerenciador de Tarefas › Detalhes, com a coluna "Linha de comando" (clique
  direito no cabeçalho › Selecionar colunas). O app é o `python.exe` com `-m listener`; o processamento, `--worker`; a
  LLM e o índice, `--worker --light`; o ao vivo, `--live`.

## Parte 1 — Primeiro uso no perfil real (15 min; B1)
- [ ] Abra o app. O assistente pede seus dados, a pasta do banco e testa o microfone. Use uma pasta fora do OneDrive
      (a chave fica no banco em texto aberto, CP10), por exemplo `C:\dados\listener-meet`.
- [ ] O ícone aparece na bandeja (no Windows 11 pode cair no menu oculto, a seta ^: arraste para a barra). Clique
      direito: Gravar…, Pausar / retomar, Marcador, Mudo, Parar, Ao vivo…, Histórico…, Fila de processamento…,
      Configurações… e Sair.
- [ ] Configurações…: role até o fim. Esperado: nada cortado nem em inglês; os atalhos com "registrado" em verde.
- [ ] Leve as Configurações para o segundo monitor (1080p a 100 %) e de volta (150 %): letra nítida e tamanho certo.
- Devolva: o que estranhou, com print.

## Parte 2 — Chaves (10 min; B4 e B13)
Tudo no primeiro grupo das Configurações, "Chaves de acesso" (U-037), com o link de onde conseguir cada uma.
- [ ] Hugging Face: pelos links do grupo, logado, aceite as condições dos dois repositórios do pyannote e crie um token
      de leitura (Settings › Access Tokens); cole em "Hugging Face".
- [ ] Gemini (Google): pelo link, crie a chave (plano pago); cole › "Testar conexão". Esperado, logo abaixo da chave:
      "ok: <modelo> respondeu em N ms".
- [ ] Salvar. No grupo "LLM", mais abaixo, confira "Ativo para texto" e "Ativo para embeddings" no Gemini (a Parte 6
      troca para o Local). No warm-up, o aviso laranja acima do "Gravar" some quando nada falta.
- [ ] Se a gravação do primeiro teste (26/10) ainda estiver no banco: Fila de processamento › selecione › "Tentar de
      novo". Se o banco foi apagado, a Parte 3 grava outra.
- Devolva: o texto do "Testar conexão".

## Parte 3 — Gravação curta de ponta a ponta (25 min; B1, F2, F3)
Precisa da Parte 2 (sem o token, o processamento não sai; o warm-up avisa). A legenda ao vivo só aparece com fala: numa
chamada de verdade ou com um vídeo tocando no navegador escolhido como fonte (no primeiro teste, a janela de chat do
Teams não tinha som e o canal R ficou em silêncio).
- [ ] Abra uma chamada de teste (Teams ou Meet com alguém, ou um vídeo tocando no navegador) e `Ctrl+Alt+Shift+R`
      (warm-up): o app aparece em "Apps tocando agora" com o medidor andando; escolha-o; "Testar 3 s"; título e
      participantes.
- [ ] Grave 3 a 5 min com headset: fale e deixe o outro lado falar; um marcador com nota (`Ctrl+Alt+Shift+M`); pause e
      retome (`Ctrl+Alt+Shift+P`); mudo e de volta (`Ctrl+Alt+Shift+U`). No widget: tempo, os medidores dos dois
      canais, a saúde "ok" e a legenda ao vivo (ligada por padrão nesta máquina).
- [ ] Parar (`Ctrl+Alt+Shift+S`): o resumo da gravação abre; a Fila mostra o job andando. Esperado: transcrição, quem
      fala (o primeiro job baixa os modelos do pyannote uma vez) e depois a LLM (ata, título, resumo); um balão do
      Windows avisa ao terminar. Anote quanto tempo levou.
- [ ] "Revisar falantes…": confirme as vozes (o trecho toca). Depois, o nome certo nas falas.
- Devolva: o tempo do job; o que a revisão errou; o `listener-worker.log` e o `listener-llm.log` (têm o tempo e a RAM de
  cada etapa, a sonda Q8 e o canário da LLM; se aparecer "OMP: Error #15", diga).

## Parte 4 — A reunião aberta (15 min; F5 e F5.1)
Precisa da reunião da Parte 3.
- [ ] Abra pelo balão ou pelo Histórico (`Ctrl+Alt+Shift+H`). Passe por Ata, Resumo, Ações, Transcrição, Marcadores,
      Linha do tempo e Chat.
- [ ] Transcrição: "Tocar", −10 s e +10 s; o clique numa fala toca daquele ponto (até ~1 s antes dela). A fala que toca
      fica com fundo âmbar e acompanha o som; pausado, fica; parado, some.
- [ ] Linha do tempo: uma faixa por pessoa ("Você" primeiro), os marcadores com bandeira, a pausa hachurada. O clique
      numa barra toca em até 1 s, a ±0,5 s do ponto, e o cursor vermelho anda com o som. `Ctrl` + roda amplia até 20×
      (no touchpad, `Ctrl` com dois dedos); a roda sem `Ctrl` rola na horizontal.
- [ ] Chat: uma pergunta sobre a reunião; as citações [mm:ss] tocam o trecho.
- [ ] Exportar: .md, .docx e copiar. Olhe o .docx no Word (títulos, ata, ações, transcrição). Salve um .md em
      Documentos no OneDrive. Cole o copiado no Outlook, no Teams e no Bloco de Notas.
- Devolva: print do que estranhar; o atraso do destaque se parecer passar de ~0,3 s.

## Parte 5 — Histórico, busca e perguntas com o Gemini (15 min; F5)
Precisa da Parte 2 e de ao menos uma reunião processada.
- [ ] Depois que a ata sai, em até ~1 min os vetores entram (Configurações › "Busca" › "Índice" sem "faltando").
- [ ] Histórico: busque uma palavra dita e uma ideia sem a palavra exata. Esperado: o selo "busca híbrida"; a primeira
      busca em até 1,5 s (atrás do proxy e do EDR) e as seguintes na hora. Anote o tempo da primeira.
- [ ] "Perguntar às reuniões": uma pergunta que cruze reuniões. Esperado: citações como [R1 12:03] que abrem e tocam o
      trecho certo.
- [ ] Edite o título de uma reunião e espere ~30 s: a busca acha o título novo.
- [ ] Sonda dos embeddings reais (a chave é pedida sem eco e não fica no histórico do PowerShell):
      ```powershell
      $k = Read-Host "Chave do Gemini" -AsSecureString
      $env:GEMINI_API_KEY = (New-Object System.Net.NetworkCredential('', $k)).Password
      .venv\Scripts\python.exe scripts\rag_probe.py gemini --batches 3
      Remove-Item Env:GEMINI_API_KEY; Remove-Variable k
      ```
- Devolva: a saída do `rag_probe` (linhas JSON) e o tempo da primeira busca.

## Parte 6 — Busca local, sem custo (20 min; F5.1)
- [ ] Configurações › "Busca": "Busca local" diz pronta (o e5 já está na pasta de modelos desta máquina). Se disser "não
      baixada", "Preparar busca local (135 MB)".
- [ ] Configurações › "LLM" › "Ativo para embeddings": "Local (e5-small, sem custo)" › Salvar. Esperado: a pergunta
      "Reindexar agora/Depois" com os minutos; escolha "Reindexar agora".
- [ ] Histórico: as buscas da Parte 5 de novo. Esperado: o selo "busca híbrida"; a primeira em até 1,5 s, as outras na
      hora; resultados parecidos com os do Gemini.
- [ ] RAM do app no Gerenciador de Tarefas: com o Histórico aberto depois de buscar (esperado ~620 a 670 MB) e 10 min
      depois da última busca (esperado ~290 MB). É a medida que decide a P23.
- [ ] Vetores adiados: com o Local ativo e a legenda ao vivo ligada, comece a gravar; durante a gravação, edite o
      título de uma reunião antiga e espere ~30 s (o índice dela roda e deixa os vetores para depois: Configurações ›
      "Busca" › "Índice" mostra "faltando"). Pare: em até ~1 min os vetores completam.
- [ ] Volte o "Ativo para embeddings" para o que preferir usar no dia a dia.
- Devolva: as duas medidas de RAM e o tempo da primeira busca.

## Parte 7 — Ao vivo (20 min; F4, B15)
- [ ] Configurações › "Ao vivo" › "Modelos": prontos (small e base já estão na pasta); se faltar, "Preparar modelos do
      ao vivo".
- [ ] Grave 5 min de uma chamada com a legenda: bandeja › "Ao vivo…" mostra a legenda. Anote o atraso (fala até texto) e
      se os nomes aparecem certos. No Gerenciador de Tarefas, o `--live` sem "modo de eficiência" (sem a folha verde) e
      a CPU dele.
- [ ] Sonda (precisa de um áudio de reunião em .wav, .flac ou .mp3 com fala logo no começo; se não tiver, peça que a LLM
      gere um da gravação da Parte 3):
      ```powershell
      $k = Read-Host "Token do Hugging Face" -AsSecureString
      $env:HF_TOKEN = (New-Object System.Net.NetworkCredential('', $k)).Password
      $env:LISTENER_MODELS_DIR = "$env:LOCALAPPDATA\listener-meet\models"
      .venv\Scripts\python.exe scripts\live_probe.py all "C:\caminho\da\reuniao.wav"
      Remove-Item Env:HF_TOKEN, Env:LISTENER_MODELS_DIR; Remove-Variable k
      ```
- Devolva: a saída do `live_probe`, o atraso que viu e a CPU do `--live`.

## Parte 8 — Reuniões reais (a semana; G1 a G5, B2 e B5)
- [ ] Três reuniões reais com headset em pelo menos duas plataformas (Teams e Meet ou Zoom), uma delas de ~1 h.
- [ ] Depois de cada uma: revisar falantes; e, no terminal (com a pasta do banco escolhida no assistente):
      ```powershell
      .venv\Scripts\python.exe -m listener --check "C:\dados\listener-meet"
      ```
- [ ] Com 5 ou mais reuniões: 10 perguntas em "Perguntar às reuniões"; conte quantas citações tocam o trecho certo (G5).
- [ ] Depois de uma semana: peça que a LLM some o custo registrado das chamadas e compare com a fatura do Gemini (G3).
- Devolva: a saída do `--check` de cada reunião (deriva por canal, deslocamento L/R, tamanho do banco, os blocos do ao
  vivo e da busca) e a conta do G5.

## Parte 9 — Situações de borda (quando acontecerem)
- [ ] Gravando o sistema inteiro (sem escolher app), clique "Tocar" numa reunião: o app pergunta antes de tocar, porque
      o som entraria na gravação (Q9).
- [ ] Com uma reunião tocando no player, comece a gravar o sistema inteiro (ou um app cujo loopback falhe e caia no do
      sistema): o player pausa sozinho e avisa na barra.
- [ ] Se a rede travar durante o warm-up (o glossário da LLM em voo), saia do app: fecha em poucos segundos e o log
      registra a espera.
- [ ] Clique num balão do Windows 11 e na central de notificações: abre a reunião.
- [ ] Logoff (ou desligar) durante uma gravação de teste: no próximo login, a gravação está salva e na Fila; o
      `listener.log` tem "encerramento durante a gravação … parando sem diálogo".
- Devolva: o que não se comportou assim.

## Ao terminar
- Mande os resultados de cada parte, mesmo aos poucos: a LLM registra, marca no backlog e corrige o que aparecer.
- Com o processamento real funcionando (Parte 3), o venv do F0 pode sair (D-050): peça e a LLM lista o que apagar.
- Decisões que esperam você (padrão entre parênteses; `backlog.md`, "Perguntas abertas ao dono"): P23, a RAM da busca
  local (aceitar e medir na Parte 6); U-034 a U-036 (sim); a linha dos testes no `AGENTS.md` (sim); `pytest-xdist` (sim,
  num lote pequeno); o próximo passo (a spec da F6 para você aprovar); transcrever sem o token do Hugging Face, sem
  saber quem fala no R (sim, numa spec).
- O push é seu: a main está à frente do `origin/main`.
- A metade Linux do L0 da F5.1 fica para a próxima sessão no Linux; a LLM roda e você só abre a sessão lá.
