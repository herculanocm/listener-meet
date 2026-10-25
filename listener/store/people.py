"""Pessoas (`people`): o usuário (`is_me`) e quem aparece nas reuniões. Fichas completas ficam para a F6; a F2 só cria,
lista e lê (revisão de falantes)."""
from __future__ import annotations

from typing import TypedDict, cast

from .core import Core, _now


class PersonRow(TypedDict, total=False):
    id: int
    name: str
    email: str | None
    role: str | None
    company: str | None
    notes: str | None
    is_me: int
    created_at: str
    updated_at: str


class PeopleRepo:
    def __init__(self, core: Core):
        self.core = core

    def list_people(self) -> list[PersonRow]:
        return [cast(PersonRow, dict(r)) for r in self.core.q("SELECT * FROM people ORDER BY is_me DESC, name COLLATE NOCASE")]

    def get_person(self, person_id: int) -> PersonRow | None:
        rows = self.core.q("SELECT * FROM people WHERE id = ?", (person_id,))
        return cast(PersonRow, dict(rows[0])) if rows else None

    def find_by_name(self, name: str) -> PersonRow | None:
        rows = self.core.q("SELECT * FROM people WHERE name = ? COLLATE NOCASE ORDER BY id LIMIT 1", (name.strip(),))
        return cast(PersonRow, dict(rows[0])) if rows else None

    def create_person(self, name: str, email: str = "", role: str = "", company: str = "", notes: str = "") -> int:
        now = _now()
        return self.core.insert("INSERT INTO people(name, email, role, company, notes, is_me, created_at, updated_at) "
                                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)", (name.strip(), email, role, company, notes, now, now))

    def get_me(self) -> PersonRow | None:
        rows = self.core.q("SELECT * FROM people WHERE is_me = 1 ORDER BY id LIMIT 1")
        return cast(PersonRow, dict(rows[0])) if rows else None

    def upsert_me(self, name: str, email: str = "", role: str = "", company: str = "") -> int:
        me = self.get_me()
        if me:
            self.core.execute("UPDATE people SET name = ?, email = ?, role = ?, company = ?, updated_at = ? WHERE id = ?",
                              (name, email, role, company, _now(), me["id"]))
            return int(me["id"])
        return self.core.insert("INSERT INTO people(name, email, role, company, is_me, created_at, updated_at) "
                                "VALUES (?, ?, ?, ?, 1, ?, ?)", (name, email, role, company, _now(), _now()))
