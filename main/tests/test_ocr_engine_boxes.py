# -*- coding: utf-8 -*-
"""OCR 引擎选择与文字框坐标测试。

背景（用户反馈：钉图里"看不到文字、也选不中文字"）：
默认的高精度引擎 oneocr 的 Rust 接口**不返回文字框坐标**
（`bounding_rect` 恒为 None、`words` 为空），代码里用 `[[0,0],[100,0],[100,20],[0,20]]`
占位。于是钉图文字层的文字框全被挤在左上角一个小方块里 —— 文本数据没问题
（Ctrl+A + 复制能拿到全文），但鼠标怎么点都命不中，看起来就是"没有文字/无法选中"。

修复：需要坐标的调用方（need_boxes=True）改用带坐标的引擎：
PP-OCR（实测最稳）→ Windows.Media.Ocr（兜底）；只要文本的调用方（翻译/复制）不受影响。
"""

import pytest
from PySide6.QtGui import QImage

from ocr import ocr_manager as ocr_module
from ocr.ocr_manager import OCRManager


def _image(width=40, height=20):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    return image


@pytest.fixture
def manager():
    return OCRManager()


@pytest.fixture
def no_engines(monkeypatch):
    """让两个带坐标的引擎都不可用。"""
    monkeypatch.setattr(ocr_module, "PP_RUST_AVAILABLE", False)
    monkeypatch.setattr(ocr_module, "WINDOWS_OCR_AVAILABLE", False)


def _ok_result(text="hello"):
    return {"code": 100, "msg": "成功",
            "data": [{"box": [[1, 2], [30, 2], [30, 12], [1, 12]],
                      "text": text, "score": 1.0}],
            "elapse": 0.0}


def _empty_result():
    return {"code": 100, "msg": "未识别到文字", "data": [], "elapse": 0.0}


# ── need_boxes 的引擎选择 ───────────────────────────────────────────

def test_need_boxes_prefers_ppocr(manager, monkeypatch):
    """PP-OCR 可用时应优先用它（有坐标、中英混排最稳）。"""
    monkeypatch.setattr(ocr_module, "PP_RUST_AVAILABLE", True)
    monkeypatch.setattr(ocr_module, "WINDOWS_OCR_AVAILABLE", True)
    monkeypatch.setattr(manager, "_recognize_with_ppocr_rust",
                        lambda pixmap, fmt: _ok_result("ppocr"))

    result = manager._recognize_with_boxes(_image(), "dict")

    assert result["data"][0]["text"] == "ppocr"


def test_need_boxes_falls_back_to_windows_media_ocr(manager, monkeypatch):
    """PP-OCR 没识别到内容时，退到同样带坐标的 Windows.Media.Ocr。"""
    monkeypatch.setattr(ocr_module, "PP_RUST_AVAILABLE", True)
    monkeypatch.setattr(ocr_module, "WINDOWS_OCR_AVAILABLE", True)
    monkeypatch.setattr(manager, "_recognize_with_ppocr_rust",
                        lambda pixmap, fmt: _empty_result())
    seen = {}

    def fake_windows(pixmap, fmt, need_boxes=False):
        seen["need_boxes"] = need_boxes
        return _ok_result("winocr")

    monkeypatch.setattr(manager, "_recognize_with_windows_ocr", fake_windows)

    result = manager._recognize_with_boxes(_image(), "dict")

    assert result["data"][0]["text"] == "winocr"
    assert seen["need_boxes"] is True


def test_need_boxes_returns_none_without_box_engine(manager, no_engines):
    """没有带坐标的引擎时返回 None，调用方回退默认引擎（至少能拿到文本）。"""
    assert manager._recognize_with_boxes(_image(), "dict") is None


def test_recognize_pixmap_falls_back_when_no_box_engine(manager, monkeypatch, no_engines):
    """need_boxes=True 但没有坐标引擎时，不应报错，仍走默认引擎。"""
    monkeypatch.setattr(manager, "_current_engine", manager.ENGINE_WINDOWS_OCR)
    seen = {}

    def fake_default(pixmap, fmt, need_boxes=False):
        seen["need_boxes"] = need_boxes
        return _ok_result("default")

    monkeypatch.setattr(manager, "_recognize_with_windows_ocr", fake_default)

    result = manager.recognize_pixmap(_image(), "dict", need_boxes=True)

    assert result["data"][0]["text"] == "default"
    assert seen["need_boxes"] is False


# ── oneocr 缺少坐标时的行为 ─────────────────────────────────────────

class _FakeOneOcr:
    """替身：只返回文本，不含坐标（与真实 Rust 接口一致）。"""

    @staticmethod
    def oneocr_recognize_raw(addr, w, h, stride):
        return {"lines": [{"text": "HELLO", "bounding_rect": None, "words": []}]}


@pytest.fixture
def fake_oneocr(monkeypatch):
    monkeypatch.setattr(ocr_module, "windows_media_ocr", _FakeOneOcr)
    monkeypatch.setattr(ocr_module, "WINDOS_OCR_AVAILABLE", True)


def test_oneocr_placeholder_box_when_boxes_not_needed(manager, fake_oneocr):
    """只要文本时，沿用占位框（保证翻译/复制的文本行为不变）。"""
    result = manager._recognize_with_windos_ocr(_image(), "dict", need_boxes=False)

    assert result["code"] == 100
    assert result["data"][0]["box"] == [[0, 0], [100, 0], [100, 20], [0, 20]]
    assert result["data"][0]["text"] == "HELLO"


def test_oneocr_fails_when_boxes_needed(manager, fake_oneocr):
    """需要坐标时报失败，让上层换用带坐标的引擎（而不是给出错误坐标）。"""
    result = manager._recognize_with_windos_ocr(_image(), "dict", need_boxes=True)

    assert result["code"] != 100


# ── 统一流程请求坐标 ────────────────────────────────────────────────

def test_recognize_image_dict_requests_boxes(qapp, monkeypatch):
    """钉图走的 recognize_image_dict 必须声明需要坐标。"""
    import ocr
    from ocr import pipeline

    seen = {}

    def fake_recognize(image, return_format="dict", **kwargs):
        seen["need_boxes"] = kwargs.get("need_boxes")
        return _ok_result()

    monkeypatch.setattr(ocr, "is_ocr_available", lambda: True)
    monkeypatch.setattr(ocr, "recognize_text", fake_recognize)

    pipeline.recognize_image_dict(_image(400, 300))

    assert seen["need_boxes"] is True
