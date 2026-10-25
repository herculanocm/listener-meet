"""Revisão de falantes e leitura (F2 L6): lógica sem Qt sobre um job do pipeline falso; revisão por teclado e leitura com
clique em pytest-qt offscreen; abertura automática pelo controlador quando há clusters do job."""
from __future__ import annotations

import pytest

from listener.model import ClusterStatus, EventType, MeetingStatus
from listener.pipeline import review
from listener.pipeline.runner import JobRunner
from listener.store import playback
from tests.fake_pipeline import FakeDeps
from tests.test_runner import CFG, SCRIPT, _record

pytest.importorskip("PySide6")


@pytest.fixture
def platform(tmp_path, clock):
    """Tons a 16 kHz mono nos dois canais: sem reamostragem bloco a bloco, a gravação falsa custa 10x menos (a reamostragem
    é coberta em test_pipeline_core e test_recorder)."""
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def _processed(platform, store) -> tuple[int, list[review.ReviewItem]]:
    store.upsert_me("Eu", role="Gerente")
    mid = _record(platform, store)
    store.jobs.enqueue(mid)
    job = store.jobs.claim(pid=1)
    assert JobRunner(store, FakeDeps(SCRIPT), job, config=CFG, heartbeat_thread=False).run() == "concluído"
    items = review.review_items(store, mid)
    return mid, items


def test_review_logic_confirm_new_person_merge_ignore_and_finish(platform, store):
    mid, items = _processed(platform, store)
    assert len(items) == 2 and all(i.pending and i.has_centroid and i.channel == "R" for i in items)      # o L do usuário não entra
    assert [i.title for i in items] == ["Voz SPK_0", "Voz SPK_1"] and items[0].summary() == "sem nome" and "min" in items[0].minutes_text
    assert len(review.pending(items)) == 2
    with pytest.raises(ValueError, match="escolha a pessoa"):
        review.confirm(store, items[0].cluster_id)                                   # sem sugestão, Enter não basta
    ana = review.new_person(store, items[0].cluster_id, "Ana", role="CFO")
    c0 = store.segments.get_cluster(items[0].cluster_id)
    assert c0["status"] == ClusterStatus.CONFIRMED and c0["person_id"] == ana
    segs = [s for s in store.segments.list_segments(mid) if s["cluster_id"] == items[0].cluster_id]
    assert segs and all(s["person_id"] == ana and s["role_at_time"] == "CFO" for s in segs)
    prof = store.profiles.profile_for(ana, "fake-tone-256")
    assert prof["n_samples"] == 1 and prof["sim_mean"] is None                       # sem similaridade medida: só o vetor
    emb = store.profiles.embeddings(prof["id"])
    assert [e[2] for e in emb] == ["confirmacao"]
    assert store._q("SELECT segment_id FROM voice_embeddings WHERE source = 'confirmacao'")[0][0] == segs[0]["id"]
    assert store._q("SELECT field, after FROM revisions WHERE entity = 'cluster'")[0]["after"] == str(ana)
    assert EventType.PROFILE_CONFIRM in [e["type"] for e in store.list_events(mid)]
    # mesma pessoa de novo (outro cluster): calibra com a similaridade ao perfil existente
    sim = review._similarity_to(store, store.segments.get_cluster(items[1].cluster_id), ana)
    assert sim is not None and sim < 0.2
    review.confirm(store, items[1].cluster_id, ana)
    prof = store.profiles.profile_for(ana, "fake-tone-256")
    assert prof["n_samples"] == 2 and prof["sim_mean"] == pytest.approx(sim) and prof["threshold"] is None
    assert review.pending(review.review_items(store, mid)) == []
    # ignorar e mesclar: segmentos perdem a pessoa; a mescla segue a pessoa do alvo e registra revisão
    review.ignore(store, items[1].cluster_id)
    assert store.segments.get_cluster(items[1].cluster_id)["status"] == ClusterStatus.IGNORED
    assert all(s["person_id"] is None for s in store.segments.list_segments(mid) if s["cluster_id"] == items[1].cluster_id)
    review.merge(store, items[1].cluster_id, items[0].cluster_id)
    c1 = store.segments.get_cluster(items[1].cluster_id)
    assert c1["merged_into"] == items[0].cluster_id and c1["person_id"] == ana and c1["status"] == ClusterStatus.CONFIRMED
    assert review.review_items(store, mid)[1].summary().startswith("mesclada")
    with pytest.raises(ValueError):
        review.merge(store, items[0].cluster_id, items[1].cluster_id)                 # ciclo
    with pytest.raises(ValueError):
        review.merge(store, items[0].cluster_id, items[0].cluster_id)
    assert store.get_meeting(mid)["status"] == MeetingStatus.PROCESSED
    review.finish(store, mid)
    assert store.get_meeting(mid)["status"] == MeetingStatus.REVIEWED
    assert review.new_person(store, items[0].cluster_id, "ana") == ana                # nome repetido reaproveita a pessoa
    with pytest.raises(ValueError):
        review.new_person(store, items[0].cluster_id, "  ")


def test_clip_range_from_segments_or_stitch_and_transcript_rows(platform, store):
    mid, items = _processed(platform, store)
    rng = review.clip_range(store, items[0].cluster_id)
    assert rng is not None and rng[0] == "R" and 0 < rng[2] - rng[1] <= 5000
    samples, sr = review.decode_clip(store, mid, *rng)
    assert sr == 16000 and len(samples) == round((rng[2] - rng[1]) * 16)
    assert review.clip_range(store, 9999) is None
    # antes dos segmentos (job interrompido na transcrição): o trecho vem do artefato da costura, com áudio decodificável
    plat3 = type(platform)(platform.tmp)
    mid3 = _record(plat3, store)
    store.jobs.enqueue(mid3)
    job3 = store.jobs.claim(pid=1)
    assert JobRunner(store, FakeDeps(SCRIPT, fail_once_at={("R", 0)}), job3, config=CFG, heartbeat_thread=False).run() == "erro"
    pending_items = review.review_items(store, mid3)
    assert pending_items and store.segments.count_segments(mid3) == 0
    rng3 = review.clip_range(store, pending_items[0].cluster_id)
    assert rng3 is not None and rng3[0] == "R" and 0 < rng3[2] - rng3[1] <= 5000
    assert review.decode_clip(store, mid3, *rng3)[0].size == round((rng3[2] - rng3[1]) * 16)
    # leitura: nome, cargo e ponto de reprodução (plataforma nova: o relógio manual da anterior já andou 3 s)
    mid2, items2 = _processed(type(platform)(platform.tmp), store)
    review.new_person(store, items2[0].cluster_id, "Ana", role="CFO")
    rows = review.transcript_rows(store, mid2)
    assert [r.line for r in rows] == ["[00:00] Ana — CFO: bom dia pessoal", "[00:00] Eu — Gerente: oi gente sim claro",
                                      "[00:01] Voz SPK_1: tudo certo então"]
    assert rows[0].play_from_ms == 50 and rows[1].play_from_ms == 150 and rows[2].play_from_ms == 1600 - 150
    assert rows[2].role == "" and rows[0].stamp == "00:00"


def test_review_dialog_keyboard_and_audio(qtbot, platform, store, monkeypatch):
    from PySide6.QtCore import Qt

    from listener.ui.review import ReviewDialog

    mid, items = _processed(platform, store)
    ana = store.people.create_person("Ana")
    store.segments.update_cluster(items[0].cluster_id, person_id=ana, status=ClusterStatus.AMBIGUOUS, similarity=0.7, margin=0.03)
    played: list[tuple[int, int]] = []
    monkeypatch.setattr(playback, "play", lambda samples, sr: played.append((len(samples), sr)))
    dlg = ReviewDialog(store, mid)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.list.count() == 2 and dlg.current_cluster_id() == items[0].cluster_id and "2 voz(es)" in dlg.header.text()
    assert dlg.list.hasFocus() or not dlg.isActiveWindow()                             # foco na lista ao abrir
    assert "pyannote: Ana (similaridade 0.70, margem 0.03)" in dlg.list.item(0).text()
    qtbot.keyClick(dlg.list, Qt.Key.Key_Space)                                        # ouvir 5 s: decodifica numa Task
    qtbot.waitUntil(lambda: bool(played), timeout=3000)
    assert dlg.played and played[0][1] == 16000 and played[0][0] <= 5 * 16000
    qtbot.keyClick(dlg, Qt.Key.Key_Return)                                            # Enter confirma a sugestão
    assert store.segments.get_cluster(items[0].cluster_id)["status"] == ClusterStatus.CONFIRMED
    assert dlg.current_cluster_id() == items[1].cluster_id                            # pula para a próxima pendente
    qtbot.keyClick(dlg.list, Qt.Key.Key_Up)                                          # setas: na lista, como o usuário
    assert dlg.current_cluster_id() == items[0].cluster_id
    qtbot.keyClick(dlg.list, Qt.Key.Key_Down)
    assert dlg.create_person("Bia", "CTO")
    assert store.segments.get_cluster(items[1].cluster_id)["person_id"] == store.people.find_by_name("Bia")["id"]
    assert dlg.header.text().startswith("Tudo confirmado")
    dlg.list.setCurrentRow(1)
    qtbot.keyClick(dlg, Qt.Key.Key_I)
    assert store.segments.get_cluster(items[1].cluster_id)["status"] == ClusterStatus.IGNORED
    dlg.list.setCurrentRow(1)
    assert dlg.merge_into(items[0].cluster_id) and store.segments.get_cluster(items[1].cluster_id)["merged_into"] == items[0].cluster_id
    assert dlg.btn_transcript.isEnabled()
    dlg.finish()
    assert store.get_meeting(mid)["status"] == MeetingStatus.REVIEWED and dlg.result() == dlg.DialogCode.Accepted


def test_transcript_dialog_lists_and_click_plays_from_first_word(qtbot, platform, store, monkeypatch):
    from listener.ui.transcript import TranscriptDialog

    mid, items = _processed(platform, store)
    review.new_person(store, items[0].cluster_id, "Ana", role="CFO")
    played: list[tuple[int, int]] = []
    monkeypatch.setattr(playback, "play", lambda samples, sr: played.append((len(samples), sr)))
    opened: list[int] = []
    dlg = TranscriptDialog(store, mid, on_review=opened.append)
    qtbot.addWidget(dlg)
    assert dlg.model.rowCount() == 3 and dlg.model.data(dlg.model.index(0), 0) == "[00:00] Ana — CFO: bom dia pessoal"
    assert "canal R" in dlg.model.data(dlg.model.index(0), 3)
    dlg.view.clicked.emit(dlg.model.index(2))
    qtbot.waitUntil(lambda: bool(played), timeout=3000)
    assert dlg.played == [(1450, 2890)] and played[0] == ((2890 - 1450) * 16, 16000)   # primeira palavra 1600 ms - 150 ms
    dlg.btn_review.click()
    assert opened == [mid]
    store._x("DELETE FROM segments WHERE meeting_id = ?", (mid,))
    dlg.reload()
    assert dlg.model.rowCount() == 0 and dlg.status.text().startswith("0 fala(s)")


def test_controller_opens_review_when_clusters_exist_and_on_done(qtbot, qapp, platform, store, monkeypatch):
    from listener.model import JobStatus, JobStep
    from listener.ui.controller import Controller

    mid, _items = _processed(platform, store)
    c = Controller(qapp, platform, store)
    c.tray = None
    try:
        job_id = store.jobs.enqueue(mid)                                              # simula um job rodando com clusters gravados
        store.jobs.claim(pid=7)
        store.jobs.set_progress(job_id, JobStep.TRANSCRIBE_R, 0.6)
        c._poll_queue()
        assert mid in c._reviews and c._reviews[mid].isVisible() and c._reviews[mid].list.count() == 2
        reloads = c._reviews[mid].reloads
        c._poll_queue()
        c._poll_queue()
        assert len(c._reviews) == 1 and c._reviews[mid].reloads == reloads              # R2-26: sem mudança, sem reload
        store.segments.insert_clusters([__import__("listener.store.segments", fromlist=["ClusterInsert"]).ClusterInsert(
            mid, job_id, "R", "SPK_9", 10, None, None, ClusterStatus.UNNAMED)])
        c._poll_queue()
        assert c._reviews[mid].reloads == reloads + 1                                  # clusters mudaram: recarrega
        t = c.open_transcript(mid)
        assert t.model.rowCount() == 3 and c.open_transcript(mid) is t                   # job vigente: o concluído
        store.jobs.finish(job_id, JobStatus.DONE)
        notes: list[str] = []
        monkeypatch.setattr(c, "notify", notes.append)
        c._poll_queue()
        assert notes == ["Planejamento: pronta para revisão de falantes."]
        c._reviews[mid].reject()
        qtbot.waitUntil(lambda: mid not in c._reviews, timeout=2000)
    finally:
        c.emergency_stop()


def test_reading_does_not_name_pending_voices_and_merge_into_suggestion_confirms_nobody(platform, store):
    """R2-07/R2-08: a sugestão guardada em `sem_nome` não vira nome na leitura nem pessoa numa mescla."""
    import numpy as np

    store.upsert_me("Eu", role="Gerente")
    mid = _record(platform, store)
    ana = store.people.create_person("Ana")
    far = np.zeros(256, np.float32)
    far[200] = 1.0                                                                        # perfil longe dos tons
    from listener.model import EmbeddingSource

    store.profiles.confirm(ana, "fake-tone-256", far, None, None, source=EmbeddingSource.ENROLL)
    store.jobs.enqueue(mid)
    job = store.jobs.claim(pid=1)
    assert JobRunner(store, FakeDeps(SCRIPT), job, config=CFG, heartbeat_thread=False).run() == "concluído"
    items = review.review_items(store, mid)
    assert all(i.status == ClusterStatus.UNNAMED and i.person_id == ana for i in items)   # sugestão guardada
    assert items[0].summary().startswith("sem nome; parecida com Ana")
    rows = review.transcript_rows(store, mid)
    assert [r.name for r in rows if r.channel == "R"] == ["Voz SPK_0", "Voz SPK_1"]        # e não "Ana"
    review.merge(store, items[1].cluster_id, items[0].cluster_id)
    c1 = store.segments.get_cluster(items[1].cluster_id)
    assert c1["merged_into"] == items[0].cluster_id and c1["status"] == ClusterStatus.UNNAMED and c1["person_id"] == ana
    assert [e[2] for e in store.profiles.embeddings(store.profiles.profile_for(ana, "fake-tone-256")["id"])] == ["cadastro"]
    review.confirm(store, items[0].cluster_id)                                           # agora sim: Enter aceita a sugestão
    assert review.transcript_rows(store, mid)[0].name == "Ana"
    assert [r.name for r in review.transcript_rows(store, mid) if r.channel == "R"] == ["Ana", "Ana"]   # a mesclada segue o alvo


def test_reconfirming_with_another_person_invalidates_the_old_vector(platform, store):
    """R2-09: "outra pessoa" depois de um erro: o vetor sai do centroide da pessoa anterior (fonte `invalidado`, nada apagado)."""
    mid, items = _processed(platform, store)
    ana = review.new_person(store, items[0].cluster_id, "Ana")
    bia = review.new_person(store, items[0].cluster_id, "Bia")
    pa, pb = store.profiles.profile_for(ana, "fake-tone-256"), store.profiles.profile_for(bia, "fake-tone-256")
    assert pa["n_samples"] == 0 and not pa["centroid"].any() and pb["n_samples"] == 1
    assert [e[2] for e in store.profiles.embeddings(pa["id"])] == ["invalidado"]           # CP3: continua gravado
    assert store._q("SELECT cluster_id FROM voice_embeddings WHERE source = 'invalidado'")[0][0] == items[0].cluster_id
    rev = store._q("SELECT entity, entity_id, field FROM revisions WHERE field = 'invalidado'")
    assert len(rev) == 1 and rev[0]["entity"] == "voice_profile" and rev[0]["entity_id"] == pa["id"]
    assert all(s["person_id"] == bia for s in store.segments.list_segments(mid) if s["cluster_id"] == items[0].cluster_id)
    review.confirm(store, items[0].cluster_id)                                           # Enter de novo na mesma pessoa: nada muda
    assert store.profiles.profile_for(bia, "fake-tone-256")["n_samples"] == 1
