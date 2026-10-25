"""listener-meet — gravador de reuniões com identificação por perfil de voz (código aberto).

Documento mestre: specs/00-handoff-v4.md. Regras de trabalho: AGENTS.md.
Pacotes: platform/ (tudo que depende do sistema operacional), capture/ (gravação em dois canais),
store/ (SQLite), ui/ (PySide6), config/ (ponteiro do banco e padrões), model.py (estados), pipeline/ e llm/ (fases seguintes).
A versão mora só aqui; o `pyproject.toml` lê daqui (`dynamic`).
"""
__version__ = "0.4.0"
