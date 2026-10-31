/* SIMULATED posteriors — NOT a real recording and NOT real model output.
 *
 * The real tool runs a wav2vec2 phoneme model (about 1.26 GB) on your recording to get, for every
 * 20 ms frame, a probability for each of the 44 output tokens. That model cannot run in this
 * page, so for the demo we *generate* such a frame x token matrix by program, following the
 * reference phonemes of the sentence, and then run the real scoring math on it.
 *
 * Scenarios: good (read well) / sub (one phone replaced by another) / del (one word skipped) /
 * mumble (every phone less clear). Deterministic for a given seed.
 */
(function (g) {
  "use strict";

  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function gauss(rng) {
    var u = 1 - rng(), v = rng();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  // phones that are commonly confused with each other (used for "second best" noise)
  var NEIGHBOURS = {
    "θ": ["s", "t", "f"], "ð": ["d", "z"], "i": ["ɪ"], "ɪ": ["i", "ɛ"], "æ": ["ɛ", "ɑ"], "ɛ": ["æ", "ɪ"],
    "ɝ": ["ə"], "ɹ": ["l", "w"], "l": ["ɹ", "n"], "v": ["w", "f", "b"], "w": ["v"], "z": ["s"], "s": ["z", "θ"],
    "ʃ": ["s"], "n": ["ŋ", "l"], "ŋ": ["n"], "ʊ": ["u"], "u": ["ʊ"], "ə": ["ɪ", "ɛ"], "ɑ": ["ʌ", "æ"],
    "t": ["d"], "d": ["t"], "p": ["b"], "b": ["p"], "k": ["g"], "g": ["k"], "f": ["v"], "h": ["f"],
    "eɪ": ["ɛ"], "oʊ": ["ɑ"], "aɪ": ["ɑ"], "aʊ": ["ɑ"], "ʧ": ["ʃ"], "ʤ": ["ʒ", "ʧ"], "m": ["n"], "j": ["i"]
  };

  /**
   * opts: {phones: canonical phones (from G2P), vocab: GOP.Vocab, scenario, target, perceived, seed, noise}
   *  - sub: target = phone index, perceived = IPA heard instead
   *  - del: target = word_index to skip
   * Returns {logProbs: Float64Array(T*V), T, V, stride: 0.02, plan: [{kind, phone, start, end, emit}]}
   */
  function simulate(opts) {
    var vocab = opts.vocab, V = vocab.size, rng = mulberry32((opts.seed >>> 0) || 1);
    var noise = opts.noise === undefined ? 0.3 : opts.noise, sc = opts.scenario || "good";
    var phones = opts.phones;
    var frames = [];   // each: {main: id, second: id|null, mainBoost, secondBoost, phone: index|-1}
    function blankFrames(k) { for (var i = 0; i < k; i++) frames.push({ main: vocab.blank, second: null, mb: 7, sb: 0, phone: -1 }); }
    var plan = [];
    blankFrames(5 + Math.floor(rng() * 3));
    var lastWord = null;
    for (var i = 0; i < phones.length; i++) {
      var p = phones[i];
      if (lastWord !== null && p.word_index !== lastWord) blankFrames(Math.floor(rng() * 3));
      lastWord = p.word_index;
      if (sc === "del" && p.word_index === opts.target) { plan.push({ kind: "skipped", phone: i, start: frames.length, end: frames.length }); continue; }
      var dur = p.is_vowel ? 5 + Math.floor(rng() * 4) : 3 + Math.floor(rng() * 3);
      var emitN = Math.max(1, Math.round(dur * 0.55));
      var canonId = vocab.tok2id[p.ipa], emit = canonId, second = null, mb = 7, sb = 2.0;
      var nb = (NEIGHBOURS[p.ipa] || []).filter(function (x) { return x in vocab.tok2id; });
      if (nb.length) second = vocab.tok2id[nb[Math.floor(rng() * nb.length)]];
      if (sc === "sub" && i === opts.target) {
        emit = vocab.tok2id[opts.perceived]; second = canonId; mb = 7; sb = 2.5;
      } else if (sc === "mumble") {
        mb = 4.2; sb = second !== null ? 3.4 + rng() * 1.2 : 0;
      }
      var start = frames.length;
      for (var k = 0; k < emitN; k++) frames.push({ main: emit, second: second, mb: mb, sb: sb, phone: i });
      for (k = emitN; k < dur; k++) frames.push({ main: vocab.blank, second: emit, mb: 6, sb: 2.5 - k * 0.3, phone: i });
      plan.push({ kind: emit === canonId ? "ok" : "replaced", phone: i, start: start, end: frames.length, emit: vocab.id2tok[emit] });
    }
    blankFrames(5 + Math.floor(rng() * 3));

    var T = frames.length, lp = new Float64Array(T * V), sigma = 0.6 + 1.6 * noise;
    var special = {};
    vocab.order.forEach(function (pr) { if (!GOP.isRealPhone(pr[0]) && pr[1] !== vocab.blank) special[pr[1]] = 1; });
    for (var t = 0; t < T; t++) {
      var f = frames[t], r = t * V, mx = -Infinity;
      for (var v = 0; v < V; v++) {
        var z = gauss(rng) * sigma - (special[v] ? 4 : 0);
        if (v === f.main) z += f.mb;
        if (f.second !== null && v === f.second) z += f.sb;
        lp[r + v] = z;
        if (z > mx) mx = z;
      }
      var se = 0;
      for (v = 0; v < V; v++) se += Math.exp(lp[r + v] - mx);
      var lse = mx + Math.log(se);
      for (v = 0; v < V; v++) lp[r + v] -= lse;
    }
    return { logProbs: lp, T: T, V: V, stride: 0.02, plan: plan, scenario: sc };
  }

  /** Default "replaced" phone for a sentence: the first phone that has a known L2 confusion. */
  function defaultSubstitution(phones, hints, vocab) {
    for (var i = 0; i < phones.length; i++) {
      var e = hints[phones[i].ipa];
      if (e && e.confusions) {
        for (var j = 0; j < e.confusions.length; j++) {
          if (e.confusions[j].perceived in vocab.tok2id) return { target: i, perceived: e.confusions[j].perceived };
        }
      }
    }
    var p = phones[Math.floor(phones.length / 2)], nb = (NEIGHBOURS[p.ipa] || ["ə"]).filter(function (x) { return x in vocab.tok2id; });
    return { target: Math.floor(phones.length / 2), perceived: nb[0] || "ə" };
  }

  /** Default skipped word: the longest content word that is not the first word. */
  function defaultDeletion(phones) {
    var count = {}, best = null;
    phones.forEach(function (p) { count[p.word_index] = (count[p.word_index] || 0) + 1; });
    Object.keys(count).forEach(function (w) { w = +w; if (w > 0 && (best === null || count[w] > count[best])) best = w; });
    return best === null ? 0 : best;
  }

  g.SIM = { simulate: simulate, defaultSubstitution: defaultSubstitution, defaultDeletion: defaultDeletion, mulberry32: mulberry32, NEIGHBOURS: NEIGHBOURS };
})(typeof window !== "undefined" ? window : globalThis);
