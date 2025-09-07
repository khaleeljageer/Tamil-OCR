# TamOCR

A desktop application for performing Optical Character Recognition (OCR) on Tamil text from images and PDF files.

## Features

*   **Image and PDF Support:** Open and process various image formats (`.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`) and PDF files.
*   **Tamil OCR:** Uses the Tesseract OCR engine with a specific Tamil language model for text recognition.
*   **Interactive UI:**
    *   View images and navigate through multi-page PDF documents.
    *   Zoom in, zoom out, and fit the image to the screen.
    *   Highlight recognized text directly on the image.
    *   Adjust the OCR confidence threshold to filter out uncertain results.
*   **Text Export:** Save the recognized text to a `.txt` file.
*   **Drag and Drop:** Easily open files by dragging and dropping them onto the application window.

## Installation

### 1. Tesseract OCR Engine

This application requires the Tesseract OCR engine to be installed on your system.

#### Windows

1.  Download and run the Tesseract installer from the [UB Mannheim GitHub page](https://github.com/UB-Mannheim/tesseract/wiki).
2.  During installation, in the "Choose Components" step, expand the "Language data" section and select **"Tamil"** to install the required language pack.
3.  It is recommended to add the Tesseract installation directory to your system's `PATH` environment variable.

#### macOS

The easiest way to install Tesseract is using [Homebrew](https://brew.sh/).

```bash
# Install Tesseract
brew install tesseract

# Install all language packs (including Tamil)
brew install tesseract-lang
```

#### Linux

**Debian/Ubuntu**

```bash
sudo apt update
sudo apt install tesseract-ocr
sudo apt install tesseract-ocr-tam
```

**Fedora**

```bash
sudo dnf install tesseract
sudo dnf install tesseract-langpack-tam
```

**Arch Linux**

```bash
sudo pacman -S tesseract
sudo pacman -S tesseract-data-tam
```

### 2. Python Dependencies

After installing Tesseract, you can install the required Python packages.

1.  **Clone the repository (or download the source code):**
    ```bash
    git clone <repository-url>
    cd TamOCR
    ```

2.  **Create and activate a virtual environment (recommended):**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate  # On Windows, use `.venv\Scripts\activate`
    ```

3.  **Install the required packages:**
    ```bash
    pip install -r requirements.txt
    ```

## Usage

Once the installation is complete, you can run the application:

```bash
python main.py
```

## Dependencies

*   [PyQt6](https://pypi.org/project/PyQt6/)
*   [pytesseract](https://pypi.org/project/pytesseract/)
*   [pdf2image](https://pypi.org/project/pdf2image/)
*   [Pillow](https://pypi.org/project/Pillow/)
*   [PyMuPDF](https://pypi.org/project/PyMuPDF/)
*   [fitz](https://pypi.org/project/fitz/)

## Future Plans

*   **Image Preprocessing:** Implement automatic image preprocessing techniques (e.g., binarization, noise reduction, deskewing) to improve OCR accuracy.
