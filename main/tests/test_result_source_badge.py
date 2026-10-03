# -*- coding: utf-8 -*-
"""「结果来源」标签测试：用户必须能区分 ECDICT 离线词典与大模型联网翻译。

监控目标：
1. 离线词典命中 → 结果下方明确标注「离线词典 · ECDICT」。
2. 大模型联网出结果 → 标注「联网翻译 · <引擎名>」。
3. 标签是独立控件，**不得混进结果文本**，否则"复制译文"会带上来源字样。
4. 加载中 / 出错 / 无结果时不显示来源。
5. 完整翻译窗口的标题栏引擎角标必须可见。

注意：判断可见性用 ``isHidden()`` 而不是 ``isVisible()``——父窗口没 show 时
``isVisible()`` 恒为 False，无法区分"显式隐藏"和"窗口没显示"。
"""
import os
import sys

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtCore import QTranslator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dictionary.models import DictEntry, DictSense  # noqa: E402
from translation.translation_dialog import TranslationDialog  # noqa: E402
from translation.translation_popup import TranslationPopup  # noqa: E402


@pytest.fixture
def popup(qapp):
    widget = TranslationPopup()
    # 全局 ESC 热键与鼠标钩子在测试里没必要真的装
    widget._register_esc_hotkey = lambda: None
    widget._unregister_esc_hotkey = lambda: None
    widget._arm_click_outside_close = lambda: None
    widget._disarm_click_outside_close = lambda: None
    yield widget
    try:
        widget.hide()
    except RuntimeError:
        pass


def _entry() -> DictEntry:
    """真实词条对象（用替身容易漏字段，反而测不准）。"""
    return DictEntry(
        headword="give",
        query="gave",
        phonetic="giv",
        senses=(DictSense("vt.", "给, 授予"),),
        inflection="Past tense",
        forms=(("Past tense", "gave"),),
        tags=("zk",),
        collins=5,
        oxford=True,
        bnc=71,
        frq=98,
    )


# ── 离线词典 ────────────────────────────────────────────────────────
def test_dictionary_hit_marks_result_as_offline(popup):
    popup.show_popup("gave", QPoint(10, 10))
    popup.show_dictionary(_entry())

    assert popup._source_origin == "offline"
    assert popup.source_badge.isHidden() is False
    assert "ECDICT" in popup.source_badge.text()


def test_dictionary_miss_is_also_marked_offline(popup):
    popup.show_popup("baref", QPoint(10, 10))
    popup.show_dictionary_miss("baref", ("bare", "barely"))

    assert popup._source_origin == "offline"
    assert popup.source_badge.isHidden() is False


# ── 联网翻译 ────────────────────────────────────────────────────────
def test_online_result_shows_engine_name(popup):
    popup.set_backend_status("OpenAI API", True)
    popup.show_popup("hello", QPoint(10, 10))
    popup.show_result("你好")

    assert popup._source_origin == "online"
    assert popup.source_badge.isHidden() is False
    assert "OpenAI API" in popup.source_badge.text()


def test_online_result_without_engine_name_falls_back(popup):
    popup.set_backend_status("", True)
    popup.show_popup("hello", QPoint(10, 10))
    popup.show_result("你好")

    assert popup._source_origin == "online"
    assert "Translation API" in popup.source_badge.text()


# ── 关键约定：来源不进结果文本 ──────────────────────────────────────
def test_source_badge_never_leaks_into_copyable_text(popup):
    popup.set_backend_status("OpenAI API", True)
    popup.show_popup("hello", QPoint(10, 10))
    popup.show_result("你好")

    # 复制走的是 _translated_text，必须是干净的译文
    assert popup.result_edit.toPlainText() == "你好"
    assert popup._translated_text == "你好"
    assert "OpenAI API" not in popup.result_edit.toPlainText()

    popup.show_dictionary(_entry())
    assert popup._translated_text == _entry().to_plain_text()
    assert "ECDICT" not in popup._translated_text


# ── 不该显示来源的状态 ──────────────────────────────────────────────
def test_no_source_badge_while_loading(popup):
    popup.show_popup("hello", QPoint(10, 10))  # 进入 loading
    assert popup._source_origin == ""
    assert popup.source_badge.isHidden() is True


def test_no_source_badge_on_error(popup):
    popup.show_popup("hello", QPoint(10, 10))
    popup.show_error("network error")
    assert popup._source_origin == ""
    assert popup.source_badge.isHidden() is True


def test_source_badge_cleared_when_result_hidden(popup):
    popup.show_popup("hello", QPoint(10, 10))
    popup.show_result("你好")
    assert popup._source_origin == "online"

    popup.show_popup("", QPoint(10, 10))  # 空文本 → 收起结果区
    assert popup._source_origin == ""
    assert popup.source_badge.isHidden() is True


def test_switching_between_offline_and_online_updates_badge(popup):
    popup.set_backend_status("OpenAI API", True)
    popup.show_popup("gave", QPoint(10, 10))

    popup.show_dictionary(_entry())
    assert popup._source_origin == "offline"

    popup.show_result("给")
    assert popup._source_origin == "online"
    assert "OpenAI API" in popup.source_badge.text()


# ── 主题与语言 ──────────────────────────────────────────────────────
def test_badge_text_is_translated(popup, qapp):
    translator = QTranslator()
    assert translator.load(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "translations", "app_zh.qm",
        )
    )
    qapp.installTranslator(translator)
    try:
        popup.set_backend_status("OpenAI API", True)
        popup.show_popup("hello", QPoint(10, 10))
        popup.show_result("你好")
        assert "联网翻译" in popup.source_badge.text()

        popup.show_dictionary(_entry())
        assert "离线词典" in popup.source_badge.text()
    finally:
        qapp.removeTranslator(translator)


def test_badge_origin_property_matches_state(popup):
    """样式靠 origin 动态属性区分颜色。"""
    popup.show_popup("gave", QPoint(10, 10))
    popup.show_dictionary(_entry())
    assert popup.source_badge.property("origin") == "offline"

    popup.show_result("给")
    assert popup.source_badge.property("origin") == "online"

    popup.show_error("boom")
    assert popup.source_badge.property("origin") == ""


# ── 完整翻译窗口 ────────────────────────────────────────────────────
def test_full_dialog_badge_is_visible(qapp):
    dialog = TranslationDialog()
    try:
        assert dialog.dashboard_title_bar.backend_badge.isHidden() is False
        dialog.set_backend_badge("OpenAI API", True)
        assert dialog.dashboard_title_bar.backend_badge.text() == "OpenAI API"
        assert dialog.dashboard_title_bar.backend_badge.isHidden() is False
    finally:
        dialog.close()
