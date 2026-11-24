"""Searchable PDF export: text layout over OCR boxes, the PDF file, and the app's export."""
import os
import re
import shutil
import subprocess

import pytest
from PIL import Image
from PyQt6.QtWidgets import QFileDialog

import main
from conftest import HAVE_POPPLER, make_ocr_data, open_with_fake_ocr, wait_until
from searchable_pdf import PageSpec, Word, layout_text, write_searchable_pdf

FONT = main.resource_path(os.path.join("font", "pdf.ttf"))
PAGE = (0, 0, 1000, 1000)
needs_pdftotext = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not available")


def ocr_line(text, top, height=40):
    """An OCR line whose words are 100 px wide with 20 px gaps."""
    return [Word(t, 50 + k * 120, top, 100, height) for k, t in enumerate(text.split())]


LINES = [ocr_line("one two", 100), ocr_line("three four", 200), ocr_line("five", 300)]


def extract(pdf, page=None):
    args = ["pdftotext"] + (["-f", str(page), "-l", str(page)] if page else []) + [str(pdf), "-"]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def media_boxes(pdf):
    data = open(pdf, "rb").read()
    return [tuple(float(v) for v in m.groups())
            for m in re.finditer(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data)]


# ---------------------------------------------------------------- layout

def test_unchanged_text_keeps_word_positions():
    layout = layout_text(LINES, "one two\n\nthree four\nfive", PAGE)
    assert layout == [[(w.text, (w.left, w.top, w.width, w.height)) for w in line] for line in LINES]


def test_edited_line_is_stretched_over_its_ocr_line():
    layout = layout_text(LINES, "one two\nthree FOUR!\nfive", PAGE)
    assert layout[1] == [("three FOUR!", (50, 200, 220, 40))]
    assert len(layout[0]) == 2 and len(layout[2]) == 1  # untouched lines keep their words


def test_merged_lines_share_the_replaced_lines_box():
    layout = layout_text(LINES, "one two three four five", PAGE)
    assert layout == [[("one two three four five", (50, 100, 220, 240))]]


def test_added_lines_go_in_the_gap_next_to_their_neighbours():
    layout = layout_text(LINES, "zero\none two\nNEW\nthree four\nfive\nsix", PAGE)
    assert [run[0][0] for run in layout] == ["zero", "one", "NEW", "three", "five", "six"]
    assert layout[0] == [("zero", (50, 60, 220, 40))]  # above the first line
    assert layout[2] == [("NEW", (50, 140, 220, 40))]  # between lines 1 and 2
    assert layout[5] == [("six", (50, 340, 100, 40))]  # below the last line


def test_added_line_without_room_shares_its_neighbours():
    tight = [ocr_line("one", 100), ocr_line("two", 145)]  # 5 px apart
    layout = layout_text(tight, "one\nNEW\ntwo", PAGE)
    assert layout[1] == [("NEW", (50, 100, 100, 85))]


def test_deleted_line_is_left_out():
    layout = layout_text(LINES, "one two\nfive", PAGE)
    assert [[r[0] for r in line] for line in layout] == [["one", "two"], ["five"]]


def test_text_without_ocr_boxes_fills_the_page():
    assert layout_text([], "a\nb", PAGE) == [[("a", (0, 0, 1000, 500))], [("b", (0, 500, 1000, 500))]]


def test_empty_text_has_no_layout():
    assert layout_text(LINES, "  \n\n", PAGE) == []


def test_ocr_line_words_matches_extracted_text():
    data = make_ocr_data([("a", 90, 1, 1, 1), ("b", 30, 1, 1, 1), ("c", 90, 1, 2, 1)])
    lines = main.ocr_line_words(data, 50)
    assert [(new, [w.text for w in words]) for new, words in lines] == [(True, ["a"]), (True, ["c"])]
    assert lines[0][1][0] == Word("a", data['left'][0], data['top'][0], 100, 30)
    assert main.extract_text_lines(data, 50) == ["a", "", "c"]


# ---------------------------------------------------------------- PDF file

@pytest.fixture
def a4_png(tmp_path):
    path = tmp_path / "page.png"
    Image.new("L", (2480, 3508), 255).save(path, dpi=(300, 300))
    return str(path)


def test_page_size_follows_image_resolution(tmp_path, a4_png):
    no_dpi = tmp_path / "no_dpi.png"
    Image.new("RGB", (600, 300), "white").save(no_dpi)
    at_150 = tmp_path / "at_150.jpg"
    Image.new("RGB", (1240, 1754), "white").save(at_150, dpi=(150, 150))
    out = tmp_path / "out.pdf"
    assert write_searchable_pdf(str(out), [PageSpec(a4_png), PageSpec(str(no_dpi)), PageSpec(str(at_150))], FONT)
    sizes = media_boxes(out)
    assert sizes[0] == pytest.approx((595.2, 841.92))  # A4
    assert sizes[1] == pytest.approx((144, 72))  # no resolution: 300 DPI assumed
    assert sizes[2] == pytest.approx((595.2, 841.92))


def test_jpeg_is_embedded_without_recompression(tmp_path):
    jpg = tmp_path / "photo.jpg"
    Image.new("RGB", (300, 200), (200, 120, 40)).save(jpg, quality=95)
    out = tmp_path / "out.pdf"
    write_searchable_pdf(str(out), [PageSpec(str(jpg))], FONT)
    assert jpg.read_bytes() in out.read_bytes()


def test_cancel_leaves_no_file(tmp_path, a4_png):
    out = tmp_path / "out.pdf"
    done = []
    written = write_searchable_pdf(str(out), [PageSpec(a4_png)] * 3, FONT,
                                   progress=lambda d, t: done.append(d), should_stop=lambda: len(done) >= 1)
    assert written is False and done == [1]
    assert os.listdir(tmp_path) == ["page.png"]


def test_failure_leaves_no_file(tmp_path, a4_png):
    out = tmp_path / "out.pdf"
    with pytest.raises(OSError):
        write_searchable_pdf(str(out), [PageSpec(a4_png), PageSpec(str(tmp_path / "missing.png"))], FONT)
    assert os.listdir(tmp_path) == ["page.png"]


@needs_pdftotext
def test_text_layer_is_extractable(tmp_path, a4_png):
    tamil = [Word("தமிழ்", 150, 200, 200, 60), Word("எழுத்துக்கள்", 372, 200, 400, 60)]
    english = [Word("Hello", 150, 290, 200, 60), Word("world", 372, 290, 200, 60)]
    pages = [PageSpec(a4_png, [tamil, english], "தமிழ் எழுத்துக்கள்\n\nHello world"),
             PageSpec(a4_png, [tamil, english], "தமிழ் எழுத்துக்கள்\nHello proofread world")]
    out = tmp_path / "out.pdf"
    write_searchable_pdf(str(out), pages, FONT, title="தமிழ் நூல்", producer="Tamil OCR test")
    assert extract(out, 1).split("\n")[:2] == ["தமிழ் எழுத்துக்கள்", "Hello world"]
    assert extract(out, 2).split("\n")[:2] == ["தமிழ் எழுத்துக்கள்", "Hello proofread world"]
    info = subprocess.run(["pdfinfo", str(out)], capture_output=True, text=True, check=True).stdout
    assert "தமிழ் நூல்" in info and "Tamil OCR test" in info and re.search(r"Pages:\s+2", info)


@needs_pdftotext
def test_text_is_invisible(tmp_path, a4_png):
    """The page must look exactly like the image: rendering it shows no text."""
    if shutil.which("pdftoppm") is None:
        pytest.skip("pdftoppm not available")
    out = tmp_path / "out.pdf"
    write_searchable_pdf(str(out), [PageSpec(a4_png, [ocr_line("visible text", 100, 60)], "visible text")], FONT)
    subprocess.run(["pdftoppm", "-r", "50", "-gray", "-png", str(out), str(tmp_path / "render")], check=True)
    rendered = Image.open(next(tmp_path.glob("render*.png"))).convert("L")
    assert rendered.getextrema()[0] >= 250  # still a blank white page (allowing JPEG noise)


# ---------------------------------------------------------------- the app

@pytest.fixture
def two_page_doc(qapp, window, make_pdf):
    if not HAVE_POPPLER:
        pytest.skip("Poppler not available")
    pdf = make_pdf(2)
    open_with_fake_ocr(qapp, window, pdf, 2)
    return window, pdf


def export(qapp, window, monkeypatch, target):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    window.export_pdf_action.trigger()
    wait_until(qapp, lambda: window.export_worker is None, timeout=60)


@needs_pdftotext
def test_app_exports_proofread_text(qapp, two_page_doc, monkeypatch, tmp_path):
    window, pdf = two_page_doc
    window.confidence_spinbox.setValue(50)  # hides the conf-40 'maybe' words
    window.next_page()
    window.text_edit.setPlainText("Corrected page two")
    out = tmp_path / "searchable"  # no extension: .pdf is added
    export(qapp, window, monkeypatch, out)

    pdf_out = tmp_path / "searchable.pdf"
    assert extract(pdf_out, 1).split() == ["page1", "text"]
    assert extract(pdf_out, 2).split() == ["Corrected", "page", "two"]
    # Same page size as the source PDF (A4), although pages were rendered at 300 DPI
    assert media_boxes(pdf_out) == [pytest.approx((595.2, 841.92))] * 2
    assert "Searchable PDF exported" in window.status_bar.currentMessage()
    assert not window.busy and window.export_pdf_action.isEnabled()


def test_app_refuses_to_overwrite_the_open_document(qapp, two_page_doc, monkeypatch, dialogs):
    window, pdf = two_page_doc
    before = open(pdf, "rb").read()
    export(qapp, window, monkeypatch, pdf)
    assert dialogs.titles("warning") == ["Export Searchable PDF"]
    assert open(pdf, "rb").read() == before


def test_app_export_failure_shows_dialog(qapp, two_page_doc, monkeypatch, tmp_path, dialogs):
    window, _ = two_page_doc
    export(qapp, window, monkeypatch, tmp_path / "missing_dir" / "out.pdf")
    assert dialogs.titles("critical") == ["Export failed"]
    assert not window.busy


def test_opening_another_file_cancels_export(qapp, two_page_doc, monkeypatch, tmp_path, image_file):
    window, _ = two_page_doc
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "out.pdf"), "")))
    window.export_searchable_pdf()
    assert window.busy and window.export_worker is not None
    window.process_file(image_file)  # what closing the window also does: stop_workers()
    assert window.export_worker is None
    assert not (tmp_path / "out.pdf.tmp").exists()


@pytest.mark.poppler
def test_rendered_pages_record_their_resolution(qapp, tmp_path, make_pdf):
    worker = main.PDFConversionWorker(make_pdf(1), str(tmp_path))
    worker.run()  # synchronously
    with Image.open(tmp_path / "page_0001.png") as page:
        assert page.info["dpi"] == pytest.approx((300, 300), abs=0.01)
