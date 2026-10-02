# -*- coding: utf-8 -*-
"""钉图 OCR 文字层的「显示」行为测试。

背景（用户反馈）：钉图里 Ctrl+A 全选 + 复制能得到全部文字，但屏幕上
看不到任何高亮 —— 因为字符位置是懒加载的，只有鼠标移动时才算，
而 Ctrl+A 只设了 selection 就直接 update()，paintEvent 里
`if not item.char_positions: continue` 于是把每个文字块都跳过了。
"""

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtWidgets import QApplication

from pin.ocr_text_layer import OCRTextLayer


_RESULT = {
    "code": 100,
    "data": [
        {"text": "hello", "score": 1.0, "box": [[10, 10], [120, 10], [120, 30], [10, 30]]},
        {"text": "world", "score": 1.0, "box": [[10, 40], [120, 40], [120, 60], [10, 60]]},
    ],
}


def _make_layer(width=230, height=74):
    layer = OCRTextLayer()
    layer.resize(width, height)
    items, union_rect = OCRTextLayer.prepare_ocr_items(_RESULT)
    layer.load_prepared_ocr_items(items, union_rect, width, height)
    return layer


def _highlight_pixels(layer) -> int:
    """把文字层渲染成图，统计被高亮覆盖的像素数。"""
    image = QImage(layer.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    layer.render(image)

    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 0:
                count += 1
    return count


def test_no_highlight_without_selection(qapp):
    layer = _make_layer()

    assert layer.has_text() is True
    assert _highlight_pixels(layer) == 0


def test_ctrl_a_selection_becomes_visible(qapp):
    """Ctrl+A 全选后必须能看到整片高亮（否则用户不知道识别到了什么）。"""
    layer = _make_layer()

    QApplication.sendEvent(
        layer,
        QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier
        ),
    )

    assert layer.selection_start == (0, 0)
    assert layer.selection_end == (1, len("world"))
    assert _highlight_pixels(layer) > 0


def test_ctrl_a_then_copy_returns_all_text(qapp):
    """全选 + 复制的内容不受显示修复影响（本条是既有能力，防回归）。"""
    layer = _make_layer()

    QApplication.sendEvent(
        layer,
        QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier
        ),
    )
    QApplication.sendEvent(
        layer,
        QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier
        ),
    )

    assert "hello" in QApplication.clipboard().text()
    assert "world" in QApplication.clipboard().text()


def test_drag_select_highlights_and_picks_text(qapp):
    """拖选：文字框位置必须与鼠标位置对得上（同时覆盖坐标空间对齐）。"""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    layer = _make_layer()

    def _send(kind, pos, button, buttons):
        point = QPointF(*pos)
        QApplication.sendEvent(
            layer,
            QMouseEvent(
                kind, point, point, button, buttons, Qt.KeyboardModifier.NoModifier
            ),
        )

    # 在第一个文字块上按下，拖到第二个文字块
    _send(QEvent.Type.MouseButtonPress, (30, 20),
          Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    assert layer.selection_start is not None, "按在文字上必须能开始选择"

    _send(QEvent.Type.MouseMove, (60, 50),
          Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    _send(QEvent.Type.MouseButtonRelease, (60, 50),
          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)

    assert _highlight_pixels(layer) > 0
    # 从第一块的中间拖到第二块的中间：两块都有一部分被选中（Word 风格）
    selected = layer.get_selected_text()
    assert "ello" in selected
    assert "wo" in selected


def test_press_on_blank_area_does_not_start_selection(qapp):
    """点在远离文字的空白处应透传给父窗口（用于拖动钉图），不产生选择高亮。"""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    layer = _make_layer()

    point = QPointF(200, 70)
    QApplication.sendEvent(
        layer,
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )

    assert layer.selection_start is None
    assert _highlight_pixels(layer) == 0


# ── 命中容差：行间/边缘按下也要能开始选字 ────────────────────────────
# 旧行为只有 ±5px 命中框：差几个像素就判为「不在文字上」→ 事件透传 →
# 钉图开始拖动窗口（且拖动后划过文字也不会选中），用户表现就是"选不中文字"。

def _press(layer, x, y):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    point = QPointF(x, y)
    QApplication.sendEvent(
        layer,
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_press_just_above_text_line_starts_selection(qapp):
    """文字行上方 3px（旧实现会变成拖动窗口）。"""
    layer = _make_layer()  # 第一行文字框 y: 10~30

    _press(layer, 60, 7)

    assert layer.selection_start is not None
    assert layer.selection_start[0] == 0
    # 光标形状与按下行为必须一致：这里也应是 I 型光标区
    assert layer._is_pos_on_text(QPoint(60, 7)) is True


def test_press_in_gap_between_lines_picks_nearest_line(qapp):
    """两行之间的空白（旧实现会变成拖动窗口），应选最近的一行。"""
    layer = _make_layer()  # 第一行 10~30，第二行 40~60

    _press(layer, 60, 37)  # 更靠近第二行

    assert layer.selection_start is not None
    assert layer.selection_start[0] == 1


def test_press_far_below_text_still_drags_window(qapp):
    """远离文字（超出容差）时仍应透传，保证钉图还能拖动。"""
    # 命中容差是按行高放宽的，所以这里用一张更高的图来构造"明显空白"区域
    layer = _make_layer(230, 160)  # 文字底边 y=60

    _press(layer, 60, 140)

    assert layer.selection_start is None
    assert layer._is_pos_on_text(QPoint(60, 140)) is False


# ── 可见性：文字层透明，必须让用户看得到"哪里有字" ──────────────────

def _move(layer, x, y):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    point = QPointF(x, y)
    QApplication.sendEvent(
        layer,
        QMouseEvent(
            QEvent.Type.MouseMove,
            point,
            point,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_flash_text_regions_marks_all_text_then_disappears(qapp):
    """识别完成后标出所有文字区域，随后自动消失（不长期遮挡钉图）。"""
    from PySide6.QtTest import QTest

    layer = _make_layer()
    assert _highlight_pixels(layer) == 0

    layer.flash_text_regions(duration_ms=10)
    assert _highlight_pixels(layer) > 0

    QTest.qWait(80)  # 等一次性定时器到期
    assert layer._flash is False
    assert _highlight_pixels(layer) == 0


def test_hover_marks_text_line_under_cursor(qapp):
    """鼠标移到文字上时标出这一行（让用户看到"按下就会选中"）。"""
    layer = _make_layer()

    _move(layer, 60, 20)  # 第一行文字
    assert layer._hover_item_idx == 0
    assert _highlight_pixels(layer) > 0

    _move(layer, 200, 70)  # 远离文字
    assert layer._hover_item_idx is None
    assert _highlight_pixels(layer) == 0


def test_hover_then_drag_selection_still_paints(qapp):
    """悬浮提示与选择高亮可以共存：拖选后必须看到选择高亮。"""
    layer = _make_layer()

    _move(layer, 60, 20)   # 先悬浮到第一行
    _press(layer, 30, 20)  # 按在第一行
    _move(layer, 100, 20)  # 拖到同一行右侧

    assert layer.selection_start is not None
    assert layer.selection_start != layer.selection_end
    assert _highlight_pixels(layer) > 0
