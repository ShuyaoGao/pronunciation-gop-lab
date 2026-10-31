"""
全局配置：模型、设备、路径、API key 开关。

所有可调项都集中在这里，并且优先读环境变量，方便在不改代码的情况下切换
（例如换更大的模型、切到 GPU、接入云端 API）。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------- 路径 ----------
# 项目根目录（本文件在 src/ 下，根目录是它的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"

SENTENCES_PATH = DATA_DIR / "sentences.json"        # 分级参考句
THRESHOLDS_PATH = DATA_DIR / "thresholds.json"      # GOP 阈值（可标定）
HINTS_PATH = DATA_DIR / "l2_hints_en.json"          # 英语 L2 发音纠错提示库


# ---------- 发音评分模型 ----------
# 默认用 wav2vec2 的「音素级 CTC」模型，输出 CMU 39 音素集（IPA 记号）。
# 它本就是「ASR 声学模型」，我们正用它每一帧的音素后验来算 GOP（见 README 原理说明）。
#
# 为什么选这个而不是 facebook/wav2vec2-lv-60-espeak-cv-ft？
#   后者的 tokenizer 在加载时强制依赖 espeak-ng，而本机（及很多 Windows 机器）装不上 espeak，
#   会直接报 "espeak not installed" 无法加载。本模型用标准 Wav2Vec2CTCTokenizer，零外部二进制依赖，
#   且其音素集恰好是 g2p_en（CMU 词典）经标准 ARPABET→IPA 折叠后的同一套，能直接对齐。
# 想换模型只需设环境变量 AISPEAK_MODEL。
DEFAULT_MODEL_ID = "vitouphy/wav2vec2-xls-r-300m-timit-phoneme"
MODEL_ID = os.environ.get("AISPEAK_MODEL", DEFAULT_MODEL_ID)

# 设备：默认 CPU（最稳、零配置）。本机若想用 GPU，设 AISPEAK_DEVICE=cuda。
# 注意：6GB 显存跑大模型可能 OOM，单句朗读用 CPU 通常几秒即可。
DEVICE = os.environ.get("AISPEAK_DEVICE", "cpu")

# 目标采样率（wav2vec2 系列固定 16kHz）
TARGET_SR = 16000


# ---------- LLM 对话陪练 ----------
# 走 OpenAI 兼容协议：同一套代码可接 OpenAI / 通义千问 / DeepSeek，只改 base_url 和 model。
# 没有配置 key 时，chat_coach 会自动降级为「离线规则陪练」，demo 仍可闭环。
LLM_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
LLM_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip() or None  # 通义千问/DeepSeek 时填各自兼容端点
LLM_MODEL = os.environ.get("AISPEAK_LLM_MODEL", "gpt-4o-mini").strip()


# ---------- Azure 发音评测（可选对照，默认关闭）----------
# 仅当同时配置了 key 与 region 才启用；否则界面显示「未配置，跳过」。
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "").strip()
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "").strip()


def llm_enabled() -> bool:
    """是否配置了可用的 LLM key。"""
    return bool(LLM_API_KEY)


def azure_enabled() -> bool:
    """是否配置了 Azure 发音评测。"""
    return bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)
