"""windows_media_ocr package.

This package combines two OCR backends behind the same API:

* OneOCR (Snipping Tool high-accuracy engine) - preferred when available.
* Windows.Media.Ocr - fallback when OneOCR is unavailable or fails.

The native OneOCR bindings live in the ``oneocr_engine`` extension.
The Windows.Media.Ocr implementation is provided by the bundled
``windows_media_ocr`` native extension (existing binary).
"""

import os as _os

# Import the Windows.Media.Ocr implementation from the bundled native module.
from .windows_media_ocr import *  # noqa: F401,F403
from . import oneocr_engine  # noqa: E402


def _runtime_dir() -> str:
    return _os.path.dirname(_os.path.abspath(__file__))


def oneocr_available() -> bool:
    """Return True when the OneOCR runtime can be loaded from this package."""
    return oneocr_engine.available(_runtime_dir())


def oneocr_initialize() -> bool:
    """Load OneOCR runtime and return True on success."""
    return oneocr_engine.initialize(_runtime_dir())


def oneocr_release() -> None:
    """Release the cached OneOCR engine handle."""
    oneocr_engine.release()


def _rect_from_quad(quad):
    """四点坐标 (x1,y1,...,x4,y4) → {"x1".."y4"}（历史 API 的形状）。

    注意：调用方（ocr_manager）按 x1..y4 四点读取，不能只给轴对齐的两个角。
    """
    if not quad or len(quad) < 8:
        return None
    return {
        "x1": float(quad[0]), "y1": float(quad[1]),
        "x2": float(quad[2]), "y2": float(quad[3]),
        "x3": float(quad[4]), "y3": float(quad[5]),
        "x4": float(quad[6]), "y4": float(quad[7]),
    }


def oneocr_recognize_raw(ptr: int, width: int, height: int, stride: int):
    """Run OneOCR on raw BGRA/RGBA pixels and return the historical dict shape.

    Each line is ``{"text": str, "bounding_rect": dict|None, "quad": list|None,
    "words": [{"text", "bounding_rect", "quad", "confidence"}]}``.

    ``bounding_rect`` 为四点矩形（x1,y1,x2,y2,x3,y3,x4,y4，与旧原生模块一致），
    ``quad`` 为同样的扁平列表；DLL 不提供坐标时均为 None。
    """
    lines = oneocr_engine.recognize_lines(
        _runtime_dir(), ptr, width, height, stride
    )
    return {
        "lines": [
            {
                "text": text,
                "bounding_rect": _rect_from_quad(quad),
                "quad": list(quad) if quad else None,
                "words": [
                    {
                        "text": word_text,
                        "bounding_rect": _rect_from_quad(word_quad),
                        "quad": list(word_quad) if word_quad else None,
                        "confidence": float(confidence),
                    }
                    for word_text, word_quad, confidence in words
                ],
            }
            for text, quad, words in lines
        ]
    }


__all__ = [name for name in globals() if not name.startswith("_")]
