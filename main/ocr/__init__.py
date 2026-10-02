"""
OCR 模块 - 文字识别功能

支持 OCR 引擎：
- windows_media_ocr: Windows 系统自带 OCR API (轻量级)
- win_advanced: 高精度引擎 (通过 Rust FFI 调用系统组件)

主要功能：
- OCRManager: OCR 管理器（单例模式）
- is_ocr_available: 检查 OCR 是否可用
- get_available_engines: 获取可用的 OCR 引擎列表
- set_ocr_engine: 设置当前使用的 OCR 引擎
- get_current_engine: 获取当前使用的 OCR 引擎
- initialize_ocr: 初始化 OCR 引擎
- recognize_text: 识别图像中的文字

统一 OCR 流程（截图翻译 / 截图总结 / OCR 复制 / 长截图翻译总结 共用）：
- recognize_image_text: 预处理 + 识别 + 合并文本
- OcrTextThread: 统一的后台识别线程
- prepare_image: 统一预处理（灰度化 + 小图放大）

注意：OCRTextLayer（钉图文字选择层）已移至 pin 模块

使用示例：
    from ocr import is_ocr_available, get_available_engines, set_ocr_engine, initialize_ocr, recognize_text
    
    if is_ocr_available():
        engines = get_available_engines()
        print(f"可用引擎: {engines}")
        
        set_ocr_engine("windows_media_ocr")  # 切换到 Windows OCR
        initialize_ocr()
        result = recognize_text(pixmap, return_format="text")
        print(result)
"""

from .ocr_manager import (
    OCRManager,
    is_ocr_available,
    get_available_engines,
    set_ocr_engine,
    get_current_engine,
    initialize_ocr,
    recognize_text,
    release_ocr_engine,
    get_ocr_memory_status,
    format_ocr_result_text
)

from .pipeline import (
    OcrOptions,
    OcrTextThread,
    prepare_image,
    recognize_image_text,
    resolve_options,
)

__all__ = [
    'OCRManager',
    'is_ocr_available',
    'get_available_engines',
    'set_ocr_engine',
    'get_current_engine',
    'initialize_ocr',
    'recognize_text',
    'release_ocr_engine',
    'get_ocr_memory_status',
    'format_ocr_result_text',
    'OcrOptions',
    'OcrTextThread',
    'prepare_image',
    'recognize_image_text',
    'resolve_options',
]
 