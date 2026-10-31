"""
GOP 阈值的加载与「GOP→0~100 分 / 颜色等级」的换算。

阈值集中放在 data/thresholds.json，方便用 scripts/calibrate_thresholds.py 标定后替换，
而不是把魔法数字散落在代码里。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Tuple

from config import THRESHOLDS_PATH


@dataclass
class Thresholds:
    calibrated: bool
    anchors: List[Tuple[float, float]]   # [(gop, score), ...]，按 gop 从大到小
    green_cutoff: float                  # score >= 此值 -> 绿（发音标准）
    yellow_cutoff: float                 # score >= 此值 -> 黄（偏差/需注意），否则红

    def gop_to_score(self, gop: float) -> float:
        """用分段线性插值把 GOP 映射到 0~100 分。"""
        anchors = self.anchors
        # anchors 已按 gop 降序；处理边界
        if gop >= anchors[0][0]:
            return anchors[0][1]
        if gop <= anchors[-1][0]:
            return anchors[-1][1]
        for (g_hi, s_hi), (g_lo, s_lo) in zip(anchors, anchors[1:]):
            if g_lo <= gop <= g_hi:
                # 线性插值
                ratio = (gop - g_lo) / (g_hi - g_lo) if g_hi != g_lo else 0.0
                return s_lo + ratio * (s_hi - s_lo)
        return anchors[-1][1]

    def level(self, score: float) -> str:
        """返回 'good' / 'warn' / 'bad'，对应前端绿/黄/红。"""
        if score >= self.green_cutoff:
            return "good"
        if score >= self.yellow_cutoff:
            return "warn"
        return "bad"


def load_thresholds() -> Thresholds:
    with open(THRESHOLDS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    anchors = [(float(g), float(s)) for g, s in data["gop_to_score_anchors"]]
    anchors.sort(key=lambda x: x[0], reverse=True)  # 保证按 gop 降序
    cutoffs = data["level_cutoffs"]
    return Thresholds(
        calibrated=bool(data.get("calibrated", False)),
        anchors=anchors,
        green_cutoff=float(cutoffs["green"]),
        yellow_cutoff=float(cutoffs["yellow"]),
    )
