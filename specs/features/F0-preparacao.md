# F0 — Preparação do ambiente e medições

Status: aprovada e executada · Aprovada por Herculano em: 2026-10-24 ("Ok para tudo") · Fase e gate: F0 · G0 · CPs: 10, 13, 14
Quando ler: para saber o que foi medido e com quê. Números: `decisoes.md` (Medições). Máquina: `memory/maquina.md`.
Tamanho-alvo: 1,5 KB.

## Objetivo
Deixar a máquina pronta para a F1 e medir o que decide o desenho: tempo de Whisper e de pyannote por hora de áudio em
CPU e se o sqlite-vec carrega no Windows.

## Escopo e resultado
1. venv Python 3.12 (`py install 3.12`, sem admin); venv 3.14 do protótipo apagado (D-019): feito.
2. Instalar PySide6, whisperx, pyannote.audio 4, sqlite-vec, google-genai, openai, anthropic, proc-tap, sounddevice,
   PyAudioWPatch, soundfile, numpy; versões em `memory/maquina.md`: feito, sem compilar nada.
3. Token do Hugging Face com os termos dos modelos pyannote aceitos (`HF_TOKEN` no ambiente; nunca no chat nem no git):
   pendente (B4).
4. Chave do Gemini: só na F3; nada em arquivo até existir o banco (CP10).
5. Medições com `scripts/medir.py` (único código desta fase, D-022): Whisper e faster-whisper medidos em 5 min de TTS;
   sqlite-vec ok (D-024); alinhamento pt presente; pyannote pendente (B4); repetir com gravação real de 1 h (B5).
6. Melhorias aprovadas: M-001 transcrição pela LLM com áudio (D-020), M-002 embeddings locais (D-021), M-003 timestamps
   do faster-whisper (D-028).

## Aceite (G0)
Tudo instala no 3.12 sem compilar: sim. Tempos anotados com data e versão: Whisper sim, pyannote não. sqlite-vec carrega.
Perguntas P4 a P16: respondidas ("Ok para tudo").
