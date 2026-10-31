/* Display names, presets and colors in one place. No product, platform or trademark names. */
(function (g) {
  "use strict";
  g.APP_CONFIG = {
    title: "发音评分实验室 · 看懂 GOP",
    shortTitle: "发音评分实验室",
    storagePrefix: "pronunciation-gop-lab.",
    simBadge: "模拟数据：后验矩阵由程序生成，不是真实录音，也不是真实模型的输出",

    // 4 difficulty presets = CEFR levels of data/sentences.json
    levels: [
      { key: "A1", name: "A1 入门", tip: "最短最常用的句子（12–17 个音素）" },
      { key: "A2", name: "A2 基础", tip: "日常句子（20–30 个音素）" },
      { key: "B1", name: "B1 进阶", tip: "带从句的长句（28–40 个音素）" },
      { key: "B2", name: "B2 中高级", tip: "多音节词多的长句（43–51 个音素）" }
    ],

    // simulated reading scenarios
    scenarios: [
      { key: "good", name: "读得好", tip: "每个音都清楚：模拟后验里应读音素在对应帧上概率最高" },
      { key: "sub", name: "读错一个音", tip: "把一个音换成常见的错音（例如 /θ/ 读成 /s/）：那几帧上概率最高的是错音" },
      { key: "del", name: "漏读一个词", tip: "整整一个词没有读出来：这个词的音素在后验里根本没有出现" },
      { key: "mumble", name: "含糊不清", tip: "每个音都不够清楚：应读音素和相近的音概率差不多" }
    ],

    steps: [
      { key: "g2p", name: "① 应读音素", tip: "参考句 → 应该读出来的音素序列（G2P，事先算好）" },
      { key: "post", name: "② 逐帧后验", tip: "每 20 毫秒一帧，模型给 44 个输出各一个概率（这里是模拟的）" },
      { key: "align", name: "③ 强制对齐", tip: "CTC Viterbi：把应读音素一个个对到帧上（白线）" },
      { key: "gop", name: "④ 算 GOP", tip: "每个音素在自己那几帧上：log P(应读音) − log P(最可能的音)，取平均" },
      { key: "score", name: "⑤ 打分反馈", tip: "GOP 按标定的锚点换成 0–100 分和红黄绿，再查表给纠错建议" }
    ],

    watchStepMs: 1700,     // auto demo: time per pipeline step
    watchHoldMs: 5200,     // auto demo: time on the final result before the next scenario

    chatLevels: ["A1", "A2", "B1", "B2"],

    colors: {
      good: "#2dbd6e", warn: "#e3b341", bad: "#ff6b6b",
      path: "#ffffff", canon: "#ffd54f", perceived: "#4fc3f7"
    }
  };
})(typeof window !== "undefined" ? window : globalThis);
