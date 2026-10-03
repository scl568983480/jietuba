"""
截图吧 - PP-OCR 版本打包脚本
使用 onefile 模式（单文件）

使用方式：
  直接运行此脚本，打包生成 jietuba.exe，携带 ppocr_rust，外置 models/ 模型目录

OneOCR 运行时（oneocr.dll / oneocr.onemodel / onnxruntime.dll ≈ 109 MB）**不打包**：
它们与 Windows 11「截图工具」自带的组件逐字节相同，运行时由 windows_media_ocr
从系统目录（或 exe 同级 oneocr/）查找，找不到则退回 Windows.Media.Ocr。

输出：
  dist/jietuba.exe
  dist/models/PP-OCRv6_det_small.onnx
  dist/models/PP-OCRv6_rec_small.onnx
"""
import PyInstaller.__main__
from pathlib import Path
import sys
import os

# 路径配置
SCRIPT_DIR = Path(__file__).resolve().parent
# 本脚本位于仓库根目录，所有素材（main/、svg/、托盘.ico、models/）均在其同级，
# 因此 REPO_DIR 即脚本所在目录，而非其父目录。
REPO_DIR = SCRIPT_DIR
MAIN_APP = "main/main_app.py"
SVG_DIR = "svg"
BUILD_DIR = "build"
DIST_DIR = "dist"
EXE_NAME = "jietuba"

# 翻译文件
TRANSLATIONS_DIR = "main/translations"

# 离线词典（ECDICT，英→中）—— 由 build_ecdict.py 生成，打包时必须存在
DICTIONARY_DIR = "main/dictionary/data"
DICTIONARY_DB = f"{DICTIONARY_DIR}/ecdict_core.db"

# 汉英词库（CC-CEDICT，中→英）—— 由 build_cedict_zh_en.py 生成。
# CC BY-SA 4.0，允许再分发，因此与 ECDICT 一样默认打进 exe。
ZH_EN_DB = f"{DICTIONARY_DIR}/cedict_zh_en.db"

# 随包分发两条词库各自的许可与出处声明
DICTIONARY_LICENSES = "main/dictionary/DICTIONARY_LICENSES.txt"

# 数据文件
datas = [
    f"{SVG_DIR};svg",
    f"{TRANSLATIONS_DIR};translations",
    # 内置词典 → 包内 dictionary/*.db。
    # 运行时也支持放到 exe 同级的 dictionary/ 目录里覆盖它（见 dictionary/service.py）
    f"{DICTIONARY_DB};dictionary",
    f"{ZH_EN_DB};dictionary",
    f"{DICTIONARY_LICENSES};dictionary",
]

# 隐藏导入
hidden_imports = [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtSvg',
    'PySide6.QtSvgWidgets',
    'PySide6.QtXml',
    'pyclipboard',
    'longstitch',
    'gifrecorder',
    'ppocr_rust',
    'windows_media_ocr',
    'PIL',
    'PIL.Image',
    'mss',
    'mss.windows',
    'mss.base',
    'mss.exception',
    'mss.factory',
    'mss.models',
    'mss.screenshot',
    'mss.tools',
    'win32api',
    'win32con',
    'win32gui',
    'pynput',
    'darkdetect',
]

# 排除的模块
excludes = [
    'matplotlib',
    'scipy',
    'numpy',
    'pandas',
    'torch',
    'tensorflow',
    'PyQt5',
    'PyQt6',
    'PySide2',
    'tkinter',
    'pytest',
    'IPython',
    'jupyter',
    'rapidocr',
    'rapidocr_onnxruntime',
    'onnxruntime',
    'keyboard',
    'av',
    'pythoncom',
    'win32com',
    'win32com.client',
    'win32com.server',
    'win32com.gen_py',
    'PySide6.QtQml',
    'PySide6.QtQuick',
    'PySide6.QtQuickControls2',
    'PySide6.QtQuickWidgets',
    'PySide6.QtQuickTest',
    'PySide6.QtNetwork',
    'PySide6.QtSql',
    'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets',
    'PySide6.QtPrintSupport',
    'PySide6.QtHelp',
    'PySide6.QtUiTools',
    'PySide6.QtDesigner',
    'PySide6.QtTest',
    'PySide6.QtConcurrent',
    'PySide6.QtDBus',
    'PySide6.Qt3DAnimation',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic',
    'PySide6.Qt3DRender',
    'PySide6.QtBluetooth',
    'PySide6.QtCharts',
    'PySide6.QtDataVisualization',
    'PySide6.QtGraphs',
    'PySide6.QtHttpServer',
    'PySide6.QtLocation',
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PySide6.QtNetworkAuth',
    'PySide6.QtNfc',
    'PySide6.QtPdf',
    'PySide6.QtPdfWidgets',
    'PySide6.QtPositioning',
    'PySide6.QtQuick3D',
    'PySide6.QtRemoteObjects',
    'PySide6.QtScxml',
    'PySide6.QtSensors',
    'PySide6.QtSerialBus',
    'PySide6.QtSerialPort',
    'PySide6.QtSpatialAudio',
    'PySide6.QtStateMachine',
    'PySide6.QtTextToSpeech',
    'PySide6.QtVirtualKeyboard',
    'PySide6.QtWebChannel',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineQuick',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebSockets',
]

if __name__ == '__main__':
    import argparse

    # --dist 用于"正在运行的旧 exe 锁住了 dist/jietuba.exe"这类情况：
    # 先打包到别的目录，关掉旧进程后再复制过去。
    _parser = argparse.ArgumentParser(description="打包 截图吧 (onefile)")
    _parser.add_argument("--dist", default=DIST_DIR, help=f"输出目录（默认 {DIST_DIR}）")
    _args = _parser.parse_args()
    DIST_DIR = _args.dist

    os.chdir(REPO_DIR)

    print("=" * 60)
    print(f"开始打包 {EXE_NAME} (onefile 模式，PP-OCR 版本)")
    print("=" * 60)
    print(f"源文件: {MAIN_APP}")
    print(f"输出目录: {DIST_DIR}")
    print(f"构建目录: {BUILD_DIR}")
    print("=" * 60)

    # ── 内置离线词典：缺失时自动从 ecdict.csv 生成 ──
    if not (REPO_DIR / DICTIONARY_DB).exists():
        print(f"未找到内置离线词典 {DICTIONARY_DB}，尝试从 ECDICT 生成 …")
        try:
            import build_ecdict

            stats = build_ecdict.build_dictionary(
                build_ecdict.resolve_source(),
                str(REPO_DIR / DICTIONARY_DB),
                mode="core",
                progress=lambda n: print(
                    f"\r  已写入 {n:,} 词条 …", end="", flush=True
                ),
            )
            print(f"\n已生成内置词典：{stats.rows:,} 词条，"
                  f"{stats.size_bytes / 1048576:.2f} MB")
        except Exception as exc:
            print(f"[ERROR] 生成离线词典失败：{exc}")
            print("        请先运行 `python build_ecdict.py`（需要 ECDICT 的 ecdict.csv）")
            raise SystemExit(1)
    else:
        size_mb = (REPO_DIR / DICTIONARY_DB).stat().st_size / 1048576
        print(f"内置离线词典: {DICTIONARY_DB} ({size_mb:.2f} MB)")

    # ── 汉英词库（CC-CEDICT）：与 ECDICT 一样默认内置 ──
    if not (REPO_DIR / ZH_EN_DB).exists():
        print(f"未找到汉英词库 {ZH_EN_DB}，尝试从 CC-CEDICT 生成 …")
        try:
            import build_cedict_zh_en

            stats = build_cedict_zh_en.build_zh_en(
                build_cedict_zh_en.resolve_source(),
                str(REPO_DIR / ZH_EN_DB),
                progress=lambda n: print(
                    f"\r  已写入 {n:,} 条 …", end="", flush=True
                ),
            )
            print(f"\n已生成汉英词库：{stats.heads:,} 词头，"
                  f"{stats.size_bytes / 1048576:.2f} MB")
        except Exception as exc:
            print(f"[ERROR] 生成汉英词库失败：{exc}")
            print("        请先运行 `python build_cedict_zh_en.py`"
                  "（需要 CC-CEDICT 的 cedict_ts.u8）")
            raise SystemExit(1)
    else:
        size_mb = (REPO_DIR / ZH_EN_DB).stat().st_size / 1048576
        print(f"汉英词库(CC-CEDICT): {ZH_EN_DB} ({size_mb:.2f} MB)")

    datas_repr  = repr([(d.split(';')[0], d.split(';')[1]) for d in datas])
    hidden_repr = repr(hidden_imports)
    excl_repr   = repr(excludes)

    spec_content = f"""\
# -*- mode: python ; coding: utf-8 -*-
# 此 spec 文件由 build_with_ocr_onefile.py 自动生成，请勿手动修改后直接运行
import os as _os
from PyInstaller.utils.hooks import collect_all

# 完整收集 windows_media_ocr：包含 oneocr_engine.pyd、旧 windows_media_ocr.pyd、
# 以及子模块隐式导入。oneocr.dll / oneocr.onemodel / onnxruntime.dll（约 109 MB）
# 会在下面被剔除：它们与 Windows 11「截图工具」自带的组件逐字节相同，
# 运行时由 windows_media_ocr 自己从系统目录（或 exe 同级 oneocr/）查找。
_wmo_datas, _wmo_binaries, _wmo_hiddenimports = collect_all('windows_media_ocr')

a = Analysis(
    ['{MAIN_APP}'],
    pathex=['main'],
    binaries=_wmo_binaries,
    datas={datas_repr} + _wmo_datas,
    hiddenimports={hidden_repr} + _wmo_hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={excl_repr},
    noarchive=False,
    optimize=0,
)

# ── binary 层过滤：使用白名单模式，只保留实际需要的 PySide6 DLL/pyd ──

# 白名单：只保留这些 PySide6 相关的 DLL 和 pyd
_PYSIDE6_WHITELIST = {{
    # 核心 DLL
    'qt6core.dll', 'qt6gui.dll', 'qt6widgets.dll',
    'qt6svg.dll', 'qt6svgwidgets.dll', 'qt6xml.dll',
    # 核心 pyd
    'qtcore.pyd', 'qtgui.pyd', 'qtwidgets.pyd',
    'qtsvg.pyd', 'qtsvgwidgets.pyd', 'qtxml.pyd',
    # shiboken / PySide6 绑定
    'pyside6.abi3.dll', 'shiboken6.abi3.dll', 'shiboken.pyd',
    # VC++ 运行时（从 shiboken6 目录带入）
    'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'msvcp140_codecvt_ids.dll',
    'vcruntime140.dll', 'vcruntime140_1.dll',
    'concrt140.dll', 'vcamp140.dll', 'vccorlib140.dll', 'vcomp140.dll',
}}

# 额外要删除的文件
_EXTRA_STRIP = {{
    'opengl32sw.dll',
    # FFmpeg（Qt Multimedia 拉入，截图不需要）
    'avcodec-61.dll', 'avformat-61.dll', 'avutil-59.dll',
    'swscale-8.dll', 'swresample-5.dll',
}}
_EXTRA_STRIP_PATTERNS = ('libscipy_openblas', 'libopenblas',)
_STRIP_BINARY_PATTERNS = ('_avif.',)  # Pillow AVIF

def _keep_binary(entry):
    name = _os.path.basename(entry[1]).lower()
    # 1) 模式排除
    if any(name.startswith(p) for p in _STRIP_BINARY_PATTERNS):
        return False
    if any(name.startswith(p) for p in _EXTRA_STRIP_PATTERNS):
        return False
    # 2) 额外指定排除
    if name in _EXTRA_STRIP:
        return False

    # 4) PySide6 相关文件：白名单模式
    src_lower = entry[1].lower()
    if 'pyside6' in src_lower or 'shiboken6' in src_lower:
        if 'plugins' in src_lower or 'translations' in src_lower:
            return True
        return name in _PYSIDE6_WHITELIST
    # 5) 非 PySide6 文件：保留
    return True

a.binaries = TOC([entry for entry in a.binaries if _keep_binary(entry)])

# ── QM 翻译文件过滤：只保留 zh/ja，去掉其他 Qt 语言包 ──
_KEEP_QM_LOCALES = {{'zh', 'zh_cn', 'zh_tw', 'ja'}}
def _keep_qm(entry):
    src = entry[1]
    name = _os.path.basename(src).lower()
    if not name.endswith('.qm'):
        return True
    if 'pyside6' not in src.lower():
        return True
    stem = name[:-3]
    locale = stem.split('_', 1)[1] if '_' in stem else stem
    return locale in _KEEP_QM_LOCALES or locale.startswith('zh')
a.datas = TOC([e for e in a.datas if _keep_qm(e)])

# ── OneOCR 运行时外置：oneocr.dll / oneocr.onemodel / onnxruntime.dll ≈ 109 MB ──
# 这三个文件与 Windows 11「截图工具」自带的组件逐字节相同（SHA256 一致），
# windows_media_ocr 运行时会自己找：exe 同级 oneocr/ → %LOCALAPPDATA%\\Jietuba\\oneocr
# → 包内 → 系统截图工具目录（注册表 AppModel 仓库 / 枚举 WindowsApps）。
# 都找不到时自动退回 Windows.Media.Ocr，功能不中断。
# 这是 exe 从 ~134 MB 降到 ~57 MB 的关键，别把它们加回来。
_WMO_EXTERNAL_RUNTIME = {{'oneocr.dll', 'oneocr.onemodel', 'onnxruntime.dll'}}

def _is_wmo_runtime(entry):
    parts = _os.path.normpath(entry[0]).split(_os.sep)
    if len(parts) < 2 or parts[0].lower() != 'windows_media_ocr':
        return False
    return parts[-1].lower() in _WMO_EXTERNAL_RUNTIME

a.binaries = TOC([e for e in a.binaries if not _is_wmo_runtime(e)])
a.datas = TOC([e for e in a.datas if not _is_wmo_runtime(e)])

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='{EXE_NAME}',
    icon='托盘.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
"""

    spec_path = REPO_DIR / f"{EXE_NAME}.spec"
    spec_path.write_text(spec_content, encoding="utf-8")
    print(f"已生成 spec 文件: {spec_path}")

    PyInstaller.__main__.run([
        str(spec_path),
        '--noconfirm',
        f'--distpath={DIST_DIR}',
        f'--workpath={BUILD_DIR}',
    ])

    # ── 把 PP-OCR 模型外置到 exe 同级 models/ 目录 ──
    import shutil
    src_models = REPO_DIR / "models"
    dst_models = REPO_DIR / DIST_DIR / "models"
    model_files = ["PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"]
    if src_models.exists():
        dst_models.mkdir(parents=True, exist_ok=True)
        for fn in model_files:
            sp = src_models / fn
            if sp.exists():
                shutil.copy2(sp, dst_models / fn)
                print(f"已外置模型: {fn}")
            else:
                print(f"警告: 缺少模型 {fn}")
    else:
        print(f"警告: 未找到 models 目录 {src_models}")

    print("=" * 60)
    print("打包完成！")
    print(f"可执行文件位置: {DIST_DIR}/{EXE_NAME}.exe")
    exe_path = REPO_DIR / DIST_DIR / f"{EXE_NAME}.exe"
    if exe_path.exists():
        print(f"可执行文件大小: {exe_path.stat().st_size / 1048576:.2f} MB")
    print(f"模型目录(需与 exe 同级): {DIST_DIR}/models/")
    print("OneOCR 运行时: 不打包，运行时从系统截图工具读取（见 windows_media_ocr）")
    print("=" * 60)
