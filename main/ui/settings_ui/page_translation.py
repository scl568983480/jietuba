# -*- coding: utf-8 -*-
"""翻译设置页 — Fluent Design

仅保留与翻译行为相关的选项（目标语言 / 保留格式），外加「离线词典」分组。
翻译引擎本身只有 OpenAI 兼容接口，其 API 配置位于同级的 LLM 设置页
（page_llm.py）。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFileDialog,
)
from PySide6.QtCore import Qt
from ui.fluent_lite import (
    SwitchSettingCard, SettingCard as FSettingCard,
    FluentIcon, ComboBox, LineEdit, PushButton, CaptionLabel,
)
from .components import (
    SettingCardGroup, WhiteCard, adjust_button_width, apply_theme_text_style,
)

from translation.languages import TRANSLATION_LANGUAGES


def _dictionary_status_text(dialog, path_override=None) -> str:
    """「离线词典」分组底部的状态说明，便于排查"为什么没生效"。"""
    try:
        import os

        from dictionary import (
            EcdictStore,
            ZhEnStore,
            resolve_dictionary_path,
            resolve_zh_en_path,
        )

        if path_override is None:
            from dictionary import get_dictionary_service

            path = get_dictionary_service().path()
            zh_path = get_dictionary_service().zh_en_path()
        else:
            path = resolve_dictionary_path(path_override)
            zh_path = resolve_zh_en_path(path_override)

        lines = []
        if path:
            store = EcdictStore(path)
            try:
                count = store.count()
            finally:
                store.close()
            size_mb = os.path.getsize(path) / 1048576
            lines.append(
                dialog.tr("EN → ZH: {count} entries, {size} MB").format(
                    count=f"{count:,}", size=f"{size_mb:.1f}"
                )
            )
            lines.append(path)
        else:
            lines.append(dialog.tr(
                "No dictionary file found. Build ecdict_core.db with "
                "build_ecdict.py, or choose a file below."
            ))

        if zh_path:
            zh_store = ZhEnStore(zh_path)
            try:
                zh_count = zh_store.count()
            finally:
                zh_store.close()
            zh_size = os.path.getsize(zh_path) / 1048576
            lines.append(
                dialog.tr("ZH → EN: {count} entries, {size} MB").format(
                    count=f"{zh_count:,}", size=f"{zh_size:.1f}"
                )
            )
            lines.append(zh_path)
        else:
            lines.append(dialog.tr(
                "No Chinese → English dictionary. Build cedict_zh_en.db with "
                "build_cedict_zh_en.py to look Chinese words up offline."
            ))
        return "\n".join(lines)
    except Exception as exc:  # 状态说明失败不影响设置页
        return dialog.tr("Dictionary status unavailable: {error}").format(
            error=str(exc)
        )


def _choose_dictionary_file(dialog) -> None:
    """让用户挑一个 ecdict.db（保存由设置页的"保存"统一完成）。"""
    path, _ = QFileDialog.getOpenFileName(
        dialog,
        dialog.tr("Choose Dictionary File"),
        dialog.config_manager.get_dictionary_path() or "",
        "SQLite dictionary (*.db);;All files (*)",
    )
    if not path:
        return
    dialog.dictionary_path_input.setText(path)
    if hasattr(dialog, "dictionary_status_label"):
        dialog.dictionary_status_label.setText(
            _dictionary_status_text(dialog, path_override=path)
        )


def _clear_dictionary_path(dialog) -> None:
    """清空自定义路径，回到自动查找。"""
    dialog.dictionary_path_input.setText("")
    if hasattr(dialog, "dictionary_status_label"):
        dialog.dictionary_status_label.setText(_dictionary_status_text(dialog))


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

    # ════ 离线词典（ECDICT） ════
    grp_dict = SettingCardGroup(dialog.tr("Offline Dictionary"), page)

    dict_enable = SwitchSettingCard(
        FluentIcon.DOCUMENT,
        dialog.tr("Enable Offline Dictionary"),
        dialog.tr(
            "Look English words up locally in the compact popup, "
            "without calling the translation API."
        ),
        parent=grp_dict,
    )
    dict_enable.setChecked(dialog.config_manager.get_dictionary_enabled())
    dialog.dictionary_enable_toggle = dict_enable
    grp_dict.addSettingCard(dict_enable)

    skip_online = SwitchSettingCard(
        FluentIcon.SEND,
        dialog.tr("Skip Online When Found"),
        dialog.tr(
            "When the word is in the dictionary, show it immediately "
            "and skip the network request."
        ),
        parent=grp_dict,
    )
    skip_online.setChecked(dialog.config_manager.get_dictionary_skip_online())
    dialog.dictionary_skip_online_toggle = skip_online
    grp_dict.addSettingCard(skip_online)

    show_phonetic = SwitchSettingCard(
        FluentIcon.FONT,
        dialog.tr("Show Phonetic"),
        parent=grp_dict,
    )
    show_phonetic.setChecked(dialog.config_manager.get_dictionary_show_phonetic())
    dialog.dictionary_show_phonetic_toggle = show_phonetic
    grp_dict.addSettingCard(show_phonetic)

    show_tags = SwitchSettingCard(
        FluentIcon.CERTIFICATE,
        dialog.tr("Show Exam Tags and Collins Stars"),
        parent=grp_dict,
    )
    show_tags.setChecked(dialog.config_manager.get_dictionary_show_tags())
    dialog.dictionary_show_tags_toggle = show_tags
    grp_dict.addSettingCard(show_tags)

    show_rank = SwitchSettingCard(
        FluentIcon.SEARCH,
        dialog.tr("Show Word Frequency"),
        parent=grp_dict,
    )
    show_rank.setChecked(dialog.config_manager.get_dictionary_show_rank())
    dialog.dictionary_show_rank_toggle = show_rank
    grp_dict.addSettingCard(show_rank)

    show_forms = SwitchSettingCard(
        FluentIcon.SYNC,
        dialog.tr("Show Word Forms"),
        parent=grp_dict,
    )
    show_forms.setChecked(dialog.config_manager.get_dictionary_show_forms())
    dialog.dictionary_show_forms_toggle = show_forms
    grp_dict.addSettingCard(show_forms)

    # ── 汉英（中 → 英）──
    zh_en_enable = SwitchSettingCard(
        FluentIcon.SYNC,
        dialog.tr("Enable Chinese → English Lookup"),
        dialog.tr(
            "Look Chinese words up locally and show their English "
            "equivalents, without calling the translation API."
        ),
        parent=grp_dict,
    )
    zh_en_enable.setChecked(
        dialog.config_manager.get_dictionary_zh_en_enabled()
    )
    dialog.dictionary_zh_en_toggle = zh_en_enable
    grp_dict.addSettingCard(zh_en_enable)

    # 自定义词典文件（留空则自动查找）
    path_card = WhiteCard(grp_dict)
    path_row = QHBoxLayout(path_card)
    path_row.setContentsMargins(20, 12, 20, 12)
    path_row.setSpacing(10)
    path_title = QLabel(dialog.tr("Dictionary File"), path_card)
    apply_theme_text_style(path_title, 14)
    path_title.setFixedWidth(135)
    path_row.addWidget(path_title)

    dialog.dictionary_path_input = LineEdit(path_card, use_default_style=False)
    dialog.dictionary_path_input.setText(
        dialog.config_manager.get_dictionary_path()
    )
    dialog.dictionary_path_input.setPlaceholderText(
        dialog.tr("Auto (bundled dictionary)")
    )
    dialog.dictionary_path_input.setStyleSheet(dialog._get_input_style())
    path_row.addWidget(dialog.dictionary_path_input, 1)

    browse_btn = PushButton(dialog.tr("Browse"), path_card)
    browse_btn.setFixedHeight(32)
    adjust_button_width(browse_btn, min_width=56, horizontal_padding=1)
    browse_btn.clicked.connect(lambda: _choose_dictionary_file(dialog))
    path_row.addWidget(browse_btn)

    reset_btn = PushButton(dialog.tr("Auto"), path_card)
    reset_btn.setFixedHeight(32)
    adjust_button_width(reset_btn, min_width=48, horizontal_padding=1)
    reset_btn.clicked.connect(lambda: _clear_dictionary_path(dialog))
    path_row.addWidget(reset_btn)

    path_card.setFixedHeight(58)
    grp_dict.addSettingCard(path_card)

    # 状态说明：词典到底加载了没有、加载的是哪个文件
    dialog.dictionary_status_label = CaptionLabel(
        _dictionary_status_text(dialog), page
    )
    dialog.dictionary_status_label.setStyleSheet("padding: 5px;")
    dialog.dictionary_status_label.setWordWrap(True)

    layout.addWidget(grp_dict)
    layout.addWidget(dialog.dictionary_status_label)

    layout.addStretch()
    scroll.setWidget(page)
    return scroll
