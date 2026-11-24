"""Build a single-file Tamil OCR executable with PyInstaller.

Usage:  python build.py [--name NAME]

Bundles the Tamil font, every model in tessdata/ and the Tesseract (and, on
Windows, Poppler) binaries the app runs. The result is written to dist/.
Run it on each platform you release for (Linux and Windows).
"""
import argparse
import glob
import os
import platform
import sys

import PyInstaller.__main__

ROOT = os.path.dirname(os.path.abspath(__file__))


def rel(*parts):
    return os.path.join(ROOT, *parts)


def bundle(entries, source, dest):
    """Add a file (or files matching a glob) to the bundle under dest/."""
    matches = sorted(glob.glob(source))
    if not matches:
        sys.exit(f"build.py: nothing matches {source}")
    for path in matches:
        entries.append(f"{path}{os.pathsep}{dest}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="Tamil-OCR", help="executable name (default: Tamil-OCR)")
    args = parser.parse_args()

    data, binaries = [], []
    bundle(data, rel("font", "marutham.ttf"), "font")
    bundle(data, rel("tessdata", "*.traineddata"), "tessdata")

    system = platform.system()
    if system == "Windows":
        tess = os.path.join("binary", "windows", "tesseract")
        bundle(binaries, rel(tess, "tesseract.exe"), tess)
        bundle(binaries, rel(tess, "*.dll"), tess)
        pop = os.path.join("binary", "windows", "poppler", "Library", "bin")
        for tool in ("pdftoppm.exe", "pdfinfo.exe", "*.dll"):
            bundle(binaries, rel(pop, tool), pop)
    elif system == "Linux":
        # Poppler comes from the system (poppler-utils)
        tess = os.path.join("binary", "linux", "tesseract")
        bundle(binaries, rel(tess, "tesseract.AppImage"), tess)
    else:
        sys.exit(f"build.py: unsupported platform {system}")

    pyinstaller_args = [
        rel("main.py"),
        "--name", args.name,
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--distpath", rel("dist"),
        "--workpath", rel("build"),
        "--specpath", rel("build"),
    ]
    for entry in data:
        pyinstaller_args += ["--add-data", entry]
    for entry in binaries:
        pyinstaller_args += ["--add-binary", entry]

    PyInstaller.__main__.run(pyinstaller_args)
    exe = rel("dist", args.name + (".exe" if system == "Windows" else ""))
    if system != "Windows":
        os.chmod(exe, 0o755)
    print(f"\nBuilt {exe} ({os.path.getsize(exe) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
