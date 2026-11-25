"""Shared fixtures. Tests run headless (Qt offscreen platform) and never show dialogs."""
import os
import shutil
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image, ImageDraw, ImageFont
from PyQt6.QtWidgets import QApplication, QMessageBox

import main

FONT_PATH = main.resource_path(os.path.join("font", "marutham.ttf"))


# ---------------------------------------------------------------- tool checks

def _tesseract_available():
    try:
        main.pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _poppler_available():
    if main.POPPLER_PATH:
        return os.path.isdir(main.POPPLER_PATH)
    return shutil.which("pdftoppm") is not None and shutil.which("pdfinfo") is not None


HAVE_TESSERACT = _tesseract_available()
HAVE_POPPLER = _poppler_available()


def pytest_collection_modifyitems(config, items):
    skip_tess = pytest.mark.skip(reason="Tesseract not available")
    skip_pop = pytest.mark.skip(reason="Poppler (pdftoppm/pdfinfo) not available")
    for item in items:
        if "tesseract" in item.keywords and not HAVE_TESSERACT:
            item.add_marker(skip_tess)
        if "poppler" in item.keywords and not HAVE_POPPLER:
            item.add_marker(skip_pop)


# ---------------------------------------------------------------- Qt helpers

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Tamil-OCR-tests")
    return app


def wait_until(app, predicate, timeout=120.0):
    """Process Qt events until predicate() is true; fail the test on timeout."""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(f"timed out after {timeout}s waiting for condition")


def settle(app, seconds=0.3):
    """Let queued signals (e.g. from finishing worker threads) be delivered."""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


class Dialogs:
    """Records QMessageBox calls instead of showing (and blocking on) them."""

    def __init__(self):
        self.shown = []
        self.question_answer = QMessageBox.StandardButton.Yes
        self.exec_answer = QMessageBox.StandardButton.Discard

    def titles(self, kind=None):
        return [title for k, title in self.shown if kind is None or k == kind]


@pytest.fixture
def dialogs(monkeypatch):
    rec = Dialogs()
    for kind in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, kind,
                            staticmethod(lambda *a, _k=kind, **kw: rec.shown.append((_k, a[1]))))

    def question(*a, **kw):
        rec.shown.append(("question", a[1]))
        return rec.question_answer
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: rec.exec_answer)
    return rec


@pytest.fixture
def window(qapp, dialogs):
    w = main.OCRApp()
    w.resize(1200, 800)
    w.show()
    w.activateWindow()
    qapp.processEvents()
    yield w
    w.confirm_discard_changes = lambda: True
    w.close()
    qapp.processEvents()


# ---------------------------------------------------------------- sample data

def make_ocr_data(words):
    """Build Tesseract-style image_to_data output.

    words: list of (text, conf, block, par, line); boxes are laid out automatically.
    """
    data = {k: [] for k in main.SESSION_OCR_KEYS}
    for i, (text, conf, block, par, line) in enumerate(words):
        data['text'].append(text)
        data['conf'].append(conf)
        data['block_num'].append(block)
        data['par_num'].append(par)
        data['line_num'].append(line)
        data['left'].append(20 + (i % 5) * 120)
        data['top'].append(20 + line * 40 + par * 100 + block * 200)
        data['width'].append(100)
        data['height'].append(30)
    return data


def fake_page_data(page_index):
    """OCR result for one page: a confident word naming the page, plus a low-confidence word."""
    return make_ocr_data([(f"page{page_index + 1}", 90, 1, 1, 1),
                          ("text", 85, 1, 1, 1),
                          ("maybe", 40, 1, 1, 2)])


def open_with_fake_ocr(app, window, path, page_count, **session_extra):
    """Open a document in the app with injected OCR results (no Tesseract needed).

    Uses the session-restore path, so every page "finishes" as soon as it is rendered.
    """
    session = {'pages': [{'ocr_data': fake_page_data(i), 'edited_text': None} for i in range(page_count)]}
    session.update(session_extra)
    window.process_file(path, session=session)
    wait_until(app, lambda: not window.busy and len(window.ocr_text) == page_count, timeout=60)


def page_image(text_lines, size=(1240, 1754), gap_after=()):
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 44)
    y = 80
    for i, line in enumerate(text_lines):
        draw.text((60, y), line, fill="black", font=font)
        y += 300 if i in gap_after else 70
    return img


@pytest.fixture
def image_file(tmp_path):
    path = tmp_path / "scan.png"
    page_image(["Tamil OCR test image", "தமிழ் எழுத்து"]).save(path)
    return str(path)


@pytest.fixture
def make_pdf(tmp_path):
    def _make(pages, name="doc.pdf"):
        path = tmp_path / name
        images = [page_image([f"Page {i + 1} sample line", f"பக்கம் {i + 1} தமிழ்"]) for i in range(pages)]
        images[0].save(path, save_all=True, append_images=images[1:], resolution=150)
        return str(path)
    return _make
