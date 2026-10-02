"""
ocr_copy.py - OCR 复制功能

复用统一的 OCR 流程（ocr.pipeline，与截图翻译/总结同一条），
将选区文字识别后复制到系统剪贴板，并以浮动提示反馈结果。

设计要点：
- 在主线程完成 QPixmap → QImage.copy() 转换（QPixmap 不能跨线程访问）
- 识别交给 ocr.pipeline.OcrTextThread（只持有 QImage，不持有任何 QWidget）
- 识别完成回调在主线程执行，因此可安全地写剪贴板、弹提示
"""
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from core.i18n import make_tr
from core import log_info, log_error, log_debug

_tr = make_tr("OcrCopy")


class OcrCopyController(QObject):
    """OCR 复制控制器（单例，主线程亲和）。

    负责：启动后台 OCR 线程，识别完成后在主线程把文字写入剪贴板并提示。
    作为 QObject 单例持有线程引用，避免被垃圾回收。
    """

    _instance = None

    def __init__(self):
        super().__init__()
        self._thread = None

    @classmethod
    def instance(cls) -> "OcrCopyController":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def copy(self, pixmap, config_manager=None):
        """对选区底图做 OCR，识别成功后复制文字到剪贴板。

        Args:
            pixmap: 选区的纯净底图（QPixmap）
            config_manager: 配置管理器（读取 OCR 预处理设置；可为空）
        """
        if pixmap is None or pixmap.isNull():
            log_debug("OCR 复制：传入 pixmap 为空，跳过", "OcrCopy")
            return

        # 主线程完成 QPixmap → QImage 深拷贝（QPixmap 不能跨线程访问）
        image = pixmap.toImage().copy()
        if image.isNull():
            log_debug("OCR 复制：QImage 转换失败，跳过", "OcrCopy")
            return

        # 旧线程处理：先置空引用，避免线程自毁后留下悬空 C++ 对象引用
        old_thread = self._thread
        self._thread = None
        if old_thread is not None:
            try:
                # 若旧线程仍存活，断开其结果连接（结果丢弃），让其 finished 自行 deleteLater
                if old_thread.isRunning():
                    from core.qt_utils import safe_disconnect
                    safe_disconnect(old_thread.finished_signal)
            except RuntimeError:
                # C++ 对象已销毁（已自毁），无需处理
                pass

        # 统一 OCR 流程（ocr.pipeline），与截图翻译/总结完全一致
        from ocr.pipeline import OcrTextThread

        self._thread = OcrTextThread(image, config_manager)
        self._thread.finished_signal.connect(self._on_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        # 线程结束后清空引用，防止 self._thread 指向已销毁对象（二次调用 isRunning 会崩）
        self._thread.finished.connect(self._clear_thread)
        self._thread.start()
        log_debug("OCR 复制：已启动后台识别线程", "OcrCopy")

    def _clear_thread(self):
        """线程结束（finished → deleteLater）后清空引用，避免悬空 C++ 对象引用。"""
        self._thread = None

    def _on_finished(self, success: bool, result: str):
        """OCR 完成回调（主线程）。"""
        if success and result:
            try:
                QApplication.clipboard().setText(result)
            except Exception as e:
                log_error(f"OCR 复制：写入剪贴板失败: {e}", "OcrCopy")
                return
            from core.toast import show_toast
            show_toast(
                "",
                _tr("Text copied to clipboard"),
                duration_ms=1000,
                icon="success",
            )
            log_info("OCR 复制成功，文字已复制到剪贴板", "OcrCopy")
        else:
            from ui.dialogs import show_modeless_warning_dialog
            show_modeless_warning_dialog(
                None, _tr("OCR Copy"), result or _tr("No text recognized")
            )
            log_error(f"OCR 复制失败: {result}", "OcrCopy")
