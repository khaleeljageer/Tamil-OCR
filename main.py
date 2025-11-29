import os
import sys
import tempfile

import pytesseract
from PIL import Image


# Helper function to get resource paths for PyInstaller
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


from PyQt6.QtCore import Qt, QRectF, QThread, pyqtSignal, QTimer, QThreadPool, QRunnable
from PyQt6.QtGui import QPixmap, QPen, QColor, QBrush, QPainter
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QLabel, QPushButton, QFileDialog, QTextEdit, QHBoxLayout, QSplitter,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
    QCheckBox, QProgressBar, QStatusBar, QSpinBox, QFrame, QLineEdit
)
from pdf2image import convert_from_path
from PIL import Image

# Increase PIL's image size limit to handle large PDF pages
Image.MAX_IMAGE_PIXELS = None  # Remove the limit entirely
tessdata_dir = resource_path('tessdata')
os.environ['TESSDATA_PREFIX'] = tessdata_dir
pytesseract.pytesseract.tesseract_cmd = resource_path('tesseract/tesseract.AppImage')

class PDFConversionWorker(QThread):
    """Separate worker for PDF to image conversion to prevent UI freeze"""
    pages_converted = pyqtSignal(list)
    conversion_progress = pyqtSignal(int, str)
    error_occurred = pyqtSignal(str)

    def __init__(self, pdf_path):
        super().__init__()
        self.pdf_path = pdf_path
        self.should_stop = False

    def run(self):
        try:
            self.conversion_progress.emit(10, "Converting PDF to images...")

            # Start with lower DPI and increase if needed
            try:
                # Try with standard DPI first
                pages = convert_from_path(self.pdf_path, dpi=300)
            except Exception as e:
                if "exceeds limit" in str(e) or "decompression bomb" in str(e):
                    # If size limit exceeded, try with lower DPI
                    self.conversion_progress.emit(15, "Large PDF detected, using lower DPI...")
                    pages = convert_from_path(self.pdf_path, dpi=150)
                else:
                    raise e

            total_pages = len(pages)

            temp_paths = []
            for i, page in enumerate(pages):
                if self.should_stop:
                    # Clean up any created files
                    for path in temp_paths:
                        try:
                            os.remove(path)
                        except:
                            pass
                    return

                fd, tmp_path = tempfile.mkstemp(suffix=f"_page_{i}.png")
                os.close(fd)

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

                page.save(tmp_path, 'PNG', optimize=True)
                temp_paths.append(tmp_path)

                # Update progress
                progress = 10 + int((i + 1) / total_pages * 30)
                self.conversion_progress.emit(progress, f"Converting page {i + 1}/{total_pages}...")

            self.pages_converted.emit(temp_paths)

        except Exception as e:
            self.error_occurred.emit(f"PDF conversion error: {str(e)}")

    def stop(self):
        self.should_stop = True


class OCRTask(QRunnable):
    """Individual OCR task for parallel processing"""

    def __init__(self, page_index, image_path, confidence_threshold, lang_string, signals):
        super().__init__()
        self.page_index = page_index
        self.image_path = image_path
        self.confidence_threshold = confidence_threshold
        self.lang_string = lang_string
        self.signals = signals

    def run(self):
        try:
            # Process OCR for this page
            pil_img = Image.open(self.image_path).convert('RGB')
            ocr_data = pytesseract.image_to_data(pil_img, lang=self.lang_string, output_type=pytesseract.Output.DICT)

            # Extract text with confidence filtering
            text_lines = self.extract_text_lines(ocr_data, self.confidence_threshold)
            text = '\n'.join(text_lines)

            # Emit results
            self.signals.page_processed.emit(self.page_index, text, ocr_data)

        except Exception as e:
            self.signals.error_occurred.emit(f"OCR error on page {self.page_index + 1}: {str(e)}")

    def extract_text_lines(self, data, confidence_threshold):
        text_lines = []
        current_line = []
        last_block_num = last_par_num = last_line_num = -1

        n_boxes = len(data.get('text', []))
        for i in range(n_boxes):
            try:
                conf = float(data['conf'][i])
            except Exception:
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


class OCRSignals(QWidget):
    """Signals for OCR parallel processing"""
    page_processed = pyqtSignal(int, str, object)
    error_occurred = pyqtSignal(str)


class OCRManager(QWidget):
    """Manages parallel OCR processing"""
    processing_complete = pyqtSignal()
    progress_update = pyqtSignal(int, str)

    def __init__(self):
        super().__init__()
        self.signals = OCRSignals()
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(min(4, os.cpu_count() or 2))
        self.total_pages = 0
        self.completed_pages = 0

    def start_processing(self, image_paths, confidence_threshold, lang_string):
        self.total_pages = len(image_paths)
        self.completed_pages = 0

        # Connect signals
        self.signals.page_processed.connect(self.on_page_completed)

        self.progress_update.emit(40, "Starting OCR processing...")

        # Create and queue OCR tasks
        for i, image_path in enumerate(image_paths):
            task = OCRTask(i, image_path, confidence_threshold, lang_string, self.signals)
            self.thread_pool.start(task)

    def on_page_completed(self, page_index, text, ocr_data):
        self.completed_pages += 1
        progress = 40 + int((self.completed_pages / self.total_pages) * 60)  # 40-100%
        self.progress_update.emit(progress, f"Processed page {self.completed_pages}/{self.total_pages}")

        if self.completed_pages >= self.total_pages:
            self.processing_complete.emit()

    def stop_all(self):
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


class OCRApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tamil OCR Desktop App")
        self.setGeometry(200, 100, 1200, 800)

        self.temp_pages = []
        self.current_page_index = 0
        self.pix_item = None
        self.highlight_items = []
        self.highlights_visible = True
        self.ocr_data_cache = {}
        self.text_cache = {}
        self.text_modified = {}

        # Store confidence threshold value directly to prevent widget issues
        self.confidence_threshold = 0

        # Workers
        self.pdf_worker = None
        self.ocr_manager = OCRManager()

        # Setup UI
        self.setup_ui()

        # Connect OCR manager signals
        self.ocr_manager.signals.page_processed.connect(self.on_page_processed)
        self.ocr_manager.signals.error_occurred.connect(self.on_processing_error)
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

        # Page info label
        self.page_info_label = QLabel("No document loaded")
        self.status_bar.addPermanentWidget(self.page_info_label)

        self.scale_factor = 1.0

    def setup_ui(self):
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # Control buttons frame
        controls_frame = QFrame()
        controls_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        controls_frame.setMaximumHeight(60)
        controls_layout = QHBoxLayout(controls_frame)
        controls_layout.setContentsMargins(10, 10, 10, 10)
        controls_layout.setSpacing(8)

        # File operations
        self.open_btn = QPushButton("Open Image/PDF")
        self.open_btn.setMinimumWidth(120)
        self.export_btn = QPushButton("Export Text")
        self.export_btn.setMinimumWidth(100)
        self.reprocess_btn = QPushButton("Reprocess OCR")
        self.reprocess_btn.setMinimumWidth(120)
        self.reprocess_btn.setToolTip("Reprocess OCR with current confidence threshold")
        self.reprocess_btn.setEnabled(False)

        # NEW: Reset text button
        self.reset_text_btn = QPushButton("Reset Text")
        self.reset_text_btn.setMinimumWidth(100)
        self.reset_text_btn.setToolTip("Reset current page text to original OCR result")
        self.reset_text_btn.setEnabled(False)

        # Checkbox
        self.toggle_highlights = QCheckBox("Show Highlights")
        self.toggle_highlights.setChecked(True)
        self.toggle_highlights.setMinimumWidth(100)

        # Confidence threshold setting
        conf_label = QLabel("Confidence Threshold:")
        self.confidence_spinbox = QSpinBox()
        self.confidence_spinbox.setRange(0, 100)
        self.confidence_spinbox.setValue(self.confidence_threshold)
        self.confidence_spinbox.setSuffix("%")
        self.confidence_spinbox.setMinimumWidth(80)
        self.confidence_spinbox.setToolTip("Minimum confidence level for OCR text recognition (0-100%)")

        # Connect confidence change signal for real-time updates
        self.confidence_spinbox.valueChanged.connect(self.on_confidence_changed)

        # Language input
        lang_label = QLabel("Tesseract Langs:")
        self.lang_input = QLineEdit("tam_cus+eng")
        self.lang_input.setFixedWidth(120)
        self.lang_input.setToolTip("Enter language codes separated by '+' (e.g., tam+eng)")

        # Navigation
        self.prev_btn = QPushButton("← Prev Page")
        self.prev_btn.setMinimumWidth(100)
        self.prev_btn.setEnabled(False)
        self.next_btn = QPushButton("Next Page →")
        self.next_btn.setMinimumWidth(100)
        self.next_btn.setEnabled(False)

        # Add widgets to controls layout
        controls_layout.addWidget(self.open_btn)
        controls_layout.addWidget(self.export_btn)
        controls_layout.addWidget(self.reprocess_btn)
        controls_layout.addWidget(self.reset_text_btn)  # NEW

        # Separator
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.VLine)
        separator1.setFrameShadow(QFrame.Shadow.Sunken)
        controls_layout.addWidget(separator1)

        controls_layout.addWidget(self.toggle_highlights)

        # Separator
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.VLine)
        separator2.setFrameShadow(QFrame.Shadow.Sunken)
        controls_layout.addWidget(separator2)

        controls_layout.addWidget(conf_label)
        controls_layout.addWidget(self.confidence_spinbox)

        # Separator
        separator3 = QFrame()
        separator3.setFrameShape(QFrame.Shape.VLine)
        separator3.setFrameShadow(QFrame.Shadow.Sunken)

        controls_layout.addWidget(separator3)
        controls_layout.addWidget(lang_label)
        controls_layout.addWidget(self.lang_input)

        controls_layout.addStretch()  # Push navigation to the right

        # Separator
        separator4 = QFrame()
        separator4.setFrameShape(QFrame.Shape.VLine)
        separator4.setFrameShadow(QFrame.Shadow.Sunken)
        controls_layout.addWidget(separator4)

        controls_layout.addWidget(self.prev_btn)
        controls_layout.addWidget(self.next_btn)

        # Content area - Splitter for image preview + text
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setChildrenCollapsible(False)

        # Graphics view with floating zoom controls
        self.scene = QGraphicsScene()
        self.image_viewer = ImageViewerWidget(self.scene)
        self.image_viewer.setMinimumSize(400, 300)
        self.graphics_view = self.image_viewer.graphics_view  # Reference for compatibility
        content_splitter.addWidget(self.image_viewer)

        # Text output with editing label
        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(5)

        # NEW: Text area label and editing status
        text_header = QHBoxLayout()
        text_header.setContentsMargins(5, 0, 5, 0)

        self.text_label = QLabel("OCR Result (Editable for Proofreading)")
        self.text_label.setStyleSheet("font-weight: bold; color: #333;")

        self.edit_status_label = QLabel()
        self.edit_status_label.setStyleSheet("color: #666; font-style: italic;")

        text_header.addWidget(self.text_label)
        text_header.addStretch()
        text_header.addWidget(self.edit_status_label)

        text_layout.addLayout(text_header)

        # Text edit area - now explicitly editable
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("OCR output will appear here...\nYou can edit this text for proofreading purposes.")
        self.text_edit.setMinimumSize(300, 300)
        # NEW: Enable editing and connect text change signal
        self.text_edit.setReadOnly(False)
        self.text_edit.textChanged.connect(self.on_text_edited)

        text_layout.addWidget(self.text_edit)
        content_splitter.addWidget(text_widget)

        # Set splitter proportions (60% image, 40% text)
        content_splitter.setSizes([720, 480])

        # Add all components to main layout
        main_layout.addWidget(controls_frame)
        main_layout.addWidget(content_splitter, 1)  # Give content area most space

        # Connect button signals
        self.open_btn.clicked.connect(self.open_file)
        self.export_btn.clicked.connect(self.export_text)
        self.reprocess_btn.clicked.connect(self.reprocess_ocr)
        self.reset_text_btn.clicked.connect(self.reset_current_text)  # NEW
        self.prev_btn.clicked.connect(self.prev_page)
        self.next_btn.clicked.connect(self.next_page)
        self.toggle_highlights.stateChanged.connect(self.toggle_highlight_visibility)

        # Connect zoom controls
        self.image_viewer.zoom_in_btn.clicked.connect(self.zoom_in)
        self.image_viewer.zoom_out_btn.clicked.connect(self.zoom_out)
        self.image_viewer.fit_btn.clicked.connect(self.fit_view)

    def on_text_edited(self):
        """Called when text is edited in the text area"""
        if hasattr(self, 'temp_pages') and self.temp_pages:
            # Mark current page as modified
            self.text_modified[self.current_page_index] = True
            self.update_edit_status()
            self.reset_text_btn.setEnabled(True)

    def update_edit_status(self):
        """Update the editing status label"""
        if self.current_page_index in self.text_modified and self.text_modified[self.current_page_index]:
            self.edit_status_label.setText("✏️ Modified")
            self.edit_status_label.setStyleSheet("color: #e67e22; font-style: italic; font-weight: bold;")
        else:
            self.edit_status_label.setText("📄 Original")
            self.edit_status_label.setStyleSheet("color: #27ae60; font-style: italic;")

    def reset_current_text(self):
        """Reset current page text to original OCR result"""
        if self.current_page_index in self.text_cache:
            # Temporarily disconnect signal to avoid triggering text edited
            self.text_edit.textChanged.disconnect()
            self.text_edit.setPlainText(self.text_cache[self.current_page_index])
            self.text_edit.textChanged.connect(self.on_text_edited)

            # Mark as not modified
            self.text_modified[self.current_page_index] = False
            self.update_edit_status()
            self.reset_text_btn.setEnabled(False)

    def save_current_page_text(self):
        """Save current text editor content before switching pages"""
        if hasattr(self, 'temp_pages') and self.temp_pages and hasattr(self, 'current_page_index'):
            # This saves the current edited text (whether modified or not)
            pass  # Text is automatically saved when we switch pages in display_current_page_with_cache

    def on_confidence_changed(self, value):
        """Update stored confidence threshold when spinbox changes and update display in real-time"""
        self.confidence_threshold = value

        # If we have cached OCR data, update the display with new confidence immediately
        if self.current_page_index in self.ocr_data_cache:
            self.update_current_page_highlights()

    def update_current_page_highlights(self):
        """Update highlights and text on current page with current confidence threshold"""
        if not self.temp_pages or self.current_page_index >= len(self.temp_pages):
            return

        # Remove existing highlights
        for item in self.highlight_items:
            self.scene.removeItem(item)
        self.highlight_items = []

        # Add highlights with current confidence threshold
        if self.current_page_index in self.ocr_data_cache:
            self.add_bounding_boxes(self.ocr_data_cache[self.current_page_index], self.confidence_threshold)

        # Only update text if it hasn't been manually edited
        if (self.current_page_index not in self.text_modified or
            not self.text_modified[self.current_page_index]):

            if self.current_page_index in self.ocr_data_cache:
                data = self.ocr_data_cache[self.current_page_index]
                text_lines = self.extract_text_lines_from_data(data, self.confidence_threshold)
                text = '\n'.join(text_lines)

                # Temporarily disconnect signal to avoid triggering text edited
                self.text_edit.textChanged.disconnect()
                self.text_edit.setPlainText(text)
                self.text_edit.textChanged.connect(self.on_text_edited)

                # Update cache with new filtered text
                self.text_cache[self.current_page_index] = text

    def extract_text_lines_from_data(self, data, confidence_threshold):
        """Extract text lines from OCR data with given confidence threshold"""
        text_lines = []
        current_line = []
        last_block_num = last_par_num = last_line_num = -1

        n_boxes = len(data.get('text', []))
        for i in range(n_boxes):
            try:
                conf = float(data['conf'][i])
            except Exception:
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

    def update_page_info(self):
        if self.temp_pages:
            total_pages = len(self.temp_pages)
            current_page = self.current_page_index + 1
            self.page_info_label.setText(f"Page {current_page} of {total_pages}")
        else:
            self.page_info_label.setText("No document loaded")

    def closeEvent(self, event):
        # Stop all workers
        if self.pdf_worker and self.pdf_worker.isRunning():
            self.pdf_worker.stop()
            self.pdf_worker.wait(3000)

        self.ocr_manager.stop_all()

        # Clean up temporary files
        self.clear_temp_pages()
        super().closeEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            self.process_file(file_path)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Delay fit_view to avoid excessive calls during resize
        QTimer.singleShot(100, self.fit_view)

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open File", "",
            "Images/PDF (*.png *.jpg *.jpeg *.tif *.tiff *.pdf)"
        )
        if file_path:
            self.process_file(file_path)

    def process_file(self, file_path):
        # Stop any running workers
        if self.pdf_worker and self.pdf_worker.isRunning():
            self.pdf_worker.stop()
            self.pdf_worker.wait(3000)

        self.ocr_manager.stop_all()

        # Clear previous state
        self.clear_temp_pages()
        self.scene.clear()
        self.text_edit.clear()
        self.current_page_index = 0
        self.pix_item = None
        self.highlight_items = []
        self.ocr_data_cache = {}
        self.text_cache = {}
        self.text_modified = {}  # NEW: Clear modification tracking

        # Show progress bar and disable UI
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.set_ui_enabled(False)

        if file_path.lower().endswith('.pdf'):
            # Start PDF conversion in separate thread
            self.status_bar.showMessage("Loading PDF...")
            self.pdf_worker = PDFConversionWorker(file_path)
            self.pdf_worker.pages_converted.connect(self.on_pdf_converted)
            self.pdf_worker.conversion_progress.connect(self.on_progress_update)
            self.pdf_worker.error_occurred.connect(self.on_processing_error)
            self.pdf_worker.start()
        else:
            # Single image file - process immediately
            self.temp_pages = [file_path]  # Don't delete original file
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.update_page_info()
            self.progress_bar.setValue(30)
            # Display image immediately
            self.display_image_only(file_path)
            # Start OCR processing
            self.start_ocr_processing()

    def on_pdf_converted(self, temp_paths):
        """Called when PDF conversion is complete"""
        self.temp_pages = temp_paths
        if self.temp_pages:
            self.prev_btn.setEnabled(len(self.temp_pages) > 1)
            self.next_btn.setEnabled(len(self.temp_pages) > 1)
            self.update_page_info()
            # Display first page immediately
            self.display_image_only(self.temp_pages[0])
            # Start OCR processing
            self.start_ocr_processing()
        else:
            self.on_processing_error("No pages found in PDF")

    def start_ocr_processing(self):
        """Start parallel OCR processing"""
        if not self.temp_pages:
            return

        # Get language string from input
        lang_string = self.lang_input.text().strip()
        if not lang_string:
            # Fallback to default if empty
            lang_string = "tam_cus+eng"
            self.lang_input.setText(lang_string)

        # Use stored confidence threshold (always available)
        self.ocr_manager.start_processing(self.temp_pages, self.confidence_threshold, lang_string)

    def reprocess_ocr(self):
        """Reprocess OCR with current confidence threshold"""
        if not self.temp_pages:
            return

        # Clear cached data
        self.ocr_data_cache = {}
        self.text_cache = {}
        self.text_modified = {}  # NEW: Clear modification tracking

        # Show progress and disable UI
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(40)
        self.set_ui_enabled(False)

        # Clear current highlights and text
        for item in self.highlight_items:
            self.scene.removeItem(item)
        self.highlight_items = []
        self.text_edit.setPlainText("Reprocessing OCR...")

        # Start OCR processing
        self.start_ocr_processing()

    def on_progress_update(self, progress, message):
        """Update progress bar and status"""
        self.progress_bar.setValue(progress)
        self.status_bar.showMessage(message)

    def on_page_processed(self, page_index, text, ocr_data):
        # Cache the results
        self.ocr_data_cache[page_index] = ocr_data
        self.text_cache[page_index] = text

        # If this is the current page, update the display
        if page_index == self.current_page_index:
            self.display_current_page_with_cache()

    def on_processing_complete(self):
        # Re-enable UI
        self.set_ui_enabled(True)
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage("OCR processing complete")

        # Make sure current page is displayed with all data
        self.display_current_page_with_cache()

    def on_processing_error(self, error_message):
        # Re-enable UI
        self.set_ui_enabled(True)
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage(f"Error: {error_message}")

    def set_ui_enabled(self, enabled):
        """Enable/disable UI elements during processing"""
        # Check if widgets still exist before trying to access them
        if hasattr(self, 'open_btn') and self.open_btn is not None:
            self.open_btn.setEnabled(enabled)
        if hasattr(self, 'export_btn') and self.export_btn is not None:
            self.export_btn.setEnabled(enabled)
        if hasattr(self, 'reprocess_btn') and self.reprocess_btn is not None:
            self.reprocess_btn.setEnabled(enabled and bool(self.temp_pages))
        if hasattr(self, 'reset_text_btn') and self.reset_text_btn is not None:  # NEW
            self.reset_text_btn.setEnabled(enabled and bool(self.temp_pages))
        if hasattr(self, 'confidence_spinbox') and self.confidence_spinbox is not None:
            try:
                self.confidence_spinbox.setEnabled(enabled)
                # DO NOT reset the value - keep user's setting
            except RuntimeError:
                # Widget has been deleted, ignore
                pass
        if hasattr(self, 'lang_input') and self.lang_input is not None:
            self.lang_input.setEnabled(enabled)

    def display_image_only(self, image_path):
        """Display just the image without OCR bounding boxes"""
        self.scene.clear()
        self.highlight_items = []

        # Load image into QPixmap
        pixmap = QPixmap(image_path)
        self.pix_item = QGraphicsPixmapItem(pixmap)
        self.pix_item.setZValue(0)
        self.scene.addItem(self.pix_item)

        # Fit view to image initially
        self.fit_view()

    def display_current_page_with_cache(self):
        """Display current page with cached OCR data if available"""
        if not self.temp_pages or self.current_page_index >= len(self.temp_pages):
            return

        image_path = self.temp_pages[self.current_page_index]

        # Clear and setup image
        self.scene.clear()
        self.highlight_items = []

        # Load image
        pixmap = QPixmap(image_path)
        self.pix_item = QGraphicsPixmapItem(pixmap)
        self.pix_item.setZValue(0)
        self.scene.addItem(self.pix_item)

        # Add bounding boxes if OCR data is available
        if self.current_page_index in self.ocr_data_cache:
            # Use stored confidence threshold (always available)
            self.add_bounding_boxes(self.ocr_data_cache[self.current_page_index], self.confidence_threshold)

        # Set text - check if page has been modified
        if (self.current_page_index in self.text_modified and
            self.text_modified[self.current_page_index]):
            # Page has been modified, keep current text editor content
            pass  # Don't change text
        elif self.current_page_index in self.text_cache:
            # Use cached original text
            # Temporarily disconnect signal to avoid triggering text edited
            self.text_edit.textChanged.disconnect()
            self.text_edit.setPlainText(self.text_cache[self.current_page_index])
            self.text_edit.textChanged.connect(self.on_text_edited)
        else:
            # No cache yet
            self.text_edit.setPlainText("Processing OCR...")

        # Update UI state
        self.update_edit_status()
        self.reset_text_btn.setEnabled(
            self.current_page_index in self.text_modified and
            self.text_modified[self.current_page_index]
        )

        # Update page info
        self.update_page_info()

        # Fit view
        self.fit_view()

    def add_bounding_boxes(self, data, confidence_threshold):
        """Add bounding boxes from OCR data with confidence filtering"""
        pen = QPen(QColor(255, 0, 0))  # red outline
        pen.setWidth(2)
        brush = QBrush(QColor(255, 255, 0, 60))  # semi-transparent yellow fill

        n_boxes = len(data.get('text', []))
        for i in range(n_boxes):
            try:
                conf = float(data['conf'][i])
            except Exception:
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

    def fit_view(self):
        if self.pix_item is not None:
            self.graphics_view.resetTransform()
            self.graphics_view.fitInView(self.pix_item, Qt.AspectRatioMode.KeepAspectRatio)
            self.scale_factor = 1.0

    def zoom_in(self):
        self.scale_view(1.25)

    def zoom_out(self):
        self.scale_view(0.8)

    def scale_view(self, factor):
        self.scale_factor *= factor
        self.graphics_view.scale(factor, factor)

    def toggle_highlight_visibility(self):
        self.highlights_visible = self.toggle_highlights.isChecked()
        for rect in self.highlight_items:
            rect.setVisible(self.highlights_visible)

    def prev_page(self):
        if self.temp_pages and self.current_page_index > 0:
            self.current_page_index -= 1
            self.display_current_page_with_cache()

    def next_page(self):
        if self.temp_pages and self.current_page_index < len(self.temp_pages) - 1:
            self.current_page_index += 1
            self.display_current_page_with_cache()

    def clear_temp_pages(self):
        for p in self.temp_pages:
            # Only delete temporary files (those with temp directory path)
            if tempfile.gettempdir() in p:
                try:
                    os.remove(p)
                except Exception:
                    pass
        self.temp_pages = []

    def export_text(self):
        """Export text - uses current text editor content (including edits)"""
        # First, save the current page's text
        self.save_current_page_edited_text()

        if len(self.temp_pages) > 1:
            # Multi-page document - collect text from all pages
            all_text = []
            for i in range(len(self.temp_pages)):
                page_text = self.get_current_page_text(i)
                all_text.append(f"=== Page {i + 1} ===\n{page_text}\n")
            text = '\n'.join(all_text)
        else:
            # Single page - use current text editor content
            text = self.text_edit.toPlainText()

        if text.strip():
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save File", "output.txt", "Text Files (*.txt)"
            )
            if file_path:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(text)

                # Count modified pages for status message
                modified_pages = len([obj for obj in self.page_texts.values() if obj.is_modified()])
                status_msg = f"Text exported to {file_path}"
                if modified_pages > 0:
                    status_msg += f" (including {modified_pages} edited page{'s' if modified_pages > 1 else ''})"

                self.status_bar.showMessage(status_msg)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = OCRApp()
    window.show()
    sys.exit(app.exec())
