# Tamil OCR Desktop

A desktop application for performing Optical Character Recognition (OCR) on Tamil text from images and PDF files.

## Features

*   **Image and PDF Support:** Open and process various image formats (`.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`) and PDF files.
*   **Bundled Language Data:** Uses Tesseract OCR engine with the specific `tam_new` and `eng` language models, which are included in this repository.
*   **Interactive UI:**
    *   View images and navigate through multi-page PDF documents.
    *   Zoom, pan, and fit the image to the screen.
    *   Highlight recognized text directly on the image.
    *   Adjust the OCR confidence threshold to filter out uncertain results in real-time.
*   **Text Export:** Save the recognized text from a single page or an entire document to a `.txt` file.
*   **Drag and Drop:** Easily open files by dragging and dropping them onto the application window.

---

## Project Setup Guide

This guide will walk you through setting up the project on your local machine.

### 1. Prerequisites

*   **Python 3.8+**
*   **Git** and **Git LFS** (for handling the language data files).
*   **Tesseract OCR Engine v5.3.0** (or a compatible v5.x version).

### 2. Tesseract OCR Engine Installation

It is crucial to install the Tesseract engine itself, but **do not** install any system-wide language packs (like `tesseract-ocr-tam`). The required language files are included in this repository.

#### **Windows**
1.  Download and run the Tesseract installer from the [**UB Mannheim GitHub page**](https://github.com/UB-Mannheim/tesseract/wiki). We recommend a v5.x version.
2.  During installation, you can **uncheck all language data**, as it is not needed.
3.  **Important:** Make sure the Tesseract installation directory (e.g., `C:\Program Files\Tesseract-OCR`) is added to your system's `PATH` environment variable.

#### **macOS**
The easiest way to install Tesseract is using [Homebrew](https://brew.sh/).
```bash
# Install Tesseract
brew install tesseract
```

#### **Linux (Ubuntu/Debian)**
```bash
sudo apt update
sudo apt install tesseract-ocr
```

### 3. Project Code and Dependencies

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/Tamil-OCR.git
    cd Tamil-OCR
    ```

2.  **Set up Git LFS:**
    The language data files (`.traineddata`) are stored using Git LFS. You need to fetch them.
    ```bash
    # Install Git LFS on your system (one-time setup)
    git lfs install

    # Pull the LFS files for this repository
    git lfs pull
    ```
    This will download the `tam_new.traineddata` file into the `tessdata` directory.

3.  **Create and activate a virtual environment (recommended):**
    ```bash
    # Create the virtual environment
    python3 -m venv .venv

    # Activate it
    # On macOS/Linux:
    source .venv/bin/activate
    # On Windows:
    # .\.venv\Scripts\activate
    ```

4.  **Install the required Python packages:**
    ```bash
    pip install -r requirements.txt
    ```

### 4. Running the Application

Once the setup is complete, you can run the application from your terminal:
```bash
python main.py
```

---

## Core Dependencies

*   [PyQt6](https://pypi.org/project/PyQt6/)
*   [pytesseract](https://pypi.org/project/pytesseract/)
*   [pdf2image](https://pypi.org/project/pdf2image/)
*   [Pillow](https://pypi.org/project/Pillow/)

## Future Plans

*   **Image Preprocessing:** Implement automatic image preprocessing techniques (e.g., binarization, noise reduction, deskewing) to improve OCR accuracy.
