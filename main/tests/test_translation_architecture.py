import json

from translation.models import (
    TranslationErrorCode,
    TranslationRequest,
    TranslationResult,
)
from translation.provider import TranslationProvider
from translation.providers.openai import OpenAPITranslateProvider
from translation.registry import ProviderRegistry
from translation.service import TranslationService


class _Config:
    def __init__(self, active="fake", provider_config=None):
        self.active = active
        self.provider_config = provider_config or {}

    def get_translation_provider(self):
        return self.active

    def get_translation_provider_config(self, provider_id):
        return self.provider_config.get(provider_id, {})


class _FakeProvider(TranslationProvider):
    provider_id = "fake"
    display_name = "Fake"

    def __init__(self, config):
        self.config = config

    def is_configured(self):
        return bool(self.config.get("token"))

    def translate(self, request):
        return TranslationResult(
            success=True,
            translated_text=f"{request.target_lang}:{request.text}",
        )


def _service(config=None):
    registry = ProviderRegistry()
    registry.register("fake", _FakeProvider, display_name="Fake Provider")
    return TranslationService(
        registry,
        config or _Config(provider_config={"fake": {"token": "ok"}}),
    )


def test_service_selects_registered_provider_from_config():
    service = _service()

    result = service.translate(TranslationRequest("hello", "ZH"))

    assert result.success
    assert result.translated_text == "zh-Hans:hello"
    assert service.provider_name() == "Fake Provider"


def test_service_reports_unconfigured_provider_without_calling_it():
    service = _service(_Config())

    result = service.translate(TranslationRequest("hello", "EN"))

    assert not result.success
    assert result.error_code is TranslationErrorCode.NOT_CONFIGURED


def test_provider_overrides_support_legacy_or_one_off_credentials():
    service = _service(_Config())

    result = service.translate(
        TranslationRequest("hello", "JA"),
        overrides={"token": "temporary"},
    )

    assert result.success
    assert result.translated_text == "ja:hello"


def test_openapi_provider_translates_through_chat_completions(monkeypatch):
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "你好，世界"}}]}
            ).encode("utf-8")

    def _urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(
        "translation.providers.openai.urllib.request.urlopen", _urlopen
    )
    provider = OpenAPITranslateProvider(
        {
            "api_url": "https://api.openai.com/v1/chat/completions",
            "api_key": "sk-test",
            "model": "gpt-4o",
        }
    )
    result = provider.translate(
        TranslationRequest("Hello world", "zh-Hans", timeout=9)
    )

    assert result.success
    assert result.translated_text == "你好，世界"
    assert captured["timeout"] == 9
    body = json.loads(captured["request"].data.decode("utf-8"))
    assert body["model"] == "gpt-4o"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"] == "Hello world"
    headers = {
        key.lower(): value
        for key, value in captured["request"].header_items()
    }
    assert headers["authorization"] == "Bearer sk-test"
    assert headers["content-type"].startswith("application/json")


def test_openapi_provider_reports_unconfigured_without_url_key_model():
    provider = OpenAPITranslateProvider(
        {"api_url": "", "api_key": "", "model": ""}
    )
    assert not provider.is_configured()
    result = provider.translate(TranslationRequest("hi", "en"))
    assert not result.success
    assert result.error_code is TranslationErrorCode.NOT_CONFIGURED
