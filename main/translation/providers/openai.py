"""OpenAI-compatible (OpenAPI) chat-completions translation provider.

This provider talks to any service that exposes an OpenAI-style
``/v1/chat/completions`` endpoint. Configuration items are the full
API URL, the API key and the model name.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping

from core import log_error

from ..models import (
    TranslationErrorCode,
    TranslationRequest,
    TranslationResult,
)
from ..provider import TranslationProvider


# Human-readable language names keyed by the application's BCP-47-style codes.
_LANGUAGE_NAMES = {
    "zh-Hans": "Chinese (Simplified)",
    "zh-Hant": "Chinese (Traditional)",
    "en": "English",
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "ja": "Japanese",
    "ko": "Korean",
    "vi": "Vietnamese",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "pt-BR": "Portuguese (Brazil)",
    "pt-PT": "Portuguese (Portugal)",
    "ru": "Russian",
    "pl": "Polish",
    "nl": "Dutch",
    "tr": "Turkish",
    "th": "Thai",
    "ar": "Arabic",
    "id": "Indonesian",
    "uk": "Ukrainian",
}


class OpenAPITranslateProvider(TranslationProvider):
    """Translate text via an OpenAI-compatible chat-completions endpoint."""

    provider_id = "openapi"
    display_name = "OpenAI API"

    def __init__(self, config: Mapping[str, Any]):
        self._api_url = str(config.get("api_url", "") or "").strip()
        self._api_key = str(config.get("api_key", "") or "").strip()
        self._model = str(config.get("model", "") or "").strip()

    def is_configured(self) -> bool:
        return bool(self._api_url and self._api_key and self._model)

    @classmethod
    def _language_name(cls, code: str | None) -> str:
        if not code:
            return ""
        return _LANGUAGE_NAMES.get(code, code)

    def translate(self, request: TranslationRequest) -> TranslationResult:
        if not request.text or not request.text.strip():
            return self._error(
                TranslationErrorCode.INVALID_REQUEST, "Text is empty"
            )
        if not self.is_configured():
            return self._error(
                TranslationErrorCode.NOT_CONFIGURED,
                "OpenAI API is not configured (URL, API key or model missing)",
            )

        target_name = (
            self._language_name(request.target_lang) or request.target_lang
        )
        system_parts = [
            "You are a professional translator.",
            f"Translate the user's text into {target_name}.",
            "Output only the translated text. Do not add explanations, "
            "notes, quotation marks, or markdown formatting.",
        ]
        if request.source_lang:
            source_name = (
                self._language_name(request.source_lang) or request.source_lang
            )
            system_parts.append(f"The source language is {source_name}.")
        system_prompt = " ".join(system_parts)

        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.text},
            ],
            "temperature": 0,
        }
        payload = json.dumps(
            body, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        http_request = urllib.request.Request(
            url=self._api_url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self._api_key}",
            },
        )

        try:
            with urllib.request.urlopen(
                http_request, timeout=request.timeout
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))
            translated = self._extract_text(raw)
            if not translated:
                return self._error(
                    TranslationErrorCode.UNKNOWN,
                    "Invalid OpenAI API response",
                )
            return TranslationResult(
                success=True,
                translated_text=translated,
                detected_source_lang="",
            )
        except urllib.error.HTTPError as exc:
            return self._http_error(exc)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            log_error(
                f"OpenAI API network error: {reason}", "OpenAPITranslate"
            )
            return self._error(
                TranslationErrorCode.NETWORK_ERROR,
                f"Network error: {reason}",
            )
        except TimeoutError as exc:
            log_error(
                f"OpenAI API request timed out: {exc}", "OpenAPITranslate"
            )
            return self._error(
                TranslationErrorCode.NETWORK_ERROR,
                "Translation request timed out, please try again later",
            )
        except (ValueError, UnicodeDecodeError) as exc:
            log_error(
                f"OpenAI API response error: {exc}", "OpenAPITranslate"
            )
            return self._error(
                TranslationErrorCode.UNKNOWN,
                "Failed to parse OpenAI API response",
            )
        except Exception as exc:
            log_error(
                f"OpenAI API request failed: {exc}", "OpenAPITranslate"
            )
            return self._error(
                TranslationErrorCode.UNKNOWN,
                f"Translation failed: {exc}",
            )

    @staticmethod
    def _extract_text(raw: Mapping[str, Any]) -> str:
        try:
            choices = raw.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            return str(message.get("content", "") or "").strip()
        except (AttributeError, TypeError, KeyError):
            return ""

    @staticmethod
    def _extract_error_message(data: Mapping[str, Any]) -> str:
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or "")
        if isinstance(error, str):
            return error
        return ""

    def _http_error(self, error: urllib.error.HTTPError) -> TranslationResult:
        try:
            body = error.read()
            data = json.loads(body.decode("utf-8")) if body else {}
        except (ValueError, UnicodeDecodeError):
            data = {}
        message = (
            self._extract_error_message(data)
            or error.reason
            or f"HTTP {error.code}"
        )
        code = self._map_error_code(message, error.code)
        log_error(
            f"OpenAI API HTTP {error.code}: {message}", "OpenAPITranslate"
        )
        return self._error(code, message)

    @staticmethod
    def _map_error_code(message: str, status_code: int) -> TranslationErrorCode:
        lowered = message.lower()
        if status_code in (401, 403):
            return TranslationErrorCode.AUTH_FAILED
        if (
            "invalid api key" in lowered
            or "incorrect api key" in lowered
            or "unauthorized" in lowered
            or "authentication" in lowered
        ):
            return TranslationErrorCode.AUTH_FAILED
        if (
            status_code == 429
            or "rate limit" in lowered
            or "quota" in lowered
            or "insufficient" in lowered
        ):
            return TranslationErrorCode.RATE_LIMITED
        if status_code >= 500:
            return TranslationErrorCode.NETWORK_ERROR
        if status_code == 400:
            return TranslationErrorCode.INVALID_REQUEST
        return TranslationErrorCode.UNKNOWN

    @staticmethod
    def _error(
        code: TranslationErrorCode, message: str
    ) -> TranslationResult:
        return TranslationResult(
            success=False,
            error_code=code,
            error_message=message,
        )
