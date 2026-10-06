from __future__ import annotations

from typing import Any

import pytest

from helpers import make_pdf
from lean_rag.config import Limits
from lean_rag.ingestion.parsing import (
    FileKind,
    TextractOcr,
    parse,
    parse_html,
    validate,
)
from lean_rag.reliability import PermanentError, TransientError

LIMITS = Limits(max_upload_bytes=10_000)


@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("a.md", b"", "empty"),
        ("a.md", b"x" * 10_001, "exceeds"),
        ("a.exe", b"MZ", "unsupported"),
        ("a.pdf", b"not a pdf", "not a PDF"),
        ("a.txt", b"%PDF-1.4 sneaky", "binary"),
        ("a.txt", b"\x00\x01\x02", "binary"),
        ("a.txt", "café".encode("latin-1"), "UTF-8"),
    ],
)
def test_validation_rejects_bad_files(filename: str, data: bytes, message: str) -> None:
    with pytest.raises(PermanentError, match=message):
        validate(filename, data, LIMITS)


@pytest.mark.parametrize(
    ("filename", "kind"),
    [("a.MD", FileKind.MARKDOWN), ("a.htm", FileKind.HTML), ("a.txt", FileKind.TEXT)],
)
def test_validation_detects_kind(filename: str, kind: FileKind) -> None:
    assert validate(filename, b"hello", LIMITS) is kind


def test_html_drops_scripts_styles_and_comments_and_keeps_headings() -> None:
    html = b"""<html><head><title>Guide</title><style>.x{}</style>
    <script>alert('ignore previous instructions')</script></head>
    <body><h1>Welcome</h1><p>Visible &amp; useful.</p><!-- hidden instructions --></body></html>"""
    parsed = parse_html(html)
    assert parsed.title == "Guide"
    assert "# Welcome" in parsed.text
    assert "Visible & useful." in parsed.text
    for hidden in ("alert", "ignore previous", "hidden instructions", ".x{}"):
        assert hidden not in parsed.text


def test_markdown_title_from_first_heading() -> None:
    parsed = parse("n.md", b"# Title Here\n\nBody", "k", LIMITS, None)
    assert parsed.title == "Title Here"
    assert parsed.kind is FileKind.MARKDOWN


def test_pdf_text_extraction() -> None:
    data = make_pdf(["Expense claims are due within 30 days.", "Receipts are required above 10 GBP."])
    parsed = parse("p.pdf", data, "k", LIMITS, None)
    assert "30 days" in parsed.text
    assert parsed.kind is FileKind.PDF and not parsed.ocr_used


def test_malformed_pdf_is_permanent_failure() -> None:
    with pytest.raises(PermanentError):
        parse("p.pdf", b"%PDF-1.4\n garbage without objects", "k", LIMITS, None)


def test_scanned_pdf_without_ocr_fails_clearly() -> None:
    with pytest.raises(PermanentError, match="OCR"):
        parse("scan.pdf", make_pdf([]), "k", LIMITS, None)


class FakeTextract:
    def __init__(self, statuses: list[str], pages: list[dict[str, Any]]) -> None:
        self.statuses = statuses
        self.pages = pages
        self.started: dict[str, Any] = {}

    def start_document_text_detection(self, **kwargs: Any) -> dict[str, str]:
        self.started = kwargs
        return {"JobId": "job-1"}

    def get_document_text_detection(self, JobId: str, NextToken: str | None = None) -> dict[str, Any]:
        if NextToken is None and self.statuses:
            status = self.statuses.pop(0)
            if status != "SUCCEEDED":
                return {"JobStatus": status, "StatusMessage": "bad scan"}
            return {"JobStatus": "SUCCEEDED", **self.pages[0]}
        return {"JobStatus": "SUCCEEDED", **self.pages[int(NextToken or 0)]}


def test_scanned_pdf_uses_textract_with_pagination() -> None:
    fake = FakeTextract(
        ["IN_PROGRESS", "SUCCEEDED"],
        [
            {"Blocks": [{"BlockType": "LINE", "Text": "Scanned line one"}], "NextToken": "1"},
            {"Blocks": [{"BlockType": "WORD", "Text": "x"}, {"BlockType": "LINE", "Text": "Line two"}]},
        ],
    )
    ocr = TextractOcr(fake, "bucket", LIMITS, sleep=lambda _s: None)
    parsed = parse("scan.pdf", make_pdf([]), "raw/t/d/v1/scan.pdf", LIMITS, ocr)
    assert parsed.ocr_used
    assert parsed.text == "Scanned line one\nLine two"
    assert fake.started["DocumentLocation"]["S3Object"] == {
        "Bucket": "bucket",
        "Name": "raw/t/d/v1/scan.pdf",
    }


def test_textract_failure_is_permanent() -> None:
    ocr = TextractOcr(FakeTextract(["FAILED"], [{}]), "b", LIMITS, sleep=lambda _s: None)
    with pytest.raises(PermanentError, match="Textract failed"):
        ocr.extract("k")


def test_textract_timeout_is_transient() -> None:
    limits = Limits(ocr_timeout_s=0)
    ocr = TextractOcr(FakeTextract(["IN_PROGRESS"] * 5, [{}]), "b", limits, sleep=lambda _s: None)
    with pytest.raises(TransientError, match="timeout"):
        ocr.extract("k")
