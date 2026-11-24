"""Main window: opening documents, temp files, edits, confidence, busy state, export.

OCR results are injected (see open_with_fake_ocr), so these tests do not need Tesseract.
"""
import os
import tempfile

import pytest
from PyQt6.QtWidgets import QFileDialog

from conftest import HAVE_POPPLER, open_with_fake_ocr, page_image, settle, wait_until


@pytest.fixture
def no_real_ocr(window, monkeypatch):
    """Queue OCR tasks into the void, so a test controls every result itself."""
    monkeypatch.setattr(window.ocr_manager.thread_pool, "start", lambda task: None)


def test_user_image_in_temp_dir_is_never_deleted(qapp, window, tmp_path):
    """Bug 1: images under the system temp dir used to be deleted as 'temp pages'."""
    user_image = os.path.join(tempfile.gettempdir(), f"user_scan_{os.getpid()}.png")
    try:
        page_image(["hello"]).save(user_image)
        open_with_fake_ocr(qapp, window, user_image, 1)
        window.clear_temp_pages()
        window.close()
        assert os.path.exists(user_image)
    finally:
        if os.path.exists(user_image):
            os.remove(user_image)


@pytest.mark.poppler
def test_pdf_pages_live_in_app_temp_dir_and_are_cleaned_up(qapp, window, make_pdf, image_file):
    open_with_fake_ocr(qapp, window, make_pdf(3), 3)
    temp_dir = window.temp_dir
    assert temp_dir and os.path.isdir(temp_dir)
    assert all(p.startswith(temp_dir) for p in window.temp_pages)

    open_with_fake_ocr(qapp, window, image_file, 1)  # opening another file removes it
    assert not os.path.exists(temp_dir)
    assert window.temp_dir is None
    assert os.path.exists(image_file)


@pytest.mark.poppler
def test_temp_dir_removed_on_close(qapp, window, make_pdf):
    open_with_fake_ocr(qapp, window, make_pdf(2), 2)
    temp_dir = window.temp_dir
    window.close()
    assert not os.path.exists(temp_dir)


@pytest.fixture
def three_pages(qapp, window, make_pdf):
    if not HAVE_POPPLER:
        pytest.skip("Poppler not available")
    open_with_fake_ocr(qapp, window, make_pdf(3), 3)
    return window


def test_programmatic_text_does_not_mark_page_modified(three_pages):
    """Bug 4: placeholder text used to count as a user edit."""
    w = three_pages
    w.set_editor_text("Processing OCR...")
    assert w.edited_text == {}
    assert w.edit_status_label.text() == "📄 Original"


def test_edit_survives_navigation_and_reset_restores_ocr(three_pages):
    """Bug 5: Reset used to restore the edited text after changing pages."""
    w = three_pages
    original = w.ocr_text[0]
    w.text_edit.setPlainText("USER EDIT")
    assert w.is_page_modified(0)
    assert w.edit_status_label.text() == "✏️ Modified"
    assert w.reset_text_btn.isEnabled()

    w.next_page()
    assert not w.is_page_modified(1)
    w.prev_page()
    assert w.text_edit.toPlainText() == "USER EDIT"

    w.reset_current_text()
    assert w.text_edit.toPlainText() == original
    assert not w.is_page_modified(0)
    assert not w.reset_text_btn.isEnabled()


def test_confidence_refilters_all_pages_but_keeps_edits(three_pages):
    w = three_pages
    assert "maybe" in w.ocr_text[1]
    w.text_edit.setPlainText("EDITED")
    w.confidence_picker.setValue(50)
    assert all("maybe" not in w.ocr_text[i] for i in range(3))
    assert w.text_edit.toPlainText() == "EDITED"
    w.confidence_picker.setValue(100)
    assert w.ocr_text[2] == ""
    assert w.page_text(0) == "EDITED"


def test_highlight_boxes_follow_confidence(three_pages):
    w = three_pages
    assert len(w.highlight_items) == 3
    w.confidence_picker.setValue(50)
    assert len(w.highlight_items) == 2
    w.toggle_highlights_action.trigger()
    assert not any(item.isVisible() for item in w.highlight_items)


def test_rerun_with_edits_asks_and_declining_keeps_them(three_pages, dialogs):
    w = three_pages
    w.text_edit.setPlainText("KEEP")
    dialogs.question_answer = dialogs.question_answer.No
    w.rerun_ocr()
    assert dialogs.titles("question") == ["Re-Run OCR"]
    assert w.page_text(0) == "KEEP" and not w.busy


def test_export_writes_edits_and_paragraphs(three_pages, tmp_path, monkeypatch):
    w = three_pages
    out = tmp_path / "out.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    w.next_page()
    w.text_edit.setPlainText("Corrected page two")
    w.export_action.trigger()
    text = out.read_text(encoding="utf-8")
    assert "=== Page 1 ===\npage1 text\nmaybe" in text
    assert "=== Page 2 ===\nCorrected page two" in text
    assert "=== Page 3 ===" in text


def test_export_failure_shows_dialog(three_pages, tmp_path, monkeypatch, dialogs):
    target = str(tmp_path / "missing_dir" / "out.txt")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (target, "")))
    three_pages.export_text()
    assert dialogs.titles("critical") == ["Export failed"]


@pytest.mark.poppler
def test_controls_disabled_while_busy(qapp, window, make_pdf, no_real_ocr):
    """Bug 8: Open/Export used to stay enabled during processing."""
    window.process_file(make_pdf(3))  # OCR never finishes (no_real_ocr): stays busy
    try:
        assert window.busy
        for action in (window.open_action, window.export_action, window.export_pdf_action,
                       window.save_session_action,
                       window.rerun_action):
            assert not action.isEnabled()
        assert not window.lang_picker.isEnabled()
        assert not window.confidence_picker.isEnabled()
    finally:
        window.stop_workers()
        window.set_ui_enabled(True)


def test_failed_page_is_reported_and_run_completes(qapp, window, image_file, dialogs, no_real_ocr):
    """Bug 7, at app level: failures count toward completion and are shown."""
    window.process_file(image_file)
    assert window.busy
    manager = window.ocr_manager
    # Report the page's OCR task as failed
    manager.signals.page_failed.emit(manager.job_id, 0, "Pytesseract error: no language")
    settle(qapp)
    assert not window.busy
    assert window.text_edit.toPlainText().startswith("OCR failed for this page")
    assert "1 page(s) failed" in window.status_bar.currentMessage()
    assert dialogs.titles("warning") == ["OCR failed"]  # every page failed: likely a setup problem


@pytest.mark.poppler
def test_broken_pdf_shows_dialog_and_ui_recovers(qapp, window, tmp_path, dialogs):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    window.process_file(str(bad))
    wait_until(qapp, lambda: not window.busy, timeout=30)
    assert dialogs.titles("warning") == ["Could not open PDF"]
    assert window.open_action.isEnabled()
