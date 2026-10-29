"""As chamadas do glossário que chegam depois do "Gravar" do warm-up (F3.1 R3-11), religadas à reunião dele. Saiu do
`ui/controller.py` na F5.6 (o controlador não cresce, A-44); o controlador segue exportando o nome."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import Store


class LateGlossaryLink:
    """Chamadas do glossário que voltam depois do "Gravar" de um warm-up (F3.1 R3-11): ficam guardadas até a reunião dele
    existir e então ganham o `meeting_id`; cada warm-up tem o seu (resposta atrasada não cai na reunião seguinte)."""

    def __init__(self, store: Store):
        self.store = store
        self.meeting_id: int | None = None
        self.pending: list[int] = []

    def add(self, call_id: int) -> None:
        if self.meeting_id is None:
            self.pending.append(call_id)
        else:
            self.store.llm_calls.set_meeting(call_id, self.meeting_id)

    def bind(self, meeting_id: int) -> None:
        self.meeting_id = meeting_id
        for call_id in self.pending:
            self.store.llm_calls.set_meeting(call_id, meeting_id)
        self.pending.clear()


__all__ = ["LateGlossaryLink"]
