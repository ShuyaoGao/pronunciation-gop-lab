# -*- coding: utf-8 -*-
"""Word grouping in src/g2p.py: punctuation inside a sentence must not shift the word labels.

g2p_en emits [' ', ',', ' '] around a comma; before the fix the word index advanced twice, so in
"Although it was raining, we decided ..." the phones of "decided" were labelled "to".
These tests feed fixed token lists, so they do not need g2p_en.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from g2p import G2P  # noqa: E402


def _g2p_with(tokens):
    g = G2P.__new__(G2P)          # skip __init__ (it would load g2p_en)
    g._g2p = lambda text: tokens
    return g


def test_comma_does_not_shift_words():
    toks = ['R', 'EY1', 'N', 'IH0', 'NG', ' ', ',', ' ', 'W', 'IY1', ' ', 'D', 'IH0', 'S', 'AY1', 'D', 'IH0', 'D', ' ', '.']
    ph = _g2p_with(toks).phonemize("raining, we decided.")
    words = {p.word_index: p.word for p in ph}
    assert words == {0: "raining,", 1: "we", 2: "decided."}
    assert [p.ipa for p in ph if p.word_index == 2] == ["d", "ɪ", "s", "aɪ", "d", "ɪ", "d"]


def test_plain_sentence_unchanged():
    toks = ['AY1', ' ', 'HH', 'AE1', 'V', ' ', 'AH0', ' ', 'R', 'EH1', 'D', ' ', 'AE1', 'P', 'AH0', 'L', ' ', '.']
    ph = _g2p_with(toks).phonemize("I have a red apple.")
    assert [p.word for p in ph] == ["I", "have", "have", "have", "a", "red", "red", "red", "apple.", "apple.", "apple.", "apple."]
    assert [p.word_index for p in ph] == [0, 1, 1, 1, 2, 3, 3, 3, 4, 4, 4, 4]


def test_exported_sentences_have_consistent_words():
    """data/g2p_sentences.json: every word of every sentence gets phones, in order, with its own label."""
    data = json.loads((ROOT / "data" / "g2p_sentences.json").read_text(encoding="utf-8"))
    for items in data["levels"].values():
        for s in items:
            words = s["text"].split()
            idx = sorted({p["word_index"] for p in s["phones"]})
            assert idx == list(range(len(words))), s["text"]
            for p in s["phones"]:
                assert p["word"] == words[p["word_index"]], (s["text"], p)
