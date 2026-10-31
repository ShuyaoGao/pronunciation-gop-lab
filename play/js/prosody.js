/* Fluency and intonation from a real microphone recording, computed in the browser.
 *
 * Ported from src/prosody.py:
 *   detectPauses   = _detect_pauses (librosa.feature.rms, 25 ms frames / 10 ms hop, -40 dB, >= 0.18 s)
 *   fluencyScore   = _fluency_score   (same empirical ranges: 6 / 9 / 16 / 20 phones per second)
 *   intonationScore= _intonation_score (semitone std: < 1.2 flat, >= 3.0 lively)
 * Differences from the full Python version (stated on the page):
 *   - F0 uses plain YIN written here (Python uses librosa.pyin);
 *   - speaking time = first..last non-silent frame (Python uses the forced-aligned phones);
 *   - stress needs the phone alignment from the model, so it is not computed here.
 */
(function (g) {
  "use strict";
  var RATE_LOW = 6.0, RATE_OK_LO = 9.0, RATE_OK_HI = 16.0, RATE_HIGH = 20.0;
  var PITCH_VAR_FLAT = 1.2, PITCH_VAR_GOOD = 3.0;
  function f1(x) { return g.GOP.pyRound(x, 1).toFixed(1); }   // Python f"{x:.1f}"

  /** librosa.feature.rms(y, frame_length, hop_length, center=True, pad_mode="constant") */
  function rms(y, frameLength, hop) {
    var pad = Math.floor(frameLength / 2), n = y.length + 2 * pad;
    var nFrames = 1 + Math.floor((n - frameLength) / hop), out = new Float64Array(Math.max(0, nFrames));
    for (var f = 0; f < nFrames; f++) {
      var s = 0, st = f * hop - pad;
      for (var k = 0; k < frameLength; k++) { var i = st + k; if (i >= 0 && i < y.length) s += y[i] * y[i]; }
      out[f] = Math.sqrt(s / frameLength);
    }
    return out;
  }

  /** _detect_pauses -> {n, total, db, silent, start, end, pauses: [[fromSec, toSec]], hopSec} */
  function detectPauses(wav, sr, silDb, minPause) {
    silDb = silDb === undefined ? -40.0 : silDb;
    minPause = minPause === undefined ? 0.18 : minPause;
    var frame = Math.floor(0.025 * sr), hop = Math.floor(0.010 * sr), r = rms(wav, frame, hop);
    var res = { n: 0, total: 0.0, db: null, silent: null, start: -1, end: -1, pauses: [], hopSec: hop / sr };
    if (!r.length) return res;
    var ref = 1e-9, i;
    for (i = 0; i < r.length; i++) if (r[i] > ref) ref = r[i];
    ref += 1e-9;
    var db = new Float64Array(r.length), silent = new Uint8Array(r.length);
    for (i = 0; i < r.length; i++) { db[i] = 20.0 * Math.log10(r[i] / ref + 1e-9); silent[i] = db[i] < silDb ? 1 : 0; }
    res.db = db; res.silent = silent;
    var start = -1, end = -1;
    for (i = 0; i < r.length; i++) if (!silent[i]) { if (start < 0) start = i; end = i; }
    if (start < 0) return res;
    res.start = start; res.end = end;
    var run = 0, total = 0, n = 0;
    for (i = start; i <= end; i++) {
      if (silent[i]) { run++; continue; }
      var dur = run * hop / sr;
      if (dur >= minPause) { n++; total += dur; res.pauses.push([(i - run) * hop / sr, i * hop / sr]); }
      run = 0;
    }
    dur = run * hop / sr;
    if (dur >= minPause) { n++; total += dur; }
    res.n = n;
    res.total = g.GOP.pyRound(total, 2);
    return res;
  }

  /** Plain YIN (de Cheveigne & Kawahara, 2002). Returns Float64Array of F0 in Hz, NaN = unvoiced. */
  function yinF0(wav, sr, fmin, fmax, frameSec, hopSec, threshold, energyFloorDb) {
    fmin = fmin || 65; fmax = fmax || 400; threshold = threshold || 0.15;
    var W = Math.floor((frameSec || 0.04) * sr), hop = Math.floor((hopSec || 0.01) * sr);
    var tauMin = Math.max(2, Math.floor(sr / fmax)), tauMax = Math.min(W - 1, Math.ceil(sr / fmin));
    var n = Math.max(0, 1 + Math.floor((wav.length - W - tauMax) / hop)), out = new Float64Array(n);
    var d = new Float64Array(tauMax + 1), peak = 0, i;
    for (i = 0; i < wav.length; i++) peak = Math.max(peak, Math.abs(wav[i]));
    var floor = peak * Math.pow(10, (energyFloorDb === undefined ? -35 : energyFloorDb) / 20);
    for (var f = 0; f < n; f++) {
      var st = f * hop, e = 0;
      for (i = 0; i < W; i++) e += wav[st + i] * wav[st + i];
      if (Math.sqrt(e / W) < floor) { out[f] = NaN; continue; }
      for (var tau = 1; tau <= tauMax; tau++) {
        var s = 0;
        for (i = 0; i < W; i++) { var df = wav[st + i] - wav[st + i + tau]; s += df * df; }
        d[tau] = s;
      }
      // cumulative mean normalized difference
      var run = 0, best = -1;
      d[0] = 1;
      for (tau = 1; tau <= tauMax; tau++) { run += d[tau]; d[tau] = run > 0 ? d[tau] * tau / run : 1; }
      for (tau = tauMin; tau <= tauMax; tau++) {
        if (d[tau] < threshold) {
          while (tau + 1 <= tauMax && d[tau + 1] < d[tau]) tau++;
          best = tau; break;
        }
      }
      if (best < 0) { out[f] = NaN; continue; }
      var x0 = best > 1 ? d[best - 1] : d[best], x2 = best < tauMax ? d[best + 1] : d[best];
      var denom = x0 + x2 - 2 * d[best], shift = denom !== 0 ? 0.5 * (x0 - x2) / denom : 0;
      out[f] = sr / (best + shift);
    }
    return out;
  }

  /** numpy.percentile (linear) */
  function percentile(sorted, q) {
    if (!sorted.length) return NaN;
    var pos = (sorted.length - 1) * q / 100, lo = Math.floor(pos), hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  function pitchStats(f0) {
    var voiced = [];
    for (var i = 0; i < f0.length; i++) if (isFinite(f0[i]) && f0[i] > 0) voiced.push(f0[i]);
    var r = { n: voiced.length, mean_hz: 0, range_st: 0, variation_st: 0, semitones: null };
    if (voiced.length < 5) return r;
    var s = voiced.slice().sort(function (a, b) { return a - b; });
    var med = percentile(s, 50), st = voiced.map(function (v) { return 12.0 * Math.log2(v / med); });
    var ss = st.slice().sort(function (a, b) { return a - b; }), m = 0;
    st.forEach(function (v) { m += v; }); m /= st.length;
    var vv = 0;
    st.forEach(function (v) { vv += (v - m) * (v - m); });
    r.mean_hz = med; r.range_st = percentile(ss, 95) - percentile(ss, 5); r.variation_st = Math.sqrt(vv / st.length);
    r.semitones = st;
    return r;
  }

  /** prosody._fluency_score; tips appended like Python. */
  function fluencyScore(rate, nPauses, totalPause, tips) {
    var score = 100.0, r = rate;
    if (r < RATE_LOW) { score -= Math.min(40, (RATE_LOW - r) * 8); tips.push("语速偏慢（约 " + f1(r) + " 音素/秒），可尝试更连贯地朗读。"); }
    else if (r < RATE_OK_LO) score -= (RATE_OK_LO - r) * 4;
    else if (r > RATE_HIGH) { score -= Math.min(30, (r - RATE_HIGH) * 5); tips.push("语速偏快（约 " + f1(r) + " 音素/秒），注意吐字清晰。"); }
    else if (r > RATE_OK_HI) score -= (r - RATE_OK_HI) * 2;
    if (nPauses >= 2 && totalPause > 0.6) {
      score -= Math.min(30, nPauses * 6 + totalPause * 8);
      tips.push("句中停顿较多（" + nPauses + " 处、共 " + f1(totalPause) + "s），可练习更流畅地连读。");
    }
    return Math.max(0.0, Math.min(100.0, score));
  }

  /** prosody._intonation_score */
  function intonationScore(v, tips) {
    if (v <= 0) return 60.0;
    if (v < PITCH_VAR_FLAT) {
      tips.push("语调偏平，像在念字。试着在句子里自然地起伏（重点词读高一点、句尾该升/降）。");
      return Math.max(30.0, 60.0 * v / PITCH_VAR_FLAT);
    }
    if (v >= PITCH_VAR_GOOD) return 95.0;
    return 60.0 + 35.0 * (v - PITCH_VAR_FLAT) / (PITCH_VAR_GOOD - PITCH_VAR_FLAT);
  }

  /** Peak normalisation like audio_utils._peak_normalize. */
  function peakNormalize(wav, target) {
    var p = 0, i;
    for (i = 0; i < wav.length; i++) p = Math.max(p, Math.abs(wav[i]));
    if (p < 1e-6) return wav;
    var out = new Float32Array(wav.length), k = (target || 0.95) / p;
    for (i = 0; i < wav.length; i++) out[i] = wav[i] * k;
    return out;
  }

  function analyze(wav, sr, nPhones, nWords) {
    wav = peakNormalize(wav, 0.95);
    var tips = [], pz = detectPauses(wav, sr), res = { ok: false, tips: tips, pauses: pz, total_sec: wav.length / sr };
    if (pz.start < 0) return res;
    var speech = Math.max((pz.end - pz.start + 1) * pz.hopSec, 1e-3);
    res.speech_sec = speech;
    res.speaking_rate_pps = nPhones / speech;
    res.speaking_rate_wpm = res.total_sec > 0 ? nWords / res.total_sec * 60 : 0;
    res.n_pauses = pz.n; res.total_pause_sec = pz.total;
    res.fluency_score = g.GOP.pyRound(fluencyScore(res.speaking_rate_pps, pz.n, pz.total, tips), 1);
    var f0 = yinF0(wav, sr, 65, 400, 0.04, 0.01);
    var ps = pitchStats(f0);
    res.f0 = f0; res.pitch = ps;
    res.intonation_score = g.GOP.pyRound(intonationScore(ps.variation_st, tips), 1);
    res.ok = true;
    return res;
  }

  g.PROSODY = { rms: rms, detectPauses: detectPauses, yinF0: yinF0, pitchStats: pitchStats, percentile: percentile,
                fluencyScore: fluencyScore, intonationScore: intonationScore, analyze: analyze, peakNormalize: peakNormalize,
                RATE: [RATE_LOW, RATE_OK_LO, RATE_OK_HI, RATE_HIGH], PITCH: [PITCH_VAR_FLAT, PITCH_VAR_GOOD] };
})(typeof window !== "undefined" ? window : globalThis);
