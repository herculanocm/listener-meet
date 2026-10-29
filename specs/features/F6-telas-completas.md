# F6 — Telas completas e UX de fábrica

Status: rascunho inicial (aprovação do Herculano antes de codar)
Fase e gate: F6 · G6
Cláusulas pétreas envolvidas: CP6, CP7, CP8, CP9, CP12, CP15
Quando ler: ao detalhar ou implementar esta fase (T2). Tabelas: `dados.md`.

## Escopo
1. Janela principal com Gravar, Histórico, Perfis e Configurações; bandeja completa.
2. Perfis de voz (seção 5 do handoff): lista com amostras e contagem de reuniões; criar de um trecho,
   renomear, apagar (perguntando se mantém o nome nas reuniões antigas), mesclar, separar; ficha da pessoa
   (nome, e-mail, cargo, empresa, descrição); caixa de "vozes desconhecidas" (U-007).
3. Configurações totais (CP8): todas as chaves com o selo "aplica agora" ou "na próxima gravação"; sub-abas
   de provedores; editor de atalhos capturando as teclas; presets; pasta de modelos; tamanho do banco com
   projeção e backup com progresso (U-010); mover o banco; restaurar padrão.
4. Notificação de reunião detectada (U-012, D-010): sessões de áudio do Windows; lista de apps observados
   configurável; "Gravar agora" com o último preset; "ignorar por 1 h"; nunca grava sozinho. Trazido para a F5.7
   (`F5.7-aviso-reuniao-sem-gravar.md`, U-094, rascunho de 29/10); sai daqui quando ela for aprovada.
5. Assistente de primeiro uso completo (U-013): token do Hugging Face, chave do provedor, teste do
   microfone, app de reunião, pasta de modelos.
6. Tema claro e escuro seguindo o sistema, DPI alto (U-014); empacotamento com atalho (PyInstaller só se
   necessário).

## Gate G6
Uma semana de uso diário sem terminal; toda configuração do CP8 editável durante o uso.
