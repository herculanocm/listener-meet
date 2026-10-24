"""listener-meet — gravador de reuniões com identificação por perfil de voz (código aberto).

Documento mestre: specs/00-handoff-v4.md. Regras de trabalho: AGENTS.md.
Pacotes: platform/ (tudo que depende do sistema operacional), capture/ (gravação em dois canais),
store/ (SQLite), ui/ (PySide6), config/ (ponteiro do banco e padrões), pipeline/ e llm/ (fases seguintes).
"""
__version__ = "0.3.0"
