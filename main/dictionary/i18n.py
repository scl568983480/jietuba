# -*- coding: utf-8 -*-
"""离线词典模块的翻译上下文。

词典相关的 UI 文案统一用 ``Dictionary`` 上下文，便于在
``main/translations/app_*.xml`` 里集中维护。

注意：``models`` 里的 ``_INFLECTION_HINTS`` 等用于匹配 **ECDICT 数据本身**
的中文串，不属于 UI 文案，不参与翻译。
"""
from __future__ import annotations

from core.i18n import make_tr

#: 绑定 "Dictionary" 上下文的翻译函数
tr = make_tr("Dictionary")

__all__ = ["tr"]
