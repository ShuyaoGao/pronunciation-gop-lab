"""
GOP 阈值标定脚本。

一条命令完成：下载 speechocean762 → 分层抽样 → 跑本项目 GOP 评分器 → 与专家音素分对照 →
写出新阈值 data/thresholds.json（旧文件备份为 thresholds_legacy.json）+ 标定报告 data/calibration_report.md。

原理：GOP 是相对分数，"多低算发错"没有绝对标准。用 speechocean762（5 位专家在音素级标注了
0/1/2 发音好坏，CC BY 4.0）做参照：
  - 在数据集上跑我们自己的评分器，得到每个音素的 GOP；
  - 用编辑距离把「我们的 canonical 音素序列」与「专家标注的音素序列」对齐（两边都折叠到同一套
    模型 IPA 记号），只取对齐上的 match 位置，避免序列错位污染分布；
  - 按专家分分成「发对(>= --good-th)」与「发错(<= --bad-th)」两组（中间分数不参与定阈值，但参与相关性）；
  - green 阈值取「发对组」P25，bad 阈值取「发错组」P75，分段线性锚点写回 thresholds.json；
  - 同时报告 AUC（GOP 区分发对/发错的能力）、与专家分的 Pearson/Spearman 相关、各音素分布。

数据来源（按顺序自动尝试，也可手动放置）：
  1. 本地已有：data/speechocean762/ 下已解压（找 resource/scores.json）；
  2. 中国镜像： https://openslr.magicdatatech.com/resources/101/speechocean762.tar.gz
  3. 美国源：   https://www.openslr.org/resources/101/speechocean762.tar.gz
  4. HuggingFace datasets（mispeech/speechocean762，需 pip install datasets）。

用法：
  python scripts/calibrate_thresholds.py                       # 默认：train 切分、抽样 1200 句、38 分钟预算
  python scripts/calibrate_thresholds.py --max-utts 2500 --split all --time-budget-min 120
说明：tar.gz 约 1.2GB；首次还会下载发音模型。GPU 可用时自动用 CUDA（可被 AISPEAK_DEVICE 覆盖）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import random
import shutil
import sys
import tarfile
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DATA_DIR = ROOT / "data"
DATASET_DIR = DATA_DIR / "speechocean762"
REPORT_PATH = DATA_DIR / "calibration_report.md"
LEGACY_PATH = DATA_DIR / "thresholds_legacy.json"

DOWNLOAD_SOURCES = [
    ("中国镜像 magicdatatech", "https://openslr.magicdatatech.com/resources/101/speechocean762.tar.gz"),
    ("美国源 openslr.org", "https://www.openslr.org/resources/101/speechocean762.tar.gz"),
]


# ---------------------------------------------------------------------------
# 数据集获取
# ---------------------------------------------------------------------------

def find_dataset_root() -> Path | None:
    """在 data/speechocean762/ 下找已解压的数据集根目录（含 resource/scores.json）。"""
    if not DATASET_DIR.is_dir():
        return None
    candidates = [DATASET_DIR] + [p for p in DATASET_DIR.iterdir() if p.is_dir()]
    for cand in candidates:
        if (cand / "resource" / "scores.json").exists():
            return cand
    # 兜底：递归找（解压层级不确定时）
    for p in DATASET_DIR.rglob("scores.json"):
        if p.parent.name == "resource":
            return p.parent.parent
    return None


def _download(url: str, dest: Path) -> bool:
    """带进度的下载；失败返回 False。"""
    def hook(blocks, bs, total):
        done = blocks * bs
        if total > 0 and blocks % 200 == 0:
            print(f"\r  {done / 1e6:.0f}/{total / 1e6:.0f} MB ({100.0 * done / total:.1f}%)",
                  end="", flush=True)
    try:
        tmp = dest.with_suffix(dest.suffix + ".part")
        urllib.request.urlretrieve(url, tmp, reporthook=hook)
        print()
        if tmp.stat().st_size < 100 * 1024 * 1024:  # 官方 tar.gz 实测约 521MB；太小说明拿到的是错误页
            print(f"  下载文件过小（{tmp.stat().st_size / 1e6:.0f}MB），疑似失败。")
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dest)
        return True
    except Exception as e:
        print(f"\n  下载失败：{e}")
        return False


def ensure_dataset_native() -> Path | None:
    """确保本地有解压好的 speechocean762（原始 tar 格式）。返回数据集根目录，失败返回 None。"""
    root = find_dataset_root()
    if root:
        print(f"使用本地已有数据集：{root}")
        return root

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    tar_path = DATASET_DIR / "speechocean762.tar.gz"
    if not tar_path.exists():
        for name, url in DOWNLOAD_SOURCES:
            print(f"尝试从 {name} 下载（约 0.5GB）：{url}")
            if _download(url, tar_path):
                break
        else:
            return None
    print("解压中…")
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(DATASET_DIR)
    return find_dataset_root()


def collect_utterances_native(root: Path, split: str) -> list[dict]:
    """从原始 tar 格式（WAVE/ + train|test/wav.scp + resource/scores.json）收集样本。"""
    with open(root / "resource" / "scores.json", "r", encoding="utf-8") as f:
        scores = json.load(f)
    splits = ["train", "test"] if split == "all" else [split]
    recs = []
    for sp in splits:
        scp = root / sp / "wav.scp"
        u2s = root / sp / "utt2spk"
        if not scp.exists():
            continue
        utt2spk = {}
        if u2s.exists():
            for line in u2s.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    utt2spk[parts[0]] = parts[1]
        for line in scp.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            utt, rel = parts[0], parts[1]
            meta = scores.get(utt)
            if not meta:
                continue
            wav = root / rel
            if not wav.exists():
                continue
            recs.append({
                "utt": utt,
                "spk": utt2spk.get(utt, utt[:5]),
                "text": meta.get("text", ""),
                "total": float(meta.get("total", 0)),
                "words": meta.get("words", []),
                "wav_path": str(wav),
                "audio": None,
            })
    return recs


def collect_utterances_hf(split: str) -> list[dict]:
    """HuggingFace 兜底：mispeech/speechocean762。返回与 native 同构的记录列表。"""
    from datasets import load_dataset  # 需要 pip install datasets
    splits = ["train", "test"] if split == "all" else [split]
    recs = []
    for sp in splits:
        ds = load_dataset("mispeech/speechocean762", split=sp)
        for i, ex in enumerate(ds):
            recs.append({
                "utt": str(ex.get("utt_id") or ex.get("id") or f"{sp}_{i}"),
                "spk": str(ex.get("speaker") or ex.get("spk") or "unk"),
                "text": ex.get("text") or "",
                "total": float(ex.get("total", 0)),
                "words": ex.get("words") or [],
                "wav_path": None,
                "audio": ex.get("audio"),
            })
    return recs


# ---------------------------------------------------------------------------
# 抽样
# ---------------------------------------------------------------------------

def _band(total: float) -> int:
    """句子总分(0-10)分段：差/中/良/优，保证各分数段都有样本。"""
    if total <= 4:
        return 0
    if total <= 6:
        return 1
    if total <= 8:
        return 2
    return 3


def stratified_sample(recs: list[dict], max_utts: int, seed: int) -> list[dict]:
    """按 (speaker, 分数段) 分层、组内乱序、轮转抽取，直到 max_utts。"""
    if max_utts <= 0 or len(recs) <= max_utts:
        return list(recs)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in recs:
        groups[(r["spk"], _band(r["total"]))].append(r)
    rng = random.Random(seed)
    for g in groups.values():
        rng.shuffle(g)
    keys = sorted(groups.keys(), key=lambda k: (str(k[0]), k[1]))
    out: list[dict] = []
    i = 0
    while len(out) < max_utts:
        progressed = False
        for k in keys:
            if i < len(groups[k]):
                out.append(groups[k][i])
                progressed = True
                if len(out) >= max_utts:
                    break
        if not progressed:
            break
        i += 1
    return out


# ---------------------------------------------------------------------------
# 专家音素 ←→ 我们的音素 对齐
# ---------------------------------------------------------------------------

def _iter_words(words):
    """兼容两种形态：list[dict]（原生 scores.json / 部分 HF 版本）与
    columnar dict（HF datasets 对嵌套 Sequence 的列式返回，如 {'phones': [[...]], ...}）。"""
    if isinstance(words, dict):
        cols = words.get("phones") or words.get("text") or []
        keys = list(words.keys())
        for i in range(len(cols)):
            yield {k: (words[k][i] if isinstance(words[k], (list, tuple)) and i < len(words[k]) else None)
                   for k in keys}
    else:
        for w in words or []:
            yield w


def expert_phone_seq(words: list, arpabet_to_ipa: dict) -> list[tuple[str, float]]:
    """把一条样本的专家标注展平成 [(模型IPA, 专家分0~2), ...]，按句中顺序。"""
    seq = []
    for w in _iter_words(words):
        phones = w.get("phones") or []
        accs = w.get("phones-accuracy") or w.get("phones_accuracy") or []
        for ph, acc in zip(phones, accs):
            ph = str(ph).strip().upper()
            # 去重音数字与可能的 '*'（标注里的特殊记号）
            ph = ph.rstrip("*")
            if ph and ph[-1].isdigit():
                ph = ph[:-1]
            ipa = arpabet_to_ipa.get(ph)
            if ipa is None:
                continue
            try:
                seq.append((ipa, float(acc)))
            except (TypeError, ValueError):
                continue
    return seq


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------

def roc_auc(good: np.ndarray, bad: np.ndarray) -> float:
    """GOP 区分发对/发错的 AUC（Mann-Whitney U / 平均秩法，含并列处理）。"""
    from scipy.stats import rankdata
    x = np.concatenate([good, bad])
    ranks = rankdata(x)
    r_good = ranks[: len(good)].sum()
    n1, n2 = len(good), len(bad)
    u = r_good - n1 * (n1 + 1) / 2.0
    return float(u / (n1 * n2))


def monotone_anchors(pairs: list[list[float]]) -> list[list[float]]:
    """按 gop 降序排序并保证严格递减（重合点向下挪 0.05），避免插值除零。"""
    pairs = sorted(pairs, key=lambda x: -x[0])
    out = []
    for g, s in pairs:
        if out and g >= out[-1][0]:
            g = out[-1][0] - 0.05
        out.append([round(g, 3), s])
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="在 speechocean762 上标定 GOP 阈值")
    ap.add_argument("--max-utts", type=int, default=1200,
                    help="最多用多少条样本（分层抽样；<=0 表示全量）")
    ap.add_argument("--split", choices=["train", "test", "all"], default="train",
                    help="用哪个切分标定（默认 train，留 test 做日后评测）")
    ap.add_argument("--time-budget-min", type=float, default=38.0,
                    help="推理时间预算（分钟），超出即提前收口并如实写进报告")
    ap.add_argument("--good-th", type=float, default=2.0, help="专家分>=此值算'发对'")
    ap.add_argument("--bad-th", type=float, default=1.0, help="专家分<=此值算'发错'（中间分不参与定阈值）")
    ap.add_argument("--seed", type=int, default=762, help="抽样随机种子")
    ap.add_argument("--date", type=str, default=_dt.date.today().isoformat(),
                    help="标定日期，写进 thresholds.json")
    args = ap.parse_args()

    # 设备：GPU 可用则自动用（可被 AISPEAK_DEVICE 显式覆盖）。必须在 import config 之前设。
    if "AISPEAK_DEVICE" not in os.environ:
        try:
            import torch
            os.environ["AISPEAK_DEVICE"] = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            os.environ["AISPEAK_DEVICE"] = "cpu"
    device = os.environ["AISPEAK_DEVICE"]
    print(f"推理设备：{device}")

    import config                                  # noqa: E402
    from g2p import ARPABET_TO_IPA                 # noqa: E402
    from pronunciation import get_scorer, _align_ops  # noqa: E402

    # ---- 1) 拿数据 ----
    source = None
    recs: list[dict] = []
    root = ensure_dataset_native()
    if root:
        source = f"openslr tar（{root}）"
        recs = collect_utterances_native(root, args.split)
    else:
        print("tar 源均失败，回退 HuggingFace datasets（mispeech/speechocean762）…")
        try:
            recs = collect_utterances_hf(args.split)
            source = "HuggingFace mispeech/speechocean762"
        except Exception as e:
            print(f"HuggingFace 也失败：{e}")
            print("三个下载源全部失败，无法标定。请检查网络后重试，或手动把数据解压到 data/speechocean762/ 。")
            sys.exit(2)
    print(f"数据来源：{source}；切分={args.split}；可用样本 {len(recs)} 条")

    sampled = stratified_sample(recs, args.max_utts, args.seed)
    sampling_note = (f"按 (speaker × 句总分段[0-4/5-6/7-8/9-10]) 分层轮转抽样 {len(sampled)}/{len(recs)} 条"
                     f"（seed={args.seed}）" if len(sampled) < len(recs)
                     else f"全量 {len(recs)} 条")
    print(f"抽样：{sampling_note}")

    # ---- 2) 跑评分器，收集 (音素, GOP, 专家分) ----
    print("加载评分器（首次会下载模型）…")
    scorer = get_scorer()
    triples: list[tuple[str, float, float]] = []   # (ipa, gop, expert_acc)
    t0 = time.time()
    n_done = n_fail = 0
    budget_sec = args.time_budget_min * 60.0
    timed_out = False
    for k, rec in enumerate(sampled):
        if time.time() - t0 > budget_sec:
            timed_out = True
            print(f"\n达到时间预算 {args.time_budget_min} 分钟，提前收口（已完成 {n_done} 条）。")
            break
        text = (rec["text"] or "").strip()
        expert = expert_phone_seq(rec["words"], ARPABET_TO_IPA)
        if not text or len(expert) < 2:
            continue
        wav_path = rec["wav_path"]
        if wav_path is None:                        # HF 内存音频 → 临时 wav
            audio = rec["audio"]
            if not audio:
                continue
            wav_path = _dump_tmp_wav(audio["array"], audio["sampling_rate"])
        try:
            result = scorer.score(wav_path, text, with_prosody=False)
        except Exception:
            n_fail += 1
            continue
        if not result.ok or not result.phones:
            n_fail += 1
            continue
        # 编辑距离对齐两串 IPA，只取 match 位置（避免旧版"截断到 min 长度"的错位）
        ours = [(p.ipa, float(p.gop)) for p in result.phones]
        ops = _align_ops([e[0] for e in expert], [o[0] for o in ours])
        for op, ri, hi in ops:
            if op == "match":
                triples.append((expert[ri][0], ours[hi][1], expert[ri][1]))
        n_done += 1
        if n_done % 25 == 0:
            el = time.time() - t0
            print(f"  {n_done}/{len(sampled)} 句，{len(triples)} 个对齐音素，"
                  f"{el / 60:.1f} 分钟（{el / max(n_done, 1):.1f}s/句）", flush=True)
    elapsed_min = (time.time() - t0) / 60.0

    gops = np.array([t[1] for t in triples], dtype=np.float64)
    accs = np.array([t[2] for t in triples], dtype=np.float64)
    good = gops[accs >= args.good_th]
    bad = gops[accs <= args.bad_th]
    n_mid = int(len(gops) - len(good) - len(bad))
    print(f"\n完成 {n_done} 句（失败 {n_fail}），对齐音素 {len(gops)} 个："
          f"发对 {len(good)} / 发错 {len(bad)} / 中间分 {n_mid}；耗时 {elapsed_min:.1f} 分钟")
    if len(good) < 200 or len(bad) < 50:
        print("样本不足，无法可靠标定。请增大 --max-utts 或放宽 --time-budget-min。")
        sys.exit(3)

    # ---- 3) 统计与阈值 ----
    from scipy.stats import pearsonr, spearmanr
    auc = roc_auc(good, bad)
    pear = float(pearsonr(gops, accs)[0])
    spear = float(spearmanr(gops, accs)[0])

    green_gop = float(np.percentile(good, 25))
    bad_gop = float(np.percentile(bad, 75))
    if bad_gop >= green_gop:                       # 极端情况下兜底：用两组中位数
        green_gop = float(np.median(good))
        bad_gop = min(float(np.median(bad)), green_gop - 0.2)
    mid_gop = (green_gop + bad_gop) / 2.0
    floor_gop = float(min(np.percentile(bad, 5), -5.0))
    anchors = monotone_anchors([
        [0.0, 100],
        [green_gop, 85],
        [mid_gop, 60],
        [bad_gop, 40],
        [float(np.percentile(bad, 25)), 10],
        [floor_gop, 0],
    ])

    # ---- 4) 备份旧阈值并写回 ----
    old = None
    if Path(config.THRESHOLDS_PATH).exists():
        with open(config.THRESHOLDS_PATH, "r", encoding="utf-8") as f:
            old = json.load(f)
        if not LEGACY_PATH.exists():
            shutil.copy2(config.THRESHOLDS_PATH, LEGACY_PATH)
            print(f"旧阈值已备份：{LEGACY_PATH}")

    out = {
        "_说明": "由 scripts/calibrate_thresholds.py 在 speechocean762 上标定生成。详见 data/calibration_report.md。",
        "calibrated": True,
        "calibrated_on": args.date,
        "dataset": "speechocean762 (OpenSLR-101, CC BY 4.0)",
        "split": args.split,
        "device": device,
        "sampling": sampling_note + ("（达时间预算提前收口）" if timed_out else ""),
        "n_utts": n_done,
        "n_phones": int(len(gops)),
        "n_good": int(len(good)),
        "n_bad": int(len(bad)),
        "auc_good_vs_bad": round(auc, 4),
        "pearson_gop_vs_expert": round(pear, 4),
        "gop_to_score_anchors": anchors,
        "level_cutoffs": {"green": 80, "yellow": 55},
    }
    with open(config.THRESHOLDS_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"新阈值已写回：{config.THRESHOLDS_PATH}")
    print(f"green_gop={green_gop:.3f}  bad_gop={bad_gop:.3f}  AUC={auc:.3f}  Pearson={pear:.3f}")

    # ---- 5) 标定报告 ----
    _write_report(args, source, sampling_note, timed_out, device, elapsed_min,
                  n_done, n_fail, triples, good, bad, n_mid,
                  auc, pear, spear, anchors, old)
    print(f"标定报告：{REPORT_PATH}")
    print("\n下一步回归验证：")
    print("  python scripts/_run_score_test.py   # good/accented/wrong 排序应不变")
    print("  python tests/test_gop_math.py")


def _write_report(args, source, sampling_note, timed_out, device, elapsed_min,
                  n_done, n_fail, triples, good, bad, n_mid,
                  auc, pear, spear, anchors, old):
    def pct(a, q):
        return f"{np.percentile(a, q):.3f}"

    lines = []
    lines.append("# GOP 阈值标定报告（speechocean762）")
    lines.append("")
    lines.append(f"- 标定日期：{args.date}")
    lines.append(f"- 数据来源：{source}；切分：{args.split}")
    lines.append(f"- 抽样方式：{sampling_note}" + ("；**达时间预算提前收口**" if timed_out else ""))
    lines.append(f"- 设备：{device}；推理耗时：{elapsed_min:.1f} 分钟；"
                 f"成功 {n_done} 句（失败/跳过 {n_fail}）")
    lines.append(f"- 对齐音素总数：{len(triples)}（专家序列与本评分器序列做编辑距离对齐，只取 match 位）")
    lines.append("")
    lines.append("## 方法")
    lines.append("")
    lines.append(f"专家音素分（0~2）按 >= {args.good_th} 记「发对」、<= {args.bad_th} 记「发错」"
                 f"（中间分 {n_mid} 个不参与定阈值，但参与相关性计算）。")
    lines.append("green 阈值 = 发对组 GOP 的 P25；bad 阈值 = 发错组 GOP 的 P75；")
    lines.append("分数锚点用分段线性插值（见 thresholds.json 的 gop_to_score_anchors）。")
    lines.append("")
    lines.append("## 总体分布与区分度")
    lines.append("")
    lines.append("| 组 | n | mean | P25 | median | P75 |")
    lines.append("|---|---|---|---|---|---|")
    lines.append(f"| 发对(>= {args.good_th}) | {len(good)} | {good.mean():.3f} | "
                 f"{pct(good, 25)} | {pct(good, 50)} | {pct(good, 75)} |")
    lines.append(f"| 发错(<= {args.bad_th}) | {len(bad)} | {bad.mean():.3f} | "
                 f"{pct(bad, 25)} | {pct(bad, 50)} | {pct(bad, 75)} |")
    lines.append("")
    lines.append(f"- **AUC（GOP 区分发对/发错）= {auc:.4f}**（0.5=无区分，1=完美）")
    lines.append(f"- Pearson(GOP, 专家分) = {pear:.4f}；Spearman = {spear:.4f}")
    lines.append("")
    lines.append("## 新旧阈值对比")
    lines.append("")
    lines.append("| | 锚点 [gop → score] |")
    lines.append("|---|---|")
    if old:
        lines.append("| 旧（文献经验值） | " +
                     ", ".join(f"{g}→{s}" for g, s in old.get("gop_to_score_anchors", [])) + " |")
    lines.append("| 新（本次标定） | " + ", ".join(f"{g}→{s}" for g, s in anchors) + " |")
    lines.append("")
    lines.append("## 各音素 GOP 分布（n>=30）")
    lines.append("")
    lines.append("| 音素 | n | GOP mean | P25 | median | P75 | 专家分 mean | 发对均值-发错均值 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    by_phone: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for ipa, g, a in triples:
        by_phone[ipa].append((g, a))
    for ipa in sorted(by_phone, key=lambda k: -len(by_phone[k])):
        rows = by_phone[ipa]
        if len(rows) < 30:
            continue
        gs = np.array([r[0] for r in rows])
        as_ = np.array([r[1] for r in rows])
        gg = gs[as_ >= args.good_th]
        bb = gs[as_ <= args.bad_th]
        sep = f"{gg.mean() - bb.mean():.3f}" if len(gg) >= 5 and len(bb) >= 5 else "-"
        lines.append(f"| /{ipa}/ | {len(rows)} | {gs.mean():.3f} | {pct(gs, 25)} | "
                     f"{pct(gs, 50)} | {pct(gs, 75)} | {as_.mean():.2f} | {sep} |")
    lines.append("")
    lines.append("## 局限")
    lines.append("")
    lines.append("- speechocean762 为中国学习者朗读语料，阈值对其它母语背景/录音条件仍可能偏移；")
    lines.append("- 专家音素集与本评分器音素集经 ARPABET→IPA 折叠对齐，个别折叠（AO→ɑ、ZH→ʃ）会损失少量区分；")
    lines.append("- 全局单一阈值曲线（未按音素分别定阈值），逐音素阈值是后续工作。")
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def _dump_tmp_wav(array, sr) -> str:
    import soundfile as sf
    tmp = ROOT / "scripts" / "_calib_tmp.wav"
    sf.write(str(tmp), np.asarray(array, dtype=np.float32), sr)
    return str(tmp)


if __name__ == "__main__":
    main()
