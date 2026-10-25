# listener-meet

Gravador pessoal de reuniões para Windows (Teams, Meet, Zoom ou qualquer app): identifica quem fala por
perfil de voz, transcreve, gera legenda, resumo e ata com uma LLM, e guarda tudo numa base consultável.

Este repositório segue **Spec-Driven Development**: nenhum código existe sem uma spec aprovada pelo dono.

- Pessoas e LLMs: comecem por [AGENTS.md](AGENTS.md).
- O que o sistema é: [ARCHITECTURE.md](ARCHITECTURE.md).
- O que o dono quer: [specs/00-handoff-v4.md](specs/00-handoff-v4.md) (documento mestre).

## Rodar (Windows, fase F1)

```powershell
# venv Python 3.12 com as dependências (ver pyproject.toml; no Windows, também o grupo win32)
C:\caminho\do\venv\Scripts\python.exe -m listener
```

Na primeira vez abre o assistente (seus dados, pasta do banco, teste do microfone). Depois: ícone na bandeja,
`Ctrl+Alt+Shift+R` abre o warm-up e `Ctrl+Alt+Shift+H` o Histórico (busca, ações abertas e perguntas sobre todas
as reuniões, F5), Gravar mostra o widget flutuante (pausar, marcador com nota, mudo global,
parar, cancelar). O log fica no arquivo escolhido na primeira inicialização (nível editável nas Configurações). Flags de desenvolvimento para o gate G1: `--selftest` (grava alguns segundos sem interface)
e `--check <pasta do banco>` (relatório de continuidade de cada gravação; mais o ao vivo, F4, e o índice da busca, F5).

Testes (qualquer sistema, com plataforma simulada): `python -m pytest -q`; lint e tipos: `ruff check .` e
`mypy listener`. No Linux a captura precisa de `libportaudio2`. O áudio fica em um arquivo por mês
(`listener-audio-AAAA-MM.db`) ao lado de `listener.db`. Licença MIT.
