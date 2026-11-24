"""Page view, navigation, keyboard shortcuts, font and About dialog."""
import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QLabel

import main
from conftest import HAVE_POPPLER, open_with_fake_ocr, settle

Ctrl = Qt.KeyboardModifier.ControlModifier


@pytest.fixture
def doc(qapp, window, make_pdf):
    if not HAVE_POPPLER:
        pytest.skip("Poppler not available")
    open_with_fake_ocr(qapp, window, make_pdf(4), 4)
    window.graphics_view.setFocus()
    qapp.processEvents()
    return window


def scale(w):
    return w.graphics_view.transform().m11()


def test_editor_uses_bundled_tamil_font(window):
    """Bug 9: the font was loaded after the editor was created."""
    assert window.custom_font_family != "monospace"
    assert window.text_edit.font().family() == window.custom_font_family
    window.font_size_picker.setValue(20)
    assert window.text_edit.font().family() == window.custom_font_family
    assert window.text_edit.font().pointSize() == 20


PICKERS = ("font_size_picker", "confidence_picker", "lang_picker")


@pytest.mark.parametrize("name", PICKERS)
def test_toolbar_pickers_never_take_focus(qapp, window, name):
    """Clicking a toolbar control must not steal the keyboard from the editor."""
    window.text_edit.setFocus()
    qapp.processEvents()
    picker = getattr(window, name)
    assert picker.focusPolicy() == Qt.FocusPolicy.NoFocus
    QTest.mouseClick(picker, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    (picker.hidePopup if hasattr(picker, "hidePopup") else picker.menu_.hide)()
    qapp.processEvents()
    assert QApplication.focusWidget() is window.text_edit


def test_choosing_from_the_list_applies_the_value(qapp, window):
    window.font_size_picker.setCurrentIndex(window.font_size_picker.findData(16))  # as a click does
    window.confidence_picker.setCurrentIndex(window.confidence_picker.findData(60))
    assert window.text_edit.font().pointSize() == 16
    assert window.confidence_threshold == 60


@pytest.mark.parametrize("name", ("font_size_picker", "confidence_picker"))
def test_pickers_ignore_mouse_wheel(window, name):
    picker = getattr(window, name)
    before = picker.value()
    wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(picker, wheel)
    assert picker.value() == before


def test_font_size_picker_snaps_to_nearest_size(window):
    window.font_size_picker.setValue(13)
    assert window.font_size_picker.value() == 12
    window.font_size_picker.setValue(100)
    assert window.font_size_picker.value() == 36


def test_confidence_picker_offers_steps_of_five_and_keeps_other_values(window):
    picker = window.confidence_picker
    assert picker.values() == list(range(0, 101, 5))
    assert picker.currentText() == "0%"
    seen = []
    picker.valueChanged.connect(seen.append)
    picker.setValue(37)  # e.g. restored from an older session: kept exactly, not rounded
    assert picker.value() == 37 and picker.currentText() == "37%"
    assert picker.values()[7:9] == [35, 37]
    picker.setValue(37)
    assert seen == [37]  # no repeated signal when nothing changed
    assert window.confidence_threshold == 37


def test_about_dialog_shows_app_version(qapp):
    dialog = main.AboutDialog()  # keep a reference so its labels stay alive
    texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert main.APP_VERSION in texts
    assert "MIT-CMU" in texts and "Poppler" in texts


def test_window_title(window):
    assert window.windowTitle() == main.APP_NAME


# ------------------------------------------------------------------- zoom

def test_zoom_kept_on_resize_and_page_refresh(qapp, doc):
    doc.zoom_in(); doc.zoom_in()
    zoomed = scale(doc)
    doc.resize(1000, 700)
    settle(qapp, 0.4)
    assert scale(doc) == pytest.approx(zoomed)
    doc.display_current_page_with_cache()  # e.g. OCR result arriving for this page
    assert scale(doc) == pytest.approx(zoomed)


def test_unzoomed_view_refits_on_resize(qapp, doc):
    doc.fit_view()
    fitted = scale(doc)
    doc.resize(1400, 950)
    settle(qapp, 0.4)
    assert scale(doc) != pytest.approx(fitted)
    assert not doc.user_zoomed


def test_new_page_starts_fitted(doc):
    doc.zoom_in()
    doc.next_page()
    assert not doc.user_zoomed


def test_refresh_does_not_duplicate_highlights(doc):
    doc.display_current_page_with_cache()
    doc.display_current_page_with_cache()
    others = [item for item in doc.scene.items() if item is not doc.pix_item]
    assert len(others) == len(doc.highlight_items) == 3


# ------------------------------------------------------------- navigation

def test_nav_buttons_and_actions_follow_position(doc):
    assert not doc.prev_btn.isEnabled() and not doc.prev_page_action.isEnabled()
    assert doc.next_btn.isEnabled() and doc.next_page_action.isEnabled()
    doc.last_page()
    assert doc.current_page_index == 3
    assert doc.prev_btn.isEnabled() and not doc.next_btn.isEnabled()
    doc.next_page()  # no-op at the end
    assert doc.current_page_index == 3
    assert doc.page_info_label.text() == "Page 4 of 4"


@pytest.mark.parametrize("key, mods, start, expected", [
    (Qt.Key.Key_PageDown, Qt.KeyboardModifier.NoModifier, 0, 1),
    (Qt.Key.Key_PageUp, Qt.KeyboardModifier.NoModifier, 2, 1),
    (Qt.Key.Key_PageDown, Ctrl, 0, 1),
    (Qt.Key.Key_PageUp, Ctrl, 2, 1),
    (Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier, 0, 1),
    (Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier, 2, 1),
    (Qt.Key.Key_End, Ctrl, 0, 3),
    (Qt.Key.Key_Home, Ctrl, 3, 0),
])
def test_page_shortcuts_in_viewer(doc, key, mods, start, expected):
    doc.current_page_index = start
    doc.display_current_page_with_cache()
    doc.graphics_view.setFocus()
    QTest.keyClick(doc.graphics_view, key, mods)
    assert doc.current_page_index == expected


def test_plain_pgdown_in_editor_scrolls_text_not_page(doc):
    doc.text_edit.setFocus()
    QTest.keyClick(doc.text_edit, Qt.Key.Key_PageDown)
    assert doc.current_page_index == 0
    QTest.keyClick(doc.text_edit, Qt.Key.Key_PageDown, Ctrl)
    assert doc.current_page_index == 1


def test_pgdown_scrolls_zoomed_page_before_changing_page(qapp, doc):
    for _ in range(4):
        doc.zoom_in()
    qapp.processEvents()
    bar = doc.graphics_view.verticalScrollBar()
    QTest.keyClick(doc.graphics_view, Qt.Key.Key_PageDown)
    assert doc.current_page_index == 0 and bar.value() > 0
    bar.setValue(bar.maximum())
    QTest.keyClick(doc.graphics_view, Qt.Key.Key_PageDown)
    assert doc.current_page_index == 1


def test_zoom_and_highlight_shortcuts(doc):
    fitted = scale(doc)
    QTest.keyClick(doc.graphics_view, Qt.Key.Key_Equal, Ctrl)
    assert scale(doc) > fitted
    QTest.keyClick(doc.graphics_view, Qt.Key.Key_Minus, Ctrl)
    QTest.keyClick(doc.graphics_view, Qt.Key.Key_Minus, Ctrl)
    assert scale(doc) < fitted
    QTest.keyClick(doc.graphics_view, Qt.Key.Key_0, Ctrl)
    assert scale(doc) == pytest.approx(fitted)
    QTest.keyClick(doc.graphics_view, Qt.Key.Key_H, Ctrl)
    assert not doc.highlights_visible


def test_file_shortcuts_open_dialogs(doc, monkeypatch):
    asked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (asked.append(a[1]), ("", ""))[1]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (asked.append(a[1]), ("", ""))[1]))
    for key, mods in [(Qt.Key.Key_O, Ctrl), (Qt.Key.Key_E, Ctrl), (Qt.Key.Key_S, Ctrl),
                      (Qt.Key.Key_S, Ctrl | Qt.KeyboardModifier.ShiftModifier)]:
        QTest.keyClick(doc.graphics_view, key, mods)
    assert asked == ["Open File", "Save File", "Save Session", "Save Session"]


def test_reset_shortcut(doc):
    doc.text_edit.setPlainText("edited")
    QTest.keyClick(doc.graphics_view, Qt.Key.Key_R, Ctrl | Qt.KeyboardModifier.ShiftModifier)
    assert not doc.is_page_modified(0)
