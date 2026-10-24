import numpy as np
import pytest

from listener.capture.audio import decode_flac, encode_flac, resample, rms, to_mono


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


def test_flac_roundtrip():
    x = (0.3 * np.sin(np.linspace(0, 200, 16000))).astype(np.float32)
    data = encode_flac(x, 16000)
    y, sr = decode_flac(data)
    assert sr == 16000 and len(y) == 16000
    assert np.max(np.abs(y - x)) < 1e-3
    assert len(data) < 2 * 16000           # comprime bem um seno


def test_flac_silence_is_tiny():
    assert len(encode_flac(np.zeros(16000 * 60, np.float32), 16000)) < 20_000
