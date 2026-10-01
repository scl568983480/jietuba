# -*- coding: utf-8 -*-
"""
全局热键冲突检测单元测试

覆盖「设置 → 快捷键 → 全局快捷键」实时冲突检测的核心逻辑：
- 热键字符串规范化
- 重复热键（应用内撞车）判定
- 空值 / 未完成输入不报冲突
- 运行期注册失败记录的读写
"""
import pytest
from PySide6.QtWidgets import QApplication

from core.hotkey_utils import is_common_shortcut, normalize_hotkey
from core.hotkey_conflicts import (
    REASON_DUPLICATE,
    validate_hotkey_fields,
)
from core import hotkey_registry


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestNormalizeHotkey:
    def test_modifier_order_is_fixed(self):
        assert normalize_hotkey("Shift+Ctrl+A") == "ctrl+shift+a"

    def test_case_and_spaces(self):
        assert normalize_hotkey("  CTRL + Alt + F4 ") == "ctrl+alt+f4"

    def test_modifier_aliases(self):
        assert normalize_hotkey("Control+Meta+K") == "ctrl+win+k"

    def test_incomplete_combo(self):
        assert normalize_hotkey("Ctrl+") == "ctrl+"

    def test_empty(self):
        assert normalize_hotkey("") == ""
        assert normalize_hotkey(None) == ""


class TestValidateHotkeyFields:
    def test_all_unique_are_available(self):
        results = validate_hotkey_fields([
            ("a", "截图", "ctrl+shift+a"),
            ("b", "剪贴板", "ctrl+shift+v"),
            ("c", "翻译", ""),
        ])
        assert all(item.available for item in results.values())

    def test_duplicate_marks_later_field(self):
        results = validate_hotkey_fields([
            ("a", "截图", "ctrl+shift+a"),
            ("b", "剪贴板", "ctrl+shift+a"),
        ])
        assert results["a"].available is True
        assert results["b"].available is False
        assert results["b"].reason == REASON_DUPLICATE

    def test_duplicate_ignores_modifier_order(self):
        results = validate_hotkey_fields([
            ("a", "截图", "ctrl+shift+a"),
            ("b", "剪贴板", "Shift+Ctrl+A"),
        ])
        assert results["b"].available is False

    def test_blank_and_incomplete_are_not_conflicts(self):
        results = validate_hotkey_fields([
            ("a", "截图", ""),
            ("b", "剪贴板", "ctrl+"),
            ("c", "翻译", "   "),
        ])
        assert all(item.available for item in results.values())

    def test_detail_mentions_first_owner(self):
        results = validate_hotkey_fields([
            ("a", "截图", "ctrl+shift+a"),
            ("b", "剪贴板", "ctrl+shift+a"),
        ])
        assert "截图" in results["b"].detail


class TestCommonShortcutWarning:
    """常用快捷键预警：能用，但不阻止保存"""

    def test_common_keys_detected(self):
        assert is_common_shortcut("ctrl+c")
        assert is_common_shortcut("CTRL + C")
        assert is_common_shortcut("ctrl+v")
        assert is_common_shortcut("ctrl+s")
        assert is_common_shortcut("alt+tab")

    def test_unlisted_keys_have_no_warning(self):
        """清单只保留最常用的几个，其余组合不预警。"""
        assert not is_common_shortcut("win+shift+s")
        assert not is_common_shortcut("ctrl+shift+c")
        assert not is_common_shortcut("ctrl+1")

    def test_common_key_is_still_available(self):
        results = validate_hotkey_fields([("a", "截图", "ctrl+c")])
        assert results["a"].available is True
        assert results["a"].warning

    def test_uncommon_key_has_no_warning(self):
        results = validate_hotkey_fields([("a", "截图", "ctrl+shift+f9")])
        assert results["a"].warning == ""

    def test_bare_keys_are_not_warned(self):
        """应用内快捷键有意使用裸键，不该被预警。"""
        assert not is_common_shortcut("r")
        assert not is_common_shortcut("space")
        assert not is_common_shortcut("pageup")

    def test_duplicate_wins_over_warning(self):
        results = validate_hotkey_fields([
            ("a", "截图", "ctrl+c"),
            ("b", "剪贴板", "ctrl+c"),
        ])
        assert results["b"].available is False
        assert results["b"].warning == ""


class TestRegistrationFailureRegistry:
    def setup_method(self):
        hotkey_registry.clear_registration_failures()

    def teardown_method(self):
        hotkey_registry.clear_registration_failures()

    def test_record_and_lookup_is_normalized(self):
        hotkey_registry.record_registration_failure("Ctrl+Shift+C")
        assert hotkey_registry.get_registered_failure("ctrl+shift+c")

    def test_unknown_key_returns_empty(self):
        assert hotkey_registry.get_registered_failure("ctrl+shift+f9") == ""

    def test_clear(self):
        hotkey_registry.record_registration_failure("ctrl+shift+c")
        hotkey_registry.clear_registration_failures()
        assert hotkey_registry.get_registration_failures() == {}

    def test_blank_is_ignored(self):
        hotkey_registry.record_registration_failure("")
        assert hotkey_registry.get_registration_failures() == {}
