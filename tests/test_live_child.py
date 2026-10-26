"""F4 L2, lado do filho: segmentador de janelas, canal (continuidade, silêncio marcado, reamostragem), fila única, máquina
de degradação, idioma, filtros e alucinações, e a sessão inteira em processo (stdin e stdout em BytesIO, banco temporário,
motores fake). O subprocesso real `--live` em modo fake fica em `test_live_process_end_to_end` (um só)."""
from __future__ import annotations

import io
import itertools
import sys
from pathlib import Path

import numpy as np
import pytest

from listener.live import proc
from listener.live import protocol as pr
from listener.live.vad import FRAME, SAMPLE_RATE, Segmenter, SegmenterConfig, WindowSpan
from listener.store import Store
from tests import conftest
from tests.fake_live import EnergyVad, FakeEngines, FakeTranscriber, session_input, tone

MS = SAMPLE_RATE // 1000
SR = SAMPLE_RATE


def frames_of(ms: int) -> int:
    return ms * MS // FRAME


def test_segmenter_closes_on_silence_long_speech_pause_and_hard_cut():
    seg = Segmenter.starting_at(0)
    out = seg.push(0, [1.0] * frames_of(2000) + [0.0] * frames_of(1300))            # 2 s de fala, 1,3 s de silêncio
    assert len(out) == 1 and seg.open_start is None
    w = out[0]
    assert w.start == 0 and w.speech == frames_of(2000) * FRAME and w.end == frames_of(2000) * FRAME + 300 * MS
    seg = Segmenter.starting_at(0)
    assert seg.push(0, [1.0] * frames_of(3000) + [0.0] * frames_of(600)) == []      # 3 s de fala: pausa de 600 ms não fecha
    out = seg.push(seg.cursor, [1.0] * frames_of(1500) + [0.0] * frames_of(600))    # passou de 4 s: 500 ms de pausa fecham
    assert len(out) == 1 and out[0].speech == (frames_of(3000) + frames_of(1500)) * FRAME
    seg = Segmenter.starting_at(0)
    spans = seg.push(0, [1.0] * frames_of(25_000)) + seg.flush()                   # fala contínua: cortes duros de 10 s
    assert [round((w.end - w.start) / SAMPLE_RATE, 1) for w in spans] == [10.0, 10.0, 5.0] and seg.cuts == 2
    assert all(a.end == b.start for a, b in itertools.pairwise(spans))              # contíguas: sem pré-rolagem repetida
    seg = Segmenter.starting_at(0)
    assert seg.push(0, [0.0] * 20 + [1.0] * 9 + [0.0] * 60) == [] and seg.short == 1  # 288 ms de fala: descartada


def test_segmenter_preroll_hysteresis_silence_marks_and_jumps():
    cfg = SegmenterConfig()
    seg = Segmenter.starting_at(16000, cfg)
    out = seg.push(16000, [0.0] * 30 + [0.6] + [0.4] * 30 + [0.2] * 50)             # 0,4 depois de fala ainda é fala (histerese)
    assert len(out) == 1 and out[0].start == 16000 + 30 * FRAME - 200 * MS and out[0].speech == 31 * FRAME
    seg = Segmenter.starting_at(0, cfg)
    seg.push(0, [1.0] * frames_of(1000))
    out = seg.silence(seg.cursor, 10 * 60 * SAMPLE_RATE)                            # pausa de 10 min marcada: O(1)
    assert len(out) == 1 and out[0].end == frames_of(1000) * FRAME + 300 * MS and seg.cursor == frames_of(1000) * FRAME + 9_600_000
    seg = Segmenter.starting_at(0, cfg)
    seg.push(0, [1.0] * frames_of(1000))
    out = seg.push(seg.cursor + 2 * SAMPLE_RATE, [0.0])                             # salto à frente conta como silêncio
    assert len(out) == 1 and seg.floor == out[0].end
    seg = Segmenter.starting_at(0, cfg)
    seg.push(0, [1.0] * frames_of(1000))
    assert seg.silence(0, 100) == [] and seg.cursor == frames_of(1000) * FRAME   # silêncio já coberto: ignorado
    assert WindowSpan(16000, 48000, 1).start_ms == 1000 and WindowSpan(16000, 48000, 1).end_ms == 3000


def _audio(channel: str, start: int, x: np.ndarray, rate: int = SAMPLE_RATE, flags: int = 0) -> pr.AudioFrame:
    pcm = np.clip(np.rint(x * 32767), -32767, 32767).astype("<i2").tobytes()
    return pr.AudioFrame(channel, start, rate, len(x), flags, pcm)


def test_channel_stream_windows_keep_the_audio_and_marked_silence_costs_nothing():
    s = proc.ChannelStream("R", EnergyVad())
    sig = tone(1.5)
    out = []
    for i in range(0, len(sig), 1600):
        out += s.feed(_audio("R", 16000 + i, sig[i:i + 1600]))                     # canal começa em 1 s da sessão
    assert out == [] and s.window_open and s.pos == 16000 + len(sig)
    out += s.feed(pr.AudioFrame("R", 16000 + len(sig), SAMPLE_RATE, 10 * 60 * SAMPLE_RATE, pr.GAP))   # 10 min de buraco
    assert len(out) == 1 and not s.window_open and s.pos == 16000 + len(sig) + 9_600_000
    w = out[0]
    assert w.start == 16000 and w.start_ms == 1000 and w.speech == 46 * FRAME and w.end == 16000 + 46 * FRAME + 300 * MS
    assert np.allclose(w.samples[:len(sig)], sig, atol=1e-4) and not w.samples[len(sig):].any()
    assert sum(len(a) for _, a in s._chunks) <= proc.SILENCE_FILL_MS * MS            # nada de 10 min de zeros na memória
    out = s.feed(_audio("R", 16000 + len(sig) + 9_600_000 + 32000, tone(0.8)))       # 2 s perdidos (descarte do app)
    assert s.lost_frames == 32000 and out == []
    out = s.feed(_audio("R", 16000 + len(sig) + 9_600_000 + 32000, tone(0.8)))       # repetido: já coberto, ignorado
    assert out == [] and s.flush()[0].speech >= 12 * FRAME


def test_channel_stream_resamples_and_trims_overlap():
    s = proc.ChannelStream("L", EnergyVad())
    x48 = tone(1.0, rate=48000)
    out = []
    for i in range(0, len(x48), 4800):
        out += s.feed(_audio("L", 48000 + i, x48[i:i + 4800], rate=48000))
    out += s.feed(pr.AudioFrame("L", 48000 + len(x48), 48000, 48000 * 2, pr.SILENCED))
    assert len(out) == 1 and out[0].start_ms == 1000 and 2280 <= out[0].end_ms <= 2320 and s.rate == 48000   # sem áudio antes de 1 s
    assert len(out[0].samples) == out[0].end - out[0].start
    seg = s.feed(_audio("L", 48000 * 4 - 4800, tone(0.3, rate=48000)))              # sobreposição de 100 ms: só a parte nova
    assert seg == [] and s.next_in == 48000 * 4 - 4800 + 14400


def test_window_queue_oldest_first_and_prune_keeps_newest_per_channel():
    q = proc.WindowQueue()
    def w(ch, end):
        return proc.LiveWindow(ch, end - 100, end, 50, np.zeros(1, np.float32))
    items = [w("L", 300), w("R", 100), w("L", 500), w("R", 400), w("R", 700)]
    for it in items:
        q.put(it)
    first = q.take(0.0)
    assert first is items[1] and len(q) == 4                                        # fechou antes, sai antes (canal tanto faz)
    survives, dropped = q.prune(first)
    assert not survives and dropped == 3 and len(q) == 2                            # fica o L 500 e o R 700
    assert [q.take(0.0).end, q.take(0.0).end] == [500, 700] and q.take(0.01) is None
    q.put(w("L", 900))
    survives, dropped = q.prune(q.take(0.0))
    assert survives and dropped == 0
    q.put(w("R", 1))
    q.close()
    assert q.drain()[0].end == 1 and q.take(0.0) is None and q.closed


def test_degrade_machine_steps_after_holding_and_never_returns():
    t = {"now": 0.0}
    m = proc.DegradeMachine(lambda: t["now"])
    assert m.update(25.0) is None                                                    # começa a contar
    t["now"] = 29.0
    assert m.update(25.0) is None
    t["now"] = 29.5
    assert m.update(10.0) is None and m.over_since is None                          # voltou abaixo: zera
    t["now"] = 40.0
    m.update(21.0)
    t["now"] = 70.0
    assert m.update(21.0) == proc.Stage.DEGRADED and not m.skipping(50.0)
    t["now"] = 99.0
    assert m.update(30.0) is None
    t["now"] = 100.0
    assert m.update(30.0) == proc.Stage.SKIPPING and m.skipping(21.0) and not m.skipping(19.0)
    t["now"] = 500.0
    assert m.update(5.0) is None and m.update(50.0) is None and m.stage == proc.Stage.SKIPPING   # nunca volta sozinho


def test_language_lock_filters_and_prompt():
    lang = proc.LanguageLock()
    lang.observe("pt", 0.9)
    assert lang.language is None
    lang.observe("en", 0.3)                                                          # pouco provável: não conta
    lang.observe("pt", 0.8)
    assert lang.language == "pt" and lang.tries == 3
    lang = proc.LanguageLock()
    lang.observe("pt", 0.52)
    lang.observe("es", 0.55)
    assert lang.language is None                                                     # discordantes e incertas: não trava
    lang.observe("en", 0.9)
    lang.observe("pt", 0.6)
    lang.observe("en", 0.65)
    assert lang.language is None and lang.tries == 5
    lang.observe("pt", 0.58)                                                         # 6 sem consenso: maior soma das >= 0,5
    assert lang.language == "pt" and lang.tries == 6                                 # pt 1,70 x en 1,55 x es 0,55
    assert proc.LanguageLock("pt-BR").language == "pt"
    lang = proc.LanguageLock()
    for _ in range(8):
        lang.observe(None, 0.0)
    assert lang.language is None and lang.tries == 6                                 # sem nenhuma detecção, segue automático
    seg = proc.Segment
    ok = proc.Transcript((seg(" Bom dia. ", 0.1, -0.3, 1.2), seg("ruído", 0.9, -0.3, 1.2), seg("lixo", 0.1, -1.5, 1.2),
                          seg("repete repete", 0.1, -0.3, 3.0), seg(" a todos", 0.0, -0.1, 1.0)))
    assert proc.caption_text(ok) == "Bom dia. a todos"
    for text in ("Obrigado por assistir!", "Legendas pela comunidade Amara.org", "Inscreva-se.", "Thank you for watching.",
                 "Subtítulos realizados por la comunidad de Amara.org", " you "):
        assert proc.caption_text(proc.Transcript((seg(text),))) == "", text
    assert proc.caption_text(proc.Transcript((seg("Obrigado, Ana."),))) == "Obrigado, Ana."
    assert proc.normalize_text("  Inscreva-se, JÁ! ") == "inscrevase ja"
    terms = proc.glossary_terms("SRM; FIDC, srm", "Cotas, FIDC")
    assert terms == ["SRM", "FIDC", "Cotas"]
    prompt = proc.build_prompt(terms, " ".join(f"p{i}" for i in range(100)))
    assert prompt.startswith("SRM, FIDC, Cotas p70") and prompt.endswith("p99") and "p69" not in prompt
    assert proc.build_prompt([], "") == "" and len(proc.build_prompt(["x"] * 100, "").split()) == proc.PROMPT_GLOSSARY_WORDS


def test_segment_filters_are_the_same_as_the_final_pass():
    from listener.pipeline import whisper

    try:
        assert (proc.NO_SPEECH_MAX, proc.LOGPROB_MIN) == (whisper.NO_SPEECH_MAX, whisper.LOGPROB_MIN)
    finally:
        sys.modules.pop("listener.pipeline.whisper", None)                          # o app não carrega whisper.py


def _lines(out: io.BytesIO) -> list[dict]:
    return [pr.decode_line(line) for line in out.getvalue().splitlines()]


def test_session_in_process_writes_captions_events_and_says_bye(tmp_path):
    db = tmp_path / "db"
    with Store(db) as s:
        mid = s.create_meeting("ao vivo", None, None, None, glossary="SRM, FIDC")
        s.set_setting("pipeline.glossary", "Cotas")
    data = session_input(pr.Hello(1000.0, names=False, model="small", threads=3),
                          [("L", tone(1.5, 440), 0), ("R", 24000, pr.SILENCED),                            # mic fala; R pausado
                           ("L", 32000, pr.SILENCED), ("R", tone(2.0), 0), ("R", 24000, pr.GAP), ("L", 16000 * 3, pr.GAP)])
    engines = FakeEngines()
    out = io.BytesIO()
    with Store.open_worker(db) as w:
        assert proc.serve(io.BytesIO(data), out, w, mid, engines, clock=lambda: 1010.0) == 0
        caps = w.captions.list_for_meeting(mid)
        events = {e["type"]: e for e in w.list_events(mid)}
    lines = _lines(out)
    assert [m["type"] for m in lines] == ["ready", "caption", "caption", "bye"]
    assert engines.loads == [("small", 3)]
    assert [(c["channel"], c["text"]) for c in caps] == [("L", "fala ao vivo 1"), ("R", "fala ao vivo 2")]
    assert {c["model"] for c in caps} == {"fake:small"}
    l_cap, r_cap = caps
    assert l_cap["t_start_ms"] == 0 and 1700 <= l_cap["t_end_ms"] <= 1810 and abs(l_cap["latency_ms"] - (10_000 - l_cap["t_end_ms"])) <= 1
    assert 1300 <= r_cap["t_start_ms"] <= 1500 and r_cap["label"] is None and r_cap["person_id"] is None
    assert lines[1]["id"] == l_cap["id"] and lines[1]["latency_ms"] == l_cap["latency_ms"]
    calls = engines.transcribers[0].calls
    assert calls[0][1] is None and calls[0][2] == "SRM, FIDC, Cotas"                 # glossário da reunião primeiro
    assert calls[1][2] == "SRM, FIDC, Cotas"                                         # a legenda anterior é do canal (R não tinha)
    bye = lines[-1]
    assert bye["captions"] == 2 and bye["app_dropped"] == 4 and bye["skipped"] == 0 and bye["model"] == "fake:small"
    assert events["live_started"]["data"]["engine"] == "fake" and events["live_started"]["data"]["threads"] == 3
    assert events["live_stopped"]["data"]["captions"] == 2 and events["live_started"]["t_ms"] == 10_000


def test_session_degrades_to_base_then_skips_old_windows(tmp_path, store):
    mid = store.create_meeting("atraso", None, None, None)
    engines = FakeEngines()
    out = io.BytesIO()
    mono = itertools.count(0.0, 16.0)                                                # cada janela "leva" 16 s de relógio
    session = proc.LiveSession(io.BytesIO(b""), out, store, mid, engines, clock=lambda: 1100.0, monotonic=lambda: next(mono))
    session.hello = pr.Hello(1000.0, model="small", threads=2)
    session.transcriber = engines.transcriber("small", 2)
    for i in range(10):
        ch = "LR"[i % 2]
        session.queue.put(proc.LiveWindow(ch, i * 16000, (i + 1) * 16000, 16000, tone(1.0)))
    session.queue.close()
    session._loop()
    assert engines.loads == [("small", 2), ("base", 2)] and session.transcriber.name == "fake:base"
    assert session.degrade.stage == proc.Stage.SKIPPING and session.stats.degradations == 2
    assert session.stats.skipped == 3 and session.stats.captions == 7                 # 5 antes de pular + a mais nova de cada canal
    states = [m for m in _lines(out) if m["type"] == "state"]
    assert [m["state"] for m in states] == ["degraded", "skipping"] and states[0]["model"] == "fake:base"
    degraded = [e for e in store.list_events(mid) if e["type"] == "live_degraded"]
    assert [e["data"]["stage"] for e in degraded] == ["degraded", "skipping"]
    models = [c["model"] for c in store.captions.list_for_meeting(mid)]
    assert models == ["fake:small"] * 3 + ["fake:base"] * 4                          # o atraso é medido depois de cada legenda


def test_stall_guard_closes_the_window_of_a_channel_that_stopped_delivering(store):
    mid = store.create_meeting("parado", None, None, None)
    session = proc.LiveSession(io.BytesIO(b""), io.BytesIO(), store, mid, FakeEngines())
    session.streams = {ch: proc.ChannelStream(ch, EnergyVad()) for ch in "LR"}
    r, l = session.streams["R"], session.streams["L"]
    assert r.feed(_audio("R", 0, tone(1.0))) == [] and r.window_open                # R falou e o loopback parou de entregar
    l.feed(_audio("L", 0, np.zeros(16000 * 3, np.float32) + 0.0001))
    assert session._stall_guard(l) == []                                              # 2 s de folga: ainda não
    l.feed(_audio("L", 48000, np.zeros(8000, np.float32)))
    assert session._stall_guard(l) == [] and r.window_open and r.pos == 24000         # R vira silêncio até 2 s atrás do L...
    l.feed(_audio("L", 56000, np.zeros(16000, np.float32)))
    out = session._stall_guard(l)                                                     # ...e a janela fecha com 1,2 s de silêncio
    assert len(out) == 1 and out[0].channel == "R" and out[0].end == 31 * FRAME + 300 * MS and not r.window_open
    assert r.feed(pr.AudioFrame("R", 16000, SAMPLE_RATE, 32000, pr.GAP)) == [] and r.pos == 48000   # o buraco tardio: só a parte nova


def test_mic_window_that_is_the_echo_of_the_meeting_is_dropped(tmp_path):
    """Sem headset o microfone pega a reunião: a janela de L muito correlacionada com o R do mesmo trecho não vira legenda
    (a de R já cobre); fala própria (outro sinal) passa."""
    db = tmp_path / "db"
    with Store(db) as s:
        mid = s.create_meeting("eco", None, None, None)
    rng = np.random.default_rng(7)
    speech = (rng.standard_normal(16000 * 2) * 0.2).astype(np.float32)               # "voz" larga: o eco é inequívoco
    echo = np.concatenate([np.zeros(800, np.float32), 0.4 * speech[:-800]])          # 50 ms depois, mais baixo
    own = (rng.standard_normal(16000 * 2) * 0.2).astype(np.float32)                  # outra voz, no mesmo instante
    gap = np.zeros(8000, np.float32)                                                  # 0,5 s: não fecha a janela
    plan = [("R", speech, 0), ("L", echo, 0), ("R", 32000, pr.SILENCED), ("L", 32000, pr.SILENCED),
            ("R", 64000, pr.SILENCED), ("L", own, 0), ("L", 32000, pr.SILENCED),          # 4..6 s: fala própria, R mudo
            ("R", speech, 0), ("L", np.concatenate([echo, gap, own[:12000]]), 0),        # 8..10,5 s: eco e logo fala própria
            ("R", 48000, pr.SILENCED), ("L", 32000, pr.SILENCED)]
    out = io.BytesIO()
    with Store.open_worker(db) as w:
        assert proc.serve(io.BytesIO(session_input(pr.Hello(1000.0, names=False), plan)), out, w, mid, FakeEngines()) == 0
        caps = w.captions.list_for_meeting(mid)
    got = [(c["channel"], round(c["t_start_ms"] / 1000, 1)) for c in caps]
    assert got == [("R", 0.0), ("L", 3.8), ("R", 7.8), ("L", 7.8)]                  # pré-rolagem de 200 ms
    assert _lines(out)[-1]["echo"] == 1                                               # só o eco puro sai


def test_emitter_survives_unserializable_lines_and_a_closed_pipe():
    out = io.BytesIO()
    em = proc.Emitter(out)
    em.emit({"type": "caption", "confidence": float("nan")})                         # perde a linha, não o laço
    em.emit({"type": "cpu", "live_pct": 1.0})
    assert out.getvalue().count(b"\n") == 1
    out.close()
    em.emit({"type": "cpu", "live_pct": 2.0})                                         # o app fechou o pipe: só para de emitir
    assert em.closed


def test_language_votes_only_with_captioned_windows_of_enough_speech(store):
    """Revisão do L0 a L3: janela filtrada (ruído) e janela com menos de 2 s de fala não votam no idioma."""
    mid = store.create_meeting("idioma", None, None, None)
    session = proc.LiveSession(io.BytesIO(b""), io.BytesIO(), store, mid, FakeEngines(), clock=lambda: 1001.0)
    session.hello = pr.Hello(1000.0)
    tr = FakeTranscriber("small", itertools.count(1), texts=["curta", "", "uma", "duas", "três"])
    session.transcriber = tr
    for i, speech_s in enumerate((1.0, 3.0, 3.0, 3.0, 3.0)):
        session.queue.put(proc.LiveWindow("R", i * 64000, i * 64000 + 48000, round(speech_s * SR), tone(3.0)))
    session.queue.close()
    session._loop()
    assert [c[1] for c in tr.calls] == [None, None, None, None, "pt"]               # trava depois de 2 votos válidos
    assert session.language.tries == 2 and session.stats.filtered == 1


def test_after_eof_only_a_window_that_fits_the_deadline_starts(store):
    """Revisão do L0 a L3: depois do EOF, uma janela só começa se couber no prazo (média do custo por janela); o resto é
    pulado e o `bye` sai antes de o app matar o processo."""
    mid = store.create_meeting("prazo", None, None, None)
    now = [1000.0]
    session = proc.LiveSession(io.BytesIO(b""), io.BytesIO(), store, mid, FakeEngines(), clock=lambda: now[0],
                               monotonic=lambda: now[0])
    session.hello = pr.Hello(1000.0, eof_grace_s=8.0)
    tr = FakeTranscriber("small", itertools.count(1), before=lambda _n: now.__setitem__(0, now[0] + 3.0))   # 3 s por janela
    session.transcriber = tr
    for i in range(5):
        session.queue.put(proc.LiveWindow("R", i * 16000, (i + 1) * 16000, 16000, tone(1.0)))
    session.queue.close()
    session.eof_at = now[0]
    session._loop()
    assert len(tr.calls) == 2 and session.stats.captions == 2 and session.stats.skipped == 3   # a 3ª acabaria em 9,6 s


def test_session_refuses_without_hello_or_meeting(store):
    mid = store.create_meeting("x", None, None, None)
    out = io.BytesIO()
    assert proc.serve(io.BytesIO(pr.encode_audio("L", 0, 16000, 2, 0, b"\0" * 4)), out, store, mid, FakeEngines()) == 2
    assert _lines(out)[0]["type"] == "error" and _lines(out)[0]["retry"] is False
    assert proc.serve(io.BytesIO(b""), io.BytesIO(), store, mid + 50, FakeEngines()) == 2

    class Broken(FakeEngines):
        def transcriber(self, model, threads):
            raise proc.LiveError("modelo small do ao vivo ausente")

    out = io.BytesIO()
    assert proc.serve(io.BytesIO(pr.encode_control(pr.Hello(1.0).to_msg())), out, store, mid, Broken()) == 1
    assert "ausente" in _lines(out)[-1]["message"] and _lines(out)[-1]["retry"] is False

    class Busy(FakeEngines):
        def transcriber(self, model, threads):
            raise MemoryError("sem memória para o modelo")

    out = io.BytesIO()                                                               # transitório: o app tenta de novo
    assert proc.serve(io.BytesIO(pr.encode_control(pr.Hello(1.0).to_msg())), out, store, mid, Busy()) == 1
    assert _lines(out)[-1]["retry"] is True
    out = io.BytesIO()                                                               # protocolo de outra versão: sem nova tentativa
    old = pr.encode_control({**pr.Hello(1.0).to_msg(), "v": pr.PROTOCOL_VERSION + 1})
    assert proc.serve(io.BytesIO(old), out, store, mid, FakeEngines()) == 2
    assert "reinicie o app" in _lines(out)[0]["message"] and _lines(out)[0]["retry"] is False


@pytest.mark.lento                                           # D-050: o subprocesso real `--live`
def test_live_process_end_to_end_in_fake_mode():
    """O único subprocesso real do ao vivo: `python -m listener --live` com LISTENER_LIVE=fake, stdin e stdout de verdade
    (lançado na coleta por `conftest.py`, para correr em paralelo ao resto da suíte)."""
    run = conftest.LIVE_RUN
    assert run, "o processo ao vivo devia ter sido lançado na coleta (conftest.pytest_collection_modifyitems)"
    out, err = run["proc"].communicate(timeout=90)
    lines = [pr.decode_line(line) for line in out.splitlines()]
    assert run["proc"].returncode == 0, err.decode("utf-8", "replace")[-2000:]
    assert [m["type"] for m in lines if m["type"] != "state"] == ["ready", "caption", "caption", "bye"] and lines[-1]["captions"] == 2
    assert [m["state"] for m in lines if m["type"] == "state"] == ["names_ready"]           # nomes fake carregam em paralelo
    mid = run["meeting_id"]
    with Store.open_readonly(run["db"]) as s:
        assert sorted(str(c["channel"]) for c in s.captions.list_for_meeting(mid)) == ["L", "R"]
        assert [e["type"] for e in s.list_events(mid)] == ["live_started", "live_stopped"]
    text = (Path(run["root"]) / "logs" / "listener-live.log").read_text(encoding="utf-8")
    assert "papel live" in text and "modo fake" in text and "ao vivo encerrado" in text
    assert b"papel live" in err                                                       # o log também vai ao stderr (descartado no app)
