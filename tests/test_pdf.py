import io

import pymupdf
import pytest

from app.services.pdf import InvalidPDF, PDFTooLarge, extract_text, read_bounded


def test_pdf_extracts_text(pdf_bytes, settings):
    assert "Python Developer" in extract_text(pdf_bytes, settings)


def test_pdf_rejects_invalid_empty_and_scanned(settings):
    with pymupdf.open() as doc:
        doc.new_page()
        blank = doc.tobytes()
    for data in [b"", b"not a pdf", b"%PDF-1.7\ncorrupt", blank]:
        with pytest.raises(InvalidPDF):
            extract_text(data, settings)


def test_pdf_rejects_encryption(settings):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Private")
        encrypted = doc.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="password", owner_pw="owner-password"
        )
    with pytest.raises(InvalidPDF, match="Encrypted"):
        extract_text(encrypted, settings)


def test_pdf_size_boundary_and_bounded_read(settings, pdf_bytes):
    settings.max_pdf_bytes = len(pdf_bytes)
    assert read_bounded(io.BytesIO(pdf_bytes), settings) == pdf_bytes
    assert extract_text(pdf_bytes, settings)
    with pytest.raises(PDFTooLarge):
        read_bounded(io.BytesIO(pdf_bytes + b"x"), settings)
    with pytest.raises(PDFTooLarge):
        extract_text(pdf_bytes + b"x", settings)


def test_pdf_page_and_text_limits(settings):
    with pymupdf.open() as doc:
        for _ in range(2):
            doc.new_page().insert_text((72, 72), "Python " * 10)
        data = doc.tobytes()
    settings.max_pdf_pages = 1
    with pytest.raises(PDFTooLarge, match="page"):
        extract_text(data, settings)
    settings.max_pdf_pages = 2
    settings.max_text_chars = 10
    with pytest.raises(PDFTooLarge, match="text"):
        extract_text(data, settings)
