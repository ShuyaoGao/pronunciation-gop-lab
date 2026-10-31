"""
音频读取与预处理：统一成 16kHz、单声道、做音量归一化。

为什么要预处理（见调研文档「难点 4」）：手机/笔记本麦克风、采样率不一、音量忽大忽小，
都会拉低声学模型的后验概率，造成「明明读对了却被判错」。统一格式 + 峰值归一化能缓解。
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

from config import TARGET_SR


def load_audio_16k_mono(path: str) -> Tuple[np.ndarray, int]:
    """
    读取任意常见音频文件，返回 (float32 单声道波形, 采样率=16000)。
    优先用 soundfile（读 WAV/FLAC 不依赖 ffmpeg）；用 librosa 完成重采样与单声道混音。
    """
    import librosa
    # librosa.load 会：读文件 -> 转单声道(mono=True) -> 重采样到 sr。
    # 它内部对 WAV 走 soundfile，无需 ffmpeg；其它格式才可能需要 audioread/ffmpeg。
    wav, sr = librosa.load(path, sr=TARGET_SR, mono=True)
    wav = np.asarray(wav, dtype=np.float32)
    wav = _peak_normalize(wav)
    return wav, sr


def _peak_normalize(wav: np.ndarray, target_peak: float = 0.95) -> np.ndarray:
    """峰值归一化：把最大振幅缩放到 target_peak，避免录音过轻/过响影响后验。"""
    peak = float(np.max(np.abs(wav))) if wav.size else 0.0
    if peak < 1e-6:
        return wav  # 静音/空，原样返回
    return wav * (target_peak / peak)


def duration_seconds(wav: np.ndarray, sr: int = TARGET_SR) -> float:
    return len(wav) / float(sr) if sr else 0.0
