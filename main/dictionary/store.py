# -*- coding: utf-8 -*-
"""ECDICT SQLite 词典的只读查询层。

查询流水线（全部本地）::

    清洗 → ① exact   精确匹配 word（大小写不敏感）
         → ② sw      "去符号"匹配，解决 long-time / longtime / long time
         → ③ 词形还原  查到的若是变形词，归并到原形后再展示
         → ④ prefix  前缀候选，供 UI 提示"你是不是想查…"

索引注意：ECDICT 的 ``word`` 唯一索引是 BINARY 排序，
``WHERE word = ? COLLATE NOCASE`` 用不上它，会退化成全表扫描
（实测 13.55 ms/次）；本模块建库时补了 ``word COLLATE NOCASE`` 索引，
同样的查询降到 0.032 ms/次。因此这里的比较一律显式带 ``COLLATE NOCASE``。
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from typing import List, Optional, Tuple

from .models import (
    DictEntry,
    LookupResult,
    ZhEnEntry,
    ZhEnLookupResult,
    ZhEnSense,
    is_substantive,
    parse_forms,
    parse_inflection,
    parse_lemma,
    parse_senses,
)
from .schema import FIELD_SEP, SENSE_SEP
# 统一各种撇号写法，避免从 OCR / 网页复制来的 ’ 查不到
_APOSTROPHES = {
    "\u2018": "'",
    "\u2019": "'",
    "\u02bc": "'",
    "\u00b4": "'",
    "`": "'",
}

# 清洗查询词时从首尾去掉的标点（词内的连字符与撇号保留）
# 含中文标点与引号：「」『』《》【】等，OCR / 网页复制常见
_TRIM_CHARS = (
    " \t\r\n\"'“”‘’「」『』()[]{}<>《》【】（）,.;:!?。，；：！？…—·-"
)

# 只有这种形态才值得查词典：至多 4 个英文词，可含连字符/撇号/缩写点
_CANDIDATE_RE = re.compile(r"^[A-Za-z][A-Za-z'\-.]*(?:\s+[A-Za-z][A-Za-z'\-.]*){0,3}$")

# 汉字（含扩展区之外的基本区即可覆盖日常词条）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 超过这个长度就不当单词查（说明是整段文字，交给翻译引擎）
MAX_QUERY_CHARS = 64

# 中文词头最长多少字才值得查汉英词典（再长基本是句子）
MAX_ZH_QUERY_CHARS = 12

# 命中前的候选词上限
SUGGEST_LIMIT = 6


def stripword(word: str) -> str:
    """ECDICT 的 ``sw``：去掉所有非字母数字字符并转小写。"""
    return "".join(ch for ch in word if ch.isalnum()).lower()


def clean_query(text: Optional[str]) -> str:
    """把用户选中/OCR 得到的原始文本整成可查询的词。"""
    if not text:
        return ""
    cleaned = text.strip()
    for bad, good in _APOSTROPHES.items():
        cleaned = cleaned.replace(bad, good)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(_TRIM_CHARS).strip()


def is_lookup_candidate(text: Optional[str]) -> bool:
    """这段文本是否值得查**英→中**词典。

    只有"英文单词 / 至多四个词的英文短语"才查；整段中英文混排交给翻译引擎，
    避免抢走正常翻译。ECDICT 是英→中词典，因此要求全 ASCII 字母。
    """
    cleaned = clean_query(text)
    if not cleaned or len(cleaned) > MAX_QUERY_CHARS:
        return False
    return bool(_CANDIDATE_RE.match(cleaned))


def is_zh_lookup_candidate(text: Optional[str]) -> bool:
    """这段文本是否值得查**汉→英**词典。

    要求整串都是汉字（允许中间有空格），长度不超过 ``MAX_ZH_QUERY_CHARS``；
    中英混排或句子交给翻译引擎。
    """
    cleaned = clean_query(text).replace(" ", "")
    if not cleaned or len(cleaned) > MAX_ZH_QUERY_CHARS:
        return False
    return all(_CJK_RE.match(ch) for ch in cleaned)


def contains_cjk(text: Optional[str]) -> bool:
    """文本里是否含汉字（用于判断翻译方向）。"""
    return bool(text) and bool(_CJK_RE.search(text))


class EcdictStore:
    """一个 ecdict.db 的只读句柄。

    连接按线程存放：SQLite 连接不能跨线程使用，而查词会从 GUI 线程与
    后台 OCR/翻译线程发起，用 thread-local 最省事也最安全。
    """

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self._lock = threading.Lock()

    # ── 连接 ────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        uri = "file:{}?mode=ro".format(_file_uri(self.path))
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """关闭当前线程的连接（其他线程的连接随其结束释放）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    # ── 查询 ────────────────────────────────────────────────────────
    def _fetch_word(self, word: str) -> Optional[sqlite3.Row]:
        if not word:
            return None
        try:
            return self._conn.execute(
                "SELECT * FROM stardict WHERE word = ? COLLATE NOCASE LIMIT 1",
                (word,),
            ).fetchone()
        except sqlite3.Error:
            return None

    def _fetch_by_sw(self, word: str) -> Optional[sqlite3.Row]:
        """按 sw 匹配同一词的不同写法（long-time / longtime / long time）。"""
        sw = stripword(word)
        if not sw:
            return None
        try:
            rows = self._conn.execute(
                "SELECT * FROM stardict WHERE sw = ? COLLATE NOCASE "
                "ORDER BY length(word), word COLLATE NOCASE LIMIT 8",
                (sw,),
            ).fetchall()
        except sqlite3.Error:
            return None
        if not rows:
            return None
        # 完全同名（忽略大小写）优先，其次最短的那个
        lowered = word.lower()
        for row in rows:
            if row["word"].lower() == lowered:
                return row
        return rows[0]

    def suggest(self, word: str, limit: int = SUGGEST_LIMIT) -> Tuple[str, ...]:
        """前缀候选词。"""
        word = clean_query(word)
        if not word:
            return ()
        try:
            rows = self._conn.execute(
                "SELECT word FROM stardict "
                "WHERE word >= ? COLLATE NOCASE AND word < ? COLLATE NOCASE "
                "ORDER BY word COLLATE NOCASE LIMIT ?",
                (word, word + "\uffff", limit),
            ).fetchall()
        except sqlite3.Error:
            return ()
        return tuple(row[0] for row in rows)

    def count(self) -> int:
        try:
            return int(self._conn.execute(
                "SELECT count(*) FROM stardict").fetchone()[0])
        except sqlite3.Error:
            return 0

    # ── 主入口 ──────────────────────────────────────────────────────
    def lookup(self, text: Optional[str]) -> LookupResult:
        """查一个单词/短语，返回已归并到原形的词条。"""
        query = clean_query(text)
        if not query:
            return LookupResult()

        row = self._fetch_word(query)
        match_kind = "exact"
        if row is None:
            row = self._fetch_by_sw(query)
            match_kind = "fuzzy"
        if row is None:
            return LookupResult(suggestions=self.suggest(query))

        entry = self._build_entry(row, query, match_kind)
        if entry is None:
            # 词条存在但没有释义（内置库里极少数），给候选词更有用
            return LookupResult(suggestions=self.suggest(query))
        return LookupResult(entry=entry)

    # ── 词条组装 ────────────────────────────────────────────────────
    def _build_entry(
        self, row: sqlite3.Row, query: str, match_kind: str
    ) -> Optional[DictEntry]:
        exchange = row["exchange"]
        lemma = parse_lemma(exchange)
        head_row = row
        headword = row["word"]
        inflection = ""

        if lemma and lemma.lower() != headword.lower():
            lemma_row = self._fetch_word(lemma)
            if lemma_row is not None:
                head_row = lemma_row
                headword = lemma_row["word"]
                inflection = parse_inflection(exchange) or "变形"

        senses = parse_senses(head_row["translation"])
        definition = head_row["definition"] if _has_column(head_row, "definition") else ""

        # 变形词自身的释义：只在有实义时补充展示（running / better）
        variant_senses = ()
        if inflection:
            variant_senses = tuple(
                sense
                for sense in parse_senses(row["translation"])
                if is_substantive(row["translation"], head_row["translation"])
            )

        if not senses and not variant_senses and not definition:
            return None

        return DictEntry(
            headword=headword,
            query=query,
            phonetic=(head_row["phonetic"] or "").strip(),
            senses=senses,
            variant_senses=variant_senses,
            definition=(definition or "").strip(),
            inflection=inflection,
            forms=parse_forms(head_row["exchange"]),
            tags=tuple((head_row["tag"] or "").split()),
            collins=int(head_row["collins"] or 0),
            oxford=bool(head_row["oxford"]),
            bnc=int(head_row["bnc"] or 0),
            frq=int(head_row["frq"] or 0),
            match_kind=match_kind,
        )


def _file_uri(path: str) -> str:
    """把本地路径转成 SQLite 能用的 file: URI（Windows 反斜杠会坏掉）。"""
    normalized = os.path.abspath(path).replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized.replace(" ", "%20").replace("#", "%23").replace("?", "%3F")


class ZhEnStore:
    """汉英（中 → 英）词典的只读句柄。

    表结构（由 ``build_cedict_zh_en.py`` 从 CC-CEDICT 生成，详见 schema.py）::

        zh_en(simp, trad, pinyin, senses)
        -- 一条记录 = 一个词条；senses 里打包多条 "词性\\x1f英文对应词"

    与 ``EcdictStore`` 分成两个文件、两个类，是因为两者数据来源与许可不同：
    ECDICT 是 MIT，CC-CEDICT 是 CC BY-SA 4.0，各自单独存放便于署名与合规。
    """

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        uri = "file:{}?mode=ro".format(_file_uri(self.path))
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    def count(self) -> int:
        try:
            return int(self._conn.execute(
                "SELECT count(DISTINCT simp) FROM zh_en").fetchone()[0])
        except sqlite3.Error:
            return 0

    def suggest(self, text: str, limit: int = SUGGEST_LIMIT) -> Tuple[str, ...]:
        """前缀候选词头（中文按字面前缀）。"""
        cleaned = clean_query(text)
        if not cleaned:
            return ()
        try:
            rows = self._conn.execute(
                "SELECT DISTINCT simp FROM zh_en "
                "WHERE simp >= ? AND simp < ? ORDER BY simp LIMIT ?",
                (cleaned, cleaned + "\uffff", limit),
            ).fetchall()
        except sqlite3.Error:
            return ()
        return tuple(row[0] for row in rows)

    @staticmethod
    def _unpack_senses(packed: str) -> list:
        """把 ``"v.\\x1fcomplete\\x1ev.\\x1faccomplish"`` 拆回释义列表。"""
        out = []
        for item in (packed or "").split(SENSE_SEP):
            if not item:
                continue
            pos, _, text = item.partition(FIELD_SEP)
            text = text.strip()
            if text:
                out.append(ZhEnSense(pos.strip(), text))
        return out

    def _rows_for(self, query: str) -> list:
        """先按简体查，没结果再按繁体查（两个索引都能命中）。"""
        for column in ("simp", "trad"):
            try:
                rows = self._conn.execute(
                    f"SELECT simp, trad, pinyin, senses FROM zh_en "
                    f"WHERE {column} = ? ORDER BY rowid",
                    (query,),
                ).fetchall()
            except sqlite3.Error:
                rows = []
            if rows:
                return rows
        return []

    def lookup(self, text: Optional[str]) -> ZhEnLookupResult:
        """查一个中文词，返回它的英文对应词（简体、繁体都能命中）。"""
        query = clean_query(text)
        if not query:
            return ZhEnLookupResult()

        rows = self._rows_for(query)
        if not rows:
            # 去掉末尾"的/了/地"再试一次（口语化输入常见）
            stripped = query.rstrip("的了地")
            if stripped and stripped != query:
                return self.lookup(stripped)
            return ZhEnLookupResult(suggestions=self.suggest(query))

        senses = []
        seen = set()
        for row in rows:
            for sense in self._unpack_senses(row["senses"]):
                key = (sense.pos, sense.text.lower())
                if key in seen:
                    continue
                seen.add(key)
                senses.append(sense)
        if not senses:
            return ZhEnLookupResult(suggestions=self.suggest(query))

        simp = (rows[0]["simp"] or query).strip()
        trad = (rows[0]["trad"] or "").strip()
        return ZhEnLookupResult(
            entry=ZhEnEntry(
                headword=simp,
                traditional="" if trad == simp else trad,
                pinyin=(rows[0]["pinyin"] or "").strip(),
                senses=tuple(senses),
                query=query,
            )
        )


def _has_column(row: sqlite3.Row, name: str) -> bool:
    try:
        row[name]
        return True
    except (IndexError, KeyError):
        return False
