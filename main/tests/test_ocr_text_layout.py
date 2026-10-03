# -*- coding: utf-8 -*-
"""OCR 文本的版式保真：行结构、阅读顺序、段落空行。

背景（回归）：oneocr 引擎只返回逐行文本、不返回坐标，而汇总代码曾给每行
塞同一个占位框，导致按 Y 坐标分行时**所有行被并成一行**，换行与段落全丢，
翻译出来自然也没有格式。本文件锁定修复后的行为。
"""

from ocr.ocr_manager import format_ocr_result_text

LINE_1 = [[0, 0], [100, 0], [100, 20], [0, 20]]
LINE_2 = [[0, 30], [100, 30], [100, 50], [0, 50]]
LINE_3 = [[0, 60], [100, 60], [100, 80], [0, 80]]


def _item(text, box=None):
    return {"box": box, "text": text, "score": 1.0}


def _result(items):
    return {"code": 100, "msg": "成功", "data": items, "elapse": 0.01}


def test_boxless_lines_are_preserved():
    """没有坐标时按引擎给出的行序换行，绝不并成一行。"""
    result = _result([_item("第一行"), _item("第二行"), _item("第三行")])

    assert format_ocr_result_text(result) == "第一行\n第二行\n第三行"


def test_identical_placeholder_boxes_keep_line_order():
    """所有文字块给同一个（占位）框时，坐标没有信息量，同样按行序输出。"""
    result = _result(
        [_item("第一行", LINE_1), _item("第二行", [list(p) for p in LINE_1])]
    )

    assert format_ocr_result_text(result) == "第一行\n第二行"


def test_reading_order_uses_coordinates_when_available():
    result = _result(
        [
            _item("世界", [[60, 0], [120, 0], [120, 20], [60, 20]]),
            _item("你好", [[0, 0], [60, 0], [60, 20], [0, 20]]),
            _item("第二行内容", LINE_2),
        ]
    )

    # 同一行内按 X 排序；中日韩之间不补空格
    assert format_ocr_result_text(result) == "你好世界\n第二行内容"


def test_cjk_blocks_on_one_line_are_not_padded_with_spaces():
    result = _result(
        [
            _item("你好", LINE_1),
            _item("世界", [[40, 0], [80, 0], [80, 20], [40, 20]]),
        ]
    )

    assert format_ocr_result_text(result) == "你好世界"


def test_latin_blocks_on_one_line_keep_a_space():
    result = _result(
        [
            _item("Hello", LINE_1),
            _item("world", [[60, 0], [120, 0], [120, 20], [60, 20]]),
        ]
    )

    assert format_ocr_result_text(result) == "Hello world"


def test_normal_line_pitch_has_no_blank_line():
    result = _result([_item("第一行", LINE_1), _item("第二行", LINE_2)])

    assert format_ocr_result_text(result) == "第一行\n第二行"


def test_paragraph_gap_inserts_a_blank_line():
    """行距 30，段落间距 90（3 倍）→ 插入空行保留段落。"""
    result = _result(
        [
            _item("第一段第一行", LINE_1),
            _item("第一段第二行", LINE_2),
            _item("第二段", [[0, 120], [100, 120], [100, 140], [0, 140]]),
        ]
    )

    assert format_ocr_result_text(result) == "第一段第一行\n第一段第二行\n\n第二段"


def test_single_block_returns_its_text():
    assert format_ocr_result_text(_result([_item("只有一行")])) == "只有一行"


def test_empty_or_failed_results_return_empty_text():
    assert format_ocr_result_text(None) == ""
    assert format_ocr_result_text({}) == ""
    assert format_ocr_result_text({"code": -1, "data": []}) == ""
    assert format_ocr_result_text({"code": 100, "data": []}) == ""
    assert format_ocr_result_text(_result([_item("")])) == ""


def test_custom_separator_is_respected():
    result = _result([_item("第一行"), _item("第二行")])

    assert format_ocr_result_text(result, separator=" | ") == "第一行 | 第二行"
