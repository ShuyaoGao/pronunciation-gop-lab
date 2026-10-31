"""
可选：调 Azure 发音评测（Pronunciation Assessment）做「自研 GOP vs 商用 API」并排对照。

教学价值：让学生直观对比自研方案与工业界成熟 API 的分数差异（调研文档 MVP 第 3 步）。
默认关闭：仅当配置了 AZURE_SPEECH_KEY 与 AZURE_SPEECH_REGION 才启用；且会把录音发往 Azure 云端，
界面需提示用户知情同意（隐私约束）。

注意：azure-cognitiveservices-speech 不在默认依赖里（保持「零 key 也能跑」）。
要用先 `pip install azure-cognitiveservices-speech` 并配置 key/region。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import config


@dataclass
class AzurePhone:
    phone: str
    score: float


@dataclass
class AzureResult:
    ok: bool
    message: str
    accuracy: Optional[float] = None
    fluency: Optional[float] = None
    completeness: Optional[float] = None
    pron: Optional[float] = None
    phones: List[AzurePhone] = None


def assess(audio_path_wav16k: str, reference_text: str) -> AzureResult:
    """对 16k 单声道 WAV 调 Azure 发音评测。未配置则返回提示。"""
    if not config.azure_enabled():
        return AzureResult(False, "未配置 Azure（AZURE_SPEECH_KEY / AZURE_SPEECH_REGION），已跳过商用对照。")

    try:
        import azure.cognitiveservices.speech as speechsdk
    except Exception:
        return AzureResult(False, "未安装 azure-cognitiveservices-speech，跳过。安装：pip install azure-cognitiveservices-speech")

    try:
        speech_config = speechsdk.SpeechConfig(
            subscription=config.AZURE_SPEECH_KEY, region=config.AZURE_SPEECH_REGION)
        audio_config = speechsdk.audio.AudioConfig(filename=audio_path_wav16k)
        pron_config = speechsdk.PronunciationAssessmentConfig(
            reference_text=reference_text,
            grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
            granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        )
        recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
        pron_config.apply_to(recognizer)
        result = recognizer.recognize_once()

        if result.reason != speechsdk.ResultReason.RecognizedSpeech:
            return AzureResult(False, f"Azure 未识别到语音（reason={result.reason}）。")

        pa = speechsdk.PronunciationAssessmentResult(result)
        phones: List[AzurePhone] = []
        try:
            for w in pa.words:
                for ph in (w.phonemes or []):
                    phones.append(AzurePhone(phone=ph.phoneme, score=ph.accuracy_score))
        except Exception:
            pass

        return AzureResult(
            True, "Azure 发音评测成功。",
            accuracy=pa.accuracy_score, fluency=pa.fluency_score,
            completeness=pa.completeness_score, pron=pa.pronunciation_score,
            phones=phones,
        )
    except Exception as e:
        return AzureResult(False, f"Azure 调用出错：{e}")
