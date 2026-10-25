# Decisões (D-nnn), perguntas abertas (P-nn) e medições

Quando ler: ao mexer em produto ou arquitetura (T1), ou quando uma spec citar um D. Formato: quem · decisão · motivo.
Decisões mudam o que o sistema é; o que muda só a forma de trabalhar fica em `memory/dono.md`. Tamanho-alvo: 8 KB.

## Decididas (todas em 2026-10-24)
| # | Quem | Decisão | Motivo |
| --- | --- | --- | --- |
| D-001 | Herculano | CP13: nenhuma codificação sem autorização explícita, por entrega; documentos não precisam | controle do dono; SDD |
| D-002 | Herculano | CP14: sem GPU, tudo em CPU; só open source e a LLM paga (Gemini inicial); o resto é proposta | máquina sem GPU |
| D-003 | Herculano | dois SQLite anexados: `listener.db` (texto, vetores, config) e `listener-audio.db` (FLAC) | backup e gestão; continua CP9 |
| D-004 | Herculano | mudo global muta o microfone no Windows (o app de reunião também silencia) e grava silêncio | único mudo visível sem API de plataforma (CP4) |
| D-005 | Herculano | legenda ao vivo leve (VAD e Whisper em janelas, 5 a 10 s de atraso, nome por embedding best effort); a passagem final substitui | WhisperX e diarização são batch; CPU dividida |
| D-006 | Herculano | repositório recomeçado do zero em SDD; protótipo v3 apagado sem commit | "não quero guardar lixo" |
| D-007 | Herculano | Handoff v4 é o mestre; Spec v3 superada | decisão do dono |
| D-008 | LLM (aceita) | identificação = embedding do pyannote, centroide por pessoa e cosseno; limiar 0,60, margem, autocalibração | identificação pronta da pyannoteAI é paga; CP5 |
| D-009 | Herculano | embeddings do RAG: gemini-embedding-001 em 768 dimensões; modelo e dimensão por vetor; reindexar ao trocar | coerente com CP2; 768 preserva a qualidade |
| D-010 | Herculano | detecção de reunião por sessões de áudio do Windows (processo e pico); Teams, Zoom e Meet no navegador (título); nunca grava sozinho | título de janela é do SO, não da plataforma (CP4) |
| D-011 | Herculano | pausa mantém a linha do tempo contínua (silêncio comprimido); evento; o player pula | alinhamento L/R e marcadores não se deslocam |
| D-012 | Herculano | exportar ata e transcrição (.md, .docx, área de transferência) só por ação do usuário | CP9 trata da persistência do app |
| D-013 | Herculano | idioma detectado por reunião (pt-BR e inglês); glossário bilíngue | há reuniões em inglês |
| D-014 | Herculano | processamento começa sozinho ao Parar, numa fila com pausar e retomar | sem isso a ata atrasa |
| D-015 | Herculano | 16 kHz mono nos dois canais por padrão (5 a 6 GB por mês); taxa configurável | Whisper e pyannote usam 16 kHz |
| D-016 | Herculano | `segments.role_at_time`: cargo do dia junto da fala, além do atual na ficha | ata antiga não muda com promoção |
| D-017 | Herculano | U-001 a U-015 aprovadas; entram por padrão nas fases | CP12 |
| D-018 | Herculano | CP13 e CP14 registradas em AGENTS e aqui; o handoff no Claude Docs não é editado | padrão confirmado |
| D-019 | Herculano | venv 3.14 do protótipo apagado; produto em Python 3.12 | WhisperX exige < 3.14 |
| D-020 | Herculano | M-001: transcrição final pela LLM com áudio nativo (Gemini) como opção ao lado do Whisper local | alívio de CPU; custo e saída do áudio são escolha do usuário |
| D-021 | Herculano | M-002: embeddings locais (multilingual-e5) como opção ao lado do Gemini | CP2 e CP14; zero custo e offline |
| D-022 | Herculano | spec F0 aprovada; `scripts/medir.py` autorizado | primeira autorização sob CP13 |
| D-023 | Herculano | pasta `memory/` no repositório | memória compartilhada entre LLMs |
| D-024 | LLM (medido) | vetores com sqlite-vec 0.1.9 (`vec0`); fallback BLOB e numpy previsto na interface | carrega no 3.12 com SQLite 3.49.1; KNN ok |
| D-025 | LLM (medido) | áudio decodificado com soundfile e entregue em memória ao pyannote e ao Whisper; nunca FFmpeg nem torchcodec | torchcodec não carrega sem FFmpeg |
| D-026 | Herculano | app de código aberto; nada pessoal no código; primeira inicialização coleta os dados do usuário e a pasta do banco | respostas Q3 e Q6 da F1 |
| D-027 | Herculano | portabilidade futura para Linux e macOS; nada do Windows fora da camada de plataforma | pedido do dono |
| D-028 | Herculano | M-003: timestamps por palavra do faster-whisper como padrão (0,67×); wav2vec2 como opção "alta precisão" | medido: wav2vec2 custava mais que a transcrição |
| D-029 | Herculano | spec F1 aprovada: Q1, Q2, Q4 e Q5 sim; Q3 assistente de primeira inicialização; Q6 usuário escolhe a pasta do banco | primeira entrega com código |
| D-030 | Herculano | CP15: open source e multiplataforma como cláusula pétrea (eleva D-026 e D-027) | decisão do dono |
| D-031 | Herculano ("Ok, siga") | documentação reescrita no formato compacto para IA: camadas T0 a T3, regras em `specs/README.md`, memória superada apagada; de 128 KB para 92 KB no total e de 78 KB para 10 KB na leitura inicial | arquivos estouravam o limite de leitura |
| D-032 | Herculano | depois da compactação, tudo que não era necessário foi apagado, inclusive o `.git` com o snapshot; git reiniciado limpo, sem commits até ele pedir | "não quero guardar lixo" |
| D-033 | Herculano | log do app em arquivo rotativo escolhido pelo usuário na primeira inicialização, com nível (DEBUG, INFO, WARNING, ERROR) editável nas Configurações; exceção explícita ao CP9 | diagnóstico legível fora do app e vivo mesmo com o banco em falha |
| D-034 | Herculano | licença MIT (`LICENSE`) | resposta à P18 |
| D-035 | Herculano | spec F1.1 aprovada em bloco: A1 a A17, U-016 e U-017; atalhos `ctrl+alt+shift+letra` com `RegisterHotKey` no Windows (P19); datas com offset (P21); implementada no mesmo dia | correções antes do G1 |
| D-036 | Herculano | spec F2 aprovada com Q1 a Q9 nos padrões (worker em subprocesso, token HF no banco, limiar 0,60 e margem 0,05, turbo int8, clusters curtos só na revisão, leitura mínima, fila pausa em gravação, embeddings do pipeline se expostos, coluna `platform`) | libera o código da F2 depois do G1 |
| D-037 | Herculano | **CP16 (cláusula pétrea):** commit obrigatório a cada grande alteração ou item importante (entrega, spec aprovada, decisão, reestruturação de docs, gate medido); a LLM commita ao fechar o item, sem pedir; nenhuma sessão termina com mudança grande sem commit | substitui "commits só quando ele pedir"; histórico é a memória do projeto |
| D-038 | Herculano | **CP17 (cláusula pétrea):** Python 3.12 como piso, sem retrocompatibilidade com versões anteriores; usar recursos e otimizações da versão em uso, performance primeiro; o piso só sobe quando a stack permitir (`requires-python` acompanha), nunca desce | 3.12 é a versão mais nova que WhisperX 3.8.6 e torch 2.8 suportam (menos que 3.14, D-019); app novo, sem base instalada a preservar |
| D-039 | Herculano ("Implemente todas as melhorias com o agente especialista sênior") | spec F1.2 aprovada em bloco (M-01 a M-31; Q1 a Q7 nos padrões): dois lotes; **um arquivo de áudio por mês** (`listener-audio-AAAA-MM.db`, anexado sob demanda; complementa D-003); Python 3.13 no CI; reamostragem com estado; F2 reaberta só no item da fila (M-20); relatórios dos revisores ficam fora do repo; implementação por agente especialista com commit por item | 65 achados de dois revisores; bugs que perdem áudio e memória antes do G1; SQLite pronto para o worker da F2 |
| D-040 | Herculano | fluxo por entrega com agentes (`AGENTS.md`): arquiteto revisa a spec rascunhada contra o código; especialista implementa com briefing e commit por item; revisor novo lê só o diff; orquestradora verifica e fecha; o dono aprova a spec, verifica no Windows e mede o gate | custo medido em 24/10: revisões em paralelo em ~25 min, 31 itens implementados em 70 min; briefing com decisões prontas evita travas e invenções |

## Em aberto (padrão assumido enquanto não há resposta)
| # | Pergunta | Padrão |
| --- | --- | --- |
| P22 | correção da deriva de relógio (F1.1 A7) | só instrumentar; corrigir em spec própria se o G1 mostrar mais de 100 ms por hora |

## Medições (nesta máquina: Intel 14 núcleos, Windows 11, sem CUDA, torch 2.8.0+cpu com 12 threads; detalhes em `memory/maquina.md`)
| Data | O quê | Resultado |
| --- | --- | --- |
| 24/10 | WhisperX large-v3-turbo int8, 5 min de TTS, 2 vozes | transcrição 0,49× · alinhamento wav2vec2 pt 0,62× · total 1,11× (67 min por hora de áudio) |
| 24/10 | faster-whisper direto, `word_timestamps`, VAD, beam 5, mesmo áudio | 0,67× (40 min por hora), já com palavras e tempos |
| 24/10 | sqlite-vec 0.1.9 | carrega, cria `vec0`, KNN responde (SQLite 3.49.1) |
| 24/10 | selftest da F1 no Windows (mic e loopback do sistema) | L e R contínuos; desvio de 19 ms na duração; sem buracos no índice |
| 24/10 | documentação antes da compactação | 128 KB no total; 78 KB na trilha obrigatória |
| 24/10 | F1.2, WSL: buraco de 30 min no R | pico de alocação de 714 MB para 4,4 MB; captura em regime de ~25 MB para 5,2 MB |
| 24/10 | F1.2, WSL: `to_mono` 60 s · `rms` por bloco · `estimate_offset` 10 s | 36 para 2,1 ms · 4,3 para 0,8 µs · 41,8 para 2,3 ms; suíte de 7,8 s (41 testes) para 3,1 s (85) |
| pendente | pyannote community-1 por hora de áudio em CPU | precisa de `HF_TOKEN` (B4) |
| pendente | banco por hora de reunião; deriva e deslocamento L/R; `list_playing_apps` | G1 e B1 |
