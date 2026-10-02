# -*- coding: utf-8 -*-
"""翻译方向与目标语言规则。

规则（用户确认版）：
1. 翻译方向只看源文：
   * 全是中文                 → 英语
   * 全是英文 / 混排 / 其它语种 → 中文
   * 判不出语种（纯数字符号）   → 配置语言
2. 配置语言（设置项 translation_target_lang）只有用户手动改语言才会变，
   自动判断永远不写它。
3. 弹窗语言框始终显示配置语言，自动方向只在内部生效、不改界面。
4. 用户手动选语言 → 存成配置语言；方向以它为准，但只在**本次弹窗**内生效，
   下一次划词/截图回到自动判断。
5. 截图翻译（OCR）与划词完全同一套规则。
"""

import pytest

from settings.tool_settings import ToolSettingsManager
import settings.tool_settings as tool_settings_module
from translation.language_detection import (
    CHINESE,
    ENGLISH,
    count_distinct_languages,
    describe_languages,
    is_all_chinese,
    is_multi_language,
    translation_direction,
)
from translation.translation_dialog import TranslationLoadingDialog
from translation.translation_manager import TranslationManager


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """把全局配置换成临时 QSettings，避免测试污染真实配置。"""
    from PySide6.QtCore import QSettings

    manager = ToolSettingsManager(
        qsettings=QSettings(
            str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
        )
    )
    monkeypatch.setattr(tool_settings_module, "_tool_settings_manager", manager)
    return manager


def _stub_pipeline(monkeypatch, manager):
    """屏蔽网络与线程：只观察目标语言的判定结果。"""
    monkeypatch.setattr(manager, "_backend_ready", lambda: True)
    # 翻译 / 总结各自一条请求通道，停止时带 surface 参数
    monkeypatch.setattr(manager, "_stop_current_thread", lambda *args, **kwargs: None)
    started = []
    monkeypatch.setattr(
        manager,
        "_start_translation",
        lambda text, target_lang, source_lang, result_target: started.append(
            (text, target_lang, result_target)
        ),
    )
    return started


def _set_config_lang(settings, code):
    settings.set_app_setting("translation_target_lang", code)


# ── 1. 方向判定（纯函数） ───────────────────────────────────────────

@pytest.mark.parametrize(
    "text, expected",
    [
        ("你好世界", ENGLISH),
        ("你好，世界！2024 年 5 月", ENGLISH),
        ("  纯中文  ", ENGLISH),
        ("Hello world", CHINESE),
        ("translates to English", CHINESE),
        ("Hello 你好", CHINESE),
        ("日本語のテキスト", CHINESE),
        ("こんにちは", CHINESE),
        ("안녕하세요", CHINESE),
        ("Привет мир", CHINESE),
        ("", None),
        ("12345 !!!", None),
    ],
)
def test_translation_direction(text, expected):
    assert translation_direction(text) == expected


def test_language_helpers():
    assert is_all_chinese("你好世界")
    assert not is_all_chinese("Hello 你好")
    assert is_multi_language("Hello 你好")
    assert not is_multi_language("Hello world")
    assert not is_multi_language("你好世界")
    assert count_distinct_languages("Hello 你好") == 2
    assert describe_languages("你好世界") == "1 个语种（全中文）"
    assert describe_languages("123") == "无语种(数字/符号)"


# ── 2. 配置语言不被自动判断修改 ─────────────────────────────────────

@pytest.mark.parametrize("configured", ["ZH", "EN", "JA"])
@pytest.mark.parametrize(
    "text, expected",
    [
        ("你好世界", ENGLISH),
        ("Hello world", CHINESE),
        ("Hello 你好", CHINESE),
        ("日本語です", CHINESE),
    ],
)
def test_direction_is_independent_of_configured_language(
    qapp, isolated_settings, monkeypatch, configured, text, expected
):
    """方向只看源文，与配置语言无关；配置语言也不会被改写。"""
    _set_config_lang(isolated_settings, configured)
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager.translate_compact(text=text, position=None)

    assert started[0][1] == expected
    assert isolated_settings.get_app_setting("translation_target_lang") == configured
    manager.close_dialog()


def test_unknown_script_falls_back_to_configured_language(
    qapp, isolated_settings, monkeypatch
):
    """纯数字/符号判不出语种 → 用配置语言。"""
    _set_config_lang(isolated_settings, "JA")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager.translate_compact(text="12345 67890", position=None)

    assert started[0][1] == "JA"
    manager.close_dialog()


# ── 3. 语言框只显示配置语言 ─────────────────────────────────────────

def test_popup_shows_configured_language_not_auto_direction(
    qapp, isolated_settings, monkeypatch
):
    """配置是中文时划中文：译文方向是英文，但语言框仍显示中文。"""
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager.translate_compact(text="你好世界", position=None)

    assert started[0][1] == "EN"                       # 实际方向
    assert manager._popup.display_target_lang() == "ZH"  # 界面显示
    manager.close_dialog()


def test_full_dialog_shows_configured_language_not_auto_direction(
    qapp, isolated_settings, monkeypatch
):
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager.translate(text="你好世界", position=None)

    assert started[0][1] == "EN"
    assert manager._dialog.get_target_lang() == "ZH"
    assert isolated_settings.get_app_setting("translation_target_lang") == "ZH"
    manager.close_dialog()


# ── 4. 连续翻译：每次重新判断，不被上一次影响 ───────────────────────

def test_consecutive_selections_rejudge_direction_each_time(
    qapp, isolated_settings, monkeypatch
):
    """中文→英文之后，接着划英文必须是中文（回归：不能一直是英文）。"""
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager.translate_compact(text="你好世界", position=None)
    assert started[-1][1] == "EN"

    manager.translate_compact(text="Hello world", position=None)
    assert started[-1][1] == "ZH"

    manager.translate_compact(text="Hello 你好", position=None)
    assert started[-1][1] == "ZH"

    manager.translate_compact(text="又一段中文", position=None)
    assert started[-1][1] == "EN"

    # 全程都没动配置语言
    assert isolated_settings.get_app_setting("translation_target_lang") == "ZH"
    manager.close_dialog()


def test_consecutive_full_window_translations_rejudge(
    qapp, isolated_settings, monkeypatch
):
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager.translate(text="你好世界", position=None)
    assert started[-1][1] == "EN"

    manager.translate(text="Hello world", position=None)
    assert started[-1][1] == "ZH"

    manager.close_dialog()


# ── 5. 用户手动选语言 ───────────────────────────────────────────────

def test_manual_choice_applies_to_current_popup_and_saves_config(
    qapp, isolated_settings, monkeypatch
):
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager.translate_compact(text="你好世界", position=None)
    assert started[-1][1] == "EN"

    # 用户在弹窗里手动改成日语
    manager._on_popup_target_lang_changed("JA")

    # 同一弹窗内继续翻译：一律用日语
    manager._on_popup_translate_requested("再一段中文")
    assert started[-1][1] == "JA"

    # 手动选择 = 改配置语言
    assert isolated_settings.get_app_setting("translation_target_lang") == "JA"
    assert manager._popup.display_target_lang() == "JA"
    manager.close_dialog()


def test_manual_choice_only_lasts_for_current_popup(
    qapp, isolated_settings, monkeypatch
):
    """手动选择只管本次弹窗；下一次划词回到自动判断。"""
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager.translate_compact(text="你好世界", position=None)
    manager._on_popup_target_lang_changed("JA")
    manager._on_popup_translate_requested("再一段中文")
    assert started[-1][1] == "JA"

    # 下一次划词（新的弹窗会话）：中文 → 英文，而不是继续日语
    manager.translate_compact(text="新一次划词的中文", position=None)
    assert started[-1][1] == "EN"

    # 下一次划英文：中文
    manager.translate_compact(text="next selection in English", position=None)
    assert started[-1][1] == "ZH"

    # 配置语言仍是用户选过的日语
    assert isolated_settings.get_app_setting("translation_target_lang") == "JA"
    manager.close_dialog()


def test_dialog_manual_choice_is_used_and_saved(
    qapp, isolated_settings, monkeypatch
):
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager.translate(text="你好世界", position=None)
    assert started[-1][1] == "EN"

    # 用户在语言框里改成日语
    manager._dialog.target_language.setCurrentIndex(
        manager._dialog.target_language.findData("JA")
    )
    assert manager._dialog.target_lang_selected_by_user
    assert isolated_settings.get_app_setting("translation_target_lang") == "JA"

    manager.translate(text="第二段中文", position=None)
    assert started[-1][1] == "JA"
    manager.close_dialog()


def test_dialog_programmatic_language_is_not_user_selection(
    qapp, isolated_settings
):
    dialog = TranslationLoadingDialog(
        original_text="", source_lang="auto", target_lang="ZH"
    )

    dialog.set_target_lang("EN")

    assert dialog.get_target_lang() == "EN"
    assert dialog.target_lang_selected_by_user is False
    dialog.close()


def test_dialog_translate_button_uses_language_box_value(
    qapp, isolated_settings, monkeypatch
):
    """点弹窗里的「翻译」按钮：用语言框里（=配置语言）的方向。"""
    _set_config_lang(isolated_settings, "EN")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager._on_translate_requested("你好世界", "auto", "EN")

    assert started[-1][1] == "EN"
    manager.close_dialog()


# ── 6. 截图翻译（OCR）与划词同一套规则 ──────────────────────────────

@pytest.mark.parametrize(
    "ocr_text, expected_provider_lang, expected_display",
    [
        ("这是截图里的中文内容", "EN", "ZH"),
        ("Screenshot text in English", "ZH", "ZH"),
        ("截图 with English mixed", "ZH", "ZH"),
    ],
)
def test_screenshot_translation_uses_ocr_text_direction(
    qapp, isolated_settings, monkeypatch, ocr_text, expected_provider_lang,
    expected_display,
):
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)
    # OCR 线程按表面分流（surface 参数），这里只关心目标语言的判定结果
    monkeypatch.setattr(manager, "_start_ocr_thread", lambda pixmap, *args: None)

    manager.translate_from_image(pixmap=object())
    # OCR 未出文字前：方向与语言框都是配置语言
    assert manager._dialog is not None

    manager._on_ocr_finished(True, ocr_text)

    assert started[-1][1] == expected_provider_lang
    # 语言框仍是配置语言，不被自动方向改写
    assert manager._dialog.get_target_lang() == expected_display
    assert isolated_settings.get_app_setting("translation_target_lang") == "ZH"
    manager.close_dialog()


def test_screenshot_translation_keeps_language_chosen_during_ocr(
    qapp, isolated_settings, monkeypatch
):
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)
    # OCR 线程按表面分流（surface 参数），这里只关心目标语言的判定结果
    monkeypatch.setattr(manager, "_start_ocr_thread", lambda pixmap, *args: None)

    manager.translate_from_image(pixmap=object())
    # OCR 期间用户自己改成日语
    manager._dialog.target_language.setCurrentIndex(
        manager._dialog.target_language.findData("JA")
    )

    manager._on_ocr_finished(True, "这是截图里的中文内容")

    assert started[-1][1] == "JA"
    manager.close_dialog()


def test_screenshot_direction_recomputed_for_next_capture(
    qapp, isolated_settings, monkeypatch
):
    """两次截图：中文→英，英文→中。"""
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)
    # OCR 线程按表面分流（surface 参数），这里只关心目标语言的判定结果
    monkeypatch.setattr(manager, "_start_ocr_thread", lambda pixmap, *args: None)

    manager.translate_from_image(pixmap=object())
    manager._on_ocr_finished(True, "第一次截图是中文")
    assert started[-1][1] == "EN"

    manager.translate_from_image(pixmap=object())
    manager._on_ocr_finished(True, "Second capture is English")
    assert started[-1][1] == "ZH"
    manager.close_dialog()


# ── 7. 空输入框 / 无源文 ────────────────────────────────────────────

def test_empty_input_popup_uses_configured_language(
    qapp, isolated_settings, monkeypatch
):
    _set_config_lang(isolated_settings, "JA")
    manager = TranslationManager()
    _stub_pipeline(monkeypatch, manager)

    manager.open_compact_input(position=None)

    assert manager._target_lang == "JA"
    assert manager._popup.display_target_lang() == "JA"
    manager.close_dialog()


def test_manual_input_translation_rejudges_language(
    qapp, isolated_settings, monkeypatch
):
    """小窗里手动输入并翻译：没手动选过语言时按输入内容判断方向。"""
    _set_config_lang(isolated_settings, "ZH")
    manager = TranslationManager()
    started = _stub_pipeline(monkeypatch, manager)

    manager._on_popup_translate_requested("中文输入")
    assert started[-1][1] == "EN"

    manager._on_popup_translate_requested("English input")
    assert started[-1][1] == "ZH"
    manager.close_dialog()
