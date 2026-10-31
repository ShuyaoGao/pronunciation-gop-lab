"""
G2P（字素转音素）：把英文参考句转成「该读出来的音素序列」（canonical phoneme sequence）。

为什么需要它：GOP 评分的前提是「已知学习者*应该*读的音素串」，再去看录音里每个音素读得标不标准。
这个「应该读的音素串」就由 G2P 得到。

实现：用 g2p_en（基于 CMU 词典 + 神经网络回退，纯 pip、无需任何外部二进制），输出 ARPABET 音素；
再用一张标准的 ARPABET→IPA 折叠表，转成发音模型词表用的同一套 IPA 记号，从而能与模型输出对齐。

注：CMU 39 音素集做了若干标准合并（例：AO→ɑ、AH→ə、ZH→ʃ），与所选模型 vitouphy/...-timit-phoneme
的词表完全一致，因此这里的映射不是“拍脑袋”，而是跟模型同源。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

# ARPABET（去掉重音数字后的两字母音素）→ 模型词表里的 IPA 记号。
# 右侧 IPA 必须出现在所选模型的 vocab 里；不在的音素按 CMU-39 标准折叠到最近音。
ARPABET_TO_IPA = {
    "AA": "ɑ",  "AE": "æ",  "AH": "ə",  "AO": "ɑ",   # AO 折叠到 ɑ（模型词表无独立 ɔ）
    "AW": "aʊ", "AY": "aɪ", "B": "b",   "CH": "ʧ",
    "D": "d",   "DH": "ð",  "EH": "ɛ",  "ER": "ɝ",
    "EY": "eɪ", "F": "f",   "G": "g",   "HH": "h",
    "IH": "ɪ",  "IY": "i",  "JH": "ʤ",  "K": "k",
    "L": "l",   "M": "m",   "N": "n",   "NG": "ŋ",
    "OW": "oʊ", "OY": "ɔɪ", "P": "p",   "R": "ɹ",
    "S": "s",   "SH": "ʃ",  "T": "t",   "TH": "θ",
    "UH": "ʊ",  "UW": "u",  "V": "v",   "W": "w",
    "Y": "j",   "Z": "z",   "ZH": "ʃ",                # ZH 折叠到 ʃ（模型词表无 ʒ）
    "DX": "ɾ",                                         # 闪音（g2p_en 通常不输出，留作兼容）
}


@dataclass
class CanonPhone:
    """参考句里一个「应该读出来」的音素。"""
    ipa: str         # 模型词表用的 IPA 记号（评分/对齐用）
    arpabet: str     # 原始 ARPABET（无重音），用于纠错提示查表 / 调试
    word: str        # 它属于参考句里的哪个单词（用于按单词分组展示）
    word_index: int  # 单词在句中的序号（区分同名重复单词，分组用）
    stress: int = -1  # CMU 重音标记：1=主重音, 2=次重音, 0=非重读元音, -1=辅音(无重音概念)
    is_vowel: bool = False  # 是否元音（重音/韵律分析用）


# CMU 元音的 ARPABET（带重音数字的那些）
_VOWEL_ARPABET = {
    "AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY",
    "IH", "IY", "OW", "OY", "UH", "UW",
}


class G2P:
    """把英文文本转成 CanonPhone 列表。"""

    def __init__(self) -> None:
        # 新版 nltk(>=3.9) 把词性标注器资源改名为 averaged_perceptron_tagger_eng，
        # 而 g2p_en 自带的下载用的是旧名，会导致 LookupError。这里先确保所需资源存在（首次需联网）。
        _ensure_nltk_data()
        # 延迟导入：g2p_en 首次导入会下载 nltk 数据，放进构造函数避免影响模块加载。
        from g2p_en import G2p as _G2p
        self._g2p = _G2p()

    def __call__(self, text: str) -> List[CanonPhone]:
        return self.phonemize(text)

    def phonemize(self, text: str) -> List[CanonPhone]:
        """
        返回参考句的 canonical 音素序列。
        g2p_en 的输出是按整句给的、用空格分隔单词的 ARPABET 序列（含重音数字与标点），
        这里把它切回单词、去重音、转 IPA。
        """
        tokens = self._g2p(text)  # 例: ['AY1', ' ', 'HH', 'AE1', 'V', ...]

        result: List[CanonPhone] = []
        # 同时维护「当前单词」用于分组：用原始文本切词，按出现顺序对齐
        words = [w for w in _split_words(text)]
        word_idx = 0
        current_word = words[0] if words else ""
        word_has_phones = False   # 当前单词是否已经有音素

        for tok in tokens:
            if tok == " ":
                # 单词分隔：推进到下一个单词。
                # g2p_en 会把句中标点输出成 [' ', ',', ' ']（标点两侧各一个空格），
                # 所以只有当前单词已经有音素时才推进，否则 "raining, we" 会多推进一格、后面的词全部错位。
                if word_has_phones:
                    word_idx += 1
                    current_word = words[word_idx] if word_idx < len(words) else ""
                    word_has_phones = False
                continue
            if not tok or not tok[0].isalpha():
                # 标点等，跳过（不推进单词）
                continue
            word_has_phones = True
            arpa = _strip_stress(tok)
            ipa = ARPABET_TO_IPA.get(arpa)
            if ipa is None:
                # 未知音素：跳过并不致命（极少见）。调试时可在此打印。
                continue
            is_vowel = arpa in _VOWEL_ARPABET
            stress = _stress_of(tok) if is_vowel else -1
            result.append(CanonPhone(ipa=ipa, arpabet=arpa,
                                     word=current_word, word_index=word_idx,
                                     stress=stress, is_vowel=is_vowel))
        return result


def _ensure_nltk_data() -> None:
    """确保 g2p_en 依赖的 nltk 资源存在（兼容新版 nltk 的改名）。首次运行需联网，之后走本地缓存。"""
    import nltk

    needed = [
        ("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng"),
        ("taggers/averaged_perceptron_tagger", "averaged_perceptron_tagger"),
        ("corpora/cmudict", "cmudict"),
    ]
    for path, pkg in needed:
        try:
            nltk.data.find(path)
        except LookupError:
            try:
                nltk.download(pkg, quiet=True)
            except Exception:
                pass  # 离线且已缓存其一时不致命


def _strip_stress(arpabet_token: str) -> str:
    """去掉 ARPABET 末尾的重音数字，如 'AY1' -> 'AY'、'HH' -> 'HH'。"""
    if arpabet_token and arpabet_token[-1].isdigit():
        return arpabet_token[:-1]
    return arpabet_token


def _stress_of(arpabet_token: str) -> int:
    """取 ARPABET 元音末尾的重音数字（1/2/0）；没有则返回 0。"""
    if arpabet_token and arpabet_token[-1].isdigit():
        return int(arpabet_token[-1])
    return 0


def _split_words(text: str) -> List[str]:
    """把文本按空白切成单词（仅用于给音素分组显示）。"""
    return [w for w in text.strip().split() if w]
