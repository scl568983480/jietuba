"""windows_media_ocr package.

This package combines two OCR backends behind the same API:

* OneOCR (Snipping Tool high-accuracy engine) - preferred when available.
* Windows.Media.Ocr - fallback when OneOCR is unavailable or fails.

The native OneOCR bindings live in the ``oneocr_engine`` extension.
The Windows.Media.Ocr implementation is provided by the bundled
``windows_media_ocr`` native extension (existing binary).

OneOCR 运行时（``oneocr.dll`` + ``oneocr.onemodel`` + ``onnxruntime.dll``，合计约
109 MB）**不再随 exe 打包**：它与 Windows 11「截图工具」自带的那份逐字节相同
（SHA256 一致），所以改为运行时在磁盘上按下面的顺序查找：

    1. 环境变量 ``JIETUBA_ONEOCR_DIR``            调试 / 自定义
    2. exe 同级的 ``oneocr\\`` 目录               绿色版，或用户手动补齐
    3. ``%LOCALAPPDATA%\\Jietuba\\oneocr\\``        用户手动补齐的固定位置
    4. 包内目录（``<wheel>/windows_media_ocr/``）  开发环境、旧版 wheel
    5. 系统「截图工具」安装目录                    注册表 AppModel 仓库 → 枚举 WindowsApps

五处都没有时 ``oneocr_available()`` 返回 False，调用方（``ocr.ocr_manager``）
会自动退回 Windows.Media.Ocr：识别质量略低，但功能不中断。
"""

from __future__ import annotations

import os as _os
import sys as _sys

# Import the Windows.Media.Ocr implementation from the bundled native module.
from .windows_media_ocr import *  # noqa: F401,F403
from . import oneocr_engine  # noqa: E402

try:  # pragma: no cover - 包本身只跑在 Windows 上，这里只为导入期不炸
    import winreg as _winreg
except ImportError:  # pragma: no cover
    _winreg = None


#: OneOCR 缺一不可的运行时文件
RUNTIME_FILES = ("oneocr.dll", "oneocr.onemodel", "onnxruntime.dll")

#: 指定运行时目录的环境变量（优先级最高，便于调试与自定义部署）
ENV_OVERRIDE = "JIETUBA_ONEOCR_DIR"

# 截图工具（Snipping Tool）的包名与 OneOCR 运行时所在的子目录
_SNIPPING_TOOL_PACKAGE_PREFIX = "Microsoft.ScreenSketch_"
_SNIPPING_TOOL_SUBDIRS = ("SnippingTool", "SnippingToolSandbox")

# HKCU 下的 AppModel 仓库：普通用户可读，不需要管理员权限，
# 也不用去枚举权限更紧的 C:\Program Files\WindowsApps。
_APPX_REPOSITORY_KEY = (
    r"Software\Classes\Local Settings\Software\Microsoft\Windows"
    r"\CurrentVersion\AppModel\Repository\Packages"
)
_PACKAGE_ROOT_VALUE = "PackageRootFolder"


def _package_dir() -> str:
    """本包所在目录（开发环境就是 wheel 安装后的目录）。"""
    return _os.path.dirname(_os.path.abspath(__file__))


def has_runtime_files(directory) -> bool:
    """目录里三件套是否齐全；缺任何一个都加载不了引擎。"""
    if not directory:
        return False
    try:
        return all(
            _os.path.isfile(_os.path.join(directory, name))
            for name in RUNTIME_FILES
        )
    except (OSError, ValueError):  # 非法路径、权限不足都当作"没有"
        return False


def _package_version(package_full_name: str) -> str:
    """``Microsoft.ScreenSketch_11.2605.38.0_x64__8wekyb3d8bbwe`` → ``11.2605.38.0``"""
    parts = package_full_name.split("_")
    return parts[1] if len(parts) > 2 else ""


def _version_key(version: str):
    """把 ``11.2605.38.0`` 变成可比较的元组，用于挑最新版本。"""
    key = []
    for part in version.split("."):
        try:
            key.append(int(part))
        except ValueError:
            key.append(0)
    return tuple(key) or (0,)


def _registry_snipping_tool_roots():
    """从 HKCU 的 AppModel 仓库读截图工具的安装根目录。"""
    if _winreg is None:
        return []
    roots = []
    try:
        with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, _APPX_REPOSITORY_KEY) as key:
            subkey_count = _winreg.QueryInfoKey(key)[0]
            for index in range(subkey_count):
                try:
                    name = _winreg.EnumKey(key, index)
                except OSError:
                    continue
                if not name.startswith(_SNIPPING_TOOL_PACKAGE_PREFIX):
                    continue
                try:
                    with _winreg.OpenKey(key, name) as sub:
                        root, _ = _winreg.QueryValueEx(sub, _PACKAGE_ROOT_VALUE)
                except OSError:
                    continue
                if root:
                    roots.append((_package_version(name), root))
    except OSError:
        return []
    return roots


def _windowsapps_snipping_tool_roots():
    """兜底：直接枚举 WindowsApps（Users 对该目录有 ReadAndExecute）。"""
    program_files = _os.environ.get("ProgramFiles") or r"C:\Program Files"
    windows_apps = _os.path.join(program_files, "WindowsApps")
    try:
        entries = _os.listdir(windows_apps)
    except OSError:
        return []
    return [
        (_package_version(name), _os.path.join(windows_apps, name))
        for name in entries
        if name.startswith(_SNIPPING_TOOL_PACKAGE_PREFIX)
    ]


def system_runtime_dirs():
    """系统截图工具里可用的 OneOCR 运行时目录，新版本在前。"""
    found = []
    seen = set()
    for version, root in _registry_snipping_tool_roots() + _windowsapps_snipping_tool_roots():
        for sub in _SNIPPING_TOOL_SUBDIRS:
            directory = _os.path.join(root, sub)
            if directory in seen:
                continue
            seen.add(directory)
            if has_runtime_files(directory):
                found.append((_version_key(version), directory))
    found.sort(key=lambda item: item[0], reverse=True)
    return [directory for _, directory in found]


def search_dirs():
    """按优先级列出候选目录（只列路径，不判断里面有没有运行时文件）。"""
    dirs = []

    override = (_os.environ.get(ENV_OVERRIDE) or "").strip()
    if override:
        dirs.append(override)

    if getattr(_sys, "frozen", False):
        # onefile 打包后 sys.executable 就是 exe 本身
        exe_dir = _os.path.dirname(_os.path.abspath(_sys.executable))
        dirs.append(_os.path.join(exe_dir, "oneocr"))

    local_appdata = _os.environ.get("LOCALAPPDATA")
    if local_appdata:
        dirs.append(_os.path.join(local_appdata, "Jietuba", "oneocr"))

    dirs.append(_package_dir())
    return dirs


_resolved_dir = None
_resolution_done = False


def runtime_dir() -> str:
    """返回实际使用的 OneOCR 运行时目录（结果缓存，避免每次识别都搜盘）。

    都找不到时返回包内目录，此时 ``oneocr_available()`` 为 False，
    调用方应退回 Windows.Media.Ocr。
    """
    global _resolved_dir, _resolution_done
    if _resolution_done:
        return _resolved_dir

    for directory in search_dirs():
        if has_runtime_files(directory):
            _resolved_dir = directory
            break
    else:
        system_dirs = system_runtime_dirs()
        _resolved_dir = system_dirs[0] if system_dirs else _package_dir()

    _resolution_done = True
    return _resolved_dir


def reset_runtime_cache() -> None:
    """清空运行时目录缓存（测试用；正常运行时不需要调用）。"""
    global _resolved_dir, _resolution_done
    _resolved_dir = None
    _resolution_done = False


def oneocr_runtime_dir() -> str:
    """当前使用的 OneOCR 运行时目录（可能不含运行时文件）。"""
    return runtime_dir()


def oneocr_available() -> bool:
    """Return True when the OneOCR runtime can be loaded from this package."""
    return oneocr_engine.available(runtime_dir())


def oneocr_initialize() -> bool:
    """Load OneOCR runtime and return True on success."""
    return oneocr_engine.initialize(runtime_dir())


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
        runtime_dir(), ptr, width, height, stride
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
