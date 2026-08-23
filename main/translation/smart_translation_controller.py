# -*- coding: utf-8 -*-
"""One-hotkey selected-text probe and translation window router."""

from __future__ import annotations

import ctypes
import time

from PySide6.QtCore import QObject, QPoint, QTimer, Slot
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from core.i18n import make_tr
from core.logger import log_debug, log_exception
from settings import get_tool_settings_manager

_tr = make_tr("TranslationDialog")


VK_CONTROL = 0x11
VK_C = 0x43
VK_INSERT = 0x2D
VK_MENU = 0x12       # Alt
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
KEYEVENTF_KEYUP = 0x0002
WM_COPY = 0x0301     # 让控件把当前选区复制到剪贴板的消息

# 取词手段优先级（参考 pot-desktop：Ctrl+C 注入最通用，WM_COPY 作为补充兜底，
# Ctrl+Insert 应对少数只认此组合的程序）。所有手段都是"发送即返回"，成功与否由
# 轮询剪贴板判定；轮询预算内按顺序逐步尝试，避免某手段"假成功"短路后续手段。
_COPY_METHOD_ORDER = ("ctrl_c", "wm_copy", "ctrl_insert")

# 轮询复制结果时，需跳过这些"句柄型"剪贴板格式（位图/图元文件等无法跨进程保存）
_HANDLE_CLIPBOARD_FORMATS = frozenset({
    0x0002,  # CF_BITMAP
    0x0003,  # CF_METAFILEPICT
    0x0009,  # CF_PALETTE
    0x000E,  # CF_ENHMETAFILE
    0x0080,  # CF_OWNERDISPLAY
    0x0082,  # CF_DSPBITMAP
    0x0083,  # CF_DSPMETAFILEPICT
    0x008E,  # CF_DSPENHMETAFILE
})


class SmartTranslationController(QObject):
    """Probe selected text and select one of the compact popup's two modes."""

    COPY_DELAY_MS = 60
    POLL_INTERVAL_MS = 20
    PROBE_TIMEOUT_MS = 700

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._config = get_tool_settings_manager()
        self._probe_active = False
        self._copy_dispatched = False
        self._probe_token = 0
        self._cursor_position = QPoint()
        # 划词探测状态
        self._saved_clipboard = None   # 用户原剪贴板（用于还原）
        self._saved_text = ""          # 探测前的剪贴板文本
        self._got_text = False         # 是否已成功取得选区文本
        self._poll_budget = 0          # 轮询剩余预算（毫秒）
        self._copy_attempt_index = 0   # 当前已尝试到的取词手段索引

    @property
    def probe_active(self) -> bool:
        return self._probe_active

    @Slot()
    def trigger(self) -> None:
        """Begin one non-blocking selection probe.

        先保存用户剪贴板，再（按 Ctrl+C → WM_COPY → Ctrl+Insert 的优先级）复制选区，
        轮询直到剪贴板内容发生变化（说明选区确实被复制进剪贴板），读取文本后把原剪贴板
        还原，做到不影响用户剪贴板。Ctrl+C 注入是最通用的取词手段（参考 pot-desktop）。
        """
        self._probe_token += 1
        token = self._probe_token
        self._probe_active = True
        self._copy_dispatched = False
        self._got_text = False
        self._copy_attempt_index = 0
        self._cursor_position = QCursor.pos()
        # 保存用户当前剪贴板（探测结束/超时后还原）
        self._saved_clipboard, _ = self._save_clipboard()
        self._saved_text = self._read_clipboard_raw()
        self._poll_budget = self.PROBE_TIMEOUT_MS - self.COPY_DELAY_MS
        log_debug(
            f"智能翻译探测开始: token={token} 原剪贴板文本长度={len(self._saved_text)}",
            "Translation",
        )

        QTimer.singleShot(self.COPY_DELAY_MS, lambda: self._dispatch_copy(token))
        QTimer.singleShot(self.PROBE_TIMEOUT_MS, lambda: self._on_timeout(token))

    def translate_selection(self, text: str) -> None:
        """Translate text supplied by a caller that owns its own selection.

        Used by widgets whose selection cannot be reached through the system
        clipboard shortcut (self-drawn text layers such as the pinned-image OCR
        overlay). Cancels any in-flight probe so its timers become no-ops.
        """
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            return
        self._probe_token += 1
        self._probe_active = False
        self._copy_dispatched = False
        self._cursor_position = QCursor.pos()
        log_debug(f"外部选区直接翻译: {len(text)} 字符", "Translation")
        self._open_compact(text)

    def _dispatch_copy(self, token: int) -> None:
        if not self._is_current(token):
            return
        self._copy_dispatched = True
        try:
            # 首选 Ctrl+C 注入（最通用的取词手段，参考 pot-desktop）
            self._try_copy(_COPY_METHOD_ORDER[0])
        except Exception as exc:
            log_exception(exc, "复制选区失败")
            # 复制发送失败也继续轮询：可能用户本来就想用监听路径取得文本
        # 开始轮询剪贴板，等待选区被复制进来
        self._poll_clipboard(token)

    def _poll_clipboard(self, token: int) -> None:
        """轮询剪贴板，直到内容相对探测前发生变化（说明选区复制成功）。

        与"定时单次读取"相比，这种方式能准确判断复制何时完成，避免读到旧内容。
        """
        if not self._is_current(token) or self._got_text:
            return
        self._poll_budget -= self.POLL_INTERVAL_MS
        text = self._read_clipboard_raw()
        if text and text != self._saved_text:
            # 剪贴板内容已变化，说明选区被成功复制
            self._got_text = True
            self._probe_active = False
            self._restore_clipboard(self._saved_clipboard)
            log_debug(f"划词读取到选区文本: {len(text)} 字符", "Translation")
            self._open_compact(text)
            return
        # 首帧复制未生效：按优先级逐步尝试后续取词手段，任一道使剪贴板变化即成功。
        # 不再依赖 WM_COPY 的"消息已发出"返回值来短路，避免跳过更通用的 Ctrl+C。
        if self._copy_attempt_index < len(_COPY_METHOD_ORDER) - 1:
            threshold = (
                self.PROBE_TIMEOUT_MS // 2
                if self._copy_attempt_index == 0
                else self.POLL_INTERVAL_MS * 3
            )
            if self._poll_budget <= threshold:
                self._copy_attempt_index += 1
                method = _COPY_METHOD_ORDER[self._copy_attempt_index]
                log_debug(f"划词复制未生效，改试 {method}", "Translation")
                try:
                    self._try_copy(method)
                except Exception as exc:
                    log_exception(exc, f"{method} 复制失败")
        if self._poll_budget > 0:
            QTimer.singleShot(
                self.POLL_INTERVAL_MS, lambda: self._poll_clipboard(token)
            )
        else:
            # 轮询超时仍未取得文本，还原剪贴板并提示无法识别
            log_debug(
                f"划词超时未取到文本（原:{len(self._saved_text)} 现:{len(text or '')}）",
                "Translation",
            )
            self._restore_clipboard(self._saved_clipboard)
            self._notify_unrecognized(token, "empty-clipboard")

    def _read_clipboard_raw(self) -> str:
        """读取系统剪贴板的 CF_UNICODETEXT。

        Windows 上 ``QApplication.clipboard().text()`` 在外部刚写入剪贴板后立刻读取，
        往往返回陈旧/空内容（Qt 对剪贴板做惰性缓存）。直接用 win32clipboard 读取真实
        的 OS 剪贴板，避开 Qt 缓存，结果可靠。剪贴板被占用时做几次短重试。
        """
        try:
            import win32clipboard
        except Exception:
            win32clipboard = None

        if win32clipboard is not None:
            for delay in (0.0, 0.03, 0.06):
                if delay:
                    time.sleep(delay)
                try:
                    win32clipboard.OpenClipboard(0)
                    try:
                        if win32clipboard.IsClipboardFormatAvailable(
                            win32clipboard.CF_UNICODETEXT
                        ):
                            data = win32clipboard.GetClipboardData(
                                win32clipboard.CF_UNICODETEXT
                            )
                        else:
                            data = ""
                    finally:
                        win32clipboard.CloseClipboard()
                except Exception:
                    # 剪贴板被占用，稍后重试
                    continue
                return (data or "").strip()

        # 回退：Qt 读取（可能陈旧，仅作为最后手段）
        try:
            return QApplication.clipboard().text().strip()
        except Exception:
            return ""

    def _save_clipboard(self):
        """保存当前剪贴板内容，返回 (data_dict, unicode_text)。

        data_dict 为 {格式id: 数据}，用于稍后 _restore_clipboard 完整还原。
        句柄型格式（位图/图元文件等）无法跨进程保存，跳过。
        """
        try:
            import win32clipboard
        except Exception:
            return None, ""
        saved = {}
        text = ""
        try:
            win32clipboard.OpenClipboard(0)
            try:
                fmt = win32clipboard.EnumClipboardFormats(0)
                while fmt:
                    if fmt in _HANDLE_CLIPBOARD_FORMATS:
                        fmt = win32clipboard.EnumClipboardFormats(fmt)
                        continue
                    try:
                        data = win32clipboard.GetClipboardData(fmt)
                    except Exception:
                        data = None
                    if data is not None:
                        if fmt == win32clipboard.CF_UNICODETEXT:
                            text = data or ""
                        saved[fmt] = data
                    fmt = win32clipboard.EnumClipboardFormats(fmt)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return None, ""
        return saved, text

    def _restore_clipboard(self, saved) -> None:
        """将 _save_clipboard 保存的内容还原到系统剪贴板。"""
        if not saved:
            return
        try:
            import win32clipboard
        except Exception:
            return
        try:
            win32clipboard.OpenClipboard(0)
            try:
                win32clipboard.EmptyClipboard()
                for fmt, data in saved.items():
                    try:
                        win32clipboard.SetClipboardData(fmt, data)
                    except Exception:
                        pass
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass

    def _try_copy(self, method: str) -> None:
        """按指定手段尝试复制选区。

        所有手段都是"发送即返回"，复制是否真正发生由后续剪贴板轮询判定，
        因此这里不做任何短路。优先级见模块常量 ``_COPY_METHOD_ORDER``：
        ctrl_c（最通用，参考 pot-desktop）→ wm_copy（补充兜底）→ ctrl_insert（少数程序）。
        """
        if method == "ctrl_c":
            self._copy_with_foreground(self._send_copy_shortcut)
        elif method == "wm_copy":
            self._copy_via_wm_copy()
        elif method == "ctrl_insert":
            self._copy_with_foreground(self._send_copy_insert)

    def _copy_via_wm_copy(self) -> bool:
        """直接给拥有选区的控件发送 WM_COPY 消息，让它把选区复制到剪贴板。

        相比模拟 Ctrl+C 按键，这种方式不依赖修饰键状态、没有按键时序问题，
        也更快、更可靠（这正是划词翻译想要的"快速获取选区"方式）。
        返回 True 表示消息已成功发出（复制是否真正发生由后续轮询判定）。
        """
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except Exception:
            return False
        try:
            fg = user32.GetForegroundWindow()
            if not fg:
                return False
            user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
            user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            fg_thread = user32.GetWindowThreadProcessId(fg, None)

            attached = False
            if fg_thread:
                kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
                my_thread = kernel32.GetCurrentThreadId()
                if fg_thread != my_thread:
                    try:
                        if user32.AttachThreadInput(fg_thread, my_thread, True):
                            attached = True
                    except Exception:
                        attached = False

            try:
                # 取得真正拥有选区的聚焦控件；取不到则用前景窗口本身
                target = user32.GetFocus() or fg
                if not target:
                    return False
                user32.SendMessageTimeoutW.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                    ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                    ctypes.POINTER(ctypes.c_ulong),
                ]
                user32.SendMessageTimeoutW.restype = ctypes.c_ulong
                result = ctypes.c_ulong(0)
                user32.SendMessageTimeoutW(
                    target, WM_COPY, 0, 0, 0, 1000,
                    ctypes.byref(result),
                )
                return True
            finally:
                if attached:
                    try:
                        user32.AttachThreadInput(fg_thread, my_thread, False)
                    except Exception:
                        pass
        except Exception as exc:
            log_exception(exc, "WM_COPY 复制失败")
            return False
        return False

    def _copy_with_foreground(self, send_fn) -> None:
        """模拟按键把选区复制到剪贴板，并尽量让按键精准送达拥有选区的那个前景窗口。

        关键点：
        - 翻译热键常带 Alt/Win 等修饰键，触发时这些键可能还没松开，直接发 Ctrl+C
          会被叠加成 Alt+Ctrl+C，目标程序不认。因此先对残留修饰键发 keyup 释放
          （在 send_fn 内部完成）。
        - keybd_event 默认把按键投到前景窗口线程；若选区别名属于另一个进程窗口，
          可能收不到。这里用 AttachThreadInput + SetForegroundWindow 把输入精准送达
          拥有选区的那个前景窗口，提升跨进程送达率。
        """
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except Exception:
            try:
                send_fn()
            except Exception:
                pass
            return

        fg = user32.GetForegroundWindow()
        attached = False
        if fg:
            try:
                user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
                user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                fg_thread = user32.GetWindowThreadProcessId(fg, None)
                kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
                my_thread = kernel32.GetCurrentThreadId()
                if fg_thread and fg_thread != my_thread:
                    if user32.AttachThreadInput(fg_thread, my_thread, True):
                        attached = True
                try:
                    user32.SetForegroundWindow(fg)
                except Exception:
                    pass
            except Exception:
                pass
        try:
            send_fn()
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(fg_thread, my_thread, False)
                except Exception:
                    pass

    def _send_copy_shortcut(self) -> None:
        """发送 Ctrl+C 复制选区。"""
        self._send_copy_keys(VK_C)

    def _send_copy_insert(self) -> None:
        """发送 Ctrl+Insert 复制选区（部分应用只认此组合）。"""
        self._send_copy_keys(VK_INSERT)

    def _send_copy_keys(self, key_vk: int) -> None:
        """发送 "Ctrl + key" 复制选区。

        - 先释放可能仍被按住的修饰键（Alt/Shift/Win），避免与翻译热键的修饰键叠加
          成 Alt+Ctrl+C 之类的组合，导致目标程序不执行复制。
        - 按键之间留微小延时，让系统完整处理一次按键序列。
        """
        user32 = ctypes.windll.user32
        for vk in (VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN):
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.005)
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.01)
        user32.keybd_event(key_vk, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(key_vk, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

    @Slot(object)
    def on_clipboard_item(self, item) -> None:
        """Consume only the clipboard event belonging to an active probe."""
        if not self._probe_active or not self._copy_dispatched:
            return

        token = self._probe_token
        content_type = getattr(item, "content_type", "")
        text = getattr(item, "content", "") if content_type == "text" else ""
        text = text.strip() if isinstance(text, str) else ""

        # 划词翻译不应改动用户剪贴板：无论取到什么，都先还原原始剪贴板
        self._restore_clipboard(self._saved_clipboard)

        if content_type == "text" and text:
            self._probe_active = False
            self._got_text = True
            log_debug(f"智能翻译获取文本成功: {len(text)} 字符", "Translation")
            self._open_compact(text)
            return

        log_debug(f"智能翻译忽略非文本内容: {content_type or 'unknown'}", "Translation")
        self._notify_unrecognized(token, "non-text")

    def _on_timeout(self, token: int) -> None:
        if not self._is_current(token) or self._got_text:
            return
        # 仍未取得文本：还原用户剪贴板，提示无法识别
        self._restore_clipboard(self._saved_clipboard)
        self._notify_unrecognized(token, "timeout")

    def _is_current(self, token: int) -> bool:
        return self._probe_active and token == self._probe_token

    def _translation_manager(self):
        from .translation_manager import TranslationManager

        return TranslationManager.instance()

    def _translation_params(self) -> dict:
        getter = getattr(
            self._config,
            "get_translation_request_params",
            self._config.get_translation_params,
        )
        return getter()

    def _open_compact(self, text: str) -> None:
        params = self._translation_params()
        self._translation_manager().translate_compact(
            text=text,
            position=self._cursor_position,
            **params,
        )

    def _notify_unrecognized(self, token: int, reason: str) -> None:
        """划词未取得文本内容时，弹出不抢眼的提示，而不是打开翻译小窗。

        很多场景下（自绘控件、跨进程选区、加密文本框等）无法把选区复制到
        剪贴板，此时不应弹出输入框打扰用户，仅提示「无法识别选中内容」。
        """
        if not self._is_current(token):
            return
        self._probe_active = False
        log_debug(f"智能翻译未能取得选区文本，提示 Toast: {reason}", "Translation")
        try:
            self._translation_manager().show_unrecognized_toast(self._cursor_position)
        except Exception:
            # 提示失败不应影响主流程
            pass
