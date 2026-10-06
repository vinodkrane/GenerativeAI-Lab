"""File validation and deterministic parsing. No LLM is involved in choosing a parser."""

from __future__ import annotations

import io
import logging
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from lean_rag.config import Limits
from lean_rag.reliability import PermanentError, TransientError, with_retries

logger = logging.getLogger(__name__)


class FileKind(StrEnum):
    PDF = "pdf"
    HTML = "html"
    MARKDOWN = "markdown"
    TEXT = "text"


EXTENSIONS: dict[str, FileKind] = {
    ".pdf": FileKind.PDF,
    ".html": FileKind.HTML,
    ".htm": FileKind.HTML,
    ".md": FileKind.MARKDOWN,
    ".markdown": FileKind.MARKDOWN,
    ".txt": FileKind.TEXT,
}

CONTENT_TYPES: dict[FileKind, str] = {
    FileKind.PDF: "application/pdf",
    FileKind.HTML: "text/html",
    FileKind.MARKDOWN: "text/markdown",
    FileKind.TEXT: "text/plain",
}

MIN_CHARS_PER_PDF_PAGE = 25  # below this a PDF is treated as scanned and sent to OCR


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    title: str | None
    kind: FileKind
    pages: int = 1
    ocr_used: bool = False


def kind_for_filename(filename: str) -> FileKind:
    lower = filename.lower()
    for ext, kind in EXTENSIONS.items():
        if lower.endswith(ext):
            return kind
    raise PermanentError(f"unsupported file type: {filename}")


def validate(filename: str, data: bytes, limits: Limits) -> FileKind:
    if not data:
        raise PermanentError("empty file")
    if len(data) > limits.max_upload_bytes:
        raise PermanentError(f"file exceeds {limits.max_upload_bytes} bytes")
    kind = kind_for_filename(filename)
    is_pdf = data[:5] == b"%PDF-"
    if kind is FileKind.PDF and not is_pdf:
        raise PermanentError("file has .pdf extension but is not a PDF")
    if kind is not FileKind.PDF:
        if is_pdf or b"\x00" in data[:4096]:
            raise PermanentError("binary content in a text document")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PermanentError("text documents must be UTF-8") from exc
    return kind


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class _HtmlText(HTMLParser):
    """Visible text only. Scripts, styles, templates and comments are dropped, which also
    removes a common hiding place for prompt-injection payloads."""

    _SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "object"}
    _BLOCK = {"p", "div", "section", "article", "li", "tr", "br", "table", "ul", "ol", "header", "footer"}
    _HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str | None = None
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._HEADINGS:
            self.parts.append(f"\n\n{self._HEADINGS[tag]} ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self._HEADINGS:
            self.parts.append("\n\n")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title = (self.title or "") + data.strip()
            return
        self.parts.append(data)


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
        if stripped:
            return stripped[:120]
    return None


def parse_text(data: bytes, kind: FileKind) -> ParsedDocument:
    text = _normalise(data.decode("utf-8"))
    return ParsedDocument(text=text, title=_first_heading(text), kind=kind)


def parse_html(data: bytes) -> ParsedDocument:
    parser = _HtmlText()
    parser.feed(data.decode("utf-8"))
    parser.close()
    text = _normalise("".join(parser.parts))
    return ParsedDocument(text=text, title=parser.title or _first_heading(text), kind=FileKind.HTML)


def parse_pdf_text(data: bytes) -> tuple[ParsedDocument, bool]:
    """Returns the parsed text and whether the PDF looks scanned (needs OCR)."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PermanentError("encrypted PDF")
        pages = [page.extract_text() or "" for page in reader.pages]
        meta_title = reader.metadata.title if reader.metadata else None
    except PermanentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise PermanentError(f"malformed PDF: {exc}") from exc
    if not pages:
        raise PermanentError("PDF has no pages")
    text = _normalise("\n\n".join(pages))
    scanned = len(text) / len(pages) < MIN_CHARS_PER_PDF_PAGE
    title = str(meta_title) if meta_title else _first_heading(text)
    return ParsedDocument(text=text, title=title, kind=FileKind.PDF, pages=len(pages)), scanned


class TextractOcr:
    """Asynchronous Textract text detection for scanned PDFs stored in S3 (bounded polling)."""

    def __init__(self, client: Any, bucket: str, limits: Limits, sleep: Any = time.sleep) -> None:
        self.client = client
        self.bucket = bucket
        self.limits = limits
        self.sleep = sleep

    def extract(self, key: str) -> str:
        start = with_retries(
            lambda: self.client.start_document_text_detection(
                DocumentLocation={"S3Object": {"Bucket": self.bucket, "Name": key}}
            ),
            op="textract_start",
            max_retries=self.limits.max_retries,
        )
        job_id = start["JobId"]
        deadline = time.monotonic() + self.limits.ocr_timeout_s
        delay = 2.0
        while True:
            resp = with_retries(
                lambda: self.client.get_document_text_detection(JobId=job_id),
                op="textract_poll",
                max_retries=self.limits.max_retries,
            )
            status = resp["JobStatus"]
            if status == "SUCCEEDED":
                break
            if status == "FAILED":
                raise PermanentError(f"Textract failed: {resp.get('StatusMessage', 'unknown')}")
            if time.monotonic() >= deadline:
                raise TransientError("Textract job did not finish within the OCR timeout")
            self.sleep(delay)
            delay = min(delay * 1.5, 15.0)
        lines: list[str] = []
        token: str | None = None
        while True:
            page = (
                resp
                if token is None
                else self.client.get_document_text_detection(JobId=job_id, NextToken=token)
            )
            lines.extend(b["Text"] for b in page.get("Blocks", []) if b.get("BlockType") == "LINE")
            token = page.get("NextToken")
            if not token:
                break
        return "\n".join(lines)


def parse(
    filename: str, data: bytes, object_key: str, limits: Limits, ocr: TextractOcr | None
) -> ParsedDocument:
    kind = validate(filename, data, limits)
    if kind is FileKind.PDF:
        parsed, scanned = parse_pdf_text(data)
        if scanned:
            if ocr is None:
                raise PermanentError("scanned PDF needs OCR, which requires Textract (not available locally)")
            text = _normalise(ocr.extract(object_key))
            parsed = ParsedDocument(
                text=text,
                title=parsed.title or _first_heading(text),
                kind=kind,
                pages=parsed.pages,
                ocr_used=True,
            )
    elif kind is FileKind.HTML:
        parsed = parse_html(data)
    else:
        parsed = parse_text(data, kind)
    if not parsed.text.strip():
        raise PermanentError("document contains no extractable text")
    if len(parsed.text) > limits.max_document_chars:
        raise PermanentError(f"document exceeds {limits.max_document_chars} characters")
    return parsed
