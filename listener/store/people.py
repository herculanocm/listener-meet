"""Pessoas (`people`): o usuário (`is_me`) e quem aparece nas reuniões. A F2 cria, lista e lê (revisão de falantes); a F5.2
(U-041) edita a ficha e apaga a pessoa (LGPD, esquecimento): `forget` tira o que a identifica de todas as reuniões numa
transação (A-06), `scrub_orphans` limpa o vínculo que um processo gravou com uma pessoa já apagada (A-08) e um id apagado
nunca volta a outra pessoa (`people.max_id` nas configurações, A-07). O áudio, as falas e o que a LLM já gerou ficam (CP3)."""
from __future__ import annotations

import json
from typing import TypedDict, cast

from ..model import ClusterStatus
from .core import Core, _now

MAX_ID_KEY = "people.max_id"         # A-07: o maior id que já existiu (o próximo nunca o reaproveita)
_NAMED = ", ".join(f"'{s}'" for s in (ClusterStatus.AUTO, ClusterStatus.CONFIRMED, ClusterStatus.AMBIGUOUS))
# o esquecimento, em ordem (A-06): (comando com {who}, a coluna da pessoa); {who} vira `col = ?` ou o teste de órfão
_UNLINK: tuple[tuple[str, str], ...] = (
    ("DELETE FROM voice_embeddings WHERE profile_id IN (SELECT id FROM voice_profiles WHERE {who})", "person_id"),
    ("DELETE FROM voice_profiles WHERE {who}", "person_id"),
    ("UPDATE segments SET person_id = NULL, role_at_time = NULL WHERE {who}", "person_id"),
    ((f"UPDATE clusters SET person_id = NULL, similarity = NULL, margin = NULL, decision_source = NULL, status = CASE WHEN "  # noqa: S608
      f"status IN ({_NAMED}) THEN '{ClusterStatus.UNNAMED}' ELSE status END WHERE {{who}}"), "person_id"),   # só constantes do enum
    ("UPDATE clusters SET llm_person_id = NULL, llm_name = NULL, llm_confidence = NULL, llm_evidence_json = NULL WHERE {who}",
     "llm_person_id"),
    ("UPDATE captions SET person_id = NULL, label = NULL, confidence = NULL WHERE {who}", "person_id"),
    ("UPDATE action_items SET owner_person_id = NULL WHERE {who}", "owner_person_id"),
)


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

    def find_all_by_name(self, name: str) -> list[PersonRow]:
        """Os homônimos (U-041: a ficha pergunta "é ela?" antes de criar outra pessoa com o mesmo nome)."""
        rows = self.core.q("SELECT * FROM people WHERE name = ? COLLATE NOCASE ORDER BY id", (name.strip(),))
        return [cast(PersonRow, dict(r)) for r in rows]

    def create_person(self, name: str, email: str = "", role: str = "", company: str = "", notes: str = "") -> int:
        now = _now()
        with self.core.transaction():
            pid = self._next_id()
            self.core.insert("INSERT INTO people(id, name, email, role, company, notes, is_me, created_at, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)", (pid, name.strip(), email, role, company, notes, now, now))
        return pid

    def update_person(self, person_id: int, name: str, email: str = "", role: str = "", company: str = "") -> None:
        self.core.execute("UPDATE people SET name = ?, email = ?, role = ?, company = ?, updated_at = ? WHERE id = ?",
                          (name.strip(), email, role, company, _now(), person_id))

    def get_me(self) -> PersonRow | None:
        rows = self.core.q("SELECT * FROM people WHERE is_me = 1 ORDER BY id LIMIT 1")
        return cast(PersonRow, dict(rows[0])) if rows else None

    def upsert_me(self, name: str, email: str = "", role: str = "", company: str = "") -> int:
        me = self.get_me()
        if me:
            self.core.execute("UPDATE people SET name = ?, email = ?, role = ?, company = ?, updated_at = ? WHERE id = ?",
                              (name, email, role, company, _now(), me["id"]))
            return int(me["id"])
        with self.core.transaction():
            pid = self._next_id()
            self.core.insert("INSERT INTO people(id, name, email, role, company, is_me, created_at, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, 1, ?, ?)", (pid, name, email, role, company, _now(), _now()))
        return pid

    def meetings_of(self, person_id: int) -> list[int]:
        """As reuniões em que a pessoa aparece: falas, vozes (confirmadas ou sugeridas), legendas, ações e esperados."""
        rows = self.core.q(
            "SELECT meeting_id AS m FROM segments WHERE person_id = ? "
            "UNION SELECT meeting_id FROM clusters WHERE person_id = ? OR llm_person_id = ? "
            "UNION SELECT meeting_id FROM captions WHERE person_id = ? "
            "UNION SELECT meeting_id FROM action_items WHERE owner_person_id = ? "
            "UNION SELECT m.id FROM meetings m, json_each(CASE WHEN json_valid(m.expected_people_json) "
            "THEN m.expected_people_json ELSE '[]' END) e WHERE e.value = ? ORDER BY 1",
            (person_id,) * 6)
        return [int(r["m"]) for r in rows]

    def forget(self, person_id: int) -> list[int]:
        """U-041 (A-06, LGPD): apaga a pessoa, os perfis de voz e os vetores, e tira o vínculo dela de falas, vozes,
        sugestões da LLM, legendas, ações, esperados e histórico da ficha, numa transação. As vozes dela voltam a `sem_nome`
        (`ignorado` só perde a pessoa; `merged_into` fica). Devolve as reuniões afetadas (o índice delas é refeito por quem
        chama). Atas, chat, `llm_calls` e eventos (só ids) ficam. "Você" (`is_me`) não se apaga."""
        with self.core.transaction():
            person = self.get_person(person_id)
            if person is None:
                return []
            if person.get("is_me"):
                raise ValueError("você não pode ser apagado: é quem fala no canal do microfone")
            meetings = self.meetings_of(person_id)
            self._remember_max_id()
            for sql, col in _UNLINK:
                self.core.execute(sql.format(who=f"{col} = ?"), (person_id,))
            for mid in meetings:
                self._drop_expected(mid, person_id)
            self.core.execute("DELETE FROM revisions WHERE entity = 'person' AND entity_id = ?", (person_id,))
            self.core.execute("DELETE FROM people WHERE id = ?", (person_id,))
        return meetings

    def scrub_orphans(self) -> int:
        """A-08: um processo que leu a pessoa antes de ela ser apagada (o worker no meio do job, o ao vivo com os perfis em
        memória) pode gravar o vínculo depois; aqui ele some. Chamado ao apagar, no fim dos dois runners e na abertura."""
        with self.core.transaction():                      # `_UNLINK` e as colunas são texto fixo deste módulo
            return sum(self.core.execute(sql.format(who=f"{col} IS NOT NULL AND {col} NOT IN (SELECT id FROM people)"))  # noqa: S608
                       for sql, col in _UNLINK)

    def _drop_expected(self, meeting_id: int, person_id: int) -> None:
        rows = self.core.q("SELECT expected_people_json FROM meetings WHERE id = ?", (meeting_id,))
        try:
            ids = [int(x) for x in json.loads(str(rows[0]["expected_people_json"] or "[]"))] if rows else []
        except (ValueError, TypeError):
            return
        if person_id in ids:
            kept = [x for x in ids if x != person_id]
            self.core.execute("UPDATE meetings SET expected_people_json = ? WHERE id = ?",
                              (json.dumps(kept) if kept else None, meeting_id))

    def _next_id(self) -> int:
        top = int(self.core.q("SELECT COALESCE(MAX(id), 0) AS m FROM people")[0]["m"])
        return max(top, self._kept_max_id()) + 1

    def _kept_max_id(self) -> int:
        rows = self.core.q("SELECT value FROM settings WHERE key = ?", (MAX_ID_KEY,))
        value = str(rows[0]["value"]) if rows else ""
        return int(value) if value.isdigit() else 0

    def _remember_max_id(self) -> None:
        top = max(int(self.core.q("SELECT COALESCE(MAX(id), 0) AS m FROM people")[0]["m"]), self._kept_max_id())
        self.core.execute("INSERT INTO settings(key, value, scope, updated_at) VALUES (?, ?, 'now', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                          (MAX_ID_KEY, str(top), _now()))
