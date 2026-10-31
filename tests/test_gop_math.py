"""
纯 Python 逻辑单测（不需要下载大模型）：CTC 强制对齐、GOP→分数映射、G2P。
运行：<env-python> tests/test_gop_math.py
"""
import sys
from pathlib import Path

import numpy as np
import pytest

# Windows 控制台默认 GBK，打印 IPA 会报错；强制 stdout 用 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctc_align import ctc_forced_align          # noqa: E402
from thresholds import load_thresholds          # noqa: E402
from g2p import G2P                             # noqa: E402


def test_ctc_align_basic():
    # vocab: 0=blank, 1,2,3 三个音素。构造 6 帧，明显先 1 再 2 再 3。
    blank = 0
    V = 4
    T = 6
    logp = np.full((T, V), -5.0)
    plan = [1, 1, 2, 2, 3, 3]  # 每帧“真正”想要的音素
    for t, sym in enumerate(plan):
        logp[t, sym] = -0.01
    logp = logp - np.log(np.exp(logp).sum(axis=1, keepdims=True))  # 归一化成 log 概率

    segs = ctc_forced_align(logp, [1, 2, 3], blank)
    assert len(segs) == 3, segs
    # 顺序应单调递增、不重叠
    assert segs[0][1] <= segs[0][2] <= segs[1][1] <= segs[1][2] <= segs[2][1] <= segs[2][2], segs
    # 音素 1 应落在前两帧附近，3 应落在后两帧附近
    assert segs[0][1] == 0, segs
    assert segs[2][2] == T, segs
    print("[OK] test_ctc_align_basic:", segs)


def test_ctc_align_too_short():
    blank = 0
    logp = np.full((2, 4), -1.0)
    segs = ctc_forced_align(logp, [1, 2, 3], blank)  # T<N
    assert segs == [], segs
    print("[OK] test_ctc_align_too_short")


def test_threshold_mapping():
    # 插值数学用固定锚点测（不依赖 data/thresholds.json——它会被标定脚本更新）
    from thresholds import Thresholds
    th = Thresholds(
        calibrated=False,
        anchors=[(0.0, 100.0), (-0.3, 90.0), (-1.0, 75.0), (-2.0, 50.0), (-3.5, 25.0), (-5.0, 0.0)],
        green_cutoff=85.0,
        yellow_cutoff=60.0,
    )
    assert abs(th.gop_to_score(0.0) - 100) < 1e-6
    assert abs(th.gop_to_score(-0.3) - 90) < 1e-6
    assert abs(th.gop_to_score(-2.0) - 50) < 1e-6
    assert th.gop_to_score(-10.0) == 0.0       # 下溢钳到 0
    assert th.gop_to_score(5.0) == 100.0       # 上溢钳到 100
    # 单调不增
    xs = [0.0, -0.3, -1.0, -2.0, -3.5, -5.0]
    ys = [th.gop_to_score(x) for x in xs]
    assert all(ys[i] >= ys[i + 1] for i in range(len(ys) - 1)), ys
    # 等级
    assert th.level(95) == "good"
    assert th.level(70) == "warn"
    assert th.level(30) == "bad"
    # 真实阈值文件：只验证能加载、锚点单调、满分零分边界（不锁具体锚点值）
    live = load_thresholds()
    assert live.anchors[0][1] == 100.0 and live.anchors[-1][1] == 0.0
    lys = [live.gop_to_score(x) for x in xs]
    assert all(lys[i] >= lys[i + 1] for i in range(len(lys) - 1)), lys
    print("[OK] test_threshold_mapping:", ys, "| live:", [round(v, 1) for v in lys])


def test_g2p():
    pytest.importorskip("g2p_en")   # optional heavy dependency (downloads nltk data); the web version does not need it
    g = G2P()
    phones = g.phonemize("I have a red apple.")
    assert len(phones) > 0
    ipas = [p.ipa for p in phones]
    # 每个 ipa 都应是模型词表里的合法记号（这里只验证非空且无 None）
    assert all(p.ipa for p in phones)
    # "red" 里应包含 /ɹ/
    assert any(p.ipa == "ɹ" for p in phones), ipas
    words = sorted(set(p.word for p in phones))
    print("[OK] test_g2p: phones=", ipas)
    print("            words=", words)


def test_align_ops():
    from pronunciation import _align_ops
    # 漏读一个音：hyp 少了 'b'
    ops = _align_ops(["a", "b", "c"], ["a", "c"])
    assert sum(1 for o in ops if o[0] == "del") == 1, ops
    assert sum(1 for o in ops if o[0] == "ins") == 0, ops
    # 多读一个音
    ops = _align_ops(["a", "c"], ["a", "b", "c"])
    assert sum(1 for o in ops if o[0] == "ins") == 1, ops
    # 完全一致
    ops = _align_ops(["a", "b"], ["a", "b"])
    assert all(o[0] == "match" for o in ops), ops
    print("[OK] test_align_ops")


def test_cefr_ordering():
    import cefr
    easy = cefr.estimate_text_cefr("I have a red apple.")
    mid = cefr.estimate_text_cefr("Although it was raining, we decided to walk to the station.")
    hard = cefr.estimate_text_cefr("The committee thoroughly reviewed the comprehensive proposal before approving it.")
    idx = cefr.cefr_index
    assert idx(easy.level) < idx(mid.level) <= idx(hard.level), (easy.level, mid.level, hard.level)
    assert easy.complexity < mid.complexity < hard.complexity
    # 重写判定：C1 文本在目标 A2 下应判为"超标"
    _, changed_offline, note = cefr.rewrite_if_too_hard(
        "The committee thoroughly reviewed the comprehensive proposal.", "A2")
    assert note != ""  # 离线下至少给出超标提示
    print("[OK] test_cefr_ordering:", easy.level, mid.level, hard.level)


def test_syllables():
    import cefr
    assert cefr._count_syllables("apple") == 2
    assert cefr._count_syllables("I") == 1
    assert cefr._count_syllables("comprehensive") == 4
    assert cefr._count_syllables("make") == 1  # 哑 e
    print("[OK] test_syllables")


if __name__ == "__main__":
    test_ctc_align_basic()
    test_ctc_align_too_short()
    test_threshold_mapping()
    test_g2p()
    test_align_ops()
    test_cefr_ordering()
    test_syllables()
    print("\nAll logic tests passed.")
