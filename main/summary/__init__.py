# -*- coding: utf-8 -*-
"""截图总结功能包 —— 提示词与大模型调用工具。

总结结果由 ``TranslationManager`` 写入**独立的总结窗口**（与截图翻译窗口
互不复用、互不打断），因此本包只提供提示词与后台大模型调用工具，
不持有任何窗口。
"""

from .summary_manager import build_summary_prompt, SummaryLLMWorker

__all__ = ["build_summary_prompt", "SummaryLLMWorker"]
