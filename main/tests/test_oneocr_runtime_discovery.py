# -*- coding: utf-8 -*-
"""OneOCR 运行时目录查找测试。

背景：``oneocr.dll`` / ``oneocr.onemodel`` / ``onnxruntime.dll`` 合计约 109 MB，
一度占到打包后 exe 的 58%（压缩后 77 MB）。它们与 Windows 11「截图工具」自带的
那三个文件**逐字节相同**（SHA256 一致），因此改为运行时在磁盘上就地查找，
不再随 exe 分发：

    JIETUBA_ONEOCR_DIR → exe 同级 oneocr/ → %LOCALAPPDATA%\\Jietuba\\oneocr
    → 包内目录 → 系统截图工具安装目录

这里覆盖查找顺序、版本优先级、缺文件时的降级行为，以及"系统那份真的能跑"。
"""

import ctypes
import os
import sys

import pytest

import windows_media_ocr as wmo


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """每个用例都从干净的缓存开始（否则第一个用例的结果会被后面的用例复用）。"""
    monkeypatch.setattr(wmo, "_resolved_dir", None, raising=False)
    monkeypatch.setattr(wmo, "_resolution_done", False, raising=False)
    yield


def _make_runtime(directory):
    """造一个三件套齐全的假运行时目录，返回其路径。"""
    path = str(directory)
    os.makedirs(path, exist_ok=True)
    for name in wmo.RUNTIME_FILES:
        with open(os.path.join(path, name), "wb") as fh:
            fh.write(b"stub")
    return path


# ── 基础判定 ────────────────────────────────────────────────────────

def test_has_runtime_files_needs_all_three(tmp_path):
    """缺任何一个都不能算"有运行时"。"""
    directory = tmp_path / "oneocr"
    directory.mkdir()
    for name in wmo.RUNTIME_FILES[:-1]:
        (directory / name).write_bytes(b"x")
    assert not wmo.has_runtime_files(str(directory))

    (directory / wmo.RUNTIME_FILES[-1]).write_bytes(b"x")
    assert wmo.has_runtime_files(str(directory))


def test_has_runtime_files_rejects_empty_and_missing(tmp_path):
    assert not wmo.has_runtime_files("")
    assert not wmo.has_runtime_files(None)
    assert not wmo.has_runtime_files(str(tmp_path / "不存在"))


def test_package_version_parsing():
    assert (
        wmo._package_version("Microsoft.ScreenSketch_11.2605.38.0_x64__8wekyb3d8bbwe")
        == "11.2605.38.0"
    )
    assert wmo._package_version("Microsoft.ScreenSketch") == ""
    assert wmo._version_key("11.2605.38.0") > wmo._version_key("10.2004.1.0")
    assert wmo._version_key("坏数据") == (0,)


# ── 查找顺序 ────────────────────────────────────────────────────────

def test_env_override_wins(tmp_path, monkeypatch):
    override = _make_runtime(tmp_path / "custom")
    monkeypatch.setenv(wmo.ENV_OVERRIDE, override)
    assert wmo.runtime_dir() == override


def test_exe_sidecar_wins_over_package_dir(tmp_path, monkeypatch):
    """打包后 exe 同级的 oneocr/ 优先于包内目录（同时也验证了 frozen 分支）。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    sidecar = _make_runtime(exe_dir / "oneocr")

    monkeypatch.delenv(wmo.ENV_OVERRIDE, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "jietuba.exe"))

    assert wmo.runtime_dir() == sidecar


def test_localappdata_dir_used_when_no_sidecar(tmp_path, monkeypatch):
    local = _make_runtime(tmp_path / "local" / "Jietuba" / "oneocr")
    monkeypatch.delenv(wmo.ENV_OVERRIDE, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert wmo.runtime_dir() == local


def test_system_dir_used_when_nothing_else_has_files(tmp_path, monkeypatch):
    """exe 旁、用户目录、包内都没有 → 用系统截图工具那份。"""
    system = _make_runtime(tmp_path / "system")
    monkeypatch.setattr(wmo, "search_dirs", lambda: [])
    monkeypatch.setattr(wmo, "system_runtime_dirs", lambda: [system])
    assert wmo.runtime_dir() == system


def test_falls_back_to_package_dir_when_nowhere_found(monkeypatch):
    """哪都没有时返回包内目录，此时 oneocr_available() 为 False，调用方退回系统 OCR。"""
    monkeypatch.setattr(wmo, "search_dirs", lambda: [])
    monkeypatch.setattr(wmo, "system_runtime_dirs", lambda: [])
    assert wmo.runtime_dir() == wmo._package_dir()


def test_search_dirs_order(tmp_path, monkeypatch):
    override = str(tmp_path / "override")
    monkeypatch.setenv(wmo.ENV_OVERRIDE, override)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "jietuba.exe"))

    assert wmo.search_dirs() == [
        override,
        str(tmp_path / "app" / "oneocr"),
        str(tmp_path / "local" / "Jietuba" / "oneocr"),
        wmo._package_dir(),
    ]


# ── 系统截图工具目录的挑选 ──────────────────────────────────────────

def test_system_dirs_prefer_newest_version(tmp_path, monkeypatch):
    old = _make_runtime(tmp_path / "old" / "SnippingTool")
    new = _make_runtime(tmp_path / "new" / "SnippingTool")
    monkeypatch.setattr(
        wmo,
        "_registry_snipping_tool_roots",
        lambda: [
            ("10.2004.1.0", str(tmp_path / "old")),
            ("11.2605.38.0", str(tmp_path / "new")),
        ],
    )
    monkeypatch.setattr(wmo, "_windowsapps_snipping_tool_roots", lambda: [])
    assert wmo.system_runtime_dirs() == [new, old]


def test_system_dirs_skip_incomplete_and_duplicate(tmp_path, monkeypatch):
    broken = tmp_path / "broken"
    (broken / "SnippingTool").mkdir(parents=True)
    (broken / "SnippingTool" / "oneocr.dll").write_bytes(b"x")  # 缺模型文件

    good = _make_runtime(tmp_path / "good" / "SnippingTool")
    roots = lambda: [("11.0.0.0", str(broken)), ("11.0.0.0", str(tmp_path / "good"))]
    monkeypatch.setattr(wmo, "_registry_snipping_tool_roots", roots)
    # 枚举结果与注册表重复，去重后只应出现一次
    monkeypatch.setattr(wmo, "_windowsapps_snipping_tool_roots", roots)
    assert wmo.system_runtime_dirs() == [good]


# ── 真机验证：系统那份运行时确实能加载、能识别 ──────────────────────

SYSTEM_DIRS = wmo.system_runtime_dirs()


@pytest.mark.skipif(
    not SYSTEM_DIRS, reason="本机未安装 Windows 截图工具，没有可用的 OneOCR 运行时"
)
def test_system_runtime_loads_and_recognizes():
    """不做任何拷贝，直接用系统截图工具的运行时跑一次识别。"""
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")

    runtime = SYSTEM_DIRS[0]
    assert wmo.has_runtime_files(runtime)
    assert wmo.oneocr_engine.available(runtime)

    font_path = os.path.join(
        os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeui.ttf"
    )
    if not os.path.isfile(font_path):
        pytest.skip("缺少测试字体 segoeui.ttf")

    image = Image.new("RGB", (720, 110), "white")
    ImageDraw.Draw(image).text(
        (20, 24), "OneOCR 2026", font=ImageFont.truetype(font_path, 48), fill="black"
    )

    width, height = image.size
    raw = image.tobytes("raw", "BGRX")  # ARGB32 小端 = 内存里的 BGRA
    buf = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
    try:
        lines = wmo.oneocr_engine.recognize_lines(
            runtime, ctypes.addressof(buf), width, height, width * 4
        )
    finally:
        wmo.oneocr_release()

    texts = [text for text, _quad, _words in lines]
    assert any("2026" in text for text in texts), texts
    assert any(quad for _text, quad, _words in lines), "带坐标的坐标接口应当可用"
