"""PDFConversionWorker: page sizing, DPI fallback and page-by-page rendering."""
import os
import warnings

import pytest
from PIL import Image

import main
from conftest import wait_until


@pytest.mark.parametrize("size, expected", [
    ((2480, 3508), (2480, 3508)),    # A4 at 300 DPI: unchanged
    ((8000, 4000), (4000, 2000)),    # landscape: width capped
    ((3000, 9000), (1333, 4000)),    # portrait: height capped
    ((4000, 4000), (4000, 4000)),    # exactly at the limit
])
def test_limit_size(size, expected):
    out = main.PDFConversionWorker.limit_size(Image.new("RGB", size))
    assert out.size == expected


def test_resource_path_is_relative_to_main_py():
    assert main.resource_path("tessdata") == os.path.join(os.path.dirname(main.__file__), "tessdata")


def test_tesseract_thread_limit_default():
    assert os.environ.get("OMP_THREAD_LIMIT") == "1"


@pytest.mark.poppler
def test_oversized_page_falls_back_to_lower_dpi(tmp_path, monkeypatch):
    pdf = tmp_path / "poster.pdf"
    Image.new("RGB", (400, 400), "white").save(pdf, resolution=10)  # a 40 x 40 inch page
    # 300 DPI = 144 MP; with this limit Pillow refuses anything above 80 MP
    monkeypatch.setattr(main.Image, "MAX_IMAGE_PIXELS", 40_000_000)
    worker = main.PDFConversionWorker(str(pdf), str(tmp_path))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        page = worker.render_page(1)
    assert page.size == (8000, 8000)  # 40 inches at 200 DPI


@pytest.mark.poppler
def test_pages_are_emitted_one_by_one(qapp, tmp_path, make_pdf):
    pdf = make_pdf(4)
    out = tmp_path / "out"
    out.mkdir()
    worker = main.PDFConversionWorker(pdf, str(out))
    events = []
    worker.document_info.connect(lambda n: events.append(("info", n)))
    worker.page_converted.connect(lambda i, p: events.append(("page", i, os.path.basename(p))))
    worker.conversion_finished.connect(lambda n: events.append(("done", n)))
    worker.start()
    wait_until(qapp, lambda: events and events[-1][0] == "done", timeout=60)
    worker.wait()
    assert events == [("info", 4)] + [("page", i, f"page_{i + 1:04d}.png") for i in range(4)] + [("done", 4)]
    assert sorted(os.listdir(out)) == [f"page_{i:04d}.png" for i in range(1, 5)]


@pytest.mark.poppler
def test_stop_ends_rendering_early(qapp, tmp_path, make_pdf):
    worker = main.PDFConversionWorker(make_pdf(20), str(tmp_path))
    pages, finished = [], []
    worker.page_converted.connect(lambda i, p: pages.append(i))
    worker.conversion_finished.connect(finished.append)
    worker.start()
    wait_until(qapp, lambda: pages, timeout=30)
    worker.stop()  # as the app does when another file is opened
    assert worker.wait(30000)
    qapp.processEvents()
    assert 1 <= len(pages) < 20
    assert finished == []  # a stopped conversion does not report completion


@pytest.mark.poppler
def test_broken_pdf_reports_error(qapp, tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    worker = main.PDFConversionWorker(str(bad), str(tmp_path))
    errors = []
    worker.error_occurred.connect(lambda n, msg: errors.append(n))
    worker.start()
    wait_until(qapp, lambda: errors, timeout=30)
    worker.wait()
    assert errors == [0]
