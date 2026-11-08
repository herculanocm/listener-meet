"""F5.20 L1 (C1, C9): a v10 (as sugestões ao vivo) e o repositório. A v10 sobre um banco v9, a volta e a subida de novo; o
objetivo da reunião ao criar e ao trocar, com `revisions`; a avaliação (nota e motivo validados, o motivo só no "Ruim", o
`rated_at` pela nota ou pelo "Usei", o desfazer); as listas na ordem de `rated_at`; as opções por sugestão; a janela pelo
tempo; a cascata da exclusão."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from listener.model import Evidence, SuggestStatus
from listener.store import Store
from listener.store import core as store_core
from listener.store import downgrade as down
from listener.store.captions import CaptionInsert
from listener.store.schema import MAIN_MIGRATIONS, MAIN_SCHEMA_VERSION
from listener.store.suggestions import OptionInsert, SuggestionInsert


def _tables(folder: Path) -> set[str]:
    with sqlite3.connect(folder / "listener.db") as c:
        return {str(r[0]) for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _meeting_columns(folder: Path) -> set[str]:
    with sqlite3.connect(folder / "listener.db") as c:
        return {str(r[1]) for r in c.execute("PRAGMA table_info(meetings)")}


def suggestion(store: Store, mid: int, t_ms: int = 60_000, *, status: SuggestStatus = SuggestStatus.OK, anchored: bool = False,
               t_until_ms: int = 50_000, options: int = 2) -> tuple[int, list[int]]:
    sid = store.suggestions.insert(SuggestionInsert(mid, t_ms, "fala", None, None, 0, 3, t_until_ms, anchored, 100, 80, "fio", "{}",
                                                    "{}", "fake", "m", "suggest/1.0", None, 0.001, False, 900, 2000, status, None))
    ids = store.suggestions.insert_options(sid, [OptionInsert(i, "Perguntar", f"opção {i}", "[]", Evidence.NONE) for i in range(options)])
    return sid, ids


def test_v10_on_a_v9_database_the_way_back_and_up_again(tmp_path, monkeypatch):
    monkeypatch.setattr(store_core, "MAIN_MIGRATIONS", MAIN_MIGRATIONS[:9])
    monkeypatch.setattr(store_core, "MAIN_SCHEMA_VERSION", 9)
    with Store(tmp_path) as old:
        assert old.schema_version() == 9
    assert "suggestions" not in _tables(tmp_path) and "goal" not in _meeting_columns(tmp_path)
    monkeypatch.undo()
    with Store(tmp_path) as s:
        assert s.schema_version() == MAIN_SCHEMA_VERSION == 11
        mid = s.create_meeting("Orçamento", None, None, None, goal="fechar o corte")
        suggestion(s, mid)
    assert {"suggestions", "suggestion_options"} <= _tables(tmp_path) and "goal" in _meeting_columns(tmp_path)
    assert down.downgrade(tmp_path, 9) == [11, 10]
    assert not {"suggestions", "suggestion_options"} & _tables(tmp_path) and "goal" not in _meeting_columns(tmp_path)
    with sqlite3.connect(tmp_path / "listener.db") as c:
        assert c.execute("SELECT title FROM meetings").fetchall() == [("Orçamento",)]       # a reunião fica, sem o objetivo
    with Store(tmp_path) as again:
        assert again.schema_version() == 11 and again.get_meeting(mid)["goal"] is None
        assert again.suggestions.list_for_meeting(mid) == []


def test_the_goal_at_creation_and_when_it_changes(store):
    mid = store.create_meeting("Orçamento", None, None, None, goal="  fechar o corte  ")
    assert store.get_meeting(mid)["goal"] == "fechar o corte"
    assert store.get_meeting(store.create_meeting("Outra", None, None, None, goal="   "))["goal"] is None
    assert store.meetings.set_goal(mid, "aprovar a exceção") and not store.meetings.set_goal(mid, " aprovar a exceção ")
    assert store.meetings.set_goal(mid, "") and store.get_meeting(mid)["goal"] is None and not store.meetings.set_goal(999, "x")
    revs = store._q("SELECT before, after FROM revisions WHERE entity = 'meeting' AND entity_id = ? AND field = 'goal' ORDER BY id", (mid,))
    assert [tuple(r) for r in revs] == [("fechar o corte", "aprovar a exceção"), ("aprovar a exceção", None)]


def test_rating_validates_keeps_the_reason_only_on_bad_and_stamps_rated_at(store):
    mid = store.create_meeting("Orçamento", None, None, None)
    _sid, (a, b) = suggestion(store, mid)
    with pytest.raises(ValueError, match="avaliação inválida"):
        store.suggestions.set_rating(a, 2)
    with pytest.raises(ValueError, match="motivo inválido"):
        store.suggestions.set_rating(a, 0, "chata")
    assert store.suggestions.set_rating(a, 0, "longa demais")
    row = store.suggestions.get_option(a)
    assert (row["rating"], row["reason"]) == (0, "longa demais") and row["rated_at"]
    assert store.suggestions.set_rating(a, 1, "longa demais") and store.suggestions.get_option(a)["reason"] is None   # só no "Ruim"
    assert store.suggestions.set_rating(a, None) and store.suggestions.get_option(a)["rated_at"] is None              # desfeita
    assert store.suggestions.set_used(b, True) and store.suggestions.get_option(b)["rated_at"]
    assert store.suggestions.set_rating(b, None) and store.suggestions.get_option(b)["rated_at"]                       # usada segue
    assert store.suggestions.set_used(b, False) and store.suggestions.get_option(b)["rated_at"] is None
    assert not store.suggestions.set_rating(999, 1) and store.suggestions.count_rated() == 0


def test_the_rated_lists_go_newest_first_and_by_meeting(store):
    one, two = store.create_meeting("Um", None, None, None), store.create_meeting("Dois", None, None, None)
    _s1, (a, b) = suggestion(store, one)
    _s2, (c, _d) = suggestion(store, two)
    for option_id, when in ((a, "01"), (c, "02"), (b, "03")):
        store.suggestions.set_rating(option_id, 1)
        store._x("UPDATE suggestion_options SET rated_at = ? WHERE id = ?", (f"2026-11-08T10:00:{when}.000-03:00", option_id))
    assert [r["id"] for r in store.suggestions.rated_for_meeting(one, 10)] == [b, a]
    assert [r["id"] for r in store.suggestions.rated_for_meeting(one, 1)] == [b]
    assert [r["id"] for r in store.suggestions.list_rated(10)] == [b, c, a] and store.suggestions.count_rated() == 3


def test_last_ok_anchored_and_the_options_by_suggestion(store):
    mid = store.create_meeting("Orçamento", None, None, None)
    assert store.suggestions.last_ok(mid) is None and store.suggestions.last_anchored_t_ms(mid) is None
    first, _ = suggestion(store, mid, 60_000, anchored=True)
    suggestion(store, mid, 120_000, status=SuggestStatus.ERROR, anchored=True)              # a falha não deixa fio
    third, _ = suggestion(store, mid, 180_000, options=3)
    suggestion(store, mid, 240_000, status=SuggestStatus.CANCELLED)                         # a mais nova cancelada: sem fio
    assert store.suggestions.last_ok(mid)["id"] == third and store.suggestions.last_anchored_t_ms(mid) == 60_000
    grouped = store.suggestions.options_for_meeting(mid)
    assert [len(grouped[s["id"]]) for s in store.suggestions.list_for_meeting(mid)] == [2, 2, 3, 2] and first in grouped
    assert [o["idx"] for o in store.suggestions.options_for(third)] == [0, 1, 2]


def test_the_caption_window_by_time(store):
    mid = store.create_meeting("Orçamento", None, None, None)
    for t0 in (30_000, 0, 60_000):
        store.captions.insert(CaptionInsert(mid, "R", t0, t0 + 3000, f"fala {t0}"))
    assert [r["t_start_ms"] for r in store.captions.list_from(mid, 30_000)] == [30_000, 60_000]


def test_deleting_the_meeting_takes_the_suggestions_and_their_ratings(store):
    mid, kept = store.create_meeting("Orçamento", None, None, None), store.create_meeting("Outra", None, None, None)
    sid, (a, _b) = suggestion(store, mid)
    store.suggestions.set_rating(a, 0, "genérica")
    other, _ = suggestion(store, kept)
    store.delete_meeting(mid)
    count: Any = store._q("SELECT COUNT(*) FROM suggestion_options WHERE suggestion_id = ?", (sid,))[0][0]
    assert store.suggestions.get(sid) is None and count == 0 and store.suggestions.get(other) is not None
