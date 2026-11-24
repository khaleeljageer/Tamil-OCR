"""Saving and restoring proofreading sessions through the main window."""
import json
import os
import shutil

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

import main
from conftest import HAVE_POPPLER, open_with_fake_ocr, wait_until


@pytest.fixture
def edited_doc(qapp, window, make_pdf, monkeypatch, tmp_path):
    """A 4-page PDF with edits on pages 2 and 4, saved as a session at <tmp>/doc.tamilocr."""
    if not HAVE_POPPLER:
        pytest.skip("Poppler not available")
    pdf = make_pdf(4)
    open_with_fake_ocr(qapp, window, pdf, 4)
    window.next_page()
    window.text_edit.setPlainText("PAGE TWO FIXED")
    window.last_page()
    window.text_edit.setPlainText("PAGE FOUR FIXED")
    window.confidence_picker.setValue(50)  # hides the conf-40 'maybe' words
    window.lang_picker.set_languages(["eng", "tam_cus"])
    session_path = str(tmp_path / ("doc" + main.SESSION_EXTENSION))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (session_path, "")))
    assert window.save_session()
    return window, pdf, session_path


def test_session_file_contents(edited_doc):
    window, pdf, path = edited_doc
    saved = json.load(open(path, encoding="utf-8"))
    assert saved['format'] == main.SESSION_FORMAT
    assert saved['app_version'] == main.APP_VERSION
    assert saved['source_path'] == os.path.abspath(pdf)
    assert saved['source_relpath'] == "doc.pdf"
    assert saved['source_sha256'] == main.file_sha256(pdf)
    assert saved['languages'] == ["eng", "tam_cus"]
    assert saved['confidence_threshold'] == 50
    assert saved['current_page'] == 3 and saved['page_count'] == 4
    assert [p['edited_text'] for p in saved['pages']] == [None, "PAGE TWO FIXED", None, "PAGE FOUR FIXED"]
    assert all(set(p['ocr_data']) == set(main.SESSION_OCR_KEYS) for p in saved['pages'])


def test_dirty_marker_in_title(edited_doc):
    window, _, _ = edited_doc
    assert window.windowTitle() == f"doc.tamilocr — {main.APP_NAME}"
    window.text_edit.setPlainText("more")
    assert window.windowTitle() == f"doc.tamilocr * — {main.APP_NAME}"


def test_save_again_does_not_ask_for_a_path(edited_doc, monkeypatch):
    window, _, path = edited_doc
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: pytest.fail("should not ask for a path")))
    window.text_edit.setPlainText("LATEST")
    assert window.save_session()
    assert json.load(open(path, encoding="utf-8"))['pages'][3]['edited_text'] == "LATEST"


def test_restore_skips_ocr_and_restores_state(qapp, edited_doc, image_file, monkeypatch):
    window, _, path = edited_doc
    open_with_fake_ocr(qapp, window, image_file, 1)  # switch to something else
    window.lang_picker.set_languages(["tam_cus", "eng"])
    window.confidence_picker.setValue(0)

    ocr_calls = []
    monkeypatch.setattr(window.ocr_manager, "add_page", lambda *a: ocr_calls.append(a))
    assert window.load_session(path)
    wait_until(qapp, lambda: not window.busy and len(window.ocr_text) == 4, timeout=60)

    assert ocr_calls == []
    assert window.edited_text == {1: "PAGE TWO FIXED", 3: "PAGE FOUR FIXED"}
    assert window.current_page_index == 3
    assert window.text_edit.toPlainText() == "PAGE FOUR FIXED"
    assert window.lang_picker.lang_string() == "eng+tam_cus"
    assert window.confidence_threshold == 50
    assert "maybe" not in window.ocr_text[0]  # restored threshold applied to restored OCR
    assert not window.session_dirty
    assert window.session_path == path


def test_moved_folder_still_finds_document(qapp, edited_doc, tmp_path):
    window, pdf, path = edited_doc
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(pdf, moved / "doc.pdf")
    shutil.move(path, moved / os.path.basename(path))
    assert window.load_session(str(moved / os.path.basename(path)))
    wait_until(qapp, lambda: not window.busy, timeout=60)
    assert window.source_path == str(moved / "doc.pdf")


def test_missing_document_asks_user_to_locate_it(qapp, edited_doc, dialogs, monkeypatch, tmp_path):
    window, pdf, path = edited_doc
    relocated = str(tmp_path / "renamed.pdf")
    os.rename(pdf, relocated)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (relocated, "")))
    assert window.load_session(path)
    wait_until(qapp, lambda: not window.busy, timeout=60)
    assert dialogs.titles("information") == ["Locate document"]
    assert window.source_path == relocated


def test_changed_document_warns_and_can_be_declined(edited_doc, dialogs):
    window, pdf, path = edited_doc
    with open(pdf, "ab") as f:
        f.write(b"\n% changed\n")
    dialogs.question_answer = QMessageBox.StandardButton.No
    assert window.load_session(path) is False
    assert dialogs.titles("question") == ["Document changed"]


def test_corrupt_session_shows_warning(window, tmp_path, dialogs):
    bad = tmp_path / ("bad" + main.SESSION_EXTENSION)
    bad.write_text("{oops", encoding="utf-8")
    assert window.load_session(str(bad)) is False
    assert dialogs.titles("warning") == ["Cannot open session"]


def test_open_path_routes_sessions(edited_doc, monkeypatch):
    window, pdf, path = edited_doc
    window.session_dirty = False
    loaded, processed = [], []
    monkeypatch.setattr(window, "load_session", loaded.append)
    monkeypatch.setattr(window, "process_file", processed.append)
    window.open_path(path)
    window.open_path(pdf)
    assert loaded == [path] and processed == [pdf]


@pytest.mark.parametrize("answer, closes, saved", [
    (QMessageBox.StandardButton.Cancel, False, False),
    (QMessageBox.StandardButton.Discard, True, False),
    (QMessageBox.StandardButton.Save, True, True),
])
def test_unsaved_edits_prompt_on_close(edited_doc, dialogs, answer, closes, saved):
    window, _, path = edited_doc
    window.text_edit.setPlainText("UNSAVED CHANGE")
    dialogs.exec_answer = answer
    assert window.close() is closes
    stored = json.load(open(path, encoding="utf-8"))['pages'][3]['edited_text']
    assert (stored == "UNSAVED CHANGE") is saved


def test_no_prompt_without_edits(edited_doc, dialogs):
    window, _, _ = edited_doc
    dialogs.exec_answer = QMessageBox.StandardButton.Cancel  # would block closing if asked
    assert not window.session_dirty
    assert window.close()
