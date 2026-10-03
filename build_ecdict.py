# -*- coding: utf-8 -*-
"""把 ECDICT 的 ecdict.csv 转换成截图吧内置的只读 SQLite 词典。

用法::

    python build_ecdict.py                        # 核心词典（默认，约 16 MB）
    python build_ecdict.py --mode full            # 全量词典（约 100 MB）
    python build_ecdict.py --src D:\\ECDICT\\ecdict.csv --out my.db

产物：

* ``--mode core``（默认）→ ``main/dictionary/data/ecdict_core.db``
* ``--mode full``        → ``main/dictionary/data/ecdict_full.db``

核心词典的筛选规则（实测样本文本覆盖率 100%）::

    柯林斯星级 > 0  或  牛津三千核心词  或  有考纲标签
    或  BNC 词频 <= 60000  或  当代语料库词频 <= 60000
    或  该词是另一个词的变形（exchange 含 "0:"）

最后一条很关键：gave / mice / taken / looked 这类变形词的 bnc 与 frq 都是 0、
也没有任何标签，只按"高频+考纲"筛会把它们全部漏掉。

索引同样重要：ECDICT 的 ``word`` 唯一索引是 BINARY 排序，
``WHERE word = ? COLLATE NOCASE`` 用不上它，会退化成全表扫描
（实测 13.55 ms/次）；补一个 ``word COLLATE NOCASE`` 索引后降到 0.032 ms/次。
"""
from __future__ import annotations

import argparse
import csv
import gc
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

# ── 路径 ────────────────────────────────────────────────────────────
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(REPO_DIR, "main", "dictionary", "data")

OUTPUTS = {
    "core": os.path.join(DATA_DIR, "ecdict_core.db"),
    "full": os.path.join(DATA_DIR, "ecdict_full.db"),
}

# 默认去这些位置找 ecdict.csv（先到先得）
DEFAULT_SOURCE_CANDIDATES = (
    os.path.join(REPO_DIR, "ecdict.csv"),
    os.path.join(REPO_DIR, "main", "dictionary", "data", "ecdict.csv"),
    r"F:\AI\ECDICT\ecdict.csv",
    os.path.join(os.path.expanduser("~"), "ECDICT", "ecdict.csv"),
)

# 核心词典词频上限（BNC / 当代语料库）
CORE_FREQ_LIMIT = 60000

# ── 表结构 ──────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE stardict (
    word        VARCHAR(64) NOT NULL,
    sw          VARCHAR(64) NOT NULL,
    phonetic    VARCHAR(64),
    translation TEXT,
    collins     INTEGER DEFAULT 0,
    oxford      INTEGER DEFAULT 0,
    tag         VARCHAR(64),
    bnc         INTEGER DEFAULT 0,
    frq         INTEGER DEFAULT 0,
    exchange    TEXT
);
CREATE INDEX sd_word_nocase ON stardict(word COLLATE NOCASE);
CREATE INDEX sd_sw ON stardict(sw, word COLLATE NOCASE);
"""

COLUMNS = ("word", "sw", "phonetic", "translation", "collins", "oxford",
           "tag", "bnc", "frq", "exchange")


@dataclass
class BuildStats:
    rows: int
    size_bytes: int
    elapsed: float
    output: str
    mode: str


def stripword(word: str) -> str:
    """ECDICT 的 sw 字段：去掉所有非字母数字字符并转小写。"""
    return "".join(ch for ch in word if ch.isalnum()).lower()


def _as_int(value: Optional[str]) -> int:
    try:
        return int((value or "0").strip() or 0)
    except (TypeError, ValueError):
        return 0


def is_core_row(row: dict) -> bool:
    """核心词典筛选规则，详见模块文档。"""
    if _as_int(row.get("collins")) or _as_int(row.get("oxford")):
        return True
    if (row.get("tag") or "").strip():
        return True
    bnc, frq = _as_int(row.get("bnc")), _as_int(row.get("frq"))
    if 0 < bnc <= CORE_FREQ_LIMIT or 0 < frq <= CORE_FREQ_LIMIT:
        return True
    # 变形词（gave / mice / taken …）：exchange 里的 "0:" 指向它的原形
    return "0:" in (row.get("exchange") or "")


def resolve_source(explicit: Optional[str] = None) -> str:
    """定位 ecdict.csv；找不到时抛出 FileNotFoundError。"""
    candidates = (explicit,) if explicit else DEFAULT_SOURCE_CANDIDATES
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError(
        "找不到 ecdict.csv，请用 --src 指定路径。已尝试：\n  "
        + "\n  ".join(p for p in candidates if p)
    )


def _atomic_replace(tmp_path: str, output: str, attempts: int = 12) -> None:
    """把临时库换成正式库。

    Windows 上刚关闭的 SQLite 文件偶尔还被索引/杀毒进程短暂占用，
    直接 ``os.replace`` 会报 WinError 32，因此带短重试。
    """
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            os.replace(tmp_path, output)
            return
        except PermissionError as exc:  # 文件被短暂占用
            last_error = exc
            time.sleep(0.15 * (attempt + 1))
    # 最后再试一次"先删后改名"，仍然失败就如实抛出
    try:
        if os.path.exists(output):
            os.remove(output)
        os.rename(tmp_path, output)
    except OSError:
        raise last_error if last_error else RuntimeError("无法替换词典文件")


def _row_to_tuple(row: dict) -> tuple:
    word = (row.get("word") or "").strip()
    return (
        word,
        stripword(word),
        (row.get("phonetic") or "").strip(),
        (row.get("translation") or "").strip(),
        _as_int(row.get("collins")),
        _as_int(row.get("oxford")),
        (row.get("tag") or "").strip(),
        _as_int(row.get("bnc")),
        _as_int(row.get("frq")),
        (row.get("exchange") or "").strip(),
    )


def build_dictionary(
    source: str,
    output: str,
    *,
    mode: str = "core",
    progress: Optional[Callable[[int], None]] = None,
    batch_size: int = 20000,
) -> BuildStats:
    """把 ``source`` 的 CSV 转成 ``output`` 的 SQLite 词典。

    ``progress`` 每写入 ``batch_size`` 行回调一次，参数为已写入行数。
    写入先落到 ``output + ".tmp"``，成功后原子替换，避免中断留下半个库。
    """
    if mode not in ("core", "full"):
        raise ValueError(f"未知的 mode: {mode!r}（可选 core / full）")

    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    tmp_path = output + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    predicate = is_core_row if mode == "core" else None
    started = time.time()
    written = 0

    con = sqlite3.connect(tmp_path)
    cur = None
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.executescript(SCHEMA)
        cur = con.cursor()
        placeholders = ",".join("?" * len(COLUMNS))
        sql = f"INSERT OR IGNORE INTO stardict VALUES ({placeholders})"

        pending: list[tuple] = []
        with open(source, "r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if not (row.get("word") or "").strip():
                    continue
                if predicate is not None and not predicate(row):
                    continue
                pending.append(_row_to_tuple(row))
                if len(pending) >= batch_size:
                    cur.executemany(sql, pending)
                    written += len(pending)
                    pending.clear()
                    if progress:
                        progress(written)
        if pending:
            cur.executemany(sql, pending)
            written += len(pending)
            if progress:
                progress(written)

        con.commit()
        con.execute("VACUUM")
        con.commit()
    finally:
        # 光标必须先关闭：否则 sqlite3_close_v2 只把连接标记为待关闭，
        # 未终结的语句仍然占着文件句柄，紧接着的 os.replace 会报 WinError 32。
        if cur is not None:
            try:
                cur.close()
            except sqlite3.Error:
                pass
        con.close()
        gc.collect()

    _atomic_replace(tmp_path, output)
    return BuildStats(
        rows=written,
        size_bytes=os.path.getsize(output),
        elapsed=time.time() - started,
        output=output,
        mode=mode,
    )


def _print_progress(written: int) -> None:
    sys.stdout.write(f"\r  已写入 {written:,} 词条 …")
    sys.stdout.flush()


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 ECDICT 的 ecdict.csv 转换成截图吧内置的 SQLite 词典"
    )
    parser.add_argument("--src", help="ecdict.csv 路径（默认自动查找）")
    parser.add_argument("--out", help="输出 .db 路径（默认按 mode 决定）")
    parser.add_argument("--mode", choices=("core", "full"), default="core",
                        help="core=核心词典（默认，约 16MB）；full=全量（约 100MB）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        source = resolve_source(args.src)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    output = args.out or OUTPUTS[args.mode]
    print(f"[INFO] 源文件: {source}")
    print(f"[INFO] 目标库: {output}")
    print(f"[INFO] 模式  : {args.mode}")

    stats = build_dictionary(source, output, mode=args.mode,
                             progress=_print_progress)
    sys.stdout.write("\r" + " " * 40 + "\r")
    print(f"[OK] 完成：{stats.rows:,} 词条，"
          f"{stats.size_bytes / 1048576:.2f} MB，耗时 {stats.elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
