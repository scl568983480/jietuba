# -*- coding: utf-8 -*-
"""离线词典服务：定位词典文件、懒加载、按设置查询。

词典文件的查找顺序（沿用项目里 OCR 模型 ``models/`` 的约定）::

    1. 设置里手动指定的路径
    2. 用户数据目录  %LOCALAPPDATA%\\Jietuba\\dictionary\\   （可选的全量词典）
    3. 打包后 exe 同级  <exe_dir>\\dictionary\\              （外置，可替换）
    4. 打包后包内      <_MEIPASS>\\dictionary\\
    5. 开发环境        main/dictionary/data/

同一目录下若同时存在全量词典与核心词典，优先用全量（覆盖更全）。
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

from .models import LookupResult, ZhEnLookupResult
from .store import EcdictStore, ZhEnStore, is_lookup_candidate, is_zh_lookup_candidate

CORE_DB_NAME = "ecdict_core.db"
FULL_DB_NAME = "ecdict_full.db"
# 汉英库（中→英），由 build_cedict_zh_en.py 从 CC-CEDICT 生成。
# CC-CEDICT 是 CC BY-SA 4.0，允许再分发（需署名）。
ZH_EN_DB_NAME = "cedict_zh_en.db"

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class DisplayOptions:
    """小窗词条卡片里显示哪些栏目。"""

    phonetic: bool = True
    tags: bool = True
    rank: bool = True
    forms: bool = True


def user_dictionary_dir() -> str:
    """可选全量词典的用户目录，与日志目录同一约定。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    return os.path.join(base, "Jietuba", "dictionary")


def _dir_candidates() -> Tuple[str, ...]:
    """按优先级给出可能存放词典文件的目录。"""
    dirs = [user_dictionary_dir()]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        dirs.append(os.path.join(exe_dir, "dictionary"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(os.path.join(meipass, "dictionary"))
    else:
        dirs.append(os.path.join(_PACKAGE_DIR, "data"))
    return tuple(dirs)


def resolve_dictionary_path(configured: str = "") -> Optional[str]:
    """找到第一个可用的词典文件；都没有则返回 ``None``。

    手动指定的路径无效时**不放弃**，而是回落到自动查找：设置里写错一个
    路径就让离线词典整个失效，比静默用回内置词典糟糕得多（设置页会显示
    实际加载的是哪个文件，用户能看出来）。
    """
    configured = (configured or "").strip()
    if configured:
        found = _match_in(configured)
        if found:
            return found
        try:
            from core.logger import log_warning

            log_warning(
                f"指定的词典路径不可用，回退到自动查找: {configured}",
                "Dictionary",
            )
        except Exception:
            pass

    for directory in _dir_candidates():
        found = _match_in(directory)
        if found:
            return found
    return None


def _match_in(path: str) -> Optional[str]:
    """``path`` 是词典文件就返回它；是目录则返回其中的全量/核心词典。"""
    if os.path.isfile(path):
        return path
    if os.path.isdir(path):
        for name in (FULL_DB_NAME, CORE_DB_NAME):
            candidate = os.path.join(path, name)
            if os.path.isfile(candidate):
                return candidate
    return None


def resolve_zh_en_path(configured: str = "") -> Optional[str]:
    """定位汉英（中→英）词典文件；找不到返回 ``None``。

    与 ``resolve_dictionary_path`` 同一套查找顺序，只是文件名不同。
    汉英库是**可选**的：没有它中→英 就继续走联网翻译。
    """
    configured = (configured or "").strip()
    if configured:
        if os.path.isfile(configured):
            return configured
        if os.path.isdir(configured):
            candidate = os.path.join(configured, ZH_EN_DB_NAME)
            if os.path.isfile(candidate):
                return candidate

    # 也看看"英→中词典"所在的目录：两个库常放在一起，
    # 用户只填了 ECDICT 的路径时，汉英库应该能被顺带找到。
    try:
        ecdict = resolve_dictionary_path(configured)
    except Exception:
        ecdict = None
    if ecdict:
        candidate = os.path.join(os.path.dirname(ecdict), ZH_EN_DB_NAME)
        if os.path.isfile(candidate):
            return candidate

    for directory in _dir_candidates():
        candidate = os.path.join(directory, ZH_EN_DB_NAME)
        if os.path.isfile(candidate):
            return candidate
    return None


class DictionaryService:
    """离线词典单例。任何异常都退化成"查不到"，绝不打断翻译流程。"""

    _instance: Optional["DictionaryService"] = None
    _instance_lock = threading.Lock()

    def __init__(self, settings=None):
        self._settings = settings
        self._store: Optional[EcdictStore] = None
        self._store_path: Optional[str] = None
        # 汉英库独立句柄：来源与许可都和 ECDICT 不同，分开管理
        self._zh_store: Optional[ZhEnStore] = None
        self._zh_store_path: Optional[str] = None
        self._lock = threading.Lock()

    # ── 单例 ────────────────────────────────────────────────────────
    @classmethod
    def instance(cls) -> "DictionaryService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """丢弃单例（测试与设置变更后重新加载用）。"""
        with cls._instance_lock:
            instance = cls._instance
            cls._instance = None
        if instance is not None:
            instance.close()

    # ── 配置 ────────────────────────────────────────────────────────
    def _config(self):
        if self._settings is None:
            from settings import get_tool_settings_manager

            self._settings = get_tool_settings_manager()
        return self._settings

    def _setting(self, key: str, default):
        try:
            return self._config().get_app_setting(key, default)
        except Exception:
            return default

    def is_enabled(self) -> bool:
        return bool(self._setting("dictionary_enabled", True))

    def skip_online_on_hit(self) -> bool:
        """命中词典时是否跳过联网翻译。"""
        return bool(self._setting("dictionary_skip_online", True))

    def display_options(self) -> DisplayOptions:
        return DisplayOptions(
            phonetic=bool(self._setting("dictionary_show_phonetic", True)),
            tags=bool(self._setting("dictionary_show_tags", True)),
            rank=bool(self._setting("dictionary_show_rank", True)),
            forms=bool(self._setting("dictionary_show_forms", True)),
        )

    # ── 词典句柄 ────────────────────────────────────────────────────
    def path(self) -> Optional[str]:
        configured = ""
        try:
            configured = self._config().get_app_setting("dictionary_path", "") or ""
        except Exception:
            configured = ""
        try:
            return resolve_dictionary_path(configured)
        except Exception:
            return None

    def store(self) -> Optional[EcdictStore]:
        """懒加载词典句柄；路径变化时自动重开。"""
        if not self.is_enabled():
            return None
        path = self.path()
        if not path:
            return None
        with self._lock:
            if self._store is not None and self._store_path == path:
                return self._store
            if self._store is not None:
                self._store.close()
            from core.logger import log_info

            log_info(f"离线词典已加载: {path}", "Dictionary")
            self._store = EcdictStore(path)
            self._store_path = path
            return self._store

    def is_available(self) -> bool:
        """启用且确实找到了词典文件。"""
        return self.store() is not None

    # ── 汉英（中 → 英） ─────────────────────────────────────────────
    def is_zh_en_enabled(self) -> bool:
        return bool(self._setting("dictionary_zh_en_enabled", True))

    def zh_en_path(self) -> Optional[str]:
        configured = ""
        try:
            configured = self._config().get_app_setting(
                "dictionary_zh_en_path", "") or ""
        except Exception:
            configured = ""
        try:
            return resolve_zh_en_path(configured)
        except Exception:
            return None

    def zh_en_store(self) -> Optional[ZhEnStore]:
        """懒加载汉英词典句柄；路径变化时自动重开。"""
        if not self.is_zh_en_enabled():
            return None
        path = self.zh_en_path()
        if not path:
            return None
        with self._lock:
            if self._zh_store is not None and self._zh_store_path == path:
                return self._zh_store
            if self._zh_store is not None:
                self._zh_store.close()
            from core.logger import log_info

            log_info(f"汉英词典已加载: {path}", "Dictionary")
            self._zh_store = ZhEnStore(path)
            self._zh_store_path = path
            return self._zh_store

    def is_zh_en_available(self) -> bool:
        return self.zh_en_store() is not None

    def lookup_zh_en(self, text: Optional[str]) -> ZhEnLookupResult:
        """查中文词头的英文对应词。不可用 / 出错时返回空结果。"""
        if not text:
            return ZhEnLookupResult()
        try:
            if not self.is_zh_en_enabled() or not is_zh_lookup_candidate(text):
                return ZhEnLookupResult()
            store = self.zh_en_store()
            if store is None:
                return ZhEnLookupResult()
            return store.lookup(text)
        except Exception as exc:  # 查词失败绝不能影响翻译
            from core.logger import log_exception

            log_exception(exc, "汉英词典查询失败")
            return ZhEnLookupResult()

    # ── 查询 ────────────────────────────────────────────────────────
    def lookup(self, text: Optional[str]) -> LookupResult:
        """查词。不可用 / 不合格 / 出错时一律返回空结果。"""
        if not text:
            return LookupResult()
        try:
            if not self.is_enabled() or not is_lookup_candidate(text):
                return LookupResult()
            store = self.store()
            if store is None:
                return LookupResult()
            return store.lookup(text)
        except Exception as exc:  # 查词失败绝不能影响翻译
            from core.logger import log_exception

            log_exception(exc, "离线词典查询失败")
            return LookupResult()

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
            self._store = None
            self._store_path = None
            if self._zh_store is not None:
                self._zh_store.close()
            self._zh_store = None
            self._zh_store_path = None


def get_dictionary_service() -> DictionaryService:
    """便捷入口。"""
    return DictionaryService.instance()
