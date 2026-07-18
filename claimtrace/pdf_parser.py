"""Deterministic PDF-to-source-registry parsing with PyMuPDF."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import fitz  # PyMuPDF

from .exceptions import PDFParsingError
from .models import PaperDocument, SourceKind, SourceUnit


_CAPTION_RE = re.compile(
    r"^(?:extended\s+data\s+|supplement(?:ary|al)\s+)?"
    r"(?P<kind>fig(?:ure)?\.?|table)\s*(?P<label>[sS]?\d+[A-Za-z]?)\b",
    re.IGNORECASE,
)

_METHOD_SECTIONS = {
    "method",
    "methods",
    "materials and methods",
    "experimental procedures",
    "methodology",
    "study design",
    "statistical analysis",
    "star methods",
}

_KNOWN_SECTIONS = _METHOD_SECTIONS | {
    "abstract",
    "summary",
    "introduction",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "limitations",
    "references",
    "acknowledgments",
}

_STAT_PATTERNS = [
    re.compile(r"\bp\s*[<=>]\s*0?\.\d+", re.IGNORECASE),
    re.compile(r"\b(?:95%\s*)?CI\b", re.IGNORECASE),
    re.compile(r"\b(?:hazard|odds|risk)\s+ratio\b|\b(?:HR|OR|RR)\s*[=:]", re.IGNORECASE),
    re.compile(r"\bn\s*=\s*\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:mean|median)\s*(?:±|\+/-)", re.IGNORECASE),
    re.compile(r"\b(?:SD|SEM|IQR)\b", re.IGNORECASE),
]


def _clean_text(text: str) -> str:
    """Normalize extraction whitespace without paraphrasing paper content."""

    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def _is_heading(text: str) -> bool:
    normalized = text.strip().rstrip(":").lower()
    if normalized in _KNOWN_SECTIONS:
        return True
    if len(text) > 100 or len(text.split()) > 10:
        return False
    if text.endswith((".", ";", ",")):
        return False
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and (text.isupper() or text.istitle())


def _caption_metadata(text: str) -> tuple[SourceKind, str] | None:
    match = _CAPTION_RE.match(text)
    if not match:
        return None
    raw_kind = match.group("kind").lower()
    kind = SourceKind.TABLE if raw_kind.startswith("table") else SourceKind.FIGURE
    label_prefix = "Table" if kind == SourceKind.TABLE else "Figure"
    return kind, f"{label_prefix} {match.group('label')}"


def _classify_source(text: str, section: str) -> tuple[SourceKind, str]:
    caption = _caption_metadata(text)
    if caption:
        return caption

    normalized_section = section.strip().rstrip(":").lower()
    if normalized_section in _METHOD_SECTIONS or "method" in normalized_section:
        return SourceKind.METHOD, section or "Methods"

    stat_hits = sum(bool(pattern.search(text)) for pattern in _STAT_PATTERNS)
    if stat_hits >= 1:
        return SourceKind.STATISTICAL_RESULT, "Statistical result"

    return SourceKind.PARAGRAPH, "Paragraph"


def parse_pdf(pdf_bytes: bytes, filename: str = "paper.pdf") -> PaperDocument:
    """Parse a PDF into one-based, page-preserving source units.

    The parser intentionally does not use OCR.  Failing explicitly for scanned
    papers is safer than pretending an empty extraction is complete.
    """

    if not pdf_bytes:
        raise PDFParsingError("The uploaded PDF is empty.")
    if b"%PDF" not in pdf_bytes[:1024]:
        raise PDFParsingError("The uploaded file does not have a valid PDF signature.")

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # PyMuPDF exposes several parser-specific errors.
        raise PDFParsingError(f"PyMuPDF could not open this PDF: {exc}") from exc

    try:
        if document.needs_pass:
            raise PDFParsingError("Password-protected PDFs are not supported in this MVP.")
        if document.page_count < 1:
            raise PDFParsingError("The PDF contains no pages.")

        sources: list[SourceUnit] = []
        pages_with_text = 0
        current_section = "Unspecified"

        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            blocks = page.get_text("blocks", sort=True)
            page_has_text = False
            source_block_index = 0

            for block in blocks:
                # PyMuPDF block layout: x0, y0, x1, y1, text, block_no, block_type.
                if len(block) >= 7 and int(block[6]) != 0:
                    continue
                text = _clean_text(str(block[4]))
                if len(text) < 2:
                    continue

                page_has_text = True
                if _is_heading(text):
                    current_section = text.strip().rstrip(":")

                source_block_index += 1
                kind, label = _classify_source(text, current_section)
                source_id = f"p{page_index + 1:03d}-b{source_block_index:03d}"
                sources.append(
                    SourceUnit(
                        source_id=source_id,
                        page_number=page_index + 1,
                        block_index=source_block_index,
                        kind=kind,
                        section=current_section,
                        label=label,
                        excerpt=text,
                        bbox=[round(float(value), 2) for value in block[:4]],
                    )
                )

            if page_has_text:
                pages_with_text += 1

        if not sources:
            raise PDFParsingError(
                "No extractable text was found. This appears to be a scanned PDF; "
                "run OCR first, then upload the searchable PDF."
            )
        if pages_with_text / document.page_count < 0.5:
            raise PDFParsingError(
                "Fewer than half of the pages contain extractable text. Run OCR first "
                "so ClaimTrace can preserve complete, page-level provenance."
            )

        safe_name = re.sub(r"[\r\n\t]", "_", Path(filename).name)[:255] or "paper.pdf"
        return PaperDocument(
            filename=safe_name,
            sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            page_count=document.page_count,
            sources=sources,
        )
    finally:
        document.close()


def build_source_packet(document: PaperDocument) -> str:
    """Serialize the immutable source registry for the model prompt."""

    chunks = []
    for source in document.sources:
        header = (
            f"[{source.source_id} | page={source.page_number} | "
            f"kind={source.kind.value} | section={source.section}]"
        )
        chunks.append(f"{header}\n{source.excerpt}")
    return "\n\n".join(chunks)
