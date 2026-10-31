# 第三方组件、模型、数据与来源说明 / Third-party notices

## 网页 play/（下载后双击即玩的部分）

| 内容 | 说明 |
|---|---|
| 第三方 JavaScript 库 | **没有**。全部代码在 `play/js/` |
| 字体 | **不内嵌任何字体**，只按名字引用操作系统自带字体（Windows：Microsoft YaHei / Segoe UI；macOS：PingFang SC 等），字体文件不随仓库分发 |
| 音频 | **不含任何录音或音频文件**。“听示范”用浏览器自带的 speechSynthesis（操作系统的语音），麦克风录音只在本机内存里分析 |
| 演示用后验矩阵 | 由 `play/js/simulate.js` 程序生成，**不是**真实录音或真实模型的输出 |
| 网络请求 | 没有。不用 CDN、不用 fetch / XHR、不用 service worker |

## 发音模型（不随仓库分发；完整版首次运行时从 HuggingFace 下载）

| 模型 | 许可证 | 说明 |
|---|---|---|
| `vitouphy/wav2vec2-xls-r-300m-timit-phoneme` | Apache-2.0（模型卡） | 在 DARPA TIMIT 上微调的音素 CTC 模型，约 1.26 GB。`data/model_vocab.json` 只复制了它的 44 个输出记号（词表），没有复制权重 |
| 其基础模型 `facebook/wav2vec2-xls-r-300m` | Apache-2.0（模型卡） | 由上面的模型作者用作微调起点 |
| 训练数据 DARPA TIMIT（LDC93S1） | LDC 用户协议（非公开再分发） | 本项目**没有**使用或分发 TIMIT 本身；它只是第三方模型的训练数据 |

## 数据

| 数据 | 许可证 | 在本仓库中的形式 |
|---|---|---|
| speechocean762（OpenSLR-101） | CC BY 4.0 | **不含**原始音频或标注；只含在其上标定得到的统计结果：`data/thresholds.json`、`data/calibration_report.md`。标定脚本会在运行时下载数据 |
| CMU Pronouncing Dictionary（经 g2p_en / nltk 使用） | BSD 风格许可 | `data/g2p_sentences.json` 是 g2p_en 对 19 句参考句的输出 |
| `data/sentences.json`、`data/l2_hints_en.json` | 本项目原创（文档类，CC BY 4.0） | 参考句与纠错提示 |

## Python（只在运行完整版 / 重新生成数据 / 跑测试时需要，不随仓库分发）

| 包 | 用途 | 许可证（来自 PyPI 元数据） |
|---|---|---|
| torch | 模型推理 | BSD-3-Clause |
| transformers | 加载 wav2vec2 | Apache-2.0 |
| gradio | 完整版网页 UI | Apache-2.0 |
| librosa | 重采样、能量、pYIN | ISC |
| soundfile | 读音频 | BSD-3-Clause |
| g2p_en | G2P | Apache-2.0；其依赖 `inflect`（MIT）、`distance`（GPL）由 pip 安装，不随本仓库分发 |
| nltk | g2p_en 的依赖 | Apache-2.0 |
| numpy / scipy / pandas | 数值计算 | BSD |
| openai | 可选的大模型对话（OpenAI 兼容接口） | Apache-2.0 |
| pytest | 测试 | MIT |
| kokoro（可选） | 本地神经 TTS | Apache-2.0 |
| 商用发音评测 SDK（可选，默认不装） | `src/azure_compare.py` 的对照功能 | 按其厂商许可；需用户自行安装并提供 key |

## 测试工具（不随仓库分发）

- Node.js：`tests/js/harness.js` 在 Node 里运行 `play/js/*.js`，与 Python 逐项比对；`tools/cdp_eval.js` 用 Node 自带的 WebSocket 读取浏览器自检结果。
- 本机已安装的 Microsoft Edge：`tools/browser_check.py`（电脑尺寸）和 `tools/mobile_check_gop.py`（手机尺寸，DevTools 协议模拟触屏）通过 Playwright（Apache-2.0，`pip install playwright`）驱动它，不下载任何浏览器；录音测试只用合成音和浏览器的假麦克风。

## 文本

- `LICENSE-docs` 中的 CC BY 4.0 法律文本来自 Creative Commons（creativecommons.org），按其规定原样附上。

## 代码来源与参考文献

- 全部代码为作者原创（由 Claude Code 辅助编写，经作者审阅），没有从外部项目复制代码。
- 方法参考（只用了思想，未复制代码）：
  - S. M. Witt, S. J. Young, "Phone-level pronunciation scoring and assessment for interactive language learning", *Speech Communication* 30 (2000) — GOP。
  - A. Graves et al., "Connectionist Temporal Classification", ICML 2006 — CTC 与强制对齐。
  - A. de Cheveigné, H. Kawahara, "YIN, a fundamental frequency estimator for speech and music", *JASA* 111 (2002) — 网页版音高估计。
  - M. Mauch, S. Dixon, "pYIN", ICASSP 2014 — 完整版音高估计（librosa.pyin）。
  - A. Babu et al., "XLS-R: Self-supervised Cross-lingual Speech Representation Learning at Scale", 2021 — 发音模型的基础模型。
  - J. Zhang et al., "speechocean762: An Open-Source Non-native English Speech Corpus for Pronunciation Assessment", Interspeech 2021 — 标定数据集。

## 名称与商标

- 网页界面不出现任何商业产品或平台名称。
- `src/` 和 `.env.example` 中提到的第三方服务（大模型接口、商用发音评测接口、TTS）都是**可选、需要用户自备 key** 的集成示例，名称只用来说明兼容对象，与这些厂商没有关联。
- 学校名称只以文字形式出现在署名中，没有使用校标图片。
