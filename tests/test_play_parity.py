# -*- coding: utf-8 -*-
"""The browser version (play/js/*.js) must compute exactly what the Python code computes.

Runs the JS through Node (tests/js/harness.js); skipped if `node` is not installed.
The posterior matrices are SIMULATED by play/js/simulate.js (no recording, no model):
for the same matrix, JS and the Python functions in src/ must agree on
  - CTC forced alignment (src/ctc_align.py)              -> identical segments
  - GOP, score, level, "heard as" (pronunciation._phone_gop)
  - greedy transcript, completeness (edit distance), overall score
  - hints (feedback.make_hint), thresholds (thresholds.py, both calibrated and legacy)
  - text difficulty (cefr.py), offline chat partner (chat_coach.py)
  - fluency / intonation formulas (prosody.py); pause detection (needs librosa)
"""
import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")
HARNESS = ROOT / "tests" / "js" / "harness.js"

from ctc_align import ctc_forced_align  # noqa: E402
from g2p import CanonPhone  # noqa: E402
from pronunciation import PronunciationScorer, _align_ops, _group_by_word, _is_real_phone  # noqa: E402
from thresholds import Thresholds  # noqa: E402


def run_js(cmd, tmp_path):
    f = tmp_path / "cmd.json"
    f.write_text(json.dumps(cmd, ensure_ascii=False), encoding="utf-8")
    p = subprocess.run([NODE, str(HARNESS), str(f)], capture_output=True, timeout=600)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return json.loads(p.stdout.decode("utf-8"))


def _vocab():
    v = json.loads((ROOT / "data" / "model_vocab.json").read_text(encoding="utf-8"))
    return v["vocab_order"], v["blank_id"]


def _thresholds(name):
    d = json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
    a = sorted([(float(g), float(s)) for g, s in d["gop_to_score_anchors"]], key=lambda x: x[0], reverse=True)
    return Thresholds(bool(d.get("calibrated")), a, float(d["level_cutoffs"]["green"]), float(d["level_cutoffs"]["yellow"]))


class FakeScorer:
    """The parts of PronunciationScorer that do not need the model."""

    def __init__(self, th):
        order, blank = _vocab()
        self.vocab = {k: v for k, v in order}
        self.id2tok = {v: k for k, v in order}
        self.blank_id = blank
        self.phone_ids = [tid for tok, tid in order if _is_real_phone(tok)]
        self.thresholds = th


def python_score(fake, log_probs, phones):
    """Same steps as PronunciationScorer.score() after the model call (frame stride 0.02 s)."""
    from feedback import make_hint
    canon = [CanonPhone(**p) for p in phones]
    canon = [c for c in canon if c.ipa in fake.vocab]
    ids = [fake.vocab[c.ipa] for c in canon]
    transcript = PronunciationScorer._greedy_transcript(fake, log_probs)
    segs = ctc_forced_align(log_probs, ids, fake.blank_id)
    ps = [PronunciationScorer._phone_gop(fake, log_probs, c, ids[i], s, e, 0.02) for c, (i, s, e) in zip(canon, segs)]
    for p in ps:
        if p.level != "good":
            p.hint = make_hint(p)
    words = _group_by_word(ps)
    acc = float(np.mean([p.score for p in ps]))
    comp = PronunciationScorer._completeness(fake, canon, transcript)
    overall = PronunciationScorer._combine_scores(fake, acc, comp, None)
    return {"segments": [list(s) for s in segs], "phones": ps, "words": words, "transcript": transcript,
            "acc": acc, "comp": comp, "overall": overall}


def _sentences():
    return json.loads((ROOT / "data" / "g2p_sentences.json").read_text(encoding="utf-8"))["levels"]


# ------------------------------------------------------------------ scoring pipeline
@pytest.mark.parametrize("legacy", [False, True], ids=["calibrated", "legacy"])
def test_scoring_pipeline_matches_python(tmp_path, legacy):
    sents = _sentences()
    cases = []
    for level, items in sents.items():
        for i in range(len(items)):
            for k, sc in enumerate(["good", "sub", "del", "mumble"]):
                cases.append({"level": level, "index": i, "scenario": sc, "seed": 100 + 7 * i + k,
                              "noise": [0.2, 0.5, 0.8][(i + k) % 3], "legacy": legacy})
    js = run_js({"cmd": "score", "cases": cases}, tmp_path)
    fake = FakeScorer(_thresholds("thresholds_legacy.json" if legacy else "thresholds.json"))
    seen_levels, seen_del = set(), 0
    for c, r in zip(cases, js):
        phones = sents[c["level"]][c["index"]]["phones"]
        lp = np.array(r["logProbs"], dtype=np.float64).reshape(r["T"], r["V"])
        py = python_score(fake, lp, phones)
        assert r["segments"] == py["segments"], c
        assert r["transcript"] == py["transcript"], c
        for a, b in zip(r["phones"], py["phones"]):
            assert abs(a["gop"] - b.gop) < 1.5e-3 and abs(a["score"] - b.score) < 0.11, (c, a, b)
            assert a["level"] == b.level and a["perceived"] == b.perceived_ipa and a["hint"] == b.hint, (c, a, b)
            assert (a["start_sec"], a["end_sec"]) == (b.start_sec, b.end_sec)
            seen_levels.add(b.level)
        assert [w["word"] for w in r["words"]] == [w.word for w in py["words"]]
        for a, b in zip(r["words"], py["words"]):
            assert abs(a["score"] - b.score) < 0.11
        comp = py["comp"]
        assert (r["completeness"]["n_del"], r["completeness"]["n_ins"]) == (comp.n_deletions, comp.n_insertions)
        assert r["completeness"]["missing"] == comp.missing_words and abs(r["completeness"]["score"] - comp.score) < 1e-9
        assert abs(r["accuracyRaw"] - py["acc"]) < 1e-9 and abs(r["overallRaw"] - py["overall"]) < 1e-9
        seen_del += comp.n_deletions > 0
    # the scenarios really exercise all colours and the omission path
    assert seen_levels == {"good", "warn", "bad"} and seen_del > 0


def test_ctc_align_random_matrices(tmp_path):
    rng = np.random.default_rng(5)
    cases = []
    for k in range(60):
        T, V, N = int(rng.integers(3, 40)), 8, int(rng.integers(1, 12))
        lp = rng.normal(size=(T, V))
        lp = lp - np.log(np.exp(lp).sum(1, keepdims=True))
        toks = [int(x) for x in rng.integers(1, V, size=N)]
        cases.append({"logProbs": lp.ravel().tolist(), "T": T, "V": V, "tokens": toks, "blank": 0})
    js = run_js({"cmd": "align", "cases": cases}, tmp_path)
    for c, got in zip(cases, js):
        lp = np.array(c["logProbs"]).reshape(c["T"], c["V"])
        assert got == [list(s) for s in ctc_forced_align(lp, c["tokens"], 0)]


def test_thresholds_match(tmp_path):
    gops = [x / 100 for x in range(-1200, 50, 7)] + [0.0, -0.05, -1.811, -3.622, -8.751, -10.807, -0.3, -5.0]
    js = run_js({"cmd": "thresholds", "gops": gops}, tmp_path)
    for name, got in zip(["thresholds.json", "thresholds_legacy.json"], js):
        th = _thresholds(name)
        for g, (s, lv) in zip(gops, got):
            assert abs(th.gop_to_score(g) - s) < 1e-12 and th.level(th.gop_to_score(g)) == lv


def test_hints_match(tmp_path):
    from feedback import make_hint
    from pronunciation import PhoneScore
    order, _ = _vocab()
    phones = [t for t, _ in order if _is_real_phone(t)]
    cases = [[a, b] for a in phones for b in phones + [""]]
    js = run_js({"cmd": "hints", "cases": cases}, tmp_path)
    for (a, b), got in zip(cases, js):
        p = PhoneScore(ipa=a, arpabet="", word="", word_index=0, gop=0, score=0, level="bad",
                       perceived_ipa=b, start_sec=0, end_sec=0)
        assert make_hint(p) == got, (a, b)


def test_python_round_port(tmp_path):
    rnd = random.Random(1)
    cases = [[2.25, 1], [0.125, 2], [96.25, 1], [2.675, 2], [0.5, 0], [1.5, 0], [-2.25, 1], [97.5, 1], [-0.0001, 3]]
    cases += [[rnd.uniform(-20, 100), rnd.choice([1, 2, 3])] for _ in range(400)]
    cases += [[k / 8, 1] for k in range(-80, 800)]
    js = run_js({"cmd": "round", "cases": cases}, tmp_path)
    for (x, nd), got in zip(cases, js):
        assert round(x, nd) == got, (x, nd)


def test_align_ops_match(tmp_path):
    rnd = random.Random(3)
    alpha = list("abcdef")
    cases = [[[rnd.choice(alpha) for _ in range(rnd.randint(0, 9))], [rnd.choice(alpha) for _ in range(rnd.randint(0, 9))]]
             for _ in range(200)]
    js = run_js({"cmd": "alignops", "cases": cases}, tmp_path)
    for (a, b), got in zip(cases, js):
        assert [list(o) for o in _align_ops(a, b)] == got


# ------------------------------------------------------------------ text tools
TEXTS = ["I have a red apple.", "Although it was raining, we decided to walk to the station.",
         "The committee thoroughly reviewed the comprehensive proposal before approving it.",
         "", "Hi!", "I is happy. You are nice? Yes!", "Don't stop believing, it's fine.",
         "Internationalization considerations notwithstanding, extraordinary responsibilities remain."]


def test_cefr_matches(tmp_path):
    import cefr
    texts = TEXTS + [s["text"] for v in _sentences().values() for s in v]
    js = run_js({"cmd": "cefr", "texts": texts}, tmp_path)
    for t, got in zip(texts, js):
        e = cefr.estimate_text_cefr(t)
        assert got["level"] == e.level and got["complexity"] == e.complexity, t
        if e.n_words:
            assert (got["n_words"], got["avg_word_len"], got["long_word_ratio"], got["avg_sentence_len"], got["avg_syllables"]) == \
                   (e.n_words, e.avg_word_len, e.long_word_ratio, e.avg_sentence_len, e.avg_syllables), t
    words = ["apple", "I", "comprehensive", "make", "little", "the", "rhythm", "queue", "", "e", "fire", "table"]
    js = run_js({"cmd": "syllables", "words": words}, tmp_path)
    assert js == [cefr._count_syllables(w) for w in words]


def test_offline_chat_matches(tmp_path):
    import chat_coach
    import config
    if config.llm_enabled():
        pytest.skip("OPENAI_API_KEY is set: chat() would call the real model")
    msgs = ["", "   ", "I is a student", "i has two cats", "Yesterday I go to the park", "Yesterday I eat rice",
            "Hello there", "我喜欢英语", "Hi 👋", "What do you think about AI?"]
    cases = [[m, lv] for m in msgs for lv in ["A1", "A2", "B1", "B2", "C1"]]
    js = run_js({"cmd": "chat", "cases": cases}, tmp_path)
    for (m, lv), got in zip(cases, js):
        assert chat_coach.chat([], m, lv) == got, (m, lv)
    import cefr
    rcases = [[t, lv] for t in TEXTS for lv in ["A1", "A2", "B1", "B2"]]
    js = run_js({"cmd": "rewrite", "cases": rcases}, tmp_path)
    for (t, lv), got in zip(rcases, js):
        assert cefr.rewrite_if_too_hard(t, lv)[2] == got


# ------------------------------------------------------------------ prosody
def test_fluency_and_intonation_formulas(tmp_path):
    import prosody
    fl = [[r, n, tot] for r in [0, 2.5, 5.9, 6, 7.3, 9, 12, 16, 17.5, 20, 23, 40] for n, tot in [(0, 0), (1, 0.5), (2, 0.6), (2, 0.61), (4, 2.3)]]
    js = run_js({"cmd": "fluency", "cases": fl}, tmp_path)
    for (r, n, tot), (s, tips) in zip(fl, js):
        res = prosody.ProsodyResult(speaking_rate_pps=r, n_pauses=n, total_pause_sec=tot)
        assert abs(prosody._fluency_score(res, 3.0) - s) < 1e-9 and res.tips == tips
    iv = [-1, 0, 0.3, 1.19, 1.2, 2.0, 2.99, 3.0, 7.5]
    js = run_js({"cmd": "intonation", "cases": iv}, tmp_path)
    for v, (s, tips) in zip(iv, js):
        res = prosody.ProsodyResult(pitch_variation_st=v)
        assert abs(prosody._intonation_score(res) - s) < 1e-9 and res.tips == tips


def _tone(sr, f0, sec, amp=0.5):
    t = np.arange(int(sr * sec)) / sr
    return amp * (np.sin(2 * np.pi * f0 * t) + 0.5 * np.sin(4 * np.pi * f0 * t) + 0.25 * np.sin(6 * np.pi * f0 * t)) / 1.75


def test_pause_detection_matches_librosa(tmp_path):
    pytest.importorskip("librosa")
    import prosody
    sr, rng = 16000, np.random.default_rng(0)
    z = lambda s: 0.0005 * rng.standard_normal(int(sr * s))  # noqa: E731
    sigs = [np.concatenate([z(0.3), _tone(sr, 150, 0.5), z(0.3), _tone(sr, 180, 0.4), z(0.1), _tone(sr, 120, 0.5), z(0.4)]),
            np.concatenate([_tone(sr, 200, 1.0), z(0.25), _tone(sr, 200, 0.2), z(0.5), _tone(sr, 210, 0.3)]),
            np.concatenate([z(0.2), _tone(sr, 140, 1.2), z(0.2)])]
    sigs = [s.astype(np.float32) for s in sigs]
    js = run_js({"cmd": "pauses", "sr": sr, "signals": [s.tolist() for s in sigs]}, tmp_path)
    import librosa
    for s, (n, total, rms50) in zip(sigs, js):
        assert (n, total) == prosody._detect_pauses(s, sr)
        ref = librosa.feature.rms(y=s, frame_length=400, hop_length=160)[0][:50]
        np.testing.assert_allclose(rms50, ref, rtol=1e-4, atol=1e-7)


def test_yin_f0_on_synthetic_tones(tmp_path):
    """JS YIN (the page's F0) recovers known pitches; silence stays unvoiced."""
    sr = 16000
    f0s = [90.0, 150.0, 220.0, 330.0]
    sigs = [_tone(sr, f, 0.6) for f in f0s] + [np.zeros(int(sr * 0.5))]
    t = np.arange(int(sr * 1.0)) / sr
    glide = 0.5 * np.sin(2 * np.pi * (120 * t + 50 * t ** 2))        # 120 -> 220 Hz
    sigs.append(glide)
    js = run_js({"cmd": "yin", "sr": sr, "signals": [s.astype(np.float32).tolist() for s in sigs]}, tmp_path)
    for f, track in zip(f0s, js[:4]):
        v = [x for x in track if x is not None]
        assert len(v) >= 0.9 * len(track)
        assert abs(np.median(v) / f - 1) < 0.02, (f, np.median(v))
    assert all(x is None for x in js[4])
    g = [x for x in js[5] if x is not None]
    assert 110 < min(g) and max(g) < 235 and g[-1] > g[0] + 60


# ------------------------------------------------------------------ data locks
def test_page_data_matches_sources(tmp_path):
    """Sentences, phonemes, vocabulary and blank on the page = the project's data files."""
    d = run_js({"cmd": "data"}, tmp_path)
    sents = json.loads((ROOT / "data" / "sentences.json").read_text(encoding="utf-8"))
    for level, items in sents.items():
        if level.startswith("_"):
            continue
        assert [s["text"] for s in d["sentences"][level]] == items
    assert d["sentences"] == _sentences()
    order, blank = _vocab()
    assert d["vocabOrder"] == order and d["blank"] == blank
    assert len(order) == 44 and len(d["phoneIds"]) == 38
    assert d["config"]["levels"] == [k for k in sents if not k.startswith("_")]
    # every canonical phone is a model token (otherwise the Python scorer would drop it)
    toks = {t for t, _ in order}
    assert all(p["ipa"] in toks for v in _sentences().values() for s in v for p in s["phones"])


def test_g2p_export_is_current():
    """data/g2p_sentences.json == src/g2p.py output (needs g2p_en)."""
    pytest.importorskip("g2p_en")
    from g2p import G2P
    g = G2P()
    for level, items in _sentences().items():
        for s in items:
            got = [{"ipa": p.ipa, "arpabet": p.arpabet, "word": p.word, "word_index": p.word_index,
                    "stress": p.stress, "is_vowel": p.is_vowel} for p in g.phonemize(s["text"])]
            assert got == s["phones"], s["text"]
