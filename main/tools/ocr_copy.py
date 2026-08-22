"""
ocr_copy.py - OCR 复制功能

复用截图翻译的 OCR 引擎（ocr 模块），将选区文字识别后复制到系统剪贴板，
并以浮动提示反馈结果。

设计要点（与 translation/translation_manager.py 的 OCR 线程保持一致）：
- 在主线程完成 QPixmap → QImage.copy() 转换（QPixmap 不能跨线程访问）
- 子线程只持有 QImage（值类型，线程安全），不持有任何 QWidget
- 识别完成回调在主线程执行，因此可安全地写剪贴板、弹提示
"""
from PySide6.QtCore import QThread, Signal, QObject
from PySide6.QtGui import QImage, QApplication

from core.i18n import make_tr
from core import log_info, log_error, log_debug

_tr = make_tr("OcrCopy")


class _OcrCopyThread(QThread):
    """后台 OCR 识别线程：只持有 QImage（值类型），不持有任何 QWidget。"""

    finished_signal = Signal(bool, str)  # (成功, 识别文本或错误信息)

    def __init__(self, image: QImage):
        super().__init__()
        self._image = image

    def run(self):
        try:
            from ocr import is_ocr_available, recognize_text, format_ocr_result_text

            if not is_ocr_available():
                self.finished_signal.emit(False, _tr("OCR is not available"))
                return

            # return_format="dict" 获取完整坐标信息，format_ocr_result_text 按阅读顺序合并
            result = recognize_text(self._image, return_format="dict")
            if result and isinstance(result, dict) and result.get("code") == 100:
                text = format_ocr_result_text(result)
                if text and text.strip():
                    self.finished_signal.emit(True, text)
                else:
                    self.finished_signal.emit(False, _tr("No text recognized"))
            else:
                self.finished_signal.emit(False, _tr("No text recognized"))
        except Exception as e:
            self.finished_signal.emit(False, f"{_tr('OCR recognition failed')}: {e}")
        finally:
            self._image = None


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

    def copy(self, pixmap):
        """对选区底图做 OCR，识别成功后复制文字到剪贴板。

        Args:
            pixmap: 选区的纯净底图（QPixmap）
        """
        if pixmap is None or pixmap.isNull():
            log_debug("OCR 复制：传入 pixmap 为空，跳过", "OcrCopy")
            return

        # 主线程完成 QPixmap → QImage 深拷贝（QPixmap 不能跨线程访问）
        image = pixmap.toImage().copy()
        if image.isNull():
            log_debug("OCR 复制：QImage 转换失败，跳过", "OcrCopy")
            return

        # 旧线程仍在跑则断开其结果连接（结果丢弃），由其 finished 自行 deleteLater
        if self._thread and self._thread.isRunning():
            try:
                from core.qt_utils import safe_disconnect
                safe_disconnect(self._thread.finished_signal)
            except Exception:
                pass

        self._thread = _OcrCopyThread(image)
        self._thread.finished_signal.connect(self._on_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        log_debug("OCR 复制：已启动后台识别线程", "OcrCopy")

    def _on_finished(self, success: bool, result: str):
        """OCR 完成回调（主线程）。"""
        if success and result:
            try:
                QApplication.clipboard().setText(result)
            except Exception as e:
                log_error(f"OCR 复制：写入剪贴板失败: {e}", "OcrCopy")
                return
            from core.toast import show_toast
            show_toast(_tr("OCR Copy"), _tr("Text copied to clipboard"))
            log_info("OCR 复制成功，文字已复制到剪贴板", "OcrCopy")
        else:
            from ui.dialogs import show_modeless_warning_dialog
            show_modeless_warning_dialog(
                None, _tr("OCR Copy"), result or _tr("No text recognized")
            )
            log_error(f"OCR 复制失败: {result}", "OcrCopy")
