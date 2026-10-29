"""F5.6 L3 (U-093), o núcleo da exclusão (`pipeline/delete.py`) e o espaço de volta (`store/space.py`): cancela os pendentes
na hora e pede o dos que rodam, espera o que roda com tempo limite e apaga mesmo assim (A-36), recusa a reunião gravando ou
pausada (A-41), várias reuniões com falha no meio (A-35), a liberação incremental em passos parada pela gravação, o
`VACUUM` inteiro recusado no mês corrente, gravando, com worker vivo ou sem disco, o pendente para a próxima abertura e a
interrupção sem corromper (A-38, A-39); e o worker de pé com a reunião excluída no meio do job, com a chamada paga gravada
sem a reunião (F5A-17, A-37)."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

import numpy as np
import pytest

from listener.capture.audio import encode_flac
from listener.llm.fake import FakeProvider
from listener.llm.fake_scripts import DEFAULTS
from listener.llm.types import LlmTask
from listener.model import JobKind, JobStatus, MeetingStatus
from listener.pipeline import delete
from listener.pipeline.llm_steps import LlmJobRunner
from listener.pipeline.runner import JobRunner
from listener.store import Store, space
from listener.store.segments import SegmentInsert
from tests.fake_pipeline import FakeDeps
from tests.test_rag_index import _pipeline_job
from tests.test_runner import CFG

STEP = 200_000                       # o passo do incremental nos testes (o app usa 10 MB)


class Clock:
    """Relógio e `sleep` falsos: dormir anda o relógio (a espera de 15 s roda sem esperar)."""

    def __init__(self, on_sleep: Any = None):
        self.t = 0.0
        self.slept = 0
        self.on_sleep = on_sleep

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s
        self.slept += 1
        if self.on_sleep is not None:
            self.on_sleep(self.slept)


def _meeting(store: Store, title: str = "r", chunks: int = 0) -> int:
    mid = store.create_meeting(title, None, None, None)
    _meeting_chunks(store, mid, chunks)
    store.finish_meeting(mid, 60_000)
    return mid


def _meeting_chunks(store: Store, mid: int, n: int, samples: int = 50_000) -> None:
    """Pedaços de ruído (o FLAC fica perto dos 16 bits crus: ~100 KB cada, quase 4 páginas de 32 KB)."""
    rng = np.random.default_rng(mid)
    for i in range(n):
        x = (rng.standard_normal(samples) * 0.2).astype(np.float32)
        store.insert_chunk(mid, "R", i, i * 4000, len(x), 16000, 0, encode_flac(x, 16000))


def _never() -> bool:
    return False


def test_pending_jobs_cancel_now_the_running_one_is_waited_with_a_time_limit_and_the_meeting_goes(store):
    mid = _meeting(store)
    running, pending = store.jobs.enqueue(mid), store.jobs.enqueue(mid, JobKind.LLM)
    assert store.jobs.claim(pid=7, kinds=("meeting",))["id"] == running
    cancelled, held = delete.cancel_jobs(store, mid)
    assert (cancelled, held) == (2, [running])
    assert store.jobs.get(pending)["status"] == JobStatus.CANCELLED and store.jobs.get(running)["cancel_requested"] == 1
    clock = Clock()
    out = delete.delete_meeting(store, mid, wait_s=15.0, sleep=clock.sleep, clock=clock.now)
    assert out.deleted and out.still_running == 1 and clock.t >= 15.0 and store.get_meeting(mid) is None   # apagou mesmo assim
    # o job que atende o cancelamento entre janelas: a espera acaba antes do prazo
    mid = _meeting(store)
    job = store.jobs.enqueue(mid)
    store.jobs.claim(pid=8, kinds=("meeting",))
    clock = Clock(lambda n: store.jobs.finish(job, JobStatus.CANCELLED) if n == 3 else None)
    out = delete.delete_meeting(store, mid, wait_s=15.0, sleep=clock.sleep, clock=clock.now)
    assert out.deleted and out.still_running == 0 and out.cancelled == 1 and clock.slept == 3 and clock.t < 1.0


def test_a_recording_or_paused_meeting_is_refused_with_the_reason(store):
    for status in (MeetingStatus.RECORDING, MeetingStatus.PAUSED):
        mid = store.create_meeting("gravando", None, None, None)
        store.meetings.set_status(mid, status)
        job = store.jobs.enqueue(mid)
        out = delete.delete_meeting(store, mid)
        assert not out.deleted and out.message == delete.RECORDING_TEXT and store.get_meeting(mid) is not None
        assert store.jobs.get(job)["status"] == JobStatus.PENDING                  # nada cancelado
    assert delete.refusal(store, 99_999) == delete.GONE_TEXT


def test_many_meetings_one_at_a_time_and_a_failure_in_the_middle_keeps_it_listed(store, monkeypatch):
    mids = [_meeting(store, f"m{i}", chunks=2) for i in range(3)]
    real = store.delete_meeting

    def flaky(mid: int) -> None:
        if mid == mids[1]:
            raise sqlite3.OperationalError("database is locked")
        real(mid)

    monkeypatch.setattr(store, "delete_meeting", flaky)
    seen: list[tuple[int, int]] = []
    out = delete.delete_many(store, mids, progress=lambda n, total: seen.append((n, total)))
    assert seen == [(1, 3), (2, 3), (3, 3)] and [o.deleted for o in out] == [True, False, True]
    assert "OperationalError" in out[1].message and store.get_meeting(mids[1]) is not None
    assert [m["id"] for m in store.list_meetings()] == [mids[1]]
    monkeypatch.undo()
    assert delete.delete_many(store, [mids[1]])[0].deleted and store.list_meetings() == [] and store.orphan_report() == {}
    stop = delete.delete_many(store, [_meeting(store)], should_stop=lambda: True)          # a saída do app: para antes
    assert stop[0].message == delete.STOPPED_TEXT and len(store.list_meetings()) == 1


def _free(store: Store, **kw: Any) -> list[space.SpaceResult]:
    kw.setdefault("recording", _never)
    return delete.free_pending(store, step_bytes=STEP, **kw)


def _freelist(path: Any) -> int:
    with closing(space.open_audio(path)) as c:
        return int(c.execute("PRAGMA freelist_count").fetchone()[0])


def test_incremental_files_free_space_in_steps_and_stop_for_a_recording(store):
    current = store.base_dir / store.core.current_audio_file()
    keep, gone = _meeting(store, "fica", chunks=4), _meeting(store, "sai", chunks=12)
    store.delete_meeting(gone)
    assert space.pending(store.core) == [current.name] and _freelist(current) > 3 * STEP // 32768   # mais de três passos
    assert _free(store, recording=lambda: True) == [] and space.pending(store.core) == [current.name]   # gravando: nem começa
    checks = iter([False, False, False])                    # o arquivo e dois passos; depois, a gravação começa
    res = _free(store, recording=lambda: next(checks, True))
    assert res[0].outcome == space.SpaceOutcome.PENDING and res[0].reason == "gravação em curso" and res[0].mode == "incremental"
    assert space.pending(store.core) == [current.name] and _freelist(current) > 0
    before = space.file_bytes(current)
    res = _free(store)
    assert res[0].outcome == space.SpaceOutcome.FREED and _freelist(current) == 0 and space.pending(store.core) == []
    assert space.file_bytes(current) < before and len(store.list_chunks(keep)) == 4
    with closing(space.open_audio(current)) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert _free(store) == []                                                  # nada pendente: nada a fazer


def _old_file_meeting(store: Store, chunks: int = 6) -> tuple[int, Any]:
    """Uma reunião de 2025 num arquivo antigo, sem `auto_vacuum` (anterior à F5.6)."""
    name = "listener-audio-2025-01.db"
    store.core.attach(name)
    with closing(space.open_audio(store.base_dir / name)) as c:
        c.execute("PRAGMA auto_vacuum = NONE")
        c.execute("VACUUM")
        assert c.execute("PRAGMA auto_vacuum").fetchone()[0] == 0
    mid = store.create_meeting("antiga", None, None, None)
    store._x("UPDATE meetings SET audio_file = ?, started_at = '2025-01-10T10:00:00.000-03:00' WHERE id = ?", (name, mid))
    store.core.forget_meeting(mid)
    _meeting_chunks(store, mid, chunks)
    store.finish_meeting(mid, 60_000)
    store.core.release_audio()                         # como a interface depois de excluir (R-02): nenhum anexo dele no app
    return mid, store.base_dir / name


def test_full_vacuum_only_off_the_current_month_without_recording_worker_or_disk_and_stays_pending(store, monkeypatch):
    mid, path = _old_file_meeting(store)
    job = store.jobs.enqueue(_meeting(store, "viva"))
    store.delete_meeting(mid)
    assert space.pending(store.core) == [path.name] and _freelist(path) > 0
    monkeypatch.setattr(store.core, "current_audio_file", lambda: path.name)          # como se fosse o mês corrente
    assert _free(store)[0].reason == "arquivo do mês corrente, sem liberação incremental"
    monkeypatch.undo()
    store.jobs.claim(pid=9, kinds=("meeting",))                                       # um worker vivo segura um job
    assert _free(store)[0].reason == "processamento em curso"
    store.jobs.finish(job, JobStatus.DONE)
    assert _free(store, disk_free=lambda _p: 0)[0].reason == "disco livre abaixo do dobro do arquivo"
    g = space.free_file(path, current=False, stop=_never, busy=lambda: True, worker_alive=_never)
    assert g.outcome == space.SpaceOutcome.PENDING and g.reason == "gravação em curso" and space.pending(store.core) == [path.name]
    store.core.attach(path.name)                                                       # R-02: anexado no app, nada de VACUUM
    assert store.core.files.users(path.name) == 1 and _free(store)[0].reason == space.ATTACHED_TEXT
    store.core.release_audio()
    assert store.core.files.users(path.name) == 0
    res = _free(store)
    assert res[0].outcome == space.SpaceOutcome.FREED and res[0].mode == "vacuum" and res[0].freed_bytes > 0
    with closing(space.open_audio(path)) as c:                                         # sai incremental: a próxima é em passos
        assert c.execute("PRAGMA auto_vacuum").fetchone()[0] == space.INCREMENTAL
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert space.pending(store.core) == [] and space.free_file(store.base_dir / "x.db", current=False, stop=_never, busy=_never,
                                                               worker_alive=_never).outcome == space.SpaceOutcome.MISSING


def test_an_interrupted_vacuum_corrupts_nothing_and_stays_pending(store):
    mid, path = _old_file_meeting(store)
    store.delete_meeting(mid)
    armed = {"on": False}

    def hold(conn: sqlite3.Connection | None) -> None:          # o `interrupt()` da saída, deterministicamente: o SQLite para
        if conn is not None:                                    # o VACUUM no meio, como o `sqlite3_interrupt` de outra thread
            conn.set_progress_handler(lambda: 1 if armed["on"] else 0, 1)

    def disk(_p: Any) -> int:
        armed["on"] = True                                      # logo antes do VACUUM
        return 10**15

    res = _free(store, on_conn=hold, disk_free=disk)
    assert res[0].outcome == space.SpaceOutcome.PENDING and res[0].reason == "interrompido" and space.pending(store.core) == [path.name]
    with closing(space.open_audio(path)) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and c.execute("PRAGMA auto_vacuum").fetchone()[0] == 0
    assert _free(store, should_stop=lambda: True) == [] and space.pending(store.core) == [path.name]   # parada: nem começa
    assert _free(store)[0].outcome == space.SpaceOutcome.FREED                                          # a próxima abertura


# ---------------------------------------------------------------- revisão do L3 (R-01 a R-10)
def _reader(path: Any) -> sqlite3.Connection:
    """Uma conexão com mapa de memória que leu o arquivo inteiro, como o worker ocioso (papel WORKER, 256 MB)."""
    c = sqlite3.connect(str(path), autocommit=True)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")                       # as páginas no arquivo (não no WAL): o mapa as cobre
    c.execute("PRAGMA mmap_size = 268435456")
    assert sum(len(r[0]) for r in c.execute("SELECT flac FROM audio_chunks")) > 0     # lê os blobs: o arquivo fica mapeado
    return c


def test_freed_only_when_the_file_shrank_and_the_retry_truncates_with_an_empty_freelist(store):
    """R-01: o arquivo que não encolheu (no Windows, o mapa de outra conexão impede o truncamento sem erro) fica pendente; a
    nova tentativa, com a lista de páginas livres já vazia, faz uma escrita mínima e o checkpoint encolhe o arquivo."""
    import sys

    current = store.base_dir / store.core.current_audio_file()
    _meeting(store, "fica", chunks=4)
    gone = _meeting(store, "sai", chunks=12)
    reader = _reader(current) if sys.platform == "win32" else None
    store.delete_meeting(gone)
    res = _free(store)
    if reader is not None:
        assert res[0].outcome == space.SpaceOutcome.PENDING and res[0].reason == space.HELD_TEXT and _freelist(current) == 0
        assert space.pending(store.core) == [current.name]
        reader.close()
        res = _free(store)
        assert res[0].mode == "truncar"
    assert res[0].outcome == space.SpaceOutcome.FREED and space.pending(store.core) == []
    with closing(space.open_audio(current)) as c:
        content = c.execute("PRAGMA page_count").fetchone()[0] * c.execute("PRAGMA page_size").fetchone()[0]
    assert current.stat().st_size == content
    with current.open("ab") as f:                       # o truncamento de antes não aconteceu (qualquer sistema): sobra no fim
        f.write(bytes(65536))
    space.add_pending(store.core, current.name)
    res = _free(store)
    assert (res[0].outcome, res[0].mode) == (space.SpaceOutcome.FREED, "truncar") and current.stat().st_size == content
    space.add_pending(store.core, current.name)
    assert _free(store)[0].outcome == space.SpaceOutcome.NOTHING and space.pending(store.core) == []


def test_the_full_vacuum_never_runs_on_a_file_the_app_has_attached_and_attaching_interrupts_it(store, monkeypatch):
    """R-02: anexado numa conexão do app (o `BEGIN IMMEDIATE` dela travaria 5 s), o VACUUM inteiro fica pendente; anexar o
    arquivo no meio do VACUUM o interrompe. O registro conta por conexão e a saída de uma thread solta os anexos dela."""
    import threading

    from contextlib import contextmanager

    mid, path = _old_file_meeting(store)
    store.delete_meeting(mid)
    monkeypatch.setattr(space, "PROGRESS_STEPS", 1)
    files = store.core.files

    @contextmanager
    def racing(halt: Any) -> Any:                       # alguém abre uma reunião daquele mês logo antes de o VACUUM começar
        with files.exclusive(path.name, halt) as alone:
            store.core.attach(path.name)
            yield alone

    res = space.free_file(path, current=False, stop=_never, busy=_never, worker_alive=_never, exclusive=racing)
    assert res.outcome == space.SpaceOutcome.PENDING and res.reason == "interrompido"       # o pedido não se perdeu
    with closing(space.open_audio(path)) as c:
        assert c.execute("PRAGMA auto_vacuum").fetchone()[0] == 0 and c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    done = threading.Event()
    files, done = store.core.files, threading.Event()
    assert files.users(path.name) == 1 and _free(store)[0].reason == space.ATTACHED_TEXT

    def other_thread() -> None:
        store.core.attach(path.name)
        assert files.users(path.name) == 2
        store.core.close_thread()
        done.set()

    threading.Thread(target=other_thread).start()
    assert done.wait(5) and files.users(path.name) == 1
    store.core.release_audio()
    after = _free(store)
    assert files.users(path.name) == 0 and after[0].outcome == space.SpaceOutcome.FREED and after[0].mode == "vacuum"


def test_the_legacy_file_waits_for_its_full_vacuum_unattached_at_the_opening(tmp_path):
    """R-02: o legado (`listener-audio.db`), pendente, não é anexado na abertura; a liberação da abertura ociosa o pega sem
    anexo nenhum. Sem pendência, a abertura o anexa como antes."""
    from listener.store.core import LEGACY_AUDIO_FILE

    with Store(tmp_path) as st:
        st.core.attach(LEGACY_AUDIO_FILE)
        legacy = tmp_path / LEGACY_AUDIO_FILE
        with closing(space.open_audio(legacy)) as c:
            c.execute("PRAGMA auto_vacuum = NONE")
            c.execute("VACUUM")
        mid = st.create_meeting("antiga", None, None, None)
        st._x("UPDATE meetings SET audio_file = ? WHERE id = ?", (LEGACY_AUDIO_FILE, mid))
        st.core.forget_meeting(mid)
        _meeting_chunks(st, mid, 6)
        st.finish_meeting(mid, 60_000)
        st.delete_meeting(mid)
        assert space.pending(st.core) == [LEGACY_AUDIO_FILE]
    with Store(tmp_path) as st:
        assert st.core.files.users(LEGACY_AUDIO_FILE) == 0 and st.orphan_report() == {}   # nem a varredura dos órfãos anexa
        res = {r.file: r for r in _free(st)}
        assert res[LEGACY_AUDIO_FILE].outcome == space.SpaceOutcome.FREED and res[LEGACY_AUDIO_FILE].mode == "vacuum"
        assert space.pending(st.core) == []
    with Store(tmp_path) as st:
        assert st.core.files.users(LEGACY_AUDIO_FILE) == 1


def test_a_stop_during_the_vacuum_interrupts_it_even_after_the_last_check(store, monkeypatch):
    """R-08: o pedido de parada que chega entre a última conferência e o VACUUM (o `interrupt()` antes de o VACUUM começar
    não tem efeito) é lido pelo SQLite a cada `PROGRESS_STEPS` instruções."""
    mid, path = _old_file_meeting(store)
    store.delete_meeting(mid)
    monkeypatch.setattr(space, "PROGRESS_STEPS", 1)                          # o arquivo do teste é pequeno (o app usa 100)
    flag = {"stop": False}

    def disk(_p: Any) -> int:                            # a última conferência passou; a parada chega agora
        flag["stop"] = True
        return 10**15

    res = space.free_file(path, current=False, stop=lambda: flag["stop"], busy=_never, worker_alive=_never, disk_free=disk)
    assert res.outcome == space.SpaceOutcome.PENDING and res.reason == "interrompido"
    with closing(space.open_audio(path)) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and c.execute("PRAGMA auto_vacuum").fetchone()[0] == 0


def test_a_job_born_during_the_wait_is_cancelled_too(store):
    """R-03: o pesado conclui na espera e enfileira o `llm`, que o leve pega na hora: a volta seguinte da espera também pede
    o cancelamento dele (antes das chamadas pagas)."""
    mid = _meeting(store)
    heavy = store.jobs.enqueue(mid)
    store.jobs.claim(pid=1, kinds=("meeting",))
    seen: dict[str, int] = {}

    def on_sleep(n: int) -> None:
        if n == 2:
            store.jobs.finish(heavy, JobStatus.DONE)
            seen["llm"] = store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["identify", "minutes"]})
            store.jobs.claim(pid=2, kinds=("llm",))
        if n == 4:
            seen["cancel"] = int(store.jobs.get(seen["llm"])["cancel_requested"])

    clock = Clock(on_sleep)
    out = delete.delete_meeting(store, mid, wait_s=15.0, sleep=clock.sleep, clock=clock.now)
    assert seen["cancel"] == 1 and out.cancelled == 2 and out.deleted


def test_enqueueing_for_a_deleted_meeting_returns_none_and_the_sweep_goes_on(store, monkeypatch):
    """R-05: a varredura lê as reuniões no começo; uma excluída antes do `enqueue_index` não aborta a varredura (as outras
    entram na fila); os runners encadeiam o `llm` e o `index` sem erro com a reunião excluída."""
    from listener.rag import index

    mids = [_meeting(store, t) for t in ("a", "b", "c")]

    def failed() -> list[int]:
        store.delete_meeting(mids[0])
        return list(mids)

    monkeypatch.setattr(store.jobs, "failed_index_meetings", failed)
    assert index.sweep(store) == 2
    assert {int(j["meeting_id"]) for j in store.jobs.active()} == set(mids[1:])
    assert store.jobs.enqueue_index(mids[0], "llm") is None and store.jobs.enqueue_if_meeting(mids[0], JobKind.LLM) is None
    assert store.jobs.enqueue_if_meeting(mids[1], JobKind.LLM, {"tasks": ["minutes"]}) is not None


def test_stopping_before_or_during_the_wait_says_what_happened(store):
    """R-06: parado antes de tocar nos jobs, nada mudou; parado na espera, os jobs já foram cancelados e a mensagem diz (e
    como refazer)."""
    mid = _meeting(store)
    running, pending = store.jobs.enqueue(mid), store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"]})
    store.jobs.claim(pid=1, kinds=("meeting",))
    out = delete.delete_meeting(store, mid, should_stop=lambda: True)
    assert out.message == delete.STOPPED_TEXT and store.jobs.get(pending)["status"] == JobStatus.PENDING
    assert store.jobs.get(running)["cancel_requested"] == 0
    clock = Clock(lambda _n: None)                       # o app começa a sair durante a espera (no primeiro sono)
    out = delete.delete_meeting(store, mid, should_stop=lambda: clock.slept > 0, sleep=clock.sleep, clock=clock.now)
    assert not out.deleted and out.message == delete.CANCELLED_TEXT and "Reprocessar" in out.message
    assert store.get_meeting(mid) is not None and store.jobs.get(pending)["status"] == JobStatus.CANCELLED


def test_the_event_and_the_paid_call_reraise_when_the_meeting_still_exists(store, monkeypatch):
    """R-10: a chave estrangeira só é engolida com a reunião excluída; com ela viva, outro erro de integridade sobe."""
    from listener.model import EventType, LlmCallStatus
    from listener.store.llm_calls import LlmCallInsert

    mid = _meeting(store)
    job = store.jobs.enqueue(mid)
    runner = JobRunner(store, FakeDeps(), store.jobs.get(job), config=CFG, heartbeat_thread=False)

    def broken(*_a: Any, **_k: Any) -> int:
        raise sqlite3.IntegrityError("CHECK constraint failed")

    monkeypatch.setattr(store, "add_event", broken)
    with pytest.raises(sqlite3.IntegrityError):
        runner._event(EventType.JOB_STEP, {"job_id": job})
    bad = LlmCallInsert(mid, None, None, "fake", "m", None, None, 0, 0, 0, 0, None, 0, LlmCallStatus.OK)  # type: ignore[arg-type]
    tries: list[Any] = []
    real = store.llm_calls._insert

    def counted(call: LlmCallInsert, sp: bool) -> int:
        tries.append(call.meeting_id)
        return real(call, sp)

    monkeypatch.setattr(store.llm_calls, "_insert", counted)
    with pytest.raises(sqlite3.IntegrityError):                                     # NOT NULL em `task`, reunião viva
        store.llm_calls.insert(bad)
    assert tries == [mid]                                                            # sem a segunda tentativa sem a reunião


# ---------------------------------------------------------------- F5A-17 (A-37): o worker de pé
def _processed(store: Store) -> tuple[int, int]:
    mid = store.create_meeting("Planejamento", None, None, None)
    store.finish_meeting(mid, 30_000)
    job = _pipeline_job(store, mid)
    store.segments.insert_segments([SegmentInsert(mid, job, "R", 0, 4000, "vamos fechar o orçamento", "pyannote", "final"),
                                    SegmentInsert(mid, job, "R", 4000, 8000, "corte de oito por cento", "pyannote", "final")])
    return mid, job


@pytest.mark.parametrize("where", ["_between_windows", "_segments"])
def test_the_heavy_runner_survives_the_meeting_deleted_mid_job(platform, store, monkeypatch, where):
    """Excluída no ponto de controle entre janelas: ele acha o job sumido e cancela; excluída antes de gravar as falas: a
    chave estrangeira falha e o erro fica sem evento. Antes da F5.6, o evento do `_fail`/`_cancelled` derrubava o worker."""
    from tests.conftest import make_config
    from listener.capture.recorder import Recorder

    mid = store.create_meeting("r", "meeting", None, None)
    rec = Recorder(platform, store, mid, make_config(), clock=platform.clock, inline_worker=True)
    rec.start()
    platform.pump(1.5)
    rec.stop()
    original = getattr(JobRunner, where)

    def gone_then(self: JobRunner, *a: Any, **kw: Any) -> Any:
        if self.store.get_meeting(self.meeting_id) is not None:
            self.store.delete_meeting(self.meeting_id)
        return original(self, *a, **kw)

    monkeypatch.setattr(JobRunner, where, gone_then)
    store.jobs.enqueue(mid)
    job = store.jobs.claim(pid=1, kinds=("meeting",))
    status = JobRunner(store, FakeDeps(), job, config=CFG, heartbeat_thread=False).run()
    assert status == (JobStatus.CANCELLED if where == "_between_windows" else JobStatus.ERROR) and store.get_meeting(mid) is None
    assert store._q("SELECT COUNT(*) FROM events WHERE meeting_id = ?", (mid,))[0][0] == 0


def test_the_llm_call_paid_while_the_meeting_was_deleted_stays_in_the_month_cost(store):
    mid, parent = _processed(store)
    store.providers.update_provider("gemini", api_key="AIzaSy-chave-de-teste-ABCDEFGH12345678", fast_model="fake-fast",
                                    strong_model="fake-strong")
    store.providers.set_price("gemini", "fake-strong", 1.25, 10.0, 0.31)
    fake = FakeProvider()

    def minutes(req: Any) -> Any:                           # a reunião sai do Histórico enquanto a LLM responde
        store.delete_meeting(mid)
        return DEFAULTS[LlmTask.MINUTES](req)

    fake.script(LlmTask.MINUTES, minutes)
    store.jobs.enqueue(mid, JobKind.LLM, {"tasks": ["minutes"], "parent_job_id": parent})
    from listener.pipeline import llm_steps

    job = store.jobs.claim(pid=2, config_json=llm_steps.llm_config_snapshot(store), kinds=("llm",))
    status = LlmJobRunner(store, job, provider=fake, heartbeat_thread=False).run()
    assert status == JobStatus.ERROR and store.get_meeting(mid) is None
    calls = store._q("SELECT meeting_id, cost_usd, request_blob FROM llm_calls WHERE task = 'minutes'")
    assert len(calls) == 1 and calls[0]["meeting_id"] is None and calls[0]["cost_usd"] and calls[0]["request_blob"] is None
    assert store.llm_calls.month_total() >= float(calls[0]["cost_usd"])


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock)
