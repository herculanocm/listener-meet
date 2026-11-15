# F6 — Telas completas e UX de fábrica

Status: rascunho (precisa de detalhamento, revisão de arquitetura e aprovação do dono antes de qualquer código)
CPs: CP5, CP7, CP8, CP9, CP12, CP15
Quando ler: ao detalhar ou implementar esta fase. Tamanho-alvo: 3 KB.

## Objetivo
O usuário faz tudo pela janela principal, sem terminal: gerencia perfis de voz, edita toda configuração durante o
uso e cuida do banco.

## Escopo
1. **Janela principal** com navegação entre Gravar, Histórico, Perfis e Configurações; a bandeja segue como atalho.
2. **Perfis de voz**: lista de pessoas com amostras de áudio para ouvir e o número de reuniões em que aparecem; criar
   um perfil, inclusive de um trecho de áudio escolhido (`embed_range`); renomear; apagar, perguntando se o nome fica
   nas reuniões antigas; mesclar perfis da mesma pessoa; separar um perfil que juntou duas vozes; a ficha da pessoa;
   a caixa de "vozes desconhecidas" de todas as reuniões, agrupadas por similaridade (U-007).
3. **Configurações totais (CP8)**: toda chave com a marca "aplica agora" ou "na próxima gravação"; editor de atalhos
   que captura as teclas; presets por plataforma, editáveis; glossário fixo; tamanho do banco com projeção e backup
   com progresso (U-010); mover o banco; restaurar padrão.
4. **Assistente de primeiro uso completo** (U-013): o que falta além das chaves, do token e do teste do microfone,
   como o app de reunião preferido.
5. **Tema escolhido à mão** (claro, escuro ou o do sistema; U-014).

## Fora de escopo
- O aviso de reunião sem gravação (já entregue na F5.7).
- O executável (já entregue na F5.8).
- Linux e macOS (F7).

## Aceite
G6: uma semana de uso diário sem terminal; toda configuração do CP8 editável durante o uso.

## Perguntas
A levantar no detalhamento. Antes desta fase, decidir a tradução da interface (`specs/roadmap.md`).
