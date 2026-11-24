# Improvements

Findings from a code review of `main.py` and the repository layout.
Items are checked off as they are fixed.

## 🔴 Bugs

- [x] **1. Can delete the user's original image.** `clear_temp_pages()` treated any path containing the system temp dir (`/tmp`, `%TEMP%`) as an app-owned temp file, so opening `/tmp/scan.png` deleted it on the next open/close. Fix: write PDF pages into an app-owned temp directory and only remove that directory.
- [x] **2. Re-running OCR finishes too early.** `OCRManager.start_processing()` connected `page_processed → on_page_completed` on every run, so pages were counted twice on re-run (early "complete", progress > 100%). Fix: connect once.
- [x] **3. Results from the old file leak into the new one.** `stop_all()` only cleared queued tasks; tasks already running still emitted results by page index into the new document's caches. Fix: tag each OCR run with a job ID and drop results from stale jobs.
- [x] **4. Pages get marked "Modified" by mistake.** Placeholder text (`"Processing OCR..."`, `"Re-running OCR..."`) was set without blocking signals, triggering `on_text_edited`. Fix: block signals for every programmatic editor update (also removes fragile `disconnect()`/`connect()` pairs).
- [x] **5. "Reset Text" can't restore the original.** `text_cache` held both OCR output and user edits, and was overwritten when changing pages. Fix: keep separate `ocr_text` and `edited_text` stores. Re-running OCR now asks before discarding edits.
- [x] **6. PDFs fail in the Windows .exe.** Poppler path wasn't wrapped in `resource_path()`, and the low-DPI fallback pointed to a non-existent `binary/windows/poppler/bin`. Fix: one `POPPLER_PATH` constant via `resource_path()`. _(Verified on Linux; needs a check in the Windows build.)_
- [x] **7. If one page fails, the app never reports completion.** Failed pages never counted toward the total. Fix: count failures, report them, and show the error on the failed page.
- [x] **8. The menu stays active during processing.** `set_ui_enabled()` referenced non-existent `open_btn` / `export_btn`; File → Open/Export and drag-and-drop stayed live during OCR. Fix: disable the actual menu actions and ignore drops while busy.
- [x] **9. The editor never used the bundled Tamil font.** `marutham.ttf` was loaded after the editor was created, so the editor stayed on `monospace`. Fix: load the font before building the UI. _(Found while adding the features.)_

## 🟡 Performance & robustness

- [x] Convert PDFs to disk page-by-page (`output_folder` / `paths_only`, or `first_page`/`last_page` batches) instead of loading every 300-DPI page into RAM; report real per-page progress. _Pages now render one at a time and OCR starts on each page as soon as it is written (rendering and OCR overlap). 30-page PDF: peak memory 2.3 GB → 0.54 GB, first page text 17 s → 4 s, total 47.5 s → 39.8 s. Memory stays ~0.55 GB at 100 pages. Cancelling mid-render is now immediate._
- [x] Set `OMP_THREAD_LIMIT=1` when running parallel Tesseract processes to avoid CPU oversubscription. _Measured 28.5 s → 20.9 s for 8 A4 pages with 4 workers. Raising the pool beyond 4 workers gave no further gain, so it stays at 4._
- [x] `Image.MAX_IMAGE_PIXELS = None` disables Pillow's limit, making the low-DPI fallback dead code — use a large finite limit or remove the fallback. _Limit is now 150 MP; an oversized page is retried per page at 200 then 150 DPI._
- [x] Replace `print()` error handling with `logging` to a file in the user's config dir, plus `QMessageBox` for user-facing errors (the windowed build has no console). _Rotating `tamil-ocr.log` in the app data dir (e.g. `~/.local/share/Tamil-OCR/`); dialogs for PDF conversion failure, OCR failing on every page, and export failure; uncaught exceptions are logged and shown instead of aborting the app._
- [x] `resizeEvent` calls `fit_view()` on every resize, discarding the user's zoom. _Refits only while the user hasn't zoomed (also on splitter drags); an OCR result arriving for the current page no longer resets zoom either._
- [x] Deduplicate `extract_text_lines` (two identical copies) into one module-level function.
- [x] `OCRSignals` / `OCRManager` should subclass `QObject`, not `QWidget`.
- [x] `resource_path()` prints an "Error" on every call when running from source — use `getattr(sys, "_MEIPASS", ...)`. _Also resolves relative to `main.py` instead of the current directory._

## 🟢 Features

- [ ] Image preprocessing (grayscale, binarisation, deskew) and a `--psm` page-segmentation option.
- [ ] Export searchable PDF via `pytesseract.image_to_pdf_or_hocr(..., extension='pdf')`.
- [x] Preserve paragraph breaks (blank line between Tesseract blocks/paragraphs).
- [x] Keyboard shortcuts (Ctrl+O, Ctrl+S, PgUp/PgDn); disable Prev/Next at the first/last page. _Ctrl+O open, Ctrl+S / Ctrl+Shift+S save session, Ctrl+E export text, Ctrl+Q quit, Ctrl+R re-run OCR, Ctrl+Shift+R reset page text, Ctrl+PgUp/PgDn or Alt+←/→ change page, Ctrl+Home/End first/last page, Ctrl+= / Ctrl+- / Ctrl+0 zoom, Ctrl+H highlights. Plain PgUp/PgDn work in the image view (scroll a zoomed page, then change page) and keep scrolling text in the editor._
- [x] Language picker populated from the `.traineddata` files in `tessdata/`. _Multi-select menu; tick order sets the primary language; at least one stays selected._
- [x] Save/restore a proofreading session. _`.tamilocr` file (JSON) with the OCR results, edits, current page, languages and confidence. Restoring re-renders page images but skips OCR. Finds the document if it was moved together with the session, warns if the document changed, and asks to save unsaved edits on close/open._

## 📦 Repo & packaging

- [ ] `.git` is ~523 MB: move release binaries to GitHub Releases; drop poppler headers/man pages and Tesseract training tools from `binary/`.
- [ ] GitHub Actions workflow to build Linux + Windows executables with PyInstaller and attach them to a Release.
- [x] README: fix `your-username` clone URL, add Windows build steps, document `poppler-utils` requirement on Linux. _Rewritten: usage guide, sessions, shortcut table, adding languages, Linux (`poppler-utils`, `libxcb-cursor0`, FUSE 2) and Windows prerequisites and build commands, log location, troubleshooting. The Linux build command was test-built; the Windows one is untested._
- [x] Pin versions in `requirements.txt`; remove `.idea/` from git; single source for the app version. _PyQt6 6.12.1, Pillow 13.0.0, pytesseract 0.3.13, pdf2image 1.17.0 (Python 3.11+, required by Pillow 13); verified with a clean install. `APP_VERSION` in `main.py` is 1.1.0._
- [x] Unit tests for text-line extraction and confidence filtering. _pytest suite in `tests/` (105 tests, ~70 s): text extraction, session files, languages, OCR manager job logic, PDF worker, and the main window (bug regressions, edits, export, view, shortcuts, sessions) plus end-to-end OCR. Most window tests inject OCR results so they need no Tesseract; tests needing Poppler/Tesseract skip when those are missing. Checked that reintroducing old bugs makes tests fail. Also fixed: a failed session save left a stray `.tmp` file._
