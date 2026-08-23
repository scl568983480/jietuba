# -*- coding: utf-8 -*-
"""大模型（LLM）设置页 — Fluent Design

从「翻译」设置中拆出的大语言模型相关配置：翻译引擎选择与 OpenAI 兼容接口
（API URL / API Key / Model）。与「翻译」为同级导航项。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea,
)
from PySide6.QtCore import Qt
from ui.fluent_lite.theme import ACCENT
from ui.fluent_lite import (
    SettingCard as FSettingCard,
    FluentIcon, ComboBox, LineEdit, PushButton,
)
from .components import (
    SettingCardGroup, WhiteCard, adjust_button_width,
    apply_theme_text_style, _add_text_setting, _toggle_api_key_visibility,
)

from translation.service import create_default_translation_service


def create_llm_page(dialog) -> QWidget:
    """创建大模型（LLM）设置页面 — Fluent Design"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    page = QWidget()
    page.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 10, 0)
    layout.setSpacing(20)

    # ════ 翻译引擎 ════
    grp_engine = SettingCardGroup(dialog.tr("LLM引擎"), page)
    engine_card = FSettingCard(
        FluentIcon.AI,
        dialog.tr("LLM引擎"),
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
    adjust_button_width(dialog.show_api_key_btn, min_width=40, horizontal_padding=1)
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
        "hy3",
    )

    layout.addWidget(grp_api)
    dialog.openapi_settings_group = grp_api

    # 提示
    info_label = QLabel(
        "💡 "
        + dialog.tr(
            "LLM配置项"
        ),
        page,
    )
    info_label.setOpenExternalLinks(True)
    info_label.setWordWrap(True)
    info_label.setStyleSheet("padding: 5px; font-size: 12px; color: #999;")
    layout.addWidget(info_label)
    dialog.openapi_translation_info_label = info_label

    dialog.translation_provider_combo.currentIndexChanged.connect(
        lambda _index: dialog._update_provider_groups()
    )
    dialog._update_provider_groups()

    layout.addStretch()
    scroll.setWidget(page)
    return scroll
