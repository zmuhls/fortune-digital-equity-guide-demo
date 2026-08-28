"""Current public calendar evidence for the Digital Equity Website Guide."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from html import unescape
import hashlib
import io
import re
import threading
import time
import urllib.parse
import urllib.request


CALENDAR_URL = "https://www.fortunedigitalequity.org/calendar"
ALLOWED_PAGE_HOSTS = {"fortunedigitalequity.org", "www.fortunedigitalequity.org"}
OFFICIAL_CALENDAR_PDF_PATH = re.compile(r"^/_files/ugd/[^/]+\.pdf$", re.I)
FILESUSR_CALENDAR_PDF_PATH = re.compile(r"^/ugd/[^/]+\.pdf$", re.I)
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PDF_PAGES = 12
MAX_EXTRACTED_CHARACTERS = 60_000


class CalendarRefreshError(RuntimeError):
    """The live public calendar could not be refreshed safely."""


def _allowed_url(url: str, hosts: set[str], *, pdf: bool = False) -> bool:
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in hosts
        and port in (None, 443)
        and not parsed.username
        and not parsed.password
        and (not pdf or parsed.path.lower().endswith(".pdf"))
    )


def _official_calendar_pdf_url(url: str) -> bool:
    """Whether a URL is the PDF route linked from Fortune's calendar page."""

    if not _allowed_url(url, ALLOWED_PAGE_HOSTS, pdf=True):
        return False
    parsed = urllib.parse.urlsplit(url)
    return bool(OFFICIAL_CALENDAR_PDF_PATH.fullmatch(urllib.parse.unquote(parsed.path)))


def _filesusr_calendar_pdf_url(url: str) -> bool:
    """Whether a redirect stays on Wix's bounded public PDF file route."""

    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
        port = parsed.port
    except ValueError:
        return False
    hostname = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and hostname.endswith(".filesusr.com")
        and port in (None, 443)
        and not parsed.username
        and not parsed.password
        and bool(FILESUSR_CALENDAR_PDF_PATH.fullmatch(urllib.parse.unquote(parsed.path)))
    )


def _allowed_calendar_pdf_result_url(url: str) -> bool:
    """Allow the original official PDF route or its bounded Wix file redirect."""

    return _official_calendar_pdf_url(url) or _filesusr_calendar_pdf_url(url)


def fetch_public_bytes(
    url: str,
    *,
    hosts: set[str],
    maximum: int,
    timeout: float = 8.0,
    allowed_initial_url: Callable[[str], bool] | None = None,
    allowed_final_url: Callable[[str], bool] | None = None,
) -> tuple[bytes, str, str]:
    """Fetch one bounded public source and validate its final URL."""

    initial_validator = allowed_initial_url or (lambda candidate: _allowed_url(candidate, hosts))
    final_validator = allowed_final_url or (lambda candidate: _allowed_url(candidate, hosts))
    if not initial_validator(url):
        raise CalendarRefreshError("calendar source URL is not allowed")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.1",
            "User-Agent": "DigitalEquityWebsiteGuide/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            if not final_validator(final_url):
                raise CalendarRefreshError("calendar source redirected outside the public site")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > maximum:
                raise CalendarRefreshError("calendar source is too large")
            body = response.read(maximum + 1)
            if len(body) > maximum:
                raise CalendarRefreshError("calendar source is too large")
            content_type = str(response.headers.get("Content-Type") or "")
    except CalendarRefreshError:
        raise
    except Exception as error:
        raise CalendarRefreshError("calendar source request failed") from error
    return body, final_url, content_type


def calendar_pdf_url(page_html: str, base_url: str = CALENDAR_URL) -> str:
    """Return the current public downloadable calendar linked by the page."""

    candidates = re.findall(
        r"\bhref\s*=\s*[\"']([^\"']+?\.pdf(?:\?[^\"']*)?)[\"']",
        str(page_html or ""),
        flags=re.I,
    )
    for raw in candidates:
        candidate = urllib.parse.urljoin(base_url, unescape(raw))
        if _official_calendar_pdf_url(candidate):
            return candidate
    raise CalendarRefreshError("calendar page has no allowed downloadable calendar")


def validate_pdf_response(pdf_bytes: bytes, content_type: str) -> None:
    """Reject HTML/error payloads before passing a calendar file to the PDF reader."""

    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if media_type != "application/pdf":
        raise CalendarRefreshError("downloadable calendar did not return a PDF content type")
    if not isinstance(pdf_bytes, (bytes, bytearray)) or b"%PDF-" not in bytes(pdf_bytes)[:1024]:
        raise CalendarRefreshError("downloadable calendar did not return PDF bytes")


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract bounded text from the public schedule PDF.

    Canva calendars frequently place weekday/date labels in a separate PDF
    column.  Layout extraction preserves readable class-title order but can
    omit that column; ordinary extraction preserves the labels but can move
    them later in the page.  Keep both source readings when they differ so a
    consumer can retain the schedule's dates without guessing.
    """

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        pages = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            layout = page.extract_text(extraction_mode="layout") or ""
            linear = page.extract_text() or ""
            readings = []
            for text in (layout, linear):
                text = re.sub(r"[\t\f\v ]+", " ", text)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if text and text not in readings:
                    readings.append(text)
            if readings:
                pages.append("\n\n".join(readings))
            if sum(len(value) for value in pages) >= MAX_EXTRACTED_CHARACTERS:
                break
    except Exception as error:
        raise CalendarRefreshError("downloadable calendar text could not be read") from error
    result = "\n\n".join(pages)[:MAX_EXTRACTED_CHARACTERS].strip()
    if len(result) < 80:
        raise CalendarRefreshError("downloadable calendar contains too little readable text")
    return result


def calendar_text_blocks(text: str, maximum: int = 900) -> list[str]:
    """Keep the schedule's source order while fitting it into retrieval blocks."""

    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    blocks = []
    current = []
    length = 0
    for line in lines:
        separator = 1 if current else 0
        if current and length + separator + len(line) > maximum:
            blocks.append("\n".join(current))
            current = []
            length = 0
            separator = 0
        current.append(line[:maximum])
        length += separator + min(len(line), maximum)
    if current:
        blocks.append("\n".join(current))
    return blocks


def fetch_live_calendar_source(base_source: dict, timeout: float = 8.0) -> dict:
    """Merge the current public schedule into an approved calendar record."""

    page_bytes, final_page_url, _ = fetch_public_bytes(
        CALENDAR_URL,
        hosts=ALLOWED_PAGE_HOSTS,
        maximum=MAX_PAGE_BYTES,
        timeout=timeout,
    )
    page_html = page_bytes.decode("utf-8", errors="replace")
    pdf_source_url = calendar_pdf_url(page_html, final_page_url)
    pdf_bytes, final_pdf_url, pdf_content_type = fetch_public_bytes(
        pdf_source_url,
        hosts=ALLOWED_PAGE_HOSTS,
        maximum=MAX_PDF_BYTES,
        timeout=timeout,
        allowed_initial_url=_official_calendar_pdf_url,
        allowed_final_url=_allowed_calendar_pdf_result_url,
    )
    if not _allowed_calendar_pdf_result_url(final_pdf_url):
        raise CalendarRefreshError("downloadable calendar redirected to an invalid file")
    validate_pdf_response(pdf_bytes, pdf_content_type)
    blocks = calendar_text_blocks(extract_pdf_text(pdf_bytes))
    if not blocks:
        raise CalendarRefreshError("downloadable calendar contains no readable blocks")

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    source = dict(base_source)
    source["blocks"] = blocks + list(base_source.get("blocks", []))
    source["calendar_source"] = "live_downloadable_calendar"
    source["source_fetched_at"] = fetched_at
    source["calendar_document_url"] = final_pdf_url
    source["calendar_document_source_url"] = pdf_source_url
    source["calendar_document_final_url"] = final_pdf_url
    source["calendar_document_sha256"] = hashlib.sha256(pdf_bytes).hexdigest()
    source["calendar_extracted_characters"] = sum(len(block) for block in blocks)
    source["calendar_live_block_count"] = len(blocks)
    return source


class LiveCalendarCache:
    """Serve the latest good public calendar without slowing chat requests."""

    def __init__(self, ttl_seconds: int = 900, fetcher=fetch_live_calendar_source):
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.fetcher = fetcher
        self._lock = threading.Lock()
        self._source = None
        self._refreshed_at = 0.0
        self._refreshing = False
        self._last_error = "not_refreshed"

    def refresh(self, base_source: dict) -> bool:
        with self._lock:
            if self._refreshing:
                return False
            self._refreshing = True
        try:
            source = self.fetcher(base_source)
        except Exception as error:
            with self._lock:
                self._last_error = type(error).__name__
            return False
        else:
            with self._lock:
                self._source = source
                self._refreshed_at = time.monotonic()
                self._last_error = ""
            return True
        finally:
            with self._lock:
                self._refreshing = False

    def source(self, base_source: dict) -> dict:
        with self._lock:
            source = dict(self._source) if self._source else None
            stale = not self._refreshed_at or (
                time.monotonic() - self._refreshed_at >= self.ttl_seconds
            )
            refreshing = self._refreshing
        if stale and not refreshing:
            threading.Thread(
                target=self.refresh,
                args=(base_source,),
                name="digital-equity-calendar-refresh",
                daemon=True,
            ).start()
        return source or dict(base_source)

    def status(self) -> dict:
        with self._lock:
            source = self._source or {}
            return {
                "status": "live" if source else ("refreshing" if self._refreshing else "snapshot"),
                "source_fetched_at": source.get("source_fetched_at"),
                "extracted_characters": source.get("calendar_extracted_characters", 0),
                "refresh_interval_seconds": self.ttl_seconds,
                "last_error": self._last_error or None,
            }
