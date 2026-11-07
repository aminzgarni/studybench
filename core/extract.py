"""Turn uploaded files into Markdown text (with locators like '## Slide 3' / '## Page 5')."""
import hashlib
import importlib.util
import shutil
from pathlib import Path
from types import SimpleNamespace

AUDIO = {".mp3", ".m4a", ".wav", ".ogg", ".oga", ".flac", ".aac", ".opus", ".wma", ".amr",
         ".3gp", ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi"}
IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
TEXT = {".txt", ".md", ".markdown", ".csv", ".vtt", ".srt"}
LEGACY = {".ppt", ".doc", ".xls", ".xlsx", ".odt", ".odp", ".rtf", ".pages", ".key"}


def classify(path):
    e = Path(path).suffix.lower()
    if e == ".pptx":
        return "slides"
    if e == ".docx":
        return "document"
    if e == ".pdf":
        return "pdf"
    if e in AUDIO:
        return "audio"
    if e in IMAGE:
        return "image"
    if e in TEXT:
        return "text"
    return "unsupported"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ocr_available():
    from . import setup
    return bool(setup.tesseract_cmd()) and importlib.util.find_spec("pytesseract") is not None \
        and importlib.util.find_spec("PIL") is not None


def ocr_image(img):
    from . import setup
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = setup.tesseract_cmd()
    cfg, lang = "", "eng"
    if (setup.TESSDATA / "deu.traineddata").exists() and (setup.TESSDATA / "eng.traineddata").exists():
        cfg, lang = f'--tessdata-dir "{setup.TESSDATA}"', "deu+eng"
    return pytesseract.image_to_string(img, lang=lang, config=cfg)


def _res(text="", status="ready", msg=""):
    return SimpleNamespace(text=text, status=status, msg=msg)


# ---------------------------------------------------------------- PPTX
def _shape_text(shape, out):
    try:
        if shape.shape_type == 6:                       # group
            for s in shape.shapes:
                _shape_text(s, out)
            return
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    out.append(" | ".join(cells))
            return
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            lines = [p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()]
            if lines:
                out.append("\n".join("- " + l if len(lines) > 1 else l for l in lines))
    except Exception:
        pass


def extract_pptx(path):
    from pptx import Presentation
    prs = Presentation(str(path))
    parts, pics = [], 0
    for i, slide in enumerate(prs.slides, 1):
        title = ""
        try:
            if slide.shapes.title is not None:
                title = slide.shapes.title.text.strip()
        except Exception:
            pass
        body = []
        for sh in slide.shapes:
            if sh.shape_type == 13:
                pics += 1
            if title and getattr(sh, "has_text_frame", False) and sh.has_text_frame \
                    and sh.text_frame.text.strip() == title:
                continue
            _shape_text(sh, body)
        notes = ""
        try:
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
        except Exception:
            pass
        block = f"## Slide {i}" + (f": {title}" if title else "") + "\n"
        block += "\n".join(body)
        if notes:
            block += f"\n\n*Speaker notes:* {notes}"
        parts.append(block.strip())
    text = "\n\n".join(parts)
    msg = f"{len(prs.slides)} slides" + (f", {pics} pictures not read" if pics else "")
    return _res(text, "ready" if text.strip() else "needs_ocr", msg)


# ---------------------------------------------------------------- DOCX
def extract_docx(path):
    import docx
    from docx.table import Table
    d = docx.Document(str(path))
    out = []
    for block in d.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                cells = []
                for c in row.cells:
                    t = c.text.strip().replace("\n", " ")
                    if not cells or cells[-1] != t:
                        cells.append(t)
                if any(cells):
                    out.append(" | ".join(cells))
            out.append("")
            continue
        t = block.text.strip()
        if not t:
            continue
        style = (block.style.name or "") if block.style is not None else ""
        if style.startswith("Heading") or style == "Title":
            digits = "".join(ch for ch in style if ch.isdigit())
            lvl = min(int(digits or 1), 4)
            out.append("\n" + "#" * (lvl + 1) + " " + t)
        elif "List" in style:
            out.append("- " + t)
        else:
            out.append(t)
    return _res("\n".join(out).strip(), msg="")


# ---------------------------------------------------------------- PDF
def extract_pdf(path):
    from pypdf import PdfReader
    r = PdfReader(str(path))
    if r.is_encrypted:
        try:
            ok = r.decrypt("")
        except Exception as e:  # noqa: BLE001
            return _res("", "error", f"Encrypted PDF could not be opened: {e}")
        if not ok:
            return _res("", "error", "PDF is password-protected. Remove the password and upload it again.")
    pages = []
    for i, p in enumerate(r.pages, 1):
        try:
            t = (p.extract_text() or "").strip()
        except Exception:
            t = ""
        pages.append(t)
    n = len(pages)
    chars = sum(len(p) for p in pages)
    if chars < 40 * max(n, 1):                       # looks scanned
        if ocr_available() and importlib.util.find_spec("pypdfium2"):
            try:
                import pypdfium2 as pdfium
                pdf = pdfium.PdfDocument(str(path))
                pages = []
                for i in range(len(pdf)):
                    img = pdf[i].render(scale=2).to_pil()
                    pages.append(ocr_image(img).strip())
                return _res(_pdf_md(pages), "ready", f"{n} pages (OCR)")
            except Exception as e:  # noqa: BLE001
                return _res("", "needs_ocr", f"Scanned PDF, OCR failed: {e}")
        if chars < 40:
            return _res("", "needs_ocr",
                        "Scanned PDF without text layer. Install Tesseract + pytesseract + pypdfium2 for OCR.")
    return _res(_pdf_md(pages), "ready", f"{n} pages")


def _pdf_md(pages):
    return "\n\n".join(f"## Page {i}\n{t}" for i, t in enumerate(pages, 1) if t.strip())


# ---------------------------------------------------------------- misc
def extract_text(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return _res(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return _res(raw.decode("utf-8", "replace"))


def extract_image(path):
    if not ocr_available():
        return _res("", "needs_ocr", "Image needs OCR: install Tesseract + `pip install pytesseract pillow`.")
    from PIL import Image
    t = ocr_image(Image.open(path)).strip()
    return _res(f"## Image\n{t}" if t else "", "ready" if t else "needs_ocr",
                "OCR" if t else "No text found in image")


def extract(path, kind):
    try:
        if kind == "slides":
            return extract_pptx(path)
        if kind == "document":
            return extract_docx(path)
        if kind == "pdf":
            return extract_pdf(path)
        if kind == "text":
            return extract_text(path)
        if kind == "image":
            return extract_image(path)
    except Exception as e:  # noqa: BLE001
        return _res("", "error", f"Could not read file: {e}")
    return _res("", "unsupported", "Unsupported file type")
