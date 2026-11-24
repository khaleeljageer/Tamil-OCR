import hashlib
import json
import logging
import logging.handlers
import os
import platform
import pytesseract
import shutil
import sys
import tempfile
import time

# We run several Tesseract processes in parallel; letting each one also spawn its own
# OpenMP threads oversubscribes the CPU (measured ~27% slower on 4 parallel pages).
# Child processes inherit this. Respect a value the user has set explicitly.
os.environ.setdefault('OMP_THREAD_LIMIT', '1')


def restore_library_path(env, frozen, system):
    """Undo PyInstaller's LD_LIBRARY_PATH so child processes use their own libraries.

    A frozen Linux build points LD_LIBRARY_PATH at its extraction folder (sys._MEIPASS),
    which holds libraries copied from the build machine. Child processes (the Tesseract
    AppImage, pdftoppm) inherit it and can fail to start when those copies don't match
    what they were built against. PyInstaller keeps the user's original value in
    LD_LIBRARY_PATH_ORIG. The running app is unaffected: the loader reads LD_LIBRARY_PATH
    only at process start.
    """
    if not (frozen and system == 'Linux'):
        return
    original = env.get('LD_LIBRARY_PATH_ORIG')
    if original is not None:
        env['LD_LIBRARY_PATH'] = original
    else:
        env.pop('LD_LIBRARY_PATH', None)


restore_library_path(os.environ, getattr(sys, 'frozen', False), platform.system())

APP_NAME = "Tamil OCR"
APP_VERSION = "1.1.0"

logger = logging.getLogger("tamil_ocr")


def resource_path(relative_path):
    """Absolute path to a bundled resource; works from source and from a PyInstaller build."""
    # PyInstaller extracts bundled files to a temp folder exposed as sys._MEIPASS
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def setup_logging():
    """Log to a rotating file in the user's data directory (the windowed build has no console)."""
    from PyQt6.QtCore import QStandardPaths
    log_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    if not log_dir:
        log_dir = tempfile.gettempdir()
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "tamil-ocr.log")

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return log_file


from PyQt6.QtCore import Qt, QRectF, QThread, pyqtSignal, QTimer, QThreadPool, QRunnable, QUrl, QObject
from PyQt6.QtGui import QPixmap, QPen, QColor, QBrush, QPainter, QFontDatabase, QDesktopServices, QAction, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QLabel, QPushButton, QFileDialog, QTextEdit, QHBoxLayout, QSplitter,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
    QProgressBar, QStatusBar, QSpinBox, QFrame, QDialog, QToolBar, QSizePolicy,
    QMessageBox, QToolButton, QMenu
)
from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image

# Raise Pillow's decompression-bomb limit (default ~89 MP) so large scans and 300-DPI
# renders of big pages load, while still refusing absurd sizes. Pillow raises
# DecompressionBombError above 2x this value; the PDF worker then retries at lower DPI.
Image.MAX_IMAGE_PIXELS = 150_000_000

PDF_DPI_STEPS = (300, 200, 150)  # tried in order when a rendered page is too large
MAX_PAGE_DIMENSION = 4000  # longest side (px) of page images handed to OCR
tessdata_dir = resource_path('tessdata')
os.environ['TESSDATA_PREFIX'] = tessdata_dir
if platform.system() == 'Windows':
    pytesseract.pytesseract.tesseract_cmd = resource_path(os.path.join("binary", "windows", "tesseract", "tesseract.exe"))
elif platform.system() == 'Linux':
    pytesseract.pytesseract.tesseract_cmd = resource_path(os.path.join("binary", "linux", "tesseract", "tesseract.AppImage"))

# Bundled poppler on Windows; elsewhere rely on the system's poppler-utils (None = search PATH)
if platform.system() == 'Windows':
    POPPLER_PATH = resource_path(os.path.join("binary", "windows", "poppler", "Library", "bin"))
else:
    POPPLER_PATH = None


def extract_text_lines(data, confidence_threshold):
    """Group Tesseract words into lines, keeping only words above the confidence threshold.

    A blank line is inserted between paragraphs (a change of Tesseract block or
    paragraph), so the exported text keeps the page's paragraph structure.
    """
    text_lines = []
    current_line = []
    last_block_num = last_par_num = last_line_num = None

    n_boxes = len(data.get('text', []))
    for i in range(n_boxes):
        try:
            conf = float(data['conf'][i])
        except (TypeError, ValueError):
            conf = -1.0
        word = (data['text'][i] or '').strip()

        if conf > confidence_threshold and word:
            block_num = int(data.get('block_num', [0] * n_boxes)[i])
            par_num = int(data.get('par_num', [0] * n_boxes)[i])
            line_num = int(data.get('line_num', [0] * n_boxes)[i])

            new_paragraph = (block_num, par_num) != (last_block_num, last_par_num)
            if new_paragraph or line_num != last_line_num:
                if current_line:
                    text_lines.append(' '.join(current_line))
                    current_line = []
                if new_paragraph and text_lines:
                    text_lines.append('')
                last_block_num, last_par_num, last_line_num = block_num, par_num, line_num

            current_line.append(word)

    if current_line:
        text_lines.append(' '.join(current_line))

    return text_lines


# Friendly names for language models; anything else is shown by its code
LANGUAGE_NAMES = {
    'tam_cus': 'Tamil (custom model)',
    'tam': 'Tamil',
    'eng': 'English',
}
DEFAULT_LANGUAGES = ['tam_cus', 'eng']


def available_languages():
    """Language codes of the .traineddata models in the bundled tessdata directory."""
    try:
        codes = sorted(f[:-len('.traineddata')] for f in os.listdir(tessdata_dir)
                       if f.endswith('.traineddata'))
    except OSError:
        logger.exception("Cannot list tessdata directory %s", tessdata_dir)
        return []
    # 'osd' is orientation/script detection, not a recognition language
    codes = [c for c in codes if c != 'osd']
    # Known languages first, in a stable order
    known = [c for c in LANGUAGE_NAMES if c in codes]
    return known + [c for c in codes if c not in known]


# --- Proofreading sessions ------------------------------------------------------

SESSION_EXTENSION = ".tamilocr"
SESSION_FORMAT = 1
# Only the Tesseract fields the app uses are stored, to keep session files small
SESSION_OCR_KEYS = ('text', 'conf', 'left', 'top', 'width', 'height', 'block_num', 'par_num', 'line_num')


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def compact_ocr_data(data):
    return {k: list(data[k]) for k in SESSION_OCR_KEYS if k in data}


def write_session_file(session_path, session):
    """Write atomically so a crash mid-save never leaves a truncated session."""
    tmp_path = session_path + ".tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(session, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp_path, session_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def read_session_file(session_path):
    with open(session_path, encoding='utf-8') as f:
        session = json.load(f)
    if not isinstance(session, dict) or session.get('format') != SESSION_FORMAT:
        raise ValueError("Not a Tamil OCR session file, or it was saved by an incompatible version")
    if not isinstance(session.get('pages'), list):
        raise ValueError("Session file has no page data")
    return session


def resolve_session_source(session_path, session):
    """Find the session's source document: saved absolute path, else the same relative
    location next to the session file (so a folder can be moved or shared as a whole)."""
    candidates = [session.get('source_path')]
    rel = session.get('source_relpath')
    if rel:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(session_path)), rel))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


class StayOpenMenu(QMenu):
    """Menu whose checkable items toggle without closing it (for multi-select)."""

    def mouseReleaseEvent(self, event):
        action = self.activeAction()
        if action is not None and action.isEnabled() and action.isCheckable():
            action.trigger()
            return
        super().mouseReleaseEvent(event)


class LanguagePicker(QToolButton):
    """Multi-select of installed Tesseract languages.

    Order matters to Tesseract (the first language is the primary one), so the
    selection keeps the order in which languages were ticked.
    """
    languages_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_ = StayOpenMenu(self)
        self.setMenu(self.menu_)
        self.actions_by_code = {}
        self.selected = []

        codes = available_languages()
        for code in codes:
            label = LANGUAGE_NAMES.get(code, code)
            action = QAction(f"{label}  [{code}]", self, checkable=True)
            action.toggled.connect(lambda checked, c=code: self._on_toggled(c, checked))
            self.menu_.addAction(action)
            self.actions_by_code[code] = action
        if not codes:
            missing = QAction("No language models found in tessdata/", self)
            missing.setEnabled(False)
            self.menu_.addAction(missing)

        self.set_languages([c for c in DEFAULT_LANGUAGES if c in codes] or codes[:1])

    def _on_toggled(self, code, checked):
        if checked and code not in self.selected:
            self.selected.append(code)
        elif not checked and code in self.selected:
            if len(self.selected) == 1:
                # At least one language is required; undo the uncheck
                self.actions_by_code[code].blockSignals(True)
                self.actions_by_code[code].setChecked(True)
                self.actions_by_code[code].blockSignals(False)
                return
            self.selected.remove(code)
        self._update_label()
        self.languages_changed.emit()

    def _update_label(self):
        self.setText(self.lang_string() or "(none)")
        names = [LANGUAGE_NAMES.get(c, c) for c in self.selected]
        self.setToolTip("OCR languages (first is primary): " + ", ".join(names) +
                        "\nClick to choose from the models installed in tessdata/")

    def set_languages(self, codes):
        """Select exactly these languages, in order; unknown codes are ignored.
        Returns the codes that are not installed."""
        missing = [c for c in codes if c not in self.actions_by_code]
        wanted = [c for c in codes if c in self.actions_by_code]
        if not wanted:
            return missing
        self.selected = list(wanted)
        for code, action in self.actions_by_code.items():
            action.blockSignals(True)
            action.setChecked(code in wanted)
            action.blockSignals(False)
        self._update_label()
        self.languages_changed.emit()
        return missing

    def languages(self):
        return list(self.selected)

    def lang_string(self):
        return '+'.join(self.selected)


class PDFConversionWorker(QThread):
    """Renders a PDF one page at a time into an app-owned temp directory.

    Pages are announced as soon as each one is written, so OCR can start on page 1
    while later pages are still rendering, and only one page is held in memory.
    """
    document_info = pyqtSignal(int)  # total page count (emitted first)
    page_converted = pyqtSignal(int, str)  # page_index, image path
    conversion_finished = pyqtSignal(int)  # number of pages converted
    error_occurred = pyqtSignal(int, str)  # pages converted before the error, message

    def __init__(self, pdf_path, output_dir):
        super().__init__()
        self.pdf_path = pdf_path
        # Every page image is written here, so cleanup never touches files the user opened
        self.output_dir = output_dir
        self.should_stop = False

    def run(self):
        converted = 0
        try:
            info = pdfinfo_from_path(self.pdf_path, poppler_path=POPPLER_PATH)
            total_pages = int(info.get("Pages", 0))
            self.document_info.emit(total_pages)

            for page_number in range(1, total_pages + 1):
                if self.should_stop:
                    return
                page = self.render_page(page_number)
                if self.should_stop:
                    return

                page = self.limit_size(page)
                tmp_path = os.path.join(self.output_dir, f"page_{page_number:04d}.png")
                page.save(tmp_path, 'PNG')
                page.close()

                self.page_converted.emit(page_number - 1, tmp_path)
                converted += 1

            self.conversion_finished.emit(converted)

        except Exception as error:
            logger.exception("PDF conversion failed after %d page(s)", converted)
            self.error_occurred.emit(converted, str(error))

    def render_page(self, page_number):
        """Render one page, stepping down the DPI if the result is too large for Pillow."""
        last_error = None
        for dpi in PDF_DPI_STEPS:
            try:
                images = convert_from_path(
                    self.pdf_path, dpi=dpi, first_page=page_number, last_page=page_number,
                    poppler_path=POPPLER_PATH)
                if not images:
                    raise RuntimeError(f"poppler returned no image for page {page_number}")
                if dpi != PDF_DPI_STEPS[0]:
                    logger.info("Page %d rendered at reduced %d DPI", page_number, dpi)
                return images[0]
            except Image.DecompressionBombError as error:
                logger.warning("Page %d too large at %d DPI, retrying lower", page_number, dpi)
                last_error = error
        raise RuntimeError(f"Page {page_number} is too large to render: {last_error}")

    @staticmethod
    def limit_size(page):
        """Downscale so the longest side is at most MAX_PAGE_DIMENSION, keeping aspect ratio."""
        width, height = page.size
        longest = max(width, height)
        if longest <= MAX_PAGE_DIMENSION:
            return page
        scale = MAX_PAGE_DIMENSION / longest
        resized = page.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                              Image.Resampling.LANCZOS)
        page.close()
        return resized

    def stop(self):
        self.should_stop = True


class OCRTask(QRunnable):
    """Individual OCR task for parallel processing"""

    def __init__(self, job_id, page_index, image_path, confidence_threshold, lang_string, signals):
        super().__init__()
        self.job_id = job_id
        self.page_index = page_index
        self.image_path = image_path
        self.confidence_threshold = confidence_threshold
        self.lang_string = lang_string
        self.signals = signals

    def run(self):
        try:
            # Process OCR for this page
            pil_img = Image.open(self.image_path).convert('RGB')
            try:
                ocr_data = pytesseract.image_to_data(pil_img, lang=self.lang_string, output_type=pytesseract.Output.DICT)
            except Exception as e:
                self.signals.page_failed.emit(self.job_id, self.page_index, f"Pytesseract error: {e}")
                return

            # Extract text with confidence filtering
            text = '\n'.join(extract_text_lines(ocr_data, self.confidence_threshold))

            # Emit results
            self.signals.page_processed.emit(self.job_id, self.page_index, text, ocr_data)

        except Exception as e:
            logger.exception("Unexpected OCR error on page %d", self.page_index + 1)
            self.signals.page_failed.emit(self.job_id, self.page_index, f"OCR error: {e}")


class OCRSignals(QObject):
    """Signals emitted from worker threads; every signal carries the job ID it belongs to."""
    page_processed = pyqtSignal(int, int, str, object)  # job_id, page_index, text, ocr_data
    page_failed = pyqtSignal(int, int, str)  # job_id, page_index, message


class OCRManager(QObject):
    """Manages parallel OCR processing.

    A job is started with the expected page count, then pages are added as they
    become available (all at once for images/re-runs, one by one while a PDF renders).
    Each job gets a new ID; results from tasks of an older job (e.g. still running
    after a new file was opened) are dropped here, so the UI only sees the current job.
    """
    page_processed = pyqtSignal(int, str, object)  # page_index, text, ocr_data
    page_failed = pyqtSignal(int, str)  # page_index, message
    processing_complete = pyqtSignal(int)  # number of failed pages
    progress_update = pyqtSignal(int, int)  # pages finished, total pages

    def __init__(self):
        super().__init__()
        self.signals = OCRSignals()
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(min(4, (os.cpu_count()) or 2))
        self.job_id = 0
        self.total_pages = 0
        self.completed_pages = 0
        self.failed_pages = 0
        self.confidence_threshold = 0
        self.lang_string = ""
        self.job_done = True

        # Connect once; jobs may be started many times
        self.signals.page_processed.connect(self._on_task_processed)
        self.signals.page_failed.connect(self._on_task_failed)

    def start_job(self, total_pages, confidence_threshold, lang_string):
        self.job_id += 1
        self.total_pages = total_pages
        self.completed_pages = 0
        self.failed_pages = 0
        self.confidence_threshold = confidence_threshold
        self.lang_string = lang_string
        self.job_done = False
        self.progress_update.emit(0, total_pages)

    def add_page(self, page_index, image_path):
        task = OCRTask(self.job_id, page_index, image_path,
                       self.confidence_threshold, self.lang_string, self.signals)
        self.thread_pool.start(task)

    def add_cached_result(self, page_index, ocr_data):
        """Count a page whose OCR result is already known (restored from a session)."""
        text = '\n'.join(extract_text_lines(ocr_data, self.confidence_threshold))
        self.page_processed.emit(page_index, text, ocr_data)
        self._count_finished_page()

    def start_processing(self, image_paths, confidence_threshold, lang_string):
        """Convenience: start a job for a known list of pages."""
        self.start_job(len(image_paths), confidence_threshold, lang_string)
        for i, image_path in enumerate(image_paths):
            self.add_page(i, image_path)

    def set_total(self, total_pages):
        """Shrink the expected page count (e.g. PDF rendering stopped early)."""
        self.total_pages = total_pages
        self._check_complete()

    def _on_task_processed(self, job_id, page_index, text, ocr_data):
        if job_id != self.job_id:
            return
        self.page_processed.emit(page_index, text, ocr_data)
        self._count_finished_page()

    def _on_task_failed(self, job_id, page_index, message):
        if job_id != self.job_id:
            return
        self.failed_pages += 1
        self.page_failed.emit(page_index, message)
        self._count_finished_page()

    def _count_finished_page(self):
        self.completed_pages += 1
        self.progress_update.emit(self.completed_pages, self.total_pages)
        self._check_complete()

    def _check_complete(self):
        if not self.job_done and self.completed_pages >= self.total_pages:
            self.job_done = True
            self.processing_complete.emit(self.failed_pages)

    def stop_all(self):
        # Invalidate the current job so results from still-running tasks are ignored
        self.job_id += 1
        self.job_done = True
        self.thread_pool.clear()
        self.thread_pool.waitForDone(3000)


class ZoomableGraphicsView(QGraphicsView):
    user_zoomed = pyqtSignal()  # emitted when the user zooms with Ctrl+wheel
    page_up_requested = pyqtSignal()
    page_down_requested = pyqtSignal()

    def __init__(self, scene):
        super().__init__(scene)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHints(QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform |
                            QPainter.RenderHint.TextAntialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def keyPressEvent(self, event):
        """PgUp/PgDown scroll a zoomed page; at the top/bottom edge they change page
        (like a PDF reader). Kept local to the viewer so the text editor's own
        PgUp/PgDown scrolling is not taken over by a window-wide shortcut."""
        if event.modifiers() == Qt.KeyboardModifier.NoModifier:
            bar = self.verticalScrollBar()
            if event.key() == Qt.Key.Key_PageDown and bar.value() >= bar.maximum():
                self.page_down_requested.emit()
                event.accept()
                return
            if event.key() == Qt.Key.Key_PageUp and bar.value() <= bar.minimum():
                self.page_up_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        # Check if Ctrl key is pressed for zoom
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Get the angle delta (positive for up, negative for down)
            angle_delta = event.angleDelta().y()

            # Calculate zoom factor
            zoom_factor = 1.25 if angle_delta > 0 else 0.8

            # Apply zoom
            self.scale(zoom_factor, zoom_factor)
            self.user_zoomed.emit()

            # Accept the event so it doesn't propagate
            event.accept()
        else:
            # Let the default scroll behavior handle it
            super().wheelEvent(event)


class ImageViewerWidget(QWidget):
    """Custom widget containing the graphics view with floating zoom controls"""
    resized = pyqtSignal()

    def __init__(self, scene):
        super().__init__()
        self.graphics_view = ZoomableGraphicsView(scene)
        self.setup_ui()

    def setup_ui(self):
        # Main layout for the image viewer
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.graphics_view)

        # Create floating zoom controls
        self.zoom_controls = QFrame(self)
        self.zoom_controls.setFixedSize(120, 40)
        self.zoom_controls.setStyleSheet("""
            QFrame {
                background-color: rgba(50, 50, 50, 200);
                border: 1px solid rgba(100, 100, 100, 150);
                border-radius: 5px;
            }
            QPushButton {
                background-color: rgba(70, 70, 70, 200);
                border: 1px solid rgba(120, 120, 120, 150);
                border-radius: 3px;
                color: white;
                font-weight: bold;
                padding: 2px;
            }
            QPushButton:hover {
                background-color: rgba(90, 90, 90, 220);
                border: 1px solid rgba(140, 140, 140, 180);
            }
            QPushButton:pressed {
                background-color: rgba(110, 110, 110, 240);
            }
        """)

        # Layout for zoom controls
        zoom_layout = QHBoxLayout(self.zoom_controls)
        zoom_layout.setContentsMargins(5, 5, 5, 5)
        zoom_layout.setSpacing(2)

        # Create zoom buttons with icons/symbols
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedSize(30, 30)
        self.zoom_in_btn.setToolTip("Zoom In (Ctrl+Mouse Wheel)")

        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedSize(30, 30)
        self.zoom_out_btn.setToolTip("Zoom Out (Ctrl+Mouse Wheel)")

        self.fit_btn = QPushButton("⊡")
        self.fit_btn.setFixedSize(30, 30)
        self.fit_btn.setToolTip("Fit to Screen")

        zoom_layout.addWidget(self.zoom_in_btn)
        zoom_layout.addWidget(self.zoom_out_btn)
        zoom_layout.addWidget(self.fit_btn)

        # Position zoom controls in top-right corner
        self.position_zoom_controls()

    def position_zoom_controls(self):
        """Position zoom controls in the top-right corner"""
        self.zoom_controls.move(self.width() - 130, 10)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Reposition zoom controls when widget is resized
        self.position_zoom_controls()
        # Covers window resizes and splitter drags
        self.resized.emit()



class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Tamil OCR")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Author
        author_label = QLabel("<b>Author:</b> Khaleel Jageer")
        author_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(author_label)

        # Email
        email_label = QLabel("<b>Email:</b> jskcse4@gmail.com")
        email_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(email_label)

        org_label = QLabel("<b>Organization:</b> Kaniyam Foundation")
        org_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(org_label)

        # Version
        version_label = QLabel(f"<b>Version:</b> {APP_VERSION}")
        version_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(version_label)

        # Project Repo
        repo_label = QLabel('<b>Project Repository:</b> <a href="https://github.com/khaleeljageer/Tamil-OCR">https://github.com/khaleeljageer/Tamil-OCR</a>')
        repo_label.setOpenExternalLinks(True)
        repo_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(repo_label)

        # Thanks
        thanks_label = QLabel("""
            <br>
            <b>Thanks to:</b><br>
            Tesseract OCR (<a href="https://tesseract-ocr.github.io/">Apache 2.0 License</a>), <br>
            PyQt6 (<a href="https://www.riverbankcomputing.com/software/pyqt/license/">GPLv3 License</a>), <br>
            pdf2image (<a href="https://github.com/Belval/pdf2image/blob/master/LICENSE">MIT License</a>), <br>
            Pillow (<a href="https://github.com/python-pillow/Pillow/blob/main/LICENSE">MIT-CMU License</a>), <br>
            Poppler (<a href="https://poppler.freedesktop.org/">GPL License</a>).<br><br>
            This project is licensed under <a href="https://www.gnu.org/licenses/gpl-3.0.en.html">GPL v3</a>.
        """)
        thanks_label.setOpenExternalLinks(True)
        thanks_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(thanks_label)

        # Report Issue Button
        issue_button = QPushButton("Report Issue")
        issue_button.clicked.connect(self.open_issue_page)
        layout.addWidget(issue_button)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    def open_issue_page(self):
        url = QUrl("https://github.com/khaleeljageer/Tamil-OCR/issues/new/choose")
        QDesktopServices.openUrl(url)


class OCRApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setGeometry(200, 100, 1200, 800)

        self.temp_pages = []  # image path for each page (user's file or app-owned temp files)
        self.temp_dir = None  # app-owned temp directory for PDF page images; only this is ever deleted
        self.current_page_index = 0
        self.pix_item = None
        self.highlight_items = []
        self.highlights_visible = True
        self.busy = False
        self.ocr_data_cache = {}  # page -> raw Tesseract data
        self.ocr_text = {}  # page -> OCR text filtered at the current confidence threshold
        self.edited_text = {}  # page -> user-edited text (present only if the page was edited)
        self.page_errors = {}  # page -> OCR error message

        # Document / session
        self.source_path = None  # the image or PDF currently open
        self.session_path = None  # .tamilocr file this document was saved to / loaded from
        self.session_dirty = False  # user edits not yet saved to a session
        self.session_cache = {}  # page -> OCR data restored from a session (skips OCR)
        self.pending_page_index = None  # page to show once rendered (restored session position)

        # Store confidence threshold value directly to prevent widget issues
        self.confidence_threshold = 0

        self.ocr_start_time = None

        # Workers
        self.pdf_worker = None
        self.pdf_converting = False
        self.ocr_manager = OCRManager()

        # Progress / page bookkeeping
        self.expected_pages = 0  # pages in the document (known before all are rendered)
        self.converted_pages = 0
        self.ocr_done_pages = 0

        # View state: keep the user's zoom unless they ask to fit
        self.displayed_image_path = None
        self.user_zoomed = False
        self.refit_timer = QTimer(self)
        self.refit_timer.setSingleShot(True)
        self.refit_timer.setInterval(100)
        self.refit_timer.timeout.connect(self.refit_if_not_zoomed)

        # Load the Tamil font BEFORE building the UI, so the editor actually uses it
        self.custom_font_family = "monospace"
        font_path = resource_path(os.path.join("font", "marutham.ttf"))
        if os.path.exists(font_path):
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id != -1:
                self.custom_font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
                logger.info("Custom font '%s' loaded", self.custom_font_family)
            else:
                logger.warning("Failed to load custom font from %s; using monospace", font_path)
        else:
            logger.warning("Custom font file not found at %s; using monospace", font_path)

        self.setup_ui()
        self.update_window_title()

        # Connect OCR manager signals
        self.ocr_manager.page_processed.connect(self.on_page_processed)
        self.ocr_manager.page_failed.connect(self.on_page_failed)
        self.ocr_manager.processing_complete.connect(self.on_processing_complete)
        self.ocr_manager.progress_update.connect(self.on_ocr_progress)

        # Enable drag and drop
        self.setAcceptDrops(True)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Progress bar (initially hidden)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

        self.page_info_label = QLabel("No document loaded")
        self.status_bar.addPermanentWidget(self.page_info_label)

    def setup_ui(self):
        # Central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # Create Menu Bar
        self.menu_bar = self.menuBar()

        # File Menu
        def make_action(text, slot, shortcuts=(), menu=None, **kwargs):
            action = QAction(text, self, **kwargs)
            action.triggered.connect(slot)
            if shortcuts:
                action.setShortcuts([QKeySequence(s) for s in shortcuts])
            if menu is not None:
                menu.addAction(action)
            return action

        file_menu = self.menu_bar.addMenu("&File")
        self.open_action = make_action("&Open Image/PDF/Session…", self.open_file,
                                       [QKeySequence.StandardKey.Open], file_menu)
        file_menu.addSeparator()
        self.save_session_action = make_action("&Save Session", self.save_session,
                                               [QKeySequence.StandardKey.Save], file_menu)
        self.save_session_as_action = make_action("Save Session &As…", self.save_session_as,
                                                  ["Ctrl+Shift+S"], file_menu)
        self.export_action = make_action("&Export Text…", self.export_text, ["Ctrl+E"], file_menu)
        file_menu.addSeparator()
        make_action("E&xit", self.close, ["Ctrl+Q"], file_menu)

        # Edit Menu
        edit_menu = self.menu_bar.addMenu("&Edit")
        self.rerun_action = make_action("&Re-Run OCR", self.rerun_ocr, ["Ctrl+R"], edit_menu)
        self.reset_action = make_action("Reset Page &Text", self.reset_current_text, ["Ctrl+Shift+R"], edit_menu)

        # View Menu
        view_menu = self.menu_bar.addMenu("&View")
        self.prev_page_action = make_action("&Previous Page", self.prev_page,
                                            ["Ctrl+PgUp", "Alt+Left"], view_menu)
        self.next_page_action = make_action("&Next Page", self.next_page,
                                            ["Ctrl+PgDown", "Alt+Right"], view_menu)
        self.first_page_action = make_action("&First Page", self.first_page, ["Ctrl+Home"], view_menu)
        self.last_page_action = make_action("&Last Page", self.last_page, ["Ctrl+End"], view_menu)
        view_menu.addSeparator()
        make_action("Zoom &In", self.zoom_in, [QKeySequence.StandardKey.ZoomIn, "Ctrl+="], view_menu)
        make_action("Zoom &Out", self.zoom_out, [QKeySequence.StandardKey.ZoomOut], view_menu)
        make_action("&Fit to Window", self.fit_view, ["Ctrl+0"], view_menu)
        view_menu.addSeparator()
        self.toggle_highlights_action = make_action("Show &Highlights", self.toggle_highlight_visibility,
                                                    ["Ctrl+H"], view_menu, checkable=True)
        self.toggle_highlights_action.setChecked(True)

        # Help Menu
        help_menu = self.menu_bar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

        # Create Toolbar for controls
        toolbar = QToolBar("Controls")
        self.addToolBar(toolbar)

        # --- Toolbar Widgets ---
        self.rerun_ocr_btn = QPushButton("Re-Run OCR")
        self.rerun_ocr_btn.setToolTip("Re-run OCR on all pages with the selected languages (Ctrl+R)")
        self.rerun_ocr_btn.setEnabled(False)
        self.rerun_ocr_btn.clicked.connect(self.rerun_ocr)
        toolbar.addWidget(self.rerun_ocr_btn)

        self.reset_text_btn = QPushButton("Reset Text")
        self.reset_text_btn.setToolTip("Reset current page text to the original OCR result (Ctrl+Shift+R)")
        self.reset_text_btn.setEnabled(False)
        self.reset_text_btn.clicked.connect(self.reset_current_text)
        toolbar.addWidget(self.reset_text_btn)

        # Add spacing around separator
        toolbar.addWidget(self.create_spacer())
        toolbar.addSeparator()
        toolbar.addWidget(self.create_spacer())

        toolbar.addWidget(QLabel("Confidence:"))
        self.confidence_spinbox = QSpinBox()
        self.confidence_spinbox.setRange(0, 100)
        self.confidence_spinbox.setValue(self.confidence_threshold)
        self.confidence_spinbox.setSuffix("%")
        self.confidence_spinbox.setToolTip("Minimum confidence level for OCR text recognition (0-100%)")
        self.confidence_spinbox.valueChanged.connect(self.on_confidence_changed)
        toolbar.addWidget(self.confidence_spinbox)

        # Add spacing around separator
        toolbar.addWidget(self.create_spacer())
        toolbar.addSeparator()
        toolbar.addWidget(self.create_spacer())

        toolbar.addWidget(QLabel("Langs:"))
        self.lang_picker = LanguagePicker()
        toolbar.addWidget(self.lang_picker)

        # Add spacing around separator
        toolbar.addWidget(self.create_spacer())
        toolbar.addSeparator()
        toolbar.addWidget(self.create_spacer())

        toolbar.addWidget(QLabel("Font Size:"))
        self.font_size_spinbox = QSpinBox()
        self.font_size_spinbox.setRange(8, 36)
        self.font_size_spinbox.setValue(12)
        self.font_size_spinbox.setSuffix(" pt")
        self.font_size_spinbox.setToolTip("Adjust font size of the text editor")
        self.font_size_spinbox.valueChanged.connect(self.on_font_size_changed)
        toolbar.addWidget(self.font_size_spinbox)

        # Spacer to push navigation to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.prev_btn = QPushButton("← Prev Page")
        self.prev_btn.setToolTip("Previous page (Ctrl+PgUp or Alt+←; PgUp in the image view)")
        self.prev_btn.setEnabled(False)
        self.prev_btn.clicked.connect(self.prev_page)
        toolbar.addWidget(self.prev_btn)

        self.next_btn = QPushButton("Next Page →")
        self.next_btn.setToolTip("Next page (Ctrl+PgDown or Alt+→; PgDown in the image view)")
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self.next_page)
        toolbar.addWidget(self.next_btn)


        # --- Main Content Area ---
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setChildrenCollapsible(False)

        self.scene = QGraphicsScene()
        self.image_viewer = ImageViewerWidget(self.scene)
        self.image_viewer.setMinimumSize(400, 300)
        self.graphics_view = self.image_viewer.graphics_view
        content_splitter.addWidget(self.image_viewer)

        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(5)

        text_header = QHBoxLayout()
        text_header.setContentsMargins(5, 0, 5, 0)
        self.text_label = QLabel("OCR Result (Editable for Proofreading)")
        self.text_label.setStyleSheet("font-weight: bold;")
        self.edit_status_label = QLabel()
        self.edit_status_label.setStyleSheet("color: #666; font-style: italic;")
        text_header.addWidget(self.text_label)
        text_header.addStretch()
        text_header.addWidget(self.edit_status_label)
        text_layout.addLayout(text_header)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("OCR output will appear here...\nYou can edit this text for proofreading purposes.")
        self.text_edit.setReadOnly(False)
        self.text_edit.textChanged.connect(self.on_text_edited)
        font = self.text_edit.font()
        font.setFamily(self.custom_font_family)
        font.setPointSize(self.font_size_spinbox.value())
        self.text_edit.setFont(font)
        text_layout.addWidget(self.text_edit)
        content_splitter.addWidget(text_widget)

        content_splitter.setSizes([720, 480])
        main_layout.addWidget(content_splitter, 1)

        # Connect signals for non-menu/toolbar items
        self.image_viewer.zoom_in_btn.clicked.connect(self.zoom_in)
        self.image_viewer.zoom_out_btn.clicked.connect(self.zoom_out)
        self.image_viewer.fit_btn.clicked.connect(self.fit_view)
        self.graphics_view.user_zoomed.connect(self.on_user_zoomed)
        self.graphics_view.page_up_requested.connect(self.prev_page)
        self.graphics_view.page_down_requested.connect(self.next_page)
        self.image_viewer.resized.connect(self.refit_timer.start)

    def create_spacer(self, width=5):
        spacer = QWidget()
        spacer.setFixedWidth(width)
        return spacer

    def show_about_dialog(self):
        dialog = AboutDialog(self)
        dialog.exec()

    def on_font_size_changed(self, size):
        """Update the font size of the text editor"""
        try:
            font = self.text_edit.font()
            font.setPointSize(size)
            self.text_edit.setFont(font)
        except Exception:
            logger.exception("Error setting font size")

    # --- Text editor state -------------------------------------------------

    def set_editor_text(self, text):
        """Set editor text programmatically without it counting as a user edit."""
        self.text_edit.blockSignals(True)
        try:
            self.text_edit.setPlainText(text)
        finally:
            self.text_edit.blockSignals(False)

    def is_page_modified(self, page_index):
        return page_index in self.edited_text

    def page_text(self, page_index):
        """Final text for a page: the user's edit if any, otherwise the OCR text."""
        if page_index in self.edited_text:
            return self.edited_text[page_index]
        return self.ocr_text.get(page_index, "")

    def on_text_edited(self):
        """Called only for real user edits (programmatic updates block signals)."""
        try:
            if self.temp_pages:
                self.edited_text[self.current_page_index] = self.text_edit.toPlainText()
                self.mark_dirty()
                self.update_edit_status()
                self.reset_text_btn.setEnabled(not self.busy)
                self.reset_action.setEnabled(not self.busy)
        except Exception:
            logger.exception("Error in on_text_edited")

    def update_edit_status(self):
        """Update the editing status label"""
        try:
            if self.is_page_modified(self.current_page_index):
                self.edit_status_label.setText("✏️ Modified")
                self.edit_status_label.setStyleSheet("color: #e67e22; font-style: italic; font-weight: bold;")
            elif self.current_page_index in self.ocr_text:
                self.edit_status_label.setText("📄 Original")
                self.edit_status_label.setStyleSheet("color: #27ae60; font-style: italic;")
            else:
                self.edit_status_label.setText("")
        except Exception:
            logger.exception("Error in update_edit_status")

    def reset_current_text(self):
        """Discard edits and restore the original OCR result for the current page"""
        try:
            if self.edited_text.pop(self.current_page_index, None) is not None:
                self.mark_dirty()
            self.set_editor_text(self.ocr_text.get(self.current_page_index, ""))
            self.update_edit_status()
            self.reset_text_btn.setEnabled(False)
            self.reset_action.setEnabled(False)
        except Exception:
            logger.exception("Error in reset_current_text")

    def on_confidence_changed(self, value):
        """Re-filter OCR text and highlights in real time; user edits are left untouched"""
        try:
            self.confidence_threshold = value

            # Re-filter every processed page so export stays consistent with the threshold
            for page_index, data in self.ocr_data_cache.items():
                self.ocr_text[page_index] = '\n'.join(extract_text_lines(data, value))

            if self.current_page_index in self.ocr_data_cache:
                self.update_current_page_highlights()
        except Exception:
            logger.exception("Error in on_confidence_changed")

    def update_current_page_highlights(self):
        """Update highlights and text on current page with current confidence threshold"""
        try:
            if not self.temp_pages or self.current_page_index >= len(self.temp_pages):
                return

            # Remove existing highlights
            for item in self.highlight_items:
                self.scene.removeItem(item)
            self.highlight_items = []

            if self.current_page_index in self.ocr_data_cache:
                self.add_bounding_boxes(self.ocr_data_cache[self.current_page_index], self.confidence_threshold)

            # Only update text if it hasn't been manually edited
            if not self.is_page_modified(self.current_page_index):
                self.set_editor_text(self.ocr_text.get(self.current_page_index, ""))
        except Exception:
            logger.exception("Error in update_current_page_highlights")

    def update_page_info(self):
        if self.temp_pages:
            total_pages = max(self.expected_pages, len(self.temp_pages))
            text = f"Page {self.current_page_index + 1} of {total_pages}"
            if self.pdf_converting and len(self.temp_pages) < total_pages:
                text += f" ({len(self.temp_pages)} rendered)"
            self.page_info_label.setText(text)
        else:
            self.page_info_label.setText("No document loaded")

    # --- Lifecycle / file handling ------------------------------------------

    def stop_workers(self):
        """Stop PDF conversion and OCR; late results from either are discarded."""
        if self.pdf_worker is not None:
            # Disconnect first so a conversion that outlives wait() can't deliver stale pages
            for sig in (self.pdf_worker.document_info,
                        self.pdf_worker.page_converted,
                        self.pdf_worker.conversion_finished,
                        self.pdf_worker.error_occurred):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            if self.pdf_worker.isRunning():
                self.pdf_worker.stop()
                self.pdf_worker.wait(3000)
            self.pdf_worker = None
        self.pdf_converting = False

        self.ocr_manager.stop_all()

    def closeEvent(self, event):
        if not self.confirm_discard_changes():
            event.ignore()
            return
        try:
            self.stop_workers()
            self.clear_temp_pages()
        except Exception:
            logger.exception("Error in closeEvent")
        super().closeEvent(event)

    def dragEnterEvent(self, event):
        try:
            if event.mimeData().hasUrls() and not self.busy:
                event.acceptProposedAction()
        except Exception:
            logger.exception("Error in dragEnterEvent")

    def dropEvent(self, event):
        try:
            if self.busy:
                return
            urls = event.mimeData().urls()
            if urls:
                # Only one document is open at a time
                self.open_path(urls[0].toLocalFile())
        except Exception:
            logger.exception("Error in dropEvent")

    def open_file(self):
        try:
            if self.busy:
                return
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Open File", "",
                "Images, PDF or session (*.png *.jpg *.jpeg *.tif *.tiff *.pdf *{SESSION_EXTENSION});;"
                "Images/PDF (*.png *.jpg *.jpeg *.tif *.tiff *.pdf);;"
                f"Tamil OCR session (*{SESSION_EXTENSION})"
            )
            if file_path:
                self.open_path(file_path)
        except Exception:
            logger.exception("Error in open_file")

    def open_path(self, file_path):
        """User-initiated open of a document or session (asks about unsaved edits first)."""
        if not file_path or self.busy or not self.confirm_discard_changes():
            return
        if file_path.lower().endswith(SESSION_EXTENSION):
            self.load_session(file_path)
        else:
            self.process_file(file_path)

    def process_file(self, file_path, session=None):
        """Open an image or PDF. With `session`, restore its OCR results and edits
        instead of re-running OCR on those pages."""
        try:
            if not file_path:
                return

            self.stop_workers()

            # Clear previous state
            self.clear_temp_pages()
            self.scene.clear()
            self.displayed_image_path = None
            self.set_editor_text("")
            self.current_page_index = 0
            self.pix_item = None
            self.highlight_items = []
            self.ocr_data_cache = {}
            self.ocr_text = {}
            self.edited_text = {}
            self.page_errors = {}
            self.expected_pages = 0
            self.converted_pages = 0
            self.ocr_done_pages = 0
            self.source_path = os.path.abspath(file_path)
            self.session_cache = {}
            self.pending_page_index = None
            self.session_dirty = False
            if session is None:
                self.session_path = None
            else:
                for i, page in enumerate(session.get('pages', [])):
                    if not isinstance(page, dict):
                        continue
                    if page.get('ocr_data'):
                        self.session_cache[i] = page['ocr_data']
                    if page.get('edited_text') is not None:
                        self.edited_text[i] = page['edited_text']
                saved_page = session.get('current_page', 0)
                if isinstance(saved_page, int) and saved_page > 0:
                    self.pending_page_index = saved_page
            self.update_edit_status()
            self.update_window_title()

            # Show progress bar and disable UI
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            self.set_ui_enabled(False)

            logger.info("Opening %s", file_path)
            if file_path.lower().endswith('.pdf'):
                # Render pages in a separate thread into an app-owned temp dir;
                # OCR starts on each page as soon as it is rendered.
                self.status_bar.showMessage("Loading PDF...")
                self.temp_dir = tempfile.mkdtemp(prefix="tamil_ocr_")
                self.pdf_converting = True
                self.pdf_worker = PDFConversionWorker(file_path, self.temp_dir)
                self.pdf_worker.document_info.connect(self.on_pdf_info)
                self.pdf_worker.page_converted.connect(self.on_pdf_page_converted)
                self.pdf_worker.conversion_finished.connect(self.on_pdf_finished)
                self.pdf_worker.error_occurred.connect(self.on_pdf_error)
                self.pdf_worker.start()
            else:
                # Single image file - used in place, never deleted
                self.temp_pages = [file_path]
                self.expected_pages = self.converted_pages = 1
                self.update_nav_buttons()
                self.update_page_info()
                self.display_current_page_with_cache()
                self.start_ocr_job(1)
                self.queue_page(0, file_path)
        except Exception as e:
            logger.exception("Error in process_file")
            self.on_processing_error(str(e))

    def on_pdf_info(self, total_pages):
        if total_pages <= 0:
            self.stop_workers()
            self.on_processing_error("No pages found in PDF")
            return
        self.expected_pages = total_pages
        self.update_page_info()
        self.start_ocr_job(total_pages)

    def on_pdf_page_converted(self, page_index, image_path):
        try:
            self.temp_pages.append(image_path)
            self.converted_pages += 1
            if page_index == self.pending_page_index:
                # Restored session: return to the page the user was on
                self.current_page_index = page_index
                self.pending_page_index = None
            self.queue_page(page_index, image_path)
            if page_index == self.current_page_index:
                self.display_current_page_with_cache()
            self.update_nav_buttons()
            self.update_page_info()
            self.update_progress()
        except Exception:
            logger.exception("Error in on_pdf_page_converted")

    def on_pdf_finished(self, converted):
        self.pdf_converting = False
        self.update_page_info()

    def on_pdf_error(self, converted, message):
        self.pdf_converting = False
        if converted == 0:
            self.stop_workers()
            self.on_processing_error(f"PDF conversion error: {message}")
            QMessageBox.warning(self, "Could not open PDF", f"The PDF could not be converted:\n\n{message}")
            return
        # Keep the pages that did render; finish OCR on those
        self.expected_pages = converted
        self.ocr_manager.set_total(converted)
        self.update_page_info()
        QMessageBox.warning(
            self, "PDF partly converted",
            f"Only the first {converted} page(s) could be converted:\n\n{message}")

    def queue_page(self, page_index, image_path):
        """OCR a page, or reuse its result if it was restored from a session."""
        if page_index in self.session_cache:
            self.ocr_manager.add_cached_result(page_index, self.session_cache.pop(page_index))
        else:
            self.ocr_manager.add_page(page_index, image_path)

    def current_lang_string(self):
        return self.lang_picker.lang_string()

    def start_ocr_job(self, total_pages):
        """Start an OCR job; pages are then added with ocr_manager.add_page()."""
        self.ocr_start_time = time.time()
        self.ocr_done_pages = 0
        self.ocr_manager.start_job(total_pages, self.confidence_threshold, self.current_lang_string())

    def rerun_ocr(self):
        """Re-run OCR on all pages (discards edits after confirmation)"""
        try:
            if not self.temp_pages or self.busy:
                return

            if self.edited_text:
                answer = QMessageBox.question(
                    self, "Re-Run OCR",
                    f"{len(self.edited_text)} page(s) have been edited. "
                    "Re-running OCR will discard these edits. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

            self.ocr_manager.stop_all()

            # Clear cached data
            if self.edited_text:
                self.mark_dirty()  # discarded edits differ from any saved session
            self.ocr_data_cache = {}
            self.ocr_text = {}
            self.edited_text = {}
            self.page_errors = {}
            self.session_cache = {}

            # Show progress and disable UI
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            self.set_ui_enabled(False)

            # Clear current highlights and text
            for item in self.highlight_items:
                self.scene.removeItem(item)
            self.highlight_items = []
            self.set_editor_text("Re-running OCR...")
            self.update_edit_status()

            self.start_ocr_job(len(self.temp_pages))
            for i, path in enumerate(self.temp_pages):
                self.ocr_manager.add_page(i, path)
        except Exception:
            logger.exception("Error in rerun_ocr")

    def on_ocr_progress(self, done, total):
        self.ocr_done_pages = done
        self.update_progress()

    def update_progress(self):
        """One progress bar for rendering + OCR (rendering only counts while a PDF is converting)."""
        total = max(self.expected_pages, 1)
        if self.pdf_converting:
            value = (self.converted_pages + self.ocr_done_pages) * 100 // (2 * total)
            message = f"Rendering page {self.converted_pages}/{total} · OCR {self.ocr_done_pages}/{total}"
        else:
            # Rendering is done (or not needed); scale OCR into whatever remains of the bar
            base = 50 if self.temp_dir else 0
            value = base + self.ocr_done_pages * (100 - base) // total
            message = f"OCR {self.ocr_done_pages}/{total} pages"
        self.progress_bar.setValue(min(value, 100))
        self.status_bar.showMessage(message)

    def on_page_processed(self, page_index, text, ocr_data):
        try:
            self.ocr_data_cache[page_index] = ocr_data
            # Re-filter in case the threshold changed while this page was processing
            self.ocr_text[page_index] = '\n'.join(extract_text_lines(ocr_data, self.confidence_threshold))

            if page_index == self.current_page_index:
                self.display_current_page_with_cache()
        except Exception:
            logger.exception("Error in on_page_processed")

    def on_page_failed(self, page_index, message):
        try:
            logger.error("OCR failed on page %d: %s", page_index + 1, message)
            self.page_errors[page_index] = message
            if page_index == self.current_page_index:
                self.display_current_page_with_cache()
        except Exception:
            logger.exception("Error in on_page_failed")

    def on_processing_complete(self, failed_pages):
        try:
            self.set_ui_enabled(True)
            self.progress_bar.setVisible(False)

            message = "OCR processing complete"
            if self.ocr_start_time:
                elapsed_time = time.time() - self.ocr_start_time
                message += f" (Time taken: {elapsed_time:.2f} seconds)"
                self.ocr_start_time = None
            if failed_pages:
                message += f" — {failed_pages} page(s) failed"
            self.status_bar.showMessage(message)
            logger.info(message)

            self.display_current_page_with_cache()

            if failed_pages and failed_pages == len(self.temp_pages):
                # Every page failing usually means a setup problem (language, Tesseract)
                first_error = self.page_errors[min(self.page_errors)]
                QMessageBox.warning(
                    self, "OCR failed",
                    f"OCR failed on every page. Check the language codes "
                    f"(currently '{self.current_lang_string()}').\n\n{first_error}")
        except Exception:
            logger.exception("Error in on_processing_complete")

    def on_processing_error(self, error_message):
        try:
            logger.error(error_message)
            self.set_ui_enabled(True)
            self.progress_bar.setVisible(False)
            self.status_bar.showMessage(f"Error: {error_message}")
        except Exception:
            logger.exception("Error in on_processing_error")

    def set_ui_enabled(self, enabled):
        """Enable/disable controls that must not be used while processing"""
        self.busy = not enabled
        has_pages = bool(self.temp_pages)
        self.open_action.setEnabled(enabled)
        self.export_action.setEnabled(enabled and has_pages)
        self.save_session_action.setEnabled(enabled and has_pages)
        self.save_session_as_action.setEnabled(enabled and has_pages)
        self.rerun_ocr_btn.setEnabled(enabled and has_pages)
        self.rerun_action.setEnabled(enabled and has_pages)
        can_reset = enabled and self.is_page_modified(self.current_page_index)
        self.reset_text_btn.setEnabled(can_reset)
        self.reset_action.setEnabled(can_reset)
        self.confidence_spinbox.setEnabled(enabled)
        self.lang_picker.setEnabled(enabled)

    # --- Page display ---------------------------------------------------------

    def display_image_only(self, image_path):
        """Show a page image. Re-showing the same image keeps the user's zoom/scroll
        and only clears highlights (e.g. when its OCR result arrives)."""
        try:
            if image_path == self.displayed_image_path and self.pix_item is not None:
                for item in self.highlight_items:
                    self.scene.removeItem(item)
                self.highlight_items = []
                return

            self.scene.clear()
            self.highlight_items = []

            pixmap = QPixmap(image_path)
            self.pix_item = QGraphicsPixmapItem(pixmap)
            self.pix_item.setZValue(0)
            self.scene.addItem(self.pix_item)
            self.scene.setSceneRect(self.pix_item.boundingRect())
            self.displayed_image_path = image_path

            # A new page starts fitted to the view
            self.fit_view()
        except Exception:
            logger.exception("Error in display_image_only")

    def display_current_page_with_cache(self):
        """Display current page with cached OCR data if available"""
        try:
            if not self.temp_pages or self.current_page_index >= len(self.temp_pages):
                return

            idx = self.current_page_index
            self.display_image_only(self.temp_pages[idx])

            if idx in self.ocr_data_cache:
                self.add_bounding_boxes(self.ocr_data_cache[idx], self.confidence_threshold)

            if self.is_page_modified(idx) or idx in self.ocr_text:
                self.set_editor_text(self.page_text(idx))
            elif idx in self.page_errors:
                self.set_editor_text(f"OCR failed for this page:\n{self.page_errors[idx]}")
            else:
                self.set_editor_text("Processing OCR...")

            self.update_edit_status()
            self.reset_text_btn.setEnabled(not self.busy and self.is_page_modified(idx))
            self.reset_action.setEnabled(not self.busy and self.is_page_modified(idx))
            self.update_nav_buttons()
            self.update_page_info()
        except Exception:
            logger.exception("Error in display_current_page_with_cache")

    def add_bounding_boxes(self, data, confidence_threshold):
        """Add bounding boxes from OCR data with confidence filtering"""
        try:
            pen = QPen(QColor(255, 0, 0))  # red outline
            pen.setWidth(2)
            brush = QBrush(QColor(255, 255, 0, 60))  # semi-transparent yellow fill

            n_boxes = len(data.get('text', []))
            for i in range(n_boxes):
                try:
                    conf = float(data['conf'][i])
                except (TypeError, ValueError):
                    conf = -1.0
                word = (data['text'][i] or '').strip()

                if conf > confidence_threshold and word:
                    x = int(data['left'][i])
                    y = int(data['top'][i])
                    w = int(data['width'][i])
                    h = int(data['height'][i])
                    rect = QGraphicsRectItem(QRectF(x, y, w, h))
                    rect.setPen(pen)
                    rect.setBrush(brush)
                    rect.setZValue(1)
                    rect.setToolTip(f"{word} (conf: {conf:.0f}%)")
                    rect.setVisible(self.highlights_visible)
                    self.scene.addItem(rect)
                    self.highlight_items.append(rect)
        except Exception:
            logger.exception("Error in add_bounding_boxes")

    def fit_view(self):
        """Fit the page to the view (Fit button, new page); clears the user-zoom state."""
        try:
            self.user_zoomed = False
            if self.pix_item is not None:
                self.graphics_view.resetTransform()
                self.graphics_view.fitInView(self.pix_item, Qt.AspectRatioMode.KeepAspectRatio)
        except Exception:
            logger.exception("Error in fit_view")

    def refit_if_not_zoomed(self):
        """After a resize, keep the page fitted unless the user has zoomed in/out."""
        if not self.user_zoomed:
            self.fit_view()

    def on_user_zoomed(self):
        self.user_zoomed = True

    def zoom_in(self):
        self.scale_view(1.25)

    def zoom_out(self):
        self.scale_view(0.8)

    def scale_view(self, factor):
        self.user_zoomed = True
        self.graphics_view.scale(factor, factor)

    def toggle_highlight_visibility(self):
        self.highlights_visible = self.toggle_highlights_action.isChecked()
        for rect in self.highlight_items:
            rect.setVisible(self.highlights_visible)

    def update_nav_buttons(self):
        """Prev/Next are enabled only when there is a page to go to (pages may still be rendering)."""
        has_prev = self.current_page_index > 0
        has_next = self.current_page_index < len(self.temp_pages) - 1
        self.prev_btn.setEnabled(has_prev)
        self.next_btn.setEnabled(has_next)
        self.prev_page_action.setEnabled(has_prev)
        self.first_page_action.setEnabled(has_prev)
        self.next_page_action.setEnabled(has_next)
        self.last_page_action.setEnabled(has_next)

    def prev_page(self):
        # Edits are stored as they happen (on_text_edited), so nothing to save here
        if self.temp_pages and self.current_page_index > 0:
            self.current_page_index -= 1
            self.display_current_page_with_cache()

    def next_page(self):
        if self.temp_pages and self.current_page_index < len(self.temp_pages) - 1:
            self.current_page_index += 1
            self.display_current_page_with_cache()

    def first_page(self):
        if self.temp_pages and self.current_page_index != 0:
            self.current_page_index = 0
            self.display_current_page_with_cache()

    def last_page(self):
        if self.temp_pages and self.current_page_index != len(self.temp_pages) - 1:
            self.current_page_index = len(self.temp_pages) - 1
            self.display_current_page_with_cache()

    # --- Sessions -------------------------------------------------------------

    def update_window_title(self):
        if self.session_path:
            name = os.path.basename(self.session_path)
        elif self.source_path:
            name = os.path.basename(self.source_path)
        else:
            self.setWindowTitle(APP_NAME)
            return
        self.setWindowTitle(f"{name}{' *' if self.session_dirty else ''} — {APP_NAME}")

    def mark_dirty(self):
        if not self.session_dirty:
            self.session_dirty = True
            self.update_window_title()

    def confirm_discard_changes(self):
        """Before closing or replacing the document: offer to save unsaved edits.
        Returns False if the user cancels."""
        if not (self.session_dirty and self.temp_pages):
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved changes")
        box.setText("This document has proofreading edits that are not saved.")
        box.setInformativeText("Save them as a session before continuing?")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            return self.save_session()
        return answer == QMessageBox.StandardButton.Discard

    def save_session(self):
        """Save to the current session file, or ask where (Ctrl+S). Returns True if saved."""
        if self.session_path:
            return self.write_session(self.session_path)
        return self.save_session_as()

    def save_session_as(self):
        if self.busy or not self.temp_pages:
            return False
        if self.session_path:
            suggested = self.session_path
        else:
            suggested = os.path.splitext(self.source_path or "session")[0] + SESSION_EXTENSION
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Session", suggested, f"Tamil OCR session (*{SESSION_EXTENSION})")
        if not path:
            return False
        if not path.lower().endswith(SESSION_EXTENSION):
            path += SESSION_EXTENSION
        return self.write_session(path)

    def build_session(self, session_path):
        session_dir = os.path.dirname(os.path.abspath(session_path))
        try:
            relpath = os.path.relpath(self.source_path, session_dir)
        except ValueError:  # different drive on Windows
            relpath = None
        return {
            'format': SESSION_FORMAT,
            'app_version': APP_VERSION,
            'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'source_path': self.source_path,
            'source_relpath': relpath,
            'source_sha256': file_sha256(self.source_path),
            'languages': self.lang_picker.languages(),
            'confidence_threshold': self.confidence_threshold,
            'current_page': self.current_page_index,
            'page_count': len(self.temp_pages),
            'pages': [
                {
                    'ocr_data': compact_ocr_data(self.ocr_data_cache[i]) if i in self.ocr_data_cache else None,
                    'edited_text': self.edited_text.get(i),
                }
                for i in range(len(self.temp_pages))
            ],
        }

    def write_session(self, session_path):
        if self.busy or not self.temp_pages or not self.source_path:
            return False
        try:
            write_session_file(session_path, self.build_session(session_path))
        except (OSError, TypeError, ValueError) as e:
            logger.exception("Error saving session")
            QMessageBox.critical(self, "Save failed", f"Could not save the session to\n{session_path}:\n\n{e}")
            return False
        self.session_path = session_path
        self.session_dirty = False
        self.update_window_title()
        self.status_bar.showMessage(f"Session saved to {session_path}")
        logger.info("Session saved to %s", session_path)
        return True

    def load_session(self, session_path):
        try:
            session = read_session_file(session_path)
        except (OSError, ValueError) as e:
            logger.exception("Error reading session %s", session_path)
            QMessageBox.warning(self, "Cannot open session", f"{session_path}\n\n{e}")
            return False

        source = resolve_session_source(session_path, session)
        if source is None:
            QMessageBox.information(
                self, "Locate document",
                f"The document for this session was not found:\n{session.get('source_path')}\n\n"
                "Please locate it.")
            source, _ = QFileDialog.getOpenFileName(
                self, "Locate Document", os.path.dirname(session_path),
                "Images/PDF (*.png *.jpg *.jpeg *.tif *.tiff *.pdf)")
            if not source:
                return False

        try:
            source_changed = file_sha256(source) != session.get('source_sha256')
        except OSError as e:
            QMessageBox.warning(self, "Cannot open document", f"{source}\n\n{e}")
            return False
        if source_changed:
            answer = QMessageBox.question(
                self, "Document changed",
                "The document has changed since this session was saved, so the saved OCR "
                "results and edits may not match its pages.\n\nOpen the session anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False

        missing = self.lang_picker.set_languages(session.get('languages') or [])
        if missing:
            logger.warning("Session languages not installed: %s", missing)
        threshold = session.get('confidence_threshold', 0)
        if isinstance(threshold, int) and 0 <= threshold <= 100:
            self.confidence_spinbox.setValue(threshold)

        logger.info("Loading session %s (document %s)", session_path, source)
        self.process_file(source, session=session)
        self.session_path = session_path
        self.update_window_title()
        return True

    def clear_temp_pages(self):
        """Delete only the app-owned temp directory; user files are never removed."""
        if self.temp_dir:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None
        self.temp_pages = []

    def export_text(self):
        """Export every page's final text (user edits take precedence over OCR output)"""
        if self.busy:
            return

        all_text = []
        for i in range(len(self.temp_pages)):
            all_text.append(f"=== Page {i + 1} ===\n{self.page_text(i)}\n")
        text = '\n'.join(all_text)

        if text.strip():
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save File", "output.txt", "Text Files (*.txt)"
            )
            if file_path:
                try:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(text)

                    self.status_bar.showMessage(f"Text exported to {file_path}")
                except (IOError, OSError) as e:
                    logger.exception("Error exporting text")
                    self.status_bar.showMessage(f"Error exporting text: {e}")
                    QMessageBox.critical(self, "Export failed", f"Could not save {file_path}:\n\n{e}")


def install_exception_hook(log_file):
    """Log uncaught exceptions and tell the user, instead of PyQt6 aborting the app."""
    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None, "Unexpected error",
                f"An unexpected error occurred:\n\n{exc_value}\n\nDetails were saved to:\n{log_file}")
    sys.excepthook = hook


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName("Tamil-OCR")
    log_file = setup_logging()
    install_exception_hook(log_file)
    logger.info("Starting Tamil OCR %s (log file: %s)", APP_VERSION, log_file)
    window = OCRApp()
    window.show()
    # "Tamil-OCR <file>" opens an image, PDF or session at startup (e.g. "Open with…")
    startup_files = [a for a in app.arguments()[1:] if not a.startswith('-')]
    if startup_files:
        QTimer.singleShot(0, lambda: window.open_path(os.path.abspath(startup_files[0])))
    sys.exit(app.exec())
