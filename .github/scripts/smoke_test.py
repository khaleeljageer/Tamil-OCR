"""Smoke-test a built Tamil OCR executable: open a generated 2-page PDF and wait for OCR.

Usage:  python .github/scripts/smoke_test.py <path-to-executable>

Passes when the app's log shows OCR completing on every page. This exercises the
bundled font, Tesseract, models and (on Windows) Poppler inside the frozen app.
"""
import os
import platform
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont

TIMEOUT = int(os.environ.get("SMOKE_TIMEOUT", "240"))  # seconds
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def log_path():
    if platform.system() == "Windows":
        return os.path.join(os.environ["LOCALAPPDATA"], "Tamil-OCR", "tamil-ocr.log")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "Tamil-OCR", "tamil-ocr.log")


def make_pdf(path):
    font = ImageFont.truetype(os.path.join(ROOT, "font", "marutham.ttf"), 44)
    pages = []
    for n in (1, 2):
        img = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(img)
        draw.text((60, 80), f"Smoke test page {n}", fill="black", font=font)
        draw.text((60, 160), "தமிழ் எழுத்து சோதனை", fill="black", font=font)
        pages.append(img)
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=150)


def stop_tree(proc):
    """Stop the launcher and the app process it started."""
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # the app runs in the launcher's session
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def main():
    exe = os.path.abspath(sys.argv[1])
    log = log_path()
    start = os.path.getsize(log) if os.path.exists(log) else 0

    work = tempfile.mkdtemp(prefix="smoke_")
    pdf = os.path.join(work, "smoke.pdf")
    make_pdf(pdf)

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    # A PyInstaller one-file executable is a launcher that runs the app as a child
    # process, so the whole process tree must be stopped at the end.
    if platform.system() == "Windows":
        proc = subprocess.Popen([exe, pdf], env=env, cwd=work,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        proc = subprocess.Popen([exe, pdf], env=env, cwd=work, start_new_session=True)
    text = ""
    try:
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            if os.path.exists(log):
                with open(log, encoding="utf-8", errors="replace") as f:
                    f.seek(start)
                    text = f.read()
                if "OCR processing complete" in text:
                    break
            time.sleep(1)
    finally:
        stop_tree(proc)

    print("---- app log ----")
    print(text or "(no log output)")
    print("-----------------")
    if "OCR processing complete" not in text:
        sys.exit("FAIL: OCR did not complete in the built executable")
    if "failed" in text.split("OCR processing complete", 1)[1].splitlines()[0]:
        sys.exit("FAIL: OCR completed but some pages failed")
    if "Custom font 'TAU-Marutham' loaded" not in text:
        sys.exit("FAIL: bundled Tamil font was not loaded")
    print("PASS: built executable opened the PDF and completed OCR on all pages")


if __name__ == "__main__":
    main()
