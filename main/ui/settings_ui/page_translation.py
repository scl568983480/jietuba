# -*- coding: utf-8 -*-
"""翻译设置页 — Fluent Design

仅保留与翻译行为相关的选项（目标语言 / 忽略换行 / 保留格式）。
翻译引擎本身只有 OpenAI 兼容接口，其 API 配置位于同级的 LLM 设置页
（page_llm.py）。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QScrollArea,
)
from PySide6.QtCore import Qt
from ui.fluent_lite import (
    SwitchSettingCard, SettingCard as FSettingCard,
    FluentIcon, ComboBox,
)
from .components import SettingCardGroup

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
