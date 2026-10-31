"""F5.10 L2 (Dados): a v9 (`chat_messages.trace_json`) com volta. Up, down e up com os ids intactos; o app v8 abre o banco
depois da volta (o código da v8 simulado pela lista de migrações até a 8); a volta recusa o que não tem volta e o banco de
um app mais novo; uma falha no meio desfaz tudo; a linha de comando `--downgrade`."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from listener.__main__ import main
from listener.model import ChatRole
from listener.store import Store
from listener.store import core as store_core
from listener.store import downgrade as down
from listener.store.schema import MAIN_MIGRATIONS, MAIN_SCHEMA_VERSION

TRACE = {"rodadas": [{"n": 1, "consultas": [{"sql": "SELECT count(*) FROM reunioes", "params": [], "linhas": 1, "ms": 2.0}]}]}


def _raw(folder: Path) -> sqlite3.Connection:
    return sqlite3.connect(folder / "listener.db")


def _columns(folder: Path) -> set[str]:
    with _raw(folder) as c:
        return {str(r[1]) for r in c.execute("PRAGMA table_info(chat_messages)")}


def _versions(folder: Path) -> tuple[int, int]:
    conn = _raw(folder)
    try:
        return int(conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]), int(
            conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _messages(folder: Path) -> list[tuple[Any, ...]]:
    conn = _raw(folder)
    try:
        rows = conn.execute("SELECT id, meeting_id, thread_id, role, text, citations_json FROM chat_messages ORDER BY id")
        return [tuple(r) for r in rows]
    finally:
        conn.close()


def _seed(folder: Path) -> tuple[list[tuple[Any, ...]], int]:
    with Store(folder) as s:
        mid = s.create_meeting("Orçamento", None, None, None)
        s.chat.add(mid, ChatRole.USER, "qual o prazo?")
        thread = s.chat.create_thread("quantas reuniões?")
        s.chat.add(None, ChatRole.USER, "quantas reuniões?", thread_id=thread)
        answer = s.chat.add(None, ChatRole.ASSISTANT, "Três [R1].", "gemini", "m", [{"alias": "R1", "meeting_id": mid}], 0.001,
                            thread_id=thread, trace=TRACE)
        assert json.loads(s.chat.get(answer)["trace_json"]) == TRACE and s.schema_version() == MAIN_SCHEMA_VERSION == 9
    return _messages(folder), thread


def test_v9_up_down_up_keeps_the_ids_and_the_v8_app_opens_after_the_down(tmp_path, monkeypatch):
    before, thread = _seed(tmp_path)
    assert "trace_json" in _columns(tmp_path) and _versions(tmp_path) == (9, 9)
    assert down.downgrade(tmp_path, 8) == [9]
    assert "trace_json" not in _columns(tmp_path) and _versions(tmp_path) == (8, 8) and _messages(tmp_path) == before
    assert down.downgrade(tmp_path, 8) == []                                           # já está lá: nada a fazer
    monkeypatch.setattr(store_core, "MAIN_MIGRATIONS", MAIN_MIGRATIONS[:8])            # o app da v8
    monkeypatch.setattr(store_core, "MAIN_SCHEMA_VERSION", 8)
    with Store(tmp_path) as old:
        assert old.schema_version() == 8 and [m["text"] for m in old.chat.list_for_thread(thread)] == ["quantas reuniões?", "Três [R1]."]
        old._x("INSERT INTO chat_messages(meeting_id, thread_id, role, text, created_at) VALUES (NULL, ?, 'user', 'e agora?', 'x')",
               (thread,))
    with Store.open_worker(tmp_path) as worker:
        assert worker.schema_version() == 8
    monkeypatch.undo()
    assert _versions(tmp_path) == (8, 8)
    with Store(tmp_path) as again:                                                      # o app da v9 sobe de novo
        assert again.schema_version() == 9 and "trace_json" in _columns(tmp_path)
        rows = again.chat.list_for_thread(thread)
        assert [(m["text"], m["trace_json"]) for m in rows] == [("quantas reuniões?", None), ("Três [R1].", None), ("e agora?", None)]
        assert again.chat.get(again.chat.add(None, ChatRole.ASSISTANT, "ok", thread_id=thread, trace=TRACE))["trace_json"] is not None
    assert _messages(tmp_path)[:len(before)] == before and _versions(tmp_path) == (9, 9)


def test_the_down_refuses_what_has_no_way_back_and_a_newer_database(tmp_path):
    _seed(tmp_path)
    with pytest.raises(ValueError, match="a versão 8 não tem volta: o menor destino possível é 8"):
        down.downgrade(tmp_path, 7)
    assert _versions(tmp_path) == (9, 9) and "trace_json" in _columns(tmp_path)        # nada mudou
    with pytest.raises(FileNotFoundError):
        down.downgrade(tmp_path / "nada", 8)
    with _raw(tmp_path) as c:
        c.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (99, 'x')")
    with pytest.raises(ValueError, match="versão 99"):
        down.downgrade(tmp_path, 8)


def test_a_failure_in_the_middle_of_the_down_undoes_everything(tmp_path, monkeypatch):
    before, _thread = _seed(tmp_path)

    def boom(conn: sqlite3.Connection) -> None:
        down._down_v9(conn)                                                             # a coluna já saiu...
        raise RuntimeError("falha no meio da volta")                                    # ...e cai antes da linha da versão

    monkeypatch.setitem(down.MAIN_DOWNS, 9, boom)
    with pytest.raises(RuntimeError, match="falha no meio"):
        down.downgrade(tmp_path, 8)
    assert _versions(tmp_path) == (9, 9) and "trace_json" in _columns(tmp_path) and _messages(tmp_path) == before


def test_the_command_line_down(tmp_path, capsys):
    _seed(tmp_path)
    assert main(["--downgrade", str(tmp_path), "8"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"downgrade": "ok", "desfeitas": [9]}
    assert main(["--downgrade", str(tmp_path), "oito"]) == 1
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["downgrade"] == "erro"
    assert _versions(tmp_path) == (8, 8)
