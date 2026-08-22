# -*- coding: utf-8 -*-
"""翻译设置页 — Fluent Design"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QScrollArea,
)
from PySide6.QtCore import Qt
from ui.fluent_lite.theme import ACCENT
from ui.fluent_lite import (
    SwitchSettingCard, SettingCard as FSettingCard,
    FluentIcon, ComboBox, CaptionLabel, LineEdit,
    PushButton, HyperlinkButton,
)
from .components import SettingCardGroup, WhiteCard, adjust_button_width, apply_theme_text_style

from translation.languages import TRANSLATION_LANGUAGES
from translation.service import create_default_translation_service


def create_translation_page(dialog) -> QWidget:
    """创建翻译设置页面 — Fluent Design"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    page = QWidget()
    page.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 10, 0)
    layout.setSpacing(20)

    # ════ 翻译引擎 ════
    grp_engine = SettingCardGroup(dialog.tr("Translation Engine"), page)
    engine_card = FSettingCard(
        FluentIcon.LANGUAGE,
        dialog.tr("Translation Engine"),
        parent=grp_engine,
    )
    dialog.translation_provider_combo = ComboBox(engine_card)
    dialog.translation_provider_combo.setFixedWidth(180)
    service = create_default_translation_service(dialog.config_manager)
    current_provider = dialog.config_manager.get_translation_provider()
    current_provider_index = 0
    for index, metadata in enumerate(service.registry.available_providers()):
        dialog.translation_provider_combo.addItem(
            metadata.display_name, userData=metadata.provider_id
        )
        if metadata.provider_id == current_provider:
            current_provider_index = index
    dialog.translation_provider_combo.setCurrentIndex(current_provider_index)
    engine_card.hBoxLayout.addWidget(
        dialog.translation_provider_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    engine_card.hBoxLayout.addSpacing(16)
    grp_engine.addSettingCard(engine_card)
    layout.addWidget(grp_engine)

    # ════ OpenAI API ════
    grp_api = SettingCardGroup(dialog.tr("OpenAI API"), page)

    # API URL
    dialog.openapi_url_input = _add_text_setting(
        dialog,
        grp_api,
        dialog.tr("API URL"),
        dialog.config_manager.get_openapi_url(),
        "https://api.openai.com/v1/chat/completions",
    )

    # API Key（卡片，带显示/隐藏）
    key_card = WhiteCard(grp_api)
    key_h = QHBoxLayout(key_card)
    key_h.setContentsMargins(20, 12, 20, 12)
    key_h.setSpacing(10)

    key_lbl = QLabel(dialog.tr("API Key"), key_card)
    apply_theme_text_style(key_lbl, 14)
    key_lbl.setFixedWidth(100)
    key_h.addWidget(key_lbl)

    dialog.openapi_api_key_input = LineEdit(key_card, use_default_style=False)
    dialog.openapi_api_key_input.setPlaceholderText("sk-...")
    dialog.openapi_api_key_input.setText(
        dialog.config_manager.get_openapi_api_key()
    )
    dialog.openapi_api_key_input.setEchoMode(LineEdit.EchoMode.Password)
    dialog.openapi_api_key_input.setStyleSheet(dialog._get_input_style())
    key_h.addWidget(dialog.openapi_api_key_input, 1)

    dialog.show_api_key_btn = PushButton(dialog.tr("Show"), key_card)
    dialog.show_api_key_btn.setFixedHeight(32)
    adjust_button_width(dialog.show_api_key_btn, min_width=60)
    dialog.show_api_key_btn.clicked.connect(
        lambda: _toggle_api_key_visibility(dialog)
    )
    key_h.addWidget(dialog.show_api_key_btn)
    key_card.setFixedHeight(58)
    grp_api.addSettingCard(key_card)

    # Model
    dialog.openapi_model_input = _add_text_setting(
        dialog,
        grp_api,
        dialog.tr("Model"),
        dialog.config_manager.get_openapi_model(),
        "gpt-4o",
    )

    layout.addWidget(grp_api)

    # ════ Amazon Translate ════
    grp_amazon = SettingCardGroup(dialog.tr("Amazon Translate"), page)
    dialog.amazon_translate_region_input = _add_text_setting(
        dialog,
        grp_amazon,
        dialog.tr("AWS Region"),
        dialog.config_manager.get_amazon_translate_region(),
        "us-west-2",
    )
    dialog.amazon_translate_access_key_input = _add_text_setting(
        dialog,
        grp_amazon,
        dialog.tr("Access Key ID"),
        dialog.config_manager.get_amazon_translate_access_key_id(),
        "AKIA...",
    )
    dialog.amazon_translate_secret_key_input = _add_text_setting(
        dialog,
        grp_amazon,
        dialog.tr("Secret Access Key"),
        dialog.config_manager.get_amazon_translate_secret_access_key(),
        dialog.tr("Required"),
        password=True,
    )
    dialog.amazon_translate_session_token_input = _add_text_setting(
        dialog,
        grp_amazon,
        dialog.tr("Session Token"),
        dialog.config_manager.get_amazon_translate_session_token(),
        dialog.tr("Optional, for temporary credentials"),
        password=True,
    )
    layout.addWidget(grp_amazon)

    # ════ Google Cloud Translation ════
    grp_google = SettingCardGroup(
        dialog.tr("Google Cloud Translation"), page
    )
    dialog.google_translate_api_key_input = _add_text_setting(
        dialog,
        grp_google,
        dialog.tr("Google API Key"),
        dialog.config_manager.get_google_translate_api_key(),
        "AIza...",
        password=True,
    )
    layout.addWidget(grp_google)

    # ════ Azure Translator ════
    grp_azure = SettingCardGroup(dialog.tr("Azure Translator"), page)
    dialog.azure_translate_api_key_input = _add_text_setting(
        dialog,
        grp_azure,
        dialog.tr("Azure API Key"),
        dialog.config_manager.get_azure_translate_api_key(),
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        password=True,
    )
    dialog.azure_translate_region_input = _add_text_setting(
        dialog,
        grp_azure,
        dialog.tr("Azure Region"),
        dialog.config_manager.get_azure_translate_region(),
        "eastasia",
    )
    dialog.azure_translate_endpoint_input = _add_text_setting(
        dialog,
        grp_azure,
        dialog.tr("Azure Endpoint"),
        dialog.config_manager.get_azure_translate_endpoint(),
        dialog.tr("Optional, use default if empty"),
    )
    layout.addWidget(grp_azure)

    dialog.openapi_settings_group = grp_api
    dialog.amazon_translate_settings_group = grp_amazon
    dialog.google_translate_settings_group = grp_google
    dialog.azure_translate_settings_group = grp_azure
    dialog.translation_provider_combo.currentIndexChanged.connect(
        lambda _index: _update_provider_groups(dialog)
    )
    _update_provider_groups(dialog)

    # ════ 翻译选项 ════
    grp_opts = SettingCardGroup(dialog.tr("Translation Options"), page)

    # 目标语言
    lang_card = FSettingCard(
        FluentIcon.LANGUAGE,
        dialog.tr("Target Language"),
        parent=grp_opts,
    )
    dialog.translation_target_combo = ComboBox(lang_card)
    dialog.translation_target_combo.setFixedWidth(180)

    lang_options = [("", dialog.tr("Auto (System)"))]
    lang_options.extend(list(TRANSLATION_LANGUAGES.items()))
    current_lang = dialog.config_manager.get_app_setting(
        "translation_target_lang", ""
    )
    current_index = 0
    for i, (code, name) in enumerate(lang_options):
        dialog.translation_target_combo.addItem(name, userData=code)
        if code == current_lang:
            current_index = i
    dialog.translation_target_combo.setCurrentIndex(current_index)

    lang_card.hBoxLayout.addWidget(
        dialog.translation_target_combo, 0, Qt.AlignmentFlag.AlignRight
    )
    lang_card.hBoxLayout.addSpacing(16)
    grp_opts.addSettingCard(lang_card)

    # 忽略换行
    split_card = SwitchSettingCard(
        FluentIcon.ALIGNMENT,
        dialog.tr("Ignore Line Breaks"),
        dialog.tr("Merge multi-line text for better translation"),
        parent=grp_opts,
    )
    split_card.setChecked(dialog.config_manager.get_translation_split_sentences())
    dialog.split_sentences_toggle = split_card
    grp_opts.addSettingCard(split_card)

    # 保留格式
    preserve_card = SwitchSettingCard(
        FluentIcon.DOCUMENT,
        dialog.tr("Preserve Formatting"),
        dialog.tr("Keep original text formatting"),
        parent=grp_opts,
    )
    preserve_card.setChecked(
        dialog.config_manager.get_translation_preserve_formatting()
    )
    dialog.preserve_formatting_toggle = preserve_card
    grp_opts.addSettingCard(preserve_card)

    layout.addWidget(grp_opts)

    # 提示
    info_label = QLabel(
        "💡 "
        + dialog.tr(
            "OpenAI-compatible endpoint. Get an API key from your provider."
        )
        + f' <a href="https://platform.openai.com/api-keys" style="color:{ACCENT};">platform.openai.com</a>',
        page,
    )
    info_label.setOpenExternalLinks(True)
    info_label.setWordWrap(True)
    info_label.setStyleSheet("padding: 5px; font-size: 12px; color: #999;")
    layout.addWidget(info_label)
    dialog.openapi_translation_info_label = info_label
    _update_provider_groups(dialog)

    layout.addStretch()
    scroll.setWidget(page)
    return scroll


def _toggle_api_key_visibility(dialog):
    """切换 API 密钥显示/隐藏"""
    if dialog.openapi_api_key_input.echoMode() == QLineEdit.EchoMode.Password:
        dialog.openapi_api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
        dialog.show_api_key_btn.setText(dialog.tr("Hide"))
        adjust_button_width(dialog.show_api_key_btn, min_width=60)
    else:
        dialog.openapi_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        dialog.show_api_key_btn.setText(dialog.tr("Show"))
        adjust_button_width(dialog.show_api_key_btn, min_width=60)


def _add_text_setting(
    dialog,
    group,
    label: str,
    value: str,
    placeholder: str,
    *,
    password: bool = False,
):
    card = WhiteCard(group)
    row = QHBoxLayout(card)
    row.setContentsMargins(20, 12, 20, 12)
    row.setSpacing(10)
    title = QLabel(label, card)
    apply_theme_text_style(title, 14)
    title.setFixedWidth(135)
    row.addWidget(title)
    edit = LineEdit(card, use_default_style=False)
    edit.setText(value or "")
    edit.setPlaceholderText(placeholder)
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setStyleSheet(dialog._get_input_style())
    row.addWidget(edit, 1)
    card.setFixedHeight(58)
    group.addSettingCard(card)
    return edit


def _update_provider_groups(dialog) -> None:
    provider_id = dialog.translation_provider_combo.currentData()
    dialog.openapi_settings_group.setVisible(provider_id == "openapi")
    dialog.amazon_translate_settings_group.setVisible(
        provider_id == "amazon"
    )
    dialog.google_translate_settings_group.setVisible(
        provider_id == "google"
    )
    dialog.azure_translate_settings_group.setVisible(
        provider_id == "azure"
    )
    if hasattr(dialog, "openapi_translation_info_label"):
        dialog.openapi_translation_info_label.setVisible(
            provider_id == "openapi"
        )
    if hasattr(dialog, "split_sentences_toggle"):
        dialog.split_sentences_toggle.setVisible(
            provider_id == "openapi"
        )
    if hasattr(dialog, "preserve_formatting_toggle"):
        dialog.preserve_formatting_toggle.setVisible(
            provider_id == "openapi"
        )
