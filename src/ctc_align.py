"""
CTC 强制对齐（自研，numpy Viterbi）。

作用：已知录音的逐帧音素后验 log_probs[T, V]，以及参考句「应该读」的音素 id 序列 tokens[N]，
找出每个音素对应到音频的哪些帧（start, end）。有了帧区间，才能逐音素算 GOP。

为什么自己写而不用 torchaudio.functional.forced_align？
  方案文档明确要求：torchaudio 的 forced_align / MMS_FA 已在 2.8 弃用、2.9 移除，不能作为新项目对齐主力。
  这里直接用「模型自身的 CTC 后验」做经典 CTC 强制对齐 Viterbi（约几十行），透明、零额外依赖，
  原理与 WhisperX 内部一致——正是「正用 ASR 声学模型的中间产物 + 对齐」。

经典 CTC 对齐：在音素序列中间插入 blank，得到 ext = [blank, p0, blank, p1, ..., p_{N-1}, blank]，
在该扩展序列上做 Viterbi（log 空间取 max），转移规则遵循 CTC：可停留、前进一格、或在「两侧标签不同」时
跨过中间的 blank 前进两格。
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np

NEG_INF = -1e30


def ctc_forced_align(
    log_probs: np.ndarray,
    tokens: List[int],
    blank: int,
) -> List[Tuple[int, int, int]]:
    """
    参数：
        log_probs: [T, V] 逐帧 log-softmax 后验
        tokens:    长度 N 的音素 id 序列（不含 blank）
        blank:     CTC blank（= pad）token 的 id
    返回：
        [(token_index, start_frame, end_frame_exclusive), ...]，长度 N，与 tokens 一一对应。
        若 T < N（音频太短，无法容纳所有音素）则返回空列表。
    """
    T = int(log_probs.shape[0])
    N = len(tokens)
    if N == 0 or T == 0 or T < N:
        return []

    # 扩展序列：blank 与音素交替
    ext = [blank]
    for tk in tokens:
        ext.append(tk)
        ext.append(blank)
    S = len(ext)  # = 2N + 1

    dp = np.full((T, S), NEG_INF, dtype=np.float64)
    back = np.zeros((T, S), dtype=np.int32)  # 记录每个 (t,s) 的最优前驱状态

    # 初始化第 0 帧：只能从 ext[0]=blank 或 ext[1]=第一个音素 开始
    dp[0, 0] = log_probs[0, ext[0]]
    if S > 1:
        dp[0, 1] = log_probs[0, ext[1]]

    for t in range(1, T):
        for s in range(S):
            sym = ext[s]
            # 候选前驱：停留(s)、前进一格(s-1)
            best_prev = dp[t - 1, s]
            best_s = s
            if s - 1 >= 0 and dp[t - 1, s - 1] > best_prev:
                best_prev = dp[t - 1, s - 1]
                best_s = s - 1
            # 跨过中间 blank 前进两格：仅当当前是「非 blank 标签」且与 s-2 标签不同
            if s - 2 >= 0 and sym != blank and ext[s - 2] != sym:
                if dp[t - 1, s - 2] > best_prev:
                    best_prev = dp[t - 1, s - 2]
                    best_s = s - 2
            if best_prev <= NEG_INF:
                # 该状态在本帧不可达
                dp[t, s] = NEG_INF
                back[t, s] = best_s
                continue
            dp[t, s] = best_prev + log_probs[t, sym]
            back[t, s] = best_s

    # 终点：最后一帧落在最后一个 blank(S-1) 或最后一个音素(S-2)
    if S >= 2 and dp[T - 1, S - 2] > dp[T - 1, S - 1]:
        end_state = S - 2
    else:
        end_state = S - 1

    # 回溯，得到每帧所处状态
    path = np.zeros(T, dtype=np.int32)
    s = end_state
    for t in range(T - 1, -1, -1):
        path[t] = s
        s = back[t, s]

    # 把「状态序列」整理成每个音素的帧区间
    # 音素 i 对应扩展状态 s = 2*i + 1
    segments: List[Tuple[int, int, int]] = []
    for i in range(N):
        state = 2 * i + 1
        frames = np.where(path == state)[0]
        if len(frames) == 0:
            # 理论上强制对齐不会跳过标签；极端情况下兜底为「紧邻的一帧」
            # 用前一个音素的结尾或 0 兜底，保证返回长度 = N
            start = segments[-1][2] if segments else 0
            segments.append((i, start, start + 1))
        else:
            segments.append((i, int(frames[0]), int(frames[-1]) + 1))
    return segments
