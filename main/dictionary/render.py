# -*- coding: utf-8 -*-
"""把词条渲染成小窗能显示、也能整体复制的富文本。

配色从调用方传入（小窗自己的 ``Palette``），本模块不依赖翻译模块，
避免 ``dictionary`` 与 ``translation`` 互相导入。
"""
from __future__ import annotations

from html import escape
from typing import Iterable, Optional, Sequence

from .i18n import tr as _tr
from .models import DictEntry, ZhEnEntry
from .service import DisplayOptions

_FONT = "'Microsoft YaHei UI','Segoe UI',sans-serif"


def render_entry_card(entry, palette, options=None) -> str:
    """按词条类型分派到对应的卡片渲染（英→中 / 中→英）。"""
    if isinstance(entry, ZhEnEntry):
        return render_zh_en_html(entry, palette, options)
    return render_entry_html(entry, palette, options)


def _stars(count: int) -> str:
    return "★" * count


def _meta_line(entry: DictEntry, palette, options: DisplayOptions) -> str:
    """标签 / 柯林斯 / 牛津 / 词频 一行。"""
    parts = []
    if options.tags:
        parts.extend(entry.tag_labels())
        if entry.oxford:
            parts.append(_tr("Oxford 3000"))
        if entry.collins:
            parts.append(
                _tr("Collins {stars}").format(stars=_stars(entry.collins))
            )
    if options.rank and entry.rank:
        parts.append(_tr("Frequency {rank}").format(rank=entry.rank))
    if not parts:
        return ""
    return (
        f'<div style="font-size:11px;color:{palette.text_3};'
        f'margin-bottom:7px;">{escape(" · ".join(parts))}</div>'
    )


def render_entry_html(
    entry: DictEntry,
    palette,
    options: Optional[DisplayOptions] = None,
) -> str:
    """渲染命中词条的卡片。"""
    options = options or DisplayOptions()
    rows = []

    headword = escape(entry.headword)
    phonetic = ""
    if options.phonetic and entry.phonetic:
        phonetic = (
            f'<span style="font-size:12px;color:{palette.text_2};">'
            f"&nbsp;&nbsp;/{escape(entry.phonetic.strip('/'))}/</span>"
        )
    rows.append(
        f'<div style="margin-bottom:1px;">'
        f'<span style="font-size:19px;font-weight:700;color:{palette.text};">'
        f"{headword}</span>{phonetic}</div>"
    )

    if entry.is_inflected:
        rows.append(
            f'<div style="font-size:11px;color:{palette.accent};'
            f'margin-bottom:5px;">'
            + escape(
                _tr('"{query}" is the {inflection} of "{headword}"').format(
                    query=entry.query,
                    inflection=entry.inflection,
                    headword=entry.headword,
                )
            )
            + "</div>"
        )
    else:
        rows.append('<div style="font-size:3px;"></div>')

    rows.append(_meta_line(entry, palette, options))

    body = []
    for sense in entry.senses:
        label = (
            f'<span style="color:{palette.accent};font-weight:600;">'
            f"{escape(sense.label)}</span> "
            if sense.label
            else ""
        )
        body.append(
            f'<div style="font-size:14px;color:{palette.text};'
            f'margin-bottom:2px;">{label}{escape(sense.text)}</div>'
        )
    if entry.variant_senses:
        body.append(
            f'<div style="font-size:11px;color:{palette.text_3};'
            f'margin-top:6px;margin-bottom:2px;">'
            f"{escape(_tr('Other senses of this form'))}</div>"
        )
        for sense in entry.variant_senses:
            body.append(
                f'<div style="font-size:13px;color:{palette.text_2};'
                f'margin-bottom:2px;">{escape(sense.text)}</div>'
            )
    if entry.definition:
        body.append(
            f'<div style="font-size:12px;color:{palette.text_2};'
            f'margin-top:6px;">{escape(entry.definition)}</div>'
        )
    if body:
        rows.append(
            f'<div style="margin-top:3px;">{"".join(body)}</div>'
        )

    if options.forms and entry.forms:
        forms = " · ".join(
            f"{escape(label)} {escape(word)}" for label, word in entry.forms
        )
        rows.append(
            f'<div style="font-size:11px;color:{palette.text_3};'
            f'margin-top:8px;">'
            f"{escape(_tr('Forms: {forms}').format(forms=forms))}</div>"
        )

    # 来源标识不写进卡片：小窗在结果下方有专门的「来源」行
    # （离线词典 · ECDICT），同一信息不必出现两次。
    return (
        f'<div style="font-family:{_FONT};font-weight:400;">{"".join(rows)}</div>'
    )


def render_zh_en_html(
    entry: ZhEnEntry,
    palette,
    options: Optional[DisplayOptions] = None,
) -> str:
    """渲染汉英（中 → 英）词条卡片：中文词头 + 拼音 + 按词性归组的英文对应词。"""
    rows = []

    headword = escape(entry.headword)
    extras = []
    if options is None or options.phonetic:
        if entry.has_variant_script:
            extras.append(
                f'<span style="font-size:12px;color:{palette.text_3};">'
                f"&nbsp;&nbsp;{escape(entry.traditional)}</span>"
            )
        if entry.pinyin:
            extras.append(
                f'<span style="font-size:12px;color:{palette.text_2};">'
                f"&nbsp;&nbsp;[{escape(entry.pinyin)}]</span>"
            )
    rows.append(
        f'<div style="margin-bottom:1px;">'
        f'<span style="font-size:19px;font-weight:700;color:{palette.text};">'
        f"{headword}</span>{''.join(extras)}</div>"
    )
    rows.append(
        f'<div style="font-size:11px;color:{palette.text_3};'
        f'margin-bottom:7px;">{escape(_tr("Chinese → English"))}</div>'
    )

    body = []
    for pos, words in entry.pos_groups():
        label = (
            f'<span style="color:{palette.accent};font-weight:600;">'
            f"{escape(pos)}</span> "
            if pos
            else ""
        )
        joined = " · ".join(
            f'<span style="font-size:15px;color:{palette.text};">{escape(w)}</span>'
            for w in words
        )
        body.append(
            f'<div style="font-size:13px;margin-bottom:3px;">{label}{joined}</div>'
        )
    if body:
        rows.append(f'<div style="margin-top:3px;">{"".join(body)}</div>')

    return (
        f'<div style="font-family:{_FONT};font-weight:400;">{"".join(rows)}</div>'
    )


def render_miss_html(
    query: str,
    suggestions: Sequence[str],
    palette,
) -> str:
    """未收录时的提示；有候选词就一并给出。"""
    not_found = _tr('Not in dictionary: "{query}"').format(query=query)
    rows = [
        f'<div style="font-family:{_FONT};font-weight:400;">',
        f'<div style="font-size:13px;color:{palette.text_2};">'
        f"{escape(not_found)}</div>",
    ]
    if suggestions:
        chips = " · ".join(escape(word) for word in suggestions)
        rows.append(
            f'<div style="font-size:12px;color:{palette.text_3};'
            f'margin-top:6px;">{escape(_tr("Did you mean:"))}&nbsp;{chips}</div>'
        )
    rows.append("</div>")
    return "".join(rows)


def plain_text_for_miss(query: str, suggestions: Iterable[str]) -> str:
    """未收录时的纯文本形式（复制用）。"""
    base = _tr('Not in dictionary: "{query}"').format(query=query)
    suggestions = tuple(suggestions)
    if suggestions:
        base += "\n" + _tr("Did you mean:") + " " + " · ".join(suggestions)
    return base
