import numpy as np
import pytest

from listener.capture.chunker import ChannelChunker


def make(sr=100, ch=1, chunk_seconds=1.0, **kw):
    out = []
    c = ChannelChunker("L", sr, ch, out.append, chunk_seconds=chunk_seconds, gap_tolerance=0.25, **kw)
    return c, out


def test_rotates_at_boundary_and_indexes():
    c, out = make()
    c.write(np.ones(250, np.float32), t_rel=0.0)
    c.close()
    assert [o.frames for o in out] == [100, 100, 50]
    assert [o.idx for o in out] == [0, 1, 2]
    assert [o.start_rel for o in out] == [0.0, 1.0, 2.0]
    assert all(o.data.shape == (o.frames, 1) for o in out)


def test_gap_filled_by_timestamp():
    c, out = make()
    c.write(np.ones(50, np.float32), t_rel=0.0)
    c.write(np.ones(50, np.float32), t_rel=2.0)        # 1,5 s sem pacotes
    c.close()
    data = np.concatenate([o.data[:, 0] for o in out])
    assert len(data) == 250
    assert data[:50].all() and not data[50:200].any() and data[200:].all()
    assert c.gap_frames_total == 150
    assert sum(o.gap_frames for o in out) == 150


def test_initial_silence_and_end_padding():
    c, out = make(start_rel=0.0)
    c.write(np.ones(10, np.float32), t_rel=3.0)        # primeiro pacote só em 3 s
    c.close(end_rel=4.5)                                # sessão parou em 4,5 s
    data = np.concatenate([o.data[:, 0] for o in out])
    assert len(data) == 450
    assert not data[:300].any() and data[300:310].all() and not data[310:].any()


def test_close_with_no_data_but_end_rel_writes_silence():
    c, out = make()
    c.close(end_rel=2.0)
    assert sum(o.frames for o in out) == 200 and not np.concatenate([o.data for o in out]).any()


def test_jitter_within_tolerance_not_filled():
    c, out = make()
    c.write(np.ones(50, np.float32), t_rel=0.0)
    c.write(np.ones(50, np.float32), t_rel=0.6)
    c.write(np.ones(50, np.float32), t_rel=0.9)
    c.close()
    assert c.gap_frames_total == 0 and c.late_blocks == 0 and sum(o.frames for o in out) == 150


def test_stereo_kept_and_mono_downmix():
    c, out = make(ch=2)
    c.write(np.full((10, 2), 0.5, np.float32), t_rel=0.0)
    c.write(np.full(10, 0.25, np.float32), t_rel=0.1)     # mono num chunker estéreo: replica
    c.close()
    assert out[0].data.shape == (20, 2) and out[0].data[15, 1] == pytest.approx(0.25)
    c2, out2 = make(ch=1)
    c2.write(np.stack([np.full(10, 1.0, np.float32), np.full(10, 0.0, np.float32)], axis=1), t_rel=0.0)
    c2.close()
    assert out2[0].data[:, 0] == pytest.approx(0.5)
