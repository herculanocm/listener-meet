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
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config.defaults import SPECS
from ..log import get_logger
from ..model import (Attribution, ClusterStatus, EmbeddingSource, EventType, JobKind, JobStatus, JobStep, MeetingStatus,
                     SegmentSource)
from ..store import Store
from ..store.core import json_default
from ..store.jobs import JobRow
from ..store.profiles import normalize
from ..store.segments import ClusterInsert, ClusterRow, SegmentInsert, resolve_person
from .align import Segmenter, merge_words
from .base import PermanentError, PipelineDeps
from .profiles import ProfileMatcher, ProfileRef, clean_turns, cluster_data, expected_names
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
type Clock = Callable[[], datetime]
type Sleep = Callable[[float], None]


class JobCancelledError(Exception):
    """Cancelamento pedido pela interface e atendido entre janelas."""


@dataclass(slots=True)
class StepTiming:
    audio_s: float = 0.0
    speech_s: float = 0.0
    wall_s: float = 0.0
    model: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"audio_s": round(self.audio_s, 3), "speech_s": round(self.speech_s, 3), "wall_s": round(self.wall_s, 3),
                "model": self.model}


def build_prompt(glossary: str, participants: str | None, title: str | None, previous: Sequence[Word]) -> str:
    """Prompt fixo (glossário, participantes, título) cortado a ~150 tokens, mais as últimas ~60 palavras da janela anterior."""
    fixed_parts = [p.strip() for p in (glossary, participants or "", title or "") if p and p.strip()]
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


class JobRunner:
    def __init__(self, store: Store, deps: PipelineDeps, job: JobRow, *, config: JobConfig | None = None, now: Clock | None = None,
                 sleep: Sleep | None = None, heartbeat_s: float = 15.0, heartbeat_thread: bool = True, wait_poll_s: float = 5.0,
                 memory_rss: Callable[[], int | None] | None = None):
        self.store = store
        self.deps = deps
        self.job = job
        self.job_id = int(job["id"])
        self.meeting_id = int(job["meeting_id"])
        self.kind = JobKind(str(job.get("kind") or JobKind.MEETING))
        self.config = config or JobConfig.from_json(job.get("config_json"))
        self.now: Clock = now or (lambda: datetime.now().astimezone())
        self.sleep: Sleep = sleep or time.sleep
        self.heartbeat_s = heartbeat_s
        self.heartbeat_thread = heartbeat_thread
        self.wait_poll_s = wait_poll_s
        self.memory_rss = memory_rss
        self.timings: dict[str, StepTiming] = {}
        self._done_weight = 0.0
        self._step: JobStep | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.meeting: dict[str, Any] = {}
        self.me: dict[str, Any] | None = None
        self.language: str | None = None
        self.status: JobStatus | None = None
        self._load_timings(job.get("timings_json"))

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
            self.store.add_event(self.meeting_id, None, EventType.JOB_START,
                                 {"job_id": self.job_id, "kind": str(self.kind), "attempts": self.job.get("attempts", 0),
                                  "config": self.config.to_dict()})
            self._prepare()
            channels = ["R", "L"] if self.meeting.get("is_in_room") else ["R"]
            clusters_by_channel: dict[str, tuple[tuple[Turn, ...], dict[str, int], list[ClusterRow]]] = {}
            for ch in channels:
                windows = self._diarize(ch)
                clusters_by_channel[ch] = self._match(ch, windows)
            if "L" not in channels:
                self._embed_user_channel()
            self._end_step(JobStep.EMBED)
            self._end_step(JobStep.MATCH)
            transcripts = {"R": self._transcribe("R", JobStep.TRANSCRIBE_R), "L": self._transcribe("L", JobStep.TRANSCRIBE_L)}
            self._segments(transcripts, clusters_by_channel)
            return self._finish_ok()
        except JobCancelledError:
            return self._cancelled()
        except PermanentError as e:
            return self._fail(e, permanent=True)
        except Exception as e:  # qualquer falha da etapa vira `erro` com nova tentativa (item 11)
            return self._fail(e, permanent=False)
        finally:
            self._stop_heartbeat()

    def _finish_ok(self) -> JobStatus:
        self._done_weight = 1.0
        others = [c for c in self.store.segments.list_clusters(self.meeting_id) if int(c["job_id"]) != self.job_id]
        if self.kind == JobKind.REPROCESS or others:
            with self.store.transaction():
                self._transport_decisions(others)
                self.store.segments.swap_job(self.meeting_id, self.job_id, MeetingStatus.PROCESSED)
        else:
            self.store.meetings.set_status(self.meeting_id, MeetingStatus.PROCESSED)
        self.store.jobs.delete_artifacts(self.job_id)
        self.store.jobs.finish(self.job_id, JobStatus.DONE, timings=self._timings_dict())
        self.store.set_setting("pipeline.rtf_json", update_rtf(self.store.get_setting("pipeline.rtf_json"), self.timings))
        self.store.add_event(self.meeting_id, None, EventType.JOB_DONE, {"job_id": self.job_id, "timings": self._timings_dict()})
        log.info("job %s concluído: %s", self.job_id, self._timings_dict())
        self.status = JobStatus.DONE
        return JobStatus.DONE

    def _cancelled(self) -> JobStatus:
        self.store.jobs.delete_artifacts(self.job_id)
        self.store.jobs.finish(self.job_id, JobStatus.CANCELLED, timings=self._timings_dict())
        self.store.add_event(self.meeting_id, None, EventType.JOB_CANCEL, {"job_id": self.job_id, "step": str(self._step or "")})
        log.info("job %s cancelado pelo usuário na etapa %s", self.job_id, self._step)
        self.status = JobStatus.CANCELLED
        return JobStatus.CANCELLED

    def _fail(self, e: BaseException, permanent: bool) -> JobStatus:
        last = traceback.format_exception_only(type(e), e)[-1].strip()
        log.error("job %s falhou na etapa %s%s", self.job_id, self._step, " (erro permanente)" if permanent else "", exc_info=e)
        self.store.jobs.finish(self.job_id, JobStatus.ERROR, error=last, timings=self._timings_dict(), count_attempt=not permanent)
        self.store.add_event(self.meeting_id, None, EventType.JOB_ERROR,
                             {"job_id": self.job_id, "step": str(self._step or ""), "error": last, "permanent": permanent})
        self.status = JobStatus.ERROR
        return JobStatus.ERROR

    # ---------------------------------------------------------------- heartbeat, pausa, cancelamento, progresso
    def _start_heartbeat(self) -> None:
        self.store.jobs.heartbeat(self.job_id)                      # antes de carregar qualquer modelo
        if self.heartbeat_thread:
            self._thread = threading.Thread(target=self._heartbeat_loop, name=f"heartbeat-{self.job_id}", daemon=True)
            self._thread.start()

    def _heartbeat_loop(self) -> None:
        try:
            while not self._stop.wait(self.heartbeat_s):
                try:
                    self.store.jobs.heartbeat(self.job_id)
                except Exception as e:  # o heartbeat nunca derruba o job; o banco pode estar ocupado
                    log.warning("heartbeat do job %s falhou: %s", self.job_id, e)
        finally:
            self.store.close_thread()

    def _stop_heartbeat(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _between_windows(self) -> None:
        """Chamado antes de cada janela: heartbeat, cancelamento e lease da gravação (nunca no meio de uma janela)."""
        self.store.jobs.heartbeat(self.job_id)
        if self.store.jobs.cancel_requested(self.job_id):
            raise JobCancelledError
        waited = False
        while self._paused():
            if not waited:
                self.store.jobs.set_status(self.job_id, JobStatus.PAUSED, JobStep.WAITING)
                log.info("job %s aguardando (gravação em curso ou pausa manual)", self.job_id)
                waited = True
            self.sleep(self.wait_poll_s)
            self.store.jobs.heartbeat(self.job_id)
            if self.store.jobs.cancel_requested(self.job_id):
                raise JobCancelledError
        if waited:
            self.store.jobs.set_status(self.job_id, JobStatus.RUNNING, self._step)

    def _paused(self) -> bool:
        if self.store.get(SPECS["pipeline.paused_manual"]):
            return True
        if not self.config.pause_on_record:
            return False
        until = self.store.get_setting("pipeline.paused_until")
        if not until:
            return False
        try:
            return datetime.fromisoformat(until) > self.now()
        except ValueError:
            return False

    def _begin_step(self, step: JobStep) -> None:
        if self._step != step:
            self._step = step
            self.store.jobs.set_progress(self.job_id, step, self._fraction(0.0), self._timings_dict())
            self.store.add_event(self.meeting_id, None, EventType.JOB_STEP, {"job_id": self.job_id, "step": str(step)})

    def _progress(self, step: JobStep, fraction: float) -> None:
        self.store.jobs.set_progress(self.job_id, step, self._fraction(fraction), self._timings_dict())

    def _end_step(self, step: JobStep) -> None:
        self._done_weight += STEP_WEIGHTS.get(step, 0.0)
        self._done_weight = min(1.0, self._done_weight)
        rss = self.memory_rss() if self.memory_rss is not None else None
        t = self.timings.get(step)
        log.info("job %s etapa %s: %s%s", self.job_id, step, t.as_dict() if t else "nada a fazer",
                 f"; RSS {rss / 1e6:.0f} MB" if rss else "")

    def _fraction(self, step_fraction: float) -> float:
        weight = STEP_WEIGHTS.get(self._step or "", 0.0)
        return round(min(1.0, self._done_weight + weight * max(0.0, min(1.0, step_fraction))), 4)

    def _timing(self, step: JobStep | str, model: str = "") -> StepTiming:
        t = self.timings.get(str(step))
        if t is None:
            t = self.timings[str(step)] = StepTiming(model=model)
        if model and not t.model:
            t.model = model
        return t

    def _timings_dict(self) -> dict[str, Any]:
        return {k: v.as_dict() for k, v in self.timings.items()}

    def _load_timings(self, text: str | None) -> None:
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        for k, v in data.items():
            if isinstance(v, dict):
                self.timings[k] = StepTiming(float(v.get("audio_s", 0)), float(v.get("speech_s", 0)), float(v.get("wall_s", 0)),
                                             str(v.get("model", "")))

    # ---------------------------------------------------------------- etapas
    def _prepare(self) -> None:
        def progress(message: str, fraction: float) -> None:
            self.store.jobs.set_progress(self.job_id, JobStep.DOWNLOAD, 0.0)
            log.info("job %s: %s (%.0f %%)", self.job_id, message, fraction * 100)
        self._step = JobStep.DOWNLOAD
        self.deps.prepare(progress)
        self._step = None

    def _diarize(self, channel: str) -> list[DiarizationWindow]:
        """Diarização por janela de até `diar_window_s` com embeddings dos turnos limpos na mesma passagem (Q8 se o
        pipeline os expuser com o mesmo modelo)."""
        step = JobStep.DIARIZE
        self._begin_step(step)
        done = self.store.jobs.artifact_indexes(self.job_id, step)
        out: list[DiarizationWindow] = []
        total_ms = max(1, int(self.meeting.get("duration_ms") or 1))
        expected = expected_names(self.meeting.get("expected_participants"))
        min_spk, max_spk = (1, len(expected) + 1) if expected else (None, None)
        for w in iter_windows(self.store, self.meeting_id, channel, self.config.diar_window_s, self.config.diar_overlap_s,
                              silence_rms=self.config.silence_rms, cut_on_silence=False):
            idx = self._artifact_idx(channel, w.idx)
            if idx in done:
                out.append(decode_diarization(self.store.jobs.get_artifact(self.job_id, step, idx) or b""))
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
        self._end_step(step)
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
            acc = np.zeros(embedder.dim, np.float32)
            for t in turns:
                a, b = round((t.start_ms - w.t0_ms) * w.sample_rate / 1000), round((t.end_ms - w.t0_ms) * w.sample_rate / 1000)
                acc += normalize(embedder.embed(w.samples[a:b], w.sample_rate)) * t.duration_ms
                timing.speech_s += t.duration_ms / 1000
            if np.any(acc):
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
        acc = np.zeros(embedder.dim, np.float32)
        total = 0
        for w in iter_windows(self.store, self.meeting_id, "L", self.config.diar_window_s, 0, silence_rms=self.config.silence_rms,
                              cut_on_silence=False):
            if w.idx in done:
                header, blob = unpack_artifact(self.store.jobs.get_artifact(self.job_id, EMBED_L_ARTIFACT, w.idx) or b"")
                if header.get("ms"):
                    acc += np.frombuffer(blob, dtype="<f4").astype(np.float32) * header["ms"]
                    total += int(header["ms"])
                continue
            self._between_windows()
            t0 = time.perf_counter()
            vec, ms = self._embed_runs(w, embedder)
            timing.wall_s += time.perf_counter() - t0
            timing.speech_s += ms / 1000
            self.store.jobs.put_artifact(self.job_id, EMBED_L_ARTIFACT, w.idx, pack_artifact({"ms": ms}, vec.tobytes() if ms else b""))
            if ms:
                acc += vec * ms
                total += ms
        if total and np.any(acc):
            self.store.profiles.confirm(int(self.me["id"]), embedder.model, normalize(acc), None, self.meeting_id,
                                        platform=self.meeting.get("platform"), source=EmbeddingSource.AUTO)

    def _embed_runs(self, w: AudioWindow, embedder: Any, min_run_ms: int = 1500, limit: int = 20) -> tuple[np.ndarray, int]:
        """Trechos contíguos com fala (RMS por quadro) do canal L, os mais longos primeiro."""
        if not w.has_speech:
            return np.zeros(embedder.dim, np.float32), 0
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
        acc = np.zeros(embedder.dim, np.float32)
        total = 0
        for a, b in runs[:limit]:
            ms = (b - a) * 20
            acc += normalize(embedder.embed(w.samples[a * frame:b * frame], w.sample_rate)) * ms
            total += ms
        return (normalize(acc) if total else acc), total

    def _match(self, channel: str, windows: list[DiarizationWindow]) -> tuple[tuple[Turn, ...], dict[str, int], list[ClusterRow]]:
        """Costura, centroides globais, casamento com os perfis e gravação dos clusters (a revisão abre aqui). Na retomada
        depois desta etapa, os clusters do job (talvez já confirmados pelo usuário) são mantidos e só a costura é relida."""
        step = JobStep.MATCH
        self._begin_step(step)
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
        matcher = ProfileMatcher(self.config.threshold, self.config.margin, expected=expected)
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
        done = self.store.jobs.artifact_indexes(self.job_id, step)
        out: list[TranscriptWindow] = []
        total_ms = max(1, int(self.meeting.get("duration_ms") or 1))
        previous: tuple[Word, ...] = ()
        for w in iter_windows(self.store, self.meeting_id, channel, self.config.whisper_window_s, self.config.whisper_overlap_s,
                              silence_rms=self.config.silence_rms, cut_on_silence=True):
            if w.idx in done:
                tw = decode_transcript(self.store.jobs.get_artifact(self.job_id, step, w.idx) or b"")
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
                prompt = build_prompt(self.config.glossary, self.meeting.get("expected_participants"), self.meeting.get("title"), previous)
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
        if self.store.segments.count_segments(self.meeting_id, self.job_id):
            self._end_step(step)                                       # retomada depois de gravar: nada a refazer
            return
        t0 = time.perf_counter()
        segmenter = Segmenter()
        rows: list[SegmentInsert] = []
        people = {int(p["id"]): p for p in self.store.people.list_people()}
        for channel, windows in transcripts.items():
            words = merge_words(windows)
            if not words:
                continue
            if channel in clusters:
                turns, ids, crows = clusters[channel]
                by_id = {int(c["id"]): c for c in crows}
                attribution = Attribution.PYANNOTE
            else:
                duration = int(self.meeting.get("duration_ms") or words[-1].end_ms)
                turns, attribution = (Turn(0, max(duration, words[-1].end_ms), USER_CLUSTER),), Attribution.CHANNEL
                ids = self._user_cluster(words)
                by_id = {int(c["id"]): c for c in self.store.segments.list_clusters(self.meeting_id, self.job_id)
                         if c["channel"] == channel}
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
        for oc in decided:
            nid = pairs.get(int(oc["id"]))
            if nid is None:
                self.store.add_revision("cluster", int(oc["id"]), "transporte", f"{oc['status']}:{oc.get('person_id')}", None, "sistema")
                continue
            mi = oc.get("merged_into")
            merged = pairs.get(int(mi)) if mi is not None else None
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
