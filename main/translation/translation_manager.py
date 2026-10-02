# -*- coding: utf-8 -*-
"""
translation_manager.py - 翻译 / 总结窗口单例管理器

负责管理各个结果窗口，实现与钉图、截图窗口的解耦。

结果表面（surface）共三个，各自持有窗口与在途请求：

* ``dialog`` —— 截图翻译主窗口（钉图翻译、托盘「打开翻译窗口」也复用）
* ``summary`` —— 截图总结主窗口（OCR + 大模型总结）
* ``compact`` —— 划词翻译小窗

独立性约定：
1. **翻译与总结互不打断**：两个主窗口各自独立存在，可以同时打开，
   先出的译文不会被后一次总结覆盖，各自在途的网络/OCR 请求也不会
   被对方取消。
2. ``dialog`` 与 ``compact`` 仍是同一个「翻译」功能的两副面孔，
   保持原有的互斥（打开一个就隐藏另一个）与共用一条请求通道。
3. 单例模式、复用窗口、关闭即清理、解耦设计的原有特点不变。

使用方式：
    from translation import TranslationManager
    
    # 获取单例（不会创建窗口）
    manager = TranslationManager.instance()
    
    # 翻译文本（创建或复用翻译窗口）
    manager.translate(
        text="Hello World",
        target_lang="zh-Hans",
        position=QPoint(100, 100)  # 可选，窗口位置
    )

    # 截图总结（创建或复用总结窗口，不影响翻译窗口）
    manager.summarize_from_image(pixmap)
"""

import time
from typing import Optional
from PySide6.QtCore import QCoreApplication, QObject, QPoint, QTimer, Signal

from core import log_info, log_debug, log_error, log_warning
from core.ui_theme import get_ui_theme
from .language_detection import describe_languages, translation_direction


# ── 结果表面 ────────────────────────────────────────────────────────
# 翻译主窗口 / 总结主窗口各自独立；划词小窗与翻译主窗口互斥。
RESULT_DIALOG = "dialog"      # 截图翻译主窗口
RESULT_SUMMARY = "summary"    # 截图总结主窗口
RESULT_COMPACT = "compact"    # 划词翻译小窗
MAIN_SURFACES = (RESULT_DIALOG, RESULT_SUMMARY)
# 翻译通道（主窗口 + 小窗共用一条请求通道）/ 总结通道
TRANSLATION_SURFACES = (RESULT_DIALOG, RESULT_COMPACT)

# 新主窗口相对于另一个已打开主窗口的层叠偏移，避免两个窗口完全重叠
CASCADE_OFFSET = QPoint(38, 38)


class TranslationManager(QObject):
    """翻译窗口单例管理器"""
    
    _instance: Optional['TranslationManager'] = None
    
    # 信号
    translation_started = Signal(str)  # 开始翻译，参数为原文
    translation_finished = Signal(bool, str, str)  # 翻译完成(成功, 译文, 错误信息)
    
    def __init__(self, translation_service=None):
        # 避免重复初始化
        if '_initialized' in self.__dict__:
            return
        super().__init__()
        self._initialized = True
        self._dialogs: dict[str, object] = {}  # surface -> TranslationLoadingDialog
        self._popup = None  # TranslationPopup 实例
        self._thread = None  # 翻译通道在途线程（主窗口与小窗共用一条通道）
        self._summary_thread = None  # 总结通道在途线程
        self._threads = set()  # Keep superseded network workers alive until they exit.
        self._request_token = 0  # 翻译通道请求令牌
        self._summary_token = 0  # 总结通道请求令牌
        self._request_targets = {}
        self._active_target = RESULT_DIALOG
        self._target_lang = "ZH"
        self._ocr_thread = None  # 翻译通道在途 OCR 线程
        self._summary_ocr_thread = None  # 总结通道在途 OCR 线程
        # 小窗本次会话是否被用户手动指定过目标语言
        self._popup_user_target = False
        # 小窗是否正处于一次取词会话中（用于区分"同一会话重译"与"新一次取词"）
        self._popup_session_active = False
        self._api_key = ""
        self._use_pro = False
        self._legacy_provider_override = False
        self._split_sentences = "nonewlines"  # 分句模式: "0"=不分句, "1"=自动分句, "nonewlines"=忽略换行
        self._preserve_formatting = True  # 保留格式
        if translation_service is None:
            from .service import create_default_translation_service

            translation_service = create_default_translation_service()
        self._translation_service = translation_service
        self._ui_theme = get_ui_theme()
        self._ui_theme.theme_changed.connect(self._on_ui_theme_changed)
        
        log_debug("TranslationManager 已初始化", "Translation")

    @staticmethod
    def _api_key_error() -> str:
        return QCoreApplication.translate(
            "TranslationDialog", "API key not configured"
        )

    def _resolve_api_key(self, api_key: str | None) -> str:
        """兼容旧调用方；新调用方应让 TranslationService 读取 Provider 配置。"""
        if api_key is None:
            return self._api_key
        self._legacy_provider_override = True
        self._api_key = api_key
        return api_key

    def _provider_overrides(self) -> dict:
        """Legacy callers no longer pass per-provider credentials; the
        active provider reads everything from its persisted configuration."""
        return {}

    def _backend_ready(self) -> bool:
        return self._translation_service.is_configured(
            overrides=self._provider_overrides()
        )

    def _backend_name(self) -> str:
        try:
            return self._translation_service.provider_name()
        except ValueError:
            return self.tr("Engine not configured")

    # ── 表面访问 ────────────────────────────────────────────────────
    @property
    def _dialog(self):
        """翻译主窗口。

        历史上管理器只持有一个 ``_dialog``；现在主窗口按表面分别保存
        （翻译 / 总结各自独立），这里保留同名读写入口指向翻译主窗口。
        """
        return self._dialogs.get(RESULT_DIALOG)

    @_dialog.setter
    def _dialog(self, dialog):
        if dialog is None:
            self._dialogs.pop(RESULT_DIALOG, None)
        else:
            self._dialogs[RESULT_DIALOG] = dialog

    def _surface_valid(self, surface: str) -> bool:
        """指定表面的主窗口是否仍然存在（未被 Qt 销毁）。"""
        dialog = self._dialogs.get(surface)
        if dialog is None:
            return False
        try:
            _ = dialog.isVisible()
            return True
        except RuntimeError:
            self._dialogs.pop(surface, None)
            return False

    def _hide_surface(self, surface: str) -> None:
        widget = self._popup if surface == RESULT_COMPACT else self._dialogs.get(surface)
        if widget is None:
            return
        try:
            widget.hide()
        except RuntimeError:
            if surface != RESULT_COMPACT:
                self._dialogs.pop(surface, None)

    def _activate_surface(self, target: str) -> None:
        """把指定表面置为前台。

        翻译主窗口与总结主窗口互不隐藏（两份结果可以同时留在屏幕上）；
        划词小窗与翻译主窗口保持原有的互斥关系。
        """
        self._active_target = target
        if target == RESULT_COMPACT:
            self._hide_surface(RESULT_DIALOG)
        else:
            self._hide_surface(RESULT_COMPACT)

    def _current_theme_name(self) -> str:
        """Return the effective application theme used by translation surfaces."""
        return "dark" if self._ui_theme.is_dark else "light"

    def _on_ui_theme_changed(self, tokens) -> None:
        """Refresh any translation surfaces that have already been created."""
        theme_name = "dark" if tokens.is_dark else "light"
        for surface in MAIN_SURFACES:
            if self._surface_valid(surface):
                self._dialogs[surface].set_theme(theme_name)
        if self._is_popup_valid():
            self._popup.set_theme(theme_name)

    # ── 目标语言：配置语言 / 显示语言 / 翻译方向 ────────────────────
    # 三者是分开的概念，别再混在一起：
    #   * 配置语言：设置项 translation_target_lang（空则跟随系统语言）。
    #     只有用户手动改语言才会变，自动判断永远不写它。
    #   * 显示语言：弹窗语言框上显示的文字，始终等于配置语言。
    #   * 翻译方向：本次真正发给翻译引擎的 target。用户本次手动选过 → 用用户选的；
    #     否则按源文判断（全是中文→英语，其它/混排→中文），判不出来 → 配置语言。
    #
    # 一次"弹窗会话"= 一次划词取词 / 一次截图，从取词到弹窗关闭；用户会话期间
    # 手动改语言只在这一次会话内有效，下一次取词重新按源文判断。
    # 总结窗口的规则相同，但配置语言是独立设置项 summary_target_lang
    # （没设过则跟随 translation_target_lang），语言框显示这个值。
    def configured_target_lang(self) -> str:
        """配置里的目标语言（用户设置；没设过则跟随系统语言）。"""
        try:
            from settings import get_tool_settings_manager

            return get_tool_settings_manager().get_translation_target_lang() or "ZH"
        except Exception as exc:
            log_warning(f"读取目标语言设置失败: {exc}", "Translation")
            return "ZH"

    def configured_summary_target_lang(self) -> str:
        """配置里的总结语言（独立记忆，未单独设置时跟随翻译目标语言）。"""
        try:
            from settings import get_tool_settings_manager

            return get_tool_settings_manager().get_summary_target_lang() or "ZH"
        except Exception as exc:
            log_warning(f"读取总结目标语言设置失败: {exc}", "Translation")
            return self.configured_target_lang()

    def _configured_lang_for(self, surface: str) -> str:
        """该表面语言框显示的配置语言（翻译 / 总结各自独立记忆）。"""
        if surface == RESULT_SUMMARY:
            return self.configured_summary_target_lang()
        return self.configured_target_lang()

    def _badge_for(self, surface: str) -> tuple[str, bool]:
        """该表面标题栏角标（引擎名, 是否已配置）：总结看大模型，翻译看翻译引擎。"""
        if surface == RESULT_SUMMARY:
            return self._summary_backend_name(), self._summary_ready()
        return self._backend_name(), self._backend_ready()

    def _dialog_target_lang(self, surface: str) -> str:
        """该表面语言框当前选中的语言（没有窗口时返回空串）。"""
        dialog = self._dialogs.get(surface)
        if dialog is None:
            return ""
        try:
            return dialog.get_target_lang() or ""
        except RuntimeError:
            self._dialogs.pop(surface, None)
            return ""

    def _cascade_position(self, surface: str) -> Optional[QPoint]:
        """新主窗口的默认位置：另一个主窗口已打开时层叠错开。

        返回 ``None`` 表示交给窗口自己按光标所在屏幕居中
        （``TranslationDialog._place_initial_window`` 会把位置夹回屏幕内）。
        """
        for other in MAIN_SURFACES:
            if other == surface:
                continue
            dialog = self._dialogs.get(other)
            if dialog is None:
                continue
            try:
                if dialog.isVisible():
                    return dialog.pos() + CASCADE_OFFSET
            except RuntimeError:
                self._dialogs.pop(other, None)
        return None

    def _direction_for(self, source_text: str) -> str:
        """按源文语种给出翻译方向；判不出语种时回退到配置语言。"""
        direction = translation_direction(source_text)
        if direction:
            return direction
        return self.configured_target_lang()

    def _log_direction(self, target_lang: str, source_text: str) -> None:
        log_debug(
            f"翻译方向={target_lang} 配置语言={self.configured_target_lang()} "
            f"源文={describe_languages(source_text)}",
            "Translation",
        )

    def _dialog_user_selected_target_lang(self, surface: str = RESULT_DIALOG) -> bool:
        """该表面窗口里的目标语言是否被用户手动改过。"""
        if not self._surface_valid(surface):
            return False
        return bool(
            getattr(self._dialogs.get(surface), "target_lang_selected_by_user", False)
        )

    def _popup_user_selected_target_lang(self) -> bool:
        """小窗本次是否被用户手动指定过目标语言。"""
        return self._popup_user_target

    def _apply_popup_target_lang(self, display_lang: str) -> None:
        """把配置语言写进小窗语言框（界面显示用）。

        自动判断出来的方向不进语言框：语言框始终显示配置语言。
        """
        popup = self._ensure_popup()
        popup.set_target_lang(display_lang, auto=not self._popup_user_target)

    @classmethod
    def instance(cls) -> 'TranslationManager':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = TranslationManager()
        return cls._instance
    
    @classmethod
    def has_instance(cls) -> bool:
        """检查单例是否已创建"""
        return cls._instance is not None
    
    def configure(self, api_key: str, use_pro: bool = False, 
                  split_sentences: str = "nonewlines", preserve_formatting: bool = True):
        """
        配置翻译服务
        
        Args:
            api_key: API 密钥（兼容旧调用，现已忽略）
            use_pro: 是否使用 Pro 版 API（兼容旧调用，现已忽略）
            split_sentences: 分句模式 ("0"=不分句, "1"=自动分句, "nonewlines"=忽略换行)
            preserve_formatting: 保留格式
        """
        self._api_key = api_key
        self._use_pro = use_pro
        self._legacy_provider_override = True
        self._split_sentences = split_sentences
        self._preserve_formatting = preserve_formatting
        log_debug(f"翻译服务已配置 (Pro: {use_pro}, split_sentences: {split_sentences}, preserve_formatting: {preserve_formatting})", "Translation")
    
    def translate(
        self,
        text: str,
        api_key: str = None,
        target_lang: str = "ZH",
        source_lang: str = None,
        position: QPoint = None,
        use_pro: bool = None,
        split_sentences: str = None,
        preserve_formatting: bool = None
    ):
        """
        翻译文本
        
        如果翻译窗口不存在，创建新窗口；
        如果已存在，复用并更新内容。
        
        Args:
            text: 要翻译的文本（可为空，此时只显示窗口不翻译）
            api_key: API 密钥（兼容旧调用，现已忽略）
            target_lang: 目标语言代码
            source_lang: 源语言代码（可选，不传则自动检测）
            position: 窗口位置（可选）
            use_pro: 是否使用 Pro 版 API（可选）
            split_sentences: 分句模式 ("0"/"1"/"nonewlines")（可选）
            preserve_formatting: 保留格式（可选）
        """
        # 使用传入的参数或已配置的参数
        api_key = self._resolve_api_key(api_key)
        if use_pro is None:
            use_pro = self._use_pro
        if split_sentences is None:
            split_sentences = self._split_sentences
        if preserve_formatting is None:
            preserve_formatting = self._preserve_formatting
        
        # 保存配置供后续翻译使用
        if use_pro is not None:
            self._use_pro = use_pro
        self._split_sentences = split_sentences
        self._preserve_formatting = preserve_formatting

        # 方向：用户在弹窗里手动选过语言 → 用用户选的；否则按源文语种判断。
        # 配置语言不会被这里改写，语言框也照旧显示配置语言。
        if self._dialog_user_selected_target_lang():
            target_lang = self._dialog.get_target_lang() or self.configured_target_lang()
        else:
            target_lang = self._direction_for(text or "")
        self._log_direction(target_lang, text or "")
        self._target_lang = target_lang

        # 停止翻译通道之前未完成的请求（不影响总结窗口）
        self._stop_current_thread(RESULT_DIALOG)
        self._activate_surface(RESULT_DIALOG)

        # 创建或复用翻译主窗口
        self._ensure_dialog(RESULT_DIALOG, text, position, source_lang, target_lang)

        # 如果有文本，启动翻译；否则只显示空窗口
        if text and text.strip():
            if not self._backend_ready():
                log_error("翻译引擎未配置", "Translation")
                if self._is_dialog_valid():
                    self._dialog.set_translation_error(self._api_key_error())
                return
            log_info(f"开始翻译: target={target_lang} 原文={text[:50]}...", "Translation")
            self.translation_started.emit(text)

            self._start_translation(
                text, target_lang, source_lang,
                result_target=RESULT_DIALOG,
            )
        else:
            log_info("打开翻译窗口（待用户输入）", "Translation")
            # 清空译文区域，等待用户输入
            if self._is_dialog_valid():
                if self._backend_ready():
                    self._dialog.target_edit.clear()
                else:
                    self._dialog.set_translation_error(self._api_key_error())
                self._dialog.show()
                self._dialog.raise_()
                self._dialog.activateWindow()
                QTimer.singleShot(0, self._dialog.source_edit.setFocus)

    def translate_compact(
        self,
        text: str,
        api_key: str = None,
        target_lang: str = "ZH",
        source_lang: str = None,
        position: QPoint = None,
        use_pro: bool = None,
        split_sentences: str = None,
        preserve_formatting: bool = None,
    ):
        """Translate selected text in the compact result popup."""
        text = (text or "").strip()
        if not text:
            return self.translate(
                text="",
                api_key=api_key,
                target_lang=target_lang,
                source_lang=source_lang,
                position=position,
                use_pro=use_pro,
                split_sentences=split_sentences,
                preserve_formatting=preserve_formatting,
            )

        api_key = self._resolve_api_key(api_key)
        if use_pro is None:
            use_pro = self._use_pro
        if split_sentences is None:
            split_sentences = self._split_sentences
        if preserve_formatting is None:
            preserve_formatting = self._preserve_formatting

        self._use_pro = bool(use_pro)
        self._split_sentences = split_sentences
        self._preserve_formatting = preserve_formatting

        self._stop_current_thread(RESULT_COMPACT)
        self._activate_surface(RESULT_COMPACT)
        # 上一次弹窗会话结束（用户改语言/改文字触发的重译走别的入口），
        # 所以到这里说明这是一次新的划词取词：清掉上次会话的手动选择。
        if not self._popup_session_active:
            self._popup_user_target = False
        self._popup_session_active = False
        configured = self.configured_target_lang()
        popup = self._ensure_popup()
        # 语言框只显示配置语言；自动判断出来的方向不进语言框。
        popup.set_target_lang(
            configured, auto=not self._popup_user_selected_target_lang()
        )
        # 方向：本次会话里用户手动选过 → 用用户选的；否则按源文语种判断。
        if self._popup_user_selected_target_lang():
            target_lang = self._target_lang
        else:
            target_lang = self._direction_for(text)
        self._log_direction(target_lang, text)
        self._target_lang = target_lang
        popup.set_backend_status(self._backend_name(), self._backend_ready())
        # 划词翻译不抢焦点，否则原应用的选区和光标会丢失。
        popup.show_popup(text, position, activate=False)
        # 取词完成：现在开始的都是"这一次取词"的后续动作（用户改语言触发的重译、
        # 或弹窗内文字触发的重译），它们继续沿用手动选择；
        # 下一次 translate_compact() 会被视为新的取词。
        self._popup_session_active = True

        if not self._backend_ready():
            popup.show_error(self._api_key_error())
            return

        log_info(f"开始划词翻译: target={target_lang} 原文={text[:50]}...", "Translation")
        self.translation_started.emit(text)
        self._start_translation(
            text, target_lang, source_lang,
            result_target=RESULT_COMPACT,
        )

    def open_compact_input(
        self,
        api_key: str = None,
        target_lang: str = "ZH",
        source_lang: str = None,
        position: QPoint = None,
        use_pro: bool = None,
        split_sentences: str = None,
        preserve_formatting: bool = None,
    ):
        """Show the compact popup empty, with the caret in the input box."""
        api_key = self._resolve_api_key(api_key)
        if use_pro is None:
            use_pro = self._use_pro
        if split_sentences is None:
            split_sentences = self._split_sentences
        if preserve_formatting is None:
            preserve_formatting = self._preserve_formatting

        self._use_pro = bool(use_pro)
        self._split_sentences = split_sentences
        self._preserve_formatting = preserve_formatting
        # 新一次弹窗：开新会话，清掉上次会话的手动选择，回到配置语言。
        self._popup_session_active = True
        self._popup_user_target = False
        configured = self.configured_target_lang()
        self._target_lang = configured

        self._stop_current_thread(RESULT_COMPACT)
        self._activate_surface(RESULT_COMPACT)
        popup = self._ensure_popup()
        # 空输入框没有源文可判语种 → 用配置语言，语言框显示同一个值。
        self._apply_popup_target_lang(configured)
        popup.set_backend_status(self._backend_name(), self._backend_ready())
        # 没有待译文本，直接把焦点交给输入框。
        popup.show_popup("", position, activate=True)
        if not self._backend_ready():
            popup.show_error(self._api_key_error())

    def show_unrecognized_toast(self, position: QPoint | None = None) -> None:
        """划词未取到文本时，弹出不抢眼的提示，而不是打开翻译小窗。"""
        try:
            from core.toast import show_toast

            show_toast(
                "",
                QCoreApplication.translate(
                    "TranslationDialog", "Could not recognize the selected text"
                ),
                icon="info",
                duration_ms=1800,
                position=position,
            )
        except Exception as exc:  # 提示本身失败不应影响主流程
            from core.logger import log_exception

            log_exception(exc, "显示无法识别提示失败")

    def _on_popup_input_changed(self):
        """Invalidate an in-flight result as soon as manual text changes."""
        if self._active_target == RESULT_COMPACT and self._thread is not None:
            self._stop_current_thread(RESULT_COMPACT)

    def _on_popup_translate_requested(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        # 这次请求由弹窗内动作触发（用户改语言重译 / 编辑原文后重译），
        # 属于"当前这一次取词"的延续；处理完就结束本次会话，
        # 使下一次 translate_compact() 重新按源文判断方向。
        self._popup_session_active = False
        self._stop_current_thread(RESULT_COMPACT)
        self._activate_surface(RESULT_COMPACT)
        if not self._backend_ready():
            if self._is_popup_valid():
                self._popup.show_error(self._api_key_error())
            return
        # 方向：本次会话里用户手动选过 → 用用户选的；否则按输入的文字判断。
        if self._popup_user_selected_target_lang():
            target_lang = self._target_lang
        else:
            target_lang = self._direction_for(text)
        self._log_direction(target_lang, text)
        self._target_lang = target_lang
        log_info(f"开始小窗输入翻译: target={target_lang} 原文={text[:50]}...", "Translation")
        self.translation_started.emit(text)
        self._start_translation(
            text,
            target_lang,
            "auto",
            result_target=RESULT_COMPACT,
        )

    def _on_popup_target_lang_changed(self, new_lang: str):
        """用户在弹窗里手动改了目标语言：存成配置语言，本次弹窗方向也以它为准。"""
        self._target_lang = new_lang
        self._popup_user_target = True
        if self._is_popup_valid():
            self._popup.set_target_lang(new_lang, auto=False)
        try:
            from settings import get_tool_settings_manager

            get_tool_settings_manager().set_app_setting(
                "translation_target_lang", new_lang
            )
            log_debug(f"目标语言已更新为配置语言: {new_lang}", "Translation")
        except Exception as e:
            log_warning(f"保存目标语言失败: {e}", "Translation")

    def _ensure_popup(self):
        """Create the compact popup lazily and reuse it for fast subsequent calls."""
        if not self._is_popup_valid():
            from .translation_popup import TranslationPopup

            self._popup = TranslationPopup()
            self._popup.set_theme(self._current_theme_name())
            self._popup.open_full_requested.connect(self._open_full_from_popup)
            self._popup.manual_input_changed.connect(self._on_popup_input_changed)
            self._popup.manual_translate_requested.connect(
                self._on_popup_translate_requested
            )
            self._popup.target_lang_changed.connect(self._on_popup_target_lang_changed)
            # 语言框显示配置语言
            self._popup.set_target_lang(self.configured_target_lang())
        return self._popup

    def _is_popup_valid(self) -> bool:
        if self._popup is None:
            return False
        try:
            _ = self._popup.isVisible()
            return True
        except RuntimeError:
            self._popup = None
            return False

    def _open_full_from_popup(
        self, source_text: str, translated_text: str, error_text: str
    ):
        """Promote compact content to the full editor without re-requesting it."""
        self._activate_surface(RESULT_DIALOG)
        if self._thread is not None and self._thread.isRunning():
            self._request_targets[self._request_token] = RESULT_DIALOG
        target_lang = "ZH"
        try:
            from settings import get_tool_settings_manager

            target_lang = get_tool_settings_manager().get_translation_target_lang()
        except Exception:
            pass
        self._ensure_dialog(RESULT_DIALOG, source_text, None, "auto", target_lang)
        if not self._is_dialog_valid():
            return
        if translated_text:
            self._dialog.set_translation_result(translated_text)
        elif error_text:
            self._dialog.set_translation_error(error_text)
        elif self._thread is not None and self._thread.isRunning():
            self._dialog.set_loading()
        else:
            self._dialog.target_edit.clear()
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
    
    def _ensure_dialog(
        self,
        surface: str,
        text: str,
        position: QPoint,
        source_lang: str,
        target_lang: str
    ):
        """确保指定表面的主窗口存在（创建或复用）

        翻译（``RESULT_DIALOG``）与总结（``RESULT_SUMMARY``）各自持有一个窗口：
        进入总结不会复用或改写翻译窗口，两份结果可以同时留在屏幕上。

        ``target_lang`` 是本次翻译方向；语言框里显示的始终是该表面的配置语言
        （翻译 / 总结各自独立记忆），不会被自动方向覆盖。
        """
        from .translation_dialog import TranslationLoadingDialog
        configured = self._configured_lang_for(surface)
        is_summary = surface == RESULT_SUMMARY
        label = "总结" if is_summary else "翻译"

        if not self._surface_valid(surface):
            # 创建新窗口
            log_debug(f"创建新{label}窗口", "Translation")
            dialog = TranslationLoadingDialog(
                original_text=text,
                position=position,
                source_lang=source_lang or "auto",
                target_lang=configured,
                target_lang_setting_key=(
                    "summary_target_lang" if is_summary else "translation_target_lang"
                ),
            )
            self._dialogs[surface] = dialog
            # 语言框 = 配置语言（不是本次的自动方向）
            dialog.set_target_lang(configured)
            # 语义在创建时一次固定：翻译窗口永远是翻译，总结窗口永远是总结
            dialog.set_mode("summary" if is_summary else "translate")
            dialog.set_theme(self._current_theme_name())
            # 连接关闭信号（带上表面，销毁时只清理自己）
            dialog.destroyed.connect(
                lambda *_args, s=surface: self._on_dialog_destroyed(s)
            )
            # 连接翻译信号 (text, source_lang, target_lang)
            dialog.translate_requested.connect(self._on_translate_requested)
            dialog.set_backend_badge(*self._badge_for(surface))
            dialog.show()
        else:
            # 复用现有窗口
            dialog = self._dialogs[surface]
            log_debug(f"复用现有{label}窗口", "Translation")
            dialog.update_content(
                text,
                source_lang=source_lang or "auto"
            )

            # 没被用户手动改过时，语言框回到配置语言（而不是自动方向）。
            if not self._dialog_user_selected_target_lang(surface):
                dialog.set_target_lang(configured)

            # 只有有文本时才显示加载状态
            if text and text.strip():
                dialog.set_loading()

            dialog.set_backend_badge(*self._badge_for(surface))

            # 激活窗口（不隐藏另一个主窗口）
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
    
    def _is_dialog_valid(self) -> bool:
        """检查翻译主窗口是否有效（未被删除）"""
        return self._surface_valid(RESULT_DIALOG)
    
    def _start_translation(
        self,
        text: str,
        target_lang: str,
        source_lang: str,
        result_target: str,
    ):
        """Start a provider-neutral translation worker."""
        from .models import TranslationRequest
        from .worker import TranslationWorker

        provider_name = self._backend_name()
        from .language_detection import count_distinct_languages, is_all_chinese

        log_info(
            f"调用翻译引擎 {provider_name}: target={target_lang}, "
            f"源文语种数={count_distinct_languages(text)}"
            f"{'（全中文）' if is_all_chinese(text) else ''}, "
            f"preserve_formatting={self._preserve_formatting}",
            "Translation",
        )
        
        if result_target == RESULT_DIALOG and self._is_dialog_valid():
            self._dialog.set_loading()  # 翻译按钮置灰并显示翻译中...

        self._request_token += 1
        token = self._request_token
        self._request_targets[token] = result_target
        request = TranslationRequest(
            text=text,
            target_lang=target_lang,
            source_lang=source_lang,
            preserve_formatting=self._preserve_formatting,
            timeout=60,
            options={"split_sentences": self._split_sentences},
        )
        thread = TranslationWorker(
            self._translation_service,
            request,
            provider_overrides=self._provider_overrides(),
        )
        self._thread = thread
        self._threads.add(thread)
        thread.finished_signal.connect(
            lambda result, t=thread, n=token: self._on_thread_result(
                t,
                n,
                result.success,
                result.translated_text,
                result.error_message,
                result.detected_source_lang,
            )
        )
        thread.finished.connect(lambda t=thread: self._release_thread(t))
        thread.start()

    def _summary_ready(self) -> bool:
        """检查总结所用的大模型（OpenAI 兼容接口）是否已配置。"""
        from settings import get_tool_settings_manager

        mgr = get_tool_settings_manager()
        return bool(
            mgr.get_openapi_url()
            and mgr.get_openapi_api_key()
            and mgr.get_openapi_model()
        )

    def _summary_backend_name(self) -> str:
        """总结模式下角标展示的大模型名称。"""
        from settings import get_tool_settings_manager

        mgr = get_tool_settings_manager()
        model = mgr.get_openapi_model()
        return model or self.tr("LLM not configured")

    def _start_summary(self, text: str, target_lang: str):
        """OCR 完成后调用大模型总结（写入总结主窗口的结果区）。

        使用独立的总结通道令牌与线程槽，因此不会取消翻译窗口在途的请求。
        """
        from settings import get_tool_settings_manager
        from summary import SummaryLLMWorker, build_summary_prompt

        mgr = get_tool_settings_manager()
        system_prompt = build_summary_prompt(target_lang)
        if self._surface_valid(RESULT_SUMMARY):
            # 确保总结按钮处于总结中...不可点击状态
            self._dialogs[RESULT_SUMMARY].set_summary_loading()
        self._summary_token += 1
        token = self._summary_token
        worker = SummaryLLMWorker(
            api_url=mgr.get_openapi_url(),
            api_key=mgr.get_openapi_api_key(),
            model=mgr.get_openapi_model(),
            system_prompt=system_prompt,
            user_text=text,
        )
        self._summary_thread = worker
        self._threads.add(worker)
        worker.finished_signal.connect(
            lambda ok, content, t=worker, n=token: self._on_summary_thread_result(
                t, n, ok, content
            )
        )
        worker.finished.connect(lambda t=worker: self._release_thread(t))
        worker.start()

    def _on_summary_thread_result(
        self, thread, token: int, success: bool, content: str
    ):
        """丢弃被新总结请求替代的结果，仅处理最新一次请求。"""
        if token != self._summary_token or thread is not self._summary_thread:
            log_debug("忽略已被新请求替代的总结结果", "Translation")
            return
        self._on_summary_finished(success, content)

    def _on_summary_finished(self, success: bool, content: str):
        """将总结结果写入总结窗口（成功=结果，失败=错误信息）。"""
        if not self._surface_valid(RESULT_SUMMARY):
            return
        if success:
            self._dialogs[RESULT_SUMMARY].set_summary_result(content)
        else:
            self._dialogs[RESULT_SUMMARY].set_summary_error(content)

    def _stop_current_thread(self, surface: Optional[str] = None):
        """Invalidate the active request without blocking the GUI thread.

        ``surface`` 指定只作废哪一条通道：翻译（主窗口 / 小窗）或总结。
        默认 ``None`` 表示全部作废（关闭窗口、退出程序时使用）。
        两条通道分开计数、分开中断，所以互不打断。
        """
        if surface in (None, RESULT_DIALOG, RESULT_COMPACT):
            old_token = self._request_token
            self._request_token += 1
            self._request_targets.pop(old_token, None)
            thread = self._thread
            self._thread = None
            if thread is not None and thread.isRunning():
                thread.requestInterruption()
        if surface in (None, RESULT_SUMMARY):
            self._summary_token += 1
            thread = self._summary_thread
            self._summary_thread = None
            if thread is not None and thread.isRunning():
                thread.requestInterruption()

    def _on_thread_result(
        self,
        thread,
        token: int,
        success: bool,
        translated_text: str,
        error: str,
        detected_lang: str,
    ):
        """Discard superseded results and deliver only the latest request."""
        if token != self._request_token or thread is not self._thread:
            self._request_targets.pop(token, None)
            log_debug("忽略已被新请求替代的翻译结果", "Translation")
            return
        result_target = self._request_targets.pop(token, self._active_target)
        self._on_translation_finished(
            success, translated_text, error, detected_lang, result_target
        )

    def _release_thread(self, thread):
        self._threads.discard(thread)
        if self._thread is thread:
            self._thread = None
        if self._summary_thread is thread:
            self._summary_thread = None
        thread.deleteLater()
    
    def _on_translation_finished(
        self,
        success: bool,
        translated_text: str,
        error: str,
        detected_lang: str,
        result_target: str,
    ):
        """翻译完成回调"""
        log_debug(f"翻译完成: success={success}, detected_lang={detected_lang}", "Translation")

        if result_target == RESULT_COMPACT and self._is_popup_valid():
            if success:
                self._popup.show_result(translated_text, detected_lang)
            else:
                self._popup.show_error(error or self.tr("Translation failed"))
        elif result_target == RESULT_DIALOG and self._is_dialog_valid():
            self._dialog.on_translation_finished(success, translated_text, error, detected_lang)
        
        self.translation_finished.emit(success, translated_text, error)
        

    def _surface_of_sender(self) -> str:
        """请求来自哪个表面（按信号发送者判断）。

        窗口按钮发出的 ``translate_requested`` 由对应窗口自己发送；直接调用
        （没有发送者）时按翻译主窗口处理。
        """
        sender = self.sender()
        if sender is None:
            return RESULT_DIALOG
        for surface, dialog in self._dialogs.items():
            if dialog is sender:
                return surface
        return RESULT_DIALOG

    def _on_translate_requested(self, text: str, source_lang: str, target_lang: str):
        """处理请求（来自弹窗底部按钮：翻译窗口=翻译 / 总结窗口=总结）"""
        surface = self._surface_of_sender()
        is_summary = surface == RESULT_SUMMARY
        self._activate_surface(surface)

        if is_summary:
            self._handle_summary_request(text, target_lang)
            return

        if not self._backend_ready():
            if self._surface_valid(surface):
                self._dialogs[surface].set_translation_error(self._api_key_error())
            return

        if not text or not text.strip():
            if self._surface_valid(surface):
                self._dialogs[surface].set_translation_error(
                    self.tr("Please enter text to translate")
                )
            return

        log_debug(f"翻译请求: -> {target_lang}", "Translation")

        # 停止翻译通道当前线程（总结通道不受影响）
        self._stop_current_thread(RESULT_DIALOG)

        # 启动翻译
        self._start_translation(
            text=text,
            target_lang=target_lang,
            source_lang=source_lang,
            result_target=RESULT_DIALOG,
        )

    def _handle_summary_request(self, text: str, target_lang: str) -> None:
        """总结窗口底部按钮：重新调用大模型总结。"""
        if not self._summary_ready():
            if self._surface_valid(RESULT_SUMMARY):
                self._dialogs[RESULT_SUMMARY].set_summary_error(
                    self.tr("LLM not configured")
                )
            return

        if not text or not text.strip():
            if self._surface_valid(RESULT_SUMMARY):
                self._dialogs[RESULT_SUMMARY].set_summary_error(
                    self.tr("No text to summarize")
                )
            return

        log_debug(f"总结请求: -> {target_lang}", "Translation")

        # 只作废总结通道上一次未完成的请求，翻译窗口不受影响
        self._stop_current_thread(RESULT_SUMMARY)
        self._start_summary(text, target_lang)

    def _on_dialog_destroyed(self, surface: str = RESULT_DIALOG):
        """窗口被销毁时的清理（只清理该表面）"""
        log_debug(f"{surface} 窗口已关闭，清理资源", "Translation")
        self._dialogs.pop(surface, None)
        if self._active_target == surface:
            self._stop_current_thread(surface)
    
    def close_dialog(self):
        """主动关闭翻译窗口、总结窗口与划词小窗"""
        for surface in MAIN_SURFACES:
            if self._surface_valid(surface):
                self._dialogs[surface].close()
            self._dialogs.pop(surface, None)
        if self._is_popup_valid():
            self._popup.close()
        self._popup = None
        # 弹窗会话结束：手动选择不再延续
        self._popup_session_active = False
        self._stop_current_thread()

    def shutdown(self, timeout_ms: int = 11000) -> None:
        """Close translation UI and let all network workers finish before exit."""
        self.close_dialog()
        threads = list(self._threads)
        for thread in threads:
            if thread.isRunning():
                thread.requestInterruption()

        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        for thread in threads:
            if not thread.isRunning():
                continue
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms and thread.wait(remaining_ms):
                continue
            if thread.isRunning():
                log_warning("退出时翻译网络线程未在期限内结束", "Translation")
    
    def is_dialog_open(self) -> bool:
        """是否至少有一个主窗口（翻译 / 总结）打开"""
        return any(
            self._surface_valid(surface) and self._dialogs[surface].isVisible()
            for surface in MAIN_SURFACES
        )
    
    @classmethod
    def cleanup(cls):
        """清理单例（程序退出时调用）"""
        if cls._instance is not None:
            cls._instance.shutdown()
            cls._instance = None
            log_debug("TranslationManager 已清理", "Translation")
    
    def translate_from_image(
        self,
        pixmap,
        api_key: str = None,
        target_lang: str = "ZH",
        use_pro: bool = None,
        split_sentences: str = None,
        preserve_formatting: bool = None
    ):
        """
        从图片进行OCR识别后翻译
        
        流程：
        1. 立即显示翻译窗口（原文区显示"识别中..."）
        2. 后台线程进行OCR识别
        3. 识别完成后填入原文
        4. 自动调用翻译API
        
        Args:
            pixmap: QPixmap 图片（已是独立副本）
            api_key: API 密钥（兼容旧调用，现已忽略）
            target_lang: 目标语言代码
            use_pro: 是否使用 Pro 版 API（兼容旧调用，现已忽略）
            split_sentences: 分句模式
            preserve_formatting: 保留格式
        """
        from PySide6.QtGui import QPixmap
        
        # 使用传入的参数或已配置的参数
        api_key = self._resolve_api_key(api_key)
        if use_pro is None:
            use_pro = self._use_pro
        if split_sentences is None:
            split_sentences = self._split_sentences
        if preserve_formatting is None:
            preserve_formatting = self._preserve_formatting
        
        # 保存配置
        if use_pro is not None:
            self._use_pro = use_pro
        self._split_sentences = split_sentences
        self._preserve_formatting = preserve_formatting

        # 只作废翻译通道上一次未完成的请求（总结窗口在途的总结不受影响）
        self._stop_current_thread(RESULT_DIALOG)
        self._activate_surface(RESULT_DIALOG)

        # 截图翻译：还没有源文（OCR 未出文字），方向先用配置语言；
        # OCR 拿到文字后按真实语种重新判断（全中文→英语，其余/混排→中文）。
        if self._dialog_user_selected_target_lang(RESULT_DIALOG):
            target_lang = self._dialog.get_target_lang() or self.configured_target_lang()
        else:
            target_lang = self.configured_target_lang()
        self._target_lang = target_lang
        self._log_direction(target_lang, "")

        log_info("截图翻译模式：显示翻译窗口并启动OCR", "Translation")

        # 1. 显示翻译窗口（原文区显示"识别中..."）
        #    另一个主窗口（总结）已打开时层叠错开，避免两个窗口完全重叠。
        self._ensure_dialog(
            RESULT_DIALOG,
            text="",  # 初始为空
            position=self._cascade_position(RESULT_DIALOG),
            source_lang="auto",
            target_lang=target_lang
        )
        
        # 设置原文区为"识别中..."状态
        if self._is_dialog_valid():
            self._dialog.source_edit.setPlainText(self._dialog.tr("Recognizing..."))
            self._dialog.source_edit.setEnabled(False)  # OCR识别期间禁用编辑
            self._dialog.target_edit.clear()  # 清空旧结果，避免误以为直接翻译/总结
            self._dialog.set_loading()  # 翻译按钮置灰并显示翻译中...

        # 2. 启动OCR线程（翻译通道）
        self._start_ocr_thread(pixmap, RESULT_DIALOG)

    def summarize_from_image(
        self,
        pixmap,
        target_lang: str = None,
    ):
        """
        从图片进行OCR识别后，用大模型总结。

        使用**独立的总结窗口**（不会被翻译窗口复用，也不会覆盖先前的译文），
        流程与截图翻译一致，区别在 OCR 完成后调用大模型总结；
        总结使用自己的语言设置（``summary_target_lang``）与独立的请求通道。

        Args:
            pixmap: QPixmap 图片（已是独立副本）
            target_lang: 本次总结语言；不传则用配置里的总结语言
        """
        from PySide6.QtGui import QPixmap

        # 总结语言独立记忆（设置项 summary_target_lang，未设置时跟随翻译语言）
        target_lang = target_lang or self.configured_summary_target_lang()
        self._target_lang = target_lang

        # 只作废总结通道上一次未完成的请求，翻译窗口不受影响
        self._stop_current_thread(RESULT_SUMMARY)
        self._activate_surface(RESULT_SUMMARY)

        # 1. 显示总结窗口（创建时即固定为总结语义）
        #    另一个主窗口（翻译）已打开时层叠错开，避免两个窗口完全重叠。
        self._ensure_dialog(
            RESULT_SUMMARY,
            text="",  # 初始为空
            position=self._cascade_position(RESULT_SUMMARY),
            source_lang="auto",
            target_lang=target_lang,
        )
        # 语言框里显示的就是本次总结语言（用户改过则以语言框为准）
        self._target_lang = self._dialog_target_lang(RESULT_SUMMARY) or target_lang

        # 未配置大模型时直接报错
        if not self._summary_ready():
            if self._surface_valid(RESULT_SUMMARY):
                self._dialogs[RESULT_SUMMARY].set_summary_error(
                    self.tr("LLM not configured")
                )
            return

        # 2. 原文区先显示「识别中...」
        if self._surface_valid(RESULT_SUMMARY):
            dialog = self._dialogs[RESULT_SUMMARY]
            dialog.source_edit.setPlainText(dialog.tr("Recognizing..."))
            dialog.source_edit.setEnabled(False)
            dialog.target_edit.clear()  # 清空上一次总结结果
            dialog.set_summary_loading()  # 总结按钮置灰并显示总结中...

        # 3. 启动总结通道的 OCR 线程，完成后回调里分流到总结
        self._start_ocr_thread(pixmap, RESULT_SUMMARY)

    def _start_ocr_thread(self, pixmap, surface: str = RESULT_DIALOG):
        """启动统一 OCR 识别线程（ocr.pipeline，与 OCR 复制共用同一条流程）

        翻译与总结各持有自己的 OCR 线程槽：新一次总结不会取消/断开翻译窗口
        还在跑的 OCR（反之亦然），两条通道互不打断。

        关键设计：在主线程完成 QPixmap → QImage.copy() 转换，
        子线程只接收不含 GUI 资源的纯数据（QImage 是值类型，线程安全），
        预处理参数也在主线程快照后交给子线程。
        """
        # ── 主线程完成 GUI 资源转换（QPixmap 不能跨线程访问）──
        if pixmap is None or pixmap.isNull():
            log_debug("传入 pixmap 为空，跳过OCR", "Translation")
            return
        image = pixmap.toImage().copy()  # 深拷贝，线程安全
        if image.isNull():
            log_debug("QImage 转换失败，跳过OCR", "Translation")
            return

        from ocr.pipeline import OcrTextThread

        # 旧线程：断开信号（结果被丢弃）再等待自然结束，绝不使用 terminate()
        previous = self._ocr_thread if surface != RESULT_SUMMARY else self._summary_ocr_thread
        if previous is not None and previous.isRunning():
            previous.cancel()
            from core.qt_utils import safe_disconnect
            safe_disconnect(previous.finished_signal)
            # 不等待（旧线程在后台跑完即可），避免阻塞主线程
            previous.finished.connect(previous.deleteLater)

        # 创建并启动新线程
        thread = OcrTextThread(image)
        if surface == RESULT_SUMMARY:
            self._summary_ocr_thread = thread
            thread.finished_signal.connect(
                lambda ok, text: self._on_ocr_finished(ok, text, RESULT_SUMMARY)
            )
        else:
            self._ocr_thread = thread
            thread.finished_signal.connect(self._on_ocr_finished)
        thread.start()
        
        log_debug("OCR线程已启动", "Translation")
    
    def _on_ocr_finished(self, success: bool, result: str, surface: str = RESULT_DIALOG):
        """OCR识别完成回调（按表面的独立通道分流：翻译 / 总结）"""
        log_debug(
            f"OCR完成({surface}): success={success}, "
            f"result_len={len(result) if result else 0}",
            "Translation",
        )

        if not self._surface_valid(surface):
            log_debug(f"{surface} 窗口已关闭，忽略OCR结果", "Translation")
            return

        dialog = self._dialogs[surface]
        # 恢复编辑状态
        dialog.source_edit.setEnabled(True)

        if success and result:
            # 填入识别的文本
            dialog.source_edit.setPlainText(result)
            log_info(f"OCR识别成功: {result[:50]}...", "Translation")

            if surface == RESULT_SUMMARY:
                # 总结：语言取自总结窗口语言框（不按源文自动定方向）
                target_lang = (
                    self._dialog_target_lang(surface)
                    or self.configured_summary_target_lang()
                )
                self._target_lang = target_lang
                self._start_summary(text=result, target_lang=target_lang)
                return

            if self._dialog_user_selected_target_lang(surface):
                # 用户在 OCR 期间自己改了语言 → 以用户为准
                target_lang = dialog.get_target_lang() or self.configured_target_lang()
            else:
                # 方向按 OCR 出的文字判断（全中文→英语，其余/混排→中文）；
                # 语言框仍显示配置语言，不被方向改写。
                target_lang = self._direction_for(result)
            self._target_lang = target_lang
            self._log_direction(target_lang, result)

            # 自动开始翻译
            self._start_translation(
                text=result,
                target_lang=target_lang,
                source_lang="auto",
                result_target=RESULT_DIALOG,
            )
        else:
            # 显示错误信息，并恢复按钮可点击状态
            dialog.set_busy(False)
            dialog.target_edit.clear()
            dialog.source_edit.setPlainText("")
            dialog.source_edit.setPlaceholderText(
                result or self.tr("No text recognized")
            )
            log_error(f"OCR识别失败: {result}", "Translation")
