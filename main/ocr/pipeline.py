# -*- coding: utf-8 -*-
"""pipeline.py - 统一的 OCR 识别流程

所有 OCR 入口共用本模块的一条流程：

    预处理（灰度化 → 小图放大） → recognize_text(dict) → 文本合并 / 坐标还原

使用方：
    * 截图翻译 / 截图总结 / OCR 复制 / 长截图翻译总结 → recognize_image_text / OcrTextThread
    * 钉图文字选择层（需要文字框坐标）        → recognize_image_dict

背景：这些入口过去各自实现 OCR —— OCR 复制自带「小图平滑放大」预处理，
翻译/总结直接把原图送去识别，钉图既不放大也没有坐标还原，于是同一张图
在不同入口识别效果不一致（复制最好、钉图最差）。现在统一到本模块。

预处理由 OCR 设置控制；读取失败时使用与旧 OCR 复制完全一致的默认值，
保证「设置读不到」不会让识别效果变差：

    ocr_grayscale       灰度化，默认 False
    ocr_upscale         小图放大，默认 True
    ocr_upscale_factor  放大倍数上限，默认 4.0（旧 OCR 复制硬编码值）

线程安全：OcrTextThread 只持有 QImage（值类型）与预处理参数快照，
不持有任何 QWidget，识别完成由信号回主线程。
"""

from typing import NamedTuple, Optional, Tuple

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QImage

from core import log_debug, log_error
from core.i18n import make_tr
from core.logger import log_exception

_tr = make_tr("OcrPipeline")


# 选区短边小于该像素（px）时先按比例平滑放大再识别，
# 解决「小选区文字像素太小导致漏识」的问题，保证任意大小的选区都能 OCR。
TARGET_MIN_SIDE = 300

# 放大倍数上限的允许区间；设置项会被夹到这个区间内。
MIN_SCALE_CAP = 1.0
MAX_SCALE_CAP = 4.0

# 与旧 OCR 复制流程完全一致的默认值（默认行为不允许改变）。
DEFAULT_GRAYSCALE = False
DEFAULT_UPSCALE = True
DEFAULT_MAX_SCALE = 4.0


class OcrOptions(NamedTuple):
    """一次 OCR 的预处理参数（在主线程解析后快照给子线程使用）。"""

    grayscale: bool = DEFAULT_GRAYSCALE
    upscale: bool = DEFAULT_UPSCALE
    max_scale: float = DEFAULT_MAX_SCALE


class PreparedImage(NamedTuple):
    """预处理结果：图像 + 实际缩放倍数（用于把文字框坐标还原回原图空间）。"""

    image: QImage
    scale_x: float = 1.0
    scale_y: float = 1.0


def _clamp_scale(value) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_SCALE
    if scale != scale:  # NaN
        return DEFAULT_MAX_SCALE
    return min(MAX_SCALE_CAP, max(MIN_SCALE_CAP, scale))


def resolve_options(config_manager=None) -> OcrOptions:
    """解析 OCR 预处理设置（必须在主线程调用）。

    Args:
        config_manager: 配置管理器；为空时回退到全局 settings 单例。

    Returns:
        OcrOptions；任何读取失败都退化成旧 OCR 复制的默认参数。
    """
    manager = config_manager
    if manager is None:
        try:
            from settings import get_tool_settings_manager

            manager = get_tool_settings_manager()
        except Exception as exc:
            log_debug(f"读取 OCR 设置不可用，使用默认预处理: {exc}", "OcrPipeline")
            return OcrOptions()

    try:
        options = OcrOptions(
            grayscale=bool(manager.get_ocr_grayscale_enabled()),
            upscale=bool(manager.get_ocr_upscale_enabled()),
            max_scale=_clamp_scale(manager.get_ocr_upscale_factor()),
        )
    except Exception as exc:
        log_exception(exc, "读取OCR设置")
        return OcrOptions()

    log_debug(
        f"OCR 预处理设置: 灰度={options.grayscale} "
        f"放大={options.upscale} 放大上限={options.max_scale}",
        "OcrPipeline",
    )
    return options


def _upscale_small_image(image: QImage, max_scale: float) -> Tuple[QImage, float]:
    """短边偏小时按比例平滑放大，倍数封顶 max_scale。

    Returns:
        (处理后的图像, 实际放大倍数)；未放大时倍数为 1.0。
    """
    width, height = image.width(), image.height()
    min_side = min(width, height)
    if min_side <= 0 or min_side >= TARGET_MIN_SIDE:
        return image, 1.0

    scale = min(max_scale, TARGET_MIN_SIDE / min_side)
    if scale <= 1.0:
        return image, 1.0

    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    scaled = image.scaled(
        new_width,
        new_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    log_debug(
        f"OCR 放大: {width}x{height} → {scaled.width()}x{scaled.height()} "
        f"(x{scale:.2f})",
        "OcrPipeline",
    )
    return scaled, scale


def prepare_image_ex(
    image: QImage, options: Optional[OcrOptions] = None
) -> PreparedImage:
    """统一 OCR 预处理，并带回实际缩放倍数（供坐标还原使用）。

    Args:
        image: 待识别图像（QImage）。
        options: 预处理参数；为空则按当前设置解析（主线程调用）。

    Returns:
        PreparedImage；输入为空时原样返回且倍数为 1.0。
    """
    if image is None or image.isNull():
        return PreparedImage(image, 1.0, 1.0)

    if options is None:
        options = resolve_options()

    result = image
    if options.grayscale and not result.isGrayscale():
        result = result.convertToFormat(QImage.Format.Format_Grayscale8)

    scale_x = scale_y = 1.0
    if options.upscale:
        result, scale = _upscale_small_image(result, options.max_scale)
        scale_x = scale_y = scale

    return PreparedImage(result, scale_x, scale_y)


def prepare_image(image: QImage, options: Optional[OcrOptions] = None) -> QImage:
    """统一 OCR 预处理：灰度化 → 小图平滑放大。

    Args:
        image: 待识别图像（QImage）。
        options: 预处理参数；为空则按当前设置解析（主线程调用）。

    Returns:
        预处理后的 QImage；输入为空时原样返回。
    """
    return prepare_image_ex(image, options).image


def _restore_box_coordinates(result, scale_x: float, scale_y: float):
    """把放大后识别出的文字框坐标还原到预处理前的像素空间。

    文字层（钉图选字）直接使用 box 像素坐标，因此放大识别必须先还原坐标，
    否则文字框会按放大倍数整体偏大、点选错位。
    """
    if not isinstance(result, dict) or result.get("code") != 100:
        return result
    if scale_x <= 0 or scale_y <= 0:
        return result
    if abs(scale_x - 1.0) < 1e-6 and abs(scale_y - 1.0) < 1e-6:
        return result

    inv_x, inv_y = 1.0 / scale_x, 1.0 / scale_y
    for item in result.get("data") or []:
        try:
            box = item.get("box")
            if not box:
                continue
            restored = []
            for point in box:
                if len(point) < 2:
                    raise ValueError("bad point")
                restored.append([float(point[0]) * inv_x, float(point[1]) * inv_y])
            item["box"] = restored
        except (TypeError, ValueError, IndexError) as exc:
            # 单个文字框坐标异常时保持原值，避免整层文字选择不可用
            log_exception(exc, "OCR坐标还原")
    return result


def recognize_image_dict(
    image: QImage,
    options: Optional[OcrOptions] = None,
) -> dict:
    """统一 OCR 识别：预处理 → 识别 → 文字框坐标还原到输入图像的像素空间。

    供需要文字框坐标的调用方使用（钉图文字选择层）。线程内调用，
    禁止触碰任何 QWidget。返回结构与 ocr.recognize_text(return_format="dict") 一致：
    {"code": 100, "msg", "data": [{"box", "text", "score"}], "elapse"}。

    Args:
        image: 待识别图像（QImage）。
        options: 预处理参数；为空则按当前设置解析（主线程更安全）。

    Returns:
        识别结果 dict；识别不可用/异常时返回 code != 100 的空结果。
    """
    try:
        from ocr import is_ocr_available, recognize_text

        if not is_ocr_available():
            return {"code": -1, "msg": _tr("OCR is not available"), "data": [], "elapse": 0.0}

        prepared = prepare_image_ex(image, options)
        # need_boxes：必须拿到真实文字框坐标（oneocr 不提供坐标时会自动换用带坐标的引擎）
        result = recognize_text(prepared.image, return_format="dict", need_boxes=True)
        return _restore_box_coordinates(result, prepared.scale_x, prepared.scale_y)
    except Exception as exc:
        log_error(f"OCR 识别异常: {exc}", "OcrPipeline")
        log_exception(exc, "OCR识别")
        return {"code": -1, "msg": f"{_tr('OCR recognition failed')}: {exc}", "data": [], "elapse": 0.0}


def recognize_image_text(
    image: QImage,
    options: Optional[OcrOptions] = None,
) -> Tuple[bool, str]:
    """统一 OCR 识别：预处理 → 识别 → 按阅读顺序合并成文本。

    线程内调用，禁止触碰任何 QWidget。

    Args:
        image: 待识别图像（QImage）。
        options: 预处理参数；为空则按当前设置解析（主线程调用更安全）。

    Returns:
        (是否成功, 识别文本或错误信息)
    """
    try:
        from ocr import is_ocr_available, recognize_text, format_ocr_result_text

        if not is_ocr_available():
            return False, _tr("OCR is not available")

        prepared = prepare_image(image, options)

        # return_format="dict" 获取完整坐标信息，format_ocr_result_text 按阅读顺序合并
        result = recognize_text(prepared, return_format="dict")
        if result and isinstance(result, dict) and result.get("code") == 100:
            text = format_ocr_result_text(result)
            if text and text.strip():
                return True, text

        return False, _tr("No text recognized")
    except Exception as exc:
        log_error(f"OCR 识别异常: {exc}", "OcrPipeline")
        log_exception(exc, "OCR识别")
        return False, f"{_tr('OCR recognition failed')}: {exc}"


class OcrTextThread(QThread):
    """统一 OCR 后台线程。

    只持有 QImage（值类型）与预处理参数快照，不持有任何 QWidget，
    因此调用方在识别期间关闭窗口也安全。
    """

    finished_signal = Signal(bool, str)  # (成功, 识别文本或错误信息)

    def __init__(self, image: QImage, config_manager=None, parent=None):
        super().__init__(parent)
        self._image = image
        # 在主线程解析设置，子线程只读快照，避免跨线程访问配置对象
        self._options = resolve_options(config_manager)
        self._cancelled = False

    def cancel(self):
        """请求取消（OCR 是同步 FFI 调用，无法中断，结果会被调用方丢弃）。"""
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                return
            success, payload = recognize_image_text(self._image, self._options)
            if self._cancelled:
                return
            self.finished_signal.emit(success, payload)
        finally:
            self._image = None  # 释放图像数据
