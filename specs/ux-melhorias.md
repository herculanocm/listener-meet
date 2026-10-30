# Melhorias de UX (U-nnn), CP12

Quando ler: ao escrever a spec de uma fase (T2). U-001 a U-015 aprovadas em 24/10 (D-017); U-016 e U-017 aprovadas e
implementadas na F1.1 (D-035); U-024 e U-026 a U-029 implementadas na F5 (D-047), U-025, U-008 e o .docx do U-011 na
F5.1 (26/10); U-030 a U-033, vistas no Windows, aprovadas e implementadas em 26/10 (D-049); U-034 a U-036 propostas nas
telas da F5.1 (26/10); U-037 e U-038 pedidas pelo dono no primeiro teste e implementadas (D-053, D-054); U-039 e
U-040 do segundo teste (D-055); U-041 a U-043 do brainstorm de 27/10, aprovadas (D-057) e implementadas na F5.2;
U-044 a U-051 aprovadas (D-060) e implementadas na F5.3, quatro delas pedidas pelo dono; U-052 a U-088 da F5.4
(design das telas), aprovadas antes de escritas (D-061) e implementadas em 28/10, com U-014 e U-034 a U-036; U-089 a
U-093 da F5.6 (ata a pedido, prompts, excluir reunião), pedidas pelo dono e aprovadas (D-063).
Status: proposta | aprovada | vetada | implementada.
Tamanho-alvo: 3 KB.

| # | Melhoria | Onde | Status |
| --- | --- | --- | --- |
| U-001 | "Apps tocando agora": processos emitindo áudio com medidor ao vivo; um clique escolhe a fonte; app de reunião primeiro | Warm-up | implementada (F1) |
| U-002 | Mudo global: botão e atalho mutam o mic no Windows e gravam silêncio; estado refletido no widget | Widget | implementada (F1; A2 corrige o endpoint) |
| U-003 | Marcador com nota por atalho: popup junto ao cursor, Enter salva, Esc cancela; a nota entra na ata | Widget | implementada (F1; A10 traz o popup para a frente) |
| U-004 | Saúde dos canais verde, âmbar, vermelho; R mudo por 20 s dispara aviso com "trocar fonte" | Widget | implementada (F1) |
| U-005 | Fila de processamento visível: progresso por etapa, pausar, retomar; aviso ao gravar com fila cheia | Janela principal | aprovada (F2) |
| U-006 | Revisão por teclado: card por falante com 5 s de áudio; Enter aceita; setas navegam; "é a mesma pessoa que" mescla | Revisão | aprovada (F2) |
| U-007 | Caixa de "vozes desconhecidas": clusters sem nome de todas as reuniões, agrupados por similaridade | Perfis | aprovada (F6; parcial na F2) |
| U-008 | Linha do tempo da reunião com faixas por falante e marcadores; clique toca | Histórico | implementada (F5.1) |
| U-009 | Custo e latência por provedor: testar conexão mostra latência; tokens e custo por reunião e por mês | Configurações | aprovada (F3) |
| U-010 | Indicador de tamanho do banco com projeção; backup com progresso | Configurações | aprovada (F6) |
| U-011 | Exportar ata e transcrição (.md, .docx, área de transferência) sob demanda (D-012) | Histórico | implementada (F5: .md e área de transferência; .docx na F5.1) |
| U-012 | Notificação de reunião detectada com "Gravar agora" (último preset) e "ignorar por 1 h" (D-010) | Bandeja | substituída pela U-094 (F5.7, D-065) |
| U-013 | Assistente de primeiro uso: token HF, chave do provedor, teste do mic, app de reunião | Primeiro uso | aprovada (F6; versão mínima na F1) |
| U-014 | Tema claro e escuro seguindo o sistema; DPI alto | Geral | implementada (F5.4: tema e DPI, 28/10; escolher à mão na F6) |
| U-015 | Preset "sala de reunião": diariza também o L e desliga "L é sempre eu" | Warm-up | aprovada (metadado na F1; lógica na F2) |
| U-016 | Detector de eco e alinhamento por correlação cruzada L×R: aviso no warm-up; deslocamento L/R no `--check` | Warm-up, `--check` | implementada (F1.1) |
| U-017 | Instrução para fixar o ícone da bandeja no Windows 11 (ícones novos vão para o menu oculto) | Primeiro uso | implementada (F1.1) |
| U-021 | Janela "Ao vivo": legendas rolando com nome e cargo e o resumo parcial, com "atualizar agora" | Ao vivo | implementada (F4) |
| U-022 | Clique numa legenda cria um marcador naquele instante com o texto dela | Ao vivo | implementada (F4) |
| U-023 | Possíveis decisões e ações do parcial com "marcar" (marcador que a ata lê) | Ao vivo | implementada (F4) |
| U-024 | Trechos da busca agrupados por reunião, termo destacado e "abrir neste ponto" (abre, rola até a fala e toca) | Histórico | implementada (F5) |
| U-025 | Destaque contínuo: a fala que toca acompanha o áudio na transcrição e na linha do tempo | Reunião aberta | implementada (F5.1) |
| U-026 | "Ações abertas" de todas as reuniões: dono, prazo, origem, atrasadas em destaque, marcar feita | Histórico | implementada (F5) |
| U-027 | "Citação certa?" sim/não em cada resposta do chat global (mede o G5) | Histórico | implementada (F5) |
| U-028 | Atalho global para abrir o Histórico com o cursor na busca | Atalhos | implementada (F5) |
| U-029 | "Copiar com citação": fala, trecho ou resposta com reunião, data e [mm:ss] | Histórico | implementada (F5) |
| U-030 | .md que não salva: "está aberto em outro programa (Word, Excel…); feche e tente de novo, ou salve com outro nome" no lugar do erro cru | Reunião aberta | implementada (D-049) |
| U-031 | Áudio do player pré-aquecido 2 s depois da bandeja: o primeiro "Tocar" do processo cai de 2 a 5 s para ~0,5 s | Player | implementada (D-049) |
| U-032 | Vírgula decimal nos números da interface ("446,4 KB", "atraso 1,5 s", "correlação 0,42") | Todas | implementada (D-049) |
| U-033 | Warm-up: o combo do microfone não engole a largura (a coluna "Janela" das fontes cabe) e o grupo dele sem vazio | Warm-up | implementada (D-049) |
| U-034 | Linha do tempo sem falas: o rodapé repete o "Sem falas nesta reunião." do meio da vista; com áudio, o rodapé ensina o clique ("clique para ouvir daquele ponto") | Reunião aberta | implementada (F5.4 L4, 28/10) |
| U-035 | Voz sem pessoa como "Voz 1", "Voz 2"… por reunião, não pelo rótulo interno do pyannote ("Voz SPEAKER_03"), na linha do tempo, na transcrição e na revisão | Reunião aberta | implementada (F5.4 L4, 28/10; todas as vozes numeradas, nomeadas ou não; a F5.5 leva o "Voz 2" à ata, à exportação, à busca e aos chats, D-062) |
| U-036 | Estado do índice nas Configurações com o nome legível do modelo ("e5-small, local"), não a chave interna (`local/multilingual-e5-small@384`), e sem mandar "preparar em Configurações (Busca)" quem já está nelas | Configurações | implementada (F5.4 L3, 28/10) |
| U-037 | "Chaves de acesso" como primeiro grupo das Configurações (Gemini, Hugging Face, GPT e Claude, com Testar, o estado e onde conseguir) e o warm-up avisando antes de gravar quando falta chave ou token; as mensagens apontam para lá | Configurações, Warm-up | implementada (D-053) |
| U-038 | O assistente da primeira inicialização pede, logo depois da pasta do banco, a LLM (Gemini, GPT ou Claude) com a chave e o "Testar conexão", e o token do Hugging Face, tudo opcional; a escolhida vira a LLM ativa (com o Claude, a busca por sentido fica na local) | Assistente | implementada (D-054) |
| U-039 | A revisão de falantes só abre sozinha com voz a confirmar; sem nenhuma, o aviso diz "processada, sem voz a confirmar" e nenhuma janela sobe por cima | Bandeja, Revisão | implementada (D-055) |
| U-040 | "Apps tocando agora" com uma linha por app (o Teams novo aparecia dividido e a linha marcada não tinha o som da chamada) | Warm-up | implementada (D-055) |
| U-041 | Ficha da pessoa ao nomear a voz: nome, cargo, empresa e e-mail; editar e apagar pessoa; a LLM e a busca com o cargo de quem fala (o e-mail fica local) | Revisão | implementada (F5.2) |
| U-042 | A ata, o executivo, o pré-resumo e o título só depois de as vozes serem identificadas (a identidade por contexto e a transcrição revisada saem antes), com "Gerar ata mesmo assim"; "Atualizar a ata com os nomes" quando eles mudam depois | Revisão, Reunião aberta | implementada (F5.2) |
| U-043 | Dispositivo de áudio conectado com o app aberto (fone Bluetooth) aparece sem reiniciar; o "Antes de gravar" atualiza a lista sozinho | Warm-up, Assistente | implementada (F5.2) |
| U-044 | Idioma padrão nas Configurações (pt-BR ou en) para transcrição, legenda ao vivo e LLM | Configurações | implementada (F5.3; pedido do dono) |
| U-045 | Idioma da reunião no "Antes de gravar", com o padrão já escolhido | Warm-up | implementada (F5.3) |
| U-046 | Trocar o idioma de uma reunião gravada e reprocessar nele (transcrição e ata) | Reunião aberta | implementada (F5.3) |
| U-047 | Fila com a coluna "Enviado", a porcentagem como barra colorida pelo estado e o botão Ajuda (o que cada tipo e etapa faz) | Fila | implementada (F5.3; pedido do dono) |
| U-048 | Aviso do próprio app que some sozinho quando algo fica pronto (ata, resumo, processamento, erro), sem o título durante a gravação | Canto da tela | implementada (F5.3; pedido do dono) |
| U-049 | Trocar microfone e saída gravada durante a gravação, pelo painel Dispositivos do widget, com os aparelhos conectados no meio dela | Widget | implementada (F5.3; pedido do dono) |
| U-050 | "novo: <nome>" com "Usar" no widget quando um aparelho chega durante a gravação | Widget | implementada (F5.3) |
| U-051 | Uma linha "Sistema inteiro: <saída>" por saída ativa na tabela de fontes | Warm-up | implementada (F5.3) |
| U-052 | Segoe UI Variable: corpo 14 px, legenda 12, título 20; segue o Tamanho do texto do Windows | Geral | implementada (F5.4 L1, 28/10) |
| U-053 | Ícones próprios em SVG, de uma cor e no tema, no lugar de emoji e caracteres soltos | Geral | implementada (F5.4 L1, 28/10) |
| U-054 | Nota e estado com 4,5:1 nos dois temas; estado nunca só pela cor (ícone ou texto junto) | Geral | implementada (F5.4 L1, 28/10) |
| U-055 | Anel de foco de 2 px só pelo teclado, em botão, caixa, aba e item de lista | Geral | implementada (F5.4 L1, 28/10) |
| U-056 | Um primário preenchido por janela (Gravar, Salvar, Próximo, Concluir); destrutivo em vermelho | Geral | implementada (F5.4 L1, 28/10) |
| U-057 | Aviso dentro da tela vira faixa com ícone, cor do tipo e ação, no lugar do texto laranja | Geral | implementada (F5.4 L1, 28/10) |
| U-058 | Tabelas: cabeçalho à esquerda, sem nº de linha, linha inteira selecionada, texto estica | Geral | implementada (F5.4 L1, 28/10) |
| U-059 | Estado vazio com ícone, frase e ação (Ata ainda não gerada + Gerar ata) | Geral | implementada (F5.4 L4, 28/10) |
| U-060 | Widget no tema, 360 px fixos; o alerta numa linha embaixo, na cor do tipo, sem alargar | Widget | implementada (F5.4 L2, 28/10) |
| U-061 | Widget: botões de ícone 36 × 32 com atalho na dica; Parar vermelho; Cancelar afastado | Widget | implementada (F5.4 L2, 28/10) |
| U-062 | Widget: mudo = microfone cortado; L, R e ao vivo com ícone; apagado salvando; hora h:mm:ss | Widget | implementada (F5.4 L2, 28/10) |
| U-063 | Medidor com trilho nos dois temas e zonas: verde até -18 dBFS, âmbar até -6, vermelho acima | Widget | implementada (F5.4 L2, 28/10) |
| U-064 | Dispositivos: atual com marca, nome e tipo em duas linhas, Novo em selo, resultado em faixa | Painel | implementada (F5.4 L2, 28/10) |
| U-065 | Antes de gravar em duas colunas (Áudio; Reunião), até 984 × 600 px | Warm-up | implementada (F5.4 L2, 28/10) |
| U-066 | Antes de gravar: falta de chave em faixa no rodapé, ao lado de Gravar (primário com ícone) | Warm-up | implementada (F5.4 L2, 28/10) |
| U-067 | Fontes com medidor próprio por linha e Reunião em selo; sala e ao vivo numa linha Opções | Warm-up | implementada (F5.4 L2, 28/10) |
| U-068 | Marcador e avisos no tema, com ícone do tipo além da faixa e fechar de 32 × 32 px | Avisos | implementada (F5.4 L2, 28/10) |
| U-069 | Resumo da gravação com ícone de sucesso; a lista de marcadores só quando há | Resumo | implementada (F5.4 L2, 28/10) |
| U-070 | Bandeja: marca do app com o estado em selo de forma e cor, legível a 16 px | Bandeja | implementada (F5.4 L2, 28/10) |
| U-071 | Configurações em páginas com navegação à esquerda, 960 × 640; um Salvar para todas | Config. | implementada (F5.4 L3, 28/10) |
| U-072 | Erro de validação abre a página, marca o campo e explica numa faixa, sem caixa de mensagem | Config. | implementada (F5.4 L3, 28/10) |
| U-073 | Sim/não em caixa; número com vírgula e faixa da `SPECS`; taxa em lista; mesmo valor salvo | Config. | implementada (F5.4 L3, 28/10) |
| U-074 | Aplica agora e na próxima gravação no subtítulo da página; na linha só o que difere | Config. | implementada (F5.4 L3, 28/10) |
| U-075 | Chaves, atalhos e downloads com selo (Falta a chave, Conectado, Registrado, Falhou) | Config. | implementada (F5.4 L3, 28/10) |
| U-076 | Sem jargão interno (F2, F3, F4, F6, CP10) na tela; cabeçalho da tabela de preços inteiro | Config. | implementada (F5.4 L3, 28/10) |
| U-077 | Fila de 1040 × 480: Estado em selo, barra nos tokens, Ajuda e Fechar numa linha só | Fila | implementada (F5.4 L3, 28/10) |
| U-078 | Reunião: título de 20 px sem moldura até o foco, fatos em legenda, menus com a seta inteira | Reunião | implementada (F5.4 L4, 28/10) |
| U-079 | Ata, executivo e pré-resumo formatados; Editar abre o Markdown; origem em dd/mm/aaaa | Reunião | implementada (F5.4 L4, 28/10) |
| U-080 | Ações: Ação estica e quebra linha; prazo dd/mm/aaaa; atrasada em selo vermelho | Reunião, Histórico | implementada (F5.4 L4, 28/10) |
| U-081 | Transcrição e Ao vivo: [mm:ss] em legenda, falante em negrito com ponto na cor dele | Reunião | implementada (F5.4 L4, 28/10) |
| U-082 | Linha do tempo nas cores `speaker`; marcador em `text` com bandeira; cursor em `accent` | Reunião | implementada (F5.4 L4, 28/10) |
| U-083 | Player: ícones de tocar, pausar, -10 s e +10 s junto do texto; tempo de largura fixa | Reunião | implementada (F5.4 L4, 28/10) |
| U-084 | Histórico: busca com lupa, índice na linha das abas, Estado em selo, custo à direita | Histórico | implementada (F5.4 L4, 28/10) |
| U-085 | Histórico: trechos e respostas do chat nas cores dos tokens (HTML sem cor fixa) | Histórico | implementada (F5.4 L4, 28/10) |
| U-086 | Revisão: voz em cartão com estado em selo; atalho em selo de tecla; primário segue a etapa | Revisão | implementada (F5.4 L4, 28/10) |
| U-087 | Ficha da pessoa: Apagar pessoa em vermelho à esquerda, longe de Salvar | Revisão | implementada (F5.4 L4, 28/10) |
| U-088 | Assistente sem a faixa vazia do Aero: título de 20 px, Passo n de 6, Próximo primário | Assistente | implementada (F5.4 L4, 28/10) |
| U-089 | Ao parar: "Gerar ata e ações" e "Gerar resumo" no resumo da gravação, com a última escolha | Resumo | implementada (F5.6 L1, 29/10) |
| U-090 | Pedido de ata ou resumo com popup: vozes sem nome, custo e "Gerar quando todos tiverem nome" | Reunião | implementada (F5.6 L1, 29/10) |
| U-091 | Resumo parcial do ao vivo como caixa no "Antes de gravar", desmarcada | Warm-up | implementada (F5.6 L1, 29/10) |
| U-092 | Página Prompts: as instruções de cada tarefa da LLM editáveis, o formato à vista, restaurar padrão | Config. | implementada (F5.6 L2, 29/10) |
| U-093 | Excluir reunião no Histórico, com seleção múltipla e confirmação; perfis de voz ficam | Histórico | implementada (F5.6 L3, 29/10) |
| U-094 | Aviso de reunião sem gravação: cartão sem foco e fora da captura com "Gravar…" (abre o "Antes de gravar" com o app como fonte) e "Agora não" (até a chamada acabar), um lembrete, a bandeja e o cartão nas Configurações; substitui a U-012 | Aviso, bandeja | implementada (F5.7 L3, 29/10) |
| U-095 | Ícone do `.exe` (Explorer e barra) com a marca do app no estado ocioso, um tamanho desenhado por escala; a descrição do arquivo "listener-meet" | Executável | aprovada (F5.8, D-068) |
