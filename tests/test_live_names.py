"""F4 L3: nome ao vivo. `match_one` (limiar e margem da F2, limiar calibrado, desempate pelos esperados), suavização por
canal, rótulo "Nome — Cargo", o filho fake com nomes (R por embedding, L é o usuário sem sala e passa pelo embedding na
sala presencial, "Participante" abaixo do limiar, nomes indisponíveis sem derrubar a legenda, carga em paralelo) e CP11:
nada grava clusters, segmentos nem perfis."""
from __future__ import annotations

import io
import sys
import threading
import time

import numpy as np
import pytest

from listener.live import names
from listener.live import protocol as pr
from listener.live import proc
from listener.pipeline.profiles import ProfileRef
from listener.store import Store
from tests.fake_live import FakeEngines, tone
from tests.fake_pipeline import ToneEmbedder

SR = 16000


def e(i: int, dim: int = 8) -> np.ndarray:
    v = np.zeros(dim, np.float32)
    v[i] = 1.0
    return v


def ref(person_id: int, vec: np.ndarray, threshold: float | None = None) -> ProfileRef:
    return ProfileRef(person_id * 10, person_id, np.asarray(vec, np.float32), threshold, f"p{person_id}")


def test_match_one_threshold_margin_calibration_and_expected_tie_break():
    profiles = [ref(1, e(0)), ref(2, e(1)), ref(3, np.zeros(8, np.float32)), ref(4, e(0, dim=4))]   # vazio e outra dimensão: fora
    m = names.match_one(e(0) * 3, profiles, 0.60, 0.05)
    assert m.decided and m.person_id == 1 and m.similarity == pytest.approx(1.0) and m.margin == pytest.approx(1.0)
    assert set(m.sims) == {1, 2}
    low = names.match_one(e(2) + 0.3 * e(0), profiles, 0.60, 0.05)                  # abaixo do limiar: sem nome
    assert not low.decided and low.best_person_id == 1 and low.similarity == pytest.approx(0.3 / np.hypot(1, 0.3), abs=1e-4)
    tie = e(0) + e(1) + 0.01 * e(0)                                                  # os dois ~0,71: margem pequena
    assert not names.match_one(tie, profiles, 0.60, 0.05).decided
    won = names.match_one(tie, profiles, 0.60, 0.05, expected_ids=frozenset({2}))     # só o 2 era esperado: ele leva
    assert won.person_id == 2 and won.best_person_id == 1 and won.similarity == pytest.approx(0.7036, abs=1e-3)
    assert not names.match_one(tie, profiles, 0.60, 0.05, expected_ids=frozenset({1, 2})).decided
    calibrated = [ref(1, e(0), threshold=0.75), ref(2, e(1))]
    v = e(0) + 0.9 * e(2)                                                            # cosseno 0,74 com o 1
    assert not names.match_one(v, calibrated, 0.60, 0.05).decided                    # o limiar calibrado vale, não o global
    assert names.match_one(v, [ref(1, e(0)), ref(2, e(1))], 0.60, 0.05).person_id == 1
    assert names.match_one(np.zeros(8), profiles, 0.6, 0.05) == names.NameMatch(None)
    assert names.match_one(e(0), [], 0.6, 0.05) == names.NameMatch(None)
    # revisão do L0 a L3: vetor NaN ou perfil corrompido não decidem nem derrubam; um perfil só sem segundo para desempatar
    one = [ref(1, e(0))]
    assert names.match_one(np.full(8, np.nan, np.float32), one, 0.6, 0.05) == names.NameMatch(None)
    assert names.match_one(e(0), [ref(1, np.full(8, np.nan, np.float32))], 0.6, 0.05) == names.NameMatch(None)
    assert not names.match_one(e(0), one, 0.0, 2.0).decided                         # margem acima do próprio cosseno


def test_channel_smoothing_keeps_the_previous_person_only_when_the_voice_is_similar():
    namer = names.ChannelNamer()
    first = names.NameMatch(7, 0.9, 0.3, 7, {7: 0.9})
    assert namer.decide(e(0), first) == (7, 0.9)
    similar = np.cos(0.5) * e(0) + np.sin(0.5) * e(1)                                # cosseno 0,88 com a anterior
    assert namer.decide(similar, names.NameMatch(None, 0.5, 0.01, 7, {7: 0.55})) == (7, 0.55) and namer.kept == 1
    different = e(3)
    assert namer.decide(different, names.NameMatch(None, 0.2, 0.0, 7, {7: 0.2})) == (None, 0.2) and namer.person_id is None
    assert namer.decide(e(3), names.NameMatch(None, 0.2, 0.0, None)) == (None, 0.2)  # sem pessoa anterior: nada a manter
    assert namer.decide(e(1), names.NameMatch(9, 0.8, 0.2, 9, {9: 0.8})) == (9, 0.8)
    assert names.person_label({"name": "Ana", "role": "CFO"}) == "Ana — CFO" and names.person_label({"name": "Bia", "role": ""}) == "Bia"
    assert names.person_label(None) is None and names.person_label({"name": " "}) is None


def _tone_vec(freq: float) -> np.ndarray:
    return ToneEmbedder().embed(tone(1.0, freq), SR)


def _db(tmp_path, in_room: bool = False) -> tuple[object, int, dict[str, int]]:
    db = tmp_path / "db"
    with Store(db) as s:
        ids = {"me": s.upsert_me("Eu", role="Gerente"), "ana": s.people.create_person("Ana", role="CFO"),
               "bia": s.people.create_person("Bia")}
        s.profiles.confirm(ids["ana"], "fake-tone-256", _tone_vec(220), None, None)
        s.profiles.confirm(ids["bia"], "fake-tone-256", _tone_vec(440), None, None)
        mid = s.create_meeting("nomes", None, None, None, is_in_room=in_room)
    return db, mid, ids


def _input(plan: list[tuple[str, np.ndarray]], hello: pr.Hello | None = None) -> bytes:
    """Cada fala seguida de 2 s de silêncio marcado no mesmo canal (fecha a janela); os dois canais andam juntos."""
    parts = [pr.encode_control((hello or pr.Hello(1000.0)).to_msg())]
    pos = 0
    for ch, x in plan:
        other = "R" if ch == "L" else "L"
        for i in range(0, len(x), 1600):
            b = x[i:i + 1600]
            parts.append(pr.encode_audio(ch, pos + i, SR, len(b), 0, np.rint(b * 32767).astype("<i2").tobytes()))
        parts.append(pr.encode_audio(other, pos, SR, len(x), pr.SILENCED))
        parts.append(pr.encode_audio(ch, pos + len(x), SR, 2 * SR, pr.SILENCED))
        parts.append(pr.encode_audio(other, pos + len(x), SR, 2 * SR, pr.SILENCED))
        pos += len(x) + 2 * SR
    return b"".join(parts)


def _counts(store: Store) -> tuple[int, ...]:
    return tuple(int(store._q(f"SELECT COUNT(*) FROM {t}")[0][0])                    # noqa: S608
                 for t in ("clusters", "segments", "voice_profiles", "voice_embeddings"))


def test_child_names_r_by_embedding_l_as_the_user_and_participant_below_threshold(tmp_path):
    db, mid, ids = _db(tmp_path)
    data = _input([("R", tone(1.5, 220)), ("L", tone(1.5, 440)), ("R", tone(1.5, 330))])
    out = io.BytesIO()
    with Store.open_worker(db) as w:
        before = _counts(w)
        assert proc.serve(io.BytesIO(data), out, w, mid, FakeEngines(), clock=lambda: 1010.0, names_sync=True) == 0
        caps = w.captions.list_for_meeting(mid)
        assert _counts(w) == before                                                  # CP11: só exibição
        stopped = next(e for e in w.list_events(mid) if e["type"] == "live_stopped")
    got = [(c["channel"], c["person_id"], c["label"]) for c in caps]
    assert got == [("R", ids["ana"], "Ana — CFO"), ("L", ids["me"], "Eu — Gerente"), ("R", None, "Participante")]
    assert caps[0]["confidence"] == pytest.approx(1.0, abs=1e-3) and caps[1]["confidence"] is None
    assert caps[2]["confidence"] < 0.6
    lines = [pr.decode_line(line) for line in out.getvalue().splitlines()]
    assert {"type": "state", "state": "names_ready", "model": "fake-tone-256", "profiles": 2} in lines
    assert next(m for m in lines if m["type"] == "caption")["label"] == "Ana — CFO"
    assert stopped["data"]["names"]["ready"] is True and stopped["data"]["names"]["profiles"] == 2


def test_in_room_the_mic_channel_also_goes_through_the_embedding(tmp_path):
    db, mid, _ids = _db(tmp_path, in_room=True)
    with Store.open_worker(db) as w:
        assert proc.serve(io.BytesIO(_input([("L", tone(1.5, 440)), ("L", tone(1.5, 220))])), io.BytesIO(), w, mid, FakeEngines(),
                          names_sync=True) == 0
        caps = w.captions.list_for_meeting(mid)
    assert [(c["channel"], c["label"]) for c in caps] == [("L", "Bia"), ("L", "Ana — CFO")]          # U-015: sala presencial


def test_names_unavailable_keep_captions_flowing_without_names(tmp_path):
    db, _mid, _ids = _db(tmp_path)
    with Store(db) as s:
        meetings = [s.create_meeting(f"sem nomes {i}", None, None, None) for i in range(3)]
    for mid, engines, reason in ((meetings[0], FakeEngines(names_error="token do Hugging Face ausente"), "token do Hugging Face ausente"),
                                 (meetings[1], FakeEngines(fail_load="torch não carregou"), "torch não carregou")):
        out = io.BytesIO()
        with Store.open_worker(db) as w:
            assert proc.serve(io.BytesIO(_input([("R", tone(1.5, 220)), ("L", tone(1.5, 440))])), out, w, mid, engines,
                              names_sync=True) == 0
            caps = w.captions.list_for_meeting(mid)
        assert [(c["channel"], c["label"]) for c in caps] == [("R", None), ("L", "Eu — Gerente")]   # L é o usuário pelo canal
        lines = [pr.decode_line(line) for line in out.getvalue().splitlines()]
        assert {"type": "state", "state": "names_unavailable", "reason": reason} in lines
    out = io.BytesIO()
    with Store.open_worker(db) as w:                                                 # nomes desligados no warm-up: nada de nome
        hello = pr.Hello(1000.0, names=False)
        data = _input([("R", tone(1.5, 220)), ("L", tone(1.5, 440))], hello)
        assert proc.serve(io.BytesIO(data), out, w, meetings[2], FakeEngines()) == 0
        assert [c["label"] for c in w.captions.list_for_meeting(meetings[2])] == [None, None]
    assert not any(pr.decode_line(line)["type"] == "state" for line in out.getvalue().splitlines())


def test_names_load_in_parallel_and_captions_do_not_wait(tmp_path):
    """Sem `names_sync`: a primeira legenda sai sem nome enquanto o torch carrega; a seguinte já tem nome."""
    db, mid, _ids = _db(tmp_path)
    loaded = threading.Event()
    engines = FakeEngines(load_event=loaded)
    session_ref: list[proc.LiveSession] = []

    def before(call: int) -> None:
        if call == 2:
            loaded.set()
            deadline = time.monotonic() + 5
            while session_ref[0].embedder is None and time.monotonic() < deadline:
                time.sleep(0.005)

    engines.before = before
    with Store.open_worker(db) as w:
        session = proc.LiveSession(io.BytesIO(_input([("R", tone(1.5, 220)), ("R", tone(1.5, 220))])), io.BytesIO(), w, mid, engines)
        session_ref.append(session)
        assert session.run() == 0
        labels = [c["label"] for c in w.captions.list_for_meeting(mid)]
    assert labels == [None, "Ana — CFO"] and engines.embedders[0].loaded


def test_real_engines_explain_why_names_are_unavailable_without_importing_torch(tmp_path, store):
    engines = proc.RealEngines(tmp_path / "modelos")
    try:
        with pytest.raises(proc.NamesUnavailableError, match="token do Hugging Face ausente"):
            engines.embedder(store, 2)
        store.settings.upsert_provider("huggingface", "huggingface", api_key="hf_x" + "y" * 10)
        store.set_setting("pipeline.models_dir", str(tmp_path / "modelos"))
        with pytest.raises(proc.NamesUnavailableError, match="pyannote ainda não baixados"):
            engines.embedder(store, 2)
    finally:
        sys.modules.pop("listener.pipeline.models", None)
    assert "torch" not in sys.modules and "pyannote" not in sys.modules
