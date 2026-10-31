// Node harness used by tests/test_play_parity.py: loads play/js/*.js + play/data/app_data.js
// into a sandbox, runs one JSON command (argv[2] = command file) and prints JSON.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const PLAY = path.resolve(__dirname, "..", "..", "play");
const ctx = { console, Math, Float64Array, Float32Array, Int32Array, Uint8Array, Array, JSON, Number, isFinite, parseFloat };
ctx.window = ctx;
ctx.globalThis = ctx;
vm.createContext(ctx);
for (const f of ["js/config.js", "data/app_data.js", "js/gop_core.js", "js/simulate.js", "js/text_tools.js", "js/prosody.js"]) {
  vm.runInContext(fs.readFileSync(path.join(PLAY, f), "utf8"), ctx, { filename: f });
}
const D = ctx.APP_DATA, GOP = ctx.GOP;
const vocab = new GOP.Vocab(D.vocab.order, D.vocab.blank);
const cmd = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const arr = (a) => Array.from(a);
let out;

function sentence(level, i) { return D.sentences[level][i]; }

if (cmd.cmd === "score") {
  out = cmd.cases.map((c) => {
    const s = sentence(c.level, c.index);
    let target = c.target, perceived = c.perceived;
    if (c.scenario === "sub" && target === undefined) { const d = ctx.SIM.defaultSubstitution(s.phones, D.hints, vocab); target = d.target; perceived = d.perceived; }
    if (c.scenario === "del" && target === undefined) target = ctx.SIM.defaultDeletion(s.phones);
    const sim = ctx.SIM.simulate({ phones: s.phones, vocab, scenario: c.scenario, target, perceived, seed: c.seed, noise: c.noise });
    const th = new GOP.Thresholds(c.legacy ? D.thresholds_legacy : D.thresholds);
    const r = GOP.scoreFromPosteriors(sim.logProbs, sim.T, sim.V, s.phones, vocab, th, D.hints, sim.stride);
    return {
      T: sim.T, V: sim.V, logProbs: arr(sim.logProbs), text: s.text, target, perceived,
      segments: r.align ? r.align.segments : [], transcript: r.transcript_ipa,
      phones: r.phones.map((p) => ({ gop: p.gop, gopRaw: p.gopRaw, score: p.score, scoreRaw: p.scoreRaw, level: p.level,
                                     perceived: p.perceived_ipa, hint: p.hint, start_sec: p.start_sec, end_sec: p.end_sec })),
      words: r.words.map((w) => ({ word: w.word, score: w.score })),
      completeness: { score: r.completeness.score, n_del: r.completeness.n_deletions, n_ins: r.completeness.n_insertions,
                      missing: r.completeness.missing_words },
      accuracyRaw: r.accuracyRaw, accuracy: r.accuracy_score, overallRaw: r.overallRaw, overall: r.overall_score
    };
  });
} else if (cmd.cmd === "align") {
  out = cmd.cases.map((c) => GOP.ctcForcedAlign(Float64Array.from(c.logProbs), c.T, c.V, c.tokens, c.blank).segments);
} else if (cmd.cmd === "thresholds") {
  out = ["thresholds", "thresholds_legacy"].map((k) => {
    const th = new GOP.Thresholds(D[k]);
    return cmd.gops.map((x) => [th.gopToScore(x), th.level(th.gopToScore(x))]);
  });
} else if (cmd.cmd === "hints") {
  out = cmd.cases.map((c) => GOP.makeHint({ ipa: c[0], perceived_ipa: c[1] }, D.hints));
} else if (cmd.cmd === "round") {
  out = cmd.cases.map((c) => GOP.pyRound(c[0], c[1]));
} else if (cmd.cmd === "alignops") {
  out = cmd.cases.map((c) => GOP.alignOps(c[0], c[1]));
} else if (cmd.cmd === "cefr") {
  out = cmd.texts.map((t) => { const e = ctx.TEXT.estimateTextCefr(t); delete e.terms; delete e.words; delete e.syllables; return e; });
} else if (cmd.cmd === "syllables") {
  out = cmd.words.map((w) => ctx.TEXT.countSyllables(w));
} else if (cmd.cmd === "chat") {
  out = cmd.cases.map((c) => ctx.TEXT.chat(c[0], c[1]).text);
} else if (cmd.cmd === "rewrite") {
  out = cmd.cases.map((c) => ctx.TEXT.rewriteCheck(c[0], c[1]).note);
} else if (cmd.cmd === "fluency") {
  out = cmd.cases.map((c) => { const tips = []; const s = ctx.PROSODY.fluencyScore(c[0], c[1], c[2], tips); return [s, tips]; });
} else if (cmd.cmd === "intonation") {
  out = cmd.cases.map((v) => { const tips = []; const s = ctx.PROSODY.intonationScore(v, tips); return [s, tips]; });
} else if (cmd.cmd === "pauses") {
  out = cmd.signals.map((sig) => { const r = ctx.PROSODY.detectPauses(Float32Array.from(sig), cmd.sr); return [r.n, r.total, arr(ctx.PROSODY.rms(Float32Array.from(sig), 400, 160)).slice(0, 50)]; });
} else if (cmd.cmd === "yin") {
  out = cmd.signals.map((sig) => arr(ctx.PROSODY.yinF0(Float32Array.from(sig), cmd.sr, 65, 400, 0.04, 0.01)).map((v) => (isFinite(v) ? v : null)));
} else if (cmd.cmd === "data") {
  out = { sentences: D.sentences, vocabOrder: D.vocab.order, blank: D.vocab.blank, phoneIds: vocab.phoneIds,
          config: { levels: ctx.APP_CONFIG.levels.map((l) => l.key) } };
} else {
  throw new Error("unknown cmd " + cmd.cmd);
}
process.stdout.write(JSON.stringify(out));
