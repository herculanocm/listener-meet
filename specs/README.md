# specs/ — como a documentação funciona

Quando ler: antes de escrever ou alterar qualquer documento. Tamanho-alvo: 5 KB.

## Arquivos
| Arquivo | Dono único de |
| --- | --- |
| `01-clausulas.md` | cláusulas CP1 a CP17 e o fluxo LLM + pyannote (CP11) |
| `decisoes.md` | decisões D-nnn, perguntas abertas P-nn e medições de referência |
| `backlog.md` | estado do projeto: pendências, perguntas ao dono, dívidas técnicas |
| `roadmap.md` | o que vem pela frente e os gates |
| `dados.md` | modelo de dados (tabelas, migrações, chaves de configuração) |
| `llm-contrato.md` | desenho técnico da camada de LLM |
| `ux-melhorias.md` | catálogo das melhorias de UX (U-nnn) |
| `features/F<n>-<nome>.md` | uma spec por entrega |

Fora desta pasta: `AGENTS.md` (regras e comandos), `ARCHITECTURE.md` (camadas, processos, logs), `CHANGELOG.md`
(o que cada versão entregou) e `memory/` (fatos do ambiente e achados de revisão já decididos).

## Ciclo de vida de uma spec
`rascunho` (pode ter perguntas) → `em revisão` (o dono lendo) → `aprovada` (libera o código, CP13) → `implementada`
(o código faz o que a spec diz, e a spec é condensada). Mudou o contrato de uma spec aprovada? Ela volta para
`em revisão` e o código espera. Reescrever o texto sem mudar o contrato não reabre a aprovação. Cada transição
importante fecha com um commit (CP16).

## Modelo de spec nova (seções obrigatórias, nesta ordem)
Cabeçalho: `Status · Aprovada em · CPs` e a linha `Quando ler / Tamanho-alvo`.
| Seção | Conteúdo |
| --- | --- |
| `## Objetivo` | uma frase: o que o usuário consegue fazer |
| `## Escopo` | itens verificáveis |
| `## Fora de escopo` | o que alguém esperaria e não entra |
| `## Cláusulas` | uma linha por CP envolvida: como a spec a cumpre |
| `## Interface` | telas, estados, atalhos; cada melhoria como `U-nnn ... incluir? sim/não` |
| `## Dados` | só o que muda em relação a `dados.md` |
| `## Comportamento` | passo a passo; erros; pausar, cancelar, perder dispositivo, ficar sem rede |
| `## Aceite` | critérios com número e como medir, em uso real |
| `## Riscos` | cada um com a mitigação |
| `## Perguntas` | cada uma com o padrão assumido |

## Modelo de spec implementada (a forma condensada, de 4 a 9 KB)
Ao fechar a entrega, a spec é reescrita para o contrato vigente: o que uma pessoa ou LLM que nunca viu o projeto
precisa para mexer na feature sem quebrar uma regra.
Cabeçalho: `Status: implementada · Versão · Aprovada em · Decisões · CPs` e a linha `Quando ler / Tamanho-alvo`.
| Seção | Conteúdo |
| --- | --- |
| `## Objetivo` | uma frase |
| `## Comportamento` | o que o produto faz hoje: regras, padrões e limites com número, chaves de configuração, bordas |
| `## Interface` | telas, estados, textos-chave, atalhos; melhorias pelo ID (U-nnn) |
| `## Dados` | tabelas, colunas, migração e chaves de `settings` que a feature criou ou mudou |
| `## Código` | módulos e testes principais, um por linha, com o papel |
| `## Invariantes` | o que uma mudança futura não pode quebrar, com o motivo em uma oração |
| `## Fora de escopo` | fronteiras que ainda valem |
| `## Aceite` | critérios com número e como medir |

Saem da spec implementada: lotes, briefing de implementação, listas de testes, perguntas já respondidas, achados de
revisão aplicados, medições de uma sessão e qualquer estado. O histórico é o git.

## Regras de escrita
O código é a referência do que está implementado (D-109): ao achar uma divergência, ajuste o documento ao código.
A spec vem antes do código só para o que ainda vai ser feito (CP13).
1. Dono único: cada fato mora em um arquivo; os outros citam o ID (CP, D, P, U, F) sem repetir o texto.
2. Cabeçalho de duas linhas: o que é, quando ler e o tamanho-alvo. Confira o tamanho ao fechar a sessão.
3. Enumeração vira tabela ou bullet de uma frase; justificativa em uma oração.
4. Marque a certeza: [fato] (fonte ou teste), [exp] (experiência), [chute] (a medir).
5. Estado só em `backlog.md`. Nenhum outro arquivo diz o que falta fazer ou verificar.
6. Sem narrativa de sessão: nada de "hoje", de quem implementou, de hash de commit ou de ordem de trabalho.
7. `memory/` guarda só o que não está em spec nem em código; o que foi superado é apagado.
8. Markdown puro: sem HTML e sem emoji; prosa com até 120 colunas (linha de tabela pode passar); títulos previsíveis.
9. Critério de aceite sempre com número; pergunta aberta sempre com o padrão assumido.
10. Português do Brasil correto e direto; identificadores, caminhos e chaves como estão no código.
