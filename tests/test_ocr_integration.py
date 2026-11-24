"""End-to-end runs with the real bundled Tesseract (skipped if it cannot start)."""
import pytest

from conftest import page_image, settle, wait_until

pytestmark = pytest.mark.tesseract


def test_image_ocr_recognises_text_and_paragraphs(qapp, window, tmp_path):
    path = tmp_path / "paragraphs.png"
    page_image(["First paragraph line one", "First paragraph line two",
                "Second paragraph after a gap"], size=(1400, 900), gap_after={1}).save(path)
    window.process_file(str(path))
    wait_until(qapp, lambda: not window.busy, timeout=120)
    text = window.ocr_text[0]
    assert "paragraph" in text
    assert "\n\n" in text  # blank line between the two paragraphs
    assert window.highlight_items


@pytest.mark.poppler
def test_first_page_text_arrives_before_pdf_finishes_rendering(qapp, window, make_pdf):
    window.process_file(make_pdf(40))
    wait_until(qapp, lambda: 0 in window.ocr_text, timeout=120)
    assert window.pdf_converting and len(window.temp_pages) < 40
    assert "rendered)" in window.page_info_label.text()
    window.stop_workers()
    window.set_ui_enabled(True)


@pytest.mark.poppler
def test_switching_documents_mid_run_drops_stale_results(qapp, window, make_pdf, image_file):
    window.process_file(make_pdf(12))
    wait_until(qapp, lambda: window.ocr_manager.completed_pages >= 1, timeout=120)
    old_dir = window.temp_dir
    window.process_file(image_file)
    wait_until(qapp, lambda: not window.busy, timeout=120)
    settle(qapp, 2)  # let tasks from the old document finish and be ignored
    assert set(window.ocr_text) == {0}
    assert len(window.temp_pages) == 1
    assert "Page" not in window.ocr_text[0]  # text is from the image, not the PDF


def test_missing_language_fails_every_page_with_one_warning(qapp, window, image_file, dialogs, monkeypatch):
    monkeypatch.setattr(window, "current_lang_string", lambda: "does_not_exist")
    window.process_file(image_file)
    wait_until(qapp, lambda: not window.busy, timeout=120)
    assert window.page_errors and "does_not_exist" in window.page_errors[0]
    assert dialogs.titles("warning") == ["OCR failed"]


@pytest.mark.poppler
def test_rerun_completes_once_with_all_pages(qapp, window, make_pdf):
    window.process_file(make_pdf(3))
    wait_until(qapp, lambda: not window.busy, timeout=120)
    completions = []
    window.ocr_manager.processing_complete.connect(completions.append)
    window.rerun_ocr()
    wait_until(qapp, lambda: not window.busy, timeout=120)
    settle(qapp, 1)
    assert completions == [0]
    assert len(window.ocr_text) == 3


@pytest.mark.poppler
def test_real_ocr_exports_searchable_pdf(qapp, window, tmp_path, monkeypatch):
    """OCR a scan, then export: the PDF's text layer is the OCR text, word for word."""
    import subprocess
    from PyQt6.QtWidgets import QFileDialog
    path = tmp_path / "scan.png"
    page_image(["Searchable export works", "with several words per line"], size=(1400, 600)).save(path)
    window.process_file(str(path))
    wait_until(qapp, lambda: not window.busy, timeout=120)
    out = tmp_path / "scan_ocr.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    window.export_searchable_pdf()
    wait_until(qapp, lambda: window.export_worker is None, timeout=60)
    extracted = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True, check=True).stdout
    assert extracted.split() == window.ocr_text[0].split()
    assert "Searchable" in extracted
