# -*- coding: utf-8 -*-
"""翻译设置页 — Fluent Design

仅保留各云翻译服务商（Amazon / Google / Azure）的配置与翻译选项。
大语言模型（LLM）相关的「翻译引擎」与 OpenAI 接口配置已拆到同级
的 LLM 设置页（page_llm.py）。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea,
)
from PySide6.QtCore import Qt
from ui.fluent_lite import (
    SwitchSettingCard, SettingCard as FSettingCard,
    FluentIcon, ComboBox,
)
from .components import (
    SettingCardGroup, apply_theme_text_style, _add_text_setting,
)

from translation.languages import TRANSLATION_LANGUAGES


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

    dialog.amazon_translate_settings_group = grp_amazon
    dialog.google_translate_settings_group = grp_google
    dialog.azure_translate_settings_group = grp_azure

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

    layout.addStretch()
    scroll.setWidget(page)
    return scroll
