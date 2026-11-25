"""extract_text_lines: confidence filtering, line grouping and paragraph breaks."""
from main import extract_text_lines
from conftest import make_ocr_data


def test_groups_words_into_lines():
    data = make_ocr_data([("Hello", 90, 1, 1, 1), ("world", 90, 1, 1, 1),
                          ("again", 90, 1, 1, 2)])
    assert extract_text_lines(data, 0) == ["Hello world", "again"]


def test_blank_line_between_paragraphs_and_blocks():
    data = make_ocr_data([("One", 90, 1, 1, 1),
                          ("Two", 90, 1, 2, 1),   # new paragraph, same block
                          ("Three", 90, 2, 1, 1)])  # new block
    assert extract_text_lines(data, 0) == ["One", "", "Two", "", "Three"]


def test_lines_within_a_paragraph_have_no_blank_line():
    data = make_ocr_data([("a", 90, 1, 1, 1), ("b", 90, 1, 1, 2), ("c", 90, 1, 1, 3)])
    assert extract_text_lines(data, 0) == ["a", "b", "c"]


def test_threshold_is_exclusive():
    data = make_ocr_data([("keep", 51, 1, 1, 1), ("edge", 50, 1, 1, 1), ("drop", 10, 1, 1, 1)])
    assert extract_text_lines(data, 50) == ["keep"]


def test_threshold_100_removes_everything():
    data = make_ocr_data([("word", 99, 1, 1, 1)])
    assert extract_text_lines(data, 100) == []


def test_filtered_out_paragraph_leaves_no_double_blank_line():
    data = make_ocr_data([("first", 90, 1, 1, 1),
                          ("noise", 5, 2, 1, 1),
                          ("last", 90, 3, 1, 1)])
    lines = extract_text_lines(data, 50)
    assert lines == ["first", "", "last"]


def test_no_leading_or_trailing_blank_lines():
    data = make_ocr_data([("noise", 5, 1, 1, 1), ("text", 90, 2, 1, 1), ("noise", 5, 3, 1, 1)])
    assert extract_text_lines(data, 50) == ["text"]


def test_ignores_empty_words_and_tesseract_structure_rows():
    # Tesseract emits rows with empty text and conf -1 for blocks/paragraphs/lines
    data = make_ocr_data([("", -1, 1, 0, 0), ("  ", 95, 1, 1, 1), ("word", 95, 1, 1, 1)])
    assert extract_text_lines(data, 0) == ["word"]


def test_unparseable_confidence_is_treated_as_minus_one():
    data = make_ocr_data([("bad", "n/a", 1, 1, 1), ("none", None, 1, 1, 1), ("ok", "88.5", 1, 1, 1)])
    assert extract_text_lines(data, 0) == ["ok"]


def test_strips_whitespace_and_handles_none_text():
    data = make_ocr_data([(" padded ", 90, 1, 1, 1), (None, 90, 1, 1, 1)])
    assert extract_text_lines(data, 0) == ["padded"]


def test_empty_data():
    assert extract_text_lines({}, 0) == []
    assert extract_text_lines(make_ocr_data([]), 0) == []


def test_tamil_text_is_kept_intact():
    data = make_ocr_data([("தமிழ்", 90, 1, 1, 1), ("எழுத்து", 90, 1, 1, 1)])
    assert extract_text_lines(data, 0) == ["தமிழ் எழுத்து"]
