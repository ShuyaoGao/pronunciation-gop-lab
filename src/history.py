"""
学习档案：把每次发音评分的结果落到本地文件，做成
  - 错误档案：哪些音反复出错（频次 + 平均分）；
  - 学习曲线：综合分随时间的变化；
  - 间隔重复推荐：综合「出错频次 + 最近是否练过」推荐下一步练什么音、练哪句。

全部本地存储（data/history.jsonl，一行一次练习），不上传任何数据。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import DATA_DIR, SENTENCES_PATH

HISTORY_PATH = DATA_DIR / "history.jsonl"


def record_session(result, cefr: Optional[str] = None) -> None:
    """记录一次评分。result 为 PronunciationResult。"""
    if result is None or not getattr(result, "ok", False):
        return
    errors = [
        {"ipa": p.ipa, "arpabet": p.arpabet, "word": p.word,
         "score": p.score, "level": p.level, "perceived": p.perceived_ipa}
        for p in result.phones if p.level != "good"
    ]
    rec = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "reference": result.reference_text,
        "overall": result.overall_score,
        "accuracy": result.accuracy_score,
        "completeness": (result.completeness.score if result.completeness else None),
        "fluency": (result.prosody.fluency_score if result.prosody and result.prosody.ok else None),
        "prosody": (result.prosody.prosody_score if result.prosody and result.prosody.ok else None),
        "cefr": cefr,
        "n_phones": len(result.phones),
        "errors": errors,
    }
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_sessions() -> List[dict]:
    if not HISTORY_PATH.exists():
        return []
    out = []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def clear_history() -> None:
    if HISTORY_PATH.exists():
        HISTORY_PATH.unlink()


def score_trend(sessions: List[dict]) -> List[Tuple[str, float]]:
    """返回 [(序号#时间, 综合分), ...] 供画学习曲线。"""
    out = []
    for i, s in enumerate(sessions, 1):
        t = s.get("time", "")[-8:]  # HH:MM:SS
        out.append((f"#{i} {t}", float(s.get("overall") or 0)))
    return out


def error_phone_frequency(sessions: List[dict]) -> List[Tuple[str, int, float]]:
    """返回 [(ipa, 出错次数, 平均得分), ...]，按出错次数降序。"""
    cnt: Dict[str, int] = defaultdict(int)
    score_sum: Dict[str, float] = defaultdict(float)
    for s in sessions:
        for e in s.get("errors", []):
            ipa = e.get("ipa", "?")
            cnt[ipa] += 1
            score_sum[ipa] += float(e.get("score") or 0)
    rows = [(ipa, cnt[ipa], round(score_sum[ipa] / cnt[ipa], 1)) for ipa in cnt]
    rows.sort(key=lambda r: (-r[1], r[2]))
    return rows


_sentence_phones_cache: Optional[Dict[str, List[str]]] = None


def _sentence_phone_index() -> Dict[str, List[str]]:
    """把题库每句话 G2P 一遍，建立「句子 -> 它包含的 IPA 音素集合」（缓存）。"""
    global _sentence_phones_cache
    if _sentence_phones_cache is not None:
        return _sentence_phones_cache
    _sentence_phones_cache = {}
    try:
        from g2p import G2P
        g = G2P()
        with open(SENTENCES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for level, sents in data.items():
            if level.startswith("_"):
                continue
            for s in sents:
                ipas = sorted({p.ipa for p in g.phonemize(s)})
                _sentence_phones_cache[s] = ipas
    except Exception:
        pass
    return _sentence_phones_cache


def recommend(sessions: List[dict], top_n: int = 3) -> dict:
    """
    间隔重复 + 弱项优先 的推荐：
      - 优先级 = 出错次数 × 权重 + 「越久没练到越该练」的回顾权重；
      - 为每个推荐音从题库挑一句包含该音的句子。
    """
    if not sessions:
        return {"phones": [], "sentences": [], "note": "还没有练习记录，先去「发音评分」练几句吧。"}

    freq = error_phone_frequency(sessions)
    # 最近一次练习里出现过的音（用于"间隔重复"：最近练过的稍微降权）
    recent_phones = set()
    if sessions:
        last = sessions[-1]
        recent_phones = {e.get("ipa") for e in last.get("errors", [])}

    scored = []
    for ipa, c, avg in freq:
        priority = c * 2.0 + (5.0 - avg / 20.0)   # 错得多、平均分低 -> 优先
        if ipa in recent_phones:
            priority *= 0.8                         # 刚练过，稍降权（间隔重复）
        scored.append((ipa, c, avg, round(priority, 2)))
    scored.sort(key=lambda r: -r[3])
    top = scored[:top_n]

    # 为每个弱项音找练习句
    idx = _sentence_phone_index()
    rec_sentences = []
    used = set()
    for ipa, c, avg, _pri in top:
        for sent, ipas in idx.items():
            if ipa in ipas and sent not in used:
                rec_sentences.append((ipa, sent))
                used.add(sent)
                break

    phones = [{"ipa": ipa, "count": c, "avg_score": avg} for ipa, c, avg, _ in top]
    return {
        "phones": phones,
        "sentences": rec_sentences,
        "note": "建议重点练习上面这些反复出错的音；下面每句都包含对应的目标音。",
    }
