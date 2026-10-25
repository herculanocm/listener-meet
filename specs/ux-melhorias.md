# Melhorias de UX (U-nnn), CP12

Quando ler: ao escrever a spec de uma fase (T2). U-001 a U-015 aprovadas em 24/10 (D-017); U-016 e U-017 aprovadas e
implementadas na F1.1 (D-035); U-024 e U-026 a U-029 implementadas na F5 (D-047), U-025 e U-008 na F5.1.
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
| U-008 | Linha do tempo da reunião com faixas por falante e marcadores; clique toca | Histórico | aprovada (F5.1) |
| U-009 | Custo e latência por provedor: testar conexão mostra latência; tokens e custo por reunião e por mês | Configurações | aprovada (F3) |
| U-010 | Indicador de tamanho do banco com projeção; backup com progresso | Configurações | aprovada (F6) |
| U-011 | Exportar ata e transcrição (.md, .docx, área de transferência) sob demanda (D-012) | Histórico | implementada (F5: .md e área de transferência; .docx na F5.1) |
| U-012 | Notificação de reunião detectada com "Gravar agora" (último preset) e "ignorar por 1 h" (D-010) | Bandeja | aprovada (F6) |
| U-013 | Assistente de primeiro uso: token HF, chave do provedor, teste do mic, app de reunião | Primeiro uso | aprovada (F6; versão mínima na F1) |
| U-014 | Tema claro e escuro seguindo o sistema; DPI alto | Geral | aprovada (F6; básico na F1) |
| U-015 | Preset "sala de reunião": diariza também o L e desliga "L é sempre eu" | Warm-up | aprovada (metadado na F1; lógica na F2) |
| U-016 | Detector de eco e alinhamento por correlação cruzada L×R: aviso no warm-up; deslocamento L/R no `--check` | Warm-up, `--check` | implementada (F1.1) |
| U-017 | Instrução para fixar o ícone da bandeja no Windows 11 (ícones novos vão para o menu oculto) | Primeiro uso | implementada (F1.1) |
| U-021 | Janela "Ao vivo": legendas rolando com nome e cargo e o resumo parcial, com "atualizar agora" | Ao vivo | implementada (F4) |
| U-022 | Clique numa legenda cria um marcador naquele instante com o texto dela | Ao vivo | implementada (F4) |
| U-023 | Possíveis decisões e ações do parcial com "marcar" (marcador que a ata lê) | Ao vivo | implementada (F4) |
| U-024 | Trechos da busca agrupados por reunião, termo destacado e "abrir neste ponto" (abre, rola até a fala e toca) | Histórico | implementada (F5) |
| U-025 | Destaque contínuo: a fala que toca acompanha o áudio na transcrição e na linha do tempo | Reunião aberta | aprovada (F5.1) |
| U-026 | "Ações abertas" de todas as reuniões: dono, prazo, origem, atrasadas em destaque, marcar feita | Histórico | implementada (F5) |
| U-027 | "Citação certa?" sim/não em cada resposta do chat global (mede o G5) | Histórico | implementada (F5) |
| U-028 | Atalho global para abrir o Histórico com o cursor na busca | Atalhos | implementada (F5) |
| U-029 | "Copiar com citação": fala, trecho ou resposta com reunião, data e [mm:ss] | Histórico | implementada (F5) |
