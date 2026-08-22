"""
toast.py - 轻量级浮动提示（自动消失）

用于 OCR 复制等"即点即走"操作的结果反馈。
必须在主线程调用 show_toast()。
"""
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout

from core.theme import get_theme
from core import log_debug


class _Toast(QWidget):
    """单条浮动提示：圆角卡片 + 主题色描边，淡入后停留一段时间再淡出关闭。"""

    def __init__(self, title: str, message: str, duration_ms: int = 2200):
        super().__init__()
        self._duration = duration_ms

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        theme_hex = get_theme().theme_color_hex

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        if title:
            title_label = QLabel(title)
            title_label.setStyleSheet(
                f"color: {theme_hex}; font-size: 13px; font-weight: bold;"
            )
            layout.addWidget(title_label)

        msg_label = QLabel(message)
        msg_label.setStyleSheet("color: #ffffff; font-size: 13px;")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        self.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(30, 30, 30, 0.92);
                border: 1px solid {theme_hex};
                border-radius: 10px;
            }}
        """)

        self.adjustSize()
        self._position()

        self.setWindowOpacity(0.0)
        self._fade(1.0, 180)
        QTimer.singleShot(self._duration, self._start_hide)

    def _position(self):
        """定位到主屏幕底部居中。"""
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        rect = screen.availableGeometry()
        x = rect.center().x() - self.width() // 2
        y = rect.bottom() - self.height() - 40
        self.move(max(rect.left(), x), max(rect.top(), y))

    def _fade(self, to: float, duration: int):
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(duration)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(to)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._anim = anim  # 保持引用，避免被提前回收

    def _start_hide(self):
        self._fade(0.0, 220)
        self._anim.finished.connect(self.close)


def show_toast(title: str, message: str, duration_ms: int = 2200):
    """显示一条自动消失的浮动提示（主线程）。"""
    try:
        toast = _Toast(title, message, duration_ms)
        toast.show()
        return toast
    except Exception as e:
        log_debug(f"显示 toast 失败: {e}", "Toast")
        return None
