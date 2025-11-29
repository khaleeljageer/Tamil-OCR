# Use a Debian base image that's likely compatible with a "Fresh Debian OS"
FROM debian:bullseye-slim

# Set a working directory
WORKDIR /app

# Install necessary system dependencies for PyQt6, pdf2image, and Pillow
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    poppler-utils \
    libgl1-mesa-glx \
    libxcb-xinerama0 \
    build-essential \
    libx11-xcb-dev \
    libglib2.0-0 \
    libegl1 \
    libxkbcommon0 \
    # Add any other required system libraries here
    && rm -rf /var/lib/apt/lists/*

# Copy your application source code
COPY . .

# Install Python dependencies from requirements.txt
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# Install PyInstaller
RUN python3 -m pip install --no-cache-dir pyinstaller

# Make the tesseract AppImage executable
RUN chmod +x tesseract/tesseract.AppImage

# Run PyInstaller to build the executable
RUN pyinstaller main.py --name Tamil-OCR --onefile --windowed --add-data "tessdata:tessdata" --add-binary "tesseract/tesseract.AppImage:tesseract" --hidden-import=pkgutil --hidden-import=PIL

# Set the command to run the executable
CMD ["dist/Tamil-OCR"]
