# Tamil OCR Desktop Application

This is a desktop application for performing Optical Character Recognition (OCR) on images and PDF files, with a focus on Tamil and English languages.

## Features

*   **Cross-Platform:** Built with PyQt6 and can be compiled into a standalone executable for Linux.
*   **Image and PDF Support:** Open various image formats (PNG, JPG, etc.) and multi-page PDF documents.
*   **Efficient PDF Processing:** Converts PDFs to images in a separate thread to keep the UI responsive, with handling for large files.
*   **Parallel OCR:** Utilizes multiple CPU cores to process pages in parallel, significantly speeding up OCR tasks.
*   **Tesseract Integration:** Powered by the Tesseract OCR engine.
*   **Custom Models:** Comes bundled with a custom Tamil Tesseract model (`tam_cus`) and the standard English model.
*   **Interactive Image Viewer:**
    *   View document pages with zoom and fit-to-screen controls.
    *   Highlights recognized words with bounding boxes.
    *   Toggle highlights on or off for better readability.
*   **Advanced OCR Controls:**
    *   **Confidence Threshold:** Adjust the minimum confidence level (0-100%) to filter out uncertain results. Changes are reflected in real-time.
    *   **Language Selection:** Easily specify which Tesseract language models to use (e.g., `tam_cus+eng`).
*   **Rich Text Editor:**
    *   View and **edit** the extracted OCR text for proofreading and corrections.
    *   The application tracks edited pages and allows you to reset the text back to the original OCR result.
    *   Adjust the editor's font size for comfort.
    *   Includes a custom Tamil font (`marutham.ttf`) for proper rendering.
*   **Export Functionality:** Save the final, proofread text from all pages into a single `.txt` file.
*   **Drag and Drop:** Quickly open files by dragging them onto the application window.

## Setup and Installation

### Prerequisites

*   Python 3.x
*   Tesseract OCR Engine (The application comes bundled with a Tesseract AppImage for Linux).

### Steps

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/Tamil-OCR.git
    cd Tamil-OCR
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```

3.  **Install the required Python packages:**
    ```bash
    pip install -r requirements.txt
    ```

## How to Run the Application

Once the setup is complete, you can run the application from the source code:

```bash
python main.py
```

## How to Compile (Linux)

This project uses PyInstaller to create a standalone executable.

1.  **Install PyInstaller:**
    ```bash
    pip install pyinstaller
    ```

2.  **Run the PyInstaller command:**

The following command will bundle the Python script, assets (fonts, Tesseract data), and the Tesseract AppImage into a single executable file located in the `dist` directory.

```bash
pyinstaller --name "Tamil-OCR" \
            --onefile \
            --windowed \
            --add-data "font/marutham.ttf:font" \
            --add-data "tessdata/eng.traineddata:tessdata" \
            --add-data "tessdata/tam_cus.traineddata:tessdata" \
            --add-binary "tesseract/tesseract.AppImage:tesseract" \
            main.py
```

3.  **Make the executable runnable:**

After building, you need to give the generated file execute permissions.

```bash
chmod +x dist/Tamil-OCR
```

4.  **Run the compiled application:**
    ```bash
    ./dist/Tamil-OCR
    ```