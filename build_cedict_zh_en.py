# -*- coding: utf-8 -*-
"""把 CC-CEDICT 转成截图吧可用的汉英 SQLite 词典。

用法::

    python build_cedict_zh_en.py --src path\\to\\cedict_ts.u8
    python build_cedict_zh_en.py                 # 自动在常见位置查找

产物：``main/dictionary/data/cedict_zh_en.db``

数据来源：CC-CEDICT（https://cc-cedict.org/），由 MDBG 发布。
许可：**CC BY-SA 4.0** —— 允许商用与再分发，条件是署名来源，
且若改进了数据需以相同许可共享。声明见 main/dictionary/CEDICT_LICENSE.txt。

格式（每行一条）::

    繁體 简体 [pin1 yin1] /gloss1/gloss2; gloss2b/

几个实现要点：

* 同一词头常有**多条**条目（例如 苹果 既有"苹果公司"也有"apple"）。只取
  第一条会把公司当成水果，因此要收集全部条目再排序。
* CC-CEDICT 的约定是：拼音首字母**大写 = 专有名词**。据此把普通名词排在
  专有名词之前，"苹果"就会先给 apple。
* 释义里 ``;`` 分隔近义词、``/`` 分隔不同义项；``CL:`` 是量词信息，
  对翻译用途是噪音，跳过。
* 词性没有专门字段，只能从 "to ..." 这种写法推断动词，其余留空。
"""
from __future__ import annotations

import argparse
import gc
import io
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(
    REPO_DIR, "main", "dictionary", "data", "cedict_zh_en.db"
)

# 常见的 CC-CEDICT 文件位置
DEFAULT_SOURCES = (
    os.path.join(REPO_DIR, "cedict_ts.u8"),
    os.path.join(REPO_DIR, "main", "dictionary", "data", "cedict_ts.u8"),
    os.path.join(REPO_DIR, ".cedict_dl", "cedict_ts.u8"),
    os.path.join(os.path.expanduser("~"), "cedict_ts.u8"),
)

SCHEMA = """
CREATE TABLE zh_en (
    simp   TEXT NOT NULL,   -- 简体词头
    trad   TEXT NOT NULL,   -- 繁体词头（与简体相同即为同一写法）
    pinyin TEXT,            -- 带声调的拼音，如 "wán chéng"
    senses TEXT NOT NULL    -- 释义打包：每条 "词性\\x1f英文对应词"，多条用 \\x1e 连接
);
CREATE INDEX idx_zh_simp ON zh_en(simp);
CREATE INDEX idx_zh_trad ON zh_en(trad);
"""

# 释义打包用的分隔符：都是不可打印字符，绝不会出现在词条文本里。
# ⚠ 必须与 main/dictionary/schema.py 保持一致（有测试守护，
#   见 main/tests/test_dictionary_zh_en.py::test_separators_match_schema）。
SENSE_SEP = "\x1e"   # 释义之间
FIELD_SEP = "\x1f"   # 词性与释义文本之间

# 一行形如：繁體 简体 [pin1 yin1] /gloss/gloss/
_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\]\s+/(.*)/\s*$")
# 拼音音节：字母（含 u:）后跟声调数字
_SYLLABLE_RE = re.compile(r"([A-Za-z:]+)([0-5])")

_TONE_MARKS = {
    "a": "āáǎà", "e": "ēéěè", "i": "īíǐì",
    "o": "ōóǒò", "u": "ūúǔù", "ü": "ǖǘǚǜ",
}
# 标调位置：优先 a > o > e，然后 iu/ui 标后一个，其余标第一个元音
_VOWELS = "aeiouü"


@dataclass
class BuildStats:
    heads: int
    senses: int
    entries: int
    size_bytes: int
    elapsed: float
    output: str


def resolve_source(explicit: Optional[str] = None) -> str:
    candidates = (explicit,) if explicit else DEFAULT_SOURCES
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError(
        "找不到 CC-CEDICT 数据文件（cedict_ts.u8）。已尝试：\n  "
        + "\n  ".join(p for p in candidates if p)
        + "\n可从 https://www.mdbg.net/chinese/dictionary?page=cc-cedict 下载，"
          "或用 --src 指定路径。"
    )


def _mark_syllable(match: re.Match) -> str:
    """把 ``wan2`` 变成 ``wán``。"""
    letters, tone = match.group(1), int(match.group(2))
    if tone == 0 or tone == 5:
        return letters.replace("u:", "ü")
    text = letters.replace("u:", "ü").lower()
    index = -1
    for vowel in ("a", "o", "e"):
        if vowel in text:
            index = text.index(vowel)
            break
    else:
        for pair, target in (("iu", "u"), ("ui", "i")):
            if pair in text:
                index = text.index(pair) + 1
                break
        else:
            for i, ch in enumerate(text):
                if ch in _VOWELS:
                    index = i
                    break
    if index < 0:
        return letters
    ch = text[index]
    marks = _TONE_MARKS.get(ch)
    if not marks:
        return letters
    marked = text[:index] + marks[tone - 1] + text[index + 1:]
    # 原本大写的（专有名词）保持首字母大写
    if letters[:1].isupper():
        marked = marked[:1].upper() + marked[1:]
    return marked


def format_pinyin(raw: str) -> str:
    """``wan2 cheng2`` → ``wán chéng``。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    return _SYLLABLE_RE.sub(_mark_syllable, raw)


def _split_senses(gloss_blob: str) -> list[tuple[str, str]]:
    """把 ``/.../`` 里的内容拆成 ``[(词性, 英文对应词), ...]``。"""
    out: list[tuple[str, str]] = []
    seen = set()
    for gloss in gloss_blob.split("/"):
        for piece in gloss.split(";"):
            piece = piece.strip()
            if not piece:
                continue
            # 量词信息对翻译用途是噪音
            if piece.startswith("CL:"):
                continue
            pos = ""
            if piece.startswith("to ") and len(piece) > 3:
                pos, piece = "v.", piece[3:].strip()
            if not piece:
                continue
            key = (pos, piece.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append((pos, piece))
    return out


def _is_proper_noun(pinyin: str) -> bool:
    """CC-CEDICT 约定：拼音首字母大写表示专有名词。"""
    return bool(pinyin) and pinyin[:1].isupper()


def _load_entries(path: str) -> dict[str, list[tuple[bool, str, str, list]]]:
    """读取所有词条，按**简体**词头聚合。

    返回 ``{简体: [(是专名, 繁体, 拼音, 释义), ...]}``。繁体只作为附加列，
    不再单独复制一份释义——否则体积会翻倍。
    """
    grouped: dict[str, list[tuple[bool, str, str, list]]] = {}
    with io.open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            match = _LINE_RE.match(line.rstrip("\n"))
            if not match:
                continue
            trad, simp, raw_pinyin, blob = match.groups()
            pinyin = format_pinyin(raw_pinyin)
            senses = _split_senses(blob)
            if not senses:
                continue
            grouped.setdefault(simp, []).append(
                (_is_proper_noun(pinyin), trad, pinyin, senses)
            )
    return grouped


def build_zh_en(
    source: str,
    output: str,
    *,
    progress: Optional[Callable[[int], None]] = None,
    batch_size: int = 20000,
) -> BuildStats:
    """把 CC-CEDICT 文本转成 ``output`` 的 SQLite 词典。"""
    started = time.time()
    grouped = _load_entries(source)

    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    tmp_path = output + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    con = sqlite3.connect(tmp_path)
    cursor = None
    heads = senses = written = 0
    insert_sql = "INSERT INTO zh_en VALUES (?,?,?,?)"
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.executescript(SCHEMA)
        cursor = con.cursor()

        rows: list[tuple[str, str, str, str]] = []
        for simp, entries in grouped.items():
            heads += 1
            # 普通名词在前、专有名词在后（稳定排序保持文件内相对顺序）
            entries.sort(key=lambda item: item[0])
            for _proper, trad, pinyin, entry_senses in entries:
                packed = SENSE_SEP.join(
                    f"{pos}{FIELD_SEP}{sense}" for pos, sense in entry_senses
                )
                rows.append((simp, trad, pinyin, packed))
                senses += len(entry_senses)
            if len(rows) >= batch_size:
                cursor.executemany(insert_sql, rows)
                written += len(rows)
                rows = []
                if progress:
                    progress(written)
        if rows:
            cursor.executemany(insert_sql, rows)
            written += len(rows)
            if progress:
                progress(written)

        con.commit()
        con.execute("VACUUM")
        con.commit()
    finally:
        # 光标必须先关：否则 sqlite3_close_v2 只标记待关闭，文件句柄还在，
        # 紧接着的 os.replace 会报 WinError 32。
        if cursor is not None:
            try:
                cursor.close()
            except sqlite3.Error:
                pass
        con.close()
        gc.collect()

    for attempt in range(12):
        try:
            os.replace(tmp_path, output)
            break
        except PermissionError:
            time.sleep(0.15 * (attempt + 1))
    else:
        if os.path.exists(output):
            os.remove(output)
        os.rename(tmp_path, output)

    return BuildStats(
        heads=heads,
        senses=senses,
        entries=written,
        size_bytes=os.path.getsize(output),
        elapsed=time.time() - started,
        output=output,
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 CC-CEDICT 转成截图吧可用的汉英 SQLite 词典"
    )
    parser.add_argument("--src", help="cedict_ts.u8 路径（默认自动查找）")
    parser.add_argument("--out", help="输出 .db 路径")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        source = resolve_source(args.src)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    output = args.out or DEFAULT_OUT
    print(f"[INFO] 源文件: {source}")
    print(f"[INFO] 目标库: {output}")
    print("[INFO] 许可  : CC-CEDICT, CC BY-SA 4.0（可再分发，需署名）")

    stats = build_zh_en(
        source, output,
        progress=lambda n: (sys.stdout.write(f"\r  已写入 {n:,} 条 …"),
                            sys.stdout.flush()),
    )
    sys.stdout.write("\r" + " " * 40 + "\r")
    print(f"[OK] 完成：词头 {stats.heads:,}，释义 {stats.senses:,}，"
          f"{stats.size_bytes / 1048576:.2f} MB，耗时 {stats.elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
