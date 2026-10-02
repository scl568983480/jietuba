# -*- coding: utf-8 -*-
"""统一 OCR 流程（ocr.pipeline）测试。

监控目标：所有 OCR 入口（截图翻译 / 截图总结 / OCR 复制 / 钉图文字层）
必须共用同一条 OCR 流程，且该流程的预处理（灰度化 + 小图放大）以原 OCR 复制的
行为为基准：

* 小选区（短边 < 300px）→ 平滑放大到短边 300px，倍数封顶 4.0
* 大选区 → 原样送出，不做无意义放大
* 需要文字框坐标的调用方（钉图）→ 坐标必须还原回输入图像的像素空间
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
    prepare_image_ex,
    recognize_image_dict,
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


# ── 需要文字框坐标的入口（钉图文字层）────────────────────────────────

def _box_result(box):
    return {"code": 100, "data": [{"box": box, "text": "hello", "score": 1.0}]}


def test_prepare_image_ex_reports_scale(qapp):
    prepared = prepare_image_ex(_image(40, 20))

    assert (prepared.image.width(), prepared.image.height()) == (160, 80)
    assert prepared.scale_x == pytest.approx(DEFAULT_MAX_SCALE)
    assert prepared.scale_y == pytest.approx(DEFAULT_MAX_SCALE)


def test_prepare_image_ex_reports_unit_scale_for_large_image(qapp):
    prepared = prepare_image_ex(_image(1200, 800))

    assert (prepared.scale_x, prepared.scale_y) == (1.0, 1.0)


def test_recognize_image_dict_restores_box_coordinates(qapp, monkeypatch):
    """放大 4 倍识别后，文字框坐标必须还原到原图像素空间（否则钉图选字错位）。"""
    import ocr

    box = [[0, 0], [40, 0], [40, 20], [0, 20]]  # 放大后（4 倍）的坐标
    monkeypatch.setattr(ocr, "is_ocr_available", lambda: True)
    monkeypatch.setattr(ocr, "recognize_text", lambda image, **kw: _box_result(box))

    result = recognize_image_dict(_image(40, 20))

    assert result["code"] == 100
    assert result["data"][0]["box"] == [
        [0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0]
    ]


def test_recognize_image_dict_keeps_coordinates_without_upscale(qapp, monkeypatch):
    import ocr

    box = [[1, 2], [30, 2], [30, 12], [1, 12]]
    monkeypatch.setattr(ocr, "is_ocr_available", lambda: True)
    monkeypatch.setattr(ocr, "recognize_text", lambda image, **kw: _box_result(box))

    result = recognize_image_dict(_image(400, 300))

    assert result["data"][0]["box"] == box


def test_recognize_image_dict_reports_missing_engine(qapp, monkeypatch):
    import ocr

    monkeypatch.setattr(ocr, "is_ocr_available", lambda: False)

    result = recognize_image_dict(_image(40, 20))

    assert result["code"] != 100
    assert result["data"] == []


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


# ── 钉图文字层 ──────────────────────────────────────────────────────

class _FakePinWindow:
    """钉图窗口替身：只需底图与 OCR 取图接口。"""

    def __init__(self, image):
        self._base_pixmap = QPixmap.fromImage(image)
        self.ocr_image = _image(image.width(), image.height())
        self.ocr_calls = 0

    def get_ocr_image(self):
        self.ocr_calls += 1
        return self.ocr_image


class _PinStubThread:
    """钉图 OCR 线程替身。"""

    instances = []

    def __init__(self, image, options=None, parent=None):
        self.image = image
        self.options = options
        self.parent = parent
        self.started = False
        self.finished = _StubSignal()
        _PinStubThread.instances.append(self)

    def start(self):
        self.started = True


def test_pin_ocr_uses_shared_pipeline_and_base_image(monkeypatch):
    """钉图 OCR 必须走统一流程：预处理设置 + 只识别纯底图 + 坐标基准=底图尺寸。"""
    from pin import pin_ocr_manager

    _PinStubThread.instances = []
    monkeypatch.setattr(pin_ocr_manager, "_OCRThread", _PinStubThread)

    window = _FakePinWindow(_image(201, 65))
    config = _FakeConfig(grayscale=True, upscale=True, factor=4.0)
    manager = pin_ocr_manager.PinOCRManager(window, config)

    finished = {}
    monkeypatch.setattr(
        manager, "_on_finished", lambda w, h: finished.update(width=w, height=h)
    )

    manager._start_recognition()

    thread = _PinStubThread.instances[0]
    assert thread.started is True
    # 统一流程的预处理参数已解析并传给线程
    assert thread.options == OcrOptions(grayscale=True, upscale=True, max_scale=4.0)
    # 只识别纯底图（不含绘制/标注），且是原始像素
    assert window.ocr_calls == 1
    assert thread.image is window.ocr_image

    # 坐标基准 = 送进 OCR 的底图尺寸（旧代码用底图尺寸但送的是底图×dpr，dpr>1 时整体偏大）
    thread.finished.receivers[0]()
    assert finished == {"width": 201, "height": 65}


def test_pin_ocr_skips_when_base_image_missing(monkeypatch):
    from pin import pin_ocr_manager

    _PinStubThread.instances = []
    monkeypatch.setattr(pin_ocr_manager, "_OCRThread", _PinStubThread)

    window = _FakePinWindow(_image(100, 50))
    window._base_pixmap = None
    manager = pin_ocr_manager.PinOCRManager(window, _FakeConfig())

    manager._start_recognition()

    assert _PinStubThread.instances == []
    assert window.ocr_calls == 0


def test_pin_window_ocr_image_uses_base_pixmap(qapp):
    """PinWindow.get_ocr_image 取的是纯底图原始像素（未变换时尺寸不变）。"""
    from pin.pin_window import PinWindow

    class _Fake:
        _base_pixmap = QPixmap.fromImage(_image(120, 40))

    image = PinWindow.get_ocr_image(_Fake())

    assert (image.width(), image.height()) == (120, 40)


def test_pin_ocr_thread_boxes_stay_inside_original_image(qapp, monkeypatch):
    """真实钉图线程：放大识别出的文字框必须落在原图范围内（选字不错位）。"""
    import ocr
    from pin import pin_ocr_manager

    source = _image(201, 65)
    captured = {}

    def fake_recognize(image, return_format="dict", **kwargs):
        # 引擎看到的是放大后的图（短边 65 → 4 倍 = 804x260）
        captured["size"] = (image.width(), image.height())
        return {
            "code": 100,
            "data": [{
                "text": "hello",
                "score": 1.0,
                "box": [[0, 0], [804, 0], [804, 260], [0, 260]],
            }],
        }

    monkeypatch.setattr(ocr, "is_ocr_available", lambda: True)
    monkeypatch.setattr(ocr, "recognize_text", fake_recognize)

    thread = pin_ocr_manager._OCRThread(source, OcrOptions())
    thread.run()

    assert captured["size"] == (804, 260)
    box = thread.prepared_items[0].original_box
    max_x = max(p[0] for p in box)
    max_y = max(p[1] for p in box)
    assert max_x <= source.width()
    assert max_y <= source.height()
