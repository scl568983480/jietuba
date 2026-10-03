# -*- coding: utf-8 -*-
"""离线词典模块 —— 基于 ECDICT 的本地英汉词典。

主要组件::

    DictionaryService  单例，定位词典文件并按设置查询
    EcdictStore        某个 ecdict.db 的只读查询句柄
    DictEntry          一次查词的结果（已把变形词归并到原形）

用法::

    from dictionary import get_dictionary_service

    result = get_dictionary_service().lookup("gave")
    if result.found:
        print(result.entry.to_plain_text())

词典文件由仓库根目录的 ``build_ecdict.py`` 从 ECDICT 的 ecdict.csv 生成。
数据来源：ECDICT（https://github.com/skywind3000/ECDICT），MIT License。
"""

from .models import (
    DictEntry,
    DictSense,
    LookupResult,
    ZhEnEntry,
    ZhEnLookupResult,
    ZhEnSense,
    is_substantive,
    parse_forms,
    parse_inflection,
    parse_lemma,
    parse_senses,
    split_lines,
)
from .service import (
    CORE_DB_NAME,
    FULL_DB_NAME,
    ZH_EN_DB_NAME,
    DictionaryService,
    DisplayOptions,
    get_dictionary_service,
    resolve_dictionary_path,
    resolve_zh_en_path,
    user_dictionary_dir,
)
from .store import (
    EcdictStore,
    ZhEnStore,
    clean_query,
    contains_cjk,
    is_lookup_candidate,
    is_zh_lookup_candidate,
    stripword,
)

__all__ = [
    "DictEntry",
    "DictSense",
    "LookupResult",
    "ZhEnEntry",
    "ZhEnSense",
    "ZhEnLookupResult",
    "DisplayOptions",
    "DictionaryService",
    "EcdictStore",
    "ZhEnStore",
    "get_dictionary_service",
    "resolve_dictionary_path",
    "resolve_zh_en_path",
    "user_dictionary_dir",
    "CORE_DB_NAME",
    "FULL_DB_NAME",
    "ZH_EN_DB_NAME",
    "clean_query",
    "contains_cjk",
    "is_lookup_candidate",
    "is_zh_lookup_candidate",
    "is_substantive",
    "parse_forms",
    "parse_inflection",
    "parse_lemma",
    "parse_senses",
    "split_lines",
    "stripword",
]
