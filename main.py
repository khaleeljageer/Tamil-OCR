import logging
import os
import platform
import pytesseract
import shutil
import sys
import tempfile
import time


# Helper function to get resource paths for PyInstaller
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception as e:
        print(f"Error in resource_path: {e}")
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


from PyQt6.QtCore import Qt, QRectF, QThread, pyqtSignal, QTimer, QThreadPool, QRunnable, QUrl, QObject
from PyQt6.QtGui import QPixmap, QPen, QColor, QBrush, QPainter, QFontDatabase, QDesktopServices, QAction, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QLabel, QPushButton, QFileDialog, QTextEdit, QHBoxLayout, QSplitter,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
    QCheckBox, QProgressBar, QStatusBar, QSpinBox, QFrame, QLineEdit, QDialog, QToolBar, QSizePolicy,
    QMessageBox
)
from pdf2image import convert_from_path
from PIL import Image

# Increase PIL's image size limit to handle large PDF pages
Image.MAX_IMAGE_PIXELS = None  # Remove the limit entirely
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
    """Group Tesseract words into lines, keeping only words above the confidence threshold."""
    text_lines = []
    current_line = []
    last_block_num = last_par_num = last_line_num = -1

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

            if (block_num != last_block_num) or (par_num != last_par_num) or (line_num != last_line_num):
                if current_line:
                    text_lines.append(' '.join(current_line))
                    current_line = []
                last_block_num, last_par_num, last_line_num = block_num, par_num, line_num

            current_line.append(word)

    if current_line:
        text_lines.append(' '.join(current_line))

    return text_lines


class PDFConversionWorker(QThread):
    """Separate worker for PDF to image conversion to prevent UI freeze"""
    pages_converted = pyqtSignal(list)
    conversion_progress = pyqtSignal(int, str)
    error_occurred = pyqtSignal(str)

    def __init__(self, pdf_path, output_dir):
        super().__init__()
        self.pdf_path = pdf_path
        # App-owned temp directory; every page image is written here so cleanup
        # never touches files the user opened.
        self.output_dir = output_dir
        self.should_stop = False

    def run(self):
        try:
            self.conversion_progress.emit(10, "Converting PDF to images...")

            try:
                pages = convert_from_path(self.pdf_path, dpi=300, poppler_path=POPPLER_PATH)
            except Exception as error:
                print(f"Error converting PDF with standard DPI: {error}")
                if "exceeds limit" in str(error) or "decompression bomb" in str(error):
                    # If size limit exceeded, try with lower DPI
                    self.conversion_progress.emit(15, "Large PDF detected, using lower DPI...")
                    pages = convert_from_path(self.pdf_path, dpi=150, poppler_path=POPPLER_PATH)
                else:
                    raise error

            total_pages = len(pages)

            temp_paths = []
            for i, page in enumerate(pages):
                if self.should_stop:
                    # Clean up any created files
                    for path in temp_paths:
                        try:
                            os.remove(path)
                        except Exception as error:
                            print(f"Error removing temp file: {error}")
                            pass
                    return

                tmp_path = os.path.join(self.output_dir, f"page_{i + 1:04d}.png")

                # Resize image if it's too large for OCR processing
                width, height = page.size
                max_dimension = 4000  # Maximum width or height

                if width > max_dimension or height > max_dimension:
                    # Calculate new dimensions maintaining aspect ratio
                    if width > height:
                        new_width = max_dimension
                        new_height = int((height * max_dimension) / width)
                    else:
                        new_height = max_dimension
                        new_width = int((width * max_dimension) / height)

                    page = page.resize((new_width, new_height), Image.Resampling.LANCZOS)

                page.save(tmp_path, 'PNG')
                temp_paths.append(tmp_path)

                # Update progress
                progress = 10 + int((i + 1) / total_pages * 30)
                self.conversion_progress.emit(progress, f"Converting page {i + 1}/{total_pages}...")

            self.pages_converted.emit(temp_paths)

        except Exception as error:
            print(f"PDF conversion error: {error}")
            self.error_occurred.emit(f"PDF conversion error: {str(error)}")

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
                print(f"Pytesseract error on page {self.page_index + 1}: {e}")
                self.signals.page_failed.emit(self.job_id, self.page_index, f"Pytesseract error: {e}")
                return

            # Extract text with confidence filtering
            text = '\n'.join(extract_text_lines(ocr_data, self.confidence_threshold))

            # Emit results
            self.signals.page_processed.emit(self.job_id, self.page_index, text, ocr_data)

        except Exception as e:
            print(f"OCR error on page {self.page_index + 1}: {e}")
            self.signals.page_failed.emit(self.job_id, self.page_index, f"OCR error: {e}")


class OCRSignals(QObject):
    """Signals emitted from worker threads; every signal carries the job ID it belongs to."""
    page_processed = pyqtSignal(int, int, str, object)  # job_id, page_index, text, ocr_data
    page_failed = pyqtSignal(int, int, str)  # job_id, page_index, message


class OCRManager(QObject):
    """Manages parallel OCR processing.

    Each run gets a new job ID. Results from tasks belonging to an older job
    (e.g. still running after a new file was opened) are dropped here, so the
    UI only ever sees results for the current job.
    """
    page_processed = pyqtSignal(int, str, object)  # page_index, text, ocr_data
    page_failed = pyqtSignal(int, str)  # page_index, message
    processing_complete = pyqtSignal(int)  # number of failed pages
    progress_update = pyqtSignal(int, str)

    def __init__(self):
        super().__init__()
        self.signals = OCRSignals()
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(min(4, (os.cpu_count()) or 2))
        self.job_id = 0
        self.total_pages = 0
        self.completed_pages = 0
        self.failed_pages = 0

        # Connect once; start_processing() may run many times
        self.signals.page_processed.connect(self._on_task_processed)
        self.signals.page_failed.connect(self._on_task_failed)

    def start_processing(self, image_paths, confidence_threshold, lang_string):
        self.job_id += 1
        self.total_pages = len(image_paths)
        self.completed_pages = 0
        self.failed_pages = 0

        self.progress_update.emit(40, "Starting OCR processing...")

        # Create and queue OCR tasks
        for i, image_path in enumerate(image_paths):
            task = OCRTask(self.job_id, i, image_path, confidence_threshold, lang_string, self.signals)
            self.thread_pool.start(task)

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
        progress = 40 + int((self.completed_pages / self.total_pages) * 60)  # 40-100%
        self.progress_update.emit(progress, f"Processed page {self.completed_pages}/{self.total_pages}")

        if self.completed_pages >= self.total_pages:
            self.processing_complete.emit(self.failed_pages)

    def stop_all(self):
        # Invalidate the current job so results from still-running tasks are ignored
        self.job_id += 1
        self.thread_pool.clear()
        self.thread_pool.waitForDone(3000)


class ZoomableGraphicsView(QGraphicsView):
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

    def wheelEvent(self, event):
        # Check if Ctrl key is pressed for zoom
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Get the angle delta (positive for up, negative for down)
            angle_delta = event.angleDelta().y()

            # Calculate zoom factor
            zoom_factor = 1.25 if angle_delta > 0 else 0.8

            # Apply zoom
            self.scale(zoom_factor, zoom_factor)

            # Accept the event so it doesn't propagate
            event.accept()
        else:
            # Let the default scroll behavior handle it
            super().wheelEvent(event)


class ImageViewerWidget(QWidget):
    """Custom widget containing the graphics view with floating zoom controls"""

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
        version_label = QLabel("<b>Version:</b> 1.0.0")
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
            Pillow (<a href="https://github.com/python-pillow/Pillow/blob/master/LICENSE">HPX License</a>).<br><br>
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
        self.setWindowTitle("Tamil OCR Desktop App")
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

        # Store confidence threshold value directly to prevent widget issues
        self.confidence_threshold = 0

        self.ocr_start_time = None

        # Workers
        self.pdf_worker = None
        self.ocr_manager = OCRManager()

        # Ensure custom_font_family is always initialized
        self.custom_font_family = "monospace"

        # Setup UI
        self.setup_ui()

        # Connect OCR manager signals
        self.ocr_manager.page_processed.connect(self.on_page_processed)
        self.ocr_manager.page_failed.connect(self.on_page_failed)
        self.ocr_manager.processing_complete.connect(self.on_processing_complete)
        self.ocr_manager.progress_update.connect(self.on_progress_update)

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
        
        self.scale_factor = 1.0

        # Load custom font
        font_path = resource_path(os.path.join("font", "marutham.ttf"))
        if os.path.exists(font_path):
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id != -1:
                self.custom_font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
                print(f"Custom font '{self.custom_font_family}' loaded successfully.")
            else:
                self.custom_font_family = "monospace"
                print(f"Failed to load custom font from {font_path}. Using monospace.")
        else:
            self.custom_font_family = "monospace"
            print(f"Custom font file not found at {font_path}. Using monospace.")


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
        file_menu = self.menu_bar.addMenu("&File")
        self.open_action = QAction("&Open Image/PDF", self)
        self.open_action.triggered.connect(self.open_file)
        self.export_action = QAction("&Export Text", self)
        self.export_action.triggered.connect(self.export_text)
        exit_action = QAction("&Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(self.open_action)
        file_menu.addAction(self.export_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        # View Menu
        view_menu = self.menu_bar.addMenu("&View")
        self.toggle_highlights_action = QAction("Show &Highlights", self, checkable=True)
        self.toggle_highlights_action.setChecked(True)
        self.toggle_highlights_action.triggered.connect(self.toggle_highlight_visibility)
        view_menu.addAction(self.toggle_highlights_action)

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
        self.rerun_ocr_btn.setToolTip("Re-Run OCR with current confidence threshold")
        self.rerun_ocr_btn.setEnabled(False)
        self.rerun_ocr_btn.clicked.connect(self.rerun_ocr)
        toolbar.addWidget(self.rerun_ocr_btn)

        self.reset_text_btn = QPushButton("Reset Text")
        self.reset_text_btn.setToolTip("Reset current page text to original OCR result")
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
        self.lang_input = QLineEdit("tam_cus+eng")
        self.lang_input.setToolTip("Enter language codes separated by '+' (e.g., tam+eng)")
        toolbar.addWidget(self.lang_input)

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
        self.prev_btn.setEnabled(False)
        self.prev_btn.clicked.connect(self.prev_page)
        toolbar.addWidget(self.prev_btn)

        self.next_btn = QPushButton("Next Page →")
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
        except Exception as e:
            print(f"Error setting font size: {e}")

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
                self.update_edit_status()
                self.reset_text_btn.setEnabled(not self.busy)
        except Exception as e:
            print(f"Error in on_text_edited: {e}")

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
        except Exception as e:
            print(f"Error in update_edit_status: {e}")

    def reset_current_text(self):
        """Discard edits and restore the original OCR result for the current page"""
        try:
            self.edited_text.pop(self.current_page_index, None)
            self.set_editor_text(self.ocr_text.get(self.current_page_index, ""))
            self.update_edit_status()
            self.reset_text_btn.setEnabled(False)
        except Exception as e:
            print(f"Error in reset_current_text: {e}")

    def on_confidence_changed(self, value):
        """Re-filter OCR text and highlights in real time; user edits are left untouched"""
        try:
            self.confidence_threshold = value

            # Re-filter every processed page so export stays consistent with the threshold
            for page_index, data in self.ocr_data_cache.items():
                self.ocr_text[page_index] = '\n'.join(extract_text_lines(data, value))

            if self.current_page_index in self.ocr_data_cache:
                self.update_current_page_highlights()
        except Exception as e:
            print(f"Error in on_confidence_changed: {e}")

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
        except Exception as e:
            print(f"Error in update_current_page_highlights: {e}")

    def update_page_info(self):
        try:
            if self.temp_pages:
                total_pages = len(self.temp_pages)
                current_page = self.current_page_index + 1
                self.page_info_label.setText(f"Page {current_page} of {total_pages}")
            else:
                self.page_info_label.setText("No document loaded")
        except Exception as e:
            print(f"Error in update_page_info: {e}")

    # --- Lifecycle / file handling ------------------------------------------

    def stop_workers(self):
        """Stop PDF conversion and OCR; late results from either are discarded."""
        if self.pdf_worker is not None:
            # Disconnect first so a conversion that outlives wait() can't deliver stale pages
            for sig in (self.pdf_worker.pages_converted,
                        self.pdf_worker.conversion_progress,
                        self.pdf_worker.error_occurred):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            if self.pdf_worker.isRunning():
                self.pdf_worker.stop()
                self.pdf_worker.wait(3000)
            self.pdf_worker = None

        self.ocr_manager.stop_all()

    def closeEvent(self, event):
        try:
            self.stop_workers()
            self.clear_temp_pages()
        except Exception as e:
            print(f"Error in closeEvent: {e}")
        super().closeEvent(event)

    def dragEnterEvent(self, event):
        try:
            if event.mimeData().hasUrls() and not self.busy:
                event.acceptProposedAction()
        except Exception as e:
            print(f"Error in dragEnterEvent: {e}")

    def dropEvent(self, event):
        try:
            if self.busy:
                return
            urls = event.mimeData().urls()
            if urls:
                # Only one document is open at a time
                self.process_file(urls[0].toLocalFile())
        except Exception as e:
            print(f"Error in dropEvent: {e}")

    def resizeEvent(self, event):
        try:
            super().resizeEvent(event)
            # Delay fit_view to avoid excessive calls during resize
            QTimer.singleShot(100, self.fit_view)
        except Exception as e:
            print(f"Error in resizeEvent: {e}")

    def open_file(self):
        try:
            if self.busy:
                return
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Open File", "",
                "Images/PDF (*.png *.jpg *.jpeg *.tif *.tiff *.pdf)"
            )
            if file_path:
                self.process_file(file_path)
        except Exception as e:
            print(f"Error in open_file: {e}")

    def process_file(self, file_path):
        try:
            if not file_path:
                return

            self.stop_workers()

            # Clear previous state
            self.clear_temp_pages()
            self.scene.clear()
            self.set_editor_text("")
            self.current_page_index = 0
            self.pix_item = None
            self.highlight_items = []
            self.ocr_data_cache = {}
            self.ocr_text = {}
            self.edited_text = {}
            self.page_errors = {}
            self.update_edit_status()

            # Show progress bar and disable UI
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            self.set_ui_enabled(False)

            if file_path.lower().endswith('.pdf'):
                # Start PDF conversion in separate thread, writing into an app-owned temp dir
                self.status_bar.showMessage("Loading PDF...")
                self.temp_dir = tempfile.mkdtemp(prefix="tamil_ocr_")
                self.pdf_worker = PDFConversionWorker(file_path, self.temp_dir)
                self.pdf_worker.pages_converted.connect(self.on_pdf_converted)
                self.pdf_worker.conversion_progress.connect(self.on_progress_update)
                self.pdf_worker.error_occurred.connect(self.on_processing_error)
                self.pdf_worker.start()
            else:
                # Single image file - used in place, never deleted
                self.temp_pages = [file_path]
                self.prev_btn.setEnabled(False)
                self.next_btn.setEnabled(False)
                self.update_page_info()
                self.progress_bar.setValue(30)
                self.display_current_page_with_cache()
                self.start_ocr_processing()
        except Exception as e:
            print(f"Error in process_file: {e}")
            self.on_processing_error(str(e))

    def on_pdf_converted(self, temp_paths):
        """Called when PDF conversion is complete"""
        try:
            self.temp_pages = temp_paths
            if self.temp_pages:
                self.prev_btn.setEnabled(len(self.temp_pages) > 1)
                self.next_btn.setEnabled(len(self.temp_pages) > 1)
                self.update_page_info()
                self.display_current_page_with_cache()
                self.start_ocr_processing()
            else:
                self.on_processing_error("No pages found in PDF")
        except Exception as e:
            print(f"Error in on_pdf_converted: {e}")

    def start_ocr_processing(self):
        """Start parallel OCR processing"""
        try:
            if not self.temp_pages:
                return

            self.ocr_start_time = time.time()

            # Get language string from input
            lang_string = self.lang_input.text().strip()
            if not lang_string:
                # Fallback to default if empty
                lang_string = "tam_cus+eng"
                self.lang_input.setText(lang_string)

            self.ocr_manager.start_processing(self.temp_pages, self.confidence_threshold, lang_string)
        except Exception as e:
            print(f"Error in start_ocr_processing: {e}")
            self.on_processing_error(str(e))

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
            self.ocr_data_cache = {}
            self.ocr_text = {}
            self.edited_text = {}
            self.page_errors = {}

            # Show progress and disable UI
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(40)
            self.set_ui_enabled(False)

            # Clear current highlights and text
            for item in self.highlight_items:
                self.scene.removeItem(item)
            self.highlight_items = []
            self.set_editor_text("Re-running OCR...")
            self.update_edit_status()

            self.start_ocr_processing()
        except Exception as e:
            print(f"Error in rerun_ocr: {e}")

    def on_progress_update(self, progress, message):
        """Update progress bar and status"""
        try:
            self.progress_bar.setValue(progress)
            self.status_bar.showMessage(message)
        except Exception as e:
            print(f"Error in on_progress_update: {e}")

    def on_page_processed(self, page_index, text, ocr_data):
        try:
            self.ocr_data_cache[page_index] = ocr_data
            # Re-filter in case the threshold changed while this page was processing
            self.ocr_text[page_index] = '\n'.join(extract_text_lines(ocr_data, self.confidence_threshold))

            if page_index == self.current_page_index:
                self.display_current_page_with_cache()
        except Exception as e:
            print(f"Error in on_page_processed: {e}")

    def on_page_failed(self, page_index, message):
        try:
            self.page_errors[page_index] = message
            self.status_bar.showMessage(f"Page {page_index + 1}: {message}")
            if page_index == self.current_page_index:
                self.display_current_page_with_cache()
        except Exception as e:
            print(f"Error in on_page_failed: {e}")

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

            self.display_current_page_with_cache()
        except Exception as e:
            print(f"Error in on_processing_complete: {e}")

    def on_processing_error(self, error_message):
        try:
            self.set_ui_enabled(True)
            self.progress_bar.setVisible(False)
            self.status_bar.showMessage(f"Error: {error_message}")
        except Exception as e:
            print(f"Error in on_processing_error: {e}")

    def set_ui_enabled(self, enabled):
        """Enable/disable controls that must not be used while processing"""
        self.busy = not enabled
        has_pages = bool(self.temp_pages)
        self.open_action.setEnabled(enabled)
        self.export_action.setEnabled(enabled and has_pages)
        self.rerun_ocr_btn.setEnabled(enabled and has_pages)
        self.reset_text_btn.setEnabled(enabled and self.is_page_modified(self.current_page_index))
        self.confidence_spinbox.setEnabled(enabled)
        self.lang_input.setEnabled(enabled)

    # --- Page display ---------------------------------------------------------

    def display_image_only(self, image_path):
        """Display just the image without OCR bounding boxes"""
        try:
            self.scene.clear()
            self.highlight_items = []

            # Load image into QPixmap
            pixmap = QPixmap(image_path)
            self.pix_item = QGraphicsPixmapItem(pixmap)
            self.pix_item.setZValue(0)
            self.scene.addItem(self.pix_item)

            # Fit view to image initially
            self.fit_view()
        except Exception as e:
            print(f"Error in display_image_only: {e}")

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
            self.update_page_info()
        except Exception as e:
            print(f"Error in display_current_page_with_cache: {e}")

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
                except Exception as e:
                    print(f"Error parsing confidence for bounding box: {e}")
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
        except Exception as e:
            print(f"Error in add_bounding_boxes: {e}")

    def fit_view(self):
        try:
            if self.pix_item is not None:
                self.graphics_view.resetTransform()
                self.graphics_view.fitInView(self.pix_item, Qt.AspectRatioMode.KeepAspectRatio)
                self.scale_factor = 1.0
        except Exception as e:
            print(f"Error in fit_view: {e}")

    def zoom_in(self):
        try:
            self.scale_view(1.25)
        except Exception as e:
            print(f"Error in zoom_in: {e}")

    def zoom_out(self):
        self.scale_view(0.8)

    def scale_view(self, factor):
        self.scale_factor *= factor
        self.graphics_view.scale(factor, factor)

    def toggle_highlight_visibility(self):
        self.highlights_visible = self.toggle_highlights_action.isChecked()
        for rect in self.highlight_items:
            rect.setVisible(self.highlights_visible)

    def prev_page(self):
        # Edits are stored as they happen (on_text_edited), so nothing to save here
        if self.temp_pages and self.current_page_index > 0:
            self.current_page_index -= 1
            self.display_current_page_with_cache()

    def next_page(self):
        if self.temp_pages and self.current_page_index < len(self.temp_pages) - 1:
            self.current_page_index += 1
            self.display_current_page_with_cache()

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
                    print(f"Error exporting text: {e}")
                    self.status_bar.showMessage(f"Error exporting text: {e}")


if __name__ == '__main__':
    try:
        app = QApplication(sys.argv)
        window = OCRApp()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        logging.basicConfig(filename='app_error.log', level=logging.DEBUG)
        logging.exception("Caught exception at top level")
