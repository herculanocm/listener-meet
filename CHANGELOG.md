# Changelog

O que cada versão entregou, da mais nova para a mais antiga. A versão mora em `listener/__init__.py`: a minor sobe a
cada feature e a patch a cada correção (D-081). O contrato de cada feature está em `specs/features/`.

## 0.14.0 (2026-11-08)
- F5.21: a tela nas sugestões ao vivo. O print da janela da reunião (ou de um monitor) vai junto com as legendas, a
  pedido; a miniatura aparece no chat da bandeja de legendas e na aba "Sugestões" da reunião.

## 0.13.0 (2026-11-08)
- F5.20: sugestões ao vivo do que falar, com avaliação por opção ("Boa", "Ruim", "Usei"), o modo "Me situar", o
  objetivo da reunião e o memorando do que o usuário prefere.

## 0.12.0 (2026-11-07)
- F5.19: "Salvar áudio…" (MP3, FLAC ou WAV) e "Salvar vídeo com legendas…" (MP4) na reunião aberta.

## 0.11.0 (2026-11-07)
- F5.18: performance. O processamento pesado cede a CPU em segundos durante uma gravação; a diarização roda o tronco
  do modelo de voz uma vez por trecho; o eco do microfone sai da transcrição; a busca e a fila ficaram mais leves.
- Vigia de travamentos na suíte de testes (`tests/hang_guard.py`).

## 0.10.0 (2026-11-06)
- F5.17: bandeja de legendas, o ao vivo como chat numa janela própria, fora da captura de tela.

## 0.9.0 (2026-11-05)
- F5.16: sem o token do Hugging Face, a reunião sai transcrita, sem a identificação de quem falou.
- Confirmações destrutivas abrem com o "Não" no Enter; a pasta do banco saiu do log.

## 0.8.0 (2026-11-04)
- F5.15: referências de voz, várias por pessoa, para reconhecer a mesma voz em condições diferentes.

## 0.7.1 (2026-11-03)
- O pedido longo do Claude Haiku passa a ser cobrado por chamada, e não pela soma das tentativas.

## 0.7.0 (2026-11-03)
- F5.14: o Claude como provedor de LLM em todas as tarefas.

## 0.6.0 (2026-11-02)
- F5.13: barra de tarefas. Instância única, o ícone fixado abre o Histórico, e o Jump List leva a Gravar, Histórico,
  Fila e Configurações.

## 0.5.0 (2026-11-02)
A versão passou a acompanhar as entregas e a aparecer no Sobre e no nome do zip. Ela reúne o que entrou desde a 0.4.0:
- F2: processamento pós-reunião (diarização, perfis de voz, transcrição) e a revisão de falantes.
- F3: camada de LLM agnóstica (título, pré-resumo, ata com evidência, resumo executivo, transcrição revisada).
- F4: legenda ao vivo e resumo parcial.
- F5: Histórico, busca híbrida e chat com citações.
- F5.1: busca local sem custo, linha do tempo por falante e exportação em .docx.
- F5.2: ficha da pessoa, ata depois das vozes identificadas, dispositivos que chegam com o app aberto.
- F5.3: idioma por reunião, fila de processamento, avisos do próprio app, troca de dispositivo durante a gravação.
- F5.4: sistema visual nos temas claro e escuro, com ícones próprios.
- F5.5: vozes numeradas ("Voz 2") em toda saída.
- F5.6: ata, ações e resumo só a pedido; prompts editáveis; excluir reunião.
- F5.7: aviso de reunião em curso sem gravação.
- F5.8: executável do Windows (PyInstaller em pasta).
- F5.9: canais L e R que se recuperam sozinhos; o som segue o aparelho padrão do Windows.
- F5.10: "perguntar à base", um agente com SQL somente leitura e busca por sentido.
- F5.11: revisão de vozes, reunião aberta e linguagem da interface revistas.
- F5.12: o Sobre e o convite para apoiar o projeto.

## 0.4.0 (2026-10-24)
- F1 revisada: um arquivo de áudio por mês, reamostragem com estado, conexão SQLite por thread.

## 0.3.0 (2026-10-24)
- F1: captura em dois canais, SQLite, "Antes de gravar", widget flutuante, atalhos globais e assistente de primeiro
  uso.
