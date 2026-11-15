# Melhorias de UX (U-nnn)

Quando ler: ao escrever a spec de uma feature, ou quando o código citar um `U-nnn`. É o catálogo das melhorias de
interface aprovadas pelo dono (CP12). A coluna "Entrega" diz a feature ou a decisão que implementou cada uma; sem
outra indicação, a melhoria está implementada. Melhoria nova entra aqui com o próximo número e, na spec, como pergunta
"incluir? sim/não". Os números que faltam na sequência não foram usados. Tamanho-alvo: 24 KB.

| # | Melhoria | Onde | Entrega |
| --- | --- | --- | --- |
| U-001 | "Apps tocando agora": processos emitindo áudio com medidor ao vivo; um clique escolhe a fonte; app de reunião primeiro | Warm-up | F1 |
| U-002 | Mudo global: botão e atalho mutam o mic no Windows e gravam silêncio; estado refletido no widget | Widget | F1 |
| U-003 | Marcador com nota por atalho: popup junto ao cursor, Enter salva, Esc cancela; a nota entra na ata | Widget | F1 |
| U-004 | Saúde dos canais verde, âmbar, vermelho; R mudo por 20 s dispara aviso com "trocar fonte" | Widget | F1 |
| U-005 | Fila de processamento visível: progresso por etapa, pausar, retomar; aviso ao gravar com fila cheia | Janela principal | F2 |
| U-006 | Revisão por teclado: card por falante com 5 s de áudio; Enter aceita; setas navegam; "é a mesma pessoa que" mescla | Revisão | F2 |
| U-007 | Caixa de "vozes desconhecidas": clusters sem nome de todas as reuniões, agrupados por similaridade | Perfis | pendente (F6) |
| U-008 | Linha do tempo da reunião com faixas por falante e marcadores; clique toca | Histórico | F5.1 |
| U-009 | Custo e latência por provedor: testar conexão mostra latência; tokens e custo por reunião e por mês | Configurações | F3 |
| U-010 | Indicador de tamanho do banco com projeção; backup com progresso | Configurações | pendente (F6) |
| U-011 | Exportar ata e transcrição (.md, .docx, área de transferência) sob demanda (D-012) | Histórico | F5, F5.1 |
| U-012 | Notificação de reunião detectada com "Gravar agora" (último preset) e "ignorar por 1 h" (D-010) | Bandeja | substituída pela U-094 |
| U-013 | Assistente de primeiro uso: token HF, chave do provedor, teste do mic, app de reunião | Primeiro uso | parcial (F1, D-054, F5.11); o resto na F6 |
| U-014 | Tema claro e escuro seguindo o sistema; DPI alto | Geral | F5.4; escolher o tema à mão na F6 |
| U-015 | Preset "sala de reunião": diariza também o L e desliga "L é sempre eu" | Warm-up | F1, F2 |
| U-016 | Detector de eco e alinhamento por correlação cruzada L×R: aviso no warm-up; deslocamento L/R no `--check` | Warm-up, `--check` | F1.1 |
| U-017 | Instrução para fixar o ícone da bandeja no Windows 11 (ícones novos vão para o menu oculto) | Primeiro uso | F1.1 |
| U-021 | Janela "Ao vivo": legendas rolando com nome e cargo e o resumo parcial, com "atualizar agora" | Ao vivo | F4 |
| U-022 | Clique numa legenda cria um marcador naquele instante com o texto dela | Ao vivo | F4 |
| U-023 | Possíveis decisões e ações do parcial com "marcar" (marcador que a ata lê) | Ao vivo | F4 |
| U-024 | Trechos da busca agrupados por reunião, termo destacado e "abrir neste ponto" (abre, rola até a fala e toca) | Histórico | F5 |
| U-025 | Destaque contínuo: a fala que toca acompanha o áudio na transcrição e na linha do tempo | Reunião aberta | F5.1 |
| U-026 | "Ações abertas" de todas as reuniões: dono, prazo, origem, atrasadas em destaque, marcar feita | Histórico | F5 |
| U-027 | "Citação certa?" sim/não em cada resposta do chat global (mede o G5) | Histórico | F5 |
| U-028 | Atalho global para abrir o Histórico com o cursor na busca | Atalhos | F5 |
| U-029 | "Copiar com citação": fala, trecho ou resposta com reunião, data e [mm:ss] | Histórico | F5 |
| U-030 | .md que não salva: "está aberto em outro programa (Word, Excel…); feche e tente de novo, ou salve com outro nome" no lugar do erro cru | Reunião aberta | D-049 |
| U-031 | Áudio do player pré-aquecido 2 s depois da bandeja: o primeiro "Tocar" do processo cai de 2 a 5 s para ~0,5 s | Player | D-049 |
| U-032 | Vírgula decimal nos números da interface ("446,4 KB", "atraso 1,5 s", "correlação 0,42") | Todas | D-049 |
| U-033 | Warm-up: o combo do microfone não engole a largura (a coluna "Janela" das fontes cabe) e o grupo dele sem vazio | Warm-up | D-049 |
| U-034 | Linha do tempo sem falas: o rodapé repete o "Sem falas nesta reunião." do meio da vista; com áudio, o rodapé ensina o clique ("clique para ouvir daquele ponto") | Reunião aberta | F5.4 |
| U-035 | Voz sem pessoa como "Voz 1", "Voz 2"… por reunião, não pelo rótulo interno do pyannote ("Voz SPEAKER_03"), na linha do tempo, na transcrição e na revisão | Reunião aberta | F5.4, F5.5 |
| U-036 | Estado do índice nas Configurações com o nome legível do modelo ("e5-small, local"), não a chave interna (`local/multilingual-e5-small@384`), e sem mandar "preparar em Configurações (Busca)" quem já está nelas | Configurações | F5.4 |
| U-037 | "Chaves de acesso" como primeiro grupo das Configurações (Gemini, Hugging Face, GPT e Claude, com Testar, o estado e onde conseguir) e o warm-up avisando antes de gravar quando falta chave ou token; as mensagens apontam para lá | Configurações, Warm-up | D-053 |
| U-038 | O assistente da primeira inicialização pede, logo depois da pasta do banco, a LLM (Gemini, GPT ou Claude) com a chave e o "Testar conexão", e o token do Hugging Face, tudo opcional; a escolhida vira a LLM ativa (com o Claude, a busca por sentido fica na local) | Assistente | D-054 |
| U-039 | A revisão de falantes só abre sozinha com voz a confirmar; sem nenhuma, o aviso diz "processada, sem voz a confirmar" e nenhuma janela sobe por cima | Bandeja, Revisão | D-055 |
| U-040 | "Apps tocando agora" com uma linha por app (o Teams novo aparecia dividido e a linha marcada não tinha o som da chamada) | Warm-up | D-055 |
| U-041 | Ficha da pessoa ao nomear a voz: nome, cargo, empresa e e-mail; editar e apagar pessoa; a LLM e a busca com o cargo de quem fala (o e-mail fica local) | Revisão | F5.2 |
| U-042 | A ata, o executivo, o pré-resumo e o título só depois de as vozes serem identificadas (a identidade por contexto e a transcrição revisada saem antes), com "Gerar ata mesmo assim"; "Atualizar a ata com os nomes" quando eles mudam depois | Revisão, Reunião aberta | F5.2 |
| U-043 | Dispositivo de áudio conectado com o app aberto (fone Bluetooth) aparece sem reiniciar; o "Antes de gravar" atualiza a lista sozinho | Warm-up, Assistente | F5.2 |
| U-044 | Idioma padrão nas Configurações (pt-BR ou en) para transcrição, legenda ao vivo e LLM | Configurações | F5.3 |
| U-045 | Idioma da reunião no "Antes de gravar", com o padrão já escolhido | Warm-up | F5.3 |
| U-046 | Trocar o idioma de uma reunião gravada e reprocessar nele (transcrição e ata) | Reunião aberta | F5.3 |
| U-047 | Fila com a coluna "Enviado", a porcentagem como barra colorida pelo estado e o botão Ajuda (o que cada tipo e etapa faz) | Fila | F5.3 |
| U-048 | Aviso do próprio app que some sozinho quando algo fica pronto (ata, resumo, processamento, erro), sem o título durante a gravação | Canto da tela | F5.3 |
| U-049 | Trocar microfone e saída gravada durante a gravação, pelo painel Dispositivos do widget, com os aparelhos conectados no meio dela | Widget | F5.3 |
| U-050 | "novo: <nome>" com "Usar" no widget quando um aparelho chega durante a gravação | Widget | F5.3 |
| U-051 | Uma linha "Sistema inteiro: <saída>" por saída ativa na tabela de fontes | Warm-up | F5.3 |
| U-052 | Segoe UI Variable: corpo 14 px, legenda 12, título 20; segue o Tamanho do texto do Windows | Geral | F5.4 |
| U-053 | Ícones próprios em SVG, de uma cor e no tema, no lugar de emoji e caracteres soltos | Geral | F5.4 |
| U-054 | Nota e estado com 4,5:1 nos dois temas; estado nunca só pela cor (ícone ou texto junto) | Geral | F5.4 |
| U-055 | Anel de foco de 2 px só pelo teclado, em botão, caixa, aba e item de lista | Geral | F5.4 |
| U-056 | Um primário preenchido por janela (Gravar, Salvar, Próximo, Concluir); destrutivo em vermelho | Geral | F5.4 |
| U-057 | Aviso dentro da tela vira faixa com ícone, cor do tipo e ação, no lugar do texto laranja | Geral | F5.4 |
| U-058 | Tabelas: cabeçalho à esquerda, sem nº de linha, linha inteira selecionada, texto estica | Geral | F5.4 |
| U-059 | Estado vazio com ícone, frase e ação (Ata ainda não gerada + Gerar ata) | Geral | F5.4 |
| U-060 | Widget no tema, 360 px fixos; o alerta numa linha embaixo, na cor do tipo, sem alargar | Widget | F5.4 |
| U-061 | Widget: botões de ícone 36 × 32 com atalho na dica; Parar vermelho; Cancelar afastado | Widget | F5.4 |
| U-062 | Widget: mudo = microfone cortado; L, R e ao vivo com ícone; apagado salvando; hora h:mm:ss | Widget | F5.4 |
| U-063 | Medidor com trilho nos dois temas e zonas: verde até -18 dBFS, âmbar até -6, vermelho acima | Widget | F5.4 |
| U-064 | Dispositivos: atual com marca, nome e tipo em duas linhas, Novo em selo, resultado em faixa | Painel | F5.4 |
| U-065 | Antes de gravar em duas colunas (Áudio; Reunião), até 984 × 600 px | Warm-up | F5.4 |
| U-066 | Antes de gravar: falta de chave em faixa no rodapé, ao lado de Gravar (primário com ícone) | Warm-up | F5.4 |
| U-067 | Fontes com medidor próprio por linha e Reunião em selo; sala e ao vivo numa linha Opções | Warm-up | F5.4 |
| U-068 | Marcador e avisos no tema, com ícone do tipo além da faixa e fechar de 32 × 32 px | Avisos | F5.4 |
| U-069 | Resumo da gravação com ícone de sucesso; a lista de marcadores só quando há | Resumo | F5.4 |
| U-070 | Bandeja: marca do app com o estado em selo de forma e cor, legível a 16 px | Bandeja | F5.4 |
| U-071 | Configurações em páginas com navegação à esquerda, 960 × 640; um Salvar para todas | Config. | F5.4 |
| U-072 | Erro de validação abre a página, marca o campo e explica numa faixa, sem caixa de mensagem | Config. | F5.4 |
| U-073 | Sim/não em caixa; número com vírgula e faixa da `SPECS`; taxa em lista; mesmo valor salvo | Config. | F5.4 |
| U-074 | Aplica agora e na próxima gravação no subtítulo da página; na linha só o que difere | Config. | F5.4 |
| U-075 | Chaves, atalhos e downloads com selo (Falta a chave, Conectado, Registrado, Falhou) | Config. | F5.4 |
| U-076 | Sem jargão interno (F2, F3, F4, F6, CP10) na tela; cabeçalho da tabela de preços inteiro | Config. | F5.4 |
| U-077 | Fila de 1040 × 480: Estado em selo, barra nos tokens, Ajuda e Fechar numa linha só | Fila | F5.4 |
| U-078 | Reunião: título de 20 px sem moldura até o foco, fatos em legenda, menus com a seta inteira | Reunião | F5.4 |
| U-079 | Ata, executivo e pré-resumo formatados; Editar abre o Markdown; origem em dd/mm/aaaa | Reunião | F5.4 |
| U-080 | Ações: Ação estica e quebra linha; prazo dd/mm/aaaa; atrasada em selo vermelho | Reunião, Histórico | F5.4 |
| U-081 | Transcrição e Ao vivo: [mm:ss] em legenda, falante em negrito com ponto na cor dele | Reunião | F5.4 |
| U-082 | Linha do tempo nas cores `speaker`; marcador em `text` com bandeira; cursor em `accent` | Reunião | F5.4 |
| U-083 | Player: ícones de tocar, pausar, -10 s e +10 s junto do texto; tempo de largura fixa | Reunião | F5.4 |
| U-084 | Histórico: busca com lupa, índice na linha das abas, Estado em selo, custo à direita | Histórico | F5.4 |
| U-085 | Histórico: trechos e respostas do chat nas cores dos tokens (HTML sem cor fixa) | Histórico | F5.4 |
| U-086 | Revisão: voz em cartão com estado em selo; atalho em selo de tecla; primário segue a etapa | Revisão | F5.4 |
| U-087 | Ficha da pessoa: Apagar pessoa em vermelho à esquerda, longe de Salvar | Revisão | F5.4 |
| U-088 | Assistente sem a faixa vazia do Aero: título de 20 px, Passo n de 6, Próximo primário | Assistente | F5.4 |
| U-089 | Ao parar: "Gerar ata e ações" e "Gerar resumo" no resumo da gravação, com a última escolha | Resumo | F5.6 |
| U-090 | Pedido de ata ou resumo com popup: vozes sem nome, custo e "Gerar quando todos tiverem nome" | Reunião | F5.6 |
| U-091 | Resumo parcial do ao vivo como caixa no "Antes de gravar", desmarcada | Warm-up | F5.6 |
| U-092 | Página Prompts: as instruções de cada tarefa da LLM editáveis, o formato à vista, restaurar padrão | Config. | F5.6 |
| U-093 | Excluir reunião no Histórico, com seleção múltipla e confirmação; perfis de voz ficam | Histórico | F5.6 |
| U-094 | Aviso de reunião sem gravação: cartão sem foco e fora da captura com "Gravar…" (abre o "Antes de gravar" com o app como fonte) e "Agora não" (até a chamada acabar), um lembrete, a bandeja e o cartão nas Configurações; substitui a U-012 | Aviso, bandeja | F5.7 |
| U-095 | Ícone do `.exe` (Explorer e barra) com a marca do app no estado ocioso, um tamanho desenhado por escala; a descrição do arquivo "listener-meet" | Executável | F5.8 |
| U-096 | Perguntar às reuniões: sugestões de perguntas no estado vazio (contagem com uma pessoa, ações atrasadas por dono, custo da LLM por mês) | Histórico | F5.10 |
| U-097 | Resposta da base: "Ver dados" abre a tabela de uma consulta (refeita na sandbox), com "Copiar como tabela" | Histórico | F5.10 |
| U-098 | Botões da revisão pelo resultado, com o nome e sem gênero ("É Ana [Enter]"), conforme o estado da voz | Revisão | F5.11 |
| U-099 | Tocar 5 s no cartão da voz selecionada | Revisão | F5.11 |
| U-100 | Ações raras da revisão num menu "Mais", com as mesmas teclas | Revisão | F5.11 |
| U-101 | O que a confirmação ensina ao perfil e como desfazer, numa linha | Revisão | F5.11 |
| U-102 | Cabeçalho da reunião numa linha: título que grava sozinho, "Exportar" e "⋯" | Reunião | F5.11 |
| U-103 | Gerar ata e resumo dentro das abas, com o estado da F5.6 na linha de origem | Reunião | F5.11 |
| U-104 | `[mm:ss]` da ata e o "Quando" das ações tocam a fala | Reunião | F5.11 |
| U-105 | Atalhos do player: Espaço, ← e → | Reunião | F5.11 |
| U-106 | Rótulos pelo resultado fora das Configurações ("pela voz", "pelo contexto", "você") | Todas | F5.11 |
| U-107 | "Descartar gravação" com lixeira, e o Enter da confirmação continua gravando; "Cancelar processamento" na fila | Widget, fila | F5.11 |
| U-108 | "Você" em toda tela; o nome na ata, na exportação e na LLM | Todas | F5.11 |
| U-109 | Plural de verdade e datas em dd/mm/aaaa | Todas | F5.11 |
| U-110 | A saída escolhida pela lista do painel do widget, com medidor por linha | Warm-up | F5.11 |
| U-111 | Sem modelos do ao vivo: caixas desmarcadas e "Preparar agora" no aviso | Warm-up | F5.11 |
| U-112 | Um aviso só, "Falta configurar", com a ação de cada item | Warm-up | F5.11 |
| U-113 | "Testar" no token do Hugging Face; o assistente conta os passos que mostra | Config., assistente | F5.11 |
| U-114 | Reunião sem fala diz qual canal ficou sem som e qual fonte estava escolhida | Reunião | F5.11 |
| U-115 | "Gravação salva com falha" quando um canal ficou sem áudio | Resumo | F5.11 |
| U-116 | Alerta do widget e etapa da fila inteiros, sem corte | Widget, fila | F5.11 |
| U-117 | Rodapé da transcrição claro; a caixa da revisada só com uma revisada | Reunião | F5.11 |
| U-118 | Bandeira fora do rótulo da linha do tempo; duração no player antes de tocar | Reunião | F5.11 |
| U-119 | Títulos da ata abaixo do título da janela | Reunião | F5.11 |
| U-120 | Escolher a pasta dos modelos; grupo "Avançado" no Processamento | Config. | F5.11 |
| U-121 | Diálogo "Sobre": marca, versão, frase, MIT, autor e repositório, apoio, este computador e créditos | Sobre | F5.12 |
| U-122 | O Sobre na bandeja, na última página das Configurações e nas boas-vindas do assistente | Bandeja, Config., assistente | F5.12 |
| U-123 | "Apoiar o projeto…" com um coração novo na bandeja, no Sobre e na página Sobre | Bandeja, Sobre | F5.12 |
| U-124 | Doar pelo Pix: a chave com "Copiar chave", R$ 0,99 e três passos | Apoio | F5.12 |
| U-125 | Doar pelo PayPal: o e-mail com "Copiar e-mail", US$ 0,20 e "Abrir o PayPal" | Apoio | F5.12 |
| U-126 | O convite: cartão no canto, sem foco e fora da captura, com o que o app já guardou | Convite | F5.12 |
| U-127 | Quando o convite aparece: depois de valor entregue, com o app parado, no máximo um a cada 30 dias; desligável | Convite, Config. | F5.12 |
| U-128 | "Apoiar o listener-meet" discreto no rodapé do Histórico | Histórico | F5.12 |
| U-129 | Uma instância só: abrir o app já rodando entrega o pedido ao que roda, sem segundo app | Barra de tarefas | F5.13 |
| U-130 | O clique no ícone fixado abre o Histórico, com o app rodando ou parado | Barra de tarefas | F5.13 |
| U-131 | Jump List no ícone da barra: Gravar…, Histórico, Fila de processamento e Configurações | Barra de tarefas | F5.13 |
| U-132 | A última página do assistente ensina a fixar o app na barra de tarefas | Assistente | F5.13 |
| U-133 | O menu da bandeja mostra o atalho global ao lado de cada item que tem um | Bandeja | F5.13 |
| U-134 | O aviso do envio nomeia o provedor (Gemini, OpenAI, Anthropic) e aparece também no assistente | LLM, Assistente | F5.14 |
| U-135 | Com o Claude ativo para texto, uma linha diz que a busca por sentido segue com o ativo para embeddings | LLM | F5.14 |
| U-136 | "Outra pessoa…" traz no topo as 3 pessoas mais parecidas com a voz, com a nota; depois, a ordem alfabética | Revisão | F5.15 |
| U-137 | A cada confirmação, as vozes pendentes da reunião refazem a sugestão pela voz com o perfil novo | Revisão | F5.15 |
| U-138 | A linha do U-101 diz se a voz cabe numa referência da pessoa ou cria uma nova, e o total depois | Revisão | F5.15 |
| U-139 | Sem o token do Hugging Face, a reunião aberta e a fila dizem que falta quem falou no R e como resolver (o token e Reprocessar) | Reunião, Fila | F5.16 |
| U-140 | Bandeja de legendas: painel próprio, flutuante e sempre por cima como o widget, que abre ao gravar com o ao vivo e não ativa | Bandeja | F5.17 |
| U-141 | As legendas como chat: os outros à esquerda com nome, cargo e cor; as suas à direita; o cabeçalho só na primeira da sequência | Bandeja | F5.17 |
| U-142 | Minimizar recolhe à última legenda numa linha; fechar esconde até o botão "Legendas" do widget | Bandeja, widget | F5.17 |
| U-143 | Rolagem de chat: rola sozinha no fim; quem subiu para ler fica onde está, com "↓ N novas" | Bandeja | F5.17 |
| U-144 | Clique direito numa fala: criar marcador neste instante e copiar | Bandeja | F5.17 |
| U-145 | A bandeja fora da captura de tela, como os avisos | Bandeja | F5.17 |
| U-146 | Encostada no widget, a bandeja nasce acima dele e o acompanha; arrastada, fica solta e lembrada | Bandeja | F5.17 |
| U-147 | Legenda agrupada sob atraso: com a fila do ao vivo atrasada, uma legenda cobre até 25 s de fala do mesmo canal; sem atraso, nada muda | Bandeja, Ao vivo | vetada por medida (D-097) |
| U-148 | Transcrição com layout incremental: a lista das falas aparece e responde na hora; a barra de rolagem cresce nas primeiras frações de segundo | Reunião | F5.18 |
| U-149 | Cursor do player a 50 ms, extrapolado entre blocos (hoje salta a cada 250 ms e fica 8 a 118 ms atrás do som) | Player | F5.18 |
| U-150 | "Salvar áudio…" no Exportar: a reunião como o player toca (as pausas fora), em MP3 (padrão), FLAC ou WAV | Reunião | F5.19 |
| U-151 | "Salvar vídeo com legendas…": MP4 com a fala atual (o ponto na cor do falante, o nome, o cargo do dia e o texto) e a anterior apagada acima, com o título e a data no topo | Reunião | F5.19 |
| U-152 | A exportação de mídia com o progresso e "Cancelar" na faixa da reunião; fechar a janela no meio pergunta | Reunião | F5.19 |
| U-153 | "Sugerir": botão com a lâmpada na bandeja de legendas e no widget, "Sugerir…" no menu da bandeja do sistema e o atalho global; apagado sem legenda, sem a bandeja ou sem chave, com o motivo e o custo estimado na dica | Bandeja, widget, menu, atalhos | F5.20 |
| U-154 | O cartão de sugestão sob o chat da bandeja: "Sugestão · hh:mm · até [mm:ss]", 2 a 3 opções com o tipo, até duas frases, os fatos com [mm:ss] clicáveis, "sem evidência" quando a LLM não cita; "Nova sugestão", "Cancelar" e o custo | Bandeja | F5.20 |
| U-155 | "Boa", "Ruim" e "Usei" em cada opção, só de ícone, com os motivos do "Ruim" em chips dentro do cartão; "desfazer" | Bandeja | F5.20 |
| U-156 | "Meu objetivo nesta reunião" no "Antes de gravar" (uma linha, opcional, fora do preset) e "Objetivo…" no cartão, num popup fora da captura | Warm-up, bandeja | F5.20 |
| U-157 | "Como sugerir agora": chips no cartão e o texto livre num popup fora da captura; vale até ser limpa e vai gravada com cada sugestão | Bandeja | F5.20 |
| U-158 | "O que o assistente aprendeu": o memorando na página Prompts, com a data, a contagem, editável, "Limpar" e "Aprender com as avaliações" | Configurações | F5.20 |
| U-159 | Modo "Me situar" no cartão: o fio atual, o decidido e o que falta, em três linhas com [mm:ss] | Bandeja | F5.20 |
| U-160 | "Falaram com você": selo discreto no botão por 20 s quando o seu primeiro nome aparece numa legenda do R; sem chamada à LLM; desligável | Bandeja, widget | F5.20 |
| U-161 | Dossiê dos participantes no pedido: as ações abertas deles e suas e as últimas reuniões em comum, uma vez por gravação; desligável | LLM | F5.20 |
| U-162 | Aba "Sugestões" na reunião aberta e os números no `--check` | Reunião, `--check` | F5.20 |
| U-163 | Caixa "Sugestões" no "Antes de gravar" (por gravação) e o liga/desliga em uso nas Configurações: desligou, o botão some e o atalho fica mudo na hora | Warm-up, Configurações, bandeja, widget | F5.20 |
| U-164 | "Sugerir com a tela": atalho `ctrl+alt+shift+t`, item no menu da bandeja do sistema e a chave "Com a tela" na linha de "Nova sugestão" do cartão (o cabeçalho não comporta mais nada a 320 px, A-26); ligada, o "Sugerir" de sempre manda a tela | Bandeja, menu, atalhos | F5.21 |
| U-165 | Qual tela: automático (a janela da reunião; sem ela, o monitor da janela em primeiro plano e depois o do cursor) ou um monitor fixo nas Configurações; a troca rápida da gravação no menu da seta da chave, com "Monitor N · resolução · principal", numerados da esquerda para a direita, sem imagem | Configurações, bandeja | F5.21 |
| U-166 | No corpo do cartão, a linha "com a tela · janela do Teams", que rola o chat até a miniatura; a tela vazia vira "a tela veio preta: só as legendas" | Bandeja | F5.21 |
| U-167 | A miniatura da tela enviada em cada pedido na aba "Sugestões" da reunião; o duplo clique (ou o Enter) no pedido abre a cópia guardada fora da captura (sem ela, a miniatura maior), sem dobrar o pedido | Reunião | F5.21 |
| U-168 | A confirmação em linha no cartão na primeira vez com cada provedor, sem janela: "A imagem da tela vai para o <provedor>. Mandar · Agora não" | Bandeja | F5.21 |
| U-169 | "[tela]" nos fatos da opção que usou a tela, ao lado dos [mm:ss], e no lugar de "sem evidência" | Bandeja | F5.21 |
| U-170 | A miniatura da tela enviada no chat da bandeja, no ponto do tempo, como um anexo seu entre as falas; o clique abre maior numa janela sem foco e fora da captura; o selo "com a tela" do cartão rola o chat até ela | Bandeja | F5.21 |
