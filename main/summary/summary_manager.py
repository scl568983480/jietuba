# -*- coding: utf-8 -*-
"""截图总结 — 大模型调用工具模块

仅提供两样东西供翻译管理器复用：
  - build_summary_prompt(target_lang): 根据目标语言返回对应提示词
  - SummaryLLMWorker: 后台调用 OpenAI 兼容 chat-completions 接口生成总结

提示词提供两个版本：
  - 总结成中文(含繁体)时使用中文提示词；
  - 总结成其它语言时使用英文提示词（并指明目标语言名）。

总结结果展示复用「截图工具栏翻译按钮」打开的翻译弹窗（TranslationDialog），
不再单独维护一个弹窗。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from PySide6.QtCore import QThread, Signal

from core import log_error, log_info


# ── 目标语言 → 英文名称（用于英文提示词中指明输出语言）──────────────
_LANG_NAMES_EN = {
    "ZH": "Chinese",
    "ZH-HANT": "Traditional Chinese",
    "EN": "English",
    "JA": "Japanese",
    "KO": "Korean",
    "VI": "Vietnamese",
    "DE": "German",
    "FR": "French",
    "ES": "Spanish",
    "IT": "Italian",
    "PT": "Portuguese",
    "RU": "Russian",
    "PL": "Polish",
    "NL": "Dutch",
    "TR": "Turkish",
    "TH": "Thai",
    "AR": "Arabic",
    "ID": "Indonesian",
    "UK": "Ukrainian",
}

# 以中文为输出目标的语言
_CHINESE_TARGETS = ("ZH", "ZH-HANT")

# 中文提示词（总结成中文时使用）
_SYSTEM_PROMPT_ZH = (
    "你是一个专业的文本总结助手。请阅读用户提供的文本"
    "（可能来自截图 OCR），并给出一个简洁、准确的中文总结。\n\n"
    "要求：\n"
    "1. 用中文提炼核心要点，语言精炼；\n"
    "2. 保留关键信息和关键数据（数字、名称、结论等）；\n"
    "3. 不要编造原文中不存在的内容；\n"
    "4. 不要添加解释性说明，直接输出总结内容；\n"
    "5. 如合适可使用要点列表。"
)

# 英文提示词模板（总结成其它语言时使用，{lang} 为目标语言英文名）
_SYSTEM_PROMPT_EN = (
    "You are a professional text summarization assistant. "
    "Read the text provided by the user (possibly from screenshot OCR) "
    "and produce a concise, accurate summary in {lang}.\n\n"
    "Requirements:\n"
    "1. Distill the core points concisely in {lang}.\n"
    "2. Preserve key information and key data (numbers, names, conclusions, etc.).\n"
    "3. Do not fabricate content that is not present in the original text.\n"
    "4. Do not add explanatory notes; output only the summary.\n"
    "5. Use a bullet list if appropriate."
)


def build_summary_prompt(target_lang: str) -> str:
    """根据目标语言返回对应的系统提示词。"""
    if target_lang in _CHINESE_TARGETS:
        return _SYSTEM_PROMPT_ZH
    lang_name = _LANG_NAMES_EN.get(target_lang, target_lang)
    return _SYSTEM_PROMPT_EN.format(lang=lang_name)


class SummaryLLMWorker(QThread):
    """后台调用 OpenAI 兼容 chat-completions 接口生成总结。"""

    finished_signal = Signal(bool, str)  # (成功, 总结文本或错误信息)

    def __init__(self, api_url: str, api_key: str, model: str,
                 system_prompt: str, user_text: str):
        super().__init__()
        self._api_url = api_url
        self._api_key = api_key
        self._model = model
        self._system_prompt = system_prompt
        self._user_text = user_text
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                return
            if not (self._api_url and self._api_key and self._model):
                self.finished_signal.emit(False, "大模型未配置（缺少 API 地址 / 密钥 / 模型）")
                return
            if not self._user_text or not self._user_text.strip():
                self.finished_signal.emit(False, "没有可总结的文本内容")
                return

            body = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": self._user_text},
                ],
                "temperature": 0.3,
            }
            payload = json.dumps(
                body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            request = urllib.request.Request(
                url=self._api_url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {self._api_key}",
                },
            )

            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                summary = self._extract_text(raw)
                if not summary:
                    self.finished_signal.emit(False, "大模型返回内容为空")
                    return
                self.finished_signal.emit(True, summary)
            except urllib.error.HTTPError as exc:
                try:
                    err_body = exc.read().decode("utf-8")
                    data = json.loads(err_body) if err_body else {}
                except (ValueError, UnicodeDecodeError):
                    data = {}
                msg = ""
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict):
                        msg = str(err.get("message") or err.get("type") or "")
                    elif isinstance(err, str):
                        msg = err
                msg = msg or exc.reason or f"HTTP {exc.code}"
                self.finished_signal.emit(False, f"大模型请求失败: {msg}")
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                self.finished_signal.emit(False, f"网络错误: {reason}")
            except (ValueError, UnicodeDecodeError) as exc:
                self.finished_signal.emit(False, f"解析大模型响应失败: {exc}")
            except Exception as exc:  # noqa: BLE001
                self.finished_signal.emit(False, f"总结失败: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.finished_signal.emit(False, f"总结失败: {exc}")

    @staticmethod
    def _extract_text(raw) -> str:
        try:
            choices = raw.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            return str(message.get("content", "") or "").strip()
        except (AttributeError, TypeError, KeyError):
            return ""
