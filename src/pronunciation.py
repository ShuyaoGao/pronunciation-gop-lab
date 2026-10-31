"""
发音评分核心（本项目技术核心 = 直接回答老师「把 ASR 逆用能否做发音评分」的问题）。

流程（GOP = Goodness of Pronunciation）：
  1. 录音 -> 16k 单声道波形；
  2. 喂给「音素级 wav2vec2 CTC 声学模型」，拿到逐帧、对每个音素的后验概率 log P(phone | frame)；
     —— 注意：这一步用的就是普通 ASR 的声学模型，我们【正用】它的中间产物（每帧音素后验），并非什么黑科技；
  3. 用 G2P 得到参考句「应该读」的音素序列；
  4. 用自研 CTC 强制对齐（ctc_align.py，不依赖已弃用的 torchaudio forced_align）把音频对齐到这串音素；
  5. 对每个音素，在它对齐到的那几帧上算 GOP = mean( log P(应读音素) - log P(该帧最可能音素) )，<=0，越接近0越标准；
  6. 用 thresholds.json 把 GOP 换成 0~100 分与「绿/黄/红」等级，并记录「模型其实把它听成了哪个音」。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

# 允许以脚本或包两种方式导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from audio_utils import load_audio_16k_mono
from ctc_align import ctc_forced_align
from g2p import G2P, CanonPhone
from thresholds import Thresholds, load_thresholds


@dataclass
class PhoneScore:
    ipa: str            # 应读音素（IPA）
    arpabet: str        # 应读音素（ARPABET），用于纠错提示查表
    word: str           # 所属单词
    word_index: int     # 单词序号（分组用）
    gop: float          # 原始 GOP（<=0）
    score: float        # 0~100
    level: str          # good / warn / bad -> 绿/黄/红
    perceived_ipa: str  # 模型在这几帧上最可能听成的音（用于「被听成了 X」提示）
    start_sec: float
    end_sec: float
    stress: int = -1    # 重音标记（来自 G2P：1主/2次/0非重读/-1辅音）
    is_vowel: bool = False
    hint: str = ""      # 纠错文字提示（由 feedback.py 填充）


@dataclass
class WordScore:
    word: str
    score: float
    phones: List[PhoneScore]


@dataclass
class CompletenessResult:
    score: float = 100.0            # 0~100：读全了多少
    n_canonical: int = 0            # 应读音素数
    n_deletions: int = 0            # 漏读
    n_insertions: int = 0           # 多读/插入
    missing_words: List[str] = field(default_factory=list)  # 疑似漏读的词


@dataclass
class PronunciationResult:
    reference_text: str
    ok: bool                       # 是否成功评分
    overall_score: float           # 综合分（多维加权）
    accuracy_score: float = 0.0    # 音素准确度（GOP 均值）
    transcript_ipa: str = ""       # 模型对整段音频的贪心识别（IPA）——「系统其实听到的是…」
    phones: List[PhoneScore] = field(default_factory=list)
    words: List[WordScore] = field(default_factory=list)
    completeness: Optional[CompletenessResult] = None
    prosody: object = None         # ProsodyResult（来自 prosody.analyze）
    note: str = ""                 # 提示/警告（如阈值未标定、录音太短）


class PronunciationScorer:
    """加载一次模型，反复评分。请用 get_scorer() 获取单例。"""

    def __init__(self) -> None:
        import torch
        from transformers import AutoModelForCTC, AutoProcessor

        self._torch = torch
        self.processor = AutoProcessor.from_pretrained(config.MODEL_ID)
        self.model = AutoModelForCTC.from_pretrained(config.MODEL_ID)
        self.model.to(config.DEVICE)
        self.model.eval()

        self.vocab: Dict[str, int] = self.processor.tokenizer.get_vocab()
        self.id2tok: Dict[int, str] = {v: k for k, v in self.vocab.items()}
        self.blank_id: int = self.processor.tokenizer.pad_token_id

        # 「真实音素」id 集合：排除 |、空格、[UNK]/[PAD]/<s>/</s> 以及个别异常 token
        self.phone_ids: List[int] = [
            tid for tok, tid in self.vocab.items() if _is_real_phone(tok)
        ]

        self.g2p = G2P()
        self.thresholds: Thresholds = load_thresholds()

    # ---------- 对外主接口 ----------
    def score(self, audio_path: str, reference_text: str,
              with_prosody: bool = True) -> PronunciationResult:
        reference_text = (reference_text or "").strip()
        if not reference_text:
            return PronunciationResult(reference_text, False, 0.0, note="参考句为空。")

        wav, sr = load_audio_16k_mono(audio_path)
        if wav.size < sr * 0.2:  # 不足 0.2 秒
            return PronunciationResult(reference_text, False, 0.0,
                                       note="录音太短或为空，请重新录制。")

        log_probs = self._logprobs(wav)            # [T, V]
        T = log_probs.shape[0]
        frame_stride = (len(wav) / sr) / max(T, 1)  # 每帧时长（秒）

        # 参考句 -> canonical 音素
        canon: List[CanonPhone] = self.g2p(reference_text)
        canon = [c for c in canon if c.ipa in self.vocab]  # 只保留模型词表内的音素
        token_ids = [self.vocab[c.ipa] for c in canon]

        transcript_ipa = self._greedy_transcript(log_probs)

        if not token_ids:
            return PronunciationResult(reference_text, False, 0.0,
                                       transcript_ipa=transcript_ipa,
                                       note="无法把参考句转成有效音素。")

        segments = ctc_forced_align(log_probs, token_ids, self.blank_id)
        if not segments:
            return PronunciationResult(
                reference_text, False, 0.0, transcript_ipa=transcript_ipa,
                note="强制对齐失败（通常是录音太短，容纳不下这么多音素）。请把句子读完整。")

        phones: List[PhoneScore] = []
        for c, (idx, s, e) in zip(canon, segments):
            ps = self._phone_gop(log_probs, c, token_ids[idx], s, e, frame_stride)
            phones.append(ps)

        words = _group_by_word(phones)
        accuracy = float(np.mean([p.score for p in phones])) if phones else 0.0

        # 完整度：用「自由贪心解码」对比「应读音素」，编辑距离找漏读/多读
        completeness = self._completeness(canon, transcript_ipa)

        # 韵律 / 流利度（本地 librosa）
        prosody = None
        if with_prosody:
            try:
                import prosody as prosody_mod
                prosody = prosody_mod.analyze(wav, sr, phones, n_words=len(words))
            except Exception:
                prosody = None

        overall = self._combine_scores(accuracy, completeness, prosody)

        note = "" if self.thresholds.calibrated else \
            "⚠ 当前 GOP 阈值为未标定的临时默认值，分数仅供课堂演示参考（见 README「阈值标定」）。"

        return PronunciationResult(
            reference_text=reference_text,
            ok=True,
            overall_score=round(overall, 1),
            accuracy_score=round(accuracy, 1),
            transcript_ipa=transcript_ipa,
            phones=phones,
            words=words,
            completeness=completeness,
            prosody=prosody,
            note=note,
        )

    def _combine_scores(self, accuracy, completeness, prosody) -> float:
        """多维加权综合分：准确度为主，完整度/流利度/韵律为辅。"""
        parts = [(accuracy, 0.55)]
        if completeness is not None:
            parts.append((completeness.score, 0.15))
        if prosody is not None and prosody.ok:
            parts.append((prosody.fluency_score, 0.15))
            parts.append((prosody.prosody_score, 0.15))
        total_w = sum(w for _, w in parts)
        return sum(s * w for s, w in parts) / total_w if total_w else accuracy

    def _completeness(self, canon: List[CanonPhone], transcript_ipa: str) -> CompletenessResult:
        """
        强制对齐会"强行"把每个应读音素都对上，因此漏读/多读不会体现在 GOP 里。
        这里用模型的「自由识别结果」(贪心解码) 与「应读音素序列」做最小编辑距离对齐，
        统计漏读(deletion)/多读(insertion)，并据此给完整度分。
        """
        ref = [c.ipa for c in canon]
        hyp = [t for t in transcript_ipa.split(" ") if t]
        if not ref:
            return CompletenessResult()
        ops = _align_ops(ref, hyp)
        n_del = sum(1 for o in ops if o[0] == "del")
        n_ins = sum(1 for o in ops if o[0] == "ins")
        # 哪些"应读音素"被判漏读 -> 归到所属词，给出疑似漏读的词
        deleted_ref_idx = [o[1] for o in ops if o[0] == "del"]
        missing_words = []
        seen = set()
        for i in deleted_ref_idx:
            w = canon[i].word
            wi = canon[i].word_index
            if wi not in seen:
                seen.add(wi)
                missing_words.append(w)
        # 完整度分：漏读惩罚比多读重
        n = len(ref)
        penalty = (n_del * 1.0 + n_ins * 0.5) / n
        score = max(0.0, 100.0 * (1.0 - penalty))
        return CompletenessResult(
            score=round(score, 1), n_canonical=n,
            n_deletions=n_del, n_insertions=n_ins,
            missing_words=missing_words[:8],
        )

    # ---------- 内部 ----------
    def _logprobs(self, wav: np.ndarray) -> np.ndarray:
        torch = self._torch
        inputs = self.processor(wav, sampling_rate=config.TARGET_SR, return_tensors="pt")
        input_values = inputs.input_values.to(config.DEVICE)
        with torch.no_grad():
            logits = self.model(input_values).logits  # [1, T, V]
        log_probs = torch.log_softmax(logits, dim=-1)[0].cpu().numpy()
        return log_probs.astype(np.float64)

    def _phone_gop(self, log_probs, canon: CanonPhone, tid: int,
                   s: int, e: int, frame_stride: float) -> PhoneScore:
        seg = log_probs[s:e]                       # [n, V]
        canon_lp = seg[:, tid]                     # 应读音素逐帧 log 后验
        best_lp = seg.max(axis=1)                  # 各帧最可能音素的 log 后验
        gop = float(np.mean(canon_lp - best_lp))   # <= 0

        # 模型在这几帧上最可能听成的「真实音素」
        mean_over_frames = seg.mean(axis=0)
        perceived_id = max(self.phone_ids, key=lambda i: mean_over_frames[i])
        perceived_ipa = self.id2tok.get(perceived_id, "?")

        score = self.thresholds.gop_to_score(gop)
        level = self.thresholds.level(score)
        return PhoneScore(
            ipa=canon.ipa, arpabet=canon.arpabet, word=canon.word,
            word_index=canon.word_index,
            gop=round(gop, 3), score=round(score, 1), level=level,
            perceived_ipa=perceived_ipa,
            start_sec=round(s * frame_stride, 3),
            end_sec=round(e * frame_stride, 3),
            stress=canon.stress, is_vowel=canon.is_vowel,
        )

    def _greedy_transcript(self, log_probs: np.ndarray) -> str:
        """CTC 贪心解码：每帧取最可能 token，合并连续重复并去 blank，得到模型实际听到的音素串。"""
        ids = log_probs.argmax(axis=1).tolist()
        out: List[str] = []
        prev = None
        for i in ids:
            if i != prev and i != self.blank_id:
                tok = self.id2tok.get(i, "")
                if _is_real_phone(tok):
                    out.append(tok)
            prev = i
        return " ".join(out)


def _align_ops(ref: List[str], hyp: List[str]) -> List[tuple]:
    """
    最小编辑距离对齐 ref/hyp 两个音素序列，返回操作序列。
    每个操作是 (op, ref_idx, hyp_idx)：op ∈ {match, sub, del, ins}。
    del = ref 里有但 hyp 没读到（漏读）；ins = hyp 多出来的（多读）。
    """
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i
    for j in range(1, m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1,         # del
                           dp[i][j - 1] + 1,         # ins
                           dp[i - 1][j - 1] + cost)  # match/sub
    # 回溯
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1):
            ops.append(("match" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            ops.append(("del", i - 1, -1))
            i -= 1
        else:
            ops.append(("ins", -1, j - 1))
            j -= 1
    ops.reverse()
    return ops


def _is_real_phone(tok: str) -> bool:
    if not tok:
        return False
    if tok in ("|", " "):
        return False
    if tok[0] in "[<{":     # [UNK] [PAD] <s> </s> 以及异常的 {'content':...} token
        return False
    return True


def _group_by_word(phones: List[PhoneScore]) -> List[WordScore]:
    words: List[WordScore] = []
    last_index = None
    for p in phones:
        if not words or p.word_index != last_index:
            words.append(WordScore(word=p.word, score=0.0, phones=[]))
            last_index = p.word_index
        words[-1].phones.append(p)
    for w in words:
        w.score = round(float(np.mean([p.score for p in w.phones])), 1) if w.phones else 0.0
    return words


# ---------- 单例 ----------
_scorer: Optional[PronunciationScorer] = None


def get_scorer() -> PronunciationScorer:
    global _scorer
    if _scorer is None:
        _scorer = PronunciationScorer()
    return _scorer
