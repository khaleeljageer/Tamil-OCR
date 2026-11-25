"""Write a searchable PDF: each page image with an invisible text layer on top.

The text layer is written from OCR results the app already has (no second OCR run),
so it matches what the user sees: words under the confidence threshold are left out,
and pages the user edited carry the edited text.

Lines whose text is unchanged keep Tesseract's word positions. Edited lines are
stretched over the box of the OCR line they replace; when lines were merged or
split, they share the boxes of the lines they replace, and added lines go in the
gap next to their neighbours. Search and
copy always find the proofread text; only the highlight position is approximate.

The invisible text uses the same technique as Tesseract's own PDF renderer: a
"glyphless" font (font/pdf.ttf, from Tesseract) whose character codes are the text's
UTF-16 code units, with a ToUnicode map so viewers can extract the text.
"""
import difflib
import io
import os
import zlib
from dataclasses import dataclass, field

from PIL import Image

DEFAULT_DPI = 300  # assumed when an image has no (or an implausible) resolution
MIN_DPI = 70
MAX_PAGE_POINTS = 14400  # PDF viewers' page size limit (200 inches)
JPEG_QUALITY = 85
CHAR_WIDTH = 0.5  # glyph advance of the glyphless font, as a fraction of the font size


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int


@dataclass
class PageSpec:
    image_path: str
    lines: list = field(default_factory=list)  # OCR lines: lists of Word, in reading order
    text: str = ""  # final text of the page (OCR text or the user's edit)


def _union(boxes):
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[0] + b[2] for b in boxes)
    bottom = max(b[1] + b[3] for b in boxes)
    return left, top, right - left, bottom - top


def line_box(words):
    return _union([(w.left, w.top, w.width, w.height) for w in words])


def _split_rows(box, count):
    """Divide a box into `count` equal horizontal strips."""
    left, top, width, height = box
    step = height / count
    return [(left, top + k * step, width, step) for k in range(count)]


def _insert_region(boxes, i, count, page_box):
    """Where to put `count` added lines that come before OCR line i: in the gap between
    the neighbouring lines when there is room, otherwise over the neighbours."""
    prev = boxes[i - 1] if i > 0 else None
    nxt = boxes[i] if i < len(boxes) else None
    ref = prev or nxt
    top = prev[1] + prev[3] if prev else page_box[1]
    bottom = nxt[1] if nxt else page_box[1] + page_box[3]
    if bottom - top >= count * ref[3] / 2:
        height = min(bottom - top, count * ref[3])
        return ref[0], (top if prev else bottom - height), ref[2], height
    return _union([b for b in (prev, nxt) if b])


def layout_text(lines, text, page_box):
    """Decide where each line of the final text goes.

    Returns a list of lines; each line is a list of (string, box) runs, box being
    (left, top, width, height) in image pixels. Lines equal to an OCR line keep one
    run per word; other lines are a single run.
    """
    targets = [t.strip() for t in text.splitlines() if t.strip()]
    if not targets:
        return []
    if not lines:
        return [[(t, row)] for t, row in zip(targets, _split_rows(page_box, len(targets)))]

    ocr_texts = [' '.join(w.text for w in line) for line in lines]
    boxes = [line_box(line) for line in lines]
    matcher = difflib.SequenceMatcher(None, ocr_texts, targets, autojunk=False)
    result = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for line in lines[i1:i2]:
                result.append([(w.text, (w.left, w.top, w.width, w.height)) for w in line])
        elif tag == 'delete':
            continue
        else:
            new_lines = targets[j1:j2]
            if tag == 'replace' and i2 - i1 == j2 - j1:
                rows = boxes[i1:i2]
            elif tag == 'replace':
                # A different number of lines: share the boxes of the lines they replace
                rows = _split_rows(_union(boxes[i1:i2]), len(new_lines))
            else:
                rows = _split_rows(_insert_region(boxes, i1, len(new_lines), page_box), len(new_lines))
            result.extend([(t, row)] for t, row in zip(new_lines, rows))
    return result


def _utf16_hex(text):
    return text.encode('utf-16-be').hex().upper()


def _num(value):
    return f"{value:.2f}".rstrip('0').rstrip('.') or '0'


def _pdf_string(text):
    """A PDF text string (UTF-16 with BOM, so any language works in document properties)."""
    return f"<FEFF{_utf16_hex(text)}>"


def text_layer(layout, scale_x, scale_y, page_height):
    """Content stream operators drawing the layout as invisible text (render mode 3)."""
    ops = ["BT", "3 Tr"]
    for line in layout:
        _, line_top, _, line_height = _union([box for _, box in line])
        size = max(line_height * scale_y, 1.0)
        baseline = page_height - (line_top + line_height) * scale_y
        ops.append(f"/F0 {_num(size)} Tf")
        for k, (string, (left, _, width, _)) in enumerate(line):
            if k + 1 < len(line):
                # Include the gap to the next word, so a space is extracted between them
                string += ' '
                width = max(width, line[k + 1][1][0] - left)
            units = len(string.encode('utf-16-be')) // 2
            if units == 0 or width <= 0:
                continue
            stretch = 100 * width * scale_x / (units * CHAR_WIDTH * size)
            ops.append(f"{_num(stretch)} Tz 1 0 0 1 {_num(left * scale_x)} {_num(baseline)} Tm "
                       f"<{_utf16_hex(string)}> Tj")
    ops.append("ET")
    return '\n'.join(ops)


def _page_image(path):
    """JPEG data, pixel size, colour space and resolution of a page image.

    JPEG files are embedded as they are; anything else is converted to JPEG.
    """
    with Image.open(path) as image:
        size = image.size
        dpi = image.info.get('dpi')
        if image.format == 'JPEG' and image.mode in ('RGB', 'L'):
            mode = image.mode
            with open(path, 'rb') as f:
                data = f.read()
        else:
            mode = 'L' if image.mode in ('1', 'L', 'LA', 'I', 'I;16', 'F') else 'RGB'
            buffer = io.BytesIO()
            image.convert(mode).save(buffer, 'JPEG', quality=JPEG_QUALITY)
            data = buffer.getvalue()
    return data, size, ('/DeviceGray' if mode == 'L' else '/DeviceRGB'), dpi


def _resolution(dpi, pixels):
    """Usable DPI for one axis: the image's own if plausible, and never a page over the PDF limit."""
    try:
        value = float(dpi)
    except (TypeError, ValueError):
        value = 0.0
    if value < MIN_DPI:
        value = DEFAULT_DPI
    return max(value, pixels * 72 / MAX_PAGE_POINTS)


class _Writer:
    """Minimal PDF object writer that streams to a file and builds the xref table."""

    def __init__(self, f):
        self.f = f
        self.offsets = {}
        self.next_id = 1
        f.write(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")

    def reserve(self):
        oid = self.next_id
        self.next_id += 1
        return oid

    def obj(self, oid, body, stream=None):
        self.offsets[oid] = self.f.tell()
        if stream is not None:
            body = body.rstrip()
            if body.endswith('>>'):
                body = body[:-2] + f" /Length {len(stream)} >>"
        self.f.write(f"{oid} 0 obj\n{body}\n".encode('ascii'))
        if stream is not None:
            self.f.write(b"stream\n" + stream + b"\nendstream\n")
        self.f.write(b"endobj\n")
        return oid

    def add(self, body, stream=None):
        return self.obj(self.reserve(), body, stream)

    def finish(self, root, info):
        xref = self.f.tell()
        count = self.next_id
        lines = [f"xref\n0 {count}\n", "0000000000 65535 f \n"]
        lines += [f"{self.offsets[oid]:010d} 00000 n \n" for oid in range(1, count)]
        lines.append(f"trailer\n<< /Size {count} /Root {root} 0 R /Info {info} 0 R >>\n"
                     f"startxref\n{xref}\n%%EOF\n")
        self.f.write(''.join(lines).encode('ascii'))


TO_UNICODE_CMAP = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfrange
<0000> <FFFF> <0000>
endbfrange
endcmap
CMapName currentdict /CMap defineresource pop
end
end"""


def _write_font(w, font_file):
    with open(font_file, 'rb') as f:
        ttf = f.read()
    file_id = w.add(f"<< /Length1 {len(ttf)} >>", ttf)
    descriptor = w.add(
        "<< /Type /FontDescriptor /FontName /GlyphLessFont /Flags 5 /FontBBox [0 0 500 1000] "
        f"/ItalicAngle 0 /Ascent 1000 /Descent 0 /CapHeight 1000 /StemV 80 /FontFile2 {file_id} 0 R >>")
    # Every character code draws glyph 1 (an empty glyph) of the glyphless font
    gid_map = w.add("<< /Filter /FlateDecode >>", zlib.compress(b"\x00\x01" * 65536))
    cid_font = w.add(
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /GlyphLessFont "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        f"/FontDescriptor {descriptor} 0 R /CIDToGIDMap {gid_map} 0 R /DW {int(CHAR_WIDTH * 1000)} >>")
    to_unicode = w.add("<< /Filter /FlateDecode >>", zlib.compress(TO_UNICODE_CMAP))
    return w.add(
        "<< /Type /Font /Subtype /Type0 /BaseFont /GlyphLessFont /Encoding /Identity-H "
        f"/DescendantFonts [{cid_font} 0 R] /ToUnicode {to_unicode} 0 R >>")


def write_searchable_pdf(path, pages, font_file, title=None, producer=None,
                         progress=None, should_stop=None):
    """Write `pages` (PageSpec) to a searchable PDF at `path`.

    The file is written to a temporary name and moved into place at the end, so a
    failed or cancelled export never leaves a partial PDF. `progress(done, total)` is
    called after each page; when `should_stop()` returns true the export is abandoned.
    Returns True when the PDF was written, False when it was cancelled.
    """
    pages = list(pages)
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, 'wb') as f:
            w = _Writer(f)
            catalog, page_tree = w.reserve(), w.reserve()
            font = _write_font(w, font_file)
            kids = []
            for index, page in enumerate(pages):
                if should_stop is not None and should_stop():
                    raise _Cancelled()
                jpeg, (px_w, px_h), colour_space, dpi = _page_image(page.image_path)
                dpi_x, dpi_y = dpi if isinstance(dpi, tuple) and len(dpi) == 2 else (dpi, dpi)
                scale_x = 72 / _resolution(dpi_x, px_w)
                scale_y = 72 / _resolution(dpi_y, px_h)
                width, height = px_w * scale_x, px_h * scale_y

                image = w.add(f"<< /Type /XObject /Subtype /Image /Width {px_w} /Height {px_h} "
                              f"/ColorSpace {colour_space} /BitsPerComponent 8 /Filter /DCTDecode >>", jpeg)
                layout = layout_text(page.lines, page.text, (0, 0, px_w, px_h))
                content = (f"q {_num(width)} 0 0 {_num(height)} 0 0 cm /Im0 Do Q\n"
                           + text_layer(layout, scale_x, scale_y, height))
                contents = w.add("<< /Filter /FlateDecode >>", zlib.compress(content.encode('ascii')))
                kids.append(w.add(
                    f"<< /Type /Page /Parent {page_tree} 0 R /MediaBox [0 0 {_num(width)} {_num(height)}] "
                    f"/Resources << /XObject << /Im0 {image} 0 R >> /Font << /F0 {font} 0 R >> >> "
                    f"/Contents {contents} 0 R >>"))
                if progress is not None:
                    progress(index + 1, len(pages))

            w.obj(page_tree, f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                             f"/Count {len(kids)} >>")
            w.obj(catalog, f"<< /Type /Catalog /Pages {page_tree} 0 R >>")
            info = []
            if title:
                info.append(f"/Title {_pdf_string(title)}")
            if producer:
                info.append(f"/Producer {_pdf_string(producer)}")
            info_id = w.add(f"<< {' '.join(info)} >>")
            w.finish(catalog, info_id)
        os.replace(tmp_path, path)
        return True
    except _Cancelled:
        _remove(tmp_path)
        return False
    except BaseException:
        _remove(tmp_path)
        raise


class _Cancelled(Exception):
    pass


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
