# Cláusulas pétreas CP1 a CP17 (cartão de leitura)

Quando ler: sempre (T0). Texto integral de CP1 a CP12 no handoff §2; CP13 a CP17 em D-001, D-002, D-030, D-037 e D-038. Só o Herculano
altera uma cláusula. Se uma parecer difícil, propor como cumpri-la, nunca removê-la. Tamanho-alvo: 3 KB.

| CP | Cláusula |
| --- | --- |
| CP1 | Projeto pessoal do Herculano, não da SRM; ele assume o uso. Compliance, DPO, avisos e modos restritos por motivo regulatório ficam fora do escopo. |
| CP2 | LLM agnóstica: interface única de provedor com adaptadores (Gemini pago inicial; GPT, Claude e outros). Trocar é só configuração. Prompts, formato da ata e esquemas são do projeto. Embeddings do RAG também trocáveis. |
| CP3 | Gravar tudo, sem expiração: áudio, transcrição, legendas, resumos, atas, marcadores e eventos para sempre. Sem retenção automática; só o usuário apaga, manualmente. |
| CP4 | Nada ativado nas plataformas: sem legendas nativas, gravação nativa, Graph API, bots ou UI Automation. Só áudio do sistema ou do app e microfone. |
| CP5 | pyannote é o motor de identificação: diarização e perfis de voz persistentes (embeddings por pessoa), reconhecidos em toda reunião e refinados a cada confirmação. Perfil a partir de um trecho escolhido. |
| CP6 | Agnóstico de plataforma (Teams, Meet, Zoom, qualquer app) com warm-up antes de cada gravação: microfone, fonte de saída (app ou sistema), teste e medidores. |
| CP7 | Interface gráfica bonita e corporativa (PySide6) para perfis, dispositivos, reuniões e configurações. Não é CLI. |
| CP8 | Configurações totais, editáveis a qualquer momento, inclusive em uso: limiar do pyannote, ganhos, dispositivos, presets, atalhos, intervalo do resumo parcial, modelo do Whisper, provedores com sub-aba por provedor (chave, modelo, parâmetros, testar conexão), restaurar padrão. |
| CP9 | Tudo no SQLite do app: configurações, chaves, perfis e embeddings, fichas, reuniões, áudio (FLAC em pedaços de ~1 min), transcrições, legendas, resumos, atas, marcadores, eventos, vetores, histórico de edições. Nunca arquivos soltos. |
| CP10 | Chaves de API no SQLite do app, não no cofre do Windows; podem aparecer em texto aberto na interface. |
| CP11 | O fluxo LLM + pyannote do handoff §4 é implementado exatamente como descrito; a LLM nunca substitui uma identificação de alta confiança do pyannote, só complementa abaixo do limiar. |
| CP12 | UX de fábrica: toda melhoria de interface e pré-configuração sugerida entra por padrão; a LLM sempre propõe UX e pergunta antes de implementar. |
| CP13 | Nenhuma codificação (código, build, dependências, scripts) sem autorização explícita e prévia do Herculano, por entrega. Documentos e specs podem. Mudança sem autorização é descartada. |
| CP14 | Sem placa de vídeo: tudo em CPU nesta máquina. Só bibliotecas open source (Python) e a LLM paga (Gemini inicial). Qualquer outra coisa é proposta, não decisão. |
| CP15 | Open source e multiplataforma: Windows primeiro, adaptável a Linux e macOS. O Windows é o propósito do app: a melhor performance e experiência são lá, e o que for feito no Linux ou no WSL só vale depois de verificado no Windows (dono, 25/10). Nada específico de SO fora de `listener/platform/<os>/`; nada pessoal no código; caminhos e dispositivos configuráveis; o usuário se configura na primeira inicialização. |
| CP16 | Commit obrigatório a cada grande alteração ou item importante: entrega implementada (fase ou correção), spec aprovada, decisão registrada, reestruturação de documentação, gate medido. A LLM commita ao fechar o item, sem precisar pedir; nenhuma sessão termina com mudança grande sem commit. Mensagem em PT-BR citando a spec ou a decisão. |
| CP17 | Python: piso 3.12, a versão mais nova que a stack aceita em 24/10/2026 (WhisperX 3.8.6 e torch 2.8 exigem menos que 3.14; D-019). Sem retrocompatibilidade: nada de shims, polyfills ou sintaxe antiga para rodar em 3.11 ou menos. Usar os recursos e as otimizações da versão em uso (sintaxe de tipos PEP 695, `@override`, `itertools.batched`, f-strings PEP 701, `sqlite3` com `autocommit`, dataclasses com `slots`, `asyncio.TaskGroup`) e pensar em performance primeiro. O piso só sobe: quando a stack permitir uma versão mais nova, migrar e subir o `requires-python`; nunca descer. |

Exceções já decididas: exportar ata e transcrição por ação do usuário não fere CP9 (D-012); fora do SQLite ficam só o
ponteiro da pasta do banco (D-029) e o arquivo de log escolhido pelo usuário (D-033); modelos de ML ficam numa pasta
própria porque não são dados do usuário (F1).
