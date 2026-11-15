# Decisões (D-nnn), perguntas abertas (P-nn) e medições

Quando ler: ao mexer em produto ou arquitetura, ou quando o código ou uma spec citar um `D-nnn`. Cada linha diz a regra
que vale hoje e o motivo; o detalhe de cada entrega está na spec da feature. Decisão superada continua na tabela, para
o ID resolver. D-104 a D-107 não existem na `main`: eram de um branch descartado (D-109), e os números não são
reutilizados. "Quem": o dono decide o produto; a LLM decide por medida ou por delegação (D-064).
Tamanho-alvo: 30 KB.

## Decisões
| # | Quem | Decisão vigente | Motivo |
| --- | --- | --- | --- |
| D-001 | dono | CP13: nenhum código sem autorização explícita, por entrega; documentos podem | spec antes do código |
| D-002 | dono | CP14: sem GPU, tudo em CPU; só open source e a LLM paga | a máquina não tem GPU dedicada |
| D-003 | dono | o áudio fica num SQLite separado do `listener.db` (um arquivo por mês desde a D-039) | backup e gestão; segue a CP9 |
| D-004 | dono | o mudo global muta o microfone no sistema (o app de reunião também silencia) e grava silêncio | é o único mudo visível sem API de plataforma (CP4) |
| D-005 | dono | legenda ao vivo leve: VAD e Whisper em janelas, 5 a 10 s de atraso, nome por embedding como melhor esforço; a passagem final a substitui | diarização e transcrição completas são em lote, e a CPU é dividida |
| D-006 | dono | repositório recomeçado do zero em spec-driven; o protótipo anterior foi apagado | "não quero guardar lixo" |
| D-007 | dono | superada pela D-108 (o handoff v4 como documento mestre) | |
| D-008 | LLM | identificação por embedding do pyannote e cosseno: limiar 0,60, margem 0,05, calibração por perfil (referências por pessoa desde a D-087) | a identificação pronta da pyannoteAI é paga; CP5 |
| D-009 | dono | embeddings da busca: `gemini-embedding-001` em 768 dimensões por padrão; modelo e dimensão gravados por vetor; reindexar ao trocar | CP2 |
| D-010 | dono | reunião detectada pelas sessões de áudio do sistema; o app nunca grava sozinho (o aviso é o da D-065) | sessão de áudio é do sistema, não da plataforma (CP4) |
| D-011 | dono | a pausa mantém a linha do tempo contínua (silêncio comprimido e um evento); o player pula a pausa | o alinhamento L/R e os marcadores não se deslocam |
| D-012 | dono | exportar (.md, .docx, área de transferência, áudio e vídeo) só por ação do usuário | a CP9 trata da persistência do app |
| D-013 | dono | superada pela D-060 (idioma detectado por reunião) | |
| D-014 | dono | o processamento começa sozinho ao parar, numa fila com pausar e retomar | sem isso a ata atrasa |
| D-015 | dono | 16 kHz mono nos dois canais por padrão (5 a 6 GB por mês); taxa configurável | Whisper e pyannote usam 16 kHz |
| D-016 | dono | `segments.role_at_time`: o cargo do dia fica junto da fala, além do atual na ficha | a ata antiga não muda com uma promoção |
| D-017 | dono | U-001 a U-015 aprovadas | CP12 |
| D-018 | dono | CP13 e CP14 registradas como cláusulas | |
| D-019 | dono | o produto roda em Python 3.12 | o WhisperX exige menos que 3.14 |
| D-020 | dono | transcrição final pela LLM com áudio nativo como opção ao lado do Whisper local (`pipeline.transcriber = llm`) | alívio de CPU; custo e saída do áudio são escolha do usuário |
| D-021 | dono | embeddings locais (multilingual-e5) como opção ao lado do provedor pago | CP2 e CP14; sem custo e sem rede |
| D-022 | dono | preparo do ambiente e medições iniciais aprovados; `scripts/medir.py` autorizado | primeira autorização sob a CP13 |
| D-023 | dono | a pasta `memory/` fica no repositório | memória compartilhada entre sessões e LLMs |
| D-024 | LLM | superada pela D-048 (vetores com sqlite-vec) | |
| D-025 | LLM | o áudio é decodificado com soundfile e entregue em memória ao pyannote e ao Whisper; nunca FFmpeg nem torchcodec | o torchcodec não carrega sem FFmpeg |
| D-026 | dono | app de código aberto; nada pessoal no código; a primeira abertura coleta os dados do usuário e a pasta do banco | |
| D-027 | dono | portabilidade para Linux e macOS; nada do Windows fora da camada de plataforma | |
| D-028 | dono | timestamps por palavra do faster-whisper como padrão; wav2vec2 como opção de alta precisão | o alinhamento wav2vec2 custava mais que a transcrição |
| D-029 | dono | assistente de primeira abertura; o usuário escolhe a pasta do banco, e só o ponteiro `db-location.json` fica fora do SQLite | |
| D-030 | dono | CP15: open source e multiplataforma como cláusula (eleva D-026 e D-027) | |
| D-031 | dono | documentação compacta para leitura por LLM, com regras em `specs/README.md` (revista pela D-108) | arquivos grandes estouravam o limite de leitura |
| D-032 | dono | git reiniciado limpo em 2026-10-24 | "não quero guardar lixo" |
| D-033 | dono | log em arquivo rotativo escolhido pelo usuário, com nível editável; exceção explícita à CP9 | diagnóstico legível fora do app, vivo mesmo com o banco em falha |
| D-034 | dono | licença MIT | |
| D-035 | dono | atalhos globais `ctrl+alt+shift+letra` com `RegisterHotKey` no Windows; datas gravadas com offset | atalhos de duas teclas colidem com o Teams e os navegadores; `RegisterHotKey` consome a tecla |
| D-036 | dono | F2: worker em subprocesso; token do Hugging Face no banco; limiar 0,60 e margem 0,05; large-v3-turbo int8; a fila pausa durante a gravação | |
| D-037 | dono | CP16: commit obrigatório a cada item fechado | o histórico do git é a memória do projeto |
| D-038 | dono | CP17: Python 3.12 como piso, sem retrocompatibilidade | app novo, sem base instalada a preservar |
| D-039 | dono | um arquivo de áudio por mês (`listener-audio-AAAA-MM.db`, anexado sob demanda); reamostragem com estado; conexão SQLite por thread | defeitos que perdiam áudio e memória; banco pronto para o worker |
| D-040 | dono | fluxo por entrega: spec, revisão de arquitetura contra o código, aprovação, implementação em lotes com commit, revisão independente do diff, fechamento (`AGENTS.md`) | decisões prontas no briefing evitam travas e invenções |
| D-041 | dono | F2: diarizar e identificar antes de transcrever; o worker termina a fila com o app fechado; o perfil do usuário é reforçado pelo canal L só depois de uma confirmação; participantes esperados desempatam abaixo de 0,05 de margem; processamento aceito até 2× a duração | |
| D-042 | LLM | janela da diarização de 1.200 s por padrão (`pipeline.diar_window_s`, de 300 a 7.200), costurada por centroide e tempo | janela menor devolve a CPU mais cedo |
| D-043 | dono | F3: alerta de custo de US$ 2 por reunião; segunda opinião por áudio desligada por padrão; "gerar de novo" pela fila, no worker leve; reprocessar mantém a ata antiga; pedido e resposta guardados no banco | desenho em `llm-contrato.md` |
| D-044 | dono | F4: ao vivo ligado por padrão com 8 núcleos ou mais; nomes ao vivo ligados; `small` com `base` na degradação; a janela "Ao vivo" não abre sozinha; o resumo parcial é escolha da gravação (`live.partial`, desligado por padrão) e divide o teto de custo com as sugestões; processo `--live` com pipes | |
| D-045 | dono | o Windows é o propósito do app: a sessão começa conferindo o ambiente; o que é feito no Linux só vale depois de verificado no Windows, e a pendência vai para o backlog | o que faltava rodar no Windows se perdia entre sessões |
| D-046 | dono | superada pela D-090 (verificação no Windows adiada) | |
| D-047 | dono | F5: índice automático com custo visível; ata e ações na busca; conversas guardadas; legendas ao vivo fora do índice; reunião aberta no lugar da janela de transcrição; chat global no modelo rápido | |
| D-048 | LLM | busca vetorial sem sqlite-vec: float32 normalizado em `rag_vectors` e assinatura binária do sinal de v − μ em numpy (Hamming, max(200, 1 % de n) candidatos até 2 mil, reordenação exata) | o `vec0` também varre tudo e exigiria a extensão em toda conexão; recall@10 de 0,995 |
| D-049 | dono | U-030 a U-033 aprovadas | vistas nas telas a 150 % |
| D-050 | dono | a suíte tem a parte rápida e a lenta (`--tudo`); lock da stack do Windows; o extra `[pipeline]` no venv do checkout; o canal R não avisa antes de 10 s sem som | |
| D-051 | LLM | o provedor local usa o `intfloat/multilingual-e5-small` int8 (ONNX, 118 MB, 384 dimensões), com revisão e sha256 fixados | cosseno 0,995 com o fp32, mesmos acertos, 1,9× mais rápido e 4× menor |
| D-052 | dono | o concluído sai do backlog, e o lixo é apagado com o OK do dono | |
| D-053 | dono | "Chaves de acesso" é o primeiro grupo das Configurações (U-037); o "Antes de gravar" avisa quando falta chave ou token; o log nunca leva título de janela nem nome de pessoa | o dono não achava onde pôr a chave; o log levava o título da janela do Teams |
| D-054 | dono | o assistente pede a LLM, a chave e o token do Hugging Face logo depois da pasta do banco, tudo opcional (U-038); com o Claude, a busca por sentido fica na local | |
| D-055 | dono | a captura do app soma a raiz com as sessões de áudio mais fundas (`capture/mixer.py`); a revisão só abre sozinha com voz a confirmar; padrões do Gemini: `gemini-3.8-flash` e `gemini-3.1-pro-preview`, com orçamento de raciocínio | o loopback de processo pega só os filhos diretos, e o Teams toca num WebView2 neto; o Gemini 2.5 dá 404 para conta nova |
| D-056 | dono | os workers nascem só com `CREATE_NO_WINDOW`, sem `DETACHED_PROCESS`; o custo cai na família de preço (`price_ref`) quando o modelo não tem linha própria; nome de pessoa e texto falado ficam fora do log | o `DETACHED_PROCESS` abria uma janela preta por worker, que, fechada, matava o worker sem rastro |
| D-057 | dono | F5.2: ficha da pessoa com nome obrigatório e cargo, empresa e e-mail opcionais; o e-mail fica só local, fora da LLM e da busca; o pedido de ata pode esperar todas as vozes terem nome (D-063); os dispositivos se atualizam sozinhos, sem trocar o microfone escolhido | |
| D-058 | LLM | o worker tem um vigia no laço do asyncio: parado por 120 s, grava o traceback de todas as threads e sai; o app o relança, e o job é retomado | uma chamada nativa presa (verificação de certificado) parava a fila para sempre |
| D-059 | dono | convenção de logs em `ARCHITECTURE.md`: níveis, categoria por módulo, sem dado pessoal | o log precisa contar a história sozinho |
| D-060 | dono | F5.3: o idioma é escolhido por reunião (pt-BR ou en; padrão nas Configurações, escolha no "Antes de gravar", troca com reprocessamento), sem detecção; avisos do próprio app, fora da captura; microfone e saída trocados durante a gravação | a detecção errava e dobrava o custo da legenda; o "Não perturbe" esconde o balão do Windows |
| D-061 | dono | F5.4: sistema visual das telas (CP7) | |
| D-062 | dono | F5.5: "Voz n" também na entrada da LLM, na exportação e no índice; nenhuma conexão Qt com lambda que prenda `self` | um widget sem dono preso por lambda derrubava a saída do app |
| D-063 | dono | F5.6: ata, ações e resumo só a pedido, ao parar ou na reunião, com um popup que recomenda esperar os nomes; identidade, transcrição revisada, título, glossário e índice seguem automáticos; prompts editáveis (só as instruções); excluir reunião é definitivo e nunca toca perfil de voz | o dono decide o custo da ata; a identificação é o núcleo |
| D-064 | dono | delegação: com um padrão seguro, a LLM decide, registra aqui e segue; "Gerar quando todos tiverem nome" espera a última voz ganhar nome | "decida por mim e siga" |
| D-065 | dono | F5.7: aviso de reunião sem gravação. "Gravar…" abre o "Antes de gravar" com o app como fonte; "Agora não" vale até a chamada acabar; outros apps com microfone e som avisam depois de 20 s; só ouvindo, depois de 45 s; o "Antes de gravar" também sai da captura | o som sozinho dava falso positivo; a CP6 exige o warm-up |
| D-066 | LLM | F5.7: o "Antes de gravar" aberto segura o aviso; um "Gravar" que falha deixa a chamada pendente; um modal aberto esconde o cartão e pausa o prazo; sem a exclusão da captura, o aviso vai só para a bandeja | quem clicou em Gravar quis gravar |
| D-067 | dono | todo áudio do app que acompanha o padrão segue o aparelho padrão de agora do Windows (`endpoints.default_id`): player, "Testar 3 s", medidor, loopback e mudo; a escolha explícita do usuário sempre prevalece | o PortAudio fixa o aparelho ao iniciar |
| D-068 | dono | o executável do Windows foi autorizado de ponta a ponta (comparativo, spec, build) | pedido do dono |
| D-069 | LLM | o `.exe` sai pelo PyInstaller em pasta (`--onedir --windowed`), não pelo Nuitka nem em arquivo único | compilar para C não acelerou (o pesado já é nativo); o arquivo único se descompacta a cada abertura |
| D-070 | LLM | F5.9: a vida do processo do app por handle (`OpenProcess` e `WaitForSingleObject`); a releitura de sessões numa thread própria do loopback; a reabertura num `ReopenMixin` do gravador; o downmix em `capture/audio.to_mono` (ITU-R BS.775) | o `is_running` do proc-tap nunca cai; o downmix no `win32` seria repetido na F7 |
| D-071 | dono | todo texto que vai à LLM é em pt-BR (instruções, contrato, rótulos, instrução de idioma); as chaves do JSON e as citações ficam como eram; os prompts subiram para a versão 2.0 | pt-BR é o idioma padrão do app |
| D-072 | dono | revisão do código inteiro: o app sai depois de um logoff cancelado; o faulthandler grava num `<log>-crash.log` à parte; a espera da pausa manual do job `llm` fica fora do laço asyncio. Os achados decididos estão em `memory/revisao-codigo.md` | corrigir só com caminho concreto de dano no uso real |
| D-073 | dono | F5.10: o agente de "perguntar à base" consulta quantas vezes precisar e para por exaustão; as guardas são só de custo, tempo e laço | um número fixo cortaria perguntas que cruzam muitas reuniões |
| D-074 | dono | F5.10: autorizador por lista de permissão das dependências de cada visão; sandbox numa conexão própria só de leitura; camada semântica com cache; migração v9 com volta; os filtros da aba Reuniões valem para as consultas; `meetings.notes` e `people.notes` ficam fora | decidir pelo nome da visão vazava a chave de API por uma CTE com o mesmo nome |
| D-075 | LLM | F5.10: a sandbox recusa `USING` e `NATURAL`; nenhuma visão lê `main.people`; o `EXPLAIN` da instrução nega toda leitura fora das páginas das visões; a consulta é interrompida se a memória crescer mais de 512 MB | o SQLite não chama o autorizador para a coluna de junção; uma ordenação crescia ~2,5 GB/s |
| D-076 | dono | a confirmação de descartar a gravação abre com o "Não" como padrão do Enter | sem botão padrão, o Enter apagava a gravação |
| D-077 | dono | F5.11: revisão de vozes, reunião aberta e linguagem da interface revistas (U-098 a U-120) | crítica de UX |
| D-078 | LLM | F5.11: mesclar uma voz confirmada numa voz sem pessoa tira o reforço e o nome; na sala presencial, a saída sem som é aviso, não falha; a legenda do canal L fora da sala diz "Você"; as teclas do player vêm pelo `keyPressEvent` | o `keyPressEvent` nunca toma a tecla de um campo |
| D-079 | dono | F5.12: o Sobre e o convite para apoiar (R$ 0,99 pelo Pix ou US$ 0,20 pelo PayPal), no máximo um a cada 30 dias, depois de valor entregue; o canal de doação, o autor e o repositório ficam em `listener/about.py` | dado público do projeto, por escolha do dono (CP15) |
| D-080 | LLM | F5.12: o intervalo de 30 dias vale também sem resposta; qualquer app usando o microfone segura o convite; com revisão aberta, modal ou sem a exclusão da captura, o convite espera; uma doação fecha o cartão | nunca por cima de trabalho nem de tela compartilhada |
| D-081 | dono | versão: `listener.__version__` sobe a minor por feature e a patch por correção; o build carimba o commit (`build-stamp.txt`, mostrado no Sobre); `build_exe.py --zip` gera `listener-meet-<versão>-win64.zip` e recusa com mudança sem commit; o `.exe` e a pasta mantêm o nome fixo | o que se compartilha é a pasta; renomear o `.exe` quebraria o atalho fixado |
| D-082 | dono | F5.13: instância única por um mutex da plataforma e um canal local; o ícone fixado abre o Histórico; Jump List só no `.exe` | sem a trava, o ícone fixado abriria um segundo app no mesmo banco |
| D-083 | LLM | F5.13: o mutex é `Local\listener-meet-<12 hex>` da pasta de configuração; quem fecha a conexão é o cliente; o Jump List sai 3 s depois da abertura; o canal fecha no começo da saída | outro perfil é outra instância; os testes nunca tocam a trava do usuário |
| D-084 | dono | F5.14: o Claude em todas as tarefas (saída estruturada, pensamento adaptativo com esforço por classe, `complete()` pelo streaming, recusa sem reenvio); padrões `claude-haiku-5-5` e `claude-opus-5-5`; trocar o provedor de texto não troca o da busca | o usuário escolhe entre o Gemini e o Claude |
| D-085 | LLM | F5.14: com o fallback do servidor, o custo sai pelo modelo que respondeu; a recusa no streaming vira `RefusalError`; o pedido nunca leva mensagem em branco; o reenvio da resposta cortada cresce ao menos 8.192 tokens; as sementes de preço entram ao abrir, só no papel APP | achados da revisão do diff, provados |
| D-086 | dono | superada pela D-090 (célula `llm` do CI) | |
| D-087 | dono | F5.15: referências de voz por pessoa (amostras confirmadas agrupadas por ligação média; a nota é a da referência mais próxima; a margem é entre pessoas); sugestões das pendentes refeitas na revisão, com piso de 0,45 | a média única diluía as condições de gravação |
| D-088 | dono | as referências ficam ligadas por padrão: `pipeline.ref_cos` 0,80 e `pipeline.max_refs` 5; `max_refs = 1` desliga | o teste no banco real empatou, sem piora medida |
| D-089 | LLM | F5.15: as constantes moram em `config/defaults.py`; a nota da revisão, em `pipeline/review_suggest.py`; "Outra pessoa…" traz no topo só quem tem nota de 0,45 ou mais | evitar import circular |
| D-090 | dono | a verificação no Windows até a F5.15 e os gates G1 a G6 valem pelo uso real, sem os números; entrega nova feita fora do Windows volta a pôr no backlog o que só o Windows verifica; o CI saiu do repositório | o dono usa o app no dia a dia |
| D-091 | dono | `pytest-xdist` só em desenvolvimento, para a suíte em paralelo; a F6 fica para depois; sem o token do Hugging Face, o processamento transcreve mesmo assim (F5.16) | |
| D-092 | LLM | a pergunta da pasta sincronizada e "Apagar preset" abrem com o "Não" no Enter; a pasta do banco e os caminhos do usuário saem do log | uma tecla punha o banco, com as chaves abertas, numa pasta que sobe para a nuvem |
| D-093 | LLM | F5.16: reprocessar sem o token uma reunião que já tem vozes é recusado; o cluster `L` do usuário existe mesmo sem diarização; os arquivos de teste que lançam subprocesso rodam num processo só (`--dist loadgroup`) | um reprocessamento sem o token tiraria quem falou de uma reunião já identificada (CP3) |
| D-094 | dono | F5.17: bandeja de legendas; a altura de cada fala fica no item (`SizeHintRole`), sem thread nova; o processo ao vivo não ganha paralelismo | o custo era o layout; com a CPU disputada, mais threads não ajudam: a alavanca é o pesado ceder a CPU |
| D-095 | LLM | F5.17: a lista comum (`ui/caption_list.py`) também na janela Ao vivo; a dica da linha recolhida não repete a legenda | a dica é uma janela fora da exclusão da captura |
| D-096 | dono | F5.18 (performance) aprovada, com as decisões técnicas delegadas | |
| D-097 | LLM | F5.18: o que entrou e o que ficou de fora por medida está na spec; o padrão das threads é `cpu_count - 2`; a suíte tem o vigia de travamentos (`tests/hang_guard.py`) e a regra de rodada (saída em arquivo; a suíte inteira só no fechamento) | as medições decidiram |
| D-098 | dono | F5.19: exportar áudio (MP3 por padrão, FLAC, WAV; mono, L + R com o pico a -1 dBFS, pausas fora) e vídeo com legendas (MP4, 1280 × 720); com gravação em curso, a exportação espera | pedido do dono; nenhuma dependência nova |
| D-099 | LLM | F5.19: uma exportação por vez no app; tarefa de prioridade normal; só codificadores de CPU; destino por arquivo temporário (`export.atomic_target`); quadro-chave a cada 30 quadros; MP3 em VBR | uma thread de prioridade baixa segurando o GIL travaria a interface e a captura |
| D-100 | dono | F5.20: sugestões ao vivo do que falar (2 a 3 opções com fatos citados por legenda, avaliações, memorando, três camadas de direcionamento, "Me situar", teto de custo partilhado com o parcial); tudo fora da captura e ligável em uso | pedido do dono; CP8 e CP12 |
| D-101 | LLM | F5.20: desvios e achados da implementação, incorporados à spec | |
| D-102 | dono | F5.21: a tela nas sugestões. A captura a pedido cabe na CP4; o alvo padrão é a janela da reunião; as janelas por cima saem pretas; confirmação na primeira vez por provedor; "[tela]" no lugar de "sem evidência" | pedido do dono |
| D-103 | LLM | F5.21: desvios e achados da implementação, incorporados à spec (a tela vazia decide pelo miolo escuro; a reunião coberta em 80 % ou mais não vai; a escolha do alvo roda na tarefa do pedido) | |
| D-108 | dono | 2026-11-15: documentação reestruturada. O handoff v4 foi apagado: as cláusulas e o fluxo da CP11 moram em `01-clausulas.md`. Specs implementadas condensadas ao contrato vigente. Apagados o roteiro de teste no Windows, as specs de preparo e de correção (F0, F1.1, F1.2, F3.1), `memory/README.md` e `memory/dono.md` (incorporado ao `AGENTS.md`). `CHANGELOG.md` criado | documentação enxuta, no padrão de mercado, para qualquer LLM ler e programar |
| D-109 | dono | 2026-11-15: o código é a referência. Onde uma spec ou outro documento diverge do código, o documento é ajustado ao código; texto desatualizado no código é apagado. O que está suspenso sai do repositório: a spec F5.22 (GPU quando houver) foi apagada, e a CP14 segue como está. O branch `claude/speakers-search-typeahead-a2d9ae` (F5.23 e F5.24, que removia a legenda ao vivo e as sugestões) foi descartado: a `main` é a referência. A licença segue MIT, com o alerta do `libx264` (GPL) no README | repositório limpo; o código implementado é a verdade |
| D-110 | dono | 2026-11-15: F5.25 aprovada. "Outra pessoa" vira uma janela com busca por nome, cargo e empresa; a nota da voz aparece em percentual em toda linha e no cartão; a lista vem em seções ("Pela voz", "Esperadas nesta reunião", "Todas"); busca sem resultado mostra nomes parecidos antes da pessoa nova; sem linha pela voz, o Enter não confirma nada (U-171 a U-177). O filtro do Histórico e o "É a mesma que…" ficam como estão | pedido do dono: achar a pessoa digitando, sem rolar; a nota de todas já era calculada, sem custo novo (medida na spec) |

## Em aberto (o padrão vale enquanto não há resposta)
| # | Pergunta | Padrão |
| --- | --- | --- |
| P22 | corrigir a deriva de relógio entre os canais | só medir; vira spec se uma reunião mostrar mais de 100 ms por hora |

## Medições de referência
Máquina: Windows 11, Intel Core Ultra 5 235U (12 núcleos, 14 threads, 2 de desempenho), 31,5 GB de RAM, sem GPU
dedicada, torch 2.8 de CPU. Todas são [fato] medido entre 2026-10-24 e 2026-11-08; servem de base para comparar.
| Tema | Resultado | O que decidiu |
| --- | --- | --- |
| Whisper large-v3-turbo int8 | 0,45 a 0,50× o tempo real com 4 a 14 threads (12 é o melhor); o processamento em lote não ganha (+4 %) | padrão `cpu_count - 2`; sem `BatchedInferencePipeline` |
| Alinhamento wav2vec2 | soma 0,62× à transcrição | `word_timestamps` do faster-whisper como padrão (D-028) |
| Diarização community-1 | os embeddings eram 93 % da etapa; com o tronco uma vez por trecho, 5 min caem de 305 para 118 s (0,29× com 10 a 12 threads; 14 piora) | `pipeline/lean_diarization.py` |
| Reunião real de 15 min (antes da F5.18) | diarização 1.042 s, com 3,6 GB de memória; Whisper 326 s no R e 165 s no L; LLM US$ 0,08 | o pesado cede a CPU durante a gravação |
| Ao vivo, máquina livre | `small`: 3,2 a 3,7 s por janela de 3 a 10 s; `base`: 1,05 a 1,25 s; idioma automático dobra o tempo; VAD a 230× o tempo real | janelas de 4 a 10 s; sem detecção de idioma (D-060) |
| Ao vivo, CPU disputada | 13 a 15 s por janela, e mais threads não ajudam; o filho em prioridade normal cai de 17,2 para 10,2 s | filho `--live` em normal; o pesado pausa (F5.18) |
| Ao vivo, 11 reuniões reais | atraso médio de 5,7 s, mediana 4,1 s, p95 15,9 s | meta do aceite da F4 |
| Passe único de várias janelas no Whisper | perde fala, mesmo com timestamps | U-147 vetada |
| Embedding do nome ao vivo | 343 ms por janela | thread própria, em paralelo com o Whisper |
| Eco do microfone | só sem headset (71 % dos blocos numa reunião); com headset, 0 a 5 % | gate de eco sem falso positivo |
| FTS5, 60 mil trechos | termo médio 13 ms, raro 3 ms, quase onipresente 85 ms | FTS5 obrigatório |
| Assinatura binária | 200 mil × 768 em 18,5 ms, recall@10 de 0,995; carregar 50 mil assinaturas em 303 ms | D-048 |
| Pragmas do SQLite | `cache_size` de 32 MiB e `mmap_size` de 256 MiB: carga das assinaturas 21 % mais rápida | pragmas no app e no worker leve |
| e5-small int8 | 190 ms por trecho com 2 threads; consulta em 6 ms quente e ~1 s a frio; pico de 725 MB no worker leve | D-051; a sessão é solta quando ociosa |
| App parado | pronto em 1,4 a 1,9 s; ~188 MB; ~0 % de CPU | |
| Player | primeiro som em 0,3 a 0,5 s com o áudio pré-aquecido (antes, 2 a 5 s) | U-031 |
| Tema | uma folha de estilo no app inteiro dobra a abertura das janelas | o tema é paleta, fonte e `ThemeStyle` só nos `draw*` |
| Widget sem pai | cada `setVisible(True)` abre uma janela nativa (~60 ms) | todo widget nasce com o pai |
| Medir abertura de janela | o mesmo código varia até 40 % entre processos (núcleos P e E) | `win_probe tempos --runs 7`, base e atual alternadas, na mesma pasta |
| Lista de legendas | 1,6 a 2,0 ms por legenda nova com até 2.000 falas (eram 73 ms com `sizeHint` em Python) | altura no item (D-094) |
| Executável | PyInstaller: ~7 min, ~890 MB, ~4.700 arquivos, zip de 351 MB; Nuitka: ~1 h, sem ganho | D-069 |
| TLS | contexto novo por conexão: 148 ms; partilhado: 98 ms; a rede não emite bilhete de sessão | `SSLContext` partilhado; sem laço persistente |
| Sensor de reunião | +0,15 ponto percentual de um núcleo; 2,6 a 5,6 ms por leitura das sessões de áudio | F5.7 |
| Dispositivos | reiniciar o PortAudio: 57 a 84 ms; trocar o microfone: ~300 ms, sem silêncio | F5.3 |
| Exportação, reunião de 55 min | MP3 em 10 s (17 MB); MP4 em 62 s (49 MB) | F5.19 |
| Suíte de testes | rápida ~62 s; inteira ~140 s com `-n 4 --dist loadgroup` (em série, 287 s) | D-091, D-093 |
| GPU | só a Intel integrada e a NPU; sem NVIDIA, sem CUDA; nada da stack travada as usa. Só uma NVIDIA com CUDA 12 aceleraria sem trocar de motor, e um `.exe` com CUDA passaria de 5 GB | o app segue só em CPU (CP14) |
