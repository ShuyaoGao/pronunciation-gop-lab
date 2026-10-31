"""
CEFR 自适应 AI 对话陪练。

目标（对应学生痛点 a、c）：
  - 按学习者 CEFR 水平控制用词/句式难度（A1 最简单 … B2 较难）；
  - 用母语者口语化表达，回答后给一句简短纠错/更地道的说法。

实现：走 OpenAI 兼容协议（同一套代码可接 OpenAI / 通义千问 / DeepSeek，仅改 base_url 与 model）。
若未配置 key，则降级为「离线规则陪练」——用固定模板演示同样的「分级 + 纠错」闭环，保证 demo 不依赖外部条件。

⚠ 关于 CEFR 控难度的可靠性（见调研文档「难点 5」）：仅靠 prompt 让 LLM 稳定输出某等级难度并不可靠，
  尤其 B1/B2。MVP 先用 prompt 控制；第二阶段应加「分级器二次校验 + 超标重写」。这里诚实标注，不夸大。
"""
from __future__ import annotations

from typing import Dict, List

import config

# 不同 CEFR 等级给 LLM 的难度约束
_CEFR_GUIDE = {
    "A1": "Use only the most basic words and very short simple sentences (present tense, ~5-8 words).",
    "A2": "Use simple everyday vocabulary and short sentences. Avoid idioms and complex grammar.",
    "B1": "Use common vocabulary and moderately complex sentences. A few connectors are fine.",
    "B2": "Use a natural range of vocabulary and complex sentences, but stay conversational.",
}


def build_system_prompt(cefr: str) -> str:
    guide = _CEFR_GUIDE.get(cefr, _CEFR_GUIDE["A2"])
    return (
        "You are a friendly, encouraging native-English speaking conversation partner for a "
        f"language learner at CEFR level {cefr}. {guide}\n"
        "Speak naturally and casually like a native speaker (contractions, common phrasing).\n"
        "Keep your reply to 1-2 short sentences, then ask one simple follow-up question to keep the chat going.\n"
        "If the learner's last message has an obvious grammar or word-choice mistake, add ONE short correction "
        "on a new line starting with '📝 ' showing a more natural way to say it. If there is no clear mistake, "
        "do not add a correction line.\n"
        "Reply in English only."
    )


def chat(history: List[Dict[str, str]], user_message: str, cefr: str = "A2") -> str:
    """
    history: [{"role":"user"/"assistant","content":...}, ...]（不含本轮 user_message）
    返回助手回复文本。
    """
    user_message = (user_message or "").strip()
    if not user_message:
        return "（请先说点什么吧～ Say something to start!）"

    if config.llm_enabled():
        try:
            return _chat_via_llm(history, user_message, cefr)
        except Exception as e:  # 网络/额度/配置问题 -> 退回离线兜底，不让 demo 崩
            return _chat_offline(user_message, cefr, error=str(e))
    return _chat_offline(user_message, cefr)


def adaptive_reply(history, user_message, target_cefr="A2", auto_adapt=False):
    """
    对话陪练 + CEFR 自适应闭环的总入口。返回 (reply, info)。
      - auto_adapt=True：综合发音历史 + 用户说话复杂度，自动估计应使用的等级；
      - 生成回复后，若回复难度超过目标等级，触发「超标重写」。
    info 是给界面展示的一行说明（估计到的等级 / 重写情况）。
    """
    import cefr
    info_parts = []
    level = target_cefr

    if auto_adapt:
        try:
            import history as hist
            sessions = hist.load_sessions()
        except Exception:
            sessions = []
        spoken = [m["content"] for m in (history or []) if m.get("role") == "user"]
        spoken.append(user_message)
        ul = cefr.estimate_user_level(sessions, spoken)
        level = ul.level
        info_parts.append(f"🎯 自动适应：估计你的水平≈{level}（{ul.reason}）")

    reply = chat(history, user_message, level)

    # 超标重写（仅在有 LLM 时真正改写；离线只提示）
    new_reply, changed, note = cefr.rewrite_if_too_hard(reply, level)
    if changed:
        reply = new_reply
    if note:
        info_parts.append(note)

    return reply, "  ｜  ".join(info_parts)


def _chat_via_llm(history, user_message, cefr) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    messages = [{"role": "system", "content": build_system_prompt(cefr)}]
    messages.extend(history[-8:])  # 只带最近几轮，控制 token
    messages.append({"role": "user", "content": user_message})

    resp = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=200,
    )
    return resp.choices[0].message.content.strip()


# ---------- 离线兜底陪练 ----------
_OFFLINE_REPLIES = {
    "A1": [
        "Nice! I like that too. What do you do on weekends?",
        "Cool! Do you have any pets?",
        "Great! What food do you like?",
    ],
    "A2": [
        "That sounds nice! What did you do yesterday?",
        "Interesting! Where would you like to travel next?",
        "Good point! How do you usually spend your free time?",
    ],
    "B1": [
        "That's a good point. Could you tell me a bit more about why you think so?",
        "I see what you mean. What would you do differently if you had the chance?",
        "Sounds reasonable. How did you first get interested in that?",
    ],
    "B2": [
        "That's an interesting perspective. What do you think are the main trade-offs?",
        "I'd love to hear more. How has your view on this changed over time?",
        "Fair enough. What evidence would change your mind?",
    ],
}


def _chat_offline(user_message: str, cefr: str, error: str = "") -> str:
    pool = _OFFLINE_REPLIES.get(cefr, _OFFLINE_REPLIES["A2"])
    # 用消息长度选一条回复，保证「同样输入→同样输出」（不引入随机，便于演示/复现）
    reply = pool[len(user_message) % len(pool)]
    correction = _simple_correction(user_message)
    note = "（离线规则陪练：未配置 LLM key，仅演示分级+纠错闭环；配置 OPENAI_API_KEY 后将启用真实 LLM）"
    text = reply
    if correction:
        text += f"\n📝 {correction}"
    text += f"\n\n{note}"
    if error:
        text += f"\n（说明：LLM 调用失败已自动降级，原因：{error}）"
    return text


def _simple_correction(msg: str) -> str:
    """极简启发式纠错，仅用于离线演示（不是真正的语法检查）。"""
    low = msg.lower().strip()
    if low.startswith("i is "):
        return "更自然：'I am ...'（I 后面用 am）"
    if " i has " in f" {low} " or low.startswith("i has "):
        return "更自然：'I have ...'（I 后面用 have）"
    if "yesterday" in low and (" go " in f" {low} " or " eat " in f" {low} "):
        return "提示：yesterday 表示过去，动词记得用过去式（go→went, eat→ate）。"
    return ""
