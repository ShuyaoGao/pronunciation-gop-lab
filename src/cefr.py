"""
CEFR 自适应（解决痛点 a：AI 不知道学习者水平、开口就太难）。

三件事：
  1. estimate_text_cefr：估计一段英文文本的难度等级（A1~C1）；
  2. estimate_user_level：综合「发音历史表现 + 学习者能说出的句子复杂度」估计其当前水平；
  3. rewrite_if_too_hard：若 AI 输出难度超过目标等级，触发「超标重写」（有 LLM 时调 LLM 简化）。

⚠ 诚实说明（见调研文档难点 5）：这里的难度估计是**轻量级词汇/句法启发式**（词长、长词占比、句长、音节数），
  不是在 Ace-CEFR 上微调过的分级器。仅靠它和 prompt 控难度并不完全可靠，尤其 B2/C1。
  第二阶段应替换为在 Ace-CEFR 数据上微调的小模型。这里先把「估计→反馈→重写」的闭环搭好。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

import config

CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1"]


def cefr_index(level: str) -> int:
    try:
        return CEFR_ORDER.index(level)
    except ValueError:
        return 1  # 默认 A2


@dataclass
class TextCEFR:
    level: str
    complexity: float                 # 0~100 综合复杂度
    n_words: int = 0
    avg_word_len: float = 0.0
    long_word_ratio: float = 0.0
    avg_sentence_len: float = 0.0
    avg_syllables: float = 0.0
    features_note: str = ""


def _count_syllables(word: str) -> int:
    """用元音字母组数粗估音节数（无需任何模型/词典）。"""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    # 去掉常见的哑 e（make->1）；但 -le 结尾的成音节 l 不减（apple->2、little->2）
    if w.endswith("e") and not w.endswith("le") and n > 1:
        n -= 1
    return max(1, n)


def estimate_text_cefr(text: str) -> TextCEFR:
    text = (text or "").strip()
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return TextCEFR(level="A1", complexity=0.0)

    n_words = len(words)
    lengths = [len(w) for w in words]
    avg_word_len = sum(lengths) / n_words
    long_word_ratio = sum(1 for L in lengths if L >= 8) / n_words
    syl = [_count_syllables(w) for w in words]
    avg_syllables = sum(syl) / n_words

    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    n_sent = max(1, len(sentences))
    avg_sentence_len = n_words / n_sent

    # 综合复杂度（启发式加权，已做大致缩放到 0~100）
    complexity = (
        (avg_word_len - 3.0) * 9.0 +
        long_word_ratio * 80.0 +
        (avg_sentence_len - 4.0) * 2.2 +
        (avg_syllables - 1.0) * 22.0
    )
    complexity = float(max(0.0, min(100.0, complexity)))

    # 复杂度 -> CEFR band（临时分界，需用 Ace-CEFR 标定）
    if complexity < 18:
        level = "A1"
    elif complexity < 34:
        level = "A2"
    elif complexity < 52:
        level = "B1"
    elif complexity < 70:
        level = "B2"
    else:
        level = "C1"

    note = (f"词数{n_words}、平均词长{avg_word_len:.1f}、长词占比{long_word_ratio:.0%}、"
            f"平均句长{avg_sentence_len:.1f}、平均音节{avg_syllables:.1f}")
    return TextCEFR(level=level, complexity=round(complexity, 1), n_words=n_words,
                    avg_word_len=round(avg_word_len, 2), long_word_ratio=round(long_word_ratio, 3),
                    avg_sentence_len=round(avg_sentence_len, 2), avg_syllables=round(avg_syllables, 2),
                    features_note=note)


@dataclass
class UserLevel:
    level: str
    reason: str
    pronunciation_avg: Optional[float] = None
    production_level: Optional[str] = None


def estimate_user_level(sessions: List[dict], spoken_texts: List[str]) -> UserLevel:
    """
    综合两路信号估计用户水平：
      - 发音表现（最近几次综合分）：分低则下调上限（发不准就别硬塞难句）；
      - 产出复杂度（用户说出的句子）：能稳定产出更复杂的句子则上调。
    """
    # 1) 产出复杂度
    texts = [t for t in (spoken_texts or []) if t and t.strip()]
    if texts:
        levels = [cefr_index(estimate_text_cefr(t).level) for t in texts[-5:]]
        prod_idx = int(round(sum(levels) / len(levels)))
        production_level = CEFR_ORDER[max(0, min(len(CEFR_ORDER) - 1, prod_idx))]
    else:
        prod_idx = 1
        production_level = None

    # 2) 发音表现
    pron_avg = None
    cap_idx = len(CEFR_ORDER) - 1
    recent = [s for s in sessions if s.get("overall") is not None][-5:]
    if recent:
        pron_avg = round(sum(float(s["overall"]) for s in recent) / len(recent), 1)
        # 发音越差，越限制难度上限
        if pron_avg < 50:
            cap_idx = 0      # A1
        elif pron_avg < 65:
            cap_idx = 1      # A2
        elif pron_avg < 78:
            cap_idx = 2      # B1
        else:
            cap_idx = len(CEFR_ORDER) - 1

    est_idx = min(prod_idx, cap_idx) if recent else prod_idx
    est_idx = max(0, min(len(CEFR_ORDER) - 1, est_idx))
    level = CEFR_ORDER[est_idx]

    reasons = []
    if production_level:
        reasons.append(f"你说的句子复杂度≈{production_level}")
    if pron_avg is not None:
        reasons.append(f"近期发音综合分≈{pron_avg}（据此限制难度上限为 {CEFR_ORDER[cap_idx]}）")
    if not reasons:
        reasons.append("暂无足够数据，默认 A2")
    return UserLevel(level=level, reason="；".join(reasons),
                     pronunciation_avg=pron_avg, production_level=production_level)


def rewrite_if_too_hard(text: str, target_cefr: str):
    """
    若 text 难度比目标高 >=1 个等级：
      - 有 LLM：调 LLM 重写到目标等级；
      - 无 LLM：返回原文 + 提示（离线无法重写，只能标注超标）。
    返回 (新文本, 是否改写, 说明)。
    """
    est = estimate_text_cefr(text)
    if cefr_index(est.level) - cefr_index(target_cefr) < 1:
        return text, False, ""  # 没超标

    if not config.llm_enabled():
        return text, False, f"（提示：这段回复估计为 {est.level}，高于目标 {target_cefr}；离线模式无法自动重写）"

    try:
        from openai import OpenAI
        client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        resp = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content":
                    f"Rewrite the user's English text to CEFR {target_cefr} level: simpler words and shorter "
                    f"sentences, but keep the meaning. Output only the rewritten English."},
                {"role": "user", "content": text},
            ],
            temperature=0.3, max_tokens=200,
        )
        new_text = resp.choices[0].message.content.strip()
        return new_text, True, f"（已把回复从 {est.level} 重写到目标 {target_cefr}）"
    except Exception as e:
        return text, False, f"（超标重写失败，保留原文：{e}）"
