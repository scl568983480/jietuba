# -*- coding: utf-8 -*-
"""
language_detection.py - 按源文语种决定翻译方向

规则：
* 全是中文            → 翻成英语（中文进、英文出）
* 其它任何情况        → 翻成中文
  （全是英文、中英混排、纯日语/韩语/俄语、两种非中文混排… 都属于这一类）
* 判不出语种（空文本、纯数字符号）→ 没有方向，由调用方决定

重要：这里只负责"这一次翻译往哪个方向翻"，**不碰**用户配置里的目标语言。
配置语言只在用户手动改语言时才变化；界面上显示的语言框也始终是配置语言。
"""

from __future__ import annotations

# 目标语言代码
CHINESE = "ZH"
ENGLISH = "EN"

# 同一个语种可能由多个 Unicode 区段构成，合并为同一个语种标识；
# LATIN 覆盖英/德/法/西…，OTHER 覆盖其它字母文字，都算作独立的语种。
_HAN = "HAN"
_JAPANESE = "JAPANESE"


def _script_of(ch: str) -> str | None:
    """把单个字符归类到语种标识；非字母、无文字系统的符号返回 None。"""
    code = ord(ch)
    if 0x3040 <= code <= 0x30FF:
        # 平假名 / 片假名：日语专有
        return _JAPANESE
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
        # 汉字：中文与日文共用，单独出现按中文处理
        return _HAN
    if 0xF900 <= code <= 0xFAFF or 0x20000 <= code <= 0x2FA1F:
        return _HAN
    if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF:
        return "KO"
    if 0x0400 <= code <= 0x04FF:
        return "RU"
    if 0x0370 <= code <= 0x03FF:
        return "EL"
    if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F:
        return "AR"
    if 0x0590 <= code <= 0x05FF:
        return "HE"
    if 0x0900 <= code <= 0x097F:
        return "HI"
    if 0x0E00 <= code <= 0x0E7F:
        return "TH"
    if ch.isascii() and ch.isalpha():
        # 拉丁字母：英/德/法/西/葡/越…共用同一脚本，无法再细分，
        # 统一视为"拉丁语种"，对"是否混排"的判断已经足够。
        return "LATIN"
    if ch.isalpha():
        # 其它有文字系统的语言（格鲁吉亚语、亚美尼亚语等）不细分成具体语言码，
        # 只归为"其它字母文字"，避免产出厂商不认识的语言代码。
        return "OTHER"
    return None


def detect_scripts(text: str) -> set[str]:
    """返回文本里出现过的语种标识集合（去重）。"""
    if not text:
        return set()
    scripts = set()
    for ch in text:
        script = _script_of(ch)
        if script is not None:
            scripts.add(script)
    return scripts


def count_distinct_languages(text: str) -> int:
    """文本里出现的语种数量（纯标点/数字视为 0）。"""
    return len(detect_scripts(text))


def is_all_chinese(text: str) -> bool:
    """文本是否只含中文（可含数字、标点、空白）。"""
    scripts = detect_scripts(text)
    return bool(scripts) and scripts <= {_HAN}


def is_multi_language(text: str) -> bool:
    """文本是否为多语种混排（如中英混排、日中混排）。"""
    return count_distinct_languages(text) > 1


def translation_direction(text: str) -> str | None:
    """按源文内容给出翻译方向（目标语言代码）；判不出语种时返回 None。

    * 全是中文 → ``EN``（中文进、英文出）
    * 其它（全是英文 / 混排 / 单一非中文语种）→ ``ZH``
    * 空文本或纯数字符号 → ``None``，由调用方回退到配置语言
    """
    scripts = detect_scripts(text)
    if not scripts:
        return None
    if scripts <= {_HAN}:
        return ENGLISH
    return CHINESE


def describe_languages(text: str) -> str:
    """给日志用的简短描述：语种数量，并标注是否为纯中文。"""
    count = count_distinct_languages(text)
    if count == 0:
        return "无语种(数字/符号)"
    return f"{count} 个语种" + ("（全中文）" if is_all_chinese(text) else "")
