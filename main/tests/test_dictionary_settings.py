# -*- coding: utf-8 -*-
"""「离线词典」设置页的构造与持久化烟测。

设置页只被 SettingsDialog 用到，之前没有测试覆盖；这里用最小替身把页面
构造出来，确保新增的控件、文案与配置读写不会在运行时炸掉。
"""
import os
import sys

import pytest
from PySide6.QtCore import QSettings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from settings import ToolSettingsManager  # noqa: E402
from ui.settings_ui.page_translation import create_translation_page  # noqa: E402


class _FakeDialog:
    """只提供设置页真正用到的那几个接口。"""

    def __init__(self, config):
        self.config_manager = config

    def tr(self, text):
        return text

    def _get_input_style(self):
        return ""


@pytest.fixture
def config(tmp_path):
    # 直接构造实例：get_tool_settings_manager() 是全局单例，
    # 注入的 QSettings 只在首次创建时生效，测试之间会互相污染。
    return ToolSettingsManager(
        qsettings=QSettings(
            str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
        )
    )


def test_page_builds_and_defaults_are_on(qapp, config):
    page = create_translation_page(_FakeDialog(config))
    assert page is not None

    dialog = _FakeDialog(config)
    page = create_translation_page(dialog)

    # 默认全部开启，且控件确实挂到了 dialog 上（dialog.py 靠这些属性保存配置）
    for attr in (
        "dictionary_enable_toggle",
        "dictionary_skip_online_toggle",
        "dictionary_show_phonetic_toggle",
        "dictionary_show_tags_toggle",
        "dictionary_show_rank_toggle",
        "dictionary_show_forms_toggle",
    ):
        widget = getattr(dialog, attr, None)
        assert widget is not None, f"缺少设置控件 {attr}"
        assert widget.isChecked() is True, f"{attr} 默认应为开"

    assert dialog.dictionary_path_input.text() == ""
    # 状态说明必须给出可读文本（找不到词典时也要有提示，而不是空字符串）
    assert dialog.dictionary_status_label.text().strip()


def test_page_reflects_stored_values(qapp, config):
    config.set_dictionary_enabled(False)
    config.set_dictionary_skip_online(False)
    config.set_dictionary_show_phonetic(False)
    config.set_dictionary_path(r"C:\temp\my_ecdict.db")

    dialog = _FakeDialog(config)
    page = create_translation_page(dialog)
    assert page is not None

    assert dialog.dictionary_enable_toggle.isChecked() is False
    assert dialog.dictionary_skip_online_toggle.isChecked() is False
    assert dialog.dictionary_show_phonetic_toggle.isChecked() is False
    # 其他显示开关仍为默认开
    assert dialog.dictionary_show_tags_toggle.isChecked() is True
    assert dialog.dictionary_path_input.text() == r"C:\temp\my_ecdict.db"


def test_setting_roundtrip_via_manager(config):
    """get/set 成对存在且能落盘。"""
    assert config.get_dictionary_enabled() is True
    config.set_dictionary_enabled(False)
    assert config.get_dictionary_enabled() is False

    assert config.get_dictionary_skip_online() is True
    config.set_dictionary_skip_online(False)
    assert config.get_dictionary_skip_online() is False

    assert config.get_dictionary_path() == ""
    config.set_dictionary_path("  X:\\dict.db  ")
    assert config.get_dictionary_path() == "X:\\dict.db"  # 自动去空白

    for getter, setter in (
        ("get_dictionary_show_phonetic", "set_dictionary_show_phonetic"),
        ("get_dictionary_show_tags", "set_dictionary_show_tags"),
        ("get_dictionary_show_rank", "set_dictionary_show_rank"),
        ("get_dictionary_show_forms", "set_dictionary_show_forms"),
    ):
        assert getattr(config, getter)() is True
        getattr(config, setter)(False)
        assert getattr(config, getter)() is False


# ── 词典文件查找 ────────────────────────────────────────────────────
def _auto_dir(monkeypatch, tmp_path):
    """造一个"自动查找目录"，里面放一个占位的核心词典。"""
    from dictionary import service as service_mod

    data_dir = tmp_path / "auto_data"
    data_dir.mkdir()
    placeholder = data_dir / service_mod.CORE_DB_NAME
    placeholder.write_bytes(b"")
    monkeypatch.setattr(service_mod, "_dir_candidates", lambda: (str(data_dir),))
    return service_mod, placeholder


def test_configured_file_takes_priority(monkeypatch, tmp_path):
    service_mod, _ = _auto_dir(monkeypatch, tmp_path)
    chosen = tmp_path / "mine.db"
    chosen.write_bytes(b"")
    assert service_mod.resolve_dictionary_path(str(chosen)) == str(chosen)


def test_configured_directory_is_accepted(monkeypatch, tmp_path):
    service_mod, _ = _auto_dir(monkeypatch, tmp_path)
    custom = tmp_path / "custom"
    custom.mkdir()
    expected = custom / service_mod.FULL_DB_NAME
    expected.write_bytes(b"")
    assert service_mod.resolve_dictionary_path(str(custom)) == str(expected)


def test_broken_configured_path_falls_back_to_auto(monkeypatch, tmp_path):
    """设置里写错路径不应让离线词典整个失效。"""
    service_mod, placeholder = _auto_dir(monkeypatch, tmp_path)
    assert (
        service_mod.resolve_dictionary_path(r"Z:\definitely\missing.db")
        == str(placeholder)
    )


def test_no_dictionary_anywhere_returns_none(monkeypatch, tmp_path):
    from dictionary import service as service_mod

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(service_mod, "_dir_candidates", lambda: (str(empty),))
    assert service_mod.resolve_dictionary_path("") is None
    assert service_mod.resolve_dictionary_path(r"Z:\nope.db") is None

