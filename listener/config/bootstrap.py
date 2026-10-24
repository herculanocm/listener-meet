"""Ponteiro para a pasta do banco: o único arquivo que o app mantém fora do SQLite.

Sem ele o app não sabe onde o banco está (o usuário escolhe a pasta na primeira inicialização, D-026/D-029).
"""
from __future__ import annotations

import json
from pathlib import Path

POINTER_NAME = "db-location.json"


def pointer_path(config_dir: Path) -> Path:
    return Path(config_dir) / POINTER_NAME


def read_db_dir(config_dir: Path) -> Path | None:
    p = pointer_path(config_dir)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        d = data.get("db_dir")
        return Path(d) if d else None
    except (FileNotFoundError, ValueError, OSError):
        return None


def write_db_dir(config_dir: Path, db_dir: Path) -> Path:
    p = pointer_path(config_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"db_dir": str(db_dir)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
