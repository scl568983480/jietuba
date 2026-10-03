# -*- coding: utf-8 -*-
"""离线词典的数据模型与文本解析。

ECDICT 的两个数据格式坑（实测）：

1. 字段内的换行是**字面量** ``\\n``（反斜杠 + n），不是真正的换行符，
   所以必须先做字面量还原再分行。
2. ``exchange`` 用 ``代号:值`` 以 ``/`` 分隔，其中 ``0`` 是原形（lemma），
   ``1`` 是"该词是原形的哪一类变形"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .i18n import tr as _tr


# ── 考纲标签（值是翻译源串，展示时按当前语言翻译） ───────────────────
TAG_LABELS = {
    "zk": "Middle School",
    "gk": "College Entrance",
    "cet4": "CET-4",
    "cet6": "CET-6",
    "ky": "Postgraduate",
    "toefl": "TOEFL",
    "ielts": "IELTS",
    "gre": "GRE",
}

# ── exchange 代号对应的变形名 ────────────────────────────────────────
EXCHANGE_LABELS = {
    "p": "Past tense",
    "d": "Past participle",
    "i": "Present participle",
    "3": "Third person singular",
    "r": "Comparative",
    "t": "Superlative",
    "s": "Plural",
}

# 变形词条自带的释义若只是"XX的过去式"这类提示，就不再单独展示。
# 这些串匹配的是 ECDICT **数据本身**的中文内容，不是 UI 文案，故不翻译。
_INFLECTION_HINTS = (
    "的过去式",
    "的过去分词",
    "的现在分词",
    "的第三人称单数",
    "的比较级",
    "的最高级",
    "的复数",
)

# 一行释义开头的词性 / 领域标记，如 "n." "vt." "interj." "[计]" "[网络]"
_SENSE_LABEL_RE = re.compile(r"^\s*((?:[A-Za-z]{1,7}\.)+|\[[^\]\s]{1,10}\])\s*(.*)$")


def split_lines(text: Optional[str]) -> list:
    """按 ECDICT 的（字面量）换行符切分字段内容。"""
    if not text:
        return []
    normalized = (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    return [line.strip() for line in normalized.split("\n") if line.strip()]


@dataclass(frozen=True)
class DictSense:
    """一条释义：``label`` 是词性/领域标记（可能为空），``text`` 是释义正文。"""

    label: str
    text: str


def parse_senses(translation: Optional[str]) -> Tuple[DictSense, ...]:
    """把 ECDICT 的 ``translation``（或 ``definition``）字段解析成释义列表。"""
    senses = []
    for line in split_lines(translation):
        match = _SENSE_LABEL_RE.match(line)
        if match and match.group(2).strip():
            senses.append(DictSense(match.group(1).strip(), match.group(2).strip()))
        else:
            senses.append(DictSense("", line))
    return tuple(senses)


def parse_lemma(exchange: Optional[str]) -> str:
    """取出 ``exchange`` 里的原形（``0:``）。没有则返回空串。"""
    for item in (exchange or "").split("/"):
        code, _, value = item.partition(":")
        if code.strip() == "0" and value.strip():
            return value.strip()
    return ""


def parse_inflection(exchange: Optional[str]) -> str:
    """取出 ``exchange`` 里的变形类型名（``1:``），如 "Past tense"。

    多个代号用 ``" / "`` 连接（语言无关的分隔符，避免出现只有空格的
    翻译 key —— 那种 key 在编译成 .qm 后会被 lrelease 规范化掉）。
    """
    for item in (exchange or "").split("/"):
        code, _, value = item.partition(":")
        if code.strip() == "1" and value.strip():
            return " / ".join(
                _tr(EXCHANGE_LABELS.get(ch, ch)) for ch in value.strip()
            )
    return ""


def parse_forms(exchange: Optional[str]) -> Tuple[Tuple[str, str], ...]:
    """取出原形之外的各个变形：``(("Past tense", "gave"), ...)``。"""
    forms = []
    for item in (exchange or "").split("/"):
        code, _, value = item.partition(":")
        code, value = code.strip(), value.strip()
        if not value or code in ("0", "1"):
            continue
        label = EXCHANGE_LABELS.get(code)
        if label:
            forms.append((_tr(label), value))
    return tuple(forms)


def is_substantive(translation: Optional[str], canonical: Optional[str] = "") -> bool:
    """变形词条自带的释义是否值得单独展示。

    ``gave`` 的释义是"give的过去式"这类提示，原形已经说明了，不必重复；
    ``running`` / ``better`` 则有自己的实义，应当保留。
    """
    if not (translation or "").strip():
        return False
    if canonical and translation.strip() == canonical.strip():
        return False
    return not any(hint in translation for hint in _INFLECTION_HINTS)


@dataclass(frozen=True)
class DictEntry:
    """一次查词的完整结果（已把变形词归并到原形）。"""

    headword: str
    query: str
    phonetic: str = ""
    senses: Tuple[DictSense, ...] = ()
    variant_senses: Tuple[DictSense, ...] = ()
    definition: str = ""
    inflection: str = ""
    forms: Tuple[Tuple[str, str], ...] = ()
    tags: Tuple[str, ...] = ()
    collins: int = 0
    oxford: bool = False
    bnc: int = 0
    frq: int = 0
    match_kind: str = "exact"

    @property
    def is_inflected(self) -> bool:
        """查的是变形词（gave → give）。"""
        return bool(self.inflection) and self.query.lower() != self.headword.lower()

    @property
    def rank(self) -> int:
        """词频排名：取 BNC 与当代语料库里较靠前的那个；都为 0 表示无数据。"""
        values = [v for v in (self.bnc, self.frq) if v > 0]
        return min(values) if values else 0

    def tag_labels(self) -> Tuple[str, ...]:
        return tuple(_tr(TAG_LABELS.get(tag, tag)) for tag in self.tags)

    def to_plain_text(self) -> str:
        """纯文本形式：用于复制到剪贴板，也是富文本渲染的内容来源。"""
        lines = []
        head = self.headword
        if self.phonetic:
            head += f"  /{self.phonetic.strip('/')}/"
        lines.append(head)

        if self.is_inflected:
            lines.append(
                _tr('"{query}" is the {inflection} of "{headword}"').format(
                    query=self.query,
                    inflection=self.inflection,
                    headword=self.headword,
                )
            )

        badges = list(self.tag_labels())
        if self.oxford:
            badges.append(_tr("Oxford 3000"))
        if self.collins:
            badges.append(
                _tr("Collins {stars}").format(stars="★" * self.collins)
            )
        if self.rank:
            badges.append(_tr("Frequency {rank}").format(rank=self.rank))
        if badges:
            lines.append(" · ".join(badges))

        lines.append("")
        for sense in self.senses:
            lines.append(f"{sense.label} {sense.text}".strip())
        if self.variant_senses:
            lines.append("")
            lines.append(_tr("Other senses of this form"))
            for sense in self.variant_senses:
                lines.append(f"{sense.label} {sense.text}".strip())
        if self.definition:
            lines.append("")
            lines.append(self.definition)

        if self.forms:
            forms = " · ".join(f"{label} {word}" for label, word in self.forms)
            lines.append("")
            lines.append(_tr("Forms: {forms}").format(forms=forms))

        return "\n".join(lines).strip()


@dataclass(frozen=True)
class LookupResult:
    """查词结果：命中时 ``entry`` 有值，未命中时 ``suggestions`` 给候选词。"""

    entry: Optional[DictEntry] = None
    suggestions: Tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.entry is not None


# ── 汉英（中 → 英） ──────────────────────────────────────────────────
# 数据来自 CC-CEDICT（https://cc-cedict.org/），由 MDBG 发布，
# 采用 CC BY-SA 4.0 许可：允许商用与再分发，条件是署名来源、
# 且改进后的数据需以相同许可共享。详见 main/dictionary/DICTIONARY_LICENSES.txt。

@dataclass(frozen=True)
class ZhEnSense:
    """一条汉英释义：``pos`` 是词性（可为空），``text`` 是一个英文对应词。"""

    pos: str
    text: str


@dataclass(frozen=True)
class ZhEnEntry:
    """中文词头的英文对应词列表。"""

    headword: str
    pinyin: str = ""
    senses: Tuple[ZhEnSense, ...] = ()
    query: str = ""
    traditional: str = ""

    @property
    def is_found(self) -> bool:
        return bool(self.senses)

    @property
    def display_query(self) -> str:
        return self.query or self.headword

    @property
    def has_variant_script(self) -> bool:
        """繁体写法是否与简体不同（相同就不必展示）。"""
        return bool(self.traditional) and self.traditional != self.headword

    def pos_groups(self) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
        """按词性归组并保持出现顺序：``(("vt.", ("complete", ...)), ...)``。"""
        order: list[str] = []
        buckets: dict[str, list[str]] = {}
        for sense in self.senses:
            if sense.pos not in buckets:
                buckets[sense.pos] = []
                order.append(sense.pos)
            if sense.text not in buckets[sense.pos]:
                buckets[sense.pos].append(sense.text)
        return tuple((pos, tuple(buckets[pos])) for pos in order)

    def to_plain_text(self) -> str:
        """纯文本形式（复制用）。"""
        head = self.headword
        if self.has_variant_script:
            head += f"（繁 {self.traditional}）"
        if self.pinyin:
            head += f"  [{self.pinyin}]"
        lines = [head, "", _tr("Chinese → English · Offline")]
        for pos, words in self.pos_groups():
            prefix = f"{pos} " if pos else ""
            lines.append(prefix + " · ".join(words))
        return "\n".join(lines).strip()


@dataclass(frozen=True)
class ZhEnLookupResult:
    """汉英查词结果。"""

    entry: Optional[ZhEnEntry] = None
    suggestions: Tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.entry is not None
