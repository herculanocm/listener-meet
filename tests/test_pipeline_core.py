"""Núcleo puro do pipeline (F2 L2): janelas, alinhamento, casamento de perfis, costura, fakes e artefatos, sem modelos."""
from __future__ import annotations

import math
import tracemalloc

import numpy as np
import pytest

from listener.capture.audio import encode_flac, silent_flac
from listener.model import ClusterStatus
from listener.pipeline.align import Segmenter, merge_words
from listener.pipeline.profiles import ProfileMatcher, ProfileRef, clean_turns, cluster_data, expected_names
from listener.pipeline.stitch import pair_labels, stitch
from listener.pipeline.types import (DiarizationWindow, JobConfig, TranscriptWindow, Turn, Word, decode_diarization,
                                     decode_transcript, encode_diarization, encode_transcript)
from listener.pipeline.windows import find_silence_cut, frame_rms, iter_windows
from tests.fake_pipeline import FakeDeps, ScriptedTranscriber, ToneDiarizer, ToneEmbedder, dominant_freq

SR = 16000


def _tone(seconds: float, freq: float = 440.0, sr: int = SR, amp: float = 0.3) -> np.ndarray:
    n = np.arange(int(seconds * sr))
    return (amp * np.sin(2 * math.pi * freq * n / sr)).astype(np.float32)


def _meeting(store, chunks: list[tuple[int, np.ndarray]], sr: int = SR, channel: str = "R") -> int:
    """Pedaços (t_start_ms, amostras) de um canal; a duração da reunião é o fim do último pedaço."""
    mid = store.create_meeting("w", None, None, None)
    for i, (t0, x) in enumerate(chunks):
        store.insert_chunk(mid, channel, i, t0, len(x), sr, 0, encode_flac(x, sr))
    end = max(t0 + round(len(x) * 1000 / sr) for t0, x in chunks)
    store.finish_meeting(mid, end)
    return mid


# ---------------------------------------------------------------- janelas
def test_iter_windows_cuts_in_the_longest_silence_without_overlap(store):
    mid = _meeting(store, [(0, _tone(1.0)), (1000, np.concatenate([_tone(0.3), np.zeros(int(0.4 * SR), np.float32), _tone(0.3)])),
                           (2000, np.zeros(SR, np.float32))])
    wins = list(iter_windows(store, mid, "R", window_s=1.5, overlap_s=0.5, silence_rms=0.01, search_s=0.5))
    assert [w.idx for w in wins] == [0, 1] and wins[0].t0_ms == 0 and wins[-1].t1_ms == 3000
    assert 1450 <= wins[0].t1_ms <= 1550 and wins[1].t0_ms == wins[0].t1_ms and wins[1].overlap_ms == 0   # cortou no silêncio
    assert wins[0].has_speech and wins[1].has_speech                              # a 2ª tem tom entre 1,7 e 2,0 s
    assert len(wins[0].samples) == round(wins[0].duration_ms * SR / 1000) and wins[0].sample_rate == SR
    assert find_silence_cut(_tone(2.0), SR, SR, SR // 2, 0.01) is None
    assert frame_rms(np.zeros(10, np.float32), SR).size == 0


def test_iter_windows_falls_back_to_overlap_and_resamples(store):
    mid = _meeting(store, [(0, _tone(3.0, sr=48000)), (3000, np.zeros(48000, np.float32))], sr=48000)
    wins = list(iter_windows(store, mid, "R", window_s=1.0, overlap_s=0.25, silence_rms=0.01, search_s=0.2))
    # sem silêncio: corte seco com 250 ms de sobreposição; a 4ª acha o silêncio que começa em 3,0 s e corta nele (3,25 s)
    assert [(w.t0_ms, w.t1_ms, w.overlap_ms) for w in wins] == [(0, 1000, 0), (750, 1750, 250), (1500, 2500, 250), (2250, 3250, 250),
                                                                 (3250, 4000, 0)]
    assert all(w.sample_rate == SR and len(w.samples) == round(w.duration_ms * SR / 1000) for w in wins)   # 48 kHz -> 16 kHz
    assert [w.has_speech for w in wins] == [True, True, True, True, False]         # a última é só silêncio
    assert abs(dominant_freq(wins[1].samples, SR) - 440) < 20
    silent = list(iter_windows(store, mid, "L", 1.0))                                   # canal sem áudio: silêncio
    assert len(silent) == 4 and not silent[0].has_speech
    assert list(iter_windows(store, store.create_meeting(None, None, None, None), "R", 1.0)) == []


def test_iter_windows_fixed_windows_for_diarization_and_memory_budget(store):
    mid = store.create_meeting("w", None, None, None)
    for i in range(6):
        store.insert_chunk(mid, "R", i, i * 10_000, 10 * SR, SR, 10 * SR, silent_flac(SR, 10 * SR))
    store.finish_meeting(mid, 60_000)
    tracemalloc.start()
    try:
        wins = list(iter_windows(store, mid, "R", window_s=60, overlap_s=30, cut_on_silence=False))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert [(w.t0_ms, w.t1_ms) for w in wins] == [(0, 60_000)] and not wins[0].has_speech
    assert peak < 2 * 60 * SR * 4, f"pico de {peak / 1e6:.1f} MB para uma janela de {60 * SR * 4 / 1e6:.1f} MB"
    wins = list(iter_windows(store, mid, "R", window_s=25, overlap_s=5, cut_on_silence=False))
    assert [(w.t0_ms, w.t1_ms, w.overlap_ms) for w in wins] == [(0, 25_000, 0), (20_000, 45_000, 5_000), (40_000, 60_000, 5_000)]


# ---------------------------------------------------------------- alinhamento
def test_segmenter_assigns_by_center_and_cuts_by_pause_length_and_sentence():
    seg = Segmenter()
    turns = [Turn(0, 2000, "A"), Turn(2500, 5000, "B")]
    words = [Word(100, 400, "Oi"), Word(2100, 2300, "tudo"), Word(2600, 2900, "bem?"), Word(5300, 5400, "sim"), Word(6000, 6100, "longe")]
    assert [lab for _, lab in seg.assign(words, turns)] == ["A", "A", "B", "B", None]
    assert [lab for _, lab in seg.assign(words, [])] == [None] * 5
    a = [Word(i * 1000, i * 1000 + 900, "x") for i in range(40)]           # 40 s do mesmo falante: corta em 30 s
    out = seg.segments([(w, "A") for w in a])
    assert [len(s.words) for s in out] == [30, 10] and out[0].end_ms == 29_900 and out[1].start_ms == 30_000
    b = [Word(0, 400, "Oi."), Word(900, 1200, "Tudo"), Word(1300, 1600, "bem."), Word(1700, 2000, "Sim"), Word(3200, 3500, "fim")]
    out = seg.segments([(w, "A") for w in b])
    assert [s.text for s in out] == ["Oi.", "Tudo bem. Sim", "fim"]          # ponto + pausa > 400 ms; pausa > 1 s
    assert out[1].confidence == 1.0 and out[0].label == "A"
    drafts = seg.run(words, turns)
    assert [(d.label, d.text) for d in drafts] == [("A", "Oi"), ("A", "tudo"), ("B", "bem?"), ("B", "sim"), (None, "longe")]


def test_merge_words_dedups_overlap_by_word_center():
    w0 = TranscriptWindow(0, 0, 1000, (Word(100, 200, "a"), Word(820, 880, "b"), Word(930, 990, "c")), overlap_ms=0)
    w1 = TranscriptWindow(1, 800, 1800, (Word(820, 880, "b2"), Word(930, 990, "c"), Word(1200, 1300, "d")), overlap_ms=200)
    assert [w.text for w in merge_words([w1, w0])] == ["a", "b", "c", "d"]    # meio da sobreposição = 900 ms


# ---------------------------------------------------------------- perfis
def _e(i: int, dim: int = 16) -> np.ndarray:
    v = np.zeros(dim, np.float32)
    v[i] = 1.0
    return v


def test_profile_matcher_lsa_threshold_margin_short_and_tie_break():
    ana, bia, dan = ProfileRef(1, 10, _e(0), name="Ana"), ProfileRef(2, 20, _e(1), name="Bia"), ProfileRef(3, 30, _e(2), name="Dan")
    mix = lambda a, b, k: (a + k * b) / np.linalg.norm(a + k * b)  # noqa: E731
    clusters = [("c0", _e(0), 20_000), ("c1", _e(1), 20_000), ("c2", mix(_e(1), _e(0), 0.3), 20_000), ("c3", _e(0), 5_000),
                ("c4", _e(5), 20_000), ("c5", mix(_e(0), _e(2), 0.95), 20_000)]
    cd = [cluster_data([Turn(0, ms, lab)], {lab: [c]}) [0] for lab, c, ms in clusters]
    res = ProfileMatcher(0.60, 0.05).match(cd, [ana, bia, dan])
    by = {r.label: r for r in res}
    assert by["c0"].status == ClusterStatus.AUTO and by["c0"].person_id == 10 and by["c0"].similarity == pytest.approx(1.0)
    assert by["c1"].status == ClusterStatus.AUTO and by["c1"].person_id == 20
    assert by["c2"].status == ClusterStatus.UNNAMED and by["c2"].person_id == 20            # Bia já foi para c1 (LSA): sem repetir
    assert by["c3"].status == ClusterStatus.UNNAMED                                          # Ana já foi para c0
    assert by["c4"].status == ClusterStatus.UNNAMED and by["c4"].similarity == pytest.approx(0.0)
    assert by["c5"].status == ClusterStatus.AMBIGUOUS and by["c5"].person_id == 30 and by["c5"].margin < 0.05   # Dan, mas perto de Ana
    # desempate pelos participantes esperados (P4): só "Dan" na lista -> auto
    res = ProfileMatcher(0.60, 0.05, expected=expected_names("Carlos; dan")).match(cd[5:], [ana, bia, dan])
    assert res[0].status == ClusterStatus.AUTO and res[0].person_id == 30
    # cluster curto nunca é auto; limiar calibrado do perfil manda; sem perfis tudo sem nome
    short = ProfileMatcher().match([cd[3]], [ana])
    assert short[0].status == ClusterStatus.AMBIGUOUS and short[0].person_id == 10
    strict = ProfileMatcher().match([cd[0]], [ProfileRef(1, 10, mix(_e(0), _e(3), 1.0), threshold=0.75)])   # cosseno 0,707
    assert strict[0].status == ClusterStatus.UNNAMED and strict[0].threshold == 0.75
    assert [r.status for r in ProfileMatcher().match(cd[:2], [])] == [ClusterStatus.UNNAMED] * 2
    # R2-16: perfil "hub": a 2ª voz é mais parecida com a Ana (já dada à 1ª), o LSA a entrega à Bia acima do limiar e com
    # margem folgada; isso não pode virar `auto` Bia sem revisão
    hub = [cluster_data([Turn(0, 20_000, lab)], {lab: [c]})[0] for lab, c in (("h0", _e(0)), ("h1", mix(_e(0), _e(1), 0.8)))]
    hub_res = {r.label: r for r in ProfileMatcher(0.60, 0.05).match(hub, [ana, bia])}
    assert hub_res["h0"].status == ClusterStatus.AUTO and hub_res["h0"].person_id == 10
    assert hub_res["h1"].status == ClusterStatus.AMBIGUOUS and hub_res["h1"].person_id == 20 and hub_res["h1"].similarity > 0.6
    # desempate não repete pessoa: "ana" está na lista, mas a Ana já foi para a outra voz -> ambíguo
    pair = [cluster_data([Turn(0, 20_000, lab)], {lab: [c]})[0] for lab, c in (("p0", _e(0)), ("p1", mix(_e(0), _e(2), 0.95)))]
    pair_res = {r.label: r for r in ProfileMatcher(0.60, 0.05, expected=("ana",)).match(pair, [ana, bia, dan])}
    assert pair_res["p0"].status == ClusterStatus.AUTO and pair_res["p1"].status == ClusterStatus.AMBIGUOUS
    assert pair_res["p1"].person_id == 30
    assert ProfileMatcher().match([cluster_data([Turn(0, 100, "x")], {})[0]], [ana])[0].person_id is None   # sem centroide
    assert expected_names(None) == () and expected_names(" Ana , Bia ") == ("ana", "bia")


def test_clean_turns_and_cluster_data():
    turns = [Turn(0, 3000, "A"), Turn(3000, 4000, "B"), Turn(4000, 7000, "A"), Turn(7000, 9000, "B"), Turn(9000, 9500, "A")]
    clean = clean_turns(turns, overlap=((4500, 5000),), min_ms=1500)
    assert clean == {"A": [Turn(0, 3000, "A")], "B": [Turn(7000, 9000, "B")]}       # sobreposição e curtos fora; mais longos primeiro
    cd = cluster_data(turns, {"A": [_e(0), _e(0)], "B": []})
    assert [(c.label, c.speech_ms, c.n_turns) for c in cd] == [("A", 6500, 3), ("B", 3000, 2)] and cd[1].centroid is None
    assert np.allclose(cd[0].centroid, _e(0))


# ---------------------------------------------------------------- costura
def test_stitch_swapped_labels_new_speaker_and_time_criterion():
    ea, eb, ec = _e(0), _e(1), _e(2)
    w0 = DiarizationWindow(0, 0, 10_000, (Turn(0, 4000, "SPEAKER_00"), Turn(4000, 10_000, "SPEAKER_01")),
                           embeddings={"SPEAKER_00": ea, "SPEAKER_01": eb})
    w1 = DiarizationWindow(1, 8000, 18_000, (Turn(8000, 12_000, "SPEAKER_00"), Turn(12_000, 18_000, "SPEAKER_01")),
                           overlap=((8500, 8700), (9500, 9700)), embeddings={"SPEAKER_00": eb, "SPEAKER_01": ea})
    w2 = DiarizationWindow(2, 16_000, 26_000, (Turn(16_000, 18_000, "SPEAKER_00"), Turn(18_000, 26_000, "SPEAKER_01")),
                           embeddings={"SPEAKER_00": ea, "SPEAKER_01": ec})
    turns, overlaps, mapping = stitch([w2, w0, w1])
    assert mapping == {(0, "SPEAKER_00"): "SPK_0", (0, "SPEAKER_01"): "SPK_1", (1, "SPEAKER_00"): "SPK_1", (1, "SPEAKER_01"): "SPK_0",
                       (2, "SPEAKER_00"): "SPK_0", (2, "SPEAKER_01"): "SPK_2"}
    assert turns == (Turn(0, 4000, "SPK_0"), Turn(4000, 12_000, "SPK_1"), Turn(12_000, 18_000, "SPK_0"), Turn(18_000, 26_000, "SPK_2"))
    assert overlaps == ((9500, 9700),)                      # a metade da janela anterior (até 9 s) é descartada
    # sem embeddings, o tempo partilhado na sobreposição decide; cosseno baixo com tempo igual também aceita
    p0 = DiarizationWindow(0, 0, 10_000, (Turn(0, 10_000, "X"),))
    p1 = DiarizationWindow(1, 8000, 18_000, (Turn(8000, 18_000, "Y"),))
    assert pair_labels(p0, p1) == {"Y": "X"}
    q0 = DiarizationWindow(0, 0, 10_000, (Turn(0, 10_000, "X"),), embeddings={"X": ea})
    q1 = DiarizationWindow(1, 8000, 18_000, (Turn(8000, 18_000, "Y"),), embeddings={"Y": ec})
    assert pair_labels(q0, q1) == {"Y": "X"} and pair_labels(DiarizationWindow(0, 0, 1, ()), q1) == {}
    # cosseno alto sem tempo partilhado (falante calado na emenda) também costura
    r0 = DiarizationWindow(0, 0, 10_000, (Turn(0, 7000, "X"),), embeddings={"X": ea})
    r1 = DiarizationWindow(1, 8000, 18_000, (Turn(11_000, 18_000, "Y"),), embeddings={"Y": ea})
    assert pair_labels(r0, r1) == {"Y": "X"} and stitch([r0, r1])[0] == (Turn(0, 7000, "SPK_0"), Turn(11_000, 18_000, "SPK_0"))


# ---------------------------------------------------------------- fakes e artefatos
def test_fakes_label_tones_and_embed_deterministically():
    x = np.concatenate([_tone(1.0, 220), _tone(1.0, 330), np.zeros(SR // 2, np.float32), _tone(1.0, 220)])
    from listener.pipeline.types import AudioWindow

    win = AudioWindow(0, "R", 0, 3500, x)
    d = ToneDiarizer().diarize(win, min_speakers=1, max_speakers=3)
    assert d.turns == (Turn(0, 1000, "SPEAKER_00"), Turn(1000, 2000, "SPEAKER_01"), Turn(2500, 3500, "SPEAKER_00"))
    emb = ToneEmbedder()
    a, b, c = emb.embed(_tone(1.0, 220), SR), emb.embed(_tone(1.5, 220), SR), emb.embed(_tone(1.0, 330), SR)
    assert a.shape == (256,) and float(a @ b) > 0.99 and float(a @ c) < 0.05 and emb.calls == 3
    tr = ScriptedTranscriber([(0.5, 1.5, "oi tudo bem"), (2.0, 3.0, "sim")])
    t = tr.transcribe(AudioWindow(1, "R", 1000, 2600, x[SR:]), language=None, initial_prompt="p")
    assert [w.text for w in t.words] == ["bem", "sim"] and t.speech_ms == 1100 and tr.calls == [("R", 1, None, "p")]
    deps = FakeDeps([(0, 1, "x")])
    assert deps.transcriber() is deps.transcriber() and deps.embedder().dim == 256
    deps.release()
    assert deps.transcriber() is deps.scripted and deps.diarizer().name == "fake:tone"


def test_artifacts_roundtrip_and_job_config():
    t = TranscriptWindow(3, 1000, 2000, (Word(1000, 1200, "olá", 0.87),), "pt", 0.98, 700, 200)
    assert decode_transcript(encode_transcript(t)) == t
    d = DiarizationWindow(1, 0, 5000, (Turn(0, 2500, "A"), Turn(2500, 5000, "B")), ((100, 200),), {"B": _e(1), "A": _e(0)}, "m")
    back = decode_diarization(encode_diarization(d))
    assert back.turns == d.turns and back.overlap == d.overlap and back.model == "m" and back.embeddings is not None
    assert np.array_equal(back.embeddings["A"], _e(0)) and np.array_equal(back.embeddings["B"], _e(1))
    assert decode_diarization(encode_diarization(DiarizationWindow(0, 0, 1, ()))).embeddings is None
    with pytest.raises(ValueError):
        decode_transcript(b"lixo")
    cfg = JobConfig.from_settings({"pipeline.whisper_model": "small", "pipeline.threshold": "0.9", "capture.silence_rms": "0.02"})
    assert cfg.whisper_model == "small" and cfg.threshold == 0.60 and cfg.silence_rms == 0.02 and cfg.diar_window_s == 1200   # R2-15
    assert JobConfig.from_json('{"pipeline.margin": 0.1}').margin == 0.1 and JobConfig.from_json("x").margin == 0.05
    assert JobConfig.from_json(None).to_dict()["whisper_window_s"] == 600


def test_known_windows_are_not_decoded_again(store, monkeypatch):
    """R2-21: na retomada, janelas já gravadas saem sem decodificar; só a última conhecida e as novas tocam o áudio."""
    from listener.pipeline import windows as windows_mod

    mid = _meeting(store, [(0, _tone(3.0))])
    calls: list[tuple[int, int]] = []
    real = windows_mod.decode_range

    def spy(st, m, ch, t0, t1):
        calls.append((t0, t1))
        return real(st, m, ch, t0, t1)

    monkeypatch.setattr(windows_mod, "decode_range", spy)
    known = {0: (0, 1000, 0), 1: (1000, 2000, 0)}
    wins = list(iter_windows(store, mid, "R", 1.0, 0.25, silence_rms=0.01, search_s=0.2, known=known))
    # tom contínuo: sem silêncio, as janelas novas saem com 250 ms de sobreposição; a 0 vem do `known`, sem áudio
    assert [(w.idx, w.t0_ms, w.t1_ms, w.samples.size > 0) for w in wins] == [(0, 0, 1000, False), (1, 1000, 2000, True),
                                                                             (2, 1750, 2750, True), (3, 2500, 3000, True)]
    assert [t0 for t0, _ in calls] == [1000, 1750, 2500]                                       # a janela 0 não foi decodificada
    assert not wins[0].has_speech and wins[1].has_speech
