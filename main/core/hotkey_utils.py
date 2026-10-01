# -*- coding: utf-8 -*-
"""热键字符串工具（无 Qt / 无业务依赖，可被任意模块安全导入）"""

from __future__ import annotations

#: 修饰键规范化后的固定顺序（小写）
MOD_ORDER = ("ctrl", "shift", "alt", "win")

#: 修饰键别名 → 规范名
MOD_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "win": "win",
    "meta": "win",
    "super": "win",
}


def normalize_hotkey(text: str) -> str:
    """把热键字符串规范化为可比较的形式（小写 + 修饰键固定顺序）。

    ``"Shift+Ctrl+A"`` / ``"ctrl+shift+a"`` / ``" shift + ctrl + a "``
    规范化为同一个 ``"ctrl+shift+a"``。

    仅有修饰键（未完成输入，如 ``"ctrl+"``）时返回 ``"ctrl+"``；
    完全为空时返回 ``""``。
    """
    if not text or not isinstance(text, str):
        return ""

    mods = set()
    primary = None
    for raw in text.strip().lower().split("+"):
        part = raw.strip()
        if not part:
            continue
        if part in MOD_ALIASES:
            mods.add(MOD_ALIASES[part])
        elif primary is None:
            primary = part
        else:
            # 多个主键（非正常输入）：保留原样，交由解析阶段判为非法
            primary = f"{primary}+{part}"

    ordered = [m for m in MOD_ORDER if m in mods]
    if primary is None:
        return ("+".join(ordered) + "+") if ordered else ""

    ordered.append(primary)
    return "+".join(ordered)


# ── 常用快捷键预警表 ─────────────────────────────────────
#
# Windows 只通过 RegisterHotKey 登记「全局热键」，浏览器 / 编辑器 / 资源管理器
# 里的 Ctrl+C、Ctrl+S 这类快捷键是各程序自己拦截按键实现的，系统无从查询，
# 因此探测结果一定是「可注册」。这里只挑最典型的高频键给出「能用但容易与
# 其它软件同时触发」的预警（不阻止保存），不追求覆盖全面。

COMMON_SHORTCUTS = frozenset({
    "ctrl+c",   # 复制
    "ctrl+v",   # 粘贴
    "ctrl+x",   # 剪切
    "ctrl+a",   # 全选
    "ctrl+z",   # 撤销
    "ctrl+s",   # 保存
    "ctrl+f",   # 查找
    "alt+tab",  # 切换窗口
})


def is_common_shortcut(text: str) -> bool:
    """是否为「常见软件高频快捷键」。

    注意：这些组合在 Windows 上其实**能注册成功**，因此不能当作硬冲突，
    只能提示用户「可能与前台的其它程序同时触发」。

    只对带修饰键的组合生效：单独按键（``r``、``space``、``pageup``）不算，
    它们本来就无法注册为全局热键，另有专门提示。
    """
    norm = normalize_hotkey(text)
    if not norm or norm.endswith("+"):
        return False

    if len(norm.split("+")) == 1:
        return False  # 单个裸键（字母 / 数字 / pageup ...）不预警

    return norm in COMMON_SHORTCUTS
