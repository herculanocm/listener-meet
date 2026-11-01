"""A ficha da pessoa na revisão de falantes (U-041, F5.2), sem Qt, fora do `pipeline/review.py`, que passava de 400 linhas
(F5.11, A-35: o P-03 cresceria o arquivo): criar a pessoa com todos os campos e confirmar a voz, editar a ficha (o cargo do
dia, D-016) e apagar a pessoa (LGPD, CP3). O log segue na categoria da revisão (`listener.pipeline.review`), como antes."""
from __future__ import annotations

from ..log import get_logger
from ..store import Store
from .review import confirm

log = get_logger("pipeline.review")


def create_person(store: Store, cluster_id: int, name: str, role: str = "", company: str = "", email: str = "",
                  author: str = "usuario") -> int:
    """U-041 (A-11): a ficha da revisão cria a pessoa com todos os campos e confirma a voz. Nunca reaproveita pelo nome: o
    homônimo a interface oferece antes ("é ela?"), e um "não" quer dizer outra pessoa com o mesmo nome."""
    name = name.strip()
    if not name:
        raise ValueError("informe o nome")
    pid = store.people.create_person(name, email=email.strip(), role=role.strip(), company=company.strip())
    log.info("pessoa %s criada pela ficha da revisão (voz %s; cargo %s, empresa %s, e-mail %s)", pid, cluster_id,
             _filled(role), _filled(company), _filled(email))
    return confirm(store, cluster_id, pid, author)


def edit_person(store: Store, person_id: int, meeting_id: int, name: str, role: str = "", company: str = "",
                email: str = "") -> list[int]:
    """U-041 (A-09, A-12): muda a ficha. Um cargo novo vale para as falas da pessoa nesta reunião e congela o antigo nas
    outras (o cargo do dia, D-016). Devolve as reuniões a reindexar: todas as da pessoa quando o nome ou o cargo mudam (o
    falante entra no texto dos trechos da busca), senão nenhuma."""
    person = store.people.get_person(person_id)
    if person is None:
        raise ValueError(f"pessoa {person_id} não existe")
    name, role = name.strip(), role.strip()
    if not name:
        raise ValueError("informe o nome")
    old_role = str(person.get("role") or "")
    with store.transaction():
        if role != old_role:
            frozen = store.segments.freeze_role(person_id, old_role, meeting_id)
            here = store.segments.set_role_in_meeting(meeting_id, person_id, role)
            log.info("pessoa %s: cargo mudou na reunião %s (%d fala(s) aqui; %d fala(s) de outras reuniões guardam o antigo)",
                     person_id, meeting_id, here, frozen)
        store.people.update_person(person_id, name, email.strip(), role, company.strip())
    renamed = name != str(person["name"])
    reindex = store.people.meetings_of(person_id) if renamed or role != old_role else []
    log.info("pessoa %s editada pela revisão da reunião %s (nome %s, cargo %s); %d reunião(ões) para reindexar", person_id,
             meeting_id, "mudou" if renamed else "igual", "mudou" if role != old_role else "igual", len(reindex))
    return reindex


def forget_person(store: Store, person_id: int) -> list[int]:
    """U-041 (A-06, A-08, LGPD): apaga a pessoa (`PeopleRepo.forget`) e limpa o vínculo órfão que outro processo tenha
    gravado; devolve as reuniões afetadas, para reindexar. Atas, resumos e respostas já gerados ficam como estão (CP3)."""
    meetings = store.people.forget(person_id)
    orphans = store.people.scrub_orphans()
    log.info("pessoa %s apagada pela revisão: %d reunião(ões) afetada(s), %d vínculo(s) órfão(s) limpo(s)", person_id,
             len(meetings), orphans)
    return meetings


def _filled(value: str) -> str:
    """Só se o campo veio preenchido: o valor nunca vai para o log (D-053, LGPD)."""
    return "sim" if value.strip() else "não"


__all__ = ["create_person", "edit_person", "forget_person"]
