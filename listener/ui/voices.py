"""U-035 (F5.4 L4): a voz sem pessoa como "Voz 1", "Voz 2"… por reunião, na ordem da primeira fala, no lugar do rótulo
interno do pyannote ("Voz SPEAKER_03"), na transcrição, na linha do tempo e na revisão. Só exibição: as falas gravadas, o
contexto e os rótulos mandados à LLM, a busca e a exportação seguem com o rótulo interno (`pipeline/review.py`).

A numeração é por voz: os clusters do job vigente, menos o canal L do usuário (como a revisão); a mescla da revisão ("é a
mesma que…") leva o número do alvo; a ordem é a da primeira fala de cada voz (a do grupo, na mescla) e a voz ainda sem
fala (o Whisper roda) vem depois, pelo id. Confirmar uma voz não renumera as outras: o número segue a voz, nomeada ou não
(a revisão mostra o número de todas; a transcrição, só das que seguem sem pessoa)."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from ..pipeline.runner import USER_CLUSTER

if TYPE_CHECKING:
    from ..pipeline.review import TranscriptRow
    from ..store import Store

VOICE_PREFIX = "Voz "            # o nome que `review._speaker` dá à fala sem pessoa ("Voz <rótulo do cluster>")


def voice_title(number: int) -> str:
    return f"{VOICE_PREFIX}{number}"


@dataclass(frozen=True, slots=True)
class VoiceNames:
    """Os números das vozes de uma reunião: por cluster (a revisão) e por canal e rótulo (as falas da transcrição)."""
    by_cluster: Mapping[int, int] = field(default_factory=dict)
    by_label: Mapping[tuple[str, str], int] = field(default_factory=dict)

    def title(self, cluster_id: int | None, fallback: str) -> str:
        """"Voz 2" da voz `cluster_id`; sem número (o L do usuário, cluster de outro job), o `fallback`."""
        number = self.by_cluster.get(int(cluster_id)) if cluster_id is not None else None
        return voice_title(number) if number is not None else fallback

    def row_name(self, row: TranscriptRow) -> str:
        """O nome da fala na tela: o da pessoa, ou "Voz N" no lugar do rótulo interno da voz sem pessoa."""
        if row.person_id is not None or not row.name.startswith(VOICE_PREFIX):
            return row.name
        number = self.by_label.get((row.channel, row.name.removeprefix(VOICE_PREFIX)))
        return voice_title(number) if number is not None else row.name

    def rows(self, rows: Iterable[TranscriptRow]) -> list[TranscriptRow]:
        """As falas com o nome da tela (cópias só das que mudam; a ordem e o resto ficam)."""
        out: list[TranscriptRow] = []
        for r in rows:
            name = self.row_name(r)
            out.append(r if name == r.name else replace(r, name=name))
        return out


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
    by_cluster = {cid: number[root] for cid, root in roots.items()}
    by_label = {(str(c["channel"]), str(c["cluster"])): by_cluster[cid] for cid, c in by_id.items()}
    return VoiceNames(by_cluster, by_label)


def voice_names(store: Store, meeting_id: int) -> VoiceNames:
    """Os números das vozes do job vigente da reunião (duas leituras curtas, sem ler as falas)."""
    job_id = store.segments.current_job_id(meeting_id)
    if job_id is None:
        return VoiceNames()
    first: dict[int, int] = {}
    for sp in store.segments.speakers(meeting_id, job_id):
        cid = sp.get("cluster_id")
        if cid is not None:
            first[int(cid)] = min(first.get(int(cid), int(sp["first"])), int(sp["first"]))
    return number_voices(store.segments.list_clusters(meeting_id, job_id), first)


__all__ = ["VOICE_PREFIX", "VoiceNames", "number_voices", "voice_names", "voice_title"]
