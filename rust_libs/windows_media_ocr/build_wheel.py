"""Prepare the mixed Rust/Python wheel and invoke maturin.

Usage:
    python build_wheel.py --snipping-tool "C:\\...\\SnippingTool"

The script copies:
  * the existing windows_media_ocr.pyd (Windows.Media.Ocr implementation)
  * oneocr.dll, oneocr.onemodel, onnxruntime.dll (OneOCR runtime)
into python/windows_media_ocr/, then runs maturin build --release.
"""

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGE_DIR = ROOT / "python" / "windows_media_ocr"
REPO_ROOT = ROOT.parent.parent
OLD_WHEEL = REPO_ROOT / "windows_media_ocr-0.3.1-cp311-cp311-win_amd64.whl"

RUNTIME_FILES = ["oneocr.dll", "oneocr.onemodel", "onnxruntime.dll"]


def copy_old_pyd() -> None:
    """Extract the Windows.Media.Ocr pyd from the current 0.3.1 wheel."""
    if not OLD_WHEEL.exists():
        raise SystemExit(f"old wheel not found: {OLD_WHEEL}")

    with zipfile.ZipFile(OLD_WHEEL) as z:
        pyd_name = next(
            name for name in z.namelist()
            if name.startswith("windows_media_ocr/windows_media_ocr") and name.endswith(".pyd")
        )
        data = z.read(pyd_name)

    target = PACKAGE_DIR / Path(pyd_name).name
    target.write_bytes(data)
    print(f"[OK] copied {Path(pyd_name).name}")


def copy_oneocr_runtime(snipping_tool_dir: Path) -> None:
    """Copy OneOCR runtime files from the SnippingTool package."""
    missing = [name for name in RUNTIME_FILES if not (snipping_tool_dir / name).exists()]
    if missing:
        raise SystemExit(f"missing runtime file(s) in {snipping_tool_dir}: {missing}")

    for name in RUNTIME_FILES:
        shutil.copy2(snipping_tool_dir / name, PACKAGE_DIR / name)
        print(f"[OK] copied {name}")


def run_maturin() -> None:
    subprocess.run(
        [sys.executable, "-m", "maturin", "build", "--release"],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snipping-tool", required=True, help="Path to the SnippingTool folder")
    args = parser.parse_args()

    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    copy_old_pyd()
    copy_oneocr_runtime(Path(args.snipping_tool))
    run_maturin()


if __name__ == "__main__":
    main()
