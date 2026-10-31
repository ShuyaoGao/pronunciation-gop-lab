/* 发音评分实验室 · page logic (plain script, works from file://).
 * Modes: 评分演示 (demo, default, SIMULATED posteriors) / 我来读 (mic: real fluency + intonation) /
 *        对话陪练 (offline rule partner + text difficulty) / 练习 (task checklist).
 * Right column = the "scoring process" panel (no neural-network weights run in this page). */
(function () {
  "use strict";
  var CFG = window.APP_CONFIG, D = window.APP_DATA, CREDITS = window.CREDITS || {};
  var GOP = window.GOP, SIM = window.SIM, TEXT = window.TEXT, PROSODY = window.PROSODY;
  var $ = function (id) { return document.getElementById(id); };
  var params = new URLSearchParams(location.search);
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function store(key, val) {
    try {
      if (val === undefined) return localStorage.getItem(CFG.storagePrefix + key);
      localStorage.setItem(CFG.storagePrefix + key, val);
    } catch (e) { /* private window: ignore */ }
    return null;
  }
  if (!D || !GOP) {
    document.body.innerHTML = "<div class='ended'>数据文件没有加载成功。请把整个 play 文件夹一起解压后再打开 index.html。</div>";
    return;
  }

  var vocab = new GOP.Vocab(D.vocab.order, D.vocab.blank), V = vocab.size;
  var TH = { cal: new GOP.Thresholds(D.thresholds), legacy: new GOP.Thresholds(D.thresholds_legacy) };
  var CAL = D.calibration, CALPH = {};
  CAL.per_phone.forEach(function (r) { CALPH[r.ipa] = r; });
  var LEVELS = CFG.levels.map(function (l) { return l.key; });
  var LVCLS = { good: "good", warn: "warn", bad: "bad" }, LVNAME = { good: "标准", warn: "需注意", bad: "明显偏差" };
  var LVCOLOR = { good: CFG.colors.good, warn: CFG.colors.warn, bad: CFG.colors.bad };

  var app = { mode: "demo", alive: true, timers: [], sizes: {}, dirty: false, touch: { any: false, portrait: false, landscape: false } };
  function later(fn, ms) {
    var id = setTimeout(function () { app.timers = app.timers.filter(function (t) { return t !== id; }); if (app.alive) fn(); }, ms);
    app.timers.push(id);
    return id;
  }
  function cancel(id) { if (id) { clearTimeout(id); app.timers = app.timers.filter(function (t) { return t !== id; }); } }
  function toast(msg) {
    var t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toast.h); toast.h = setTimeout(function () { t.classList.remove("show"); }, 2200);
  }
  function fmt(x, d) { return GOP.pyRound(x, d === undefined ? 1 : d).toFixed(d === undefined ? 1 : d); }
  function scoreColor(s) { return s >= 80 ? CFG.colors.good : (s >= 55 ? CFG.colors.warn : CFG.colors.bad); }

  // ================================================================== DEMO state
  var S = { level: "A1", index: 0, scenario: "good", seed: 7, noise: 0.3, legacy: false, step: 1, playing: true,
            timer: null, sim: null, res: null, sel: null, sub: null, del: null, cycle: 0 };
  function sentence() { return D.sentences[S.level][S.index]; }
  function th() { return S.legacy ? TH.legacy : TH.cal; }

  function sentenceDefaults() {
    var ph = sentence().phones;
    S.sub = SIM.defaultSubstitution(ph, D.hints, vocab);
    S.del = SIM.defaultDeletion(ph);
  }
  function recompute() {
    var ph = sentence().phones;
    S.sim = SIM.simulate({ phones: ph, vocab: vocab, scenario: S.scenario, seed: S.seed, noise: S.noise,
                           target: S.scenario === "sub" ? S.sub.target : S.del, perceived: S.sub.perceived });
    S.res = GOP.scoreFromPosteriors(S.sim.logProbs, S.sim.T, S.sim.V, ph, vocab, th(), D.hints, S.sim.stride);
    if (S.sel === null || S.sel >= S.res.phones.length) S.sel = S.scenario === "sub" ? S.sub.target : worstPhone();
    markDirty();
  }
  function worstPhone() {
    var r = S.res, w = 0;
    if (!r || !r.phones.length) return 0;
    r.phones.forEach(function (p, i) { if (p.gopRaw < r.phones[w].gopRaw) w = i; });
    return w;
  }
  function setSentence(level, index) {
    S.level = level; S.index = index; S.sel = null; sentenceDefaults(); recompute();
  }
  function setScenario(sc) {
    S.scenario = sc; S.sel = null; recompute();
    if (sc === "sub") S.sel = S.sub.target;
    renderOpts(); renderExplain();
  }
  function setStep(k, manual) {
    S.step = Math.max(1, Math.min(5, k));
    if (manual && S.step === 5) practiceEvent("manual-step5");
    if (S.step === 5 && S.scenario === "del") practiceEvent("del-seen");
    markDirty(); renderExplain();
  }

  function scheduleDemo() {
    cancel(S.timer); S.timer = null;
    if (!S.playing || app.mode !== "demo" || !app.alive) return;
    S.timer = later(function () {
      S.timer = null;
      if (S.step < 5) { setStep(S.step + 1); }
      else {
        // next scenario; after the last one, next sentence of the same level
        var order = CFG.scenarios.map(function (s) { return s.key; }), i = order.indexOf(S.scenario);
        S.seed += 1;
        if (i === order.length - 1) {
          var n = D.sentences[S.level].length;
          setSentence(S.level, (S.index + 1) % n);
          S.scenario = order[0];
        } else S.scenario = order[i + 1];
        S.sel = null; recompute(); S.step = 1;
        renderStage(); renderOpts(); renderExplain();
      }
      scheduleDemo();
    }, S.step < 5 ? CFG.watchStepMs : CFG.watchHoldMs);
  }
  function setPlaying(on) { S.playing = on; scheduleDemo(); renderControls(); }

  // ================================================================== MIC state
  var M = { level: "A1", index: 0, recording: false, stream: null, ac: null, proc: null, chunks: [], sr: 0,
            result: null, wav: null, timer: null, status: "", meter: 0 };
  function micSentence() { return D.sentences[M.level][M.index]; }

  // ================================================================== CHAT state
  var C = { level: "A2", log: [], speak: false, last: null, lastReply: null };

  // ================================================================== PRACTICE
  var TASKS = [
    { id: "steps", text: "在「评分演示」里暂停，用「下一步」把 5 个步骤一步步走到 ⑤。", anim: "step" },
    { id: "heat", text: "选「读错一个音」，在热力图里把鼠标移到（手机上点一下）<b>错音那一行</b>的亮格上（提示会写出它是哪个音）。", anim: "click" },
    { id: "bad", text: "点一个<b>红色</b>音素块，读一读“被听成”的前 3 名和纠错建议。", anim: "click" },
    { id: "del", text: "选「漏读一个词」，走到 ⑤，看「完整度」和“疑似漏读的词”。", anim: "step" },
    { id: "legacy", text: "把阈值切到「标定前（经验值）」，对比同一个音的分数怎么变，再切回来。", anim: "click" },
    { id: "listen", text: "在「我来读」里点「🔊 听示范」。", anim: "mic" },
    { id: "chat", text: "在「对话陪练」里用英文发一句话，看难度估计面板。", anim: "keys" }
  ];
  var PR = { done: {} };
  (function () { var s = store("tasks"); if (s) { try { PR.done = JSON.parse(s) || {}; } catch (e) { PR.done = {}; } } })();
  function practiceEvent(ev) {
    var map = { "manual-step5": "steps", "heat-perceived": "heat", "select-bad": "bad", "del-seen": "del", "legacy": "legacy",
                "listen": "listen", "chat": "chat" };
    var id = map[ev];
    if (!id || PR.done[id]) return;
    if (id === "del" && !(S.res && S.res.completeness && S.res.completeness.n_deletions > 0)) return;
    PR.done[id] = 1;
    store("tasks", JSON.stringify(PR.done));
    if (app.mode === "practice") renderExplain();
    toast("练习任务完成 ✓");
  }

  // ================================================================== MODES
  function setMode(m) {
    if (!/^(demo|mic|chat|practice)$/.test(m)) return;
    var prev = app.mode;
    app.mode = m;
    document.querySelectorAll(".tab").forEach(function (t) { t.classList.toggle("on", t.dataset.mode === m); });
    ["demo", "mic", "chat", "practice"].forEach(function (k) { document.body.classList.toggle("m-" + k, k === m); });
    if (prev !== m) { openSheet(false); setMenu(false); }
    if (m !== "mic" && M.recording) stopRecording(true);
    if (m === "practice") { S.playing = false; }
    if (m === "demo" && prev === "practice") { S.playing = true; }
    cancel(S.timer); S.timer = null;
    if (m === "demo") scheduleDemo();
    renderAll();
  }
  function renderAll() { renderStage(); renderOpts(); renderControls(); renderExplain(); layout(); }

  // ================================================================== LEFT column
  function presetButtons(list, cur, kind) {
    return list.map(function (p) {
      return "<button class='preset" + (p.key === cur ? " on" : "") + "' data-kind='" + kind + "' data-key='" + p.key + "' data-tip='" + esc(p.name + "：" + p.tip) + "'>" + esc(p.name) + "</button>";
    }).join("");
  }
  function sentenceSelect(id, level, index) {
    return "<select id='" + id + "' data-tip='选一句参考句（共 " + D.sentences[level].length + " 句；音素事先用 G2P 算好）'>" +
      D.sentences[level].map(function (s, i) { return "<option value='" + i + "'" + (i === index ? " selected" : "") + ">" + esc(s.text) + "</option>"; }).join("") + "</select>";
  }

  function renderStage() {
    var st = $("stage"), h = "";
    if (app.mode === "demo" || app.mode === "practice") {
      var s = sentence(), r = S.res, step = S.step, scName = CFG.scenarios.filter(function (x) { return x.key === S.scenario; })[0].name;
      var setting = scName;
      if (S.scenario === "sub") { var tp = s.phones[S.sub.target]; setting += "：“" + cleanWord(tp.word) + "” 里的 /" + tp.ipa + "/ 读成了 /" + S.sub.perceived + "/"; }
      if (S.scenario === "del") setting += "：没有读 “" + cleanWord(wordOf(S.del)) + "”";
      h += "<div class='sim-badge'>⚠ " + esc(CFG.simBadge) + "<br>本次模拟设定：" + esc(setting) + "（噪声 " + S.noise.toFixed(1) + "，种子 " + S.seed + "）</div>";
      h += "<div class='sent'><span class='lv'>" + S.level + "</span>" + esc(s.text) + "</div>";
      var done = step >= 5 && r && r.ok;
      h += "<div class='metrics'>" +
        metric("综合", done ? r.overall_score : null, "准确度 55% + 完整度 15%") +
        metric("音素准确度", done ? r.accuracy_score : null, "各音素分数的平均") +
        metric("完整度", done ? r.completeness.score : null, done ? "漏 " + r.completeness.n_deletions + " / 多 " + r.completeness.n_insertions : "") +
        "<div class='metric na' data-tip='流利度、语调需要真实录音：去「我来读」用麦克风试；重音需要完整版（Python + 模型）'><div class='lb'>流利度 / 韵律</div><div class='v'>见「我来读」</div></div></div>";
      h += "<div class='words'>" + wordChips(s, r, step) + "</div>";
      if (step >= 5 && r) h += "<div class='transcript'>模型（模拟）自由识别出的音素：<span class='ipa'>" + esc(r.transcript_ipa) + "</span></div>";
    } else if (app.mode === "mic") {
      var ms = micSentence(), res = M.result;
      h += "<div class='sent'><span class='lv'>" + M.level + "</span>" + esc(ms.text) + "</div>";
      h += "<div class='opt-row' style='justify-content:center'>" +
        "<button class='btn' id='b-listen' data-tip='用浏览器自带的语音朗读这句（系统声音，不联网）'>🔊 听示范</button>" +
        "<button class='btn rec-btn" + (M.recording ? " on" : "") + "' id='b-rec' data-tip='最多录 15 秒；读完点停止。录音只在本机内存里分析，不上传、不保存' data-anim='mic'>" + (M.recording ? "■ 停止并分析" : "● 开始录音") + "</button>" +
        "<label class='btn' style='cursor:pointer' data-tip='也可以选一个本机的录音文件（wav / mp3 / m4a 等，浏览器能解码的格式）'>选择录音文件<input type='file' id='file-in' accept='audio/*' hidden></label>" +
        "<span class='meter'><i id='meter-bar'></i></span></div>";
      h += "<div class='transcript' id='mic-status'>" + esc(M.status || "先听示范，再点「开始录音」读这句。") + "</div>";
      var ok = res && res.ok;
      h += "<div class='metrics'>" +
        metric("流利度", ok ? res.fluency_score : null, ok ? fmt(res.speaking_rate_pps) + " 音素/秒" : "语速 + 停顿") +
        metric("语调", ok ? res.intonation_score : null, ok ? "起伏 " + fmt(res.pitch.variation_st) + " 半音" : "音高起伏") +
        "<div class='metric'><div class='lb'>停顿</div><div class='v'>" + (ok ? res.n_pauses : "—") + "</div><div class='sb'>" + (ok ? "共 " + fmt(res.total_pause_sec) + " 秒" : "句中 ≥ 0.18 秒") + "</div></div>" +
        "<div class='metric na' data-tip='准确度（GOP）、完整度、重音都需要发音模型对齐录音，网页里跑不了；见 README 的完整版'><div class='lb'>准确度 / 重音</div><div class='v'>需完整版</div></div></div>";
    } else if (app.mode === "chat") {
      h += "<div class='sim-badge'>离线规则陪练：固定模板 + 简单规则，<b>不是 AI 模型</b>。完整版（Python）可以接大模型。</div>";
      h += "<div class='chatlog' id='chatlog'>" + (C.log.length ? C.log.map(function (m) {
        if (m.role === "user") return "<div class='msg user'>" + esc(m.text) + "</div>";
        return "<div class='msg bot'>" + esc(m.reply) + (m.correction ? "<div class='corr'>📝 " + esc(m.correction) + "</div>" : "") + "</div>";
      }).join("") : "<div class='msg bot'>Hi! Tell me something about your day.<div class='corr'>（用英文打一句话，回车发送）</div></div>") + "</div>";
      h += "<div class='chatin'><input id='chat-in' placeholder='用英文说点什么…（回车发送）' autocomplete='off'><button class='btn go' id='b-send'>发送</button></div>";
    }
    st.innerHTML = h;
    bindTips(st);
    if (app.mode === "chat") { var cl = $("chatlog"); cl.scrollTop = cl.scrollHeight; }
  }
  function cleanWord(w) { return String(w || "").replace(/[^A-Za-z']/g, ""); }
  function wordOf(wi) { var p = sentence().phones.filter(function (x) { return x.word_index === wi; })[0]; return p ? p.word : ""; }
  function metric(label, v, sub) {
    var show = v !== null && v !== undefined;
    return "<div class='metric'><div class='lb'>" + label + "</div><div class='v' style='color:" + (show ? scoreColor(v) : "var(--muted)") + "'>" +
      (show ? Math.round(v) : "—") + "</div><div class='sb'>" + esc(sub || "") + "</div></div>";
  }
  function wordChips(s, r, step) {
    var out = [], byWord = {};
    s.phones.forEach(function (p, i) { (byWord[p.word_index] = byWord[p.word_index] || []).push(i); });
    Object.keys(byWord).forEach(function (wi) {
      var idx = byWord[wi], w = s.phones[idx[0]].word, chips = idx.map(function (i) {
        var p = r && r.phones[i], cls = "chip ipa";
        if (step >= 5 && p) cls += " " + LVCLS[p.level];
        if (S.sel === i) cls += " sel";
        var tip = step >= 5 && p ? "应读 /" + p.ipa + "/；得分 " + Math.round(p.score) + "（" + LVNAME[p.level] + "）；系统听到 /" + p.perceived_ipa + "/；GOP=" + p.gop.toFixed(2) + (p.hint ? "；建议：" + p.hint : "") : "应读 /" + s.phones[i].ipa + "/（ARPABET " + s.phones[i].arpabet + "）";
        return "<span class='" + cls + "' data-ph='" + i + "' title='" + esc(tip) + "'>" + esc(s.phones[i].ipa) + "</span>";
      }).join("");
      var ws = "";
      if (step >= 5 && r) { var wsc = r.words.filter(function (x) { return x.phones[0] === r.phones[idx[0]]; })[0]; if (wsc) ws = "<span class='ws' style='color:" + scoreColor(wsc.score) + "'>" + Math.round(wsc.score) + "</span>"; }
      out.push("<div class='wd'><div class='wl'>" + esc(cleanWord(w)) + ws + "</div>" + chips + "</div>");
    });
    return out.join("");
  }

  function renderOpts() {
    var o = $("opts"), h = "";
    if (app.mode === "demo" || app.mode === "practice") {
      h += "<div class='opt-row'><span class='lab'>难度</span>" + presetButtons(CFG.levels, S.level, "level") + "</div>";
      h += "<div class='opt-row'><span class='lab'>句子</span>" + sentenceSelect("sel-sent", S.level, S.index) + "</div>";
      h += "<div class='opt-row'><span class='lab'>读法</span>" + presetButtons(CFG.scenarios, S.scenario, "scenario") + "</div>";
      var ph = sentence().phones;
      if (S.scenario === "sub") {
        h += "<div class='opt-row'><span class='lab'></span>把 <select id='sel-subt'>" + ph.map(function (p, i) { return "<option value='" + i + "'" + (i === S.sub.target ? " selected" : "") + ">" + esc(cleanWord(p.word)) + " 的 /" + esc(p.ipa) + "/</option>"; }).join("") +
          "</select> 读成 <select id='sel-subp' class='ipa'>" + vocab.phoneIds.map(function (id) { var t = vocab.id2tok[id]; return "<option" + (t === S.sub.perceived ? " selected" : "") + ">" + esc(t) + "</option>"; }).join("") + "</select></div>";
      }
      if (S.scenario === "del") {
        var seen = {}, opts = [];
        ph.forEach(function (p) { if (!seen[p.word_index]) { seen[p.word_index] = 1; opts.push("<option value='" + p.word_index + "'" + (p.word_index === S.del ? " selected" : "") + ">" + esc(cleanWord(p.word)) + "</option>"); } });
        h += "<div class='opt-row'><span class='lab'></span>漏读的词 <select id='sel-del'>" + opts.join("") + "</select></div>";
      }
      h += "<div class='opt-row'><span class='lab'>噪声</span><input type='range' id='rng-noise' min='0' max='1' step='0.05' value='" + S.noise + "' data-tip='模拟后验的随机扰动大小：越大越像嘈杂环境、读得越不稳'><span class='ms' id='noise-lab'>" + S.noise.toFixed(2) + "</span>" +
        "<label class='ck' data-tip='阈值决定 GOP 怎样换成分数。标定后 = 在 speechocean762 上标定的锚点；标定前 = 文献里常见的经验值'><input type='checkbox' id='ck-legacy'" + (S.legacy ? " checked" : "") + "> 用标定前的阈值（经验值）</label></div>";
    } else if (app.mode === "mic") {
      h += "<div class='opt-row'><span class='lab'>难度</span>" + presetButtons(CFG.levels, M.level, "miclevel") + "</div>";
      h += "<div class='opt-row'><span class='lab'>句子</span>" + sentenceSelect("sel-micsent", M.level, M.index) + "</div>";
    } else if (app.mode === "chat") {
      h += "<div class='opt-row'><span class='lab'>等级</span>" + CFG.chatLevels.map(function (k) {
        return "<button class='preset" + (k === C.level ? " on" : "") + "' data-kind='chatlevel' data-key='" + k + "' data-tip='" + k + "：陪练按这个 CEFR 等级挑回复模板'>" + k + "</button>"; }).join("") +
        "<label class='ck' data-tip='用浏览器自带的语音朗读回复（系统声音，不联网）'><input type='checkbox' id='ck-speak'" + (C.speak ? " checked" : "") + "> 朗读回复</label></div>";
    }
    o.innerHTML = h;
    bindTips(o);
  }

  function renderControls() {
    var c = $("controls"), h = "";
    if (app.mode === "demo" || app.mode === "practice") {
      h = "<button class='btn " + (S.playing ? "" : "go") + "' id='b-play' data-tip='自动演示：每一步停一会儿，看完一种读法换下一种（空格）'>" + (S.playing ? "⏸ 暂停" : "▶ 自动演示") + "</button>" +
        "<button class='btn' id='b-prev' data-tip='上一步（←）'>上一步</button><button class='btn' id='b-next' data-tip='下一步（→）' data-anim='step'>下一步</button>" +
        "<button class='btn' id='b-reroll' data-tip='换一个随机种子，生成另一份模拟后验（N）'>换一种读法</button>";
    } else if (app.mode === "mic") {
      h = "<span style='color:var(--muted);font-size:12px'>录音只在这台电脑的浏览器内存里分析，不上传、不保存；关掉页面就没了。</span>";
    } else if (app.mode === "chat") {
      h = "<button class='btn' id='b-chatclear'>清空对话</button><span style='color:var(--muted);font-size:12px'>同样的输入总是得到同样的回复（不随机），方便课堂复现。</span>";
    }
    c.innerHTML = h;
    bindTips(c);
  }

  function renderExplain() {
    var e = $("explain"), h = "";
    if (app.mode === "demo") {
      var st = CFG.steps[S.step - 1];
      h += "<p><b>" + st.name + "</b>：" + st.tip + "。</p>";
      h += stepDetail(S.step);
      if (S.step >= 5 && S.res && S.res.ok) h += problemList(S.res);
    } else if (app.mode === "practice") {
      var n = 0;
      TASKS.forEach(function (t) { if (PR.done[t.id]) n++; });
      var cur = TASKS.filter(function (t) { return !PR.done[t.id]; })[0];
      h = "<h3>练习：跟着清单看懂发音评分（已完成 " + n + " / " + TASKS.length + "）</h3><div class='progress'><i style='width:" + (100 * n / TASKS.length) + "%'></i></div><ul class='tasks'>" +
        TASKS.map(function (t, i) {
          var d = !!PR.done[t.id];
          return "<li class='" + (d ? "done" : (cur === t ? "cur" : "")) + "' data-tip='" + esc(t.text.replace(/<[^>]+>/g, "")) + "' data-anim='" + t.anim + "'><span class='box'>" + (d ? "✓" : "") + "</span><span>" + (i + 1) + ". " + t.text + "</span></li>";
        }).join("") + "</ul>" + (n === TASKS.length ? "<p style='color:var(--green)'><b>全部完成！</b></p>" : "") +
        "<p style='margin-top:4px'><button class='mini-btn' id='b-tasks-reset'>清空进度重来</button></p>";
    } else if (app.mode === "mic") {
      var r = M.result;
      h += "<p><b>这里是真算的</b>：浏览器把你的录音切成 10 毫秒一帧，算每帧响度找停顿；用 YIN 算法找每帧的音高（F0），看起伏大不大。规则和完整版 <code>src/prosody.py</code> 相同。</p>";
      h += "<p style='color:var(--muted)'>与完整版的差别：完整版用模型对齐后的音素时长算语速、用 pYIN 算音高，还能算重音和每个音的准确度；这里语速 = 句子的音素数 ÷ 有声音的时长。所有经验区间都还没有在母语者语料上标定。</p>";
      if (r && r.ok && r.tips.length) h += "<p><b>建议：</b></p><ul class='problems'>" + r.tips.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul>";
      else if (r && r.ok) h += "<p style='color:var(--green)'>语速、停顿和语调都在经验区间里。</p>";
    } else if (app.mode === "chat") {
      h += "<p><b>它怎么回复：</b>按你选的等级，从 3 句固定回复里按“你这句话的字符数 ÷ 3 的余数”挑一句；再用 3 条简单规则找常见错误（I is / I has / yesterday + go、eat）。</p>" +
        "<p style='color:var(--muted)'>完整版（Python）配置了大模型接口后，会让模型按 CEFR 等级回复，并对超出等级的回复自动重写；这个网页不联网，只演示“分级 + 纠错”的流程。</p>";
    }
    e.innerHTML = h;
    bindTips(e);
  }
  function stepDetail(k) {
    var s = sentence(), r = S.res;
    if (k === 1) return "<p>“" + esc(s.text) + "” → <span class='ipa'>" + s.phones.map(function (p) { return esc(p.ipa); }).join(" ") + "</span>（" + s.phones.length + " 个音素）。用的是 g2p_en（CMU 词典 + 神经网络回退），再按固定表把 ARPABET 换成模型词表里的 IPA 记号。</p>";
    if (k === 2) return "<p>右边热力图：横轴是帧（每帧 20 毫秒，共 " + S.sim.T + " 帧 ≈ " + (S.sim.T * 0.02).toFixed(1) + " 秒），纵轴是模型的 " + V + " 个输出（38 个音素 + 空白等特殊记号），越亮概率越大。<b>真实工具里这一步由约 1.26 GB 的发音模型完成，这里是程序生成的模拟数据。</b></p>";
    if (k === 3) return "<p>已知应读的 " + s.phones.length + " 个音素，在它们中间插入“空白”，用动态规划（Viterbi）找一条总概率最大、顺序不变的路径（白线），于是每个音素都分到一段帧。</p>";
    if (k === 4) return "<p>GOP = 平均( log P(应读音素) − log P(这一帧最可能的音) )，≤ 0。读得准时两者相同，GOP ≈ 0；被听成别的音时明显为负。</p>";
    var t = th();
    return "<p>GOP 按锚点分段线性换成 0–100 分：≥ " + t.green + " 分绿色、≥ " + t.yellow + " 分黄色、其余红色。当前用的是" + (S.legacy ? "<b>标定前的经验值</b>" : "<b>在 speechocean762 上标定的锚点</b>") + "。</p>" +
      (r && r.completeness ? completenessText(r.completeness) : "");
  }
  function completenessText(c) {
    if (!c.n_deletions && !c.n_insertions) return "<p style='color:var(--green)'>完整度：自由识别的音素序列和应读序列对得上，没有明显漏读 / 多读。</p>";
    return "<p style='color:#ffd27a'>完整度 " + Math.round(c.score) + "：编辑距离对齐后，漏读 " + c.n_deletions + " 个音、多读 " + c.n_insertions + " 个音。" +
      (c.missing_words.length ? "疑似漏读的词：" + c.missing_words.map(function (w) { return esc(cleanWord(w)); }).join("、") + "。" : "") + "</p>";
  }
  function problemList(r) {
    var bad = r.phones.map(function (p, i) { return [p, i]; }).filter(function (x) { return x[0].level !== "good"; });
    if (!bad.length) return "<p style='color:var(--green)'>👍 各音素都达标。</p>";
    return "<p><b>需要改进的音（点击查看）：</b></p><ul class='problems'>" + bad.slice(0, 8).map(function (x) {
      var p = x[0];
      return "<li><span class='chip ipa " + p.level + "' data-ph='" + x[1] + "'>" + esc(p.ipa) + "</span>（" + esc(cleanWord(p.word)) + "，" + Math.round(p.score) + " 分）：" + esc(p.hint) + "</li>";
    }).join("") + (bad.length > 8 ? "<li>……共 " + bad.length + " 个</li>" : "") + "</ul>";
  }

  // ================================================================== RIGHT panel
  function heat(v) { v = Math.max(0, Math.min(1, v)); return "rgb(" + (Math.min(255, 255 * v * 1.6) | 0) + "," + ((200 * Math.pow(v, 1.6)) | 0) + "," + ((50 * Math.pow(v, 3)) | 0) + ")"; }
  function sizeCanvas(cv, w, h) {
    var dpr = window.devicePixelRatio || 1;
    if (cv._w !== w || cv._h !== h || cv._d !== dpr) {
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); cv.style.width = w + "px"; cv.style.height = h + "px";
      cv._w = w; cv._h = h; cv._d = dpr;
    }
    var ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    return ctx;
  }
  var hits = {};

  function renderPanel() {
    var p = $("panel");
    if (app.mode === "demo" || app.mode === "practice") renderDemoPanel(p);
    else if (app.mode === "mic") renderMicPanel(p);
    else renderChatPanel(p);
  }

  function buildDemoPanel(p) {
    p.innerHTML =
      "<div class='brain-head'><div id='formula'>评分过程：<span class='ipa'>参考句 → 应读音素 → 逐帧后验（" + V + " 个输出）→ CTC 强制对齐 → GOP → 分数与反馈</span></div>" +
      "<div class='steps' id='steps'>" + CFG.steps.map(function (s, i) { return "<div class='step' data-step='" + (i + 1) + "' data-tip='" + esc(s.tip) + "'>" + s.name + "</div>"; }).join("") + "</div></div>" +
      "<div class='lt' id='lt-heat'></div><canvas class='plot' id='cv-heat'></canvas>" +
      "<div class='lt' id='lt-gop'></div><div class='hscroll' id='gop-wrap'><canvas class='plot' id='cv-gop'></canvas></div>" +
      "<div class='panel-row' id='row-bottom'><div style='flex:1.1'><div class='lt'>GOP → 分数的锚点（分段线性；灰线 = 标定前）</div><canvas class='plot' id='cv-anchor'></canvas></div>" +
      "<div class='subcard' id='sel-card' style='flex:1'></div><div class='subcard' id='cal-card' style='flex:1.1'></div></div>";
    p.dataset.kind = "demo";
    bindTips(p);
    bindDemoCanvases();
  }

  function renderDemoPanel(p) {
    if (p.dataset.kind !== "demo") buildDemoPanel(p);
    var sz = app.sizes, W = sz.panelW, sim = S.sim, r = S.res, s = sentence(), step = S.step;
    document.querySelectorAll("#steps .step").forEach(function (el) { var k = +el.dataset.step; el.className = "step" + (k === step ? " on" : "") + (k < step ? " done" : ""); });
    $("lt-heat").innerHTML = "<b>逐帧后验热力图</b>（<span style='color:#ffd27a'>模拟数据</span>）：" + sim.T + " 帧 × " + V + " 个输出；黄色行 = 这句话里的应读音素" + (step >= 3 ? "，白线 = 对齐路径" : "");
    // ---------- heatmap
    var labW = 40, rowsH = sz.heatH, rowH = rowsH / V, cw = (W - labW) / sim.T, stripH = 18, H = rowsH + stripH + 4;
    var cv = $("cv-heat"), ctx = sizeCanvas(cv, W, H), lp = sim.logProbs, canonSet = {};
    s.phones.forEach(function (ph) { canonSet[vocab.tok2id[ph.ipa]] = 1; });
    if (step >= 2) {
      for (var t = 0; t < sim.T; t++) for (var v = 0; v < V; v++) {
        var pr = Math.exp(lp[t * V + v]);
        if (pr < 0.02) continue;
        ctx.fillStyle = heat(Math.pow(pr, 0.6));
        ctx.fillRect(labW + t * cw, v * rowH, Math.max(1, cw + 0.3), Math.max(1, rowH + 0.3));
      }
    } else {
      ctx.fillStyle = "#8b98a8"; ctx.font = "13px 'Microsoft YaHei','PingFang SC',sans-serif"; ctx.textAlign = "center";
      ctx.fillText("第 ② 步才有逐帧后验（点上方「② 逐帧后验」或「下一步」）", labW + (W - labW) / 2, rowsH / 2);
    }
    ctx.font = Math.max(8, Math.min(11, rowH + 2)) + "px 'Segoe UI','Lucida Sans Unicode','Arial Unicode MS',sans-serif"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (v = 0; v < V; v++) {
      var isC = canonSet[v], isB = v === vocab.blank;
      if (isC || isB) { ctx.fillStyle = isC ? "rgba(255,213,79,.18)" : "rgba(139,152,168,.12)"; ctx.fillRect(labW, v * rowH, W - labW, rowH); }
      if (rowH >= 7 || isC || isB) {
        ctx.fillStyle = isC ? CFG.colors.canon : (isB ? "#c3cdd9" : "#5d6b7c");
        if (rowH >= 7 || isC || isB) ctx.fillText(isB ? "空白" : vocab.id2tok[v].replace(" ", "␣"), labW - 4, v * rowH + rowH / 2);
      }
    }
    var al = r.align;
    if (step >= 3 && al && al.path) {
      ctx.fillStyle = CFG.colors.path;
      for (t = 0; t < sim.T; t++) { var tok = al.ext[al.path[t]]; ctx.fillRect(labW + t * cw + cw * 0.2, tok * rowH + rowH * 0.25, Math.max(1.2, cw * 0.6), Math.max(1.5, rowH * 0.5)); }
    }
    // phone strip under the heatmap (aligned segments)
    hits.strip = [];
    var y0 = rowsH + 3;
    if (step >= 3 && r.ok) {
      r.phones.forEach(function (ph, i) {
        var x = labW + ph.startFrame * cw, w = Math.max(2, (ph.endFrame - ph.startFrame) * cw);
        ctx.fillStyle = step >= 5 ? LVCOLOR[ph.level] : "#4a6f9e";
        ctx.globalAlpha = S.sel === i ? 1 : 0.75;
        ctx.fillRect(x + 0.5, y0, w - 1, stripH);
        ctx.globalAlpha = 1;
        if (S.sel === i) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.strokeRect(x + 0.5, y0, w - 1, stripH); }
        if (w >= 10) { ctx.fillStyle = "#0b1016"; ctx.font = "11px 'Segoe UI','Lucida Sans Unicode',sans-serif"; ctx.textAlign = "center"; ctx.fillText(ph.ipa, x + w / 2, y0 + stripH / 2 + 1); }
        hits.strip.push({ x: x, w: w, i: i });
      });
    }
    hits.heat = { labW: labW, cw: cw, rowH: rowH, rowsH: rowsH, y0: y0, stripH: stripH };

    // ---------- GOP bars
    var t2 = th(), g80 = t2.scoreToGop(t2.green), g55 = t2.scoreToGop(t2.yellow);
    $("lt-gop").innerHTML = "<b>逐音素 GOP</b>（≤ 0，越接近 0 越标准）· 虚线 = 分数分界：绿/黄 " + t2.green + " 分 ↔ GOP " + fmt(g80, 2) + "，黄/红 " + t2.yellow + " 分 ↔ GOP " + fmt(g55, 2) + (S.legacy ? "（标定前阈值）" : "（标定后阈值）");
    var GH = sz.gopH, cg = $("cv-gop"), n = r.phones.length, GW = app.touch.portrait ? Math.max(W, labW + n * 18) : W;
    var gx = sizeCanvas(cg, GW, GH), bw = (GW - labW) / Math.max(1, n);
    var minG = -12, yOf = function (gv) { return 6 + (GH - 24) * (Math.max(minG, Math.min(0.3, gv)) - 0) / (minG - 0); };
    gx.font = "10px Consolas, Menlo, monospace"; gx.textAlign = "right"; gx.textBaseline = "middle"; gx.fillStyle = "#8b98a8"; gx.strokeStyle = "#263244"; gx.lineWidth = 1;
    for (var gt = 0; gt >= minG; gt -= 3) { var yy = yOf(gt); gx.beginPath(); gx.moveTo(labW, yy); gx.lineTo(GW, yy); gx.stroke(); gx.fillText(String(gt), labW - 4, yy); }
    hits.gop = [];
    if (step >= 4 && r.ok) {
      r.phones.forEach(function (ph, i) {
        var x = labW + i * bw, ytop = yOf(0), yb = yOf(ph.gopRaw);
        gx.fillStyle = step >= 5 ? LVCOLOR[ph.level] : "#4a6f9e";
        gx.fillRect(x + bw * 0.15, ytop, bw * 0.7, Math.max(1.5, yb - ytop));
        if (S.sel === i) { gx.strokeStyle = "#fff"; gx.lineWidth = 1.5; gx.strokeRect(x + bw * 0.1, ytop - 1, bw * 0.8, Math.max(3, yb - ytop) + 2); }
        if (bw >= 9) { gx.fillStyle = "#c3cdd9"; gx.textAlign = "center"; gx.font = "11px 'Segoe UI','Lucida Sans Unicode',sans-serif"; gx.fillText(ph.ipa, x + bw / 2, GH - 8); }
        hits.gop.push({ x: x, w: bw, i: i });
      });
    } else {
      gx.fillStyle = "#8b98a8"; gx.font = "13px 'Microsoft YaHei','PingFang SC',sans-serif"; gx.textAlign = "center";
      gx.fillText("第 ④ 步才算 GOP", labW + (GW - labW) / 2, GH / 2);
    }
    [[g80, CFG.colors.good, t2.green + " 分"], [g55, CFG.colors.bad, t2.yellow + " 分"]].forEach(function (L) {
      var yl = yOf(L[0]);
      gx.setLineDash([5, 4]); gx.strokeStyle = L[1]; gx.lineWidth = 1.2; gx.beginPath(); gx.moveTo(labW, yl); gx.lineTo(GW, yl); gx.stroke(); gx.setLineDash([]);
      gx.fillStyle = L[1]; gx.textAlign = "left"; gx.font = "10.5px 'Microsoft YaHei','PingFang SC',sans-serif"; gx.fillText(L[2], GW - 34, yl - 6);
    });
    hits.gopLab = labW;

    renderAnchor();
    renderSelCard();
    renderCalCard();
    ["sel-card", "cal-card"].forEach(function (id) { $(id).style.maxHeight = (sz.anchorH + 18) + "px"; $(id).style.overflowY = "auto"; });
  }

  function renderAnchor() {
    var cv = $("cv-anchor"), W = cv.parentNode.clientWidth, H = app.sizes.anchorH, ctx = sizeCanvas(cv, W, H);
    var L = 30, R = W - 8, T = 8, B = H - 18, gmin = -12, gmax = 0.5;
    var X = function (gv) { return L + (gv - gmin) / (gmax - gmin) * (R - L); }, Y = function (sv) { return B - sv / 100 * (B - T); };
    ctx.strokeStyle = "#263244"; ctx.lineWidth = 1; ctx.fillStyle = "#8b98a8"; ctx.font = "10px Consolas, Menlo, monospace";
    [0, 55, 80, 100].forEach(function (sv) { ctx.beginPath(); ctx.moveTo(L, Y(sv)); ctx.lineTo(R, Y(sv)); ctx.stroke(); ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.fillText(String(sv), L - 3, Y(sv)); });
    [-12, -9, -6, -3, 0].forEach(function (gv) { ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillText(String(gv), X(gv), B + 3); });
    function curve(t, color, dash, w) {
      ctx.setLineDash(dash); ctx.strokeStyle = color; ctx.lineWidth = w; ctx.beginPath();
      for (var k = 0; k <= 200; k++) { var gv = gmin + (gmax - gmin) * k / 200, sv = t.gopToScore(gv); if (k) ctx.lineTo(X(gv), Y(sv)); else ctx.moveTo(X(gv), Y(sv)); }
      ctx.stroke(); ctx.setLineDash([]);
    }
    curve(S.legacy ? TH.cal : TH.legacy, "#5d6b7c", [4, 3], 1.2);
    curve(th(), CFG.colors.canon, [], 2);
    ctx.fillStyle = CFG.colors.canon;
    th().anchors.forEach(function (a) { if (a[0] >= gmin) { ctx.beginPath(); ctx.arc(X(a[0]), Y(a[1]), 3, 0, 7); ctx.fill(); } });
    var p = S.res && S.res.phones[S.sel];
    if (p && S.step >= 5) {
      var gx = Math.max(gmin, p.gopRaw);
      ctx.strokeStyle = "#fff"; ctx.setLineDash([2, 2]); ctx.beginPath(); ctx.moveTo(X(gx), B); ctx.lineTo(X(gx), Y(p.scoreRaw)); ctx.lineTo(L, Y(p.scoreRaw)); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = LVCOLOR[p.level]; ctx.beginPath(); ctx.arc(X(gx), Y(p.scoreRaw), 5, 0, 7); ctx.fill();
    }
  }

  function renderSelCard() {
    var c = $("sel-card"), r = S.res, i = S.sel, p = r && r.phones[i];
    if (!p) { c.innerHTML = ""; return; }
    var h = "<h4>选中的音素：<span class='ipa'>/" + esc(p.ipa) + "/</span>（“" + esc(cleanWord(p.word)) + "”）</h4>";
    if (S.step < 4) h += "<div>帧区间：" + (S.step >= 3 ? p.startFrame + "–" + (p.endFrame - 1) + "（" + p.start_sec.toFixed(2) + "–" + p.end_sec.toFixed(2) + " 秒）" : "第 ③ 步对齐后才知道") + "</div>";
    else {
      h += "<div>帧 " + p.startFrame + "–" + (p.endFrame - 1) + " · GOP <b>" + p.gop.toFixed(3) + "</b>" + (S.step >= 5 ? " · 得分 <b style='color:" + LVCOLOR[p.level] + "'>" + Math.round(p.score) + "</b>（" + LVNAME[p.level] + "）" : "") + "</div>";
      h += "<div>被听成的前 3 名（这些帧上的平均 log 后验）：</div><div class='ipa'>" + p.top3.map(function (x, k) {
        return (k ? " · " : "") + "<b style='color:" + (x.ipa === p.ipa ? CFG.colors.good : CFG.colors.perceived) + "'>/" + esc(x.ipa) + "/</b> " + x.meanLogP.toFixed(2);
      }).join("") + "</div>";
      if (S.step >= 5 && p.hint) h += "<div style='color:#ffd27a'>建议：" + esc(p.hint) + "</div>";
    }
    var cal = CALPH[p.ipa];
    if (cal) h += "<div style='color:var(--muted)'>标定集里的 /" + esc(p.ipa) + "/：" + cal.n + " 个，GOP 中位数 " + cal.median.toFixed(3) + "，P25 " + cal.p25.toFixed(3) + "，专家分均值 " + cal.expert_mean.toFixed(2) + "（满分 2）</div>";
    c.innerHTML = h;
  }

  function renderCalCard() {
    var t = D.thresholds, g = CAL.groups;
    $("cal-card").innerHTML = "<h4>阈值从哪来（真实标定数据）</h4>" +
      "<div>" + esc(t.dataset) + "，" + esc(t.split) + " 切分，分层抽样 <b>" + t.n_utts + "</b> 句、<b>" + t.n_phones.toLocaleString("en-US") + "</b> 个对齐音素（专家判“发对” " + t.n_good.toLocaleString("en-US") + " 个、“发错” " + t.n_bad.toLocaleString("en-US") + " 个）。</div>" +
      "<div>GOP 区分发对 / 发错：AUC <b>" + t.auc_good_vs_bad + "</b>；与专家分 Pearson <b>" + t.pearson_gop_vs_expert + "</b>" + (CAL.spearman !== null ? "，Spearman " + CAL.spearman : "") + "。</div>" +
      (g.good && g.bad ? "<div>发对组 GOP 中位数 " + g.good.median + "、发错组 " + g.bad.median + "；锚点取发对组 P25、发错组 P75。</div>" : "") +
      "<div style='color:var(--muted)'>来源：data/thresholds.json、data/calibration_report.md（" + esc(t.calibrated_on) + " 标定）。语料是中国学习者朗读，换口音 / 设备时阈值可能偏移。</div>";
  }

  function bindDemoCanvases() {
    var heatCv = $("cv-heat"), gopCv = $("cv-gop");
    on(heatCv, "mousemove", function (e) {
      var hh = hits.heat; if (!hh || !S.sim) return;
      var rc = heatCv.getBoundingClientRect(), x = e.clientX - rc.left, y = e.clientY - rc.top;
      var t = Math.floor((x - hh.labW) / hh.cw), v = Math.floor(y / hh.rowH);
      if (y >= hh.y0) { var sp = stripHit(x); if (sp !== null) { var ph = S.res.phones[sp]; showTipAt(e.clientX, e.clientY, "音素 /" + esc(ph.ipa) + "/（" + esc(cleanWord(ph.word)) + "）：帧 " + ph.startFrame + "–" + (ph.endFrame - 1) + "；点击选中"); } else hideTip(); return; }
      if (t < 0 || t >= S.sim.T || v < 0 || v >= V || S.step < 2) { hideTip(); return; }
      var tok = vocab.id2tok[v], pr = Math.exp(S.sim.logProbs[t * V + v]);
      var seg = S.res.phones.filter(function (p) { return t >= p.startFrame && t < p.endFrame; })[0];
      showTipAt(e.clientX, e.clientY, "第 " + t + " 帧（" + (t * 20) + " 毫秒）· 输出 <b class='ipa'>" + esc(v === vocab.blank ? "空白（CTC blank）" : tok) + "</b> · 概率 " + (pr * 100).toFixed(1) + "%" +
        (S.step >= 3 && seg ? "<br>这一帧对齐到应读音素 /" + esc(seg.ipa) + "/" : "") + "<br><span style='color:#ffd27a'>模拟数据</span>");
      if (S.scenario === "sub" && S.step >= 3 && tok === S.sub.perceived && pr > 0.3) practiceEvent("heat-perceived");
    });
    on(heatCv, "mouseleave", hideTip);
    on(heatCv, "pointerdown", function (e) {
      if (e.pointerType !== "touch" || !hits.heat || !S.sim) return;
      var hh = hits.heat, rc = heatCv.getBoundingClientRect(), x = e.clientX - rc.left, y = e.clientY - rc.top;
      if (y >= hh.y0) return;   // phone strip: the click handler selects the phone
      var t = Math.floor((x - hh.labW) / hh.cw), v = Math.floor(y / hh.rowH);
      if (t < 0 || t >= S.sim.T || v < 0 || v >= V || S.step < 2) return;
      var tok = vocab.id2tok[v], pr = Math.exp(S.sim.logProbs[t * V + v]);
      var seg = S.res.phones.filter(function (p) { return t >= p.startFrame && t < p.endFrame; })[0];
      toast("第 " + t + " 帧 · 输出 " + (v === vocab.blank ? "空白" : tok) + " · 概率 " + (pr * 100).toFixed(1) + "%" + (S.step >= 3 && seg ? " · 对齐到 /" + seg.ipa + "/" : "") + "（模拟数据）");
      if (S.scenario === "sub" && S.step >= 3 && tok === S.sub.perceived && pr > 0.3) practiceEvent("heat-perceived");
    });
    on(heatCv, "click", function (e) { var rc = heatCv.getBoundingClientRect(), x = e.clientX - rc.left, sp = stripHit(x); if (sp !== null && e.clientY - rc.top >= hits.heat.y0) selectPhone(sp); });
    on(gopCv, "click", function (e) { var i = gopHit(e); if (i !== null) selectPhone(i); });
    on(gopCv, "mousemove", function (e) {
      var i = gopHit(e); if (i === null || S.step < 4) { hideTip(); return; }
      var p = S.res.phones[i];
      showTipAt(e.clientX, e.clientY, "/" + esc(p.ipa) + "/（" + esc(cleanWord(p.word)) + "）GOP " + p.gop.toFixed(3) + (S.step >= 5 ? "，" + Math.round(p.score) + " 分，听成 /" + esc(p.perceived_ipa) + "/" : "") + "<br>点击选中");
    });
    on(gopCv, "mouseleave", hideTip);
    on(gopCv, "pointerdown", function (e) {
      if (e.pointerType !== "touch") return;
      var i = gopHit(e); if (i === null || S.step < 4) return;
      var p = S.res.phones[i];
      toast("/" + p.ipa + "/（" + cleanWord(p.word) + "）GOP " + p.gop.toFixed(3) + (S.step >= 5 ? "，" + Math.round(p.score) + " 分，听成 /" + p.perceived_ipa + "/" : ""));
    });
  }
  function stripHit(x) { var l = hits.strip || []; for (var k = 0; k < l.length; k++) if (x >= l[k].x && x <= l[k].x + l[k].w) return l[k].i; return null; }
  function gopHit(e) {
    var rc = $("cv-gop").getBoundingClientRect(), x = e.clientX - rc.left, l = hits.gop || [];
    for (var k = 0; k < l.length; k++) if (x >= l[k].x && x <= l[k].x + l[k].w) return l[k].i;
    return null;
  }
  function selectPhone(i) {
    S.sel = i;
    if (S.res && S.res.phones[i] && S.res.phones[i].level === "bad" && S.step >= 5) practiceEvent("select-bad");
    renderStage(); markDirty();
  }

  // ---------- mic panel
  function renderMicPanel(p) {
    if (p.dataset.kind !== "mic") {
      p.innerHTML = "<div class='brain-head'><div id='formula'>流利度 / 语调计算过程（<b>真实录音、浏览器里真算</b>）</div></div>" +
        "<div class='lt'><b>响度（dB，相对最大值）</b> · 低于 −40 dB 算静音；句中连续静音 ≥ 0.18 秒算一次停顿（红色）</div><canvas class='plot' id='cv-db'></canvas>" +
        "<div class='lt'><b>音高 F0（YIN）</b> · 换算成相对中位数的半音；起伏 = 半音的标准差</div><canvas class='plot' id='cv-f0'></canvas>" +
        "<div class='lt'><b>经验区间</b>（来自 src/prosody.py，尚未标定）</div><canvas class='plot' id='cv-gauge'></canvas>" +
        "<div class='subcard' id='mic-formula'></div>";
      p.dataset.kind = "mic";
    }
    var W = app.sizes.panelW, r = M.result, H1 = app.sizes.micH;
    var c1 = sizeCanvas($("cv-db"), W, H1), c2 = sizeCanvas($("cv-f0"), W, H1), c3 = sizeCanvas($("cv-gauge"), W, 96);
    if (!r || !r.ok) {
      [c1, c2].forEach(function (c) { c.fillStyle = "#8b98a8"; c.font = "13px 'Microsoft YaHei','PingFang SC',sans-serif"; c.textAlign = "center"; c.fillText(r && !r.ok ? "没有检测到声音：请靠近麦克风再读一次" : "录音后这里显示真实的计算过程", W / 2, H1 / 2); });
    } else {
      var pz = r.pauses, db = pz.db, n = db.length, X = function (i) { return 30 + (W - 36) * i / Math.max(1, n - 1); }, Y = function (d) { return 6 + (H1 - 22) * Math.min(1, -Math.max(-80, d) / 80); };
      c1.fillStyle = "rgba(255,107,107,.25)";
      pz.pauses.forEach(function (q) { c1.fillRect(X(q[0] / pz.hopSec), 0, X(q[1] / pz.hopSec) - X(q[0] / pz.hopSec), H1 - 16); });
      c1.fillStyle = "rgba(45,189,110,.12)"; c1.fillRect(X(pz.start), 0, X(pz.end) - X(pz.start), H1 - 16);
      c1.strokeStyle = "#4fc3f7"; c1.lineWidth = 1.2; c1.beginPath();
      for (var i = 0; i < n; i++) { if (i) c1.lineTo(X(i), Y(db[i])); else c1.moveTo(X(i), Y(db[i])); }
      c1.stroke();
      c1.setLineDash([5, 4]); c1.strokeStyle = CFG.colors.bad; c1.beginPath(); c1.moveTo(30, Y(-40)); c1.lineTo(W - 6, Y(-40)); c1.stroke(); c1.setLineDash([]);
      c1.fillStyle = "#8b98a8"; c1.font = "10px Consolas, Menlo, monospace"; c1.textAlign = "right";
      [0, -40, -80].forEach(function (d) { c1.fillText(String(d), 27, Y(d) + 3); });
      c1.textAlign = "left"; c1.fillText((n * pz.hopSec).toFixed(1) + " 秒", W - 50, H1 - 3);
      var f0 = r.f0, med = r.pitch.mean_hz, nf = f0.length, Xf = function (k) { return 30 + (W - 36) * k / Math.max(1, nf - 1); }, Yf = function (st) { return (H1 - 16) / 2 - st * (H1 - 24) / 24; };
      c2.strokeStyle = "#263244"; [-12, 0, 12].forEach(function (st) { c2.beginPath(); c2.moveTo(30, Yf(st)); c2.lineTo(W - 6, Yf(st)); c2.stroke(); });
      c2.fillStyle = "#8b98a8"; c2.font = "10px Consolas, Menlo, monospace"; c2.textAlign = "right";
      [-12, 0, 12].forEach(function (st) { c2.fillText((st > 0 ? "+" : "") + st, 27, Yf(st) + 3); });
      c2.fillStyle = CFG.colors.canon;
      if (med > 0) for (var k = 0; k < nf; k++) if (isFinite(f0[k]) && f0[k] > 0) { var stv = 12 * Math.log2(f0[k] / med); c2.fillRect(Xf(k) - 1, Yf(Math.max(-12, Math.min(12, stv))) - 1, 2.5, 2.5); }
      c2.textAlign = "left"; c2.fillStyle = "#8b98a8"; c2.fillText(med > 0 ? "中位数 " + med.toFixed(0) + " Hz" : "浊音帧太少", 34, 11);
    }
    // gauges
    function gauge(y, label, lo, hi, zones, val, unit) {
      var L = 110, R = W - 50, X = function (v) { return L + (R - L) * (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo); };
      c3.fillStyle = "#c3cdd9"; c3.font = "12px 'Microsoft YaHei','PingFang SC',sans-serif"; c3.textAlign = "left"; c3.fillText(label, 4, y + 9);
      zones.forEach(function (z) { c3.fillStyle = z[2]; c3.fillRect(X(z[0]), y, X(z[1]) - X(z[0]), 12); });
      c3.fillStyle = "#8b98a8"; c3.font = "10px Consolas, Menlo, monospace"; c3.textAlign = "center";
      zones.forEach(function (z) { c3.fillText(String(z[0]), X(z[0]), y + 24); });
      c3.fillText(String(hi), X(hi), y + 24);
      if (val !== null && val !== undefined && isFinite(val)) {
        c3.fillStyle = "#fff"; c3.beginPath(); c3.moveTo(X(val), y - 2); c3.lineTo(X(val) - 5, y - 9); c3.lineTo(X(val) + 5, y - 9); c3.fill();
        c3.textAlign = "left"; c3.fillText(fmt(val) + unit, R + 4, y + 10);
      }
    }
    var RT = PROSODY.RATE, PT = PROSODY.PITCH, bad = "rgba(255,107,107,.45)", mid = "rgba(227,179,65,.45)", good = "rgba(45,189,110,.5)";
    gauge(14, "语速（音素/秒）", 0, 26, [[0, RT[0], bad], [RT[0], RT[1], mid], [RT[1], RT[2], good], [RT[2], RT[3], mid], [RT[3], 26, bad]], r && r.ok ? r.speaking_rate_pps : null, "");
    gauge(60, "音高起伏（半音）", 0, 6, [[0, PT[0], bad], [PT[0], PT[1], mid], [PT[1], 6, good]], r && r.ok ? r.pitch.variation_st : null, "");
    $("mic-formula").innerHTML = "<div><b>流利度</b> = 100 − 语速不在 9–16 音素/秒时的扣分 − 停顿 ≥ 2 处且总长 > 0.6 秒时的扣分（最多 30）</div>" +
      "<div><b>语调</b>：起伏 &lt; 1.2 半音 → 30–60 分（偏平）；1.2–3.0 → 60–95 分；≥ 3.0 → 95 分</div>" +
      (r && r.ok ? "<div style='color:var(--muted)'>本次：有声音的时长 " + fmt(r.speech_sec, 2) + " 秒，句子 " + micSentence().phones.length + " 个音素 → 语速 " + fmt(r.speaking_rate_pps) + " 音素/秒；浊音帧 " + r.pitch.n + " 个。</div>" : "") +
      "<div style='color:var(--muted)'>录音和计算都只在你的浏览器里，不上传。</div>";
  }

  // ---------- chat panel
  function renderChatPanel(p) {
    if (p.dataset.kind !== "chat") { p.innerHTML = "<div class='brain-head'><div id='formula'>句子难度估计（src/cefr.py 的启发式，<b>不是</b>训练好的分级器）</div></div><div id='chat-panel'></div>"; p.dataset.kind = "chat"; }
    var box = $("chat-panel"), h = "";
    function block(title, text) {
      var e = TEXT.estimateTextCefr(text);
      if (!e.n_words) return "<div class='subcard'><h4>" + title + "</h4><div style='color:var(--muted)'>还没有内容</div></div>";
      var t = e.terms, W = 100;
      var bands = [[0, 18, "A1"], [18, 34, "A2"], [34, 52, "B1"], [52, 70, "B2"], [70, 100, "C1"]];
      var bar = "<div style='position:relative;height:22px;margin:6px 0 16px;background:#0c1117;border-radius:4px'>" + bands.map(function (b) {
        return "<div style='position:absolute;left:" + b[0] + "%;width:" + (b[1] - b[0]) + "%;top:0;bottom:0;border-right:1px solid #34425a;font-size:10px;color:#8b98a8;text-align:center;line-height:22px'>" + b[2] + "</div>"; }).join("") +
        "<div style='position:absolute;left:calc(" + Math.min(W, e.complexity) + "% - 2px);top:-3px;bottom:-3px;width:4px;background:#ffd54f;border-radius:2px'></div>" +
        "<div style='position:absolute;left:" + Math.min(92, e.complexity) + "%;top:24px;font-size:11px;color:#ffd54f'>" + e.complexity + "</div></div>";
      return "<div class='subcard'><h4>" + title + "：估计 <b>" + e.level + "</b>（复杂度 " + e.complexity + " / 100）</h4>" + bar +
        "<div class='hscroll'><table class='ftable'><tr><th>特征</th><th>值</th><th>× 权重</th><th>贡献</th></tr>" +
        "<tr><td>平均词长（字母）</td><td class='num'>" + e.avg_word_len + "</td><td>(x − 3) × 9</td><td class='num'>" + t.word_len.toFixed(1) + "</td></tr>" +
        "<tr><td>长词占比（≥ 8 个字母）</td><td class='num'>" + e.long_word_ratio + "</td><td>x × 80</td><td class='num'>" + t.long_words.toFixed(1) + "</td></tr>" +
        "<tr><td>平均句长（词）</td><td class='num'>" + e.avg_sentence_len + "</td><td>(x − 4) × 2.2</td><td class='num'>" + t.sentence_len.toFixed(1) + "</td></tr>" +
        "<tr><td>平均音节数</td><td class='num'>" + e.avg_syllables + "</td><td>(x − 1) × 22</td><td class='num'>" + t.syllables.toFixed(1) + "</td></tr></table></div>" +
        "<div style='margin-top:4px'>音节（按元音字母组粗估）：" + e.words.slice(0, 24).map(function (w, i) { return esc(w) + "<sub>" + e.syllables[i] + "</sub>"; }).join(" ") + "</div></div>";
    }
    h += block("你的上一句", C.last || "");
    h += block("陪练的回复", C.lastReply || "");
    if (C.lastReply) {
      var rc = TEXT.rewriteCheck(C.lastReply, C.level);
      h += "<div class='subcard'><h4>超标检查（目标 " + C.level + "）</h4><div>" + (rc.note ? esc(rc.note) : "回复估计为 " + rc.est.level + "，没有超过目标等级。") + "</div><div style='color:var(--muted)'>完整版接上大模型后，超标的回复会被自动改写到目标等级；离线版只能提示。</div></div>";
    }
    h += "<div class='legend'>分界：复杂度 &lt; 18 → A1，&lt; 34 → A2，&lt; 52 → B1，&lt; 70 → B2，其余 C1（临时经验值，未在分级语料上标定）。</div>";
    box.innerHTML = h;
  }

  // ================================================================== mic recording / analysis
  function analyzeSamples(wav16, sr) {
    var ms = micSentence(), nWords = 0, seen = {};
    ms.phones.forEach(function (p) { if (!seen[p.word_index]) { seen[p.word_index] = 1; nWords++; } });
    M.result = PROSODY.analyze(wav16, sr, ms.phones.length, nWords);
    M.status = M.result.ok ? "分析完成：" + (wav16.length / sr).toFixed(1) + " 秒录音。" : "没有检测到声音。";
    renderStage(); renderExplain(); markDirty();
    return M.result;
  }
  function resample(samples, srIn, srOut) {
    if (srIn === srOut) return Promise.resolve(samples);
    var OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    var len = Math.max(1, Math.ceil(samples.length * srOut / srIn)), off = new OAC(1, len, srOut);
    var buf = off.createBuffer(1, samples.length, srIn);
    buf.getChannelData(0).set(samples);
    var src = off.createBufferSource(); src.buffer = buf; src.connect(off.destination); src.start();
    return off.startRendering().then(function (r) { return r.getChannelData(0); });
  }
  function startRecording(testStream) {
    if (!testStream && (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)) {
      M.status = "这个浏览器不能录音（没有 getUserMedia，或页面不是 https / 本地文件）。可以改用「选择录音文件」；其它模式不受影响。";
      toast(M.status); renderStage(); return Promise.resolve(false);
    }
    var getStream = testStream ? Promise.resolve(testStream) :
      navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    return getStream.then(function (stream) {
      if (!app.alive) { stream.getTracks().forEach(function (t) { t.stop(); }); return false; }
      var AC = window.AudioContext || window.webkitAudioContext;
      M.stream = stream; M.ac = new AC(); M.sr = M.ac.sampleRate; M.chunks = [];
      var src = M.ac.createMediaStreamSource(stream), proc = M.ac.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = function (e) {
        var d = e.inputBuffer.getChannelData(0); M.chunks.push(new Float32Array(d));
        var pk = 0; for (var i = 0; i < d.length; i++) pk = Math.max(pk, Math.abs(d[i]));
        var bar = $("meter-bar"); if (bar) bar.style.width = Math.min(100, pk * 140) + "%";
      };
      src.connect(proc); proc.connect(M.ac.destination);
      M.proc = proc; M.src = src; M.recording = true; M.status = "正在录音……读完点「停止并分析」（最多 15 秒）";
      M.timer = later(function () { if (M.recording) stopRecording(); }, 15000);
      renderStage();
      return true;
    }).catch(function (err) {
      var nm = (err && err.name) || String(err);
      M.status = (nm === "NotFoundError" || nm === "DevicesNotFoundError" ? "没有找到麦克风" : "没有拿到麦克风权限") + "（" + nm + "）。" +
        "请在浏览器地址栏旁允许使用麦克风后再点「开始录音」，或改用「选择录音文件」；其它模式不受影响。";
      toast(M.status); renderStage();
      return false;
    });
  }
  function releaseMic() {
    try { if (M.proc) { M.proc.onaudioprocess = null; M.proc.disconnect(); } } catch (e) { /* ignore */ }
    try { if (M.src) M.src.disconnect(); } catch (e) { /* ignore */ }
    if (M.stream) M.stream.getTracks().forEach(function (t) { t.stop(); });
    var ac = M.ac;
    M.proc = M.src = M.stream = M.ac = null; M.recording = false;
    cancel(M.timer); M.timer = null;
    return ac ? ac.close().catch(function () {}) : Promise.resolve();
  }
  function stopRecording(discard) {
    var chunks = M.chunks, sr = M.sr;
    M.chunks = [];
    return releaseMic().then(function () {
      if (discard || !app.alive) { renderStage(); return null; }
      var n = 0; chunks.forEach(function (c) { n += c.length; });
      var all = new Float32Array(n), o = 0; chunks.forEach(function (c) { all.set(c, o); o += c.length; });
      M.status = "正在分析……"; renderStage();
      return resample(all, sr, 16000).then(function (w) { return analyzeSamples(w, 16000); });
    });
  }
  function analyzeFile(file) {
    var AC = window.AudioContext || window.webkitAudioContext, ac = new AC();
    M.status = "正在解码 " + file.name + " ……"; renderStage();
    return file.arrayBuffer().then(function (buf) { return ac.decodeAudioData(buf); }).then(function (ab) {
      var n = ab.length, mono = new Float32Array(n);
      for (var c = 0; c < ab.numberOfChannels; c++) { var d = ab.getChannelData(c); for (var i = 0; i < n; i++) mono[i] += d[i] / ab.numberOfChannels; }
      ac.close();
      return resample(mono, ab.sampleRate, 16000).then(function (w) { return analyzeSamples(w, 16000); });
    }).catch(function (err) { ac.close(); M.status = "这个文件解码失败（" + (err && err.message || err) + "）"; renderStage(); });
  }
  function speak(text) {
    if (!("speechSynthesis" in window)) { toast("这个浏览器没有自带的朗读功能"); return false; }
    try {
      window.speechSynthesis.cancel();
      var u = new SpeechSynthesisUtterance(text), vs = window.speechSynthesis.getVoices();
      u.lang = "en-US"; u.rate = 0.9;
      var en = vs.filter(function (v) { return /^en(-|_)US/i.test(v.lang); })[0] || vs.filter(function (v) { return /^en/i.test(v.lang); })[0];
      if (en) u.voice = en;
      window.speechSynthesis.speak(u);
      return true;
    } catch (e) { toast("朗读失败：" + e.message); return false; }
  }

  // ================================================================== chat
  function sendChat() {
    var inp = $("chat-in"); if (!inp) return;
    var msg = inp.value;
    var out = TEXT.chat(msg, C.level);
    if (!msg.trim()) { toast(out.text); return; }
    C.log.push({ role: "user", text: msg.trim() });
    C.log.push({ role: "bot", reply: out.reply, correction: out.correction });
    C.last = msg.trim(); C.lastReply = out.reply;
    if (C.speak) speak(out.reply);
    renderStage(); markDirty(); practiceEvent("chat");
    var ni = $("chat-in"); if (ni) ni.focus();
  }

  // ================================================================== layout / render loop
  function layout() {
    var right = $("right");
    if (app.touch.portrait) {
      // phone portrait: the panel sits under the main picture (demo / practice) or in a full-screen sheet (mic / chat);
      // compact fixed heights, the whole page scrolls, so no fitting loop
      var pw = right.clientWidth || (window.innerWidth - 16);
      app.sizes.panelW = Math.max(240, pw - 22 - (document.body.classList.contains("sheet-panel") ? 16 : 0));
      app.sizes.heatH = 220; app.sizes.gopH = 110; app.sizes.anchorH = 110; app.sizes.micH = 130;
      renderPanel();
      return;
    }
    var rw = right.clientWidth - 22, rh = right.clientHeight;
    app.sizes.panelW = Math.max(300, rw);
    var avail = rh - 260;
    app.sizes.heatH = Math.max(132, Math.min(44 * 9, Math.round(avail * 0.55)));
    app.sizes.gopH = Math.max(90, Math.min(200, Math.round(avail * 0.27)));
    app.sizes.anchorH = Math.max(100, Math.min(170, Math.round(avail * 0.25)));
    app.sizes.micH = Math.max(110, Math.min(240, Math.round((rh - 330) / 2)));
    fit();
  }
  function fit() {
    if (!app.alive) return;
    var right = $("right"), sz = app.sizes;
    for (var i = 0; i < 12; i++) {
      renderPanel();
      var ro = right.scrollHeight - right.clientHeight;
      if (ro <= 1) break;
      if (app.mode === "mic") { if (sz.micH > 80) sz.micH = Math.max(80, sz.micH - Math.ceil(ro / 2)); else break; }
      else if (app.mode === "chat") break;
      else if (sz.heatH > 132) sz.heatH = Math.max(132, sz.heatH - ro);
      else if (sz.gopH > 80) sz.gopH = Math.max(80, sz.gopH - ro);
      else if (sz.anchorH > 70) sz.anchorH = Math.max(70, sz.anchorH - ro);
      else if (sz.heatH > 100) sz.heatH = Math.max(100, sz.heatH - ro);
      else if (sz.gopH > 64) sz.gopH = Math.max(64, sz.gopH - ro);
      else break;
    }
  }
  function markDirty() {
    if (app.dirty || !app.alive) return;
    app.dirty = true;
    requestAnimationFrame(function () { app.dirty = false; if (app.alive) { renderPanel(); if (app.mode === "demo" || app.mode === "practice") renderStageLight(); } });
  }
  var lastStageKey = "";
  function renderStageLight() {
    var key = [app.mode, S.level, S.index, S.scenario, S.seed, S.step, S.sel, S.legacy, S.noise].join("|");
    if (key !== lastStageKey) { lastStageKey = key; renderStage(); }
  }

  // ================================================================== tooltips
  var DEMOS = {
    click: "<div class='demo demo-click'><i class='d-dot'></i><i class='d-dot'></i><i class='d-dot target'></i><i class='d-cursor'></i></div>",
    keys: "<div class='demo demo-keys'><i class='k k-up'>↑</i><i class='k k-left'>←</i><i class='k k-down'>↓</i><i class='k k-right'>→</i></div>",
    step: "<div class='demo demo-steps'><i class='st s1'>①</i><i class='st s2'>②</i><i class='st s3'>③</i><i class='st s4'>④</i><i class='st s5'>⑤</i></div>",
    mic: "<div class='demo demo-mic'><i class='mic-dot'></i><i class='wave w1'></i><i class='wave w2'></i><i class='wave w3'></i></div>"
  };
  function bindTips(root) {
    (root || document).querySelectorAll("[data-tip]").forEach(function (el) {
      if (el._tip) return;
      el._tip = true;
      el.addEventListener("mouseenter", function () { showTip(el); });
      el.addEventListener("mouseleave", hideTip);
    });
  }
  function showTip(el) {
    var tip = $("tip");
    tip.innerHTML = esc(el.dataset.tip) + (el.dataset.anim && DEMOS[el.dataset.anim] ? DEMOS[el.dataset.anim] : "");
    tip.hidden = false;
    var r = el.getBoundingClientRect(), tw = tip.offsetWidth, th2 = tip.offsetHeight;
    var x = Math.min(window.innerWidth - tw - 8, Math.max(8, r.left + r.width / 2 - tw / 2)), y = r.bottom + 8;
    if (y + th2 > window.innerHeight - 8) y = r.top - th2 - 8;
    tip.style.left = x + "px"; tip.style.top = Math.max(8, y) + "px";
  }
  function showTipAt(x, y, html) {
    var tip = $("tip"); tip.innerHTML = html; tip.hidden = false;
    tip.style.left = Math.min(window.innerWidth - tip.offsetWidth - 8, x + 14) + "px";
    tip.style.top = Math.min(window.innerHeight - tip.offsetHeight - 8, y + 14) + "px";
  }
  function hideTip() { $("tip").hidden = true; }

  // ================================================================== help / exit / big
  function openHelp() { $("help").hidden = false; setMenu(false); var hb = document.querySelector(".help-body"); if (hb) hb.scrollTop = 0; }
  function setMenu(open) { var m = $("menu"); if (!m) return; m.hidden = !open; $("menu-btn").setAttribute("aria-expanded", open ? "true" : "false"); }
  /** Full-screen sheet for the process panel (phone portrait, operate modes). */
  function openSheet(open) {
    open = !!open && app.touch.portrait;
    if (document.body.classList.contains("sheet-panel") === open) return;
    document.body.classList.toggle("sheet-panel", open);
    if (open) $("right").scrollTop = 0;
    layout();
  }
  function showPanel() {
    if (app.touch.portrait && (app.mode === "mic" || app.mode === "chat")) openSheet(true);
    else if (app.touch.portrait) $("right").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  /** touch + width <= 980 + portrait -> body.touchp (phone layout); touch + landscape + height <= 980 -> body.touchl (rotate hint). */
  function classifyTouch() {
    var touch = false;
    try { touch = window.matchMedia("(pointer: coarse)").matches || ((navigator.maxTouchPoints || 0) > 0 && window.matchMedia("(hover: none)").matches); } catch (e) { /* old browsers */ }
    var w = window.innerWidth, h = window.innerHeight, portrait = h >= w;
    var next = { any: touch, portrait: touch && portrait && w <= 980, landscape: touch && !portrait && h <= 980 };
    var changed = next.portrait !== app.touch.portrait || next.landscape !== app.touch.landscape;
    app.touch = next;
    document.body.classList.toggle("touch", next.any);
    document.body.classList.toggle("touchp", next.portrait);
    document.body.classList.toggle("touchl", next.landscape);
    if (!next.landscape) document.body.classList.remove("rot-ok");
    $("rotate-hint").hidden = !next.landscape;
    if (!next.portrait) { document.body.classList.remove("sheet-panel"); setMenu(false); }
    return changed;
  }
  function clearPressed() { document.querySelectorAll(".pressed").forEach(function (el) { el.classList.remove("pressed"); }); }
  function onResize() { classifyTouch(); clearPressed(); layout(); }
  function closeHelp() { $("help").hidden = true; store("helpSeen", "1"); }
  function setBig(onv) { document.body.classList.toggle("big", onv); $("ck-big").checked = onv; store("big", onv ? "1" : "0"); layout(); }
  var listeners = [];
  function on(t, type, fn, o) { t.addEventListener(type, fn, o); listeners.push([t, type, fn, o]); }
  function shutdown() {
    app.alive = false;
    app.timers.forEach(function (t) { clearTimeout(t); }); app.timers = [];
    listeners.forEach(function (l) { l[0].removeEventListener(l[1], l[2], l[3]); }); listeners = [];
    try { if ("speechSynthesis" in window) window.speechSynthesis.cancel(); } catch (e) { /* ignore */ }
    releaseMic();
    S.sim = S.res = null; M.result = null; C.log = [];
    window.APP_DATA = null;
    try { window.close(); } catch (e) { /* ignore */ }
    setTimeout(function () {
      document.body.innerHTML = "<div class='ended'>已停止运行，所有计算都已结束，可以直接关闭这个标签页。</div>";
      document.title = "已停止 · " + CFG.shortTitle;
    }, 150);
  }

  // ================================================================== events
  function onKey(e) {
    if (e.target && /input|textarea|select/i.test(e.target.tagName) && e.target.type !== "checkbox" && e.target.type !== "range") {
      if (e.key === "Enter" && e.target.id === "chat-in") { e.preventDefault(); sendChat(); }
      return;
    }
    var k = e.key;
    if (!$("help").hidden) { if (k === "Escape" || k === "Enter" || k === "h" || k === "H") { closeHelp(); e.preventDefault(); } return; }
    if (k === "Escape") { setMenu(false); openSheet(false); return; }
    if (k === "h" || k === "H" || k === "?") return openHelp();
    if (k === "b" || k === "B") return setBig(!document.body.classList.contains("big"));
    if (k >= "1" && k <= "4") return setMode(["demo", "mic", "chat", "practice"][+k - 1]);
    if (app.mode === "demo" || app.mode === "practice") {
      if (k === " ") { e.preventDefault(); setPlaying(!S.playing); }
      else if (k === "ArrowRight") { e.preventDefault(); manualStep(1); }
      else if (k === "ArrowLeft") { e.preventDefault(); manualStep(-1); }
      else if (k === "n" || k === "N") reroll();
    }
  }
  function manualStep(d) { if (S.playing) setPlaying(false); setStep(S.step + d, true); }
  function reroll() { S.seed += 1; recompute(); renderStage(); renderExplain(); }

  function onClick(e) {
    var t = e.target, el;
    if (t.closest("#menu-btn")) { setMenu($("menu").hidden); return; }
    if (!$("menu").hidden && !t.closest("#menu")) setMenu(false);
    if ((el = t.closest(".tab"))) return setMode(el.dataset.mode);
    if ((el = t.closest(".step"))) { if (S.playing) setPlaying(false); return setStep(+el.dataset.step, true); }
    if ((el = t.closest(".preset"))) {
      var key = el.dataset.key, kind = el.dataset.kind;
      if (kind === "level") { setSentence(key, 0); S.step = Math.min(S.step, 5); renderAll(); }
      else if (kind === "scenario") { setScenario(key); renderStage(); }
      else if (kind === "miclevel") { M.level = key; M.index = 0; M.result = null; M.status = ""; renderAll(); }
      else if (kind === "chatlevel") { C.level = key; renderOpts(); markDirty(); }
      return;
    }
    if ((el = t.closest("[data-ph]"))) return selectPhone(+el.dataset.ph);
    switch (t.id) {
      case "b-play": setPlaying(!S.playing); break;
      case "b-prev": manualStep(-1); break;
      case "b-next": manualStep(1); break;
      case "b-reroll": reroll(); break;
      case "b-listen": if (speak(micSentence().text)) practiceEvent("listen"); break;
      case "b-rec": if (M.recording) stopRecording(); else startRecording(); break;
      case "b-send": sendChat(); break;
      case "b-chatclear": C.log = []; C.last = C.lastReply = null; renderStage(); markDirty(); break;
      case "btn-help": openHelp(); break;
      case "help-close": closeHelp(); break;
      case "btn-exit": case "menu-exit": shutdown(); break;
      case "menu-help": openHelp(); break;
      case "menu-panel": setMenu(false); showPanel(); break;
      case "panel-btn": openSheet(true); break;
      case "panel-close": openSheet(false); break;
      case "rot-ok": document.body.classList.add("rot-ok"); break;
      case "b-tasks-reset": PR.done = {}; store("tasks", "{}"); renderExplain(); break;
    }
  }
  function onChange(e) {
    var t = e.target;
    if (t.id === "sel-sent") { setSentence(S.level, +t.value); renderAll(); }
    else if (t.id === "sel-subt") { S.sub.target = +t.value; S.sel = S.sub.target; recompute(); renderStage(); }
    else if (t.id === "sel-subp") { S.sub.perceived = t.value; recompute(); renderStage(); }
    else if (t.id === "sel-del") { S.del = +t.value; recompute(); renderStage(); }
    else if (t.id === "ck-legacy") { S.legacy = t.checked; recompute(); renderStage(); renderExplain(); practiceEvent("legacy"); }
    else if (t.id === "sel-micsent") { M.index = +t.value; M.result = null; M.status = ""; renderStage(); markDirty(); }
    else if (t.id === "ck-speak") { C.speak = t.checked; }
    else if (t.id === "ck-big") { setBig(t.checked); }
    else if (t.id === "file-in" && t.files && t.files[0]) { analyzeFile(t.files[0]); }
  }
  function onInput(e) {
    if (e.target.id === "rng-noise") { S.noise = +e.target.value; $("noise-lab").textContent = S.noise.toFixed(2); recompute(); renderStageLight(); }
  }

  function renderFooter() {
    var c = CREDITS;
    $("foot").textContent = [(c.AUTHOR || "Shuyao Gao") + (c.AUTHOR_EMAIL ? "（" + c.AUTHOR_EMAIL + "）" : ""), c.UNIVERSITY_EN, c.UNIVERSITY_KO, (c.SERIES_NAME ? c.SERIES_NAME + "系列" : ""),
      "代码 MIT · 文档 CC BY 4.0", "演示用后验为模拟数据", "离线纯静态网页，关掉标签页即全部结束"].filter(Boolean).join("  ·  ");
  }

  // ================================================================== self-test (tools/browser_check.py)
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function synthSpeechLike(sr) {
    // test signal only (never shown as a recording): 3 voiced bursts with a gliding pitch and 2 gaps
    var parts = [[0.25, 0], [0.7, 140], [0.35, 0], [0.6, 170], [0.25, 0], [0.8, 120], [0.3, 0]], out = [];
    parts.forEach(function (p) {
      var n = Math.floor(p[0] * sr);
      for (var i = 0; i < n; i++) {
        var t = i / sr, f = p[1] * (1 + 0.15 * Math.sin(2 * Math.PI * 1.5 * t));
        out.push(p[1] ? 0.4 * (Math.sin(2 * Math.PI * f * t) + 0.4 * Math.sin(4 * Math.PI * f * t)) : 0.0005 * Math.sin(i));
      }
    });
    return Float32Array.from(out);
  }
  async function selftest() {
    var out = { steps: [], ok: false };
    function note(s) { out.steps.push(s); }
    try {
      openHelp(); closeHelp(); note("help");
      setMode("demo"); await wait(CFG.watchStepMs * 5 + CFG.watchHoldMs + 300);
      note("auto demo scenario=" + S.scenario + " step=" + S.step);
      setPlaying(false);
      ["good", "sub", "del", "mumble"].forEach(function (sc) { setScenario(sc); for (var k = 1; k <= 5; k++) setStep(k, true); });
      setScenario("sub"); setStep(5); selectPhone(S.sub.target); renderPanel();
      if (!S.res.ok) throw new Error("scoring failed");
      note("scenarios ok, sub level=" + S.res.phones[S.sub.target].level);
      $("ck-legacy").click(); $("ck-legacy").click(); note("legacy toggle");
      setSentence("B2", 2); setScenario("del"); setStep(5); renderPanel();
      if (S.res.completeness.n_deletions < 1) throw new Error("deletion not detected");
      note("B2 del: missing=" + S.res.completeness.missing_words.join(","));
      reroll(); setBig(true); await wait(50); setBig(false); note("big");
      setMode("mic");
      var r = analyzeSamples(synthSpeechLike(16000), 16000);
      if (!r.ok) throw new Error("mic analysis failed");
      note("mic synth: pauses=" + r.n_pauses + " fluency=" + r.fluency_score + " inton=" + r.intonation_score);
      speak("test");
      setMode("chat");
      ["I is a student", "Yesterday I go to the park with my friends", "Hello"].forEach(function (m) { $("chat-in").value = m; sendChat(); });
      note("chat msgs=" + C.log.length);
      setMode("practice"); manualStep(1); note("practice");
      setMode("demo"); setPlaying(false);
      await wait(100);
      out.overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;
      out.rightOverflow = $("right").scrollHeight - $("right").clientHeight;
      out.viewport = [window.innerWidth, window.innerHeight];
      out.sizes = app.sizes;
      out.ok = window.__errors.length === 0 && !out.overflowX;
    } catch (err) { window.__errors.push("selftest: " + (err && err.stack || err)); }
    out.errors = window.__errors.slice();
    $("selftest").textContent = JSON.stringify(out);
    document.title = "SELFTEST " + (out.ok && !out.errors.length ? "PASS" : "FAIL");
  }
  /** Real-time recording path (tools/browser_check.py drives it over the DevTools protocol).
   *  mictest=stream: a synthetic test tone through a MediaStream (no microphone at all);
   *  mictest=device: getUserMedia with the browser's fake test device. */
  async function mictest(kind) {
    var out = { ok: false, kind: kind }, osc = null, gen = null;
    try {
      setMode("mic");
      var stream = null;
      if (kind === "stream") {
        var AC = window.AudioContext || window.webkitAudioContext;
        gen = new AC(); osc = gen.createOscillator();
        var gn = gen.createGain(), dest = gen.createMediaStreamDestination();
        osc.frequency.value = 160; gn.gain.value = 0.3; osc.connect(gn); gn.connect(dest); osc.start();
        if (gen.state !== "running") await gen.resume();
        stream = dest.stream;
      }
      var got = await startRecording(stream);
      out.gotMic = got;
      if (got) {
        await wait(1600);
        out.chunks = M.chunks.length;
        var r = await stopRecording();
        out.analysis = r ? { ok: r.ok, sec: r.total_sec, pauses: r.n_pauses, f0: r.pitch && Math.round(r.pitch.mean_hz) } : null;
      }
      out.ok = !!(out.analysis && out.analysis.ok && out.analysis.sec > 0.8) && window.__errors.length === 0;
    } catch (err) { window.__errors.push("mictest: " + (err && err.stack || err)); }
    try { if (osc) osc.stop(); if (gen) gen.close(); } catch (e) { /* ignore */ }
    out.errors = window.__errors.slice();
    $("selftest").textContent = JSON.stringify(out);
  }
  function prepareShot(kind) {
    closeHelp();
    if (kind === "mic") { setMode("mic"); M.level = "A2"; M.index = 0; renderAll(); analyzeSamples(synthSpeechLike(16000), 16000); M.status = "（截图用的程序合成测试信号，不是真人录音）"; renderStage(); return; }
    if (kind === "chat") { setMode("chat"); ["Yesterday I go to the park", "I has a cat and she likes to sleep on my bed"].forEach(function (m) { $("chat-in").value = m; sendChat(); }); return; }
    setPlaying(false);
    if (kind === "sub") { setSentence("A2", 0); setScenario("sub"); }
    else if (kind === "del") { setSentence("B1", 0); setScenario("del"); }
    else { setSentence("A1", 0); setScenario("good"); }
    setStep(5); renderAll();
  }

  // ================================================================== boot
  function boot() {
    $("app-title").textContent = CFG.title; document.title = CFG.title;
    renderFooter();
    bindTips(document);
    on(document, "keydown", onKey); on(document, "click", onClick); on(document, "change", onChange); on(document, "input", onInput);
    on(window, "resize", onResize); on(window, "orientationchange", onResize);
    classifyTouch();
    // press feedback (+ a tiny vibration on touch when allowed); all pressed states clear when fingers lift or the window loses focus
    on(document, "pointerdown", function (ev) {
      var b = ev.target.closest && ev.target.closest(".btn, .preset, .tab, .chip, .step, #panel-btn, #panel-close, #menu-btn, .big-btn");
      if (!b || b.disabled) return;
      b.classList.add("pressed");
      if (ev.pointerType === "touch" && navigator.vibrate && navigator.userActivation && navigator.userActivation.hasBeenActive) { try { navigator.vibrate(8); } catch (e2) { /* unsupported */ } }
    });
    on(document, "pointerup", clearPressed); on(document, "pointercancel", clearPressed); on(window, "blur", clearPressed);
    on(document, "contextmenu", function (ev) { if (app.touch.any && !ev.target.closest("input, textarea")) ev.preventDefault(); });
    on(document, "visibilitychange", function () { clearPressed(); if (document.hidden && M.recording) stopRecording(); });
    if (store("big") === "1") { document.body.classList.add("big"); $("ck-big").checked = true; }
    sentenceDefaults(); recompute();
    setMode("demo");
    window.__gop = { S: S, M: M, C: C, PR: PR, app: app, ready: true };   // read by tools/mobile_check_gop.py (tests only)
    if (params.get("selftest")) { closeHelp(); selftest(); return; }
    if (params.get("mictest")) { closeHelp(); mictest(params.get("mictest")); return; }
    if (params.get("exittest")) { closeHelp(); setTimeout(function () { $("btn-exit").click(); }, 300); return; }
    if (params.get("shot")) { prepareShot(params.get("shot")); if (params.get("big") === "1") setBig(true); return; }
    if (params.get("nohelp") !== "1" && store("helpSeen") !== "1") openHelp();
  }
  boot();
})();
