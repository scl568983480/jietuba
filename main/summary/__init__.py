# -*- coding: utf-8 -*-
"""截图总结功能包 —— 提示词与大模型调用工具。

总结结果复用「截图工具栏翻译按钮」打开的翻译弹窗（TranslationDialog），
因此本包只提供提示词与后台大模型调用工具，不再持有独立弹窗。
"""

from .summary_manager import build_summary_prompt, SummaryLLMWorker

__all__ = ["build_summary_prompt", "SummaryLLMWorker"]
