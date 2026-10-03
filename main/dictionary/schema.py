# -*- coding: utf-8 -*-
"""汉英（中 → 英）词典的表结构与打包约定。

生成方是仓库根目录的 ``build_cedict_zh_en.py``，读取方是 ``store.ZhEnStore``。
两边必须对表结构和分隔符保持一致，因此这里集中定义，并有测试守护一致性
（``main/tests/test_dictionary_zh_en.py``）。

表结构::

    CREATE TABLE zh_en (
        simp   TEXT NOT NULL,   -- 简体词头
        trad   TEXT NOT NULL,   -- 繁体词头（与简体相同即为同一写法）
        pinyin TEXT,            -- 带声调的拼音，如 "wán chéng"
        senses TEXT NOT NULL    -- 释义打包，见下面的分隔符
    );
    CREATE INDEX idx_zh_simp ON zh_en(simp);
    CREATE INDEX idx_zh_trad ON zh_en(trad);

一条记录 = 一个词条（不是一条释义），释义打包进 ``senses`` 以省体积：
``"v.\x1fcomplete\x1ev.\x1faccomplish"``。
两个分隔符都取自不可打印控制字符，绝不会出现在词条文本里。
"""

#: 释义之间
SENSE_SEP = "\x1e"
#: 词性与释义文本之间
FIELD_SEP = "\x1f"

TABLE = "zh_en"

__all__ = ["SENSE_SEP", "FIELD_SEP", "TABLE"]
