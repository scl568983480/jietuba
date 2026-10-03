# -*- coding: utf-8 -*-
"""汉英（中 → 英）离线查词测试。

监控目标：
1. 中文词 → 走汉英库，英文词 → 走 ECDICT，两句不串门。
2. 中文句子 / 中英混排 / 超长中文不查词典，交回翻译引擎。
3. 汉英库缺失或损坏时只能退化成"没查过"，绝不打断翻译。
4. 未装汉英库（只有 ECDICT）时，中→英 仍然走联网，行为与改动前一致。
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dictionary.models import ZhEnEntry, ZhEnSense  # noqa: E402
from dictionary.schema import FIELD_SEP, SENSE_SEP  # noqa: E402
from dictionary.store import (  # noqa: E402
    ZhEnStore,
    clean_query,
    contains_cjk,
    is_lookup_candidate,
    is_zh_lookup_candidate,
)

SCHEMA = """
CREATE TABLE zh_en (
    simp   TEXT NOT NULL,
    trad   TEXT NOT NULL,
    pinyin TEXT,
    senses TEXT NOT NULL
);
CREATE INDEX idx_zh_simp ON zh_en(simp);
CREATE INDEX idx_zh_trad ON zh_en(trad);
"""


def _pack(*senses):
    """把 (词性, 释义) 打包成 store 期望的格式。"""
    return SENSE_SEP.join(f"{pos}{FIELD_SEP}{text}" for pos, text in senses)


# 一个词条一行；senses 里打包多条释义
ROWS = [
    ("完成", "完成", "wán chéng",
     _pack(("v.", "complete"), ("v.", "accomplish"), ("v.", "finish"))),
    # 同一个词头两条条目：普通词义在前、专有名词在后
    ("苹果", "蘋果", "píng guǒ", _pack(("", "apple"))),
    ("苹果", "蘋果", "Píng guǒ", _pack(("", "Apple (American tech company)"))),
    ("漂亮", "漂亮", "piào liang", _pack(("", "pretty"), ("", "beautiful"))),
    ("取消", "取消", "qǔ xiāo",
     _pack(("", "countermand"), ("v.", "cancel"), ("v.", "call off"))),
    ("截图", "截圖", "jié tú", _pack(("", "screenshot"))),
    ("完成品", "完成品", "wán chéng pǐn", _pack(("", "finished product"))),
]


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "cedict_zh_en.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO zh_en VALUES (?,?,?,?)", ROWS)
    con.commit()
    con.close()
    instance = ZhEnStore(str(db))
    yield instance
    instance.close()


def test_separators_match_schema():
    """生成方（build_cedict_zh_en.py）与读取方必须用同一套分隔符。"""
    import importlib.util

    repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    name = "build_cedict_probe"
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(repo, "build_cedict_zh_en.py")
    )
    module = importlib.util.module_from_spec(spec)
    # 必须先注册到 sys.modules：该脚本用了 `from __future__ import annotations`，
    # dataclasses 解析字符串注解时会去 sys.modules 里按模块名取命名空间。
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        assert module.SENSE_SEP == SENSE_SEP
        assert module.FIELD_SEP == FIELD_SEP
    finally:
        sys.modules.pop(name, None)


# ── 候选判定 ────────────────────────────────────────────────────────
def test_chinese_word_is_a_candidate():
    assert is_zh_lookup_candidate("完成")
    assert is_zh_lookup_candidate("漂亮")
    assert is_zh_lookup_candidate("完成。")      # 尾部标点会被清掉
    assert is_zh_lookup_candidate("截图")


def test_sentences_and_mixed_text_are_not_candidates():
    assert not is_zh_lookup_candidate("这是一整个句子不应该查词典")
    assert not is_zh_lookup_candidate("完成 complete")
    assert not is_zh_lookup_candidate("complete")
    assert not is_zh_lookup_candidate("")
    assert not is_zh_lookup_candidate(None)


def test_english_candidate_check_is_unaffected_by_chinese():
    """两个判定必须互斥，否则会互相抢词。"""
    assert is_lookup_candidate("complete")
    assert not is_lookup_candidate("完成")
    assert is_zh_lookup_candidate("完成")
    assert not is_zh_lookup_candidate("complete")


def test_contains_cjk():
    assert contains_cjk("完成")
    assert contains_cjk("完成 complete")
    assert not contains_cjk("complete")
    assert not contains_cjk("")


# ── 查询 ────────────────────────────────────────────────────────────
def test_lookup_groups_by_part_of_speech(store):
    entry = store.lookup("完成").entry
    assert entry.headword == "完成"
    assert entry.pinyin == "wán chéng"
    # CC-CEDICT 没有词性字段，"to ..." 推断为动词，顺序保持条目内顺序
    assert entry.pos_groups() == (
        ("v.", ("complete", "accomplish", "finish")),
    )


def test_lookup_keeps_multiple_pos_groups_in_order(store):
    entry = store.lookup("取消").entry
    assert entry.pos_groups() == (
        ("", ("countermand",)),
        ("v.", ("cancel", "call off")),
    )


def test_proper_noun_comes_after_common_meaning(store):
    """苹果 先给 apple，再给苹果公司（专有名词排在后面）。"""
    entry = store.lookup("苹果").entry
    flat = [sense.text for sense in entry.senses]
    assert flat[0] == "apple"
    assert any("Apple" in text for text in flat)


def test_traditional_head_also_hits(store):
    """繁体词头（截图 → 截圖）也能查到同一词条。"""
    entry = store.lookup("截圖").entry
    assert entry.headword == "截图"
    assert entry.has_variant_script is True
    assert entry.traditional == "截圖"
    assert [sense.text for sense in entry.senses] == ["screenshot"]


def test_same_script_has_no_variant_flag(store):
    entry = store.lookup("完成").entry
    assert entry.has_variant_script is False


def test_lookup_strips_trailing_particles(store):
    """口语里常带"的/了"，去掉后再查一次。"""
    assert store.lookup("完成了").entry.headword == "完成"
    assert store.lookup("完成的").entry.headword == "完成"
    # 去掉后仍查不到就如实返回未收录
    assert store.lookup("苹果了").entry is not None  # 苹果 有收录


def test_lookup_miss_returns_suggestions(store):
    result = store.lookup("完")
    assert result.found is False
    assert "完成" in result.suggestions
    # 纯前缀候选，不应乱给
    assert all(s.startswith("完") for s in result.suggestions)


def test_lookup_unknown_word(store):
    result = store.lookup("蹦蹦跳跳")
    assert result.found is False
    assert result.suggestions == ()


def test_lookup_empty(store):
    assert store.lookup("").found is False
    assert store.lookup(None).found is False


def test_count_reports_distinct_heads(store):
    # ROWS 里的简体词头：完成 / 苹果 / 漂亮 / 取消 / 截图 / 完成品
    assert store.count() == 6


def test_plain_text_is_copy_friendly(store):
    text = store.lookup("完成").entry.to_plain_text()
    assert "完成" in text
    assert "wán chéng" in text
    assert "accomplish" in text and "complete" in text
    # 纯文本不应含 HTML
    assert "<" not in text


def test_broken_file_degrades_gracefully(tmp_path):
    store = ZhEnStore(str(tmp_path / "nope.db"))
    try:
        assert store.lookup("完成").found is False
        assert store.count() == 0
    finally:
        store.close()


# ── 数据模型 ────────────────────────────────────────────────────────
def test_zh_en_entry_pos_groups_dedups_and_orders():
    entry = ZhEnEntry(
        headword="完成",
        pinyin="wán chéng",
        senses=(
            ZhEnSense("vt.", "complete"),
            ZhEnSense("vt.", "complete"),   # 重复应被去掉
            ZhEnSense("", "done"),
            ZhEnSense("vt.", "finish"),
        ),
    )
    assert entry.pos_groups() == (("vt.", ("complete", "finish")), ("", ("done",)))


def test_clean_query_strips_chinese_punctuation():
    assert clean_query("完成。") == "完成"
    assert clean_query("「完成」") == "完成"
    assert clean_query("  完成  ") == "完成"
