"""
韵律 / 流利度分析（超音段特征）—— 补上「逐音素 GOP 覆盖不到」的维度（调研文档难点 3）。

全部本地用 librosa 计算，无需任何 key / 大模型：
  - 流利度：语速（音素/秒、词/分）、停顿次数与总时长；
  - 语调（intonation）：基频 F0 的起伏（半音标准差/音域）——L2 常见问题是"读得太平"；
  - 重音（stress）：主重音元音是否比非重读元音更"突出"（更长/更响/音更高）。

这些阈值同样是【临时经验值】，需用母语者语料标定（与 GOP 阈值同理，见 README）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np


@dataclass
class ProsodyResult:
    ok: bool = True
    fluency_score: float = 0.0
    prosody_score: float = 0.0          # 语调 + 重音 的综合
    intonation_score: float = 0.0
    stress_score: float = 0.0
    speaking_rate_pps: float = 0.0      # phones per second
    speaking_rate_wpm: float = 0.0      # words per minute（近似）
    n_pauses: int = 0
    total_pause_sec: float = 0.0
    pitch_mean_hz: float = 0.0
    pitch_range_st: float = 0.0         # 音域（半音）
    pitch_variation_st: float = 0.0     # 半音标准差
    tips: List[str] = field(default_factory=list)


# 经验区间（临时值，需标定）
_RATE_LOW, _RATE_OK_LO, _RATE_OK_HI, _RATE_HIGH = 6.0, 9.0, 16.0, 20.0  # 音素/秒
_PITCH_VAR_FLAT, _PITCH_VAR_GOOD = 1.2, 3.0   # 半音标准差：<1.2 偏平，>=3 起伏自然


def analyze(wav: np.ndarray, sr: int, phones, n_words: int) -> ProsodyResult:
    """
    phones: List[PhoneScore]（需含 start_sec/end_sec/stress/is_vowel）。
    """
    if wav is None or len(wav) == 0 or not phones:
        return ProsodyResult(ok=False)

    total_dur = len(wav) / float(sr)
    speech_dur = max(phones[-1].end_sec - phones[0].start_sec, 1e-3)

    res = ProsodyResult()

    # ---------- 流利度：语速 + 停顿 ----------
    res.speaking_rate_pps = len(phones) / speech_dur
    res.speaking_rate_wpm = (n_words / total_dur) * 60.0 if total_dur > 0 else 0.0
    res.n_pauses, res.total_pause_sec = _detect_pauses(wav, sr)
    res.fluency_score = _fluency_score(res, total_dur)

    # ---------- 语调：F0 起伏 ----------
    f0_hz = _pitch_contour(wav, sr)
    voiced = f0_hz[np.isfinite(f0_hz) & (f0_hz > 0)]
    if voiced.size >= 5:
        res.pitch_mean_hz = float(np.median(voiced))
        ref = res.pitch_mean_hz
        semitones = 12.0 * np.log2(voiced / ref)
        lo, hi = np.percentile(semitones, [5, 95])
        res.pitch_range_st = float(hi - lo)
        res.pitch_variation_st = float(np.std(semitones))
    res.intonation_score = _intonation_score(res)

    # ---------- 重音：主重音元音是否更突出 ----------
    res.stress_score = _stress_score(wav, sr, phones, res)

    res.prosody_score = round(0.5 * res.intonation_score + 0.5 * res.stress_score, 1)
    res.fluency_score = round(res.fluency_score, 1)
    res.intonation_score = round(res.intonation_score, 1)
    res.stress_score = round(res.stress_score, 1)
    return res


def _detect_pauses(wav, sr, sil_db=-40.0, min_pause=0.18):
    """用短时能量找语音内部的停顿（静音段）。返回 (停顿次数, 停顿总秒数)。"""
    import librosa
    frame = int(0.025 * sr)
    hop = int(0.010 * sr)
    rms = librosa.feature.rms(y=wav, frame_length=frame, hop_length=hop)[0]
    if rms.size == 0:
        return 0, 0.0
    ref = np.max(rms) + 1e-9
    db = 20.0 * np.log10(rms / ref + 1e-9)
    silent = db < sil_db

    # 去掉首尾静音，只看"语音内部"的停顿
    voiced_idx = np.where(~silent)[0]
    if voiced_idx.size == 0:
        return 0, 0.0
    start, end = voiced_idx[0], voiced_idx[-1]
    inner = silent[start:end + 1]

    n_pauses, total = 0, 0.0
    run = 0
    for s in inner:
        if s:
            run += 1
        else:
            dur = run * hop / sr
            if dur >= min_pause:
                n_pauses += 1
                total += dur
            run = 0
    dur = run * hop / sr
    if dur >= min_pause:
        n_pauses += 1
        total += dur
    return n_pauses, round(total, 2)


def _pitch_contour(wav, sr):
    """用 librosa.pyin 估计基频 F0（Hz），unvoiced 处为 NaN。"""
    import librosa
    try:
        f0, _, _ = librosa.pyin(
            wav, fmin=65.0, fmax=400.0, sr=sr,
            frame_length=int(0.040 * sr), hop_length=int(0.010 * sr),
        )
        return f0
    except Exception:
        return np.array([np.nan])


def _fluency_score(res: ProsodyResult, total_dur: float) -> float:
    score = 100.0
    r = res.speaking_rate_pps
    # 语速过慢/过快都扣分
    if r < _RATE_LOW:
        score -= min(40, (_RATE_LOW - r) * 8)
        res.tips.append(f"语速偏慢（约 {r:.1f} 音素/秒），可尝试更连贯地朗读。")
    elif r < _RATE_OK_LO:
        score -= (_RATE_OK_LO - r) * 4
    elif r > _RATE_HIGH:
        score -= min(30, (r - _RATE_HIGH) * 5)
        res.tips.append(f"语速偏快（约 {r:.1f} 音素/秒），注意吐字清晰。")
    elif r > _RATE_OK_HI:
        score -= (r - _RATE_OK_HI) * 2
    # 停顿
    if res.n_pauses >= 2 and res.total_pause_sec > 0.6:
        score -= min(30, res.n_pauses * 6 + res.total_pause_sec * 8)
        res.tips.append(f"句中停顿较多（{res.n_pauses} 处、共 {res.total_pause_sec:.1f}s），可练习更流畅地连读。")
    return float(max(0.0, min(100.0, score)))


def _intonation_score(res: ProsodyResult) -> float:
    v = res.pitch_variation_st
    if v <= 0:
        return 60.0  # 没取到足够浊音，给中性分
    if v < _PITCH_VAR_FLAT:
        res.tips.append("语调偏平，像在念字。试着在句子里自然地起伏（重点词读高一点、句尾该升/降）。")
        return float(max(30.0, 60.0 * v / _PITCH_VAR_FLAT))
    if v >= _PITCH_VAR_GOOD:
        return 95.0
    # 介于之间线性
    return float(60.0 + 35.0 * (v - _PITCH_VAR_FLAT) / (_PITCH_VAR_GOOD - _PITCH_VAR_FLAT))


def _stress_score(wav, sr, phones, res: ProsodyResult) -> float:
    """主重音元音(stress==1)是否比非重读元音(stress==0)更长/更响/音更高。"""
    import librosa
    stressed_prom, unstressed_prom = [], []
    for p in phones:
        if not getattr(p, "is_vowel", False):
            continue
        a, b = int(p.start_sec * sr), int(p.end_sec * sr)
        seg = wav[a:b]
        if seg.size < int(0.01 * sr):
            continue
        dur = p.end_sec - p.start_sec
        energy = float(np.sqrt(np.mean(seg ** 2)) + 1e-9)
        prom = dur * energy  # 时长×响度，作为"突出度"的简易代理
        if p.stress == 1:
            stressed_prom.append(prom)
        elif p.stress == 0:
            unstressed_prom.append(prom)

    if not stressed_prom or not unstressed_prom:
        return 70.0  # 没有可对比的重读/非重读元音（如全是单音节），给中性分

    ratio = (np.mean(stressed_prom) + 1e-9) / (np.mean(unstressed_prom) + 1e-9)
    # ratio > 1 表示重读元音确实更突出
    if ratio >= 1.6:
        return 95.0
    if ratio <= 0.9:
        res.tips.append("重音不够明显：英语重读音节要读得更长、更响、音更高，弱读音节要轻短。")
        return float(max(30.0, 60.0 * ratio))
    return float(60.0 + 35.0 * (ratio - 0.9) / (1.6 - 0.9))
