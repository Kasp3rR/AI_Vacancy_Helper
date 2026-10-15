from pathlib import Path
from typing import BinaryIO

import pymupdf

from app.config import Settings


class InvalidPDF(ValueError):
    pass


class PDFTooLarge(InvalidPDF):
    pass


def read_bounded(file: BinaryIO, settings: Settings) -> bytes:
    data = file.read(settings.max_pdf_bytes + 1)
    if len(data) > settings.max_pdf_bytes:
        raise PDFTooLarge("PDF exceeds size limit")
    return data


def extract_text(data: bytes, settings: Settings) -> str:
    if len(data) > settings.max_pdf_bytes:
        raise PDFTooLarge("PDF exceeds size limit")
    if not data.startswith(b"%PDF-"):
        raise InvalidPDF("File is not a PDF")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:  # type: ignore[no-untyped-call]
            if document.needs_pass:
                raise InvalidPDF("Encrypted PDFs are not supported")
            if document.page_count > settings.max_pdf_pages:
                raise PDFTooLarge("PDF exceeds page limit")
            parts: list[str] = []
            length = 0
            for page in document:
                text = str(page.get_text()).strip()
                length += len(text) + 1
                if length > settings.max_text_chars:
                    raise PDFTooLarge("PDF exceeds extracted text limit")
                parts.append(text)
            result = "\n".join(parts).strip()
            if not result:
                raise InvalidPDF("PDF contains no extractable text; OCR is not supported")
            return result
    except InvalidPDF:
        raise
    except (RuntimeError, ValueError) as exc:
        raise InvalidPDF("PDF is damaged or unreadable") from exc


def load_text(path: Path, settings: Settings) -> str:
    with path.open("rb") as file:
        data = read_bounded(file, settings)
    return extract_text(data, settings)
