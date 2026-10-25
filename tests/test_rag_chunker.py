"""F5 L2: trechos do índice (`rag/chunker.py`, puro) e vetores (`rag/vectors.py`, puro): corte por turno, 120 s e 1.400
caracteres, fala longa em fim de frase, ata com e sem carimbo, seção desconhecida, ações, executivo, `reuniao`, hash
determinístico; assinatura centralizada, Hamming, reordenação e a regra do `mu`."""
from __future__ import annotations

import dataclasses

import numpy as np

from listener.llm.render import render_minutes
from listener.model import ChunkKind
from listener.rag import chunker, vectors
from listener.rag.chunker import ActionInput, MeetingInput, Utterance

ANA, BIA = "Ana — CFO", "Bia"


def _u(sid: int, t0: float, t1: float, speaker: str, text: str, pid: int | None = None, words=()) -> Utterance:
    return Utterance(sid, int(t0 * 1000), int(t1 * 1000), speaker, pid, text, tuple(words))


def test_transcript_closes_at_turn_end_after_60s_one_line_per_turn_without_overlap():
    utts = [_u(1, 0, 20, ANA, "bom dia", 1), _u(2, 20, 45, ANA, "vamos ao orçamento", 1), _u(3, 45, 70, BIA, "concordo", 2),
            _u(4, 70, 80, ANA, "fechado", 1), _u(5, 80, 90, BIA, "até sexta", 2)]
    chunks = chunker.transcript_chunks(utts, "Orçamento", "2026-10-25")
    assert [c.text for c in chunks] == [f"{ANA}: bom dia vamos ao orçamento\n{BIA}: concordo", f"{ANA}: fechado\n{BIA}: até sexta"]
    first, second = chunks
    assert (first.t_start_ms, first.t_end_ms, first.seg_first, first.seg_last) == (0, 70_000, 1, 3)       # fim do turno da Bia > 60 s
    assert first.speakers == f"{ANA}, {BIA}" and first.person_ids == (1, 2) and first.kind == ChunkKind.TRANSCRIPT
    assert (second.t_start_ms, second.t_end_ms, second.seg_first, second.seg_last) == (70_000, 90_000, 4, 5)
    assert first.title == "Orçamento" and first.text_hash != second.text_hash


def test_transcript_closes_mid_turn_before_120s_or_1400_chars():
    long_turn = [_u(i, i * 25, i * 25 + 25, ANA, f"fala {i}", 1) for i in range(6)]          # um turno só de 150 s
    chunks = chunker.transcript_chunks(long_turn, "", "2026-10-25")
    assert [(c.t_start_ms, c.t_end_ms) for c in chunks] == [(0, 100_000), (100_000, 150_000)]
    assert chunks[1].text == f"{ANA}: fala 4 fala 5"                                          # a linha recomeça com o rótulo
    words = "palavra " * 40                                                                    # ~320 caracteres por fala
    many = [_u(i, i * 2, i * 2 + 2, BIA, words.strip(), None) for i in range(10)]               # 20 s, ~3.200 caracteres
    chunks = chunker.transcript_chunks(many, "", "2026-10-25")
    assert all(len(c.text) <= chunker.BODY_MAX for c in chunks) and len(chunks) == 3
    assert [c.seg_first for c in chunks] == [0, 4, 8] and chunks[0].person_ids == ()


def test_long_utterance_is_cut_at_sentence_end_with_word_times_or_proportional():
    sentence = "Esta frase tem umas palavras para encher o trecho. "
    text = (sentence * 60).strip()                                                              # ~3.000 caracteres, uma fala
    tokens = text.split()
    words = [(10_000 + i * 100, 10_000 + i * 100 + 90, w) for i, w in enumerate(tokens)]
    chunks = chunker.transcript_chunks([_u(7, 10, 10 + len(tokens) / 10, ANA, text, 1, words)], "", "2026-10-25")
    assert len(chunks) == 3 and all(len(c.text) <= chunker.BODY_MAX for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)                                             # corte em fim de frase
    starts = [c.t_start_ms or 0 for c in chunks]
    assert starts[0] == 10_000 and starts == sorted(starts)
    first_words = [c.text.removeprefix(f"{ANA}: ").split()[0] for c in chunks]
    assert first_words == ["Esta"] * 3
    n_first = len(chunks[0].text.removeprefix(f"{ANA}: ").split())
    assert starts[1] == words[n_first][0]                                                       # pelo `words_json`
    plain = chunker.transcript_chunks([_u(8, 0, 300, ANA, text)], "", "2026-10-25")              # sem palavras: proporcional
    assert plain[0].t_start_ms == 0 and 90_000 < (plain[1].t_start_ms or 0) < 150_000 and plain[-1].t_end_ms == 300_000
    assert chunker.split_text("x" * 3000, 1400) == ["x" * 1400, "x" * 1400, "x" * 200]          # frase sem espaço: corte seco


def _minutes_md() -> str:
    data = {"context": "Reunião de orçamento.",
            "decisions": [{"text": "Aprovar o orçamento de 2027", "segment_ids": [11]}, {"text": "Contratar auditoria", "segment_ids": []}],
            "actions": [{"text": "Enviar a planilha", "owner_text": "Ana", "owner_person_id": None, "due_text": None,
                         "due_date": "2026-10-30", "segment_ids": [11]}],
            "highlights": [{"marker_id": 5, "text": "o número final", "segment_ids": [12]}],
            "open_points": [{"text": "Prazo do fundo", "segment_ids": [12]}],
            "next_steps": [{"text": "Nova reunião na sexta", "segment_ids": []}]}
    return render_minutes(data, {11: ("x", 63_000), 12: ("y", 3_725_000)}, "pt-BR", markers={5: "importante"})


def test_minutes_items_by_section_with_and_without_stamp_unknown_section_and_no_actions():
    md = _minutes_md() + "\n## Riscos do projeto\n\n- Câmbio alto [02:00]\n\nUm parágrafo solto.\n"
    items = chunker.parse_minutes(md)
    assert items == [(ChunkKind.MINUTES, "Reunião de orçamento.", None), (ChunkKind.DECISION, "Aprovar o orçamento de 2027", 63_000),
                     (ChunkKind.DECISION, "Contratar auditoria", None), (ChunkKind.HIGHLIGHT, "importante — o número final", 3_725_000),
                     (ChunkKind.PENDING, "Prazo do fundo", 3_725_000), (ChunkKind.NEXT_STEP, "Nova reunião na sexta", None),
                     (ChunkKind.MINUTES, "Câmbio alto", 120_000), (ChunkKind.MINUTES, "Um parágrafo solto.", None)]
    assert not any("planilha" in text for _k, text, _t in items)                                 # a seção Ações fica de fora
    edited = "## Decisions\n\n* Approve budget [1:02:03]\n1. Second item\n   continuing here\n- —\n"
    assert chunker.parse_minutes(edited) == [(ChunkKind.DECISION, "Approve budget", 3_723_000),
                                             (ChunkKind.DECISION, "Second item continuing here", None)]


def test_build_chunks_actions_executive_meeting_and_deterministic_hash():
    m = MeetingInput("Orçamento 2027", "2026-10-25", 3_900_000, (ANA, BIA), (1, 2),
                     (_u(1, 0, 30, ANA, "bom dia", 1), _u(2, 30, 70, BIA, "vamos lá", 2)), _minutes_md(),
                     (ActionInput("Enviar a planilha", "Carla", 9, "2026-10-30", 63_000, 11),
                      ActionInput("Revisar", None, None, None, None, None)),
                     "Resumo executivo curto.")
    chunks = chunker.build_chunks(m)
    kinds = [c.kind for c in chunks]
    assert kinds == [ChunkKind.TRANSCRIPT, ChunkKind.MINUTES, ChunkKind.DECISION, ChunkKind.DECISION, ChunkKind.HIGHLIGHT,
                     ChunkKind.PENDING, ChunkKind.NEXT_STEP, ChunkKind.ACTION, ChunkKind.ACTION, ChunkKind.EXECUTIVE, ChunkKind.MEETING]
    action, bare = chunks[7], chunks[8]
    assert action.text == "Enviar a planilha · dono: Carla · prazo: 2026-10-30" and action.speakers == "Carla"
    assert (action.t_start_ms, action.seg_first, action.seg_last, action.person_ids) == (63_000, 11, 11, (1, 2, 9))
    assert bare.text == "Revisar" and bare.t_start_ms is None and bare.person_ids == (1, 2)
    executive, meeting = chunks[9], chunks[10]
    assert executive.text == "Resumo executivo curto." and executive.t_start_ms is None and executive.speakers == ""
    assert meeting.text == f"Reunião: Orçamento 2027\nData: 2026-10-25\nParticipantes: {ANA}, {BIA}\nDuração: 1 h 05 min"
    assert meeting.person_ids == (1, 2) and meeting.t_start_ms is None
    assert chunker.embed_input("Orçamento 2027", "2026-10-25", "corpo") == "Orçamento 2027 · 2026-10-25\ncorpo"
    assert len(chunker.header("t" * 300, "2026-10-25")) == chunker.HEADER_MAX and chunker.header("", "2026-10-25") == "2026-10-25"
    again = chunker.build_chunks(m)
    assert [c.text_hash for c in again] == [c.text_hash for c in chunks]                         # determinístico
    shifted = MeetingInput(m.title, m.date, m.duration_ms, m.participants, m.participant_ids,
                           (_u(1, 1, 31, ANA, "bom dia", 1), _u(2, 31, 71, BIA, "vamos lá", 2)), m.minutes_md, m.actions, m.executive)
    moved = chunker.build_chunks(shifted)
    assert moved[0].text_hash == chunks[0].text_hash and moved[0].t_start_ms == 1000              # tempo fora do hash
    renamed = chunker.build_chunks(dataclasses.replace(m, title="Outro título"))
    assert not {c.text_hash for c in renamed} & {c.text_hash for c in chunks}                     # o título entra em todo trecho
    empty = chunker.build_chunks(MeetingInput("", "2026-10-25", None))
    assert [c.kind for c in empty] == [ChunkKind.MEETING] and "sem título" in empty[0].text and "Duração: —" in empty[0].text


def test_signatures_hamming_rerank_and_mu_rule():
    rng = np.random.default_rng(3)
    common = rng.standard_normal(64).astype(np.float32)
    base = vectors.normalize(common + 0.6 * rng.standard_normal((500, 64)).astype(np.float32))    # anisotrópico
    assert np.allclose(np.linalg.norm(base, axis=1), 1.0)
    mu = base.mean(axis=0)
    sigs = vectors.signatures(base, mu)
    assert sigs.shape == (500, 8) and sigs.dtype == np.uint8
    assert np.array_equal(sigs[0], np.packbits((base[0] - mu) > 0))
    words = vectors.as_words(sigs)
    q = vectors.normalize(base[42] + 0.02 * rng.standard_normal(64).astype(np.float32))[0]
    cand = vectors.candidates(words, vectors.signatures(q, mu)[0], k=20)
    assert 42 in cand.tolist() and len(cand) == 20
    pos, scores = vectors.rerank(q, base[cand], 3)
    assert cand[pos[0]] == 42 and scores[0] >= scores[1] >= scores[2]
    mask = np.zeros(500, bool)
    mask[:10] = True
    only = vectors.candidates(words, vectors.signatures(q, mu)[0], k=50, mask=mask)
    assert sorted(only.tolist()) == list(range(10))                                              # filtro antes do top-k
    assert vectors.n_candidates(50) == 50 and vectors.n_candidates(5_000) == 200 and vectors.n_candidates(60_000) == 600
    assert vectors.n_candidates(10_000_000) == vectors.MAX_CANDIDATES
    assert vectors.mu_due(64, 0) and not vectors.mu_due(100, 64) and vectors.mu_due(128, 64)
    assert vectors.mu_due(3000, 1500) and not vectors.mu_due(9000, 2048) and not vectors.mu_due(0, 0)
    assert vectors.hamming(words[:2], words[0]).tolist()[0] == 0
