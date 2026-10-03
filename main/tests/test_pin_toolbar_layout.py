# -*- coding: utf-8 -*-
"""钉图工具栏布局测试。

背景（用户反馈）：钉图工具栏出现按钮重叠。
原因：钉图工具栏复用截图工具栏，只重排了一部分按钮；漏排的按钮
（截图总结）保留着截图工具栏的坐标，直接压在画笔按钮上。
"""

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from pin.pin_toolbar import PinToolbar


@pytest.fixture
def toolbar(qapp):
    bar = PinToolbar()
    bar.show()
    return bar


def _visible_buttons(bar):
    buttons = [
        (name, obj) for name, obj in vars(bar).items()
        if isinstance(obj, QPushButton) and not obj.isHidden()
    ]
    return sorted(buttons, key=lambda item: item[1].x())


def test_no_overlapping_buttons(toolbar):
    """所有可见按钮必须平铺，不能互相重叠。"""
    buttons = _visible_buttons(toolbar)

    overlaps = []
    for (name_a, a), (name_b, b) in zip(buttons, buttons[1:]):
        inter = a.geometry().intersected(b.geometry())
        if inter.width() > 0 and inter.height() > 0:
            overlaps.append(f"{name_a} 与 {name_b} 重叠 {inter.width()}px")

    assert overlaps == [], "钉图工具栏按钮重叠: " + "; ".join(overlaps)


def test_buttons_tile_left_to_right_without_gaps(toolbar):
    """按钮应紧挨着依次排开，且都落在工具栏宽度内。"""
    buttons = _visible_buttons(toolbar)
    assert len(buttons) >= 10

    for (name_a, a), (name_b, b) in zip(buttons, buttons[1:]):
        assert b.x() == a.x() + a.width(), f"{name_a} 与 {name_b} 之间有空隙/重叠"

    last_name, last = buttons[-1]
    assert last.x() + last.width() <= toolbar.width(), (
        f"{last_name} 超出工具栏宽度 {toolbar.width()}"
    )


def test_summary_button_is_layouted_not_stacked_on_pen(toolbar):
    """截图总结按钮必须被排进布局（此前它压在画笔上）。"""
    summary = toolbar.screenshot_summary_btn
    pen = toolbar.pen_btn

    assert not summary.isHidden()
    assert summary.geometry().intersected(pen.geometry()).width() == 0
    assert summary.x() != pen.x()


def test_screenshot_only_buttons_stay_hidden(toolbar):
    """钉图场景用不到的按钮仍然隐藏。"""
    for attr in ("confirm_btn", "long_screenshot_btn", "pin_btn",
                 "cancel_btn", "gif_btn", "ocr_copy_btn"):
        button = getattr(toolbar, attr, None)
        if button is not None:
            assert button.isHidden(), f"{attr} 在钉图工具栏里不应显示"


def _make_pin_window(monkeypatch):
    """建一个离屏钉图窗口（屏蔽 OCR 线程，只验证工具栏接线）。"""
    import canvas  # noqa: F401  先导入 canvas，避免 tools/canvas 循环导入
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage

    from pin import pin_ocr_manager
    from pin.pin_window import PinWindow
    from ui.settings_ui.mock_config import MockConfig

    monkeypatch.setattr(pin_ocr_manager.PinOCRManager, "init_now", lambda self: None)

    image = QImage(120, 60, QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    return PinWindow(image=image, position=QPoint(40, 40), config_manager=MockConfig())


def test_summary_button_is_wired_in_pin_window(qapp, monkeypatch):
    """总结按钮在钉图里必须真的有响应（此前点了没有任何反应）。"""
    window = _make_pin_window(monkeypatch)

    called = []
    monkeypatch.setattr(window, "_on_summary_clicked", lambda: called.append(True))
    window.show_toolbar()  # 创建工具栏并完成信号连接

    window.toolbar.screenshot_summary_btn.click()

    assert called == [True], "点击总结按钮没有触发钉图总结"


def test_translate_button_still_wired(qapp, monkeypatch):
    """顺带防回归：翻译按钮仍然有响应。"""
    window = _make_pin_window(monkeypatch)

    called = []
    monkeypatch.setattr(window, "_on_translate_clicked", lambda: called.append(True))
    window.show_toolbar()

    window.toolbar.screenshot_translate_btn.click()

    assert called == [True]
