# -*- coding: utf-8 -*-
"""离线词典接入划词小窗的行为测试。

监控目标：
1. 命中词典且开启"跳过联网"时，必须**不发**网络请求（省 API、秒出）。
2. 未命中时必须回退到原有联网翻译，不能因为词典挡住正常翻译。
3. 目标语言不是中文时不查词典（ECDICT 只有中文释义）。
4. 整段文字不查词典，直接走翻译引擎。
5. 词典不可用/查询异常都不得打断翻译流程。
"""
import os
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dictionary.models import (  # noqa: E402
    DictEntry,
    DictSense,
    LookupResult,
    ZhEnEntry,
    ZhEnLookupResult,
    ZhEnSense,
)
from translation.translation_manager import TranslationManager  # noqa: E402

ENTRY = DictEntry(
    headword="give",
    query="gave",
    phonetic="giv",
    senses=(DictSense("n.", "弹性, 适应性"), DictSense("vt.", "给, 授予")),
    inflection="Past tense",
    forms=(("Past tense", "gave"), ("Past participle", "given")),
    tags=("zk", "gk"),
    collins=5,
    oxford=True,
    bnc=71,
    frq=98,
)


class _FakeDictionary:
    """可编程的离线词典替身（英→中 与 中→英 两侧都可编程）。"""

    def __init__(self, result=None, *, available=True, enabled=True,
                 skip_online=True, zh_result=None, zh_available=True,
                 zh_enabled=True):
        self.result = result or LookupResult()
        self.available = available
        self.enabled = enabled
        self.skip_online = skip_online
        self.zh_result = zh_result or ZhEnLookupResult()
        self.zh_available = zh_available
        self.zh_enabled = zh_enabled
        self.lookup_calls = []
        self.zh_lookup_calls = []

    def is_enabled(self):
        return self.enabled

    def is_available(self):
        return self.available

    def skip_online_on_hit(self):
        return self.skip_online

    def display_options(self):
        from dictionary import DisplayOptions

        return DisplayOptions()

    def lookup(self, text):
        self.lookup_calls.append(text)
        return self.result

    def is_zh_en_available(self):
        return self.zh_available

    def is_zh_en_enabled(self):
        return self.zh_enabled

    def lookup_zh_en(self, text):
        self.zh_lookup_calls.append(text)
        return self.zh_result


@pytest.fixture
def manager(monkeypatch, qapp):
    """一个把词典与网络都换成替身的 TranslationManager。

    小窗显示时会注册全局 ESC 热键与 Windows 鼠标钩子；测试里把它们换成
    空操作，避免后台钩子线程在 pytest-qt 的 teardown 之后还活着。
    """
    from translation.translation_popup import TranslationPopup

    monkeypatch.setattr(
        TranslationPopup, "_register_esc_hotkey", lambda self: None
    )
    monkeypatch.setattr(
        TranslationPopup, "_arm_click_outside_close", lambda self: None
    )
    monkeypatch.setattr(
        TranslationPopup, "_disarm_click_outside_close", lambda self: None
    )
    monkeypatch.setattr(
        TranslationPopup, "_unregister_esc_hotkey", lambda self: None
    )

    previous = TranslationManager._instance
    instance = TranslationManager()
    TranslationManager._instance = None
    monkeypatch.setattr(instance, "_backend_ready", lambda: True)
    monkeypatch.setattr(instance, "_backend_name", lambda: "fake")
    monkeypatch.setattr(instance, "configured_target_lang", lambda: "ZH")
    started = []
    monkeypatch.setattr(
        instance,
        "_start_translation",
        lambda *args, **kwargs: started.append((args, kwargs)),
    )
    instance._test_started = started
    yield instance
    instance._stop_current_thread()
    if instance._popup is not None:
        try:
            instance._popup.hide()
        except RuntimeError:
            pass
    TranslationManager._instance = previous


def _use_dictionary(monkeypatch, fake):
    monkeypatch.setattr(
        TranslationManager, "_dictionary_service", staticmethod(lambda: fake)
    )
    return fake


# ── 命中 ────────────────────────────────────────────────────────────
def test_hit_skips_network_and_renders_entry(monkeypatch, manager):
    fake = _use_dictionary(
        monkeypatch, _FakeDictionary(LookupResult(entry=ENTRY))
    )

    manager.translate_compact(text="gave", position=QPoint(10, 10))

    assert fake.lookup_calls == ["gave"]
    # 关键：命中且跳过联网时，绝不能发起网络请求
    assert manager._test_started == []
    assert manager._popup.result_edit.toPlainText()  # 卡片已渲染
    assert manager._popup._dictionary_mode is True
    assert manager._popup.copy_button.isEnabled() is True
    # 复制拿到的是纯文本释义
    assert "弹性, 适应性" in manager._popup._translated_text


def test_hit_with_skip_online_disabled_still_translates(monkeypatch, manager):
    fake = _use_dictionary(
        monkeypatch,
        _FakeDictionary(LookupResult(entry=ENTRY), skip_online=False),
    )

    manager.translate_compact(text="gave", position=QPoint(10, 10))

    assert fake.lookup_calls == ["gave"]
    # 词条先显示，随后照常联网
    assert len(manager._test_started) == 1


# ── 未命中 ──────────────────────────────────────────────────────────
def test_miss_falls_back_to_online_translation(monkeypatch, manager):
    fake = _use_dictionary(monkeypatch, _FakeDictionary(LookupResult()))

    manager.translate_compact(text="zzzzqqqq", position=QPoint(10, 10))

    assert fake.lookup_calls == ["zzzzqqqq"]
    assert len(manager._test_started) == 1
    assert manager._popup._dictionary_mode is False


def test_miss_without_backend_shows_api_error_for_multiword(monkeypatch, manager):
    """多词短语没有有意义的候选词 → 保持原有的「引擎未配置」提示。"""
    _use_dictionary(monkeypatch, _FakeDictionary(LookupResult()))
    monkeypatch.setattr(manager, "_backend_ready", lambda: False)
    expected = manager._api_key_error()

    manager.translate_compact(text="selected text", position=QPoint(10, 10))

    assert manager._popup.result_edit.toPlainText() == expected
    assert manager._popup.result_edit.property("error") is True


def test_miss_with_suggestions_shows_hint_for_single_word(monkeypatch, manager):
    _use_dictionary(
        monkeypatch,
        _FakeDictionary(LookupResult(suggestions=("bare", "barely"))),
    )
    monkeypatch.setattr(manager, "_backend_ready", lambda: False)

    manager.translate_compact(text="baref", position=QPoint(10, 10))

    assert manager._popup._dictionary_mode is True
    assert "bare" in manager._popup.result_edit.toPlainText()


# ── 不该查词典的情况 ────────────────────────────────────────────────
def test_paragraph_is_not_looked_up(monkeypatch, manager):
    fake = _use_dictionary(
        monkeypatch, _FakeDictionary(LookupResult(entry=ENTRY))
    )

    manager.translate_compact(
        text="This is a whole paragraph.", position=QPoint(10, 10)
    )

    assert fake.lookup_calls == []
    assert len(manager._test_started) == 1


def test_non_chinese_target_is_not_looked_up(monkeypatch, manager):
    """ECDICT 只有中文释义；目标是英文时不该用它顶替翻译。"""
    fake = _use_dictionary(
        monkeypatch, _FakeDictionary(LookupResult(entry=ENTRY))
    )
    monkeypatch.setattr(manager, "configured_target_lang", lambda: "EN")
    monkeypatch.setattr(manager, "_direction_for", lambda text: "EN")

    manager.translate_compact(text="gave", position=QPoint(10, 10))

    assert fake.lookup_calls == []
    assert len(manager._test_started) == 1


def test_disabled_dictionary_is_not_used(monkeypatch, manager):
    fake = _use_dictionary(
        monkeypatch,
        _FakeDictionary(LookupResult(entry=ENTRY), enabled=False),
    )

    manager.translate_compact(text="gave", position=QPoint(10, 10))

    assert fake.lookup_calls == []
    assert len(manager._test_started) == 1


# ── 隔离性 ──────────────────────────────────────────────────────────
def test_dictionary_failure_never_breaks_translation(monkeypatch, manager):
    class _Boom:
        def is_enabled(self):
            raise RuntimeError("boom")

    _use_dictionary(monkeypatch, _Boom())

    manager.translate_compact(text="gave", position=QPoint(10, 10))

    # 词典炸了也要照常翻译
    assert len(manager._test_started) == 1


def test_missing_dictionary_module_is_tolerated(monkeypatch, manager):
    def _raise():
        raise ImportError("no dictionary")

    monkeypatch.setattr(
        TranslationManager, "_dictionary_service", staticmethod(_raise)
    )

    manager.translate_compact(text="gave", position=QPoint(10, 10))

    assert len(manager._test_started) == 1


def test_typed_word_in_popup_uses_dictionary(monkeypatch, manager):
    """用户在弹窗里把文字改成英文单词，同样先走本地词典。"""
    fake = _use_dictionary(
        monkeypatch, _FakeDictionary(LookupResult(entry=ENTRY))
    )
    manager.open_compact_input(position=QPoint(10, 10))

    manager._on_popup_translate_requested("gave")

    assert fake.lookup_calls == ["gave"]
    assert manager._test_started == []


# ── 中 → 英（汉英库） ───────────────────────────────────────────────
ZH_ENTRY = ZhEnEntry(
    headword="完成",
    pinyin="wán chéng",
    senses=(
        ZhEnSense("vt.", "accomplish"),
        ZhEnSense("vt.", "complete"),
        ZhEnSense("vt.", "finish"),
    ),
    query="完成",
)


def test_chinese_word_uses_zh_en_dictionary(monkeypatch, manager):
    fake = _use_dictionary(
        monkeypatch, _FakeDictionary(zh_result=ZhEnLookupResult(entry=ZH_ENTRY))
    )

    manager.translate_compact(text="完成", position=QPoint(10, 10))

    # 关键：中文词走汉英库，且不发网络请求
    assert fake.zh_lookup_calls == ["完成"]
    assert fake.lookup_calls == []
    assert manager._test_started == []
    assert manager._popup._source_origin == "offline"
    assert "完整" not in manager._popup._translated_text  # 是英文对应词
    assert "complete" in manager._popup._translated_text


def test_english_and_chinese_do_not_cross_paths(monkeypatch, manager):
    """两个方向必须各走各的库，不能互相抢。"""
    fake = _use_dictionary(
        monkeypatch,
        _FakeDictionary(
            LookupResult(entry=ENTRY), zh_result=ZhEnLookupResult(entry=ZH_ENTRY)
        ),
    )

    manager.translate_compact(text="gave", position=QPoint(10, 10))
    assert fake.lookup_calls == ["gave"]
    assert fake.zh_lookup_calls == []

    fake.lookup_calls.clear()
    manager.translate_compact(text="完成", position=QPoint(10, 10))
    assert fake.zh_lookup_calls == ["完成"]
    assert fake.lookup_calls == []


def test_chinese_miss_falls_back_to_online(monkeypatch, manager):
    fake = _use_dictionary(monkeypatch, _FakeDictionary())

    manager.translate_compact(text="蹦蹦跳跳", position=QPoint(10, 10))

    assert fake.zh_lookup_calls == ["蹦蹦跳跳"]
    assert len(manager._test_started) == 1


def test_chinese_sentence_is_not_looked_up(monkeypatch, manager):
    fake = _use_dictionary(
        monkeypatch, _FakeDictionary(zh_result=ZhEnLookupResult(entry=ZH_ENTRY))
    )

    manager.translate_compact(
        text="这是一整句话不应该查离线词典的", position=QPoint(10, 10)
    )

    assert fake.zh_lookup_calls == []
    assert len(manager._test_started) == 1


def test_missing_zh_en_library_keeps_online_behaviour(monkeypatch, manager):
    """只装了英→中词库时，中→英 必须照旧联网。"""
    fake = _use_dictionary(
        monkeypatch,
        _FakeDictionary(
            LookupResult(entry=ENTRY), zh_result=ZhEnLookupResult(),
            zh_available=False,
        ),
    )

    manager.translate_compact(text="完成", position=QPoint(10, 10))

    assert fake.zh_lookup_calls == ["完成"]
    assert len(manager._test_started) == 1
    assert manager._popup._source_origin == ""


def test_disabled_zh_en_is_not_used(monkeypatch, manager):
    fake = _use_dictionary(
        monkeypatch,
        _FakeDictionary(
            zh_result=ZhEnLookupResult(entry=ZH_ENTRY), zh_enabled=False
        ),
    )

    manager.translate_compact(text="完成", position=QPoint(10, 10))

    assert fake.zh_lookup_calls == []
    assert len(manager._test_started) == 1


def test_zh_en_hit_respects_skip_online_setting(monkeypatch, manager):
    fake = _use_dictionary(
        monkeypatch,
        _FakeDictionary(
            zh_result=ZhEnLookupResult(entry=ZH_ENTRY), skip_online=False
        ),
    )

    manager.translate_compact(text="完成", position=QPoint(10, 10))

    assert fake.zh_lookup_calls == ["完成"]
    # 关掉"命中跳过联网"时，词条先显示、随后仍要联网
    assert len(manager._test_started) == 1
