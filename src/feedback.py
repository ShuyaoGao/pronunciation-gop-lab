"""
纠错反馈与可视化：把发音评分结果变成「哪个音错、错在哪、怎么改」的文字提示，
以及一张「按单词分组、逐音素红绿黄」的 HTML 图。
"""
from __future__ import annotations

import html
import json
from typing import Dict

from config import HINTS_PATH
from pronunciation import PhoneScore, PronunciationResult

# 颜色（绿/黄/红）
_LEVEL_COLOR = {
    "good": "#1a7f37",   # 绿
    "warn": "#bf8700",   # 黄/琥珀
    "bad":  "#cf222e",   # 红
}
_LEVEL_BG = {
    "good": "#e9f7ee",
    "warn": "#fff8e1",
    "bad":  "#ffebe9",
}
_LEVEL_LABEL = {"good": "标准", "warn": "需注意", "bad": "明显偏差"}

_hints_cache: Dict | None = None


def _load_hints() -> Dict:
    global _hints_cache
    if _hints_cache is None:
        with open(HINTS_PATH, "r", encoding="utf-8") as f:
            _hints_cache = json.load(f)
    return _hints_cache


def make_hint(p: PhoneScore) -> str:
    """为一个发音偏差的音素生成纠错提示。"""
    hints = _load_hints()
    entry = hints.get(p.ipa)
    if entry:
        # 优先匹配「被听成了哪个音」的具体提示
        for conf in entry.get("confusions", []):
            if conf.get("perceived") == p.perceived_ipa:
                return conf["tip"]
        if entry.get("tip"):
            base = entry["tip"]
            if p.perceived_ipa and p.perceived_ipa != p.ipa:
                return f"{base}（系统更像听到 /{p.perceived_ipa}/）"
            return base
    # 没有针对性提示时的兜底
    if p.perceived_ipa and p.perceived_ipa != p.ipa:
        return f"/{p.ipa}/ 发得更像 /{p.perceived_ipa}/，注意口型与时长。"
    return f"/{p.ipa}/ 发音偏离较大，注意口型与时长。"


def attach_hints(result: PronunciationResult) -> PronunciationResult:
    """给所有非「标准」音素填上纠错提示。"""
    for p in result.phones:
        if p.level != "good":
            p.hint = make_hint(p)
    return result


def result_to_html(result: PronunciationResult) -> str:
    """渲染逐音素红绿图（按单词分组）。每个音素是一个彩色小块，鼠标悬停看详情。"""
    if not result.ok:
        return f"<div style='color:#cf222e;padding:8px'>无法评分：{html.escape(result.note)}</div>"

    parts = []
    parts.append(_overall_banner(result))

    parts.append("<div style='line-height:2.4;font-size:18px;margin-top:10px'>")
    for w in result.words:
        chips = []
        for p in w.phones:
            chips.append(_phone_chip(p))
        word_html = (
            "<span style='display:inline-block;margin:2px 10px 2px 0;"
            "padding:2px 6px;border:1px solid #eee;border-radius:8px;vertical-align:middle'>"
            f"<span style='color:#888;font-size:12px'>{html.escape(w.word)}</span><br>"
            f"{''.join(chips)}"
            "</span>"
        )
        parts.append(word_html)
    parts.append("</div>")

    # 纠错清单
    bad_phones = [p for p in result.phones if p.level != "good"]
    if bad_phones:
        parts.append("<div style='margin-top:14px'><b>需要改进的音：</b><ul style='margin:6px 0'>")
        for p in bad_phones:
            color = _LEVEL_COLOR[p.level]
            parts.append(
                f"<li style='margin:3px 0'><span style='color:{color};font-weight:bold'>"
                f"/{html.escape(p.ipa)}/</span> "
                f"（{html.escape(p.word)}，{p.score:.0f}分）：{html.escape(p.hint)}</li>"
            )
        parts.append("</ul></div>")
    else:
        parts.append("<div style='margin-top:14px;color:#1a7f37'>👍 各音素发音都达标，很棒！</div>")

    # 完整度（漏读/多读）
    parts.append(_completeness_block(result))
    # 韵律 / 流利度
    parts.append(_prosody_block(result))

    # 系统实际听到的
    parts.append(
        "<div style='margin-top:10px;color:#666;font-size:13px'>"
        f"系统对整段录音的音素识别（贪心解码）：<code>{html.escape(result.transcript_ipa)}</code></div>"
    )
    if result.note:
        parts.append(f"<div style='margin-top:8px;color:#bf8700;font-size:13px'>{html.escape(result.note)}</div>")
    return "".join(parts)


def _color_of(score: float) -> str:
    return "#1a7f37" if score >= 80 else ("#bf8700" if score >= 55 else "#cf222e")


def _metric_card(label: str, score: float, sub: str = "") -> str:
    color = _color_of(score)
    sub_html = f"<div style='color:#999;font-size:11px'>{html.escape(sub)}</div>" if sub else ""
    return (
        "<div style='display:inline-block;min-width:96px;text-align:center;margin:4px 8px 4px 0;"
        "padding:8px 10px;border:1px solid #eee;border-radius:10px;vertical-align:top'>"
        f"<div style='font-size:12px;color:#666'>{html.escape(label)}</div>"
        f"<div style='font-size:24px;font-weight:bold;color:{color}'>{score:.0f}</div>"
        f"{sub_html}</div>"
    )


def _overall_banner(result: PronunciationResult) -> str:
    score = result.overall_score
    color = _color_of(score)
    cards = [_metric_card("综合", score),
             _metric_card("音素准确度", result.accuracy_score)]
    if result.completeness is not None:
        c = result.completeness
        cards.append(_metric_card("完整度", c.score, f"漏{c.n_deletions}/多{c.n_insertions}"))
    if result.prosody is not None and getattr(result.prosody, "ok", False):
        pr = result.prosody
        cards.append(_metric_card("流利度", pr.fluency_score, f"{pr.speaking_rate_pps:.1f} 音素/秒"))
        cards.append(_metric_card("韵律", pr.prosody_score, "语调+重音"))
    return (
        f"<div style='font-size:20px;margin-bottom:4px'>综合发音评分："
        f"<b style='color:{color}'>{score:.0f}</b> / 100</div>"
        f"<div>{''.join(cards)}</div>"
    )


def _completeness_block(result: PronunciationResult) -> str:
    c = result.completeness
    if c is None:
        return ""
    if c.n_deletions == 0 and c.n_insertions == 0:
        return "<div style='margin-top:6px;color:#1a7f37;font-size:13px'>✅ 完整度：句子读全了，无明显漏读/多读。</div>"
    msg = f"完整度 {c.score:.0f}：检测到漏读 {c.n_deletions} 个音、多读/插入 {c.n_insertions} 个音。"
    if c.missing_words:
        msg += " 疑似漏读的词：" + "、".join(html.escape(w) for w in c.missing_words) + "。"
    return f"<div style='margin-top:6px;color:#bf8700;font-size:13px'>⚠ {msg}</div>"


def _prosody_block(result: PronunciationResult) -> str:
    pr = result.prosody
    if pr is None or not getattr(pr, "ok", False):
        return ""
    detail = (f"语速 {pr.speaking_rate_pps:.1f} 音素/秒（约 {pr.speaking_rate_wpm:.0f} 词/分）；"
              f"句中停顿 {pr.n_pauses} 处（共 {pr.total_pause_sec:.1f}s）；"
              f"音高起伏 {pr.pitch_variation_st:.1f} 半音、音域 {pr.pitch_range_st:.1f} 半音。")
    tips = ""
    if pr.tips:
        tips = "<ul style='margin:4px 0'>" + "".join(
            f"<li>{html.escape(t)}</li>" for t in pr.tips) + "</ul>"
    return (
        "<div style='margin-top:8px;border-left:3px solid #ddd;padding-left:8px'>"
        "<b>流利度 / 韵律</b>"
        f"<div style='color:#666;font-size:13px'>{detail}</div>{tips}</div>"
    )


def _phone_chip(p: PhoneScore) -> str:
    color = _LEVEL_COLOR[p.level]
    bg = _LEVEL_BG[p.level]
    tip = (f"应读 /{p.ipa}/；得分 {p.score:.0f}（{_LEVEL_LABEL[p.level]}）；"
           f"系统听到 /{p.perceived_ipa}/；GOP={p.gop:.2f}")
    if p.hint:
        tip += f"；建议：{p.hint}"
    return (
        f"<span title='{html.escape(tip)}' "
        f"style='display:inline-block;margin:0 2px;padding:1px 7px;border-radius:6px;"
        f"background:{bg};color:{color};border:1px solid {color}'>"
        f"{html.escape(p.ipa)}</span>"
    )
