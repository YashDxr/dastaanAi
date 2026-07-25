"""Turn an uploaded document into plain text.

Two paths through a PDF. Most files carry a text layer and `pypdf` reads it for
free; scans carry only pixels and need OCR, which is roughly a thousand times
slower. The choice is made per document by measuring how much text the cheap
path actually recovered, because a PDF gives no reliable flag for it - a scanned
page and an empty page look identical to a parser.

Everything here runs before a model sees the text, and the caller moderates the
result, so nothing in this module trusts its input beyond parsing it.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

import structlog

log = structlog.get_logger(__name__)

# Below this many characters per page the text layer is assumed to be absent or
# useless (a scan with a stray watermark, say) and OCR takes over. A genuinely
# sparse page - a chapter heading alone - costs one needless OCR pass, which is
# cheaper than silently importing a blank story.
MIN_CHARS_PER_PAGE = 80

# OCR is CPU-bound and runs on a shared worker. Fifty pages is already a couple
# of minutes; past that the upload is a book, not a story.
MAX_OCR_PAGES = 50
OCR_DPI = 200

# Tesseract language packs installed in the image. Anything else falls back to
# English rather than failing, since a wrong pack still beats no text.
OCR_LANGUAGES = {"en": "eng", "hi": "hin"}


class ExtractionError(Exception):
    """The file cannot be read. The message reaches the user, so it explains
    what to do rather than what broke."""


class Method(StrEnum):
    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    DOCX = "docx"
    PLAIN_TEXT = "plain_text"


@dataclass(frozen=True)
class Extraction:
    text: str
    method: Method
    page_count: int


# --- content types ---------------------------------------------------------

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOC = "application/msword"

IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/tiff"})
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})

SUPPORTED_TYPES = frozenset({PDF, DOCX}) | IMAGE_TYPES | TEXT_TYPES

# Magic bytes, checked instead of the declared content type. A browser will
# happily label anything, and the extractors are the wrong place to discover a
# mislabelled file.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", PDF),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", DOC),  # OLE2: legacy .doc
)


def sniff(data: bytes, declared: str | None = None) -> str:
    """The file's real content type.

    DOCX and WEBP both need a second look: DOCX is a zip and WEBP hides behind a
    RIFF header, so neither is identifiable from a fixed prefix alone.
    """
    for signature, content_type in _SIGNATURES:
        if data.startswith(signature):
            return content_type

    if data.startswith(b"PK\x03\x04"):
        # Any OOXML file is a zip. Only Word documents have this part.
        return DOCX if b"word/document.xml" in data[:8192] or _is_docx(data) else "application/zip"

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"

    if declared in TEXT_TYPES or _looks_like_text(data):
        return "text/plain"

    return declared or "application/octet-stream"


def _is_docx(data: bytes) -> bool:
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return "word/document.xml" in archive.namelist()
    except Exception:
        return False


def _looks_like_text(data: bytes) -> bool:
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


# --- entry point -----------------------------------------------------------


def extract(data: bytes, content_type: str, *, language: str = "en") -> Extraction:
    """Plain text from an uploaded file, with the method that produced it."""
    actual = sniff(data, content_type)

    if actual == DOC:
        raise ExtractionError(
            "Legacy .doc files are not supported. Open the file and save it as "
            ".docx or PDF, then upload again."
        )
    if actual == PDF:
        return _from_pdf(data, language=language)
    if actual == DOCX:
        return Extraction(_from_docx(data), Method.DOCX, 1)
    if actual in IMAGE_TYPES:
        return Extraction(_ocr_image_bytes(data, language=language), Method.OCR, 1)
    if actual in TEXT_TYPES or actual == "text/plain":
        return Extraction(_decode(data), Method.PLAIN_TEXT, 1)

    raise ExtractionError(
        "That file type is not supported. Upload a PDF, Word document (.docx), "
        "image, or plain text file."
    )


# --- pdf -------------------------------------------------------------------


def _from_pdf(data: bytes, *, language: str) -> Extraction:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError("That PDF could not be opened; it may be corrupt.") from exc

    if reader.is_encrypted and not _try_empty_password(reader):
        raise ExtractionError(
            "That PDF is password protected. Remove the password and upload again."
        )

    pages = [_page_text(page) for page in reader.pages]
    page_count = len(pages)
    if page_count == 0:
        raise ExtractionError("That PDF has no pages.")

    text = strip_boilerplate(pages)
    density = len(text) / page_count

    if density >= MIN_CHARS_PER_PAGE:
        return Extraction(text, Method.TEXT_LAYER, page_count)

    log.info("pdf_text_layer_sparse", pages=page_count, chars_per_page=round(density, 1))
    return Extraction(_ocr_pdf(data, language=language), Method.OCR, min(page_count, MAX_OCR_PAGES))


def _try_empty_password(reader: object) -> bool:
    """Print-protected PDFs are encrypted with an empty user password, which is
    free to try and covers most files that report as encrypted."""
    try:
        return bool(reader.decrypt(""))  # type: ignore[attr-defined]
    except Exception:
        return False


def _page_text(page: object) -> str:
    try:
        return page.extract_text() or ""  # type: ignore[attr-defined]
    except Exception:
        log.warning("pdf_page_extract_failed", exc_info=True)
        return ""


def _ocr_pdf(data: bytes, *, language: str) -> str:
    from pdf2image import convert_from_bytes
    from pdf2image.exceptions import PDFInfoNotInstalledError

    try:
        images = convert_from_bytes(
            data, dpi=OCR_DPI, first_page=1, last_page=MAX_OCR_PAGES, fmt="png"
        )
    except PDFInfoNotInstalledError as exc:
        raise ExtractionError(
            "OCR is unavailable on this server (poppler is not installed)."
        ) from exc
    except Exception as exc:
        raise ExtractionError("That PDF could not be rendered for OCR.") from exc

    pages = [_ocr_image(image, language=language) for image in images]
    text = strip_boilerplate(pages)
    if not text.strip():
        raise ExtractionError(
            "No readable text was found in that PDF, even after OCR. It may be a "
            "photograph or a very low-quality scan."
        )
    return text


# --- ocr -------------------------------------------------------------------


def _tesseract_lang(language: str) -> str:
    return OCR_LANGUAGES.get(language.split("-")[0].lower(), "eng")


def _ocr_image(image: object, *, language: str) -> str:
    import pytesseract

    try:
        return pytesseract.image_to_string(image, lang=_tesseract_lang(language))
    except pytesseract.TesseractNotFoundError as exc:
        raise ExtractionError(
            "OCR is unavailable on this server (tesseract is not installed)."
        ) from exc
    except Exception:
        log.warning("ocr_page_failed", exc_info=True)
        return ""


def _ocr_image_bytes(data: bytes, *, language: str) -> str:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            text = _ocr_image(image, language=language)
    except UnidentifiedImageError as exc:
        raise ExtractionError("That image could not be opened.") from exc

    if not text.strip():
        raise ExtractionError("No readable text was found in that image.")
    return text


# --- docx ------------------------------------------------------------------


def _from_docx(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(
            "That Word document could not be opened; it may be corrupt or not a .docx file."
        ) from exc

    blocks = [paragraph.text for paragraph in document.paragraphs]
    # Dialogue is sometimes laid out in a two-column table (speaker | line), so
    # tables are read rather than skipped.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append(" ".join(cells))

    text = strip_boilerplate(blocks)
    if not text.strip():
        raise ExtractionError("That Word document appears to be empty.")
    return text


# --- plain text ------------------------------------------------------------


def _decode(data: bytes) -> str:
    from charset_normalizer import from_bytes

    best = from_bytes(data).best()
    text = str(best) if best else data.decode("utf-8", errors="replace")
    cleaned = strip_boilerplate([text])
    if not cleaned.strip():
        raise ExtractionError("That file appears to be empty.")
    return cleaned


# --- cleanup ---------------------------------------------------------------

# A line that is nothing but a page number, in arabic or roman numerals, with or
# without the dashes and brackets typesetters wrap them in.
_PAGE_NUMBER = re.compile(
    r"^\s*(?:page\s+)?[-–—\[(]*\s*(?:\d{1,4}|[ivxlcdm]{1,7})\s*[-–—\])]*\s*$", re.I
)
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_BLANK_RUN = re.compile(r"\n{3,}")
_SPACE_RUN = re.compile(r"[ \t]{2,}")


def strip_boilerplate(pages: list[str]) -> str:
    """Join pages, dropping the furniture that surrounds the prose.

    Running headers and footers are found by frequency rather than position: a
    line that appears near the top or bottom of most pages is chrome, whatever it
    says. Two pages are too few to tell a repeated header from a repeated line of
    dialogue, so the rule only applies from three pages up.
    """
    if not pages:
        return ""

    repeated = _repeated_lines(pages) if len(pages) >= 3 else set()

    kept_pages: list[str] = []
    for page in pages:
        lines = [
            line.rstrip()
            for line in page.splitlines()
            if not _PAGE_NUMBER.match(line) and line.strip() not in repeated
        ]
        body = "\n".join(lines).strip()
        if body:
            kept_pages.append(body)

    text = "\n\n".join(kept_pages)
    # A word split across a line break by justification is one word, not two.
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _SPACE_RUN.sub(" ", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def _repeated_lines(pages: list[str]) -> set[str]:
    """Lines appearing near the edge of at least 60% of pages."""
    counts: Counter[str] = Counter()
    for page in pages:
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        # Only the first and last few lines can be a header or footer. A phrase
        # repeated mid-page is a refrain and belongs to the story - so the window
        # narrows on short pages, where taking two from each end would otherwise
        # swallow the body and delete the very text we are here to keep.
        window = 2 if len(lines) >= 5 else 1
        edges = {*lines[:window], *lines[-window:]}
        counts.update(edge for edge in edges if len(edge) < 120)

    threshold = max(3, int(len(pages) * 0.6))
    return {line for line, count in counts.items() if count >= threshold}
