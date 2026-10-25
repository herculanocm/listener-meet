# Como o Herculano quer trabalhar (vigente desde 24/10/2026)

- PT-BR, informal e direto. Par sênior que discorda quando for o caso, não espelho. Conclusão primeiro; [fato], [exp] e
  [chute] separados.
- Melhorias sempre como pergunta ("incluir? sim/não" com padrão); ele responde em bloco ("Ok para tudo", "Ok, siga").
- Fechar cada etapa com uma pergunta concreta de autorização para a próxima: a que mais muda o caminho.
- Nenhum código sem autorização por entrega (CP13); commit a cada grande alteração, sem pedir (CP16); "não quero guardar
  lixo": protótipo sem valor é apagado, não arquivado.
- Documentos vivos ficam no Claude Docs (handoffs entre LLMs); a cópia fiel fica em `specs/`.
- Documentação compacta para IA (D-031): ele zera o contexto entre sessões e usa várias LLMs; arquivos grandes e repetidos
  "estouram o limite de leitura". Regras em `specs/README.md`.
- O propósito do app é o Windows: performance e experiência no Windows nativo primeiro; Linux e macOS implementam o que
  der (CP15). Desenvolver no Linux ou no WSL está ok, mas testes e funcionalidades só valem depois de rodar no Windows:
  a LLM confere o ambiente no começo de cada sessão e o lembra do que falta verificar lá (25/10).
- Python moderno e rápido: piso 3.12, sem retrocompatibilidade, recursos novos e performance primeiro (CP17); quando a
  stack liberar uma versão mais nova, ele quer migrar, não esperar.
