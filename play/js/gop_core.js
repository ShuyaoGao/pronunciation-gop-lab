/* Pronunciation scoring math, ported line by line from the Python version:
 *   src/ctc_align.py     ctc_forced_align (Viterbi over [blank, p0, blank, p1, ..., blank])
 *   src/pronunciation.py _phone_gop, _greedy_transcript, _completeness, _align_ops, _combine_scores
 *   src/thresholds.py    gop_to_score (piecewise linear anchors), level
 *   src/feedback.py      make_hint (lookup in data/l2_hints_en.json)
 * Given the same posterior matrix, results equal the Python ones (tests/test_play_parity.py).
 */
(function (g) {
  "use strict";
  var NEG_INF = -1e30;

  /** Python's round(x, nd): correctly rounded, ties to even on the exact binary value. */
  function pyRound(x, nd) {
    if (!isFinite(x)) return x;
    nd = nd || 0;
    var neg = x < 0, s = Math.abs(x).toFixed(Math.min(100, nd + 30));
    var dot = s.indexOf("."), intPart = s.slice(0, dot), frac = s.slice(dot + 1);
    var keep = frac.slice(0, nd), next = frac.charCodeAt(nd) - 48, rest = frac.slice(nd + 1);
    var up;
    if (next > 5) up = true;
    else if (next < 5) up = false;
    else if (/[1-9]/.test(rest)) up = true;
    else {                                     // exact tie -> even
      var last = nd > 0 ? keep.charCodeAt(nd - 1) - 48 : intPart.charCodeAt(intPart.length - 1) - 48;
      up = last % 2 === 1;
    }
    var digits = (intPart + keep).split("").map(Number);
    if (up) {
      var i = digits.length - 1;
      while (i >= 0) { if (digits[i] === 9) { digits[i] = 0; i--; } else { digits[i]++; break; } }
      if (i < 0) digits.unshift(1);
    }
    var str = digits.join("");
    var ip = str.slice(0, str.length - nd) || "0", fp = str.slice(str.length - nd);
    var v = parseFloat(ip + (nd > 0 ? "." + fp : ""));
    return neg && v !== 0 ? -v : v;
  }

  /** ctc_align.ctc_forced_align. logProbs: Float64Array T*V (row-major). Returns
   *  {segments: [[i, start, endExclusive], ...], path: Int32Array(T) of extended states, ext}. */
  function ctcForcedAlign(logProbs, T, V, tokens, blank) {
    var N = tokens.length;
    if (N === 0 || T === 0 || T < N) return { segments: [], path: null, ext: null };
    var ext = [blank];
    for (var k = 0; k < N; k++) { ext.push(tokens[k]); ext.push(blank); }
    var S = ext.length;
    var dp = new Float64Array(T * S).fill(NEG_INF), back = new Int32Array(T * S);
    dp[0] = logProbs[ext[0]];
    if (S > 1) dp[1] = logProbs[ext[1]];
    for (var t = 1; t < T; t++) {
      var row = t * S, prow = (t - 1) * S, lrow = t * V;
      for (var s = 0; s < S; s++) {
        var sym = ext[s];
        var bestPrev = dp[prow + s], bestS = s;
        if (s - 1 >= 0 && dp[prow + s - 1] > bestPrev) { bestPrev = dp[prow + s - 1]; bestS = s - 1; }
        if (s - 2 >= 0 && sym !== blank && ext[s - 2] !== sym) {
          if (dp[prow + s - 2] > bestPrev) { bestPrev = dp[prow + s - 2]; bestS = s - 2; }
        }
        if (bestPrev <= NEG_INF) { dp[row + s] = NEG_INF; back[row + s] = bestS; continue; }
        dp[row + s] = bestPrev + logProbs[lrow + sym];
        back[row + s] = bestS;
      }
    }
    var last = (T - 1) * S, endState = (S >= 2 && dp[last + S - 2] > dp[last + S - 1]) ? S - 2 : S - 1;
    var path = new Int32Array(T);
    s = endState;
    for (t = T - 1; t >= 0; t--) { path[t] = s; s = back[t * S + s]; }
    var segments = [];
    for (var i = 0; i < N; i++) {
      var state = 2 * i + 1, first = -1, lastF = -1;
      for (t = 0; t < T; t++) if (path[t] === state) { if (first < 0) first = t; lastF = t; }
      if (first < 0) {
        var st = segments.length ? segments[segments.length - 1][2] : 0;
        segments.push([i, st, st + 1]);
      } else segments.push([i, first, lastF + 1]);
    }
    return { segments: segments, path: path, ext: ext };
  }

  /** thresholds.Thresholds */
  function Thresholds(data) {
    var a = data.gop_to_score_anchors.map(function (p) { return [Number(p[0]), Number(p[1])]; });
    a.sort(function (x, y) { return y[0] - x[0]; });          // gop descending (stable, like Python)
    this.anchors = a;
    this.calibrated = !!data.calibrated;
    this.green = Number(data.level_cutoffs.green);
    this.yellow = Number(data.level_cutoffs.yellow);
  }
  Thresholds.prototype.gopToScore = function (gop) {
    var a = this.anchors;
    if (gop >= a[0][0]) return a[0][1];
    if (gop <= a[a.length - 1][0]) return a[a.length - 1][1];
    for (var i = 0; i < a.length - 1; i++) {
      var gHi = a[i][0], sHi = a[i][1], gLo = a[i + 1][0], sLo = a[i + 1][1];
      if (gLo <= gop && gop <= gHi) {
        var ratio = gHi !== gLo ? (gop - gLo) / (gHi - gLo) : 0.0;
        return sLo + ratio * (sHi - sLo);
      }
    }
    return a[a.length - 1][1];
  };
  Thresholds.prototype.level = function (score) {
    if (score >= this.green) return "good";
    if (score >= this.yellow) return "warn";
    return "bad";
  };
  /** Inverse of gopToScore (for drawing the cut-off lines on the GOP axis). */
  Thresholds.prototype.scoreToGop = function (score) {
    var a = this.anchors;
    for (var i = 0; i < a.length - 1; i++) {
      var sHi = a[i][1], sLo = a[i + 1][1];
      if (sLo <= score && score <= sHi && sHi !== sLo) return a[i + 1][0] + (score - sLo) / (sHi - sLo) * (a[i][0] - a[i + 1][0]);
    }
    return score >= a[0][1] ? a[0][0] : a[a.length - 1][0];
  };

  function isRealPhone(tok) {
    if (!tok) return false;
    if (tok === "|" || tok === " ") return false;
    if ("[<{".indexOf(tok[0]) >= 0) return false;
    return true;
  }

  /** Model vocabulary helper. vocabOrder: [[token, id], ...] in the tokenizer's get_vocab() order. */
  function Vocab(vocabOrder, blankId) {
    this.order = vocabOrder;
    this.size = 0;
    this.id2tok = {};
    this.tok2id = {};
    var self = this;
    vocabOrder.forEach(function (p) { self.id2tok[p[1]] = p[0]; self.tok2id[p[0]] = p[1]; self.size = Math.max(self.size, p[1] + 1); });
    this.blank = blankId;
    this.phoneIds = vocabOrder.filter(function (p) { return isRealPhone(p[0]); }).map(function (p) { return p[1]; });
  }

  /** pronunciation._phone_gop (+ raw values for display). */
  function phoneGop(logProbs, T, V, vocab, canon, tid, s, e, frameStride, th) {
    var n = e - s, sum = 0, colMean = new Float64Array(V);
    for (var t = s; t < e; t++) {
      var r = t * V, best = -Infinity;
      for (var v = 0; v < V; v++) { var x = logProbs[r + v]; if (x > best) best = x; colMean[v] += x; }
      sum += logProbs[r + tid] - best;
    }
    var gop = sum / n;
    for (v = 0; v < V; v++) colMean[v] /= n;
    var bestId = vocab.phoneIds[0];
    for (var k = 1; k < vocab.phoneIds.length; k++) if (colMean[vocab.phoneIds[k]] > colMean[bestId]) bestId = vocab.phoneIds[k];
    var rawScore = th.gopToScore(gop);
    var ranked = vocab.phoneIds.slice().sort(function (a, b) { return colMean[b] - colMean[a]; }).slice(0, 3)
      .map(function (id) { return { ipa: vocab.id2tok[id], meanLogP: colMean[id] }; });
    return {
      ipa: canon.ipa, arpabet: canon.arpabet, word: canon.word, word_index: canon.word_index,
      gop: pyRound(gop, 3), gopRaw: gop, score: pyRound(rawScore, 1), scoreRaw: rawScore, level: th.level(rawScore),
      perceived_ipa: vocab.id2tok[bestId] || "?", top3: ranked,
      start_sec: pyRound(s * frameStride, 3), end_sec: pyRound(e * frameStride, 3),
      stress: canon.stress, is_vowel: canon.is_vowel, startFrame: s, endFrame: e, hint: ""
    };
  }

  /** pronunciation._greedy_transcript */
  function greedyTranscript(logProbs, T, V, vocab) {
    var out = [], prev = null;
    for (var t = 0; t < T; t++) {
      var r = t * V, bi = 0, bv = logProbs[r];
      for (var v = 1; v < V; v++) if (logProbs[r + v] > bv) { bv = logProbs[r + v]; bi = v; }
      if (bi !== prev && bi !== vocab.blank) {
        var tok = vocab.id2tok[bi] || "";
        if (isRealPhone(tok)) out.push(tok);
      }
      prev = bi;
    }
    return out.join(" ");
  }

  /** pronunciation._align_ops: [[op, refIdx, hypIdx], ...] with op in match/sub/del/ins. */
  function alignOps(ref, hyp) {
    var n = ref.length, m = hyp.length, i, j;
    var dp = [];
    for (i = 0; i <= n; i++) { dp.push(new Array(m + 1).fill(0)); }
    for (i = 1; i <= n; i++) dp[i][0] = i;
    for (j = 1; j <= m; j++) dp[0][j] = j;
    for (i = 1; i <= n; i++) for (j = 1; j <= m; j++) {
      var cost = ref[i - 1] === hyp[j - 1] ? 0 : 1;
      dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost);
    }
    var ops = [];
    i = n; j = m;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && dp[i][j] === dp[i - 1][j - 1] + (ref[i - 1] === hyp[j - 1] ? 0 : 1)) {
        ops.push([ref[i - 1] === hyp[j - 1] ? "match" : "sub", i - 1, j - 1]); i--; j--;
      } else if (i > 0 && dp[i][j] === dp[i - 1][j] + 1) {
        ops.push(["del", i - 1, -1]); i--;
      } else {
        ops.push(["ins", -1, j - 1]); j--;
      }
    }
    ops.reverse();
    return ops;
  }

  /** pronunciation._completeness */
  function completeness(canon, transcript) {
    var ref = canon.map(function (c) { return c.ipa; });
    var hyp = transcript.split(" ").filter(function (x) { return x; });
    if (!ref.length) return { score: 100.0, n_canonical: 0, n_deletions: 0, n_insertions: 0, missing_words: [], ops: [] };
    var ops = alignOps(ref, hyp), nDel = 0, nIns = 0, missing = [], seen = {};
    ops.forEach(function (o) { if (o[0] === "del") nDel++; if (o[0] === "ins") nIns++; });
    ops.forEach(function (o) {
      if (o[0] !== "del") return;
      var c = canon[o[1]];
      if (!seen[c.word_index]) { seen[c.word_index] = 1; missing.push(c.word); }
    });
    var n = ref.length, penalty = (nDel * 1.0 + nIns * 0.5) / n, score = Math.max(0.0, 100.0 * (1.0 - penalty));
    return { score: pyRound(score, 1), n_canonical: n, n_deletions: nDel, n_insertions: nIns,
             missing_words: missing.slice(0, 8), ops: ops, hyp: hyp };
  }

  /** pronunciation._combine_scores (prosody may be null). */
  function combineScores(accuracy, comp, prosody) {
    var parts = [[accuracy, 0.55]];
    if (comp) parts.push([comp.score, 0.15]);
    if (prosody && prosody.ok) { parts.push([prosody.fluency_score, 0.15]); parts.push([prosody.prosody_score, 0.15]); }
    var tw = 0, s = 0;
    parts.forEach(function (p) { tw += p[1]; s += p[0] * p[1]; });
    return tw ? s / tw : accuracy;
  }

  /** feedback.make_hint */
  function makeHint(p, hints) {
    var entry = hints[p.ipa];
    if (entry) {
      var confs = entry.confusions || [];
      for (var i = 0; i < confs.length; i++) if (confs[i].perceived === p.perceived_ipa) return confs[i].tip;
      if (entry.tip) {
        var base = entry.tip;
        if (p.perceived_ipa && p.perceived_ipa !== p.ipa) return base + "（系统更像听到 /" + p.perceived_ipa + "/）";
        return base;
      }
    }
    if (p.perceived_ipa && p.perceived_ipa !== p.ipa) return "/" + p.ipa + "/ 发得更像 /" + p.perceived_ipa + "/，注意口型与时长。";
    return "/" + p.ipa + "/ 发音偏离较大，注意口型与时长。";
  }

  function mean(xs) { var s = 0; for (var i = 0; i < xs.length; i++) s += xs[i]; return xs.length ? s / xs.length : 0; }

  /** Whole PronunciationScorer.score() after the model step: posteriors -> result. */
  function scoreFromPosteriors(logProbs, T, V, canonAll, vocab, th, hints, frameStride) {
    var canon = canonAll.filter(function (c) { return c.ipa in vocab.tok2id; });
    var ids = canon.map(function (c) { return vocab.tok2id[c.ipa]; });
    var transcript = greedyTranscript(logProbs, T, V, vocab);
    var res = { ok: false, transcript_ipa: transcript, phones: [], words: [], note: "" };
    if (!ids.length) { res.note = "无法把参考句转成有效音素。"; return res; }
    var al = ctcForcedAlign(logProbs, T, V, ids, vocab.blank);
    res.align = al;
    if (!al.segments.length) { res.note = "强制对齐失败（音频太短，容纳不下这么多音素）。"; return res; }
    canon.forEach(function (c, k) {
      var sg = al.segments[k];
      res.phones.push(phoneGop(logProbs, T, V, vocab, c, ids[sg[0]], sg[1], sg[2], frameStride, th));
    });
    var words = [], lastIdx = null;
    res.phones.forEach(function (p) {
      if (!words.length || p.word_index !== lastIdx) { words.push({ word: p.word, score: 0, phones: [] }); lastIdx = p.word_index; }
      words[words.length - 1].phones.push(p);
    });
    words.forEach(function (w) { w.score = pyRound(mean(w.phones.map(function (p) { return p.score; })), 1); });
    res.words = words;
    var acc = mean(res.phones.map(function (p) { return p.score; }));
    res.completeness = completeness(canon, transcript);
    res.accuracyRaw = acc;
    res.accuracy_score = pyRound(acc, 1);
    res.overallRaw = combineScores(acc, res.completeness, null);
    res.overall_score = pyRound(res.overallRaw, 1);
    res.phones.forEach(function (p) { if (p.level !== "good") p.hint = makeHint(p, hints); });
    res.ok = true;
    return res;
  }

  g.GOP = { NEG_INF: NEG_INF, pyRound: pyRound, ctcForcedAlign: ctcForcedAlign, Thresholds: Thresholds,
            isRealPhone: isRealPhone, Vocab: Vocab, phoneGop: phoneGop, greedyTranscript: greedyTranscript,
            alignOps: alignOps, completeness: completeness, combineScores: combineScores, makeHint: makeHint,
            scoreFromPosteriors: scoreFromPosteriors, mean: mean };
})(typeof window !== "undefined" ? window : globalThis);
