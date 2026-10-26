import numpy as np
import pytest

from listener.capture.audio import StreamResampler, decode_flac, encode_flac, resample, rms, silent_flac, to_int16, to_mono


@pytest.mark.lento                                           # D-050: conta pesada (~1,7 s no Windows)
def test_resample_48k_stereo_to_16k_mono_keeps_tone():
    sr = 48000
    t = np.arange(sr * 2) / sr
    tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    stereo = np.stack([tone, tone], axis=1)
    mono = to_mono(stereo)
    y = resample(mono, sr, 16000)
    assert len(y) == 16000 * 2
    spec = np.abs(np.fft.rfft(y[:16000]))
    assert np.argmax(spec) == 1000            # 1 kHz continua em 1 kHz
    assert rms(y) == pytest.approx(0.5 / np.sqrt(2), rel=0.05)


def test_to_mono_variants_and_rms():
    x = np.array([[1.0, 0.0], [0.5, 0.5]], np.float32)
    assert to_mono(x).tolist() == [0.5, 0.5]
    assert to_mono(np.array([0.25], np.float32)).tolist() == [0.25]
    assert to_mono(np.ones((4, 1), np.float32)).shape == (4,)
    assert to_mono(np.ones((4, 3), np.float32)) == pytest.approx(np.ones(4))
    assert rms(np.zeros(0)) == 0.0 and rms(np.full((10, 2), 0.5, np.float32)) == pytest.approx(0.5)


def test_int16_rounds_instead_of_truncating():
    assert to_int16(np.array([0.6 / 32767, 1.4 / 32767, -1.6 / 32767, 2.0], np.float32)).tolist() == [1, 1, -2, 32767]


@pytest.mark.parametrize(("sr_from", "block"), [(48000, 480), (48000, 4800), (44100, 4410), (44100, 1000), (32000, 1234)])
def test_stream_resampler_matches_whole_signal(sr_from, block):
    """Overlap-save: a saída por blocos é igual à do sinal inteiro, inclusive nas fronteiras (M-15)."""
    t = np.arange(sr_from * 2) / sr_from
    x = (0.5 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 3000 * t)).astype(np.float32)
    whole = resample(x, sr_from, 16000)
    r = StreamResampler(sr_from, 16000)
    parts = [r.process(x[i:i + block]) for i in range(0, len(x), block)]
    parts.append(r.flush())
    y = np.concatenate(parts)
    assert abs(len(y) - len(whole)) <= 1
    n = min(len(y), len(whole))
    assert np.max(np.abs(y[:n] - whole[:n])) < 1e-6
    assert r.pending_in == 0


def test_stream_resampler_reports_pending_and_resets():
    r = StreamResampler(48000, 16000)
    assert r.pending_in == 0
    y = r.process(np.zeros(4800, np.float32))
    assert len(y) == 1600 - r.hold // 3 and r.pending_in == r.hold
    y = r.process(np.zeros(2, np.float32))          # menor que `down`: fica como resto
    assert len(y) == 0 and r.pending_in == r.hold + 2
    r.reset()
    assert r.pending_in == 0 and len(r.flush()) == 0


def test_flac_roundtrip():
    x = (0.3 * np.sin(np.linspace(0, 200, 16000))).astype(np.float32)
    data = encode_flac(x, 16000)
    y, sr = decode_flac(data)
    assert sr == 16000 and len(y) == 16000
    assert np.max(np.abs(y - x)) < 1e-3
    assert len(data) < 2 * 16000           # comprime bem um seno


def test_flac_silence_is_tiny_and_cached():
    assert len(encode_flac(np.zeros(16000 * 60, np.float32), 16000)) < 20_000
    a = silent_flac(16000, 16000 * 60)
    assert a is silent_flac(16000, 16000 * 60) and decode_flac(a)[0].shape == (16000 * 60,)
