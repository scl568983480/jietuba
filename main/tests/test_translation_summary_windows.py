# -*- coding: utf-8 -*-
"""截图翻译与截图总结是两个独立窗口，互不打断。

用户确认的行为：
* 截图翻译得到一份结果后，可以再截一张图做总结；
* 总结**不会**复用、清空或改写翻译窗口，两份结果可以同时留在屏幕上；
* 两个入口各自持有在途的 OCR / 网络请求，一方开启不会取消另一方。

翻译主窗口与划词小窗仍然互斥（原有的两副面孔关系不变）。
"""

import pytest
from PySide6.QtCore import QPoint, QSettings
from PySide6.QtGui import QImage, QPixmap

from ocr import pipeline as pipeline_module
from settings.tool_settings import ToolSettingsManager
import settings.tool_settings as tool_settings_module
from translation.translation_manager import (
    RESULT_COMPACT,
    RESULT_DIALOG,
    RESULT_SUMMARY,
    TranslationManager,
)


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """把全局配置换成临时 QSettings，避免测试污染真实配置。"""
    manager = ToolSettingsManager(
        qsettings=QSettings(
            str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
        )
    )
    monkeypatch.setattr(tool_settings_module, "_tool_settings_manager", manager)
    return manager


def _image(width=320, height=200):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(0xFF204060)
    return image


def _pixmap():
    return QPixmap.fromImage(_image())


def _stub_windows(monkeypatch, manager):
    """屏蔽 OCR 与网络：只观察两个窗口 / 两条通道的关系。"""
    monkeypatch.setattr(manager, "_backend_ready", lambda: True)
    monkeypatch.setattr(manager, "_summary_ready", lambda: True)
    monkeypatch.setattr(manager, "_start_ocr_thread", lambda pixmap, *args: None)
    translations = []
    summaries = []
    monkeypatch.setattr(
        manager,
        "_start_translation",
        lambda text, target_lang, source_lang, result_target: translations.append(
            (text, target_lang, result_target)
        ),
    )
    monkeypatch.setattr(
        manager,
        "_start_summary",
        lambda text, target_lang: summaries.append((text, target_lang)),
    )
    return translations, summaries


class _FakeRunningThread:
    """只记录是否被要求中断的在途线程替身。"""

    def __init__(self):
        self.interrupted = False

    def isRunning(self):
        return True

    def requestInterruption(self):
        self.interrupted = True


# ── 两个独立窗口 ────────────────────────────────────────────────────

def test_summary_opens_its_own_window_and_keeps_translation_result(
    qapp, isolated_settings, monkeypatch
):
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)

    manager.translate_from_image(pixmap=object())
    translate_dialog = manager._dialog
    assert translate_dialog is not None
    # 模拟翻译已经出结果
    translate_dialog.target_edit.setPlainText("TRANSLATED RESULT")

    manager.summarize_from_image(pixmap=object())

    summary_dialog = manager._dialogs[RESULT_SUMMARY]
    assert summary_dialog is not translate_dialog
    # 翻译窗口仍是原来那个，结果也没被总结清掉
    assert manager._dialogs[RESULT_DIALOG] is translate_dialog
    assert translate_dialog.target_edit.toPlainText() == "TRANSLATED RESULT"
    # 两个窗口各自固定在自己的语义上（不再靠切换同一个窗口的模式）
    assert translate_dialog._mode == "translate"
    assert summary_dialog._mode == "summary"
    assert translate_dialog.windowTitle() != summary_dialog.windowTitle()
    manager.close_dialog()


def test_translation_after_summary_does_not_reuse_the_summary_window(
    qapp, isolated_settings, monkeypatch
):
    """反向顺序同样成立：总结之后再截图翻译，不得到同一个窗口。"""
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)

    manager.summarize_from_image(pixmap=object())
    summary_dialog = manager._dialogs[RESULT_SUMMARY]
    summary_dialog.target_edit.setPlainText("SUMMARY RESULT")

    manager.translate_from_image(pixmap=object())

    assert manager._dialogs[RESULT_DIALOG] is not summary_dialog
    assert manager._dialogs[RESULT_SUMMARY] is summary_dialog
    assert summary_dialog.target_edit.toPlainText() == "SUMMARY RESULT"
    assert summary_dialog._mode == "summary"
    manager.close_dialog()


def test_both_main_windows_can_be_open_at_the_same_time(
    qapp, isolated_settings, monkeypatch
):
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)

    manager.translate_from_image(pixmap=object())
    manager.summarize_from_image(pixmap=object())

    assert manager.is_dialog_open()
    assert manager._surface_valid(RESULT_DIALOG)
    assert manager._surface_valid(RESULT_SUMMARY)

    manager.close_dialog()
    assert not manager._dialogs


# ── 互不打断：线程与令牌 ────────────────────────────────────────────

def test_starting_summary_does_not_cancel_translation_request(
    qapp, isolated_settings, monkeypatch
):
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)
    running = _FakeRunningThread()
    manager._thread = running  # 翻译通道在途请求
    translate_token = manager._request_token
    summary_token = manager._summary_token

    manager.summarize_from_image(pixmap=object())

    assert not running.interrupted
    assert manager._thread is running
    assert manager._request_token == translate_token
    assert manager._summary_token == summary_token + 1
    manager.close_dialog()


def test_starting_translation_does_not_cancel_summary_request(
    qapp, isolated_settings, monkeypatch
):
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)
    running = _FakeRunningThread()
    manager._summary_thread = running  # 总结通道在途请求
    summary_token = manager._summary_token
    translate_token = manager._request_token

    manager.translate_from_image(pixmap=object())

    assert not running.interrupted
    assert manager._summary_thread is running
    assert manager._summary_token == summary_token
    assert manager._request_token == translate_token + 1
    manager.close_dialog()


def test_summary_does_not_clear_translation_source_text(
    qapp, isolated_settings, monkeypatch
):
    """总结不能把翻译窗口的原文改成"识别中..."。"""
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)

    manager.translate_from_image(pixmap=object())
    translate_dialog = manager._dialog
    translate_dialog.source_edit.setPlainText("ocr text of capture 1")

    manager.summarize_from_image(pixmap=object())

    assert translate_dialog.source_edit.toPlainText() == "ocr text of capture 1"
    assert manager._dialogs[RESULT_SUMMARY]._mode == "summary"
    manager.close_dialog()


# ── 互不打断：OCR 通道 ──────────────────────────────────────────────

class _StubSignal:
    def __init__(self):
        self.receivers = []

    def connect(self, slot):
        self.receivers.append(slot)


class _StubOcrThread:
    instances = []

    def __init__(self, image, config_manager=None, parent=None):
        self.image = image
        self.cancelled = False
        self.started = False
        self.finished_signal = _StubSignal()
        self.finished = _StubSignal()
        _StubOcrThread.instances.append(self)

    def cancel(self):
        self.cancelled = True

    def isRunning(self):
        return True

    def start(self):
        self.started = True


@pytest.fixture
def stub_ocr_thread(monkeypatch):
    _StubOcrThread.instances = []
    monkeypatch.setattr(pipeline_module, "OcrTextThread", _StubOcrThread)
    return _StubOcrThread


def test_summary_and_translation_ocr_run_in_separate_channels(
    qapp, isolated_settings, monkeypatch, stub_ocr_thread
):
    """截图翻译的 OCR 还在跑时开始总结，不得取消/断开翻译的 OCR。"""
    manager = TranslationManager()
    monkeypatch.setattr(manager, "_backend_ready", lambda: True)
    monkeypatch.setattr(manager, "_summary_ready", lambda: True)
    monkeypatch.setattr(manager, "_start_translation", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_start_summary", lambda *args, **kwargs: None)

    manager.translate_from_image(pixmap=_pixmap())
    translate_ocr = manager._ocr_thread
    assert translate_ocr is stub_ocr_thread.instances[0]
    assert translate_ocr.started

    manager.summarize_from_image(pixmap=_pixmap())
    summary_ocr = manager._summary_ocr_thread

    assert summary_ocr is stub_ocr_thread.instances[1]
    assert summary_ocr is not translate_ocr
    # 翻译通道的 OCR 没有被取消、也没有被断开
    assert not translate_ocr.cancelled
    assert manager._ocr_thread is translate_ocr
    assert len(translate_ocr.finished_signal.receivers) == 1
    assert len(summary_ocr.finished_signal.receivers) == 1
    manager.close_dialog()


# ── 窗口按钮分流：总结窗口的按钮只会触发总结 ────────────────────────

def test_summary_window_action_runs_summary_not_translation(
    qapp, isolated_settings, monkeypatch
):
    """总结窗口底部按钮（语义为「总结」）必须走总结通道。"""
    manager = TranslationManager()
    translations, summaries = _stub_windows(monkeypatch, manager)

    manager.summarize_from_image(pixmap=object())
    summary_dialog = manager._dialogs[RESULT_SUMMARY]
    summary_dialog.source_edit.setPlainText("需要总结的文字")

    summary_dialog._request_translation()  # 等价于点窗口底部按钮

    assert summaries == [("需要总结的文字", summary_dialog.get_target_lang())]
    assert translations == []
    manager.close_dialog()


def test_translation_window_action_still_runs_translation(
    qapp, isolated_settings, monkeypatch
):
    manager = TranslationManager()
    translations, summaries = _stub_windows(monkeypatch, manager)

    manager.translate_from_image(pixmap=object())
    translate_dialog = manager._dialog
    translate_dialog.source_edit.setPlainText("text to translate")

    translate_dialog._request_translation()

    assert summaries == []
    assert len(translations) == 1
    assert translations[0][0] == "text to translate"
    assert translations[0][2] == RESULT_DIALOG
    manager.close_dialog()


# ── 关闭一个窗口不影响另一个 ────────────────────────────────────────

def test_destroying_summary_window_keeps_translation_window(
    qapp, isolated_settings, monkeypatch
):
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)

    manager.translate_from_image(pixmap=object())
    manager.summarize_from_image(pixmap=object())
    translate_dialog = manager._dialogs[RESULT_DIALOG]
    running = _FakeRunningThread()
    manager._thread = running  # 翻译通道在途请求

    manager._on_dialog_destroyed(RESULT_SUMMARY)

    assert RESULT_SUMMARY not in manager._dialogs
    assert manager._dialogs[RESULT_DIALOG] is translate_dialog
    assert manager._thread is running and not running.interrupted
    manager.close_dialog()


# ── 总结语言与翻译语言互不覆盖 ──────────────────────────────────────

def test_summary_window_language_is_saved_separately(
    qapp, isolated_settings, monkeypatch
):
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)
    isolated_settings.set_app_setting("translation_target_lang", "ZH")

    manager.summarize_from_image(pixmap=object())
    summary_dialog = manager._dialogs[RESULT_SUMMARY]
    summary_dialog.target_language.setCurrentIndex(
        summary_dialog.target_language.findData("JA")
    )

    assert isolated_settings.get_app_setting("summary_target_lang") == "JA"
    # 总结窗口改语言不能顺手改掉翻译语言（否则下一次截图翻译会被"打断"）
    assert isolated_settings.get_app_setting("translation_target_lang") == "ZH"
    manager.close_dialog()


# ── 窗口层叠 ────────────────────────────────────────────────────────

def test_second_main_window_cascades_instead_of_overlapping(
    qapp, isolated_settings, monkeypatch
):
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)

    # 只有一个主窗口时，新窗口仍按光标所在屏幕居中
    assert manager._cascade_position(RESULT_DIALOG) is None

    manager.translate_from_image(pixmap=object())
    dialog = manager._dialog
    dialog.move(QPoint(200, 150))

    position = manager._cascade_position(RESULT_SUMMARY)

    assert position is not None
    assert position != dialog.pos()
    assert position.x() > dialog.pos().x()
    assert position.y() > dialog.pos().y()
    manager.close_dialog()


# ── 划词小窗与翻译主窗口的原有互斥不变 ──────────────────────────────

def test_compact_popup_still_hides_only_the_translation_window(
    qapp, isolated_settings, monkeypatch
):
    manager = TranslationManager()
    _stub_windows(monkeypatch, manager)
    manager.translate_from_image(pixmap=object())
    manager.summarize_from_image(pixmap=object())
    translate_dialog = manager._dialogs[RESULT_DIALOG]
    summary_dialog = manager._dialogs[RESULT_SUMMARY]
    summary_visible_before = summary_dialog.isVisible()

    manager._activate_surface(RESULT_COMPACT)

    # 翻译主窗口被划词小窗让位（原有互斥）
    assert not translate_dialog.isVisible()
    # 总结窗口不是翻译功能的另一副面孔，不该被划词小窗顺手隐藏
    assert summary_dialog.isVisible() == summary_visible_before
    manager.close_dialog()
