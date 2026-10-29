"""U-035 (F5.4 L4) e F5.5: a voz sem pessoa como "Voz 1", "Voz 2"… por reunião, na ordem da primeira fala, no lugar do
rótulo interno do pyannote ("Voz SPEAKER_03"), em todo lugar onde o usuário lê: a tela (transcrição, linha do tempo e
revisão), a entrada da LLM (ata, transcrição revisada, título e chat da reunião, `llm_steps.build_context`), a exportação,
os trechos da busca e o chat sobre todas as reuniões (`review.transcript_rows`). O rótulo gravado (`segments.cluster`,
`speaker_clusters.cluster`), os eventos, o log e a identidade por contexto (`Context.identify_lines`, A-14) seguem com o
rótulo interno. Sem Qt (A-12).

A numeração é por voz: os clusters do job vigente, menos o canal L do usuário (como a revisão); a mescla da revisão ("é a
mesma que…") leva o número do alvo; a ordem é a da primeira fala de cada voz (a do grupo, na mescla) e a voz ainda sem
fala (o Whisper roda) vem depois, pelo id. Confirmar uma voz não renumera as outras: o número segue a voz, nomeada ou não
(a revisão mostra o número de todas; a transcrição, só das que seguem sem pessoa). Sem job vigente, nenhuma (A-05)."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..store.segments import resolve_person
from .runner import USER_CLUSTER

if TYPE_CHECKING:
    from ..store import Store
    from ..store.segments import ClusterRow

VOICE_PREFIX = "Voz "            # a voz sem pessoa: "Voz 2" pela numeração, "Voz <rótulo>" fora dela
UNKNOWN = "Desconhecido"         # a fala sem cluster nem rótulo


def voice_title(number: int) -> str:
    return f"{VOICE_PREFIX}{number}"


def voice_label(label: object) -> str:
    """O nome interno da voz sem pessoa, "Voz <rótulo>" (o da revisão e da assinatura v1, A-13); sem rótulo, "Desconhecido"."""
    return f"{VOICE_PREFIX}{label}" if label else UNKNOWN


@dataclass(frozen=True, slots=True)
class VoiceNames:
    """Os números das vozes de uma reunião, por cluster: casar sempre pelo cluster, nunca pelo rótulo (A-12, A-15)."""
    by_cluster: Mapping[int, int] = field(default_factory=dict)

    def title(self, cluster_id: int | None, fallback: str) -> str:
        """"Voz 2" da voz `cluster_id`; sem número (o L do usuário, cluster de outro job), o `fallback`."""
        number = self.by_cluster.get(int(cluster_id)) if cluster_id is not None else None
        return voice_title(number) if number is not None else fallback


NO_NAMES = VoiceNames()


def speaker_of(s: Mapping[str, Any], clusters: dict[int, ClusterRow], people: Mapping[int, Mapping[str, Any]],
               names: VoiceNames = NO_NAMES) -> tuple[Mapping[str, Any] | None, str]:
    """(pessoa, nome) de uma fala (A-02, A-15): a pessoa do segmento, senão a do cluster (na mescla, a do alvo, se decidida);
    sem pessoa, "Voz N" pelo número da voz em `names` (a mesclada leva o do alvo); fora da numeração (o L do usuário sem
    pessoa, cluster de outro job, fala sem cluster), "Voz <rótulo>"; sem rótulo, "Desconhecido". Pura: sem banco."""
    pid = s.get("person_id")
    cid = s.get("cluster_id")
    if pid is None and cid is not None and int(cid) in clusters:
        pid = resolve_person(clusters[int(cid)], clusters)
    person = people.get(int(pid)) if pid is not None else None
    if person is not None:
        return person, str(person["name"])
    return None, names.title(cid, voice_label(s.get("cluster")))


def _root(cluster_id: int, by_id: Mapping[int, Mapping[str, Any]]) -> int:
    """O cluster do fim da cadeia de mesclas (`merged_into`), com guarda contra ciclo; fora da reunião, para no último."""
    seen = {cluster_id}
    current = cluster_id
    while True:
        nxt = by_id[current].get("merged_into")
        if nxt is None or int(nxt) not in by_id or int(nxt) in seen:
            return current
        current = int(nxt)
        seen.add(current)


def first_speech(rows: Iterable[Mapping[str, Any]], key: str = "t_start_ms") -> dict[int, int]:
    """A primeira fala (ms) de cada cluster: das falas já lidas (`t_start_ms`) ou de `segments.speakers()` (`first`)."""
    first: dict[int, int] = {}
    for r in rows:
        cid = r.get("cluster_id")
        if cid is not None:
            t = int(r[key])
            first[int(cid)] = min(first.get(int(cid), t), t)
    return first


def number_voices(clusters: Iterable[Mapping[str, Any]], first_ms: Mapping[int, int]) -> VoiceNames:
    """Os números a partir dos clusters (id, canal, rótulo, mescla) e da primeira fala de cada um (ms; sem fala, ausente)."""
    by_id = {int(c["id"]): c for c in clusters if not (str(c["channel"]) == "L" and str(c["cluster"]) == USER_CLUSTER)}
    roots = {cid: _root(cid, by_id) for cid in by_id}
    start: dict[int, tuple[bool, int, int]] = {}                     # o grupo: (sem fala, primeira fala, menor id)
    for cid, root in roots.items():
        key = (cid not in first_ms, first_ms.get(cid, 0), cid)
        start[root] = min(start.get(root, key), key)
    order = sorted(start, key=start.__getitem__)
    number = {root: i + 1 for i, root in enumerate(order)}
    return VoiceNames({cid: number[root] for cid, root in roots.items()})


def numbering(clusters: Iterable[Mapping[str, Any]], job_id: int | None, rows: Iterable[Mapping[str, Any]],
              key: str = "t_start_ms") -> VoiceNames:
    """Os números das vozes do job `job_id` pelos clusters dele e pelas falas já lidas (A-17: nenhuma consulta a mais); sem
    job vigente, nenhum (A-05: a leitura lê então os clusters de todos os jobs, e o número de um não vale para o outro)."""
    return number_voices(clusters, first_speech(rows, key)) if job_id is not None else NO_NAMES


def voice_names(store: Store, meeting_id: int) -> VoiceNames:
    """Os números das vozes do job vigente da reunião (duas leituras curtas, sem ler as falas): a revisão."""
    job_id = store.segments.current_job_id(meeting_id)
    if job_id is None:
        return NO_NAMES
    first = first_speech(store.segments.speakers(meeting_id, job_id), "first")
    return number_voices(store.segments.list_clusters(meeting_id, job_id), first)


__all__ = ["NO_NAMES", "UNKNOWN", "VOICE_PREFIX", "VoiceNames", "first_speech", "number_voices", "numbering", "speaker_of",
           "voice_label", "voice_names", "voice_title"]
