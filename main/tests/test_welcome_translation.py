from PySide6.QtCore import QSettings

from settings.tool_settings import ToolSettingsManager
from ui.welcome.page5_translation import TranslationPage


def _manager(tmp_path):
    settings = QSettings(
        str(tmp_path / "welcome_translation.ini"),
        QSettings.Format.IniFormat,
    )
    return ToolSettingsManager(qsettings=settings)


def test_welcome_translation_defaults_to_openapi(qapp, tmp_path):
    manager = _manager(tmp_path)
    page = TranslationPage(manager)

    assert manager.get_translation_provider() == "openapi"
    assert page._provider_combo.currentData() == "openapi"
    assert page.illus_area.isHidden()
    assert page._settings_card.property("welcomeSettingRow") is True
    assert (
        page._credential_stack.currentWidget()
        is page._provider_pages["openapi"]
    )

    page.close()


def test_welcome_translation_only_offers_the_openai_engine(qapp, tmp_path):
    """三个云翻译引擎已下线，下拉里只应剩下 OpenAI 兼容接口。"""
    page = TranslationPage(_manager(tmp_path))

    providers = [
        page._provider_combo.itemData(index)
        for index in range(page._provider_combo.count())
    ]

    assert providers == ["openapi"]
    assert set(page._provider_pages) == {"openapi"}

    page.close()


def test_welcome_translation_inputs_are_not_clipped(qapp, tmp_path):
    page = TranslationPage(_manager(tmp_path))
    page.show()
    qapp.processEvents()

    controls = [
        page._provider_combo,
        page._lang_combo,
        page._openapi_url_edit,
        page._openapi_key_edit,
        page._openapi_model_edit,
    ]
    for control in controls:
        assert control.height() >= control.sizeHint().height()

    page.close()


def test_welcome_translation_saves_openai_credentials(qapp, tmp_path):
    manager = _manager(tmp_path)
    page = TranslationPage(manager)
    page._openapi_url_edit.setText("https://api.example.com/v1/chat/completions")
    page._openapi_key_edit.setText("openai-key")
    page._openapi_model_edit.setText("gpt-4o")
    page._lang_combo.setCurrentIndex(page._lang_combo.findData("JA"))

    page.save()

    assert manager.get_translation_provider() == "openapi"
    assert manager.get_openapi_url() == (
        "https://api.example.com/v1/chat/completions"
    )
    assert manager.get_openapi_api_key() == "openai-key"
    assert manager.get_openapi_model() == "gpt-4o"
    assert manager.get_translation_provider_config("openapi") == {
        "api_url": "https://api.example.com/v1/chat/completions",
        "api_key": "openai-key",
        "model": "gpt-4o",
    }
    assert manager.get_translation_target_lang() == "JA"

    page.close()


def test_welcome_translation_panel_height_fits_the_credentials(qapp, tmp_path):
    page = TranslationPage(_manager(tmp_path))

    assert (
        page._credential_stack.height()
        >= page._provider_pages["openapi"].sizeHint().height() + 10
    )

    page.close()
