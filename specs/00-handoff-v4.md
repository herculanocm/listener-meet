# Handoff v4 — listener-meet

Autor: Herculano · 24/10/2026 · Destinatário: Fable (LLM que vai analisar e continuar o projeto)

> Cópia fiel do documento mestre no Claude Docs: https://claude.ai/artifact/L9q6Yi1znE3cxCCJLkd6YM
> Em conflito, vale o Claude Doc. Só o Herculano altera este conteúdo. As cláusulas CP13, CP14 e CP15,
> decididas depois do handoff, estão em `AGENTS.md` e `decisoes.md`.

Este documento é a fonte da verdade do projeto. Ele substitui a Spec v3 onde houver conflito. As seções
marcadas como **CLÁUSULA PÉTREA** não podem ser alteradas, reinterpretadas nem "otimizadas" pela LLM que
for desenvolver: elas são decisões do dono do produto. Se alguma cláusula parecer tecnicamente difícil, a
LLM deve propor como cumpri-la, nunca removê-la.

## 1. O que é e por que existe

O listener-meet é um app desktop para Windows, pessoal do Herculano, que grava qualquer reunião (Teams,
Google Meet, Zoom ou qualquer outra ferramenta), identifica quem está falando por perfil de voz,
transcreve, gera legenda, resumo e ata com uma LLM e guarda tudo numa base consultável por RAG.

Motivador: Herculano participa de muitas reuniões, pessoais e corporativas, e perde decisões, ações e
contexto. Ele quer uma memória completa e pesquisável de tudo que foi dito, por quem, com qual cargo e em
que minuto, sem depender de ninguém para ativar recursos nas plataformas de reunião.

Princípios de produto:

- Funciona sozinho, no computador do Herculano, sem depender de admin, TI ou de recursos nativos das plataformas.
- A biblioteca local (pyannote + Whisper) faz o trabalho pesado; a LLM é o cérebro de contexto.
- Tudo é configurável pela interface, a qualquer momento.
- UI/UX em primeiro lugar: toda melhoria de experiência sugerida já sai pronta de fábrica.

## 2. Cláusulas pétreas

**CP1 — Projeto pessoal.** O app é do Herculano, não da SRM. É uma ferramenta pessoal rodando no computador
dele, gravando reuniões corporativas e pessoais. O Herculano assume integralmente a responsabilidade pelo
uso. Trilhas de Compliance, DPO e políticas corporativas estão fora do escopo do projeto. Não implementar
avisos, bloqueios ou modos restritos por motivo regulatório.

**CP2 — LLM agnóstica.** Nenhuma LLM é fixa. A arquitetura deve ter uma camada de abstração (interface
única de provedor) com adaptadores intercambiáveis: Google Gemini, OpenAI GPT, Anthropic Claude e outros no
futuro. A configuração inicial decidida é o **Gemini (plano pago da API)**. Trocar de provedor é apenas
mudar a configuração na interface, sem alterar código do restante do app. Prompts, formato da ata e
esquemas de saída pertencem ao projeto, não ao provedor. O mesmo vale para o modelo de embeddings do RAG:
também trocável.

**CP3 — Gravar tudo, sem expiração.** Áudio, transcrição, legendas, resumos, atas, marcadores e eventos são
mantidos para sempre. Não existe job de retenção nem apagamento automático. Só o usuário apaga, manualmente.

**CP4 — Nada é ativado nas plataformas.** Não usar legendas ao vivo do Teams, gravação nativa, transcrição
nativa, Graph API, bots ou UI Automation para ler legendas. A camada de "legendas via UIA" da Spec v3 sai do
projeto. O app só escuta áudio do sistema/aplicativo e do microfone.

**CP5 — pyannote é o motor principal de identificação.** O pyannote faz a diarização e cria **perfis de voz
persistentes** (embeddings biométricos por pessoa), reconhecendo sempre a mesma voz em todas as reuniões.
Perfis são cadastrados, reaproveitados e refinados a cada confirmação do usuário. Deve ser possível criar um
perfil a partir de um trecho de áudio escolhido.

**CP6 — Agnóstico de plataforma, com tela de warm-up.** Funciona com Teams, Meet, Zoom ou qualquer app.
Antes de cada gravação abre uma tela de pré-start (warm-up) para escolher o dispositivo de entrada
(microfone) e a fonte de saída (loopback de um aplicativo específico ou do sistema inteiro), com teste e
medidores, antes de começar a ouvir.

**CP7 — Interface bonita e corporativa.** App gráfico, visual corporativo e caprichado, para gerenciar
perfis, dispositivos, reuniões e configurações. Não é CLI.

**CP8 — Aba de Configurações total.** Toda configuração do app fica numa aba de Configurações, **editável a
qualquer momento**, inclusive durante o uso. Isso inclui, entre outros: limiar de confiança do pyannote para
acionar a LLM, volumes e ganhos, dispositivos, presets, atalhos, intervalo do resumo parcial, modelo do
Whisper, provedor de LLM e de embeddings. Há uma sub-aba por provedor (Gemini, GPT, Claude e outros), cada
uma com chave de API, modelo e parâmetros, além de um botão de testar conexão. Botão de restaurar padrão.

**CP9 — Tudo no SQLite.** Tudo que precisa ser persistido vai para o banco SQLite do próprio app:
configurações, chaves de API, perfis de voz e embeddings, fichas das pessoas, reuniões, áudio,
transcrições, legendas, resumos, atas, marcadores, eventos, vetores do RAG e histórico de edições. **Nunca
salvar em arquivos soltos.** Áudio fica em blobs comprimidos (FLAC) em pedaços de cerca de 1 minuto, para
não estourar limites de blob e permitir reprodução por trecho.

**CP10 — Chaves no app.** As chaves de API ficam guardadas no SQLite do app, não no cofre de credenciais do
Windows. Podem ficar visíveis em texto aberto na interface. Decisão do dono do produto.

**CP11 — Fluxo LLM + pyannote (cláusula principal).** O fluxo da seção 4 deve ser implementado exatamente
como descrito.

**CP12 — UX de fábrica.** Toda melhoria de interface, experiência aumentada e pré-configuração sugerida
entra por padrão. A LLM que desenvolve deve sempre pensar em UI/UX e propor melhorias ao Herculano antes de
implementar.

## 3. Arquitetura em camadas

1. **Captura.** Dois canais sincronizados num relógio único (QPC): canal L = microfone do Herculano; canal R
   = áudio dos outros participantes (loopback por processo do app de reunião escolhido, com fallback para
   loopback do sistema inteiro). Microfone mutado grava silêncio para não desalinhar. Buracos do loopback
   (WASAPI não entrega pacotes em silêncio) são preenchidos por timestamp. Pedaços de 1 minuto em FLAC
   gravados direto no SQLite.
2. **Transcrição.** WhisperX local (CPU int8 por padrão, GPU se houver), alinhamento por palavra, glossário
   enviado como initial_prompt. Gera legenda ao vivo e transcrição final.
3. **Identificação.** Canal L = Herculano. Canal R: pyannote diariza, extrai embedding de cada voz e compara
   com os perfis cadastrados (cosseno), devolvendo perfil sugerido e nota de confiança.
4. **Inteligência (LLM agnóstica).** Glossário, resumo parcial, resolução de identidade por contexto,
   título, pré-resumo, ata, chat e RAG.
5. **Base.** SQLite com FTS5 (busca por palavra) + sqlite-vec (busca vetorial), busca híbrida.
6. **Interface.** PySide6.

## 4. Fluxo LLM + pyannote (cláusula principal)

**Antes da reunião (warm-up).** O usuário informa, opcionalmente, título e participantes esperados. A LLM
monta um glossário de termos prováveis (jargões, siglas, nomes) que vai para o Whisper, e reduz a lista de
perfis candidatos para o pyannote comparar primeiro.

**Durante a reunião.** Legenda ao vivo pelo Whisper local, com nome e cargo de quem fala. A cada intervalo
configurável (padrão de alguns minutos), a LLM recebe o trecho novo e atualiza um resumo parcial visível no
app, sinalizando possíveis decisões e ações.

**Depois da reunião.**

- O pyannote entrega cada voz com perfil sugerido e confiança.
- Acima do limiar configurado, o nome é atribuído direto.
- Abaixo do limiar, a LLM analisa a transcrição em busca de pistas ("valeu, Rodrigo", assunto, cargo, lista
  de participantes) e propõe um nome com justificativa. Pode receber trechos de áudio difíceis para segunda
  opinião (o Gemini aceita áudio nativo).
- Sem evidência, fica sem nome e vai para a revisão.
- O usuário confirma na tela de revisão; cada confirmação reforça o embedding do perfil, e o sistema melhora
  com o tempo.
- A LLM gera: título sugerido, pré-resumo, ata (decisões; ações com dono e prazo; destaques dos marcadores),
  resumo executivo e transcrição revisada com nomes e cargos. Tudo indexado para o RAG.
- A LLM nunca substitui uma identificação do pyannote de alta confiança; ela só complementa abaixo do limiar.

## 5. Telas e funcionalidades

**Bandeja e janela principal.** Ícone na bandeja do Windows com estado; abre a janela principal com
navegação entre Gravar, Histórico, Perfis e Configurações.

**Warm-up (pré-start).**

- Seleção de microfone e da fonte de saída (app específico como Zoom, Teams, navegador do Meet, ou sistema inteiro).
- Medidores de volume ao vivo por canal e teste de áudio.
- Lembra a última configuração usada.
- Presets por plataforma (Zoom, Teams, Meet), editáveis.
- Título e participantes esperados opcionais.

**Widget flutuante de gravação.** Sempre no topo, sobre qualquer janela, arrastável. Mostra tempo decorrido
e medidores por canal. Botões:

- **Pausar/Retomar:** pausa marcada na linha do tempo, mesma reunião.
- **Marcador com anotação:** marca o momento e abre um campo rápido para o Herculano escrever uma nota, sem
  parar a gravação. A LLM destaca esses pontos na ata e usa a nota como contexto; no histórico dá para pular
  direto para eles.
- **Parar:** encerra, salva e vai para a tela de revisão.
- **Cancelar:** descarta a gravação, sempre com confirmação.
- Alerta se um canal ficar mudo durante a gravação.

**Atalhos globais.** Iniciar, pausar, parar e marcar funcionam de qualquer lugar, mesmo com o app escondido
(exemplo: Ctrl+Shift+R para gravar). Todos configuráveis.

**Notificação de reunião detectada.** Quando um app de reunião começa a tocar áudio, aparece um aviso
sugerindo gravar. Nunca grava sozinho. Desligável nas configurações.

**Tela de revisão (fim da gravação).** Uma tela única:

- Título sugerido pela LLM, editável, com botão de gerar de novo.
- Timestamps de início e fim registrados.
- Pré-resumo sugerido pela LLM, editável, com botão de gerar de novo.
- Confirmação dos falantes não reconhecidos ou de baixa confiança, com a sugestão e justificativa da LLM e
  um trecho de áudio para ouvir.
- O banco guarda a sugestão original da LLM e a versão editada pelo usuário.
- Não pergunta nada sobre cargo ou dados de perfil (isso se edita só na tela de Perfis).

**Histórico.**

- Lista de todas as reuniões com busca e filtros por pessoa, data e título.
- Reunião aberta mostra ata, resumo, transcrição com nome e cargo, e marcadores com anotações.
- Clicar em qualquer frase toca o áudio daquele ponto.
- Chat com a LLM sobre uma reunião ou sobre todas, com RAG; respostas citam reunião, quem falou e minuto
  [mm:ss], clicáveis.

**Perfis de voz.**

- Lista de pessoas com amostras de áudio para ouvir e número de reuniões em que aparecem.
- Criar perfil (inclusive a partir de um trecho de áudio), renomear, apagar, mesclar perfis que são a mesma
  pessoa e separar um perfil que juntou duas vozes.
- Ficha da pessoa: nome, e-mail, cargo, empresa, descrição livre e demais campos.
- Ao apagar, perguntar se mantém o nome nas reuniões antigas.
- Edição de dados e cargo acontece só quando o Herculano quiser, nesta tela.

**Nome e cargo em todo lugar.** Cada fala na legenda, na transcrição, na ata e nos chunks do RAG carrega
nome e cargo de quem falou (ex.: "Rodrigo — Tech Lead"), para a LLM entender o peso de cada fala (CTO,
gerente de produto, etc.). Sugestão a validar com o Herculano: guardar o cargo vigente no dia da reunião
junto da fala, além do cargo atual na ficha.

**Configurações.** Conforme CP8, incluindo: dispositivos e presets; volumes e ganhos; limiar de confiança
do pyannote; intervalo do resumo parcial; modelo e dispositivo do Whisper (CPU/GPU); glossário fixo;
atalhos; notificação de reunião detectada; provedores de LLM e embeddings com sub-abas; tamanho do banco e
botão de backup; restaurar padrão.

## 6. Stack

- Python 3.12 num único venv (WhisperX exige Python < 3.14).
- Interface: PySide6.
- Captura: WASAPI; proc-tap para loopback por processo, com fallback de endpoint; relógio QPC.
- Transcrição: WhisperX (faster-whisper, int8 em CPU; GPU opcional).
- Identificação: pyannote (diarização + embeddings de falante); token do Hugging Face e aceite dos termos
  do modelo community-1.
- LLM: camada de provedores com adaptadores Gemini (inicial), GPT e Claude.
- Banco: SQLite + FTS5 + sqlite-vec; áudio em blobs FLAC.
- Empacotamento: venv + atalho enquanto for pessoal; PyInstaller só se necessário.

Modelo de dados sugerido (a refinar): settings, providers, people (ficha), voice_profiles e
voice_embeddings, meetings, audio_chunks, segments (fala, canal, tempo, pessoa, cargo no dia, confiança,
origem da atribuição), captions, markers (tempo e anotação), summaries e minutes (versão LLM e versão
editada), action_items, events, rag_chunks e rag_vectors.

## 7. O que já existe (da Spec v3)

Código atual em Python que já roda: captura por process loopback do ms-teams.exe, microfone WASAPI 16 kHz
com silêncio quando mutado, relógio único QPC, FLAC por minuto, preenchimento de buracos por timestamp,
eventos.jsonl, CLI (devices, record, watch), 25 testes unitários + smoke. Precisa ser adaptado:
generalizar a captura para qualquer processo ou sistema, trocar arquivos (FLAC, jsonl) por SQLite, remover
a camada de legendas via UIA e as libs sem uso (keyboard, windows-capture), organizar o pacote em
capture/, pipeline/, llm/, store/, ui/ e fazer git init.

> Nota de 24/10/2026: por decisão posterior do Herculano (D-006), esse código foi apagado e o repositório
> recomeçou do zero em SDD. O que ele validou está registrado em `ARCHITECTURE.md`.

## 8. Mudanças em relação à Spec v3

- LLM: Claude fixo → camada agnóstica, Gemini inicial.
- Identificação: legendas do Teams via UIA → pyannote com perfis de voz persistentes.
- Perfis de voz: fora do MVP → núcleo do produto.
- Retenção de 7 dias → sem expiração.
- Compliance/DPO, modo só-legendas para externas e avisos → fora do escopo (CP1).
- Captura presa ao Teams → agnóstica de plataforma.
- CLI e bandeja mínima → app gráfico completo e corporativo.
- Arquivos (FLAC, jsonl, md, json) → tudo no SQLite.
- Novas funcionalidades: warm-up, widget flutuante (pausar, marcar, parar, cancelar), atalhos globais,
  detecção de reunião, revisão com título e pré-resumo, histórico com RAG, gestão de perfis com ficha, nome
  e cargo nas falas, aba de configurações total.

## 9. Pontos técnicos de atenção

- O canal R mistura todas as vozes remotas: fala sobreposta reduz a precisão da diarização.
- pyannote e WhisperX em CPU são lentos; medir em 1 h de áudio e considerar GPU.
- Crescimento do banco: cerca de 20 GB por mês sem expiração; FLAC, backup e indicador de tamanho são necessários.
- Chaves em texto aberto e banco com todo o histórico: manter o .db fora do OneDrive e de qualquer
  repositório git (.gitignore), e BitLocker ligado.
- Uso em reuniões corporativas pode esbarrar em políticas internas de segurança da empresa; decisão e
  responsabilidade do Herculano (CP1).
- Validar o modelo de alinhamento do WhisperX para português.

## 10. Como o Fable deve trabalhar com o Herculano

- Respeitar todas as cláusulas pétreas sem reabri-las.
- Comunicação em português do Brasil, informal e direta.
- Sempre pensar em UI/UX e propor melhorias, perguntando antes de incluir.
- Primeira tarefa sugerida: ler este handoff, apontar lacunas ou contradições, propor o plano de fases
  (captura generalizada → SQLite → pyannote com perfis → LLM agnóstica → telas) com critérios de aceite
  medidos em reuniões reais, e só então codar.
