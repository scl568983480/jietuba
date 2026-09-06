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


def oneocr_recognize_raw(ptr: int, width: int, height: int, stride: int):
    """Run OneOCR on raw BGRA/RGBA pixels and return the same shape as the old API.

    The old native module returned a dict with a ``lines`` key.  Each line is
    ``{"text": str, "bounding_rect": optional, "words": []}``.  We mirror that
    here so existing callers continue to work.
    """
    lines = oneocr_engine.recognize_raw(_runtime_dir(), ptr, width, height, stride)
    return {
        "lines": [
            {
                "text": line,
                "bounding_rect": None,
                "words": [],
            }
            for line in lines
        ]
    }


__all__ = [name for name in globals() if not name.startswith("_")]
