"""`JobRunner`: um job do começo ao fim (F2 itens 9 a 12), lendo e escrevendo só no banco.

Ordem (P1): diarizar R (embeddings na mesma passagem, com relógio próprio) -> casar perfis (clusters gravados: a revisão já
pode abrir) -> transcrever R -> transcrever L -> segmentos. Cada janela vira um artefato logo depois de calculada; na
retomada, os `idx` presentes são pulados (perda máxima: uma janela). Entre janelas: heartbeat, cancelamento cooperativo e
a lease da gravação (Q7). Nunca há transação em volta de computação. Preset sala (U-015): L segue o caminho do R; senão
L é o usuário (`attribution = canal`) e reforça o perfil dele com `source = automatico` (P3). Reprocessar escreve com o
`job_id` novo e, no último passo, transporta as decisões humanas por cosseno e troca numa só transação (item 12, CP3).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from typing import Any, override

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..log import get_logger
from ..model import Attribution, ClusterStatus, EventType, JobKind, JobStatus, JobStep, MeetingStatus, SegmentSource
from ..store import Store
from ..store.core import json_default
from ..store.jobs import JobRow
from ..store.profiles import normalize
from ..store.segments import ClusterInsert, ClusterRow, SegmentInsert, resolve_person
from .align import Segmenter, merge_words
from .base import PermanentError, PipelineDeps
from .jobbase import BaseJobRunner, Clock, JobCancelledError, Sleep, StepTiming
from .profiles import ProfileMatcher, ProfileRef, clean_turns, cluster_data, expected_names, expected_person_ids
from .stitch import stitch
from .types import (AudioWindow, DiarizationWindow, JobConfig, TranscriptWindow, Turn, Word, decode_diarization, decode_transcript,
                    encode_diarization, encode_transcript, pack_artifact, unpack_artifact)
from .windows import frame_rms, iter_windows

log = get_logger("pipeline.runner")

STEP_WEIGHTS: dict[str, float] = {JobStep.DIARIZE: 0.45, JobStep.EMBED: 0.05, JobStep.MATCH: 0.0, JobStep.TRANSCRIBE_R: 0.33,
                                  JobStep.TRANSCRIBE_L: 0.15, JobStep.SEGMENTS: 0.02}
RTF_SEEDS: dict[str, float] = {JobStep.DIARIZE: 0.8, JobStep.EMBED: 0.05, JobStep.TRANSCRIBE_R: 0.67, JobStep.TRANSCRIBE_L: 0.67}
EMA_ALPHA = 0.3
PROMPT_WORDS_FIXED = 110          # ~150 tokens: sobra espaço para o contexto da janela anterior (o Whisper guarda 224)
PROMPT_WORDS_CONTEXT = 60
STITCH_ARTIFACT = "stitch"        # artefatos auxiliares (não são JobStep)
EMBED_L_ARTIFACT = "embed_l"
TRANSPORT_MIN_COS = 0.8
USER_CLUSTER = "L"
MIN_LANGUAGE_PROB = 0.5
__all__ = ["JobCancelledError", "JobRunner", "StepTiming", "build_prompt", "update_rtf"]    # nomes da F2 continuam aqui


def build_prompt(glossary: str, participants: str | None, title: str | None, previous: Sequence[Word],
                 llm_glossary: str | None = None) -> str:
    """Prompt fixo (glossário da LLM primeiro, depois o fixo, participantes e título, sem termos repetidos) cortado a ~150
    tokens, mais as últimas ~60 palavras da janela anterior (F3A-12: o mais específico sobrevive ao corte)."""
    terms: list[str] = []
    seen: set[str] = set()
    for source in (llm_glossary or "", glossary or ""):
        for term in (t.strip() for t in source.replace(";", ",").split(",")):
            if term and term.casefold() not in seen:
                seen.add(term.casefold())
                terms.append(term)
    fixed_parts = [p.strip() for p in (", ".join(terms), participants or "", title or "") if p and p.strip()]
    fixed = " ".join(" ".join(fixed_parts).split()[:PROMPT_WORDS_FIXED])
    context = " ".join(w.text.strip() for w in previous[-PROMPT_WORDS_CONTEXT:] if w.text.strip())
    return f"{fixed} {context}".strip() if fixed or context else ""


def update_rtf(rtf_json: str, timings: dict[str, StepTiming]) -> str:
    """Média móvel (alfa 0,3) do RTF por `etapa:modelo` (item 15): transcrição sobre a fala, o resto sobre a duração."""
    try:
        rtf: dict[str, float] = {k: float(v) for k, v in json.loads(rtf_json or "{}").items()}
    except (ValueError, TypeError, AttributeError):
        rtf = {}
    for step, t in timings.items():
        base = t.speech_s if step in (JobStep.TRANSCRIBE_R, JobStep.TRANSCRIBE_L) else t.audio_s
        if base <= 0 or t.wall_s <= 0:
            continue
        key = f"{step}:{t.model}"
        value = t.wall_s / base
        rtf[key] = round(EMA_ALPHA * value + (1 - EMA_ALPHA) * rtf[key], 4) if key in rtf else round(value, 4)
    return json.dumps(rtf, ensure_ascii=False)


class JobRunner(BaseJobRunner):
    step_weights = STEP_WEIGHTS

    def __init__(self, store: Store, deps: PipelineDeps, job: JobRow, *, config: JobConfig | None = None, now: Clock | None = None,
                 sleep: Sleep | None = None, heartbeat_s: float = 15.0, heartbeat_thread: bool = True, wait_poll_s: float = 5.0,
                 memory_rss: Callable[[], int | None] | None = None):
        super().__init__(store, job, now=now, sleep=sleep, heartbeat_s=heartbeat_s, heartbeat_thread=heartbeat_thread,
                         wait_poll_s=wait_poll_s, memory_rss=memory_rss)
        self.deps = deps
        self.config = config or JobConfig.from_json(job.get("config_json"))
        self.me: dict[str, Any] | None = None
        self.language: str | None = None

    @property
    @override
    def respects_lease(self) -> bool:
        return self.config.pause_on_record

    # ---------------------------------------------------------------- ciclo
    def run(self) -> JobStatus:
        meeting = self.store.get_meeting(self.meeting_id)
        if meeting is None:
            return self._fail(PermanentError("a reunião deste job não existe mais"), permanent=True)
        self.meeting = dict(meeting)
        self.language = self.meeting.get("language")
        self.me = dict(self.store.get_me() or {}) or None
        if self.kind == JobKind.EMBED_RANGE:
            return self._fail(PermanentError("perfil de trecho livre entra na F6"), permanent=True)
        self._start_heartbeat()
        try:
            return self._execute()
        except JobCancelledError:
            return self._cancelled()
        except PermanentError as e:
            return self._fail(e, permanent=True)
        except Exception as e:  # qualquer falha da etapa vira `erro` com nova tentativa (item 11)
            return self._fail(e, permanent=False)
        finally:
            self._stop_heartbeat()

    @override
    def _execute(self) -> JobStatus:
        self.store.add_event(self.meeting_id, None, EventType.JOB_START,
                             {"job_id": self.job_id, "kind": str(self.kind), "attempts": self.job.get("attempts", 0),
                              "config": self.config.to_dict()})
        self._prepare()
        channels = ["R", "L"] if self.meeting.get("is_in_room") else ["R"]
        clusters_by_channel: dict[str, tuple[tuple[Turn, ...], dict[str, int], list[ClusterRow]]] = {}
        for ch in channels:
            windows = self._diarize(ch, share=1.0 / len(channels))          # preset sala: o peso da etapa se divide (R2-20)
            clusters_by_channel[ch] = self._match(ch, windows)
        if "L" not in channels:
            self._embed_user_channel()
        self._end_step(JobStep.EMBED)
        self._end_step(JobStep.MATCH)
        transcripts = {"R": self._transcribe("R", JobStep.TRANSCRIBE_R), "L": self._transcribe("L", JobStep.TRANSCRIBE_L)}
        self._segments(transcripts, clusters_by_channel)
        return self._finish_ok()

    def _pending_voices(self) -> bool:
        """Há voz do job por decidir (fora o `L` do usuário)? Decide se uma reunião `revisada` volta a `processada` (R2-11)."""
        return any(str(c["status"]) in (ClusterStatus.AMBIGUOUS, ClusterStatus.UNNAMED) and c.get("merged_into") is None
                   and not (c["channel"] == "L" and c["cluster"] == USER_CLUSTER)
                   for c in self.store.segments.list_clusters(self.meeting_id, self.job_id))

    @override
    def _finish_ok(self) -> JobStatus:
        self._done_weight = 1.0
        others = [c for c in self.store.segments.list_clusters(self.meeting_id) if int(c["job_id"]) != self.job_id]
        if self.kind == JobKind.REPROCESS or others:
            with self.store.transaction():
                self._transport_decisions(others)
                # só vozes novas que ficaram pendentes depois do transporte rebaixam `revisada` (R2-11); no primeiro
                # processamento "Concluir" com voz sem nome é decisão do usuário e a reunião fica `revisada`
                downgrade = self.kind == JobKind.REPROCESS and self._pending_voices()
                self.store.segments.swap_job(self.meeting_id, self.job_id, MeetingStatus.PROCESSED, downgrade_reviewed=downgrade)
        else:
            self.store.meetings.mark_processed(self.meeting_id, force=False)
        self.store.jobs.delete_artifacts(self.job_id)
        self.store.jobs.finish(self.job_id, JobStatus.DONE, timings=self._timings_dict())
        self.store.set_setting("pipeline.rtf_json", update_rtf(self.store.get_setting("pipeline.rtf_json"), self.timings))
        self.store.add_event(self.meeting_id, None, EventType.JOB_DONE, {"job_id": self.job_id, "timings": self._timings_dict()})
        log.info("job %s concluído: %s", self.job_id, self._timings_dict())
        self.status = JobStatus.DONE
        self._enqueue_llm()
        self._index_after_pipeline()
        return JobStatus.DONE

    def _index_after_pipeline(self) -> None:
        """F5A-03: trechos e FTS na hora (a busca por palavra acha a reunião antes da ata) e o job `index`, que pede os vetores
        depois do job `llm` (o claim põe o `index` atrás). Falhar aqui não desfaz o job concluído: só o log."""
        try:
            from ..rag.index import enqueue_index, sync_text
        except ImportError as e:
            log.warning("job %s: índice da busca indisponível: %s", self.job_id, e)
            return
        try:
            sync_text(self.store, self.meeting_id)
        except Exception as e:  # o índice nunca derruba o processamento
            log.warning("job %s: trechos da busca não gravados (o job index refaz): %s", self.job_id, e)
        try:
            enqueue_index(self.store, self.meeting_id, "pipeline")
        except Exception as e:
            log.warning("job %s: não enfileirei o índice da busca: %s", self.job_id, e)

    def _enqueue_llm(self) -> None:
        """F3 item 2: o job da F2 termina como sempre e enfileira o job `llm` da reunião (worker leve); falhar aqui não
        desfaz o job concluído."""
        try:
            from .llm_steps import enqueue_after_pipeline

            enqueue_after_pipeline(self.store, self.meeting_id, self.job_id, self.kind)
        except Exception as e:  # a camada de LLM nunca derruba o processamento
            log.warning("job %s: não enfileirei o job de LLM: %s", self.job_id, e)

    @override
    def _cancelled(self) -> JobStatus:
        """Sem decisão humana nos clusters do job, eles saem com os artefatos (dado derivado); com decisão, ficam com os
        artefatos da diarização, do embed do L e da costura: o trecho de 5 s continua disponível e a nova tentativa
        reaproveita os mesmos ids sem refazer horas de diarização (R2-10, N-03)."""
        clusters = self.store.segments.list_clusters(self.meeting_id, self.job_id)
        decided = any(str(c["status"]) in (ClusterStatus.CONFIRMED, ClusterStatus.IGNORED) or c.get("merged_into") is not None
                      for c in clusters if not (c["channel"] == "L" and c["cluster"] == USER_CLUSTER))
        with self.store.transaction():
            if decided:
                self.store.jobs.delete_artifacts(self.job_id, keep=(STITCH_ARTIFACT, str(JobStep.DIARIZE), EMBED_L_ARTIFACT))
            else:
                self.store.segments.delete_clusters(self.meeting_id, self.job_id)
                self.store.jobs.delete_artifacts(self.job_id)
        self.store.jobs.finish(self.job_id, JobStatus.CANCELLED, timings=self._timings_dict())
        self.store.add_event(self.meeting_id, None, EventType.JOB_CANCEL, {"job_id": self.job_id, "step": str(self._step or "")})
        log.info("job %s cancelado pelo usuário na etapa %s", self.job_id, self._step)
        self.status = JobStatus.CANCELLED
        return JobStatus.CANCELLED

    def _between_windows(self) -> None:
        """Nome da F2 para `checkpoint()`: heartbeat, cancelamento e lease da gravação antes de cada janela."""
        self.checkpoint()

    # ---------------------------------------------------------------- etapas
    def _prepare(self) -> None:
        def progress(message: str, fraction: float) -> None:
            self.store.jobs.set_progress(self.job_id, JobStep.DOWNLOAD, 0.0)
            log.info("job %s: %s (%.0f %%)", self.job_id, message, fraction * 100)
        self._step = JobStep.DOWNLOAD
        self.deps.prepare(progress)
        self._step = None

    def _diarize(self, channel: str, share: float = 1.0) -> list[DiarizationWindow]:
        """Diarização por janela de até `diar_window_s` com embeddings dos turnos limpos na mesma passagem (Q8 se o
        pipeline os expuser com o mesmo modelo). Janelas já gravadas não são decodificadas de novo (R2-21)."""
        step = JobStep.DIARIZE
        self._begin_step(step, share)
        saved: dict[int, DiarizationWindow] = {}
        for idx in sorted(self.store.jobs.artifact_indexes(self.job_id, step)):
            local = idx - 100_000 if channel != "R" else idx
            if (idx >= 100_000) == (channel != "R"):
                saved[local] = decode_diarization(self.store.jobs.get_artifact(self.job_id, step, idx) or b"")
        known = {i: (w.t0_ms, w.t1_ms, self.config.diar_overlap_s * 1000 if i else 0) for i, w in saved.items()}
        out: list[DiarizationWindow] = []
        total_ms = max(1, int(self.meeting.get("duration_ms") or 1))
        expected = expected_names(self.meeting.get("expected_participants"))
        min_spk, max_spk = (1, len(expected) + 1) if expected else (None, None)
        for w in iter_windows(self.store, self.meeting_id, channel, self.config.diar_window_s, self.config.diar_overlap_s,
                              silence_rms=self.config.silence_rms, cut_on_silence=False, known=known):
            idx = self._artifact_idx(channel, w.idx)
            if w.idx in saved:
                out.append(saved[w.idx])
                continue
            self._between_windows()
            t0 = time.perf_counter()
            if w.has_speech:
                dw = self.deps.diarizer().diarize(w, min_speakers=min_spk, max_speakers=max_spk)
            else:
                dw = DiarizationWindow(w.idx, w.t0_ms, w.t1_ms, ())
            timing = self._timing(step, self.deps.diarizer().name)
            timing.wall_s += time.perf_counter() - t0
            timing.audio_s += w.duration_ms / 1000
            dw = self._embed_window(w, dw)
            self.store.jobs.put_artifact(self.job_id, step, idx, encode_diarization(dw))
            out.append(dw)
            self._progress(step, w.t1_ms / total_ms)
        self._end_step(step, share)
        return out

    def _embed_window(self, w: AudioWindow, dw: DiarizationWindow) -> DiarizationWindow:
        """Centroide por rótulo da janela: embeddings do pipeline (Q8) só se o modelo for o mesmo; senão wespeaker (ou o
        fake) nos turnos limpos (>= 1,5 s, sem sobreposição, no máximo 20 por rótulo, os mais longos)."""
        if not dw.turns:
            return DiarizationWindow(dw.idx, dw.t0_ms, dw.t1_ms, dw.turns, dw.overlap, None, dw.model)
        embedder = self.deps.embedder()
        timing = self._timing(JobStep.EMBED, embedder.model)
        if dw.embeddings and dw.model == embedder.model:
            log.info("job %s: embeddings do próprio pipeline (%s) para %d rótulos (Q8)", self.job_id, dw.model, len(dw.embeddings))
            return dw
        t0 = time.perf_counter()
        vectors: dict[str, np.ndarray] = {}
        for label, turns in clean_turns(dw.turns, dw.overlap).items():
            acc: np.ndarray | None = None                            # dimensão vem do primeiro vetor, não de `dim` (R2-23)
            for t in turns:
                a, b = round((t.start_ms - w.t0_ms) * w.sample_rate / 1000), round((t.end_ms - w.t0_ms) * w.sample_rate / 1000)
                v = normalize(embedder.embed(w.samples[a:b], w.sample_rate)) * t.duration_ms
                acc = v if acc is None else acc + v
                timing.speech_s += t.duration_ms / 1000
            if acc is not None and np.any(acc):
                vectors[label] = normalize(acc)
        timing.wall_s += time.perf_counter() - t0
        timing.audio_s += w.duration_ms / 1000
        return DiarizationWindow(dw.idx, dw.t0_ms, dw.t1_ms, dw.turns, dw.overlap, vectors or None, embedder.model)

    def _embed_user_channel(self) -> None:
        """P3: um vetor `automatico` por reunião no perfil do usuário, dos trechos com fala do canal L (fora do centroide)."""
        if self.me is None:
            return
        step = JobStep.EMBED
        done = self.store.jobs.artifact_indexes(self.job_id, EMBED_L_ARTIFACT)
        embedder = self.deps.embedder()
        timing = self._timing(step, embedder.model)
        acc: np.ndarray | None = None
        total = 0
        saved: dict[int, tuple[dict[str, Any], bytes]] = {}
        for idx in sorted(done):
            saved[idx] = unpack_artifact(self.store.jobs.get_artifact(self.job_id, EMBED_L_ARTIFACT, idx) or b"")
        known = {i: (int(h["t0"]), int(h["t1"]), 0) for i, (h, _) in saved.items() if "t0" in h}
        for w in iter_windows(self.store, self.meeting_id, "L", self.config.diar_window_s, 0, silence_rms=self.config.silence_rms,
                              cut_on_silence=False, known=known):
            if w.idx in saved:
                header, blob = saved[w.idx]
                if header.get("ms"):
                    v = np.frombuffer(blob, dtype="<f4").astype(np.float32) * header["ms"]
                    acc = v if acc is None else acc + v
                    total += int(header["ms"])
                continue
            self._between_windows()
            t0 = time.perf_counter()
            vec, ms = self._embed_runs(w, embedder)
            timing.wall_s += time.perf_counter() - t0
            timing.speech_s += ms / 1000
            self.store.jobs.put_artifact(self.job_id, EMBED_L_ARTIFACT, w.idx,
                                         pack_artifact({"ms": ms, "t0": w.t0_ms, "t1": w.t1_ms}, vec.tobytes() if ms else b""))
            if ms:
                acc = vec * ms if acc is None else acc + vec * ms
                total += ms
        if total and acc is not None and np.any(acc):
            self.store.profiles.upsert_auto_embedding(int(self.me["id"]), embedder.model, normalize(acc), self.meeting_id,
                                                      self.meeting.get("platform"))

    def _embed_runs(self, w: AudioWindow, embedder: Any, min_run_ms: int = 1500, limit: int = 20) -> tuple[np.ndarray, int]:
        """Trechos contíguos com fala (RMS por quadro) do canal L, os mais longos primeiro."""
        if not w.has_speech:
            return np.zeros(0, np.float32), 0
        frame = max(1, int(w.sample_rate * 20 / 1000))
        loud = frame_rms(w.samples, w.sample_rate) > self.config.silence_rms
        runs: list[tuple[int, int]] = []
        start = -1
        for i, v in enumerate([*loud.tolist(), False]):
            if v and start < 0:
                start = i
            elif not v and start >= 0:
                runs.append((start, i))
                start = -1
        runs = [(a, b) for a, b in runs if (b - a) * 20 >= min_run_ms]
        runs.sort(key=lambda r: r[0] - r[1])
        acc: np.ndarray | None = None
        total = 0
        for a, b in runs[:limit]:
            ms = (b - a) * 20
            v = normalize(embedder.embed(w.samples[a * frame:b * frame], w.sample_rate)) * ms
            acc = v if acc is None else acc + v
            total += ms
        return (normalize(acc) if acc is not None and total else np.zeros(0, np.float32)), total

    def _match(self, channel: str, windows: list[DiarizationWindow]) -> tuple[tuple[Turn, ...], dict[str, int], list[ClusterRow]]:
        """Costura, centroides globais, casamento com os perfis e gravação dos clusters (a revisão abre aqui). Na retomada
        depois desta etapa, os clusters do job (talvez já confirmados pelo usuário) são mantidos e só a costura é relida."""
        step = JobStep.MATCH
        self._begin_step(step, 1.0)
        prefix = "" if channel == "R" else f"{channel}/"
        art_idx = 0 if channel == "R" else 1
        raw = self.store.jobs.get_artifact(self.job_id, STITCH_ARTIFACT, art_idx)
        if raw is not None:
            header, _ = unpack_artifact(raw)
            turns = tuple(Turn(int(a), int(b), str(c)) for a, b, c in header["turns"])
            centroids = {k: np.asarray(v, np.float32) for k, v in header.get("centroids", {}).items()}
            model = header.get("model")
        else:
            local_turns, overlaps, mapping = stitch(windows)
            acc: dict[str, np.ndarray] = {}
            model = next((w.model for w in windows if w.model), None)
            for w in windows:
                if not w.embeddings:
                    continue
                for label, vec in w.embeddings.items():
                    g = prefix + mapping[w.idx, label]
                    ms = sum(t.duration_ms for t in w.turns if t.speaker == label)
                    acc[g] = acc.get(g, np.zeros_like(vec)) + normalize(vec) * ms
            centroids = {g: normalize(v) for g, v in acc.items() if np.any(v)}
            turns = tuple(Turn(t.start_ms, t.end_ms, prefix + t.speaker) for t in local_turns)
            self.store.jobs.put_artifact(self.job_id, STITCH_ARTIFACT, art_idx, pack_artifact(
                {"turns": [(t.start_ms, t.end_ms, t.speaker) for t in turns], "overlap": list(overlaps),
                 "centroids": {k: v.tolist() for k, v in centroids.items()}, "model": model}))
        existing = [c for c in self.store.segments.list_clusters(self.meeting_id, self.job_id) if c["channel"] == channel]
        if existing:
            return turns, {str(c["cluster"]): int(c["id"]) for c in existing}, existing
        data = cluster_data(turns, {k: [v] for k, v in centroids.items()})
        model = model or self.deps.embedder().model
        profiles = self._profile_refs(model)
        expected = expected_names(self.meeting.get("expected_participants"))
        matcher = ProfileMatcher(self.config.threshold, self.config.margin, expected=expected,
                                 expected_ids=expected_person_ids(self.meeting.get("expected_people_json")))
        results = {r.label: r for r in matcher.match(data, profiles)}
        rows = [ClusterInsert(self.meeting_id, self.job_id, channel, c.label, c.speech_ms, c.centroid, model, results[c.label].status,
                              results[c.label].person_id, results[c.label].similarity, results[c.label].margin) for c in data]
        ids = self.store.segments.insert_clusters(rows)
        log.info("job %s canal %s: %d clusters (%s)", self.job_id, channel,
                 len(rows), ", ".join(f"{r.cluster}={r.status}" for r in rows))
        return turns, ids, [c for c in self.store.segments.list_clusters(self.meeting_id, self.job_id) if c["channel"] == channel]

    def _profile_refs(self, model: str) -> list[ProfileRef]:
        names = {int(p["id"]): str(p.get("name") or "") for p in self.store.people.list_people()}
        return [ProfileRef(int(p["id"]), int(p["person_id"]), p["centroid"], p.get("threshold"), names.get(int(p["person_id"]), ""))
                for p in self.store.profiles.list_profiles(model) if p.get("person_id") is not None and int(p.get("n_samples") or 0) > 0]

    def _transcribe(self, channel: str, step: JobStep) -> list[TranscriptWindow]:
        self._begin_step(step)
        saved: dict[int, TranscriptWindow] = {}
        for idx in sorted(self.store.jobs.artifact_indexes(self.job_id, step)):
            saved[idx] = decode_transcript(self.store.jobs.get_artifact(self.job_id, step, idx) or b"")
        known = {i: (tw.t0_ms, tw.t1_ms, tw.overlap_ms) for i, tw in saved.items()}
        out: list[TranscriptWindow] = []
        total_ms = max(1, int(self.meeting.get("duration_ms") or 1))
        previous: tuple[Word, ...] = ()
        for w in iter_windows(self.store, self.meeting_id, channel, self.config.whisper_window_s, self.config.whisper_overlap_s,
                              silence_rms=self.config.silence_rms, cut_on_silence=True, known=known):
            if w.idx in saved:
                tw = saved[w.idx]
                out.append(tw)
                previous = tw.words
                if self.language is None and tw.language and tw.language_prob >= MIN_LANGUAGE_PROB:
                    self.language = tw.language
                continue
            self._between_windows()
            if not w.has_speech:
                tw = TranscriptWindow(w.idx, w.t0_ms, w.t1_ms, (), overlap_ms=w.overlap_ms)
            else:
                transcriber = self.deps.transcriber()
                prompt = build_prompt(self.config.glossary, self.meeting.get("expected_participants"), self.meeting.get("title"), previous,
                                      self.meeting.get("glossary"))
                t0 = time.perf_counter()
                tw = transcriber.transcribe(w, language=self.language, initial_prompt=prompt)
                timing = self._timing(step, transcriber.name)
                timing.wall_s += time.perf_counter() - t0
                timing.audio_s += w.duration_ms / 1000
                timing.speech_s += tw.speech_ms / 1000
                if self.language is None and tw.language and tw.language_prob >= MIN_LANGUAGE_PROB:
                    self.language = tw.language                          # D-013: detectado uma vez, fixo depois
                    self.store.meetings.set_language(self.meeting_id, tw.language)
                previous = tw.words
            self.store.jobs.put_artifact(self.job_id, step, w.idx, encode_transcript(tw))
            out.append(tw)
            self._progress(step, w.t1_ms / total_ms)
        self._end_step(step)
        return out

    def _segments(self, transcripts: dict[str, list[TranscriptWindow]],
                  clusters: dict[str, tuple[tuple[Turn, ...], dict[str, int], list[ClusterRow]]]) -> None:
        step = JobStep.SEGMENTS
        self._begin_step(step)
        t0 = time.perf_counter()
        segmenter = Segmenter()
        rows: list[SegmentInsert] = []
        people = {int(p["id"]): p for p in self.store.people.list_people()}
        for channel, windows in transcripts.items():
            words = merge_words(windows)
            if channel in clusters:
                turns, ids, _crows = clusters[channel]
                attribution = Attribution.PYANNOTE
            else:
                ids = self._user_cluster(words)                        # existe mesmo sem fala transcrita
                duration = int(self.meeting.get("duration_ms") or 0)
                end = max(duration, words[-1].end_ms) if words else duration
                turns, attribution = (Turn(0, end, USER_CLUSTER),), Attribution.CHANNEL
            # clusters relidos do banco agora: o usuário pode ter confirmado vozes enquanto o Whisper rodava (P1, R2-04)
            by_id = {int(c["id"]): c for c in self.store.segments.list_clusters(self.meeting_id, self.job_id) if c["channel"] == channel}
            if not words:
                continue
            for d in segmenter.run(words, turns):
                cid = ids.get(d.label) if d.label else None
                crow = by_id.get(cid) if cid is not None else None
                person = None
                if crow is not None and str(crow["status"]) in (ClusterStatus.AUTO, ClusterStatus.CONFIRMED):
                    person = resolve_person(crow, by_id)
                role = str(people[person].get("role") or "") or None if person in people else None
                sim = crow.get("similarity") if crow is not None else None
                conf = float(sim) if sim is not None else d.confidence
                words_json = json.dumps([(w.start_ms, w.end_ms, w.text, round(w.prob, 3)) for w in d.words], ensure_ascii=False,
                                        default=json_default)
                rows.append(SegmentInsert(self.meeting_id, self.job_id, channel, d.start_ms, d.end_ms, d.text, str(attribution),
                                          str(SegmentSource.FINAL), d.label, cid, person, role, conf, words_json))
        rows.sort(key=lambda r: (r.t_start_ms, r.channel))
        with self.store.transaction():                                 # parciais de uma queda anterior saem junto (R2-03)
            self.store.segments.delete_segments(self.meeting_id, self.job_id)
            n = self.store.segments.insert_segments(rows)
        timing = self._timing(step)
        timing.wall_s += time.perf_counter() - t0
        log.info("job %s: %d segmentos", self.job_id, n)
        self._end_step(step)

    def _user_cluster(self, words: Sequence[Word]) -> dict[str, int]:
        """Cluster `L` = o usuário (fora do preset sala), já confirmado: a revisão não pergunta por ele."""
        existing = [c for c in self.store.segments.list_clusters(self.meeting_id, self.job_id) if c["channel"] == "L"]
        if existing:
            return {str(c["cluster"]): int(c["id"]) for c in existing}
        speech = sum(w.end_ms - w.start_ms for w in words)
        me_id = int(self.me["id"]) if self.me else None
        return self.store.segments.insert_clusters([ClusterInsert(self.meeting_id, self.job_id, "L", USER_CLUSTER, speech, None, None,
                                                                  ClusterStatus.CONFIRMED if me_id else ClusterStatus.UNNAMED, me_id)])

    # ---------------------------------------------------------------- reprocessar (item 12)
    def _transport_decisions(self, old: list[ClusterRow]) -> None:
        """Decisões humanas dos clusters antigos (pessoa, status, mesclas) para os novos por cosseno entre centroides do
        mesmo modelo e canal; o cluster `L` casa pelo rótulo; o que não casar vira `revisions` com nota."""
        new = self.store.segments.list_clusters(self.meeting_id, self.job_id)
        decided = [c for c in old
                   if str(c["status"]) in (ClusterStatus.CONFIRMED, ClusterStatus.IGNORED) or c.get("merged_into") is not None]
        pairs: dict[int, int] = {}
        for channel in {str(c["channel"]) for c in old}:
            o = [c for c in old if c["channel"] == channel and c["centroid"].size]
            n = [c for c in new if c["channel"] == channel and c["centroid"].size and c.get("model") in {x.get("model") for x in o}]
            if o and n:
                s = np.stack([normalize(c["centroid"]) for c in o]) @ np.stack([normalize(c["centroid"]) for c in n]).T
                for i, j in zip(*linear_sum_assignment(s, maximize=True), strict=True):
                    if s[i, j] >= TRANSPORT_MIN_COS:
                        pairs[int(o[i]["id"])] = int(n[j]["id"])
            for oc in (c for c in old if c["channel"] == channel and not c["centroid"].size):
                match = next((c for c in new if c["channel"] == channel and c["cluster"] == oc["cluster"]), None)
                if match is not None:
                    pairs[int(oc["id"])] = int(match["id"])
        people = {int(p["id"]): p for p in self.store.people.list_people()}
        new_by_id = {int(c["id"]): c for c in new}
        old_by_id = {int(c["id"]): c for c in old}
        for oc in decided:
            nid = pairs.get(int(oc["id"]))
            if nid is None:                                      # nota na reunião: o cluster antigo some na troca (R2-25)
                self.store.add_revision("meeting", self.meeting_id, "transporte", f"{oc['cluster']}:{oc['status']}:{oc.get('person_id')}",
                                        None, "sistema")
                continue
            nc = new_by_id[nid]
            if str(nc["status"]) in (ClusterStatus.CONFIRMED, ClusterStatus.IGNORED) or nc.get("merged_into") is not None:
                # o usuário já decidiu esta voz durante o reprocessamento (P1): a decisão nova vale; se difere, fica anotada (R2-05)
                if (str(nc["status"]), nc.get("person_id")) != (str(oc["status"]), oc.get("person_id")):
                    self.store.add_revision("cluster", nid, "transporte_conflito", f"{oc['status']}:{oc.get('person_id')}",
                                            f"{nc['status']}:{nc.get('person_id')}", "sistema")
                continue
            mi = oc.get("merged_into")
            merged = pairs.get(int(mi)) if mi is not None else None
            if mi is not None and merged is None:                # a mescla não tem para onde apontar: fica anotada (R2-25)
                target = old_by_id.get(int(mi), {}).get("cluster", mi)
                self.store.add_revision("cluster", nid, "transporte_mescla", f"{oc['cluster']}->{target}", None, "sistema")
            self.store.segments.update_cluster(nid, person_id=oc.get("person_id"), status=str(oc["status"]), merged_into=merged)
            if oc.get("person_id") is not None and str(oc["status"]) == ClusterStatus.CONFIRMED:
                pid = int(oc["person_id"])  # type: ignore[arg-type]
                role = str(people.get(pid, {}).get("role") or "") or None
                self.store.segments.set_segments_person(self.meeting_id, nid, pid, role)
        moved = sum(1 for c in decided if int(c["id"]) in pairs)
        log.info("job %s: %d decisão(ões) transportada(s) de %d", self.job_id, moved, len(decided))

    # ---------------------------------------------------------------- util
    @staticmethod
    def _artifact_idx(channel: str, idx: int) -> int:
        return idx if channel == "R" else 100_000 + idx                # L (preset sala) no mesmo passo, sem colidir
