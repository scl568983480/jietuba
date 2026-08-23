"""
toast.py - 轻量级浮动提示工具（自动消失）

通用、跨模块复用的浮动提示工具，用于"即点即走"操作的结果反馈
（如 OCR 复制成功、保存完成等）。

用法（必须在主线程调用）：
    from core.toast import show_toast, show_toast_seconds
    show_toast("标题", "内容")                              # 默认 2.2 秒
    show_toast("标题", "内容", duration_ms=1000)            # 显式毫秒
    show_toast("标题", "内容", icon="success")              # 带图标（语义名）
    show_toast_seconds("标题", "内容", seconds=1)           # 按秒设置

特性：
- 宽度随内容自适应（不固定宽度）；
- 可选图标（"success" / "warning" / "info" 或直接传 QIcon）；
- 默认定位到主屏幕中央；
- 自动淡入、停留指定时长后淡出关闭，无需手动管理生命周期。
"""
from PySide6.QtCore import Qt, QRectF, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap, QPen, QColor
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout

from core.theme import get_theme
from core import log_debug


# 持有活动 toast 的强引用，避免局部 _Toast 在 show() 后出作用域被 GC 回收
# （PySide 中无父对象的 QObject 失去 Python 引用会被销毁，导致 toast 不显示）。
_active_toasts = []

def _make_check_icon(size: int = 20) -> QIcon:
    """自绘“绿色圆形 + 白色对勾”图标，作为成功提示。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # 绿色圆形
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#52c41a"))
    p.drawEllipse(1, 1, size - 2, size - 2)
    # 白色对勾
    p.setPen(QPen(Qt.white, max(2, size // 9),
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    s = size
    p.drawLine(int(s * 0.30), int(s * 0.52),
               int(s * 0.44), int(s * 0.67))
    p.drawLine(int(s * 0.44), int(s * 0.67),
               int(s * 0.72), int(s * 0.35))
    p.end()
    return QIcon(pm)


# 语义图标 → 项目内置 svg（找不到时回退到系统标准图标）
_ICON_SVG = {
    "info": "svg/fluent_icons/Info.svg",
    "warning": "svg/fluent_icons/CancelClose.svg",
}
_ICON_FALLBACK = {
    "warning": QWidget().style().standardIcon(
        QWidget().style().StandardPixmap.SP_MessageBoxWarning),
    "info": QWidget().style().standardIcon(
        QWidget().style().StandardPixmap.SP_MessageBoxInformation),
}


def _resolve_icon(icon):
    """把 icon 参数（语义名 / QIcon）解析为可用的 QIcon。"""
    if isinstance(icon, QIcon):
        return icon
    if isinstance(icon, str):
        if icon == "success":
            return _make_check_icon(20)
        name = icon
        try:
            from core.resource_manager import ResourceManager
            svg = _ICON_SVG.get(name)
            if svg:
                path = ResourceManager.get_resource_path(svg)
                if path and __import__("os").path.exists(path):
                    return QIcon(path)
        except Exception:
            pass
        return _ICON_FALLBACK.get(name, QIcon())
    return QIcon()


class _Toast(QWidget):
    """单条浮动提示：绿框白底蓝字 + 左侧打钩图标，淡入停留后淡出关闭。

    风格参考常见网站的成功 toast：白色背景、绿色边框、蓝色文字、
    左侧绿色圆形对勾图标；整条单行显示（不显示标题）。
    """

    # 图标显示尺寸（像素）
    _ICON_SIZE = 18

    # 语义 → 边框色（success 绿框，info 蓝框，warning 橙框）
    _BORDER_COLOR = {
        "success": "#52c41a",
        "info": "#1677ff",
        "warning": "#faad14",
    }

    def __init__(self, title: str, message: str, duration_ms: int = 2200, icon=None, position=None):
        super().__init__()
        self._duration = duration_ms
        self._position_arg = position

        semantic = icon if isinstance(icon, str) else "success"
        self._semantic = semantic

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)

        resolved = _resolve_icon(icon)
        if not resolved.isNull():
            icon_label = QLabel(self)
            icon_label.setPixmap(resolved.pixmap(self._ICON_SIZE, self._ICON_SIZE))
            icon_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            outer.addWidget(icon_label)

        # 单行文本：可显示标题（可选，粗体）+ 内容；无标题时即为单行内容
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        outer.addLayout(text_col, 1)

        if title:
            title_label = QLabel(title, self)
            title_label.setStyleSheet(
                "color: #1677ff; font-size: 12px; font-weight: bold;"
            )
            text_col.addWidget(title_label)

        msg_label = QLabel(message, self)
        msg_label.setStyleSheet("color: #1677ff; font-size: 12px;")
        msg_label.setWordWrap(False)  # 单行显示，不换行
        text_col.addWidget(msg_label)

        # 注：WA_TranslucentBackground=True 会屏蔽 stylesheet 的背景/边框绘制，
        # 故白底绿框改用 paintEvent 自绘（见 paintEvent），不依赖 stylesheet。
        self.adjustSize()
        self._position()

        self.setWindowOpacity(0.0)
        self._fade(1.0, 180)
        QTimer.singleShot(self._duration, self._start_hide)

    def paintEvent(self, event):
        """自绘圆角白底 + 绿框（避免 translucent 背景屏蔽 stylesheet）。"""
        semantic = getattr(self, "_semantic", "success")
        border = self._BORDER_COLOR.get(semantic, "#52c41a")
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 8, 8)
        pen = QPen(QColor(border))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 8, 8)

    def _position(self):
        """定位到主屏幕中央，或给定的参考点附近（如划词位置）。"""
        if self._position_arg is not None:
            ref = self._position_arg
            screen = QGuiApplication.screenAt(ref) or QGuiApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                x = ref.x() + 14
                y = ref.y() + 22
                if x + self.width() > area.right() - 8:
                    x = area.right() - self.width() - 8
                if y + self.height() > area.bottom() - 8:
                    y = ref.y() - self.height() - 16
                x = max(area.left() + 8, x)
                y = max(area.top() + 8, y)
                self.move(x, y)
                return

        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        rect = screen.availableGeometry()
        x = rect.center().x() - self.width() // 2
        y = rect.center().y() - self.height() // 2
        self.move(x, y)

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


def show_toast(title: str, message: str, duration_ms: int = 2200, icon=None, position=None):
    """显示一条自动消失的浮动提示（主线程）。

    Args:
        title: 提示标题（可为空字符串）
        message: 提示内容
        duration_ms: 提示停留时长（毫秒），默认 2200ms。
                    控制 toast 显示几秒用此参数（如 1 秒传 1000）。
        icon: 可选图标。接受语义名 "success" / "warning" / "info"，
              或直接传入 QIcon；为 None 则不显示图标。
        position: 可选参考点 (QPoint)。提供时 toast 出现在其附近（如划词位置）；
                  为 None 则定位到主屏幕中央。
    """
    try:
        toast = _Toast(title, message, duration_ms, icon=icon, position=position)
        # 保留强引用，防止 GC 回收；toast 关闭后由 destroyed 信号移除
        _active_toasts.append(toast)
        toast.destroyed.connect(
            lambda *_: _remove_toast(toast)
        )
        toast.show()
        return toast
    except Exception as e:
        log_debug(f"显示 toast 失败: {e}", "Toast")
        return None


def _remove_toast(toast):
    """toast 销毁后从活动列表移除引用。"""
    try:
        _active_toasts.remove(toast)
    except ValueError:
        pass


def show_toast_seconds(title: str, message: str, seconds: float = 2.2, icon=None, position=None):
    """按秒显示一条自动消失的浮动提示（主线程便捷封装）。

    Args:
        seconds: 提示停留时长（秒），默认 2.2 秒。
        icon: 可选图标（同 show_toast）。
        position: 可选参考点 (QPoint)，同 show_toast。
    """
    return show_toast(
        title, message, duration_ms=int(round(seconds * 1000)), icon=icon, position=position
    )
