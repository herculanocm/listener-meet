"""Revisão mínima de falantes (F2 item 13, U-006) e leitura (item 14), sem Qt: o que a tela mostra e o que cada ação grava.

Cada confirmação: `clusters.status = confirmado` com a pessoa, `segments.person_id` e `role_at_time` do cluster, um vetor em
`voice_embeddings` (`source = confirmacao`) com centroide recalculado e autocalibração (Welford com a similaridade medida
antes), `revisions` e um evento `PROFILE_CONFIRM`. Mesclar guarda `clusters.merged_into` (segmentos mantêm o `cluster_id`,
D-F2-9). "Perfil de um trecho" nesta fase é o centroide do cluster já embedado pelo job (P6). O trecho de 5 s para ouvir
vem dos segmentos ou, enquanto o Whisper ainda roda, dos turnos guardados no artefato da costura.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..log import get_logger
from ..model import ClusterStatus, DecisionSource, EventType, MeetingStatus
from ..store import Store
from ..store.profiles import normalize
from ..store.segments import ClusterRow, resolve_person
from .runner import STITCH_ARTIFACT, USER_CLUSTER
from .types import unpack_artifact

log = get_logger("pipeline.review")

CLIP_S = 5.0
PLAY_LEAD_MS = 150          # R-14: tocar a partir da primeira palavra menos 150 ms


@dataclass(frozen=True, slots=True)
class ReviewItem:
    cluster_id: int
    label: str
    channel: str
    status: ClusterStatus
    speech_ms: int
    person_id: int | None            # sugestão ou confirmação
    person_name: str
    similarity: float | None
    margin: float | None
    merged_into: int | None
    has_centroid: bool
    llm_person_id: int | None = None        # F3 item 5: sugestão por contexto (só em vozes pendentes)
    llm_name: str | None = None
    llm_confidence: float | None = None
    llm_evidence: tuple[tuple[int, str, int], ...] = ()   # (segment_id, citação, t_start_ms)
    decision_source: str | None = None

    @property
    def pending(self) -> bool:
        return self.status in (ClusterStatus.AMBIGUOUS, ClusterStatus.UNNAMED)

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_name) and self.pending

    def llm_text(self) -> str:
        """"LLM: Rodrigo (0,8) 'valeu, Rodrigo' aos 12:03" para o card da revisão (U-006)."""
        if not self.has_llm:
            return ""
        conf = f" ({self.llm_confidence:.2f})".replace(".", ",") if self.llm_confidence is not None else ""
        quote = ""
        if self.llm_evidence:
            _sid, text, t_ms = self.llm_evidence[0]
            s = max(0, t_ms // 1000)
            quote = f" \u201c{text}\u201d aos {s // 60:02d}:{s % 60:02d}"
        return f"LLM: {self.llm_name}{conf}{quote}"

    @property
    def minutes_text(self) -> str:
        return f"{self.speech_ms / 60000:.1f} min".replace(".", ",")

    @property
    def title(self) -> str:
        return f"Voz {self.label}" if self.channel == "R" else f"Voz {self.label} (canal {self.channel})"

    def summary(self) -> str:
        if self.merged_into is not None:
            return f"mesclada com a voz #{self.merged_into}"
        if self.status == ClusterStatus.IGNORED:
            return "ignorada"
        sim = (f" (similaridade {self.similarity:.2f}" + (f", margem {self.margin:.2f})" if self.margin is not None else ")")
               ).replace(".", ",") if self.similarity is not None else ""
        llm = f" · {self.llm_text()}" if self.has_llm else ""
        match self.status:
            case ClusterStatus.CONFIRMED:
                return f"confirmada: {self.person_name}{sim}"
            case ClusterStatus.AUTO:
                return f"{self.person_name}{sim}"
            case ClusterStatus.AMBIGUOUS:
                return f"pyannote: {self.person_name}{sim}{llm}; Enter aceita o pyannote" + ("; L aceita a LLM" if llm else "")
            case _:
                base = f"sem nome; parecida com {self.person_name}{sim}" if self.person_name else "sem nome"
                return base + llm + ("; Enter ou L aceita a LLM" if llm and not self.person_name else ("; L aceita a LLM" if llm else ""))


@dataclass(frozen=True, slots=True)
class TranscriptRow:
    segment_id: int
    channel: str
    t_start_ms: int
    t_end_ms: int
    name: str
    role: str
    text: str                        # original do Whisper (nunca alterado, CP3)
    play_from_ms: int
    text_llm: str | None = None      # F3 item 8: transcrição revisada, quando existe
    prefer_llm: bool = True          # P4: a leitura mostra a revisada por padrão, com alternância
    person_id: int | None = None     # F5: a pessoa resolvida (segmento, cluster ou mescla); None = voz sem nome

    @property
    def stamp(self) -> str:
        s = max(0, self.t_start_ms // 1000)
        return f"{s // 60:02d}:{s % 60:02d}"

    @property
    def shown_text(self) -> str:
        return self.text_llm if (self.prefer_llm and self.text_llm) else self.text

    @property
    def line(self) -> str:
        who = f"{self.name} — {self.role}" if self.role else self.name
        return f"[{self.stamp}] {who}: {self.shown_text}"


# ---------------------------------------------------------------- leitura
def _people(store: Store) -> dict[int, dict[str, Any]]:
    return {int(p["id"]): dict(p) for p in store.people.list_people()}


def review_items(store: Store, meeting_id: int) -> list[ReviewItem]:
    """Um card por cluster do job vigente (`current_job_id`, R2-17), exceto o canal L do usuário; mais fala primeiro."""
    job_id = store.segments.current_job_id(meeting_id)
    if job_id is None:
        return []
    people = _people(store)
    out: list[ReviewItem] = []
    for c in store.segments.list_clusters(meeting_id, job_id):
        if c["channel"] == "L" and c["cluster"] == USER_CLUSTER:
            continue
        pid = c.get("person_id")
        name = str(people.get(int(pid), {}).get("name") or "") if pid is not None else ""
        llm_pid = c.get("llm_person_id")
        llm_name = str(people.get(int(llm_pid), {}).get("name") or "") if llm_pid is not None else (c.get("llm_name") or None)
        out.append(ReviewItem(int(c["id"]), str(c["cluster"]), str(c["channel"]), ClusterStatus(str(c["status"])), int(c["speech_ms"]),
                              int(pid) if pid is not None else None, name, c.get("similarity"), c.get("margin"), c.get("merged_into"),
                              bool(c["centroid"].size), int(llm_pid) if llm_pid is not None else None, llm_name or None,
                              c.get("llm_confidence"), _evidence(store, c.get("llm_evidence_json")), c.get("decision_source")))
    return out


def _evidence(store: Store, raw: str | None) -> tuple[tuple[int, str, int], ...]:
    """Evidências da LLM com o minuto vindo de `segments.t_start_ms` (nunca do texto da LLM)."""
    if not raw:
        return ()
    try:
        items = json.loads(raw)
    except ValueError:
        return ()
    out: list[tuple[int, str, int]] = []
    for e in items if isinstance(items, list) else []:
        try:
            sid = int(e["segment_id"])
        except (KeyError, TypeError, ValueError):
            continue
        row = store._q("SELECT t_start_ms FROM segments WHERE id = ?", (sid,))
        t_ms = int(row[0][0]) if row else int(e.get("t_start_ms") or 0)
        out.append((sid, str(e.get("quote") or ""), t_ms))
    return tuple(out)


def pending(items: list[ReviewItem]) -> list[ReviewItem]:
    return [i for i in items if i.pending and i.merged_into is None]


def _speaker(s: Mapping[str, Any], people: dict[int, dict[str, Any]], clusters: dict[int, ClusterRow]) -> tuple[str, str, int | None]:
    """(nome, cargo do dia, pessoa) de um segmento: a pessoa do segmento, senão a do cluster (mescla incluída); sem pessoa, o
    rótulo da voz e cargo vazio."""
    pid = s.get("person_id")
    if pid is None and s.get("cluster_id") is not None and int(s["cluster_id"]) in clusters:
        pid = resolve_person(clusters[int(s["cluster_id"])], clusters)
    person = people.get(int(pid)) if pid is not None else None
    if pid is None or person is None:
        return (f"Voz {s.get('cluster')}" if s.get("cluster") else "Desconhecido"), "", None
    return str(person["name"]), str(s.get("role_at_time") or person.get("role") or ""), int(pid)


def _row(s: Mapping[str, Any], people: dict[int, dict[str, Any]], clusters: dict[int, ClusterRow], prefer_llm: bool) -> TranscriptRow:
    name, role, pid = _speaker(s, people, clusters)
    first = int(s["t_start_ms"])
    try:
        words = json.loads(s.get("words_json") or "[]")
        if words:
            first = int(words[0][0])
    except (ValueError, TypeError, IndexError):
        pass
    return TranscriptRow(int(s["id"]), str(s.get("channel") or ""), int(s["t_start_ms"]), int(s["t_end_ms"]), name, role,
                         str(s.get("text") or ""), max(0, first - PLAY_LEAD_MS), s.get("text_llm") or None, prefer_llm, pid)


def _reading(store: Store, meeting_id: int) -> tuple[dict[int, dict[str, Any]], int | None, dict[int, ClusterRow]]:
    job_id = store.segments.current_job_id(meeting_id)
    return _people(store), job_id, {int(c["id"]): c for c in store.segments.list_clusters(meeting_id, job_id)}


def transcript_rows(store: Store, meeting_id: int, prefer_llm: bool = True) -> list[TranscriptRow]:
    """`[mm:ss] Nome — Cargo: texto` por segmento; sem pessoa, o rótulo da voz; toca da primeira palavra menos 150 ms.
    `prefer_llm` mostra a transcrição revisada quando existe (P4); o texto original fica sempre na linha."""
    people, job_id, clusters = _reading(store, meeting_id)
    return [_row(s, people, clusters, prefer_llm) for s in store.segments.list_segments(meeting_id, job_id)]


def transcript_rows_between(store: Store, meeting_id: int, windows: Iterable[tuple[int, int]],
                            prefer_llm: bool = True) -> list[TranscriptRow]:
    """As mesmas linhas da leitura, só dos segmentos vigentes que cruzam as janelas [t0, t1), sem repetir, em ordem de tempo
    (F5 R6-02: o chat global manda poucas falas de reuniões longas). Sem ler `words_json`: toca do começo do segmento."""
    people, job_id, clusters = _reading(store, meeting_id)
    seen: dict[int, TranscriptRow] = {}
    for t0, t1 in windows:
        for s in store.segments.overlapping(meeting_id, job_id, t0, t1):
            if int(s["id"]) not in seen:
                seen[int(s["id"])] = _row(s, people, clusters, prefer_llm)
    return sorted(seen.values(), key=lambda r: (r.t_start_ms, r.segment_id))


def participants(store: Store, meeting_id: int) -> list[str]:
    """"Nome — Cargo" de quem fala na reunião (pessoas resolvidas, na ordem da primeira fala), sem ler as falas."""
    people, job_id, clusters = _reading(store, meeting_id)
    out: dict[str, None] = {}
    for s in store.segments.speakers(meeting_id, job_id):
        name, role, pid = _speaker(s, people, clusters)
        if pid is not None:
            out.setdefault(f"{name} — {role}" if role else name)
    return list(out)


# ---------------------------------------------------------------- áudio do card
def clip_range(store: Store, cluster_id: int, seconds: float = CLIP_S) -> tuple[str, int, int] | None:
    """(canal, t0, t1) do trecho mais longo da voz: pelos segmentos ou, antes deles, pelos turnos da costura."""
    c = store.segments.get_cluster(cluster_id)
    if c is None:
        return None
    best: tuple[int, int] | None = None
    for s in store.segments.list_segments(int(c["meeting_id"]), int(c["job_id"])):
        if s.get("cluster_id") == cluster_id and (best is None or s["t_end_ms"] - s["t_start_ms"] > best[1] - best[0]):
            best = (int(s["t_start_ms"]), int(s["t_end_ms"]))
    if best is None:
        raw = store.jobs.get_artifact(int(c["job_id"]), STITCH_ARTIFACT, 0 if c["channel"] == "R" else 1)
        if raw is not None:
            header, _ = unpack_artifact(raw)
            for a, b, label in header.get("turns", []):
                if label == c["cluster"] and (best is None or b - a > best[1] - best[0]):
                    best = (int(a), int(b))
    if best is None:
        return None
    return str(c["channel"]), best[0], min(best[1], best[0] + int(seconds * 1000))


def decode_clip(store: Store, meeting_id: int, channel: str, t0_ms: int, t1_ms: int) -> tuple[np.ndarray, int]:
    from ..store.playback import decode_range

    return decode_range(store, meeting_id, channel, t0_ms, t1_ms)


# ---------------------------------------------------------------- ações (todas por ação do usuário)
def _similarity_to(store: Store, c: ClusterRow, person_id: int) -> float | None:
    if not c["centroid"].size or not c.get("model"):
        return None
    p = store.profiles.profile_for(person_id, str(c["model"]))
    if p is None or int(p.get("n_samples") or 0) == 0 or p["centroid"].size != c["centroid"].size:
        return None
    return float(np.dot(normalize(c["centroid"]), normalize(p["centroid"])))


def decision_source_for(c: ClusterRow, pid: int) -> DecisionSource:
    """O que o usuário aceitou (F3 item 5, mede o G3): a sugestão do pyannote, a da LLM, outra pessoa ou nenhuma sugestão."""
    pyannote = c.get("person_id")
    llm = c.get("llm_person_id")
    if pyannote is not None and int(pyannote) == pid:          # inclui o "parecida com" de um card `sem_nome` (F3.1 R3-08)
        return DecisionSource.USER_PYANNOTE
    if llm is not None and int(llm) == pid:
        return DecisionSource.USER_LLM
    if pyannote is None and llm is None and not c.get("llm_name"):
        return DecisionSource.USER
    return DecisionSource.USER_OTHER


def confirm(store: Store, cluster_id: int, person_id: int | None = None, author: str = "usuario",
            source: DecisionSource | None = None) -> int:
    """Enter no card (sugestão do pyannote) ou "outra pessoa" (`person_id`); devolve a pessoa confirmada. Num card `sem_nome`
    só com sugestão da LLM, Enter aceita a LLM (F3A-43). `source` registra o que foi aceito (`decision_source`)."""
    c = store.segments.get_cluster(cluster_id)
    if c is None:
        raise ValueError(f"cluster {cluster_id} não existe")
    pid = person_id if person_id is not None else c.get("person_id")
    if pid is None and str(c["status"]) == ClusterStatus.UNNAMED and c.get("llm_person_id") is not None:
        pid = c.get("llm_person_id")
    if pid is None:
        if str(c["status"]) == ClusterStatus.UNNAMED and c.get("llm_name"):
            return new_person(store, cluster_id, str(c["llm_name"]), author=author, source=DecisionSource.USER_LLM)
        raise ValueError("escolha a pessoa desta voz")
    pid = int(pid)
    source = source or decision_source_for(c, pid)
    person = store.people.get_person(pid)
    if person is None:
        raise ValueError(f"pessoa {pid} não existe")
    meeting = store.get_meeting(int(c["meeting_id"])) or {}
    sim = c.get("similarity") if pid == c.get("person_id") else _similarity_to(store, c, pid)
    segs = [s for s in store.segments.list_segments(int(c["meeting_id"]), int(c["job_id"])) if s.get("cluster_id") == cluster_id]
    previous = int(c["person_id"]) if str(c["status"]) == ClusterStatus.CONFIRMED and c.get("person_id") is not None else None  # type: ignore[arg-type]
    with store.transaction():
        if previous is not None and previous != pid and c.get("model"):
            old = store.profiles.profile_for(previous, str(c["model"]))             # correção: o vetor sai do perfil anterior (R2-09)
            if old is not None and store.profiles.invalidate_cluster_embeddings(int(old["id"]), cluster_id):
                store.add_revision("voice_profile", int(old["id"]), "invalidado", f"cluster {cluster_id}", str(pid), author)
        store.segments.update_cluster(cluster_id, status=ClusterStatus.CONFIRMED, person_id=pid, decision_source=str(source))
        store.segments.set_segments_person(int(c["meeting_id"]), cluster_id, pid, str(person.get("role") or "") or None)
        if c["centroid"].size and c.get("model") and previous != pid:
            store.profiles.confirm(pid, str(c["model"]), c["centroid"], sim, int(c["meeting_id"]),
                                   segment_id=int(segs[0]["id"]) if segs else None, platform=meeting.get("platform"), cluster_id=cluster_id)
        store.add_revision("cluster", cluster_id, "person_id", c.get("person_id"), pid, author)
        store.add_event(int(c["meeting_id"]), None, EventType.PROFILE_CONFIRM,
                        {"cluster_id": cluster_id, "person_id": pid, "similarity": sim, "label": c["cluster"]})
    log.info("voz %s da reunião %s confirmada como %s (sim %s)", c["cluster"], c["meeting_id"], person.get("name"), sim)
    return pid


def new_person(store: Store, cluster_id: int, name: str, role: str = "", author: str = "usuario",
               source: DecisionSource | None = None) -> int:
    """Nome novo vira pessoa só na confirmação (F3 item 5)."""
    name = name.strip()
    if not name:
        raise ValueError("informe o nome")
    existing = store.people.find_by_name(name)
    pid = int(existing["id"]) if existing else store.people.create_person(name, role=role)
    return confirm(store, cluster_id, pid, author, source=source)


def accept_llm(store: Store, cluster_id: int, author: str = "usuario") -> int:
    """Tecla `L` no card: aceita a sugestão da LLM (pessoa cadastrada ou nome novo); `decision_source = usuario_llm`."""
    c = store.segments.get_cluster(cluster_id)
    if c is None:
        raise ValueError(f"cluster {cluster_id} não existe")
    if c.get("llm_person_id") is not None:
        return confirm(store, cluster_id, int(c["llm_person_id"]), author, source=DecisionSource.USER_LLM)  # type: ignore[arg-type]
    if c.get("llm_name"):
        return new_person(store, cluster_id, str(c["llm_name"]), author=author, source=DecisionSource.USER_LLM)
    raise ValueError("esta voz não tem sugestão da LLM")


def ignore(store: Store, cluster_id: int, author: str = "usuario") -> None:
    c = store.segments.get_cluster(cluster_id)
    if c is None:
        return
    with store.transaction():
        store.segments.update_cluster(cluster_id, status=ClusterStatus.IGNORED)
        store.segments.set_segments_person(int(c["meeting_id"]), cluster_id, None, None)
        store.add_revision("cluster", cluster_id, "status", c["status"], ClusterStatus.IGNORED, author)


def merge(store: Store, cluster_id: int, into_cluster_id: int, author: str = "usuario") -> None:
    """"É a mesma pessoa que a voz X": o cluster passa a seguir o outro; só confirma junto se o outro já tem pessoa decidida
    (`auto` ou `confirmado`); uma sugestão no alvo não confirma ninguém (R2-08)."""
    if cluster_id == into_cluster_id:
        raise ValueError("escolha outra voz")
    c, target = store.segments.get_cluster(cluster_id), store.segments.get_cluster(into_cluster_id)
    if c is None or target is None or int(c["meeting_id"]) != int(target["meeting_id"]):
        raise ValueError("vozes de reuniões diferentes")
    if target.get("merged_into") == cluster_id:
        raise ValueError("essas vozes já estão mescladas")
    by_id = {int(x["id"]): x for x in store.segments.list_clusters(int(c["meeting_id"]), int(c["job_id"]))}
    pid = resolve_person(target, by_id)
    with store.transaction():
        store.segments.update_cluster(cluster_id, merged_into=into_cluster_id)
        store.add_revision("cluster", cluster_id, "merged_into", c.get("merged_into"), into_cluster_id, author)
        if pid is not None:
            confirm(store, cluster_id, pid, author)


def finish(store: Store, meeting_id: int) -> None:
    """"Concluir": a reunião vira `revisada` mesmo com vozes ignoradas ou sem nome (o usuário decidiu)."""
    store.meetings.set_status(meeting_id, MeetingStatus.REVIEWED)
    store.add_event(meeting_id, None, EventType.PROFILE_CONFIRM, {"review": "concluída"})
