# Cláusulas pétreas (CP1 a CP17)

Quando ler: sempre. São as decisões do dono do produto que nenhuma spec, revisão ou otimização reabre. Só ele altera
uma cláusula. Se uma parecer difícil de cumprir, proponha como cumpri-la, nunca como removê-la. Tamanho-alvo: 6 KB.

| CP | Cláusula |
| --- | --- |
| CP1 | **Projeto pessoal.** O app é do dono, não do empregador dele, e ele assume o uso. Compliance, DPO, avisos e modos restritos por motivo regulatório ficam fora do escopo. |
| CP2 | **LLM agnóstica.** Uma interface única de provedor com adaptadores (Gemini, OpenAI, Claude e outros). Trocar de provedor é só configuração. Prompts, formato da ata e esquemas de saída são do projeto, não do provedor. O modelo de embeddings da busca também é trocável. |
| CP3 | **Gravar tudo, sem expiração.** Áudio, transcrição, legendas, resumos, atas, marcadores e eventos ficam para sempre. Não há retenção automática: só o usuário apaga, à mão. |
| CP4 | **Nada ativado nas plataformas.** Sem legendas nativas, gravação nativa, API da plataforma, bots ou UI Automation. O app só ouve o áudio do sistema ou do app e o microfone. |
| CP5 | **pyannote é o motor de identificação.** Diarização e perfis de voz persistentes (embeddings por pessoa), reconhecidos em toda reunião e refinados a cada confirmação do usuário. Dá para criar um perfil a partir de um trecho de áudio. |
| CP6 | **Qualquer plataforma, com "Antes de gravar" (warm-up).** Funciona com Teams, Meet, Zoom ou qualquer app. Antes de cada gravação, uma tela escolhe o microfone e a fonte de saída (um app ou o sistema inteiro), com teste e medidores. |
| CP7 | **Interface gráfica caprichada e corporativa** (PySide6) para perfis, dispositivos, reuniões e configurações. Não é CLI. |
| CP8 | **Configurações totais.** Toda configuração é editável a qualquer momento, inclusive durante o uso: limiar do pyannote, ganhos, dispositivos, presets, atalhos, intervalo do resumo parcial, modelo do Whisper, provedores (uma sub-aba por provedor, com chave, modelo, parâmetros e "testar conexão") e "restaurar padrão". |
| CP9 | **Tudo no SQLite do app.** Configurações, chaves, perfis e embeddings, fichas, reuniões, áudio (FLAC em pedaços de ~1 min), transcrições, legendas, resumos, atas, marcadores, eventos, vetores e histórico de edições. Nunca em arquivos soltos. |
| CP10 | **Chaves no app.** As chaves de API ficam no SQLite do app, não no cofre de credenciais do sistema, e podem aparecer em texto aberto na interface. |
| CP11 | **Fluxo LLM + pyannote.** O fluxo descrito abaixo é implementado como está. A LLM nunca substitui uma identificação de alta confiança do pyannote; só complementa abaixo do limiar. |
| CP12 | **UX de fábrica.** Toda melhoria de interface e toda pré-configuração sugerida entram por padrão. Quem desenvolve sempre propõe a melhoria e pergunta antes de implementar. |
| CP13 | **Nenhum código sem autorização.** Código, build, dependência e script só com autorização explícita e prévia do dono, por entrega. Documentos e specs podem. Mudança sem autorização é descartada. |
| CP14 | **Só CPU, só open source.** Sem placa de vídeo: tudo roda em CPU. Só bibliotecas open source e a LLM paga. Qualquer outra coisa é proposta, não decisão. |
| CP15 | **Open source e multiplataforma.** Windows primeiro: é o propósito do app, e o que for feito no Linux só vale depois de verificado no Windows. Nada específico de sistema operacional fora de `listener/platform/<os>/`; nada pessoal no código; caminhos e dispositivos configuráveis; o usuário se configura na primeira abertura. |
| CP16 | **Commit obrigatório** a cada item fechado: entrega implementada, spec aprovada, decisão registrada, reestruturação de documentação, gate medido. Quem fecha o item commita, sem precisar pedir. Mensagem em PT-BR citando a spec ou a decisão. |
| CP17 | **Python moderno.** O piso é 3.12 e só sobe (o teto `<3.14` vale enquanto o WhisperX e o torch o exigirem). Sem retrocompatibilidade: nada de shim, polyfill ou sintaxe antiga. Usar os recursos e as otimizações da versão em uso (tipos PEP 695, `@override`, `itertools.batched`, `sqlite3` com `autocommit`, dataclasses com `slots`, `asyncio.TaskGroup`), com performance primeiro. |

## Fluxo LLM + pyannote (CP11)
**Antes da reunião.** O usuário informa, se quiser, o título e os participantes esperados. A LLM monta um glossário de
termos prováveis (jargões, siglas, nomes), que vai para o Whisper, e reduz a lista de perfis candidatos.

**Durante a reunião.** A legenda ao vivo sai do Whisper local, com o nome e o cargo de quem fala. A cada intervalo
configurável, a LLM recebe o trecho novo e atualiza um resumo parcial visível, com possíveis decisões e ações.

**Depois da reunião.**
1. O pyannote entrega cada voz com o perfil sugerido e a confiança.
2. Acima do limiar configurado, o nome é atribuído direto.
3. Abaixo do limiar, a LLM procura pistas na transcrição (um "valeu, Rodrigo", o assunto, o cargo, a lista de
   participantes) e propõe um nome com a justificativa. Ela pode receber trechos de áudio difíceis para uma segunda
   opinião, se o usuário ligar a opção.
4. Sem evidência, a voz fica sem nome e vai para a revisão.
5. O usuário confirma na revisão; cada confirmação reforça o perfil de voz, e o sistema melhora com o tempo.
6. A LLM gera o título, o pré-resumo, a ata (decisões; ações com dono e prazo; destaques dos marcadores), o resumo
   executivo e a transcrição revisada com nomes e cargos. Tudo é indexado para a busca.

Cada fala, na legenda, na transcrição, na ata e no índice, carrega o nome e o cargo de quem falou; o cargo do dia da
reunião fica gravado junto da fala (D-016).

## Exceções já decididas
- Exportar ata, transcrição, áudio e vídeo por ação do usuário não fere a CP9 (D-012, D-098).
- Ficam fora do SQLite: o ponteiro da pasta do banco (D-029), o arquivo de log escolhido pelo usuário (D-033) e os
  modelos de ML, que não são dados do usuário.
- Ata, ações e resumo executivo só são gerados a pedido (D-063); o resto do fluxo da CP11 segue automático.
- A captura da tela a pedido, para as sugestões ao vivo, cabe na CP4: é uma captura do sistema por ação explícita,
  sem nada ativado na plataforma (D-102).
- O canal de doação, o nome do autor e o repositório ficam em `listener/about.py` como dado público do projeto, sem
  ferir a CP15 (D-079).
