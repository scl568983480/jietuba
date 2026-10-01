# -*- coding: utf-8 -*-
"""全局热键「运行时注册结果」记录

``main_app.MainApp.update_hotkey()`` 每次重新注册全局热键时，把注册失败的
热键记在这里，设置界面的冲突检测据此区分两种 ❌：

- 本程序上次注册就失败（可能被其它程序长期占用）
- 本次探测时才失败

只保存最近一次注册尝试的结果，避免残留过期状态。
"""

from __future__ import annotations

from typing import Dict

from core.logger import log_debug
from core.hotkey_utils import normalize_hotkey

#: 规范化热键 → 失败说明
_registered_failures: Dict[str, str] = {}


def clear_registration_failures() -> None:
    """开始新一轮注册前清空记录。"""
    _registered_failures.clear()


def record_registration_failure(hotkey: str, reason: str = "") -> None:
    """记录一个注册失败的全局热键。"""
    norm = normalize_hotkey(hotkey)
    if not norm:
        return
    _registered_failures[norm] = reason or "register_failed"
    log_debug(f"记录热键注册失败: {norm}", "Hotkey")


def get_registered_failure(hotkey: str) -> str:
    """返回该热键上次注册失败的原因，未失败返回空字符串。"""
    return _registered_failures.get(normalize_hotkey(hotkey), "")


def get_registration_failures() -> Dict[str, str]:
    """返回一份当前失败记录的副本。"""
    return dict(_registered_failures)
