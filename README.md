# Tamil OCR Desktop Application

A desktop application for Optical Character Recognition (OCR) on images and PDF files, focused on Tamil and English. Open a scan, check the recognised text next to the page, proofread it, and export clean text.

## Features

*   **Images and PDFs:** open PNG, JPG and TIFF images and multi-page PDFs (from the menu or by drag and drop).
*   **Fast and memory-light PDF processing:** pages are rendered one at a time and OCR starts on each page as soon as it is ready, so the first page's text appears within seconds and memory use stays flat even for long books.
*   **Parallel OCR:** several pages are recognised at once, powered by the Tesseract OCR engine.
*   **Bundled models:** a custom Tamil model (`tam_cus`) and the standard English model (`eng`).
*   **Language picker:** choose one or more of the installed models; the first one you tick is the primary language.
*   **Interactive page viewer:** zoom, fit to window, and highlighted word boxes (hover a box to see the word and its confidence).
*   **Confidence threshold:** hide words below a confidence level (0–100%); the text updates instantly.
*   **Proofreading editor:**
    *   Edit the recognised text of each page; edited pages are marked, and **Reset Text** restores the original OCR result.
    *   Paragraphs are kept, with a blank line between them.
    *   Uses the bundled Tamil font (`marutham.ttf`); the font size is adjustable.
*   **Sessions:** save your work to a `.tamilocr` file and continue later; see [Sessions](#sessions).
*   **Export:** save the proofread text of all pages to a single `.txt` file.
*   **Keyboard shortcuts:** for opening, saving, page navigation and zoom; see [Keyboard shortcuts](#keyboard-shortcuts).

## Download

Pre-built executables for Linux (`Tamil-OCR_linux`) and Windows (`Tamil-OCR_windows.exe`) are in the [`release/`](release/) folder. On Linux, make the file executable first:

```bash
chmod +x Tamil-OCR_linux
./Tamil-OCR_linux
```

On Linux, reading PDFs also needs `poppler-utils`, and the window needs `libxcb-cursor0` (see [Prerequisites](#prerequisites)).

## Using the application

1.  **Open** an image, PDF or saved session with **File → Open** (Ctrl+O), or drag the file onto the window.
2.  OCR runs automatically. The status bar shows progress; with a PDF you can already read and edit the first pages while later ones are still processing.
3.  **Proofread** each page in the editor on the right. Use **Prev / Next Page**, or the shortcuts below, to move between pages.
4.  Adjust **Confidence** to filter out uncertain words, or change the languages and click **Re-Run OCR** (Ctrl+R).
5.  **Save a session** (Ctrl+S) to keep your work, or **Export Text** (Ctrl+E) to get the final `.txt` file.

### Sessions

A session (`.tamilocr` file) stores everything needed to continue proofreading later:

*   the OCR results of every page, so reopening a session does **not** run OCR again;
*   your edits, the page you were on, the selected languages and the confidence threshold.

The document itself is not copied into the session; the session records where it is. Keep the session next to its document (for example in the same folder) and you can move or share the folder as a whole. If the document can't be found, the app asks you to locate it, and if it has changed since the session was saved, the app warns you before opening.

When you close the app or open another file with unsaved edits, you are asked whether to save them. A `*` in the window title means there are unsaved edits.

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+O | Open an image, PDF or session |
| Ctrl+S | Save session |
| Ctrl+Shift+S | Save session as… |
| Ctrl+E | Export text |
| Ctrl+Q | Quit |
| Ctrl+R | Re-run OCR on all pages |
| Ctrl+Shift+R | Reset the current page's text to the OCR result |
| Ctrl+PgUp / Ctrl+PgDown, Alt+← / Alt+→ | Previous / next page |
| PgUp / PgDown (in the page view) | Scroll a zoomed page, then go to the previous / next page |
| Ctrl+Home / Ctrl+End | First / last page |
| Ctrl+= / Ctrl+- / Ctrl+0 | Zoom in / zoom out / fit to window |
| Ctrl+mouse wheel | Zoom |
| Ctrl+H | Show / hide word highlights |

Inside the text editor, plain PgUp / PgDown scroll the text as usual.

### Adding OCR languages

The language picker lists every model in the `tessdata/` folder. To add a language, download its `.traineddata` file (for example from [tessdata_best](https://github.com/tesseract-ocr/tessdata_best)) into `tessdata/` and restart the app. When building an executable, rebuild it so the new model is bundled.

## Running from source

### Prerequisites

*   **Python 3.11 or newer**
*   **Linux:** `poppler-utils` (to read PDFs) and `libxcb-cursor0` (required by Qt 6.5+ to open the window on X11):
    ```bash
    sudo apt install poppler-utils libxcb-cursor0      # Debian / Ubuntu
    sudo dnf install poppler-utils xcb-util-cursor     # Fedora
    ```
    Tesseract is bundled as an AppImage (`binary/linux/tesseract/`). If it fails to start, install FUSE 2 (`sudo apt install libfuse2`, or `libfuse2t64` on Ubuntu 24.04+).
*   **Windows:** nothing extra; Tesseract and Poppler are bundled in `binary/windows/`.

### Steps

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/khaleeljageer/Tamil-OCR.git
    cd Tamil-OCR
    ```
    The release executables and OCR models are stored with [Git LFS](https://git-lfs.com/); run `git lfs install` before cloning, or `git lfs pull` afterwards.

2.  **Create a virtual environment (recommended):**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate          # Windows: .venv\Scripts\activate
    ```

3.  **Install the dependencies** (versions are pinned in `requirements.txt`):
    ```bash
    pip install -r requirements.txt
    ```

4.  **Run the application:**
    ```bash
    python main.py
    ```

## Building an executable

This project uses [PyInstaller](https://pyinstaller.org/) to create a single-file executable. Install it in your virtual environment first:

```bash
pip install pyinstaller
```

The commands below bundle the whole `tessdata/` folder, so any language models you have added are included.

### Linux

```bash
pyinstaller --name "Tamil-OCR" \
            --onefile \
            --windowed \
            --add-data "font/marutham.ttf:font" \
            --add-data "tessdata:tessdata" \
            --add-binary "binary/linux/tesseract/tesseract.AppImage:binary/linux/tesseract" \
            main.py
chmod +x dist/Tamil-OCR
./dist/Tamil-OCR
```

### Windows

Run in **Command Prompt** from the project folder. Tesseract and Poppler need their DLLs, so their whole folders are bundled:

```bat
pyinstaller --name "Tamil-OCR" ^
            --onefile ^
            --windowed ^
            --add-data "font\marutham.ttf;font" ^
            --add-data "tessdata;tessdata" ^
            --add-binary "binary\windows\tesseract;binary\windows\tesseract" ^
            --add-binary "binary\windows\poppler\Library\bin;binary\windows\poppler\Library\bin" ^
            main.py
dist\Tamil-OCR.exe
```

The executable is created in the `dist` folder.

## Troubleshooting

*   **Log file:** errors are written to `tamil-ocr.log`, which is useful to attach when [reporting an issue](https://github.com/khaleeljageer/Tamil-OCR/issues/new/choose):
    *   Linux: `~/.local/share/Tamil-OCR/tamil-ocr.log`
    *   Windows: `%LOCALAPPDATA%\Tamil-OCR\tamil-ocr.log`
*   **"OCR failed on every page":** usually the selected language model is missing from `tessdata/`, or Tesseract could not start (on Linux, check FUSE 2 above).
*   **PDFs don't open on Linux:** install `poppler-utils`.
*   **"Could not load the Qt platform plugin "xcb"" on Linux:** install `libxcb-cursor0` (Fedora: `xcb-util-cursor`).
*   **Very large pages:** pages too large to render at 300 DPI are rendered at a lower resolution automatically.

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

It uses [Tesseract OCR](https://tesseract-ocr.github.io/) (Apache 2.0), [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) (GPL v3), [pdf2image](https://github.com/Belval/pdf2image) (MIT), [Pillow](https://github.com/python-pillow/Pillow) (MIT-CMU) and [Poppler](https://poppler.freedesktop.org/) (GPL).
