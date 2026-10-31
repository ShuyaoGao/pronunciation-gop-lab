"""
TTS（文本转语音）—— 实现「听—说」闭环：
  - 朗读「目标参考句」让学习者先听母语示范、再模仿（听力/跟读）；
  - 把 AI 对话陪练的回复读出来，形成语音交互。

后端（按可用性自动选择，优先级从高到低）：
  1. Kokoro（本地神经 TTS，Apache-2.0，更自然）——若安装了 `kokoro` 才用；
  2. Windows SAPI（System.Speech）——Windows 自带，零依赖零 key，作保底。

为什么默认 SAPI：本机零配置即可发声，最可控；想要更自然的音色可装 Kokoro / 接 ElevenLabs（见 README）。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
SAPI_SCRIPT = ROOT / "scripts" / "sapi_tts.ps1"
CACHE_DIR = ROOT / "data" / "tts_cache"

_kokoro_pipeline = None
_kokoro_tried = False


def synthesize(text: str, voice_hint: str = "Zira") -> Optional[str]:
    """把文本合成成 WAV，返回文件路径；失败返回 None。带缓存（同文本不重复合成）。"""
    text = (text or "").strip()
    if not text:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5((text + "|" + voice_hint).encode("utf-8")).hexdigest()[:16]
    out_path = CACHE_DIR / f"{key}.wav"
    if out_path.exists() and out_path.stat().st_size > 1000:
        return str(out_path)

    # 1) 尝试 Kokoro
    p = _kokoro_synthesize(text, out_path)
    if p:
        return p
    # 2) 回退 SAPI
    return _sapi_synthesize(text, out_path, voice_hint)


def backend_name() -> str:
    """当前会用的 TTS 后端名（用于界面提示）。"""
    if _kokoro_available():
        return "Kokoro（本地神经 TTS）"
    if sys.platform.startswith("win"):
        return "Windows SAPI（系统自带）"
    return "（无可用 TTS 后端）"


# ---------- SAPI ----------
def _sapi_synthesize(text: str, out_path: Path, voice_hint: str) -> Optional[str]:
    if not sys.platform.startswith("win"):
        return None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
            tf.write(text)
            txt_file = tf.name
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(SAPI_SCRIPT),
               "-TextFile", txt_file, "-OutFile", str(out_path), "-VoiceMatch", voice_hint]
        # 注意：不要 text=True —— PowerShell 输出是 GBK，按 locale 解码会在读取线程里崩溃。
        # 用 bytes 捕获、不解码；是否成功以输出文件是否生成为准。
        subprocess.run(cmd, capture_output=True, text=False, timeout=60)
        Path(txt_file).unlink(missing_ok=True)
        if out_path.exists() and out_path.stat().st_size > 1000:
            return str(out_path)
        return None
    except Exception:
        return None


# ---------- Kokoro（可选）----------
def _kokoro_available() -> bool:
    global _kokoro_tried
    try:
        import importlib.util
        return importlib.util.find_spec("kokoro") is not None
    except Exception:
        return False


def _kokoro_synthesize(text: str, out_path: Path) -> Optional[str]:
    global _kokoro_pipeline, _kokoro_tried
    if not _kokoro_available():
        return None
    try:
        import soundfile as sf
        if _kokoro_pipeline is None:
            from kokoro import KPipeline
            _kokoro_pipeline = KPipeline(lang_code="a")  # 'a' = American English
        chunks = []
        for _, _, audio in _kokoro_pipeline(text, voice="af_heart"):
            chunks.append(audio)
        if not chunks:
            return None
        import numpy as np
        wav = np.concatenate(chunks)
        sf.write(str(out_path), wav, 24000)
        return str(out_path) if out_path.exists() else None
    except Exception:
        return None
