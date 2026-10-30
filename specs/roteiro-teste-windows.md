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
- Ordem: as partes 1 a 6 numa sessão (~2 h); a 7 e as 10 a 19 quando der; a 8 e a 9 nas reuniões reais da semana.
- Para ver a RAM e a CPU de cada processo: Gerenciador de Tarefas › Detalhes, com a coluna "Linha de comando" (clique
  direito no cabeçalho › Selecionar colunas). O app é o `python.exe` com `-m listener`; o processamento, `--worker`; a
  LLM e o índice, `--worker --light`; o ao vivo, `--live`.

## Parte 1 — Primeiro uso no perfil real (15 min; B1)
- [ ] Abra o app. O assistente pede seus dados, a pasta do banco, as chaves e testa o microfone. Use uma pasta fora do
      OneDrive (a chave fica no banco em texto aberto, CP10), por exemplo `C:\dados\listener-meet`.
- [ ] Página "Chaves de acesso" do assistente (U-038): escolha a LLM (Gemini recomendado), cole a chave e "Testar
      conexão" (esperado: "ok: <modelo> respondeu em N ms"); cole o token do Hugging Face (antes, pelos links, aceite os
      termos dos dois repositórios do pyannote). Pode pular e fazer na Parte 2.
- [ ] O ícone aparece na bandeja (no Windows 11 pode cair no menu oculto, a seta ^: arraste para a barra). Clique
      direito: Gravar…, Pausar / retomar, Marcador, Mudo, Parar, Ao vivo…, Histórico…, Fila de processamento…,
      Configurações… e Sair.
- [ ] Configurações…: role até o fim. Esperado: nada cortado nem em inglês; os atalhos com "registrado" em verde.
- [ ] Leve as Configurações para o segundo monitor (1080p a 100 %) e de volta (150 %): letra nítida e tamanho certo.
- Devolva: o que estranhou, com print.

## Parte 2 — Chaves (10 min; B4 e B13)
Se você já pôs as duas no assistente, confira só o terceiro passo (os ativos e o aviso do warm-up). Senão, ou para
trocar: o primeiro grupo das Configurações, "Chaves de acesso" (U-037), com o link de onde conseguir cada uma.
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
Teams não tinha som e o canal R ficou em silêncio). Desde o D-055, "Apps tocando agora" tem uma linha por app e a
captura do Teams soma os processos dele (antes o R do Teams ficava mudo); se o R ainda ficar sem barra com alguém
falando, escolha "Sistema inteiro" e me avise.
- [ ] Abra uma chamada de teste (Teams ou Meet com alguém, ou um vídeo tocando no navegador) e `Ctrl+Alt+Shift+R`
      (warm-up): o app aparece em "Apps tocando agora" com o medidor andando; escolha-o; "Testar 3 s"; título e
      participantes.
- [ ] Grave 3 a 5 min com headset: fale e deixe o outro lado falar; um marcador com nota (`Ctrl+Alt+Shift+M`); pause e
      retome (`Ctrl+Alt+Shift+P`); mudo e de volta (`Ctrl+Alt+Shift+U`). No widget: tempo, os medidores dos dois
      canais, a saúde "ok" e a legenda ao vivo (ligada por padrão nesta máquina).
- [ ] Parar (`Ctrl+Alt+Shift+S`): o resumo da gravação abre; a Fila mostra o job andando. Esperado: transcrição, quem
      fala (o primeiro job baixa os modelos do pyannote uma vez) e depois a LLM (ata, título, resumo); um balão do
      Windows avisa ao terminar. Anote quanto tempo levou. Desde o D-056 nenhuma janela preta (terminal) aparece
      enquanto a fila processa; se aparecer uma, não a feche (fechar mata o processamento): anote a hora e me avise.
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

## Parte 10 — F5.2: fone novo, ficha da pessoa e a ata depois das vozes (20 min)
Antes: Configurações › Log no nível DEBUG (D-059: cada execução fica no log, sem nome, fala, e-mail nem chave).
- [ ] Com o "Antes de gravar" aberto, ligue o fone Bluetooth: em até 3 s ele aparece no combo do microfone com o aviso
      "novo: …", sem fechar o warm-up; desligue: o combo volta e, se ele estava escolhido, cai no padrão com aviso.
- [ ] Grave uma reunião curta com alguém do outro lado. No fim, o balão diz "pronta para revisão de falantes; a ata sai
      depois que as vozes forem identificadas" e a aba da ata diz que ela espera N voz(es).
- [ ] Na revisão, N (nova pessoa) abre a ficha: nome, cargo, empresa e e-mail. Repita um nome que já existe: o app
      pergunta "é ela?". Ao confirmar a última voz, a ata sai sozinha pela fila.
- [ ] Na reunião aberta, a ata e a transcrição mostram "Nome — Cargo". Edite a ficha (E) e mude o cargo: a aba da ata
      diz "Os nomes mudaram depois desta ata" e "Atualizar a ata com os nomes" refaz pela fila.
- [ ] "Gerar ata mesmo assim", numa reunião com voz sem nome, gera a ata sem esperar.
- [ ] Apagar pessoa (E › Apagar pessoa…) avisa o que sai e o que fica; depois, as vozes dela voltam a "sem nome".
- [ ] Feche o terminal do app com ele aberto: o `listener.log` termina com "app encerrando: o terminal do app foi
      fechado".
- Devolva: os quatro logs (`listener.log`, `listener-worker.log`, `listener-llm.log`, `listener-live.log`) e o que não se
  comportou assim.

## Parte 11 — F5.3: idioma, fila, avisos e dispositivos na gravação (30 min)
Antes: log em DEBUG; um fone (Bluetooth ou USB) desligado à mão; para o aviso e os dispositivos, uma chamada no Teams.
- [ ] Configurações › "Idioma e avisos": o padrão é Português. No "Antes de gravar", "Idioma da reunião" já vem com ele;
      o microfone começa em "Padrão do Windows (acompanha)" e a tabela de fontes tem uma linha "Sistema inteiro" por
      saída. Grave 2 min falando inglês com o combo em Inglês: legenda, transcrição e ata saem em inglês; o
      `listener-worker.log` diz "transcrição em en (o da reunião)" e nada de idioma detectado.
- [ ] Na reunião aberta, o cabeçalho diz "Idioma: en"; "Trocar para Português (pt-BR)…" pergunta com o tempo e o custo;
      confirme e veja "en → pt-BR (reprocessando)" até o fim; a ata nova sai em português e a anterior vira versão
      anterior. Salve o .docx: o Word revisa no idioma da ata.
- [ ] Fila: a coluna "Enviado" com dia e hora; a barra azul andando no job que roda, verde no concluído, vermelha no erro;
      "Ajuda" explica cada tipo e etapa.
- [ ] Avisos: ao fim do processamento e da ata, um aviso no canto da tela some sozinho em ~6 s (o mouse em cima segura; o
      clique abre a reunião). Compartilhando a tela no Teams, quem está do outro lado não vê o aviso.
- [ ] Dispositivos: grave com "Sistema inteiro" e o som nos alto-falantes; conecte o fone no meio: em até 3 s o widget diz
      "novo: <fone>" com "Usar", que troca o microfone e abre o painel com as saídas do fone primeiro; o botão 🎧 abre o
      mesmo painel. Troque a saída gravada para o fone (ou, em "acompanha", veja a troca sozinha quando o Windows passar
      o som para ele). A gravação não para; na reunião aberta, o silêncio da troca fica abaixo de 1,5 s com fio e de 3 s
      com Bluetooth. Gravando com o mudo ligado, o fone fica mudo e o microfone antigo volta.
- [ ] Desligue o fone escolhido: o widget avisa e volta ao padrão; religue: a gravação volta para o fone sozinha.
- Devolva: os quatro logs e o que não se comportou assim.

## Parte 12 — F5.4: as telas novas no claro, no escuro e no notebook (30 min)
Antes: uma reunião processada com ata (a da Parte 3 serve) e, se der, o notebook a 150 %.
- [ ] Tema: com o app aberto e a reunião, o Histórico e as Configurações à vista, troque o Windows entre claro e escuro
      (Configurações › Personalização › Cores). Tudo troca sem reabrir: widget, ícone da bandeja, cartões, selos,
      bandeiras dos marcadores, lupa da busca. Nada fica com fundo branco no escuro nem texto escuro sobre escuro.
- [ ] Contraste alto (Acessibilidade › Temas de contraste): o app usa as cores do sistema e continua legível, inclusive
      a linha selecionada, os selos e as faixas. Troque de um tema de contraste para outro (Aquático para Deserto):
      se as cores não acompanharem, diga se volta ao reabrir o app. Tamanho do texto em 125 % (Acessibilidade ›
      Tamanho do texto): na próxima abertura o texto cresce e nada sai cortado. Com "Efeitos de animação" desligado
      (Acessibilidade › Efeitos visuais), a faixa azul do "rodando" na fila fica parada.
- [ ] No notebook a 150 % e a 100 %: "Antes de gravar", Configurações, reunião, Histórico, fila e revisão abrem
      inteiros dentro da tela, sem texto cortado. O widget tem 360 px de largura e cresce para cima com a legenda e o
      alerta, sem sair da tela; Parar em vermelho, Cancelar afastado, o mudo como microfone cortado.
- [ ] Configurações: navegue pelas 9 páginas. A roda do mouse rolando a página por cima de um número ou de uma lista não
      muda o valor. Salve sem mexer em nada: o `listener.log` não mostra troca de configuração. Ponha um atalho
      inválido (ex.: `ctrl+ç`) e salve: a página Atalhos abre com o campo marcado em vermelho e o motivo numa faixa.
- [ ] Reunião: a ata aparece formatada (títulos, listas); "Editar" mostra o Markdown, "Cancelar edição" volta sem
      mudar, editar e Salvar grava. As vozes sem nome aparecem como "Voz 2", "Voz 3"… iguais na transcrição, na linha
      do tempo e na revisão. Na revisão, os atalhos de sempre (Enter, L, O, N, M, I, Espaço, E) funcionam.
- [ ] Teclado e Narrador: Tab percorre na ordem da tela e o anel de foco só aparece pelo teclado; Esc fecha as janelas;
      com o Narrador (Ctrl+Win+Enter), os botões só de ícone do widget dizem o nome. Enter num botão com o anel aciona
      esse botão (no "Antes de gravar", Tab até Cancelar e Enter cancela, não grava); Enter num campo aciona o botão
      azul da janela.
- [ ] Abra e feche três reuniões, a revisão e o Histórico e saia pela bandeja: o app fecha sem a mensagem "Python
      parou de funcionar" (Visualizador de Eventos › Aplicativo, sem erro do `python.exe` nesse horário).
- Devolva: print de qualquer tela estranha (cor, corte, sobreposição) no claro e no escuro, e o `listener.log`.

## Parte 13 — F5.5: "Voz 2" na ata, na exportação e na busca (10 min)
Antes: uma reunião com pelo menos uma voz sem nome e a chave da LLM (a da Parte 3 serve).
- [ ] Na primeira abertura depois da atualização, as atas antigas não pedem "Atualizar a ata com os nomes" só por isso.
      O `listener.log` mostra a varredura do índice uma vez (quantas reuniões entraram), e ela não volta na abertura
      seguinte.
- [ ] Gere a ata de novo numa reunião com voz sem nome: a ata, as ações e o executivo falam "Voz 2", o mesmo número
      da transcrição e da linha do tempo, nunca "SPK_1". Exporte o .md e o .docx: o mesmo "Voz 2".
- [ ] No Histórico, busque uma frase dessa voz: o trecho e o "Copiar com citação" mostram "Voz 2". Pergunte às
      reuniões algo que ela disse: a resposta cita "Voz 2".
- [ ] Numa reunião em inglês, a ata mantém "Voz 2" (não "Voice 2").
- [ ] Com a ata nova pronta, mescle duas vozes sem nome na revisão: a reunião passa a oferecer "Atualizar a ata com os
      nomes".
- Devolva: o trecho da varredura no `listener.log` e qualquer "SPK_" ou "Voice" que aparecer.

## Parte 14 — F5.6 L3: excluir reunião (15 min)
Antes: anote, em Pessoas, quantas pessoas têm perfil de voz; tenha duas reuniões de teste que pode perder.
- [ ] No Histórico, selecione as duas (Ctrl+clique), clique com o botão direito e "Excluir 2 reuniões…": a confirmação
      lista as duas, diz o que sai e o que fica e o foco começa em "Cancelar". Confirme: "Excluindo 1 de 2…", depois o
      aviso de pronto, e elas somem da lista, da busca e das ações abertas.
- [ ] Os perfis de voz continuam os mesmos: abra a próxima reunião processada e veja os nomes reconhecidos como antes.
- [ ] Com uma reunião aberta numa janela, exclua-a pelo Histórico: a janela fecha antes. Com o player tocando, ele para.
- [ ] Comece a processar uma reunião de teste e exclua-a no meio: o app não trava, a fila não mostra erro e o worker
      continua (o log dele mostra "evento … não gravado").
- [ ] Durante uma gravação, tente excluir a reunião que está sendo gravada: ela aparece à parte, com o motivo, e o
      botão de excluir fica apagado quando é a única.
- [ ] Depois de alguns minutos parado, o `listener.log` mostra a liberação de espaço (bytes) ou "pendente" com o
      motivo; anote quanto tempo levou e o tamanho do `listener-audio-AAAA-MM.db` antes e depois.
- Devolva: o trecho do `listener.log` da exclusão e da liberação de espaço, e qualquer erro na tela.

## Parte 15 — F5.6 L1: ata, resumo e ações só a pedido (25 min)
Antes: a chave do Gemini configurada; uma conversa de 3 min com pelo menos duas pessoas.
- [ ] Grave e, no "Gravação salva", deixe "Gerar ata e ações" e "Gerar resumo" desmarcadas (o padrão na primeira vez) e
      dê OK. Depois do processamento, sem clique: título, vozes identificadas e transcrição revisada; a linha sob os
      fatos da reunião mostra "não gerado" nos dois, as abas mostram "Ata ainda não gerada." e o custo da reunião não
      tem chamada de ata.
- [ ] Grave outra e marque "Gerar ata e ações" antes do OK: abre o popup ("a identificação das vozes ainda não rodou",
      a recomendação e o custo "estimado pela duração"), com "Gerar quando todos tiverem nome" em destaque; Enter
      escolhe ele. Teste também o Esc no popup: nada é pedido. Na próxima gravação, a caixa já vem marcada.
- [ ] Com o pedido "quando todos tiverem nome": depois do processamento, a linha diz "pedido · espera N vozes"; dê
      nome à última voz na revisão: a ata sai uma vez ("gerando", depois "gerado em dd/mm").
- [ ] Numa reunião com voz sem nome, "Gerar resumo" e "Gerar agora mesmo assim": sai já, com "Voz 2" (a ata vai junto
      se faltar). Anote quanto tempo levou.
- [ ] Mude o nome de uma voz numa reunião com ata: a linha diz "desatualizado (nomes)" e a faixa oferece "Atualizar a
      ata com os nomes", que abre o popup.
- [ ] Troque o idioma de uma reunião com ata, com "Gerar ata e ações" marcada: o popup inclui a ata no custo; no fim do
      reprocessamento a ata sai no idioma novo. Sem caixa, a ata fica "desatualizado (transcrição)".
- [ ] Sem chave (tire-a por um momento em Configurações, Chaves de acesso): as caixas do "Gravação salva", os botões da
      reunião e "Resumo parcial" no "Antes de gravar" ficam apagados, com o aviso de configurar a chave.
- [ ] Se tiver uma reunião antiga com a revisão pela metade, termine a revisão: nenhuma ata nova sai sozinha.
- [ ] Ao vivo: no "Antes de gravar", "Resumo parcial" vem desmarcada; grave 5 min com a legenda. A janela diz "o
      automático está desligado nesta gravação"; "Atualizar resumo agora" gera um parcial; o `--check` da reunião
      mostra 0 parciais automáticos. Marque a caixa numa segunda gravação: o parcial sai sozinho no intervalo.
- [ ] No notebook a 150 %: a reunião com a linha nova cabe sem cortar os botões.
- Devolva: as linhas do `listener.log` com "pedido", o custo estimado contra o cobrado (Custos), o tempo da ata, se o
  título ficou bom e qualquer popup ou botão que confundiu.

## Parte 16 — F5.6 L2: os prompts nas Configurações (15 min)
Antes: **reinicie o app**, porque a página nova só aparece depois disso. Tenha a chave do Gemini e uma reunião
processada com falas.
- [ ] Configurações, Prompts: a lista mostra as 11 tarefas e quando cada uma roda. Escolha "Ata e ações". Aparecem as
      instruções do app em inglês, o selo "Padrão do app" e a contagem de caracteres; embaixo, "O que vai junto, fixo".
      O Tab sai do editor em vez de escrever no texto.
- [ ] Escreva instruções em português, por exemplo: "Você escreve a ata para a diretoria; destaque valores, prazos e
      quem decidiu. Escreva em {idioma}.". Clique em Salvar e peça a ata numa reunião. Confira:
      - a ata segue as suas instruções;
      - as citações [mm:ss] e "Voz 2" continuam certas;
      - em Custos, a chamada aparece como `minutes/1.2+u` seguido de seis letras e números.
- [ ] Deixe o texto vazio e clique em Salvar. Faça o mesmo com `{cliente}` no texto e depois com mais de 4.000
      caracteres. Em cada caso, o Salvar abre a página e a tarefa, o editor fica com borda vermelha e uma faixa explica
      o problema. Nada é gravado.
- [ ] "Restaurar padrão" volta ao texto do app, com o selo "Padrão do app".
- [ ] Em Identificação das vozes, Transcrição revisada e Título aparece o aviso de que mexer ali piora a identificação
      e a transcrição.
- [ ] Edite um prompt enquanto um job de LLM está rodando. O job termina com o texto antigo e o seguinte usa o novo.
- [ ] Se uma ata sair fora do formato com as suas instruções, a linha da reunião mostra "falhou", a nota e o botão
      "Restaurar o prompt padrão". Clique nele e depois em "Gerar de novo".
- [ ] Se você usa a transcrição pela LLM: edite as instruções dela e processe uma reunião. O `listener.log` mostra a
      linha com `+u`.
- [ ] No notebook a 150 % e com texto ampliado, a página cabe na tela. Com o leitor de tela, a lista e os botões são
      lidos com nome.
- Devolva:
  - o resultado de uma busca no `listener.log` por um pedaço das suas instruções (não pode aparecer nada);
  - a ata gerada com as suas instruções;
  - qualquer coisa que confundiu.

## Parte 17 — F5.7: o aviso de reunião sem gravação (40 min, numa chamada de verdade)
Antes: **reinicie o app**. Tenha à mão:
- uma chamada do Teams (pode ser só você, por "Reunir agora");
- um Meet no Chrome;
- se der, um segundo monitor e o fone de comunicação.

Nada aqui grava sozinho: o app só grava depois do seu clique em Gravar.
- [ ] Primeiro a sonda, com o app aberto e sem gravar:
      `.venv\Scripts\python.exe scripts\detect_probe.py --seconds 120`, durante a chamada do Teams. Ligue e desligue o
      mudo no meio. Repita no Meet. A saída só traz o executável, o pid, as contagens e os picos, nunca o título.
- [ ] Teams em chamada, sem gravar:
      - o cartão "Reunião detectada" aparece no canto em até ~15 s;
      - o foco não sai do Teams: o que você digita no chat vai para o Teams, nada vai para o cartão, e o Espaço de
        falar continua funcionando.
- [ ] Compartilhe a tela no Teams com o cartão à vista e confira:
      - quem assiste não vê o cartão;
      - "Gravar…" traz o "Antes de gravar" para a frente, com o Teams escolhido e a faixa "Fonte: Teams, pelo aviso de
        reunião";
      - o Enter grava;
      - quem assiste também não vê o "Antes de gravar".
- [ ] Deixe o cartão sumir sozinho: são 20 s, e com o mouse em cima ele não some. Confira:
      - a dica da bandeja diz "reunião no Teams sem gravação";
      - o primeiro item do menu, em negrito, é "Gravar a reunião do Teams…";
      - 2 min depois vem um lembrete, um só.
- [ ] "Agora não": nada mais até a chamada acabar. Numa chamada nova, depois de 30 s sem chamada e mais 2 min, ele
      avisa de novo.
- [ ] Durante uma chamada, abra o "Antes de gravar" pela bandeja e cancele. O aviso ainda vem (D-066).
- [ ] Com uma chamada pendente, o atalho de Gravar e o clique esquerdo na bandeja abrem o "Antes de gravar" com o app.
- [ ] Abra as Configurações com o cartão à vista: o cartão some e volta quando elas fecham (D-066).
- [ ] "Não avisar para o Teams", na bandeja. Em Configurações > Idioma e avisos > "Reunião sem gravação", o Teams está
      desmarcado. Marque e salve.
- [ ] Ponha o fone como dispositivo de comunicação e o alto-falante como padrão, e grave o Teams pelo aviso. A
      gravação tem a voz de quem fala no Teams.
- [ ] 20 min com o Teams parado, um vídeo no YouTube, música no Spotify e as notificações do Teams: nenhum aviso.
- [ ] Com dois monitores e a barra de tarefas em outra posição:
      - o cartão fica no canto da tela do app;
      - os outros avisos do app sobem acima dele.
- [ ] Ligue o Narrador: o cartão é lido quando aparece, sem receber o foco.
- [ ] A 150 % e no tema escuro, o cartão e o cartão das Configurações cabem na tela.
- [ ] A saída do app no meio de uma troca de dispositivo (o sensor pode estar preso no sistema de áudio):
      - troque o dock ou o fone e feche o app pelo Sair logo em seguida;
      - faça um logoff com o app aberto.
      Nos dois casos, o Windows não mostra nenhum "parou de funcionar". O `listener.log` traz "aviso de reunião sem
      gravação: parado" e, se o sensor ficou preso, "saída imediata".
- Devolva:
  - a saída da sonda;
  - quanto tempo o aviso levou para aparecer;
  - todo aviso falso: qual app e o que você fazia;
  - o resultado do compartilhamento de tela;
  - numa reunião do Teams e do Zoom pelo navegador, só o formato do título da aba, sem o nome da reunião (é para
    reconhecer a reunião no navegador além do Meet);
  - o resultado de uma busca no `listener.log` pelo nome da reunião (não pode aparecer nada).

## Parte 18 — D-067: o som segue o aparelho escolhido no Windows (15 min)
Antes: **reinicie o app**. Tenha o alto-falante e o fone ligados; se der, também um fone Bluetooth.
- [ ] Primeiro a sonda, sem som nenhum (ela toca silêncio): `.venv\Scripts\python.exe scripts\probe_output_follow.py
      --seconds 120`. Durante os 2 min, troque a saída padrão no Windows várias vezes: ida, volta e algumas trocas
      seguidas rápidas. Depois de cada troca, a linha deve voltar a "segue" em até ~2 s.
- [ ] No app, toque uma reunião e confira cada caso:
      - trocar a saída no Windows com o som tocando: o som passa para o aparelho escolhido em até ~2 s, do mesmo ponto
        (pode repetir uns 0,2 s e ter um corte curto na troca);
      - pausar, trocar e retomar;
      - trocar e logo usar −10 s, +10 s ou clicar em outro ponto;
      - trocar com o player parado e apertar Tocar;
      - tirar o fone com o som tocando: o som segue para o novo padrão, sem erro.
- [ ] Na revisão, o fim de um trecho de 5 s sai inteiro, sem cortar a última palavra.
- [ ] No "Antes de gravar", "Testar 3 s":
      - com uma saída escolhida, toca nela;
      - com o padrão, toca no aparelho padrão de agora.
- [ ] O medidor do microfone com "Padrão do Windows (acompanha)" segue a troca do microfone padrão.
- [ ] Fone Bluetooth conectado com o app já aberto e posto como padrão:
      - sem gravação, o som vai para ele em ~2 s;
      - com gravação, a barra do player avisa e o som vai para ele quando a gravação acabar.
- Devolva:
  - a saída da sonda;
  - qualquer caso em que o som não foi para o aparelho escolhido: qual aparelho e o que você fazia;
  - o trecho do `listener.log` da troca (só traz ids de aparelho, nunca nomes).

## Parte 19 — F5.8: o app pelo `listener-meet.exe` (30 min, numa reunião de verdade)
Antes: feche o app do venv (bandeja › Sair) e espere sumir o `--worker` no Gerenciador de Tarefas. Nunca abra o `.exe` e
o app do venv juntos: os dois usam o mesmo banco (pergunta 3 da F5.8).
- [ ] Gere o executável: uma vez, `.venv\Scripts\python.exe -m pip install -r requirements\build-windows.txt`; depois,
      `.venv\Scripts\python.exe scripts\build_exe.py` (~7 min, a máquina segue usável). A última linha diz o tempo, o
      tamanho e o número de arquivos.
- [ ] Copie a pasta `build\exe\dist\listener-meet` inteira para um caminho local e curto, fora do OneDrive (por exemplo
      `C:\Apps\listener-meet`). Abra o `listener-meet.exe` com duplo clique: o app abre sem janela preta, com a marca
      na bandeja, na barra e no ícone do arquivo no Explorer. Para fixar: clique direito no ícone da barra › Fixar.
- [ ] O Gerenciador de Tarefas e o mixer de volume mostram "listener-meet". Anote o tempo da primeira abertura e o de
      uma segunda.
- [ ] Grave uma reunião de verdade com a captura por app (Teams ou Meet) e o ao vivo ligado. Na primeira gravação o
      Windows pode pedir o microfone para o caminho novo. Confira:
      - as legendas aparecem;
      - ao parar, o processamento roda (em Detalhes, `listener-meet.exe` com `--worker`), sem janela preta e sem o
        cursor de "carregando" a cada processo;
      - peça a ata e toque um trecho;
      - durante a gravação, o aviso de reunião sem gravação não aparece por causa do próprio app.
- [ ] A bandeja e os atalhos globais funcionam como no venv.
- [ ] Abra o `.exe` direto de `build\exe\dist\listener-meet` e, com ele aberto, rode o build de novo: ele recusa
      ("feche o listener-meet...") e a pasta fica intacta. A cópia em outro lugar não trava o build.
- [ ] Antivírus: `Get-MpThreatDetection` não traz nada novo, e nenhum aviso do SmartScreen ao abrir.
- Devolva:
  - a última linha do build e os dois tempos de abertura;
  - qualquer caixa de erro (print);
  - as linhas de erro do `listener.log` e do `listener-worker.log`;
  - a saída do `Get-MpThreatDetection`.

## Ao terminar
- Mande os resultados de cada parte, mesmo aos poucos: a LLM registra, marca no backlog e corrige o que aparecer.
- Com o processamento real funcionando (Parte 3), o venv do F0 pode sair (D-050): peça e a LLM lista o que apagar.
- Decisões que esperam você (padrão entre parênteses; `backlog.md`, "Perguntas abertas ao dono"): P23, a RAM da busca
  local (aceitar e medir na Parte 6); a linha dos testes no `AGENTS.md` (sim); `pytest-xdist` (sim, num lote
  pequeno); o próximo passo (a spec da F6 para você aprovar); transcrever sem o token do Hugging Face, sem saber quem
  fala no R (sim, numa spec); "Voz 2" também na ata e na exportação (sim, numa spec); os ~5 ms a mais na abertura do
  "Antes de gravar" e da revisão (aceitar).
- O push é seu: a main está à frente do `origin/main`.
- A metade Linux do L0 da F5.1 fica para a próxima sessão no Linux; a LLM roda e você só abre a sessão lá.
