/* Ports of src/cefr.py (text difficulty heuristic) and src/chat_coach.py (offline rule-based
 * partner, NOT an AI model). Same inputs -> same outputs as Python (tests/test_play_parity.py). */
(function (g) {
  "use strict";
  var pyRound = g.GOP.pyRound;
  var CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1"];

  function cefrIndex(level) { var i = CEFR_ORDER.indexOf(level); return i < 0 ? 1 : i; }

  function countSyllables(word) {
    var w = word.toLowerCase().replace(/[^a-z]/g, "");
    if (!w) return 0;
    var groups = w.match(/[aeiouy]+/g) || [], n = groups.length;
    if (w.endsWith("e") && !w.endsWith("le") && n > 1) n -= 1;
    return Math.max(1, n);
  }

  function estimateTextCefr(text) {
    text = (text || "").trim();
    var words = text.match(/[A-Za-z']+/g) || [];
    if (!words.length) return { level: "A1", complexity: 0.0, n_words: 0, terms: null };
    var n = words.length, lengths = words.map(function (w) { return w.length; });
    var avgWordLen = lengths.reduce(function (a, b) { return a + b; }, 0) / n;
    var longRatio = lengths.filter(function (L) { return L >= 8; }).length / n;
    var syl = words.map(countSyllables), avgSyl = syl.reduce(function (a, b) { return a + b; }, 0) / n;
    var sentences = text.split(/[.!?]+/).filter(function (s) { return s.trim(); });
    var nSent = Math.max(1, sentences.length), avgSentLen = n / nSent;
    var terms = {
      word_len: (avgWordLen - 3.0) * 9.0, long_words: longRatio * 80.0,
      sentence_len: (avgSentLen - 4.0) * 2.2, syllables: (avgSyl - 1.0) * 22.0
    };
    var complexity = terms.word_len + terms.long_words + terms.sentence_len + terms.syllables;
    complexity = Math.max(0.0, Math.min(100.0, complexity));
    var level = complexity < 18 ? "A1" : complexity < 34 ? "A2" : complexity < 52 ? "B1" : complexity < 70 ? "B2" : "C1";
    return { level: level, complexity: pyRound(complexity, 1), n_words: n, avg_word_len: pyRound(avgWordLen, 2),
             long_word_ratio: pyRound(longRatio, 3), avg_sentence_len: pyRound(avgSentLen, 2),
             avg_syllables: pyRound(avgSyl, 2), terms: terms, words: words, syllables: syl };
  }

  var OFFLINE_REPLIES = {
    A1: ["Nice! I like that too. What do you do on weekends?", "Cool! Do you have any pets?", "Great! What food do you like?"],
    A2: ["That sounds nice! What did you do yesterday?", "Interesting! Where would you like to travel next?",
         "Good point! How do you usually spend your free time?"],
    B1: ["That's a good point. Could you tell me a bit more about why you think so?",
         "I see what you mean. What would you do differently if you had the chance?",
         "Sounds reasonable. How did you first get interested in that?"],
    B2: ["That's an interesting perspective. What do you think are the main trade-offs?",
         "I'd love to hear more. How has your view on this changed over time?",
         "Fair enough. What evidence would change your mind?"]
  };

  function simpleCorrection(msg) {
    var low = msg.toLowerCase().trim(), pad = " " + low + " ";
    if (low.indexOf("i is ") === 0) return "更自然：'I am ...'（I 后面用 am）";
    if (pad.indexOf(" i has ") >= 0 || low.indexOf("i has ") === 0) return "更自然：'I have ...'（I 后面用 have）";
    if (low.indexOf("yesterday") >= 0 && (pad.indexOf(" go ") >= 0 || pad.indexOf(" eat ") >= 0))
      return "提示：yesterday 表示过去，动词记得用过去式（go→went, eat→ate）。";
    return "";
  }

  /** chat_coach._chat_offline (full text, identical to Python) -> {text, reply, correction} */
  function chatOffline(userMessage, cefr) {
    var pool = OFFLINE_REPLIES[cefr] || OFFLINE_REPLIES.A2;
    var reply = pool[Array.from(userMessage).length % pool.length];
    var correction = simpleCorrection(userMessage);
    var note = "（离线规则陪练：未配置 LLM key，仅演示分级+纠错闭环；配置 OPENAI_API_KEY 后将启用真实 LLM）";
    var text = reply;
    if (correction) text += "\n📝 " + correction;
    text += "\n\n" + note;
    return { text: text, reply: reply, correction: correction };
  }

  /** chat_coach.chat(): strips the message first; empty -> fixed prompt */
  function chat(userMessage, cefr) {
    userMessage = (userMessage || "").trim();
    if (!userMessage) return { text: "（请先说点什么吧～ Say something to start!）", reply: "", correction: "" };
    return chatOffline(userMessage, cefr);
  }

  /** cefr.rewrite_if_too_hard, offline branch (no model in the page). */
  function rewriteCheck(text, target) {
    var est = estimateTextCefr(text);
    if (cefrIndex(est.level) - cefrIndex(target) < 1) return { changed: false, note: "", est: est };
    return { changed: false, est: est, note: "（提示：这段回复估计为 " + est.level + "，高于目标 " + target + "；离线模式无法自动重写）" };
  }

  g.TEXT = { CEFR_ORDER: CEFR_ORDER, cefrIndex: cefrIndex, countSyllables: countSyllables,
             estimateTextCefr: estimateTextCefr, chat: chat, chatOffline: chatOffline,
             simpleCorrection: simpleCorrection, rewriteCheck: rewriteCheck, OFFLINE_REPLIES: OFFLINE_REPLIES };
})(typeof window !== "undefined" ? window : globalThis);
