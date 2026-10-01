# -*- coding: utf-8 -*-
"""全局热键冲突检测

集中处理「设置 → 快捷键 → 全局快捷键」六个输入框（截图 / 截图备用 /
翻译 / 翻译备用 / 剪贴板 / 剪贴板备用）的冲突判定，供：

- ``ui.hotkey_edit.HotkeyEdit``  输入过程中的实时 ✅ / ❌ 反馈
- ``ui.settings_ui.dialog``       点击「应用」保存前的整体校验与弹窗

使用同一份逻辑，保证「实时提示」与「保存提示」结论一致。

三类结果：
1. ``duplicate``    —— 与本次设置中的另一个热键重复（应用内部撞车）→ ❌
2. ``occupied``     —— 被其它程序或系统占用（注册失败）→ ❌
3. ``unsupported``  —— 该键无法注册为全局热键（如 ``pageup``）→ ❌
4. ``warning``      —— 命中常用快捷键清单，能用但可能同时触发其它程序 → ⚠️

判定顺序与实际生效顺序（``main_app.update_hotkey`` 的注册顺序）保持一致：
截图 → 截图备用 → 翻译 → 翻译备用 → 剪贴板 → 剪贴板备用。
重复时先出现的那个保留，后面的被判为冲突。

应用内快捷键（截图 / 钉图窗口内）**不走这里**：它们只在窗口内生效，不受
系统或其它软件影响，无需任何探测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence, Tuple

from core.logger import log_debug, log_exception
from core.hotkey_utils import is_common_shortcut, normalize_hotkey
from core.hotkey_registry import get_registered_failure

#: 冲突原因
REASON_DUPLICATE = "duplicate"
REASON_OCCUPIED = "occupied"
REASON_UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class HotkeyCheck:
    """单个热键输入框的检测结果"""

    key: str                 # 输入框标识（调用方自定义，如 "hotkey_input"）
    label: str               # 界面显示的设置项名称
    value: str               # 去除首尾空白后的原始输入
    available: bool          # 是否可用（✅）；带预警时仍为 True
    reason: str = ""         # 不可用原因：REASON_DUPLICATE / REASON_OCCUPIED
    detail: str = ""         # 人类可读的说明（用于 tooltip 与保存弹窗）
    warning: str = ""        # 预警说明（非冲突，仅提示）

    @property
    def is_blank(self) -> bool:
        return not self.value.strip()


def parse_hotkey(text: str) -> Optional[Tuple[int, int]]:
    """解析为 ``(modifiers, vk)``，失败返回 ``None``。"""
    try:
        from core.shortcut_manager import ShortcutManager

        return ShortcutManager._parse_hotkey(text)
    except Exception:
        return None


def _probe_registered(mods: int, vk: int) -> bool:
    """该组合是否已由当前进程注册（此时探测注册会失败，但属于「本程序持有」）。"""
    try:
        from core.shortcut_manager import ShortcutManager

        return (mods, vk) in ShortcutManager._registered_keys_global
    except Exception as e:
        log_exception(e, "热键冲突检测：读取已注册热键")
        return False


def is_hotkey_available(text: str) -> bool:
    """检测单个热键当前是否可注册。

    与 ``ShortcutManager.check_hotkey_availability`` 行为一致，但会先看
    「本进程已注册」集合，避免自己注册的键被误判为被占用。
    """
    parsed = parse_hotkey(text)
    if parsed is None:
        return False

    mods, vk = parsed
    if _probe_registered(mods, vk):
        return True

    try:
        from core.shortcut_manager import ShortcutManager

        return bool(ShortcutManager.instance().check_hotkey_availability(text))
    except Exception as e:
        log_exception(e, "热键冲突检测：可用性探测")
        return False


def _common_warning(value: str, tr) -> str:
    """命中常用快捷键清单时返回预警文案，否则返回空字符串。"""
    if not is_common_shortcut(value):
        return ""
    return tr(
        "This shortcut is widely used by other programs; it can be "
        "registered, but may be triggered together with that program."
    )


def validate_hotkey_fields(
    fields: Sequence[Tuple[str, str, str]],
    tr=None,
) -> dict:
    """整体检测一组全局热键输入框。

    应用内快捷键不走这里：它只在截图 / 钉图窗口内生效，不受系统或其它软件
    影响，不需要做任何冲突探测（同组重复由设置页的替换确认弹窗处理）。

    Args:
        fields: ``[(key, label, value), ...]``，顺序应为实际注册顺序。
        tr: 可选的翻译函数，用于生成说明文案。

    Returns:
        ``{key: HotkeyCheck}``
    """
    _t = tr if callable(tr) else (lambda text: text)

    results: dict = {}
    seen: dict = {}  # 规范化热键 → (key, label)

    for key, label, raw_value in fields:
        value = (raw_value or "").strip()
        norm = normalize_hotkey(value)

        # 空值：表示不使用该热键，不算冲突
        if not value:
            results[key] = HotkeyCheck(
                key=key, label=label, value=value, available=True
            )
            continue

        # 未完成的中间态（如 "ctrl+"）：交给输入框静默处理，不报冲突
        if norm.endswith("+"):
            results[key] = HotkeyCheck(
                key=key, label=label, value=value, available=True
            )
            continue

        warning = _common_warning(value, _t)

        if norm in seen:
            first_key, first_label = seen[norm]
            results[key] = HotkeyCheck(
                key=key,
                label=label,
                value=value,
                available=False,
                reason=REASON_DUPLICATE,
                detail=_t('Already used by "%1" in this settings.').replace(
                    "%1", first_label
                ),
            )
            continue

        seen[norm] = (key, label)

        parsed = parse_hotkey(value)
        if parsed is None:
            results[key] = HotkeyCheck(
                key=key,
                label=label,
                value=value,
                available=False,
                reason=REASON_UNSUPPORTED,
                detail=_t(
                    "This key cannot be registered as a global hotkey; "
                    "choose a letter, digit or function key with modifiers."
                ),
            )
            continue

        mods, vk = parsed
        if _probe_registered(mods, vk):
            results[key] = HotkeyCheck(
                key=key, label=label, value=value, available=True,
                warning=warning,
            )
            continue

        if is_hotkey_available(value):
            results[key] = HotkeyCheck(
                key=key, label=label, value=value, available=True,
                warning=warning,
            )
            continue

        # 区分「本程序上次注册就失败」与「本次被其它程序占用」，给出更准的提示
        if get_registered_failure(value):
            detail = _t("Registration failed; the key may be occupied by another program.")
        else:
            detail = _t("Already occupied by another program or the system.")
        results[key] = HotkeyCheck(
            key=key,
            label=label,
            value=value,
            available=False,
            reason=REASON_OCCUPIED,
            detail=detail,
        )

    return results


def log_conflicts(results: Mapping[str, HotkeyCheck]) -> None:
    """把冲突与预警写入日志，便于排查。"""
    conflicts = [c for c in results.values() if not c.available]
    if conflicts:
        summary = "; ".join(
            f"{c.label}={c.value}({c.reason})" for c in conflicts
        )
        log_debug(f"检测到快捷键冲突: {summary}", "Hotkey")

    warnings = [c for c in results.values() if c.available and c.warning]
    if warnings:
        summary = "; ".join(f"{c.label}={c.value}" for c in warnings)
        log_debug(f"快捷键预警（常用键）: {summary}", "Hotkey")


def iter_conflicts(results: Mapping[str, HotkeyCheck]) -> Iterable[HotkeyCheck]:
    """按输入顺序产出所有不可用的检测结果。"""
    return (c for c in results.values() if not c.available)
