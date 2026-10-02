# -*- coding: utf-8 -*-
"""统一 OCR 流程（ocr.pipeline）测试。

监控目标：截图翻译 / 截图总结 / OCR 复制 必须共用同一条 OCR 流程，
且该流程的预处理（灰度化 + 小图放大）以原 OCR 复制的行为为基准：

* 小选区（短边 < 300px）→ 平滑放大到短边 300px，倍数封顶 4.0
* 大选区 → 原样送出，不做无意义放大
* 设置读取失败 → 退化成上述默认值，识别效果不变差
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from ocr import pipeline as pipeline_module
from ocr.pipeline import (
    DEFAULT_MAX_SCALE,
    OcrOptions,
    TARGET_MIN_SIDE,
    prepare_image,
    recognize_image_text,
    resolve_options,
)


class _FakeConfig:
    """只提供 OCR 预处理三个设置项的假配置。"""

    def __init__(self, grayscale=False, upscale=True, factor=DEFAULT_MAX_SCALE):
        self._values = (grayscale, upscale, factor)

    def get_ocr_grayscale_enabled(self):
        return self._values[0]

    def get_ocr_upscale_enabled(self):
        return self._values[1]

    def get_ocr_upscale_factor(self):
        return self._values[2]


class _BrokenConfig:
    """所有读取都抛异常，模拟配置不可用。"""

    def get_ocr_grayscale_enabled(self):
        raise RuntimeError("boom")


def _image(width, height, fmt=QImage.Format.Format_ARGB32):
    image = QImage(width, height, fmt)
    image.fill(Qt.GlobalColor.white)
    return image


# ── 预处理 ──────────────────────────────────────────────────────────

def test_small_selection_is_upscaled_to_target_min_side(qapp):
    """小选区按 OCR 复制的原行为放大：短边补到 300px（默认上限 4 倍）。"""
    # 40x20 → 短边 20，需要 15 倍，被默认上限 4.0 截断
    result = prepare_image(_image(40, 20))

    assert (result.width(), result.height()) == (160, 80)
    assert result.width() / 40 == pytest.approx(DEFAULT_MAX_SCALE)


def test_medium_selection_upscales_only_up_to_cap(qapp):
    """200x100：短边 100，需要 3 倍即达标，不触发上限。"""
    result = prepare_image(_image(200, 100))

    assert min(result.width(), result.height()) == TARGET_MIN_SIDE
    assert (result.width(), result.height()) == (600, 300)


def test_large_selection_is_not_resized(qapp):
    """大选区不做任何放大（避免无意义的内存与耗时开销）。"""
    source = _image(1200, 800)

    result = prepare_image(source)

    assert (result.width(), result.height()) == (1200, 800)


def test_max_scale_option_caps_the_factor(qapp):
    """放大上限设置生效：上限 2.0 时小选区只放大 2 倍。"""
    result = prepare_image(
        _image(40, 20), OcrOptions(grayscale=False, upscale=True, max_scale=2.0)
    )

    assert (result.width(), result.height()) == (80, 40)


def test_upscale_can_be_disabled(qapp):
    result = prepare_image(
        _image(40, 20), OcrOptions(grayscale=False, upscale=False, max_scale=4.0)
    )

    assert (result.width(), result.height()) == (40, 20)


def test_grayscale_option_converts_image(qapp):
    result = prepare_image(
        _image(40, 20), OcrOptions(grayscale=True, upscale=False, max_scale=4.0)
    )

    assert result.isGrayscale()


def test_null_image_is_returned_unchanged(qapp):
    assert prepare_image(QImage()).isNull()


# ── 设置解析 ────────────────────────────────────────────────────────

def test_resolve_options_reads_settings():
    options = resolve_options(_FakeConfig(grayscale=True, upscale=False, factor=3.0))

    assert options.grayscale is True
    assert options.upscale is False
    assert options.max_scale == pytest.approx(3.0)


@pytest.mark.parametrize(
    "raw, expected",
    [(99.0, 4.0), (0.1, 1.0), (2.5, 2.5), ("bad", DEFAULT_MAX_SCALE)],
)
def test_resolve_options_clamps_scale(raw, expected):
    assert resolve_options(_FakeConfig(factor=raw)).max_scale == pytest.approx(expected)


def test_resolve_options_falls_back_to_ocr_copy_defaults():
    """配置不可用时退化成旧 OCR 复制的默认参数，识别效果不变差。"""
    options = resolve_options(_BrokenConfig())

    assert options == OcrOptions(
        grayscale=False, upscale=True, max_scale=DEFAULT_MAX_SCALE
    )


# ── 识别 ────────────────────────────────────────────────────────────

def test_recognize_image_text_uses_preprocessed_image(qapp, monkeypatch):
    """识别前必须走统一预处理：送进引擎的应是放大后的图。"""
    import ocr

    seen = {}

    def fake_recognize(image, return_format="dict"):
        seen["size"] = (image.width(), image.height())
        return {"code": 100, "data": [{"text": "hello"}]}

    monkeypatch.setattr(ocr, "is_ocr_available", lambda: True)
    monkeypatch.setattr(ocr, "recognize_text", fake_recognize)

    success, text = recognize_image_text(_image(40, 20))

    assert success is True
    assert text == "hello"
    assert seen["size"] == (160, 80)


def test_recognize_image_text_reports_missing_engine(qapp, monkeypatch):
    import ocr

    monkeypatch.setattr(ocr, "is_ocr_available", lambda: False)

    success, message = recognize_image_text(_image(40, 20))

    assert success is False
    assert message


def test_recognize_image_text_reports_no_text(qapp, monkeypatch):
    import ocr

    monkeypatch.setattr(ocr, "is_ocr_available", lambda: True)
    monkeypatch.setattr(ocr, "recognize_text", lambda image, **kw: {"code": 100, "data": []})

    success, message = recognize_image_text(_image(400, 300))

    assert success is False
    assert message


# ── 三条入口共用同一流程 ─────────────────────────────────────────────

class _StubThread:
    """替身：只记录构造参数与连接/启动调用。"""

    instances = []

    def __init__(self, image, config_manager=None, parent=None):
        self.image = image
        self.config_manager = config_manager
        self.started = False
        self.finished_signal = _StubSignal()
        self.finished = _StubSignal()
        _StubThread.instances.append(self)

    def isRunning(self):
        return False

    def start(self):
        self.started = True

    def deleteLater(self):
        pass

    def cancel(self):
        pass


class _StubSignal:
    def __init__(self):
        self.receivers = []

    def connect(self, slot):
        self.receivers.append(slot)


@pytest.fixture
def stub_pipeline_thread(monkeypatch):
    _StubThread.instances = []
    monkeypatch.setattr(pipeline_module, "OcrTextThread", _StubThread)
    return _StubThread


def test_translation_and_summary_use_shared_ocr_thread(qapp, stub_pipeline_thread):
    """截图翻译 / 总结（同一入口）必须走统一 OCR 线程。"""
    from translation.translation_manager import TranslationManager

    manager = TranslationManager()
    manager._start_ocr_thread(QPixmap.fromImage(_image(400, 300)))

    assert len(stub_pipeline_thread.instances) == 1
    thread = stub_pipeline_thread.instances[0]
    assert thread.started is True
    assert thread.image.width() == 400


def test_ocr_copy_uses_shared_ocr_thread(qapp, stub_pipeline_thread):
    """OCR 复制必须走统一 OCR 线程，并把配置传下去。"""
    # tools 与 canvas 互相引用：按应用的真实顺序先导入 canvas，tools 包才能完成初始化
    import canvas  # noqa: F401
    from tools.ocr_copy import OcrCopyController

    config = _FakeConfig()
    OcrCopyController().copy(QPixmap.fromImage(_image(400, 300)), config)

    assert len(stub_pipeline_thread.instances) == 1
    thread = stub_pipeline_thread.instances[0]
    assert thread.started is True
    assert thread.config_manager is config
