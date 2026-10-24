# specs/ — como as specs funcionam e como escrever documentos aqui

Quando ler: antes de escrever ou alterar qualquer documento. Tamanho-alvo: 3,5 KB.

## Arquivos
`00-handoff-v4.md` mestre do dono (só ele edita) · `01-clausulas.md` cartão CP1 a CP15 · `decisoes.md` D-nnn, perguntas
P-nn e medições · `roadmap.md` fases e gates · `backlog.md` estado e o que fazer agora · `dados.md` modelo de dados
canônico · `ux-melhorias.md` U-nnn · `features/F<n>-<nome>.md`
uma spec por entrega (correções de fase aprovada: `F<n>.<m>-<nome>.md`).

## Ciclo de vida de uma spec
`rascunho` (LLM escreveu; pode ter perguntas e melhorias) → `em revisão` (ele lendo) → `aprovada` (linha "Aprovada por
Herculano em AAAA-MM-DD"; libera código, CP13) → `implementada` (o código faz o que a spec diz; nada além) →
`verificada` (gate medido em reunião real, anotado em `decisoes.md`). Mudou o contrato de uma spec aprovada? Volta para
`em revisão` e o código espera. Condensar o texto sem mudar o contrato não reabre a aprovação (D-031).

## Template (seções obrigatórias, nesta ordem)
Cabeçalho: `Status · Aprovada por Herculano em · Fase e gate · CPs` e a linha `Quando ler / Tamanho-alvo`.
`## Objetivo` (uma frase: o que o usuário consegue fazer) · `## Escopo` (itens verificáveis) · `## Fora de escopo` ·
`## Cláusulas` (uma linha por CP envolvida: como cumpre) · `## Interface` (telas, estados, atalhos; melhorias
`U-nnn ... incluir? sim/não`) · `## Dados` (só o que muda em relação a `dados.md`) · `## Comportamento` (passo a passo;
erros; pausar, cancelar, perder dispositivo, ficar sem rede) · `## Aceite` (gate medido em reunião real, com número e
como medir) · `## Riscos` (com mitigação) · `## Perguntas` (cada uma com o padrão assumido).

## Regras de escrita para leitura por IA (D-031)
1. Dono único: cada fato mora em um arquivo; os outros citam o ID (CP, D, P, U, A, B, Q, F/G) sem repetir o texto.
2. Cabeçalho de duas linhas: o que é, quando ler, tamanho-alvo. Orçamento conferido com `wc -c` ao fechar a sessão.
3. Enumeração vira tabela ou bullet de uma frase; justificativa em uma oração.
4. Tags [fato] (fonte ou teste), [exp] (experiência) e [chute] (a medir) no lugar de frases sobre certeza.
5. Estado atual só em `backlog.md`; nenhum outro arquivo tem seção "estado".
6. `memory/` guarda só o que não está em spec nem em código; memória superada é apagada depois de incorporada (o git
   guarda o histórico).
7. Sem narrativa de sessão: histórico é `decisoes.md` mais git.
8. Markdown puro, linhas de até 120 colunas, sem HTML nem emoji; títulos previsíveis.
9. Critérios de aceite sempre com número; perguntas abertas sempre com padrão assumido.
10. Orçamentos: T0 (AGENTS, cláusulas, backlog) até 12 KB somados; spec de fase até 8 KB; `decisoes.md` até 8 KB.
