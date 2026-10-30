"""F5.9 (itens 1 e 2): a vida dos canais L e R. L1b-plataforma: o fake imita o `alive()` do protocolo (`FakeStream.die()` e
`FakePlatform.kill_r()`: a stream morre calada, sem `on_error`, e para de entregar). L1b-gravador: a vigia de 1 s do
gravador lê o `alive()` e marca o canal perdido (Aceite 1) e o que não abriu no começo é marcado e tentado de novo (Aceite 4)."""
from __future__ import annotations


def test_the_fake_stream_dies_quietly_and_kill_r_picks_the_r_stream(platform, store, recorder_factory):
    rec = recorder_factory()
    rec.start()
    platform.pump(0.3)
    assert all(s.alive() is None for s in platform.live_streams())             # o driver não diz nada
    r = platform.kill_r()
    assert r.freq != 440.0 and r.alive() is False and not r.stopped and r in platform.live_streams()
    assert all(s.alive() is None for s in platform.mic_streams) and r.on_error is not None
    delivered = r.delivered
    platform.pump(0.3)
    assert r.delivered == delivered and "R" not in rec.snapshot.lost            # calada: sem `on_error`, ninguém marcou
    rec.stop()
    assert r.stop_calls == 1
