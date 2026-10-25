"""Ao vivo (F4): legenda leve com nome e resumo parcial durante a gravação, num terceiro processo (`python -m listener
--live`). `protocol.py` e `names.py` são puros; `client.py` roda no app (sem Qt e sem ML: regra estática de importações);
`proc.py` e `vad.py` rodam no filho. Este `__init__` não importa nada: o app só puxa o que usa."""
