# -*- coding: utf-8 -*-
"""离线词典的模型解析与查询层测试。

监控目标：
1. ECDICT 字段里的**字面量** ``\\n`` 必须还原成真正的换行（这是 ECDICT 的
   数据格式特点，用普通 split("\\n") 会得到一整行）。
2. 变形词必须归并到原形（gave → give、mice → mouse），否则用户查到
   "give的过去式"这种没有信息量的释义。
3. 查询必须走 ``word COLLATE NOCASE`` 索引：不加这个索引会退化成全表扫描
   （实测 13.55 ms/次 → 0.032 ms/次）。
4. 只有"英文单词/短语"才查词典，整段中英文混排必须交给翻译引擎。
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dictionary import (  # noqa: E402
    EcdictStore,
    clean_query,
    is_lookup_candidate,
    is_substantive,
    parse_forms,
    parse_inflection,
    parse_lemma,
    parse_senses,
    split_lines,
    stripword,
)
from dictionary.i18n import tr as _tr  # noqa: E402
from dictionary.models import EXCHANGE_LABELS, TAG_LABELS  # noqa: E402


def ex(code: str) -> str:
    """变形名在当前语言下的写法。

    断言不能写死 "Past tense"：测试顺序会影响全局翻译器状态
    （``test_i18n`` 会装上真实的 .qm），写死就会时好时坏。
    """
    return _tr(EXCHANGE_LABELS[code])


def tag(code: str) -> str:
    """考纲标签在当前语言下的写法。"""
    return _tr(TAG_LABELS[code])


# 与 build_ecdict.py 产出的表结构保持一致
SCHEMA = """
CREATE TABLE stardict (
    word VARCHAR(64) NOT NULL,
    sw VARCHAR(64) NOT NULL,
    phonetic VARCHAR(64),
    translation TEXT,
    collins INTEGER DEFAULT 0,
    oxford INTEGER DEFAULT 0,
    tag VARCHAR(64),
    bnc INTEGER DEFAULT 0,
    frq INTEGER DEFAULT 0,
    exchange TEXT
);
CREATE INDEX sd_word_nocase ON stardict(word COLLATE NOCASE);
CREATE INDEX sd_sw ON stardict(sw, word COLLATE NOCASE);
"""

ROWS = [
    # word, sw, phonetic, translation, collins, oxford, tag, bnc, frq, exchange
    ("give", "give", "giv", "n. 弹性, 适应性\\nvt. 给, 授予\\nvi. 捐赠",
     5, 1, "zk gk", 71, 98, "p:gave/d:given/i:giving/3:gives"),
    ("gave", "gave", "", "give的过去式", 0, 0, "", 0, 0, "0:give/1:p"),
    ("run", "run", "rʌn", "n. 跑, 赛跑\\nvi. 跑, 奔跑",
     5, 1, "gk", 202, 210, "p:ran/d:run/i:running/3:runs"),
    ("running", "running", "", "n. 赛跑, 流出, 运转\\na. 流动的, 跑着的",
     0, 0, "", 3269, 3252, "0:run/1:i/i:running/s:runnings"),
    ("look", "look", "luk", "vi. 看, 注意", 5, 1, "zk", 100, 120,
     "p:looked/d:looked/i:looking/3:looks"),
    ("looked", "looked", "", "v. 看, 瞧( look的过去式和过去分词 )",
     0, 0, "", 0, 0, "0:look/1:pd"),
    ("mouse", "mouse", "maus", "n. 老鼠, 鼠标", 4, 1, "zk gk", 2845, 3000,
     "s:mice"),
    ("mice", "mice", "", "pl. 老鼠", 0, 0, "", 0, 0, "0:mouse/1:s"),
    ("longtime", "longtime", "'lɔŋtaim", "a. 长时间的", 0, 0, "", 33518,
     3536, ""),
    ("hello", "hello", "hә'lәu", "interj. 喂, 嘿", 3, 1, "zk gk", 2319,
     2238, ""),
    ("bare", "bare", "bєә", "a. 赤裸的", 2, 0, "", 500, 600, ""),
]


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "ecdict_test.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO stardict VALUES (?,?,?,?,?,?,?,?,?,?)", ROWS
    )
    con.commit()
    con.close()
    instance = EcdictStore(str(db))
    yield instance
    instance.close()


# ── 字段解析 ────────────────────────────────────────────────────────
def test_split_lines_restores_literal_newlines():
    """ECDICT 字段里的换行是字面量 '\\n'，不是真正的换行符。"""
    assert split_lines("n. 弹性\\nvt. 给") == ["n. 弹性", "vt. 给"]
    assert split_lines("a. 第一\\r\\nb. 第二") == ["a. 第一", "b. 第二"]
    assert split_lines("") == []
    assert split_lines(None) == []


def test_parse_senses_splits_label_and_text():
    senses = parse_senses("n. 弹性, 适应性\\n[计] 计算机\\n裸的")
    assert [(s.label, s.text) for s in senses] == [
        ("n.", "弹性, 适应性"),
        ("[计]", "计算机"),
        ("", "裸的"),
    ]


def test_parse_lemma_and_inflection():
    assert parse_lemma("0:give/1:p") == "give"
    assert parse_inflection("0:give/1:p") == ex("p")
    # 多个代号按 exchange 里出现的顺序展开，用语言无关的 " / " 连接
    assert parse_inflection("0:perceive/1:pd") == f'{ex("p")} / {ex("d")}'
    assert parse_lemma("p:gave/d:given") == ""
    assert parse_inflection("") == ""


def test_parse_forms_skips_lemma_entries():
    forms = dict(parse_forms("p:gave/d:given/i:giving/3:gives/0:give/1:p"))
    assert forms[ex("p")] == "gave"
    assert forms[ex("d")] == "given"
    assert forms[ex("i")] == "giving"
    assert forms[ex("3")] == "gives"
    assert len(forms) == 4


def test_is_substantive_filters_pure_inflection_notes():
    assert is_substantive("give的过去式") is False
    assert is_substantive("v. 看, 瞧( look的过去式和过去分词 )") is False
    assert is_substantive("n. 赛跑, 流出, 运转") is True
    assert is_substantive("", "anything") is False
    assert is_substantive("same", "same") is False


# ── 查询文本清洗 ────────────────────────────────────────────────────
def test_clean_query_trims_punctuation_and_normalizes_apostrophes():
    assert clean_query("  Hello,  ") == "Hello"
    assert clean_query("“word”") == "word"
    assert clean_query("don\u2019t") == "don't"
    assert clean_query("give.") == "give"
    assert clean_query(None) == ""


def test_stripword_matches_ecdict_semantics():
    assert stripword("long-time") == "longtime"
    assert stripword("Long Time") == "longtime"


def test_is_lookup_candidate_accepts_words_and_short_phrases():
    assert is_lookup_candidate("hello")
    assert is_lookup_candidate("give up")
    assert is_lookup_candidate("long-time")
    assert is_lookup_candidate("don't")


def test_is_lookup_candidate_rejects_text_for_the_translator():
    # 整段英文 / 中文 / 中英混排 / 超长 / 空 —— 都应交给翻译引擎
    assert not is_lookup_candidate(
        "This is a whole paragraph of English text."
    )
    assert not is_lookup_candidate("你好世界")
    assert not is_lookup_candidate("hello 你好")
    assert not is_lookup_candidate("a" * 80)
    assert not is_lookup_candidate("")
    assert not is_lookup_candidate(None)


# ── 查询 ────────────────────────────────────────────────────────────
def test_lookup_exact_word(store):
    entry = store.lookup("hello").entry
    assert entry.headword == "hello"
    assert entry.senses[0].text == "喂, 嘿"
    assert entry.collins == 3
    assert entry.oxford is True
    assert entry.tag_labels() == (tag("zk"), tag("gk"))
    assert entry.rank == 2238


def test_lookup_is_case_insensitive(store):
    assert store.lookup("HELLO").entry.headword == "hello"
    assert store.lookup("Hello").entry.headword == "hello"


def test_lookup_merges_inflected_form_into_lemma(store):
    """gave 必须显示 give 的释义，并标明"是 give 的过去式"。"""
    entry = store.lookup("gave").entry
    assert entry.headword == "give"
    assert entry.query == "gave"
    assert entry.inflection == ex("p")
    assert entry.is_inflected is True
    assert entry.senses[0].text == "弹性, 适应性"
    # "give的过去式"这类没有信息量的释义不应单独展示
    assert entry.variant_senses == ()
    assert dict(entry.forms)[ex("p")] == "gave"


def test_lookup_keeps_substantive_variant_senses(store):
    """running 除了是 run 的现在分词，自己也有实义，应当保留。"""
    entry = store.lookup("running").entry
    assert entry.headword == "run"
    assert entry.inflection == ex("i")
    assert [s.text for s in entry.variant_senses] == [
        "赛跑, 流出, 运转",
        "流动的, 跑着的",
    ]


def test_lookup_suppresses_redundant_variant_senses(store):
    """looked 的释义只是"看, 瞧( look的过去式和过去分词 )"，不该重复展示。"""
    entry = store.lookup("looked").entry
    assert entry.headword == "look"
    assert entry.inflection == f'{ex("p")} / {ex("d")}'
    assert entry.variant_senses == ()


def test_lookup_irregular_plural(store):
    entry = store.lookup("mice").entry
    assert entry.headword == "mouse"
    assert entry.inflection == ex("s")
    assert entry.senses[0].text == "老鼠, 鼠标"
    assert entry.variant_senses[0].text == "老鼠"


def test_lookup_fuzzy_matches_alternative_spelling(store):
    """"long-time"（带连字符）库里只收了 "longtime"，应当模糊命中。"""
    entry = store.lookup("long-time").entry
    assert entry.headword == "longtime"
    assert entry.match_kind == "fuzzy"


def test_lookup_miss_gives_prefix_suggestions(store):
    """未收录时给出同前缀的候选词（"baref" 没有前缀候选，"bar" 有）。"""
    result = store.lookup("bar")
    assert result.found is False
    assert "bare" in result.suggestions

    # 前缀匹配是严格的前缀，不是"字母序在它之后"
    assert store.lookup("baref").suggestions == ()


def test_lookup_unknown_word_returns_empty_result(store):
    result = store.lookup("zzzzqqqq")
    assert result.found is False
    assert result.suggestions == ()


def test_lookup_empty_query(store):
    assert store.lookup("").found is False
    assert store.lookup(None).found is False


def test_plain_text_contains_key_information(store):
    text = store.lookup("gave").entry.to_plain_text()
    assert "give" in text
    assert ex("p") in text
    assert "弹性, 适应性" in text
    # 「gave」是「give」的过去式
    assert "gave" in text


def test_query_plan_uses_nocase_index(store):
    """回归护栏：大小写不敏感查询必须命中索引，不能退化成全表扫描。"""
    plan = store._conn.execute(  # noqa: SLF001
        "EXPLAIN QUERY PLAN SELECT * FROM stardict "
        "WHERE word = ? COLLATE NOCASE",
        ("give",),
    ).fetchall()
    detail = " ".join(str(row[-1]) for row in plan).upper()
    assert "SEARCH" in detail
    assert "SCAN" not in detail


def test_missing_file_degrades_gracefully(tmp_path):
    """词典文件损坏/缺失时只能返回"查不到"，绝不抛异常打断翻译。"""
    store = EcdictStore(str(tmp_path / "nope.db"))
    try:
        result = store.lookup("hello")
        assert result.found is False
    finally:
        store.close()
