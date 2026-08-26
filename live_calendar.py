"""Current public calendar evidence for the Digital Equity Website Guide."""

from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import io
import re
import threading
import time
import urllib.parse
import urllib.request


CALENDAR_URL = "https://www.fortunedigitalequity.org/calendar"
ALLOWED_PAGE_HOSTS = {"fortunedigitalequity.org", "www.fortunedigitalequity.org"}
ALLOWED_FILE_HOSTS = ALLOWED_PAGE_HOSTS | {
    "static.wixstatic.com",
    "www-fortunedigitalequity-org.filesusr.com",
}
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PDF_PAGES = 12
MAX_EXTRACTED_CHARACTERS = 60_000


class CalendarRefreshError(RuntimeError):
    """The live public calendar could not be refreshed safely."""


def _allowed_url(url: str, hosts: set[str], *, pdf: bool = False) -> bool:
    parsed = urllib.parse.urlsplit(str(url or ""))
    return (
        parsed.scheme == "https"
        and parsed.hostname in hosts
        and not parsed.username
        and not parsed.password
        and (not pdf or parsed.path.lower().endswith(".pdf"))
    )


def fetch_public_bytes(
    url: str,
    *,
    hosts: set[str],
    maximum: int,
    timeout: float = 8.0,
) -> tuple[bytes, str, str]:
    """Fetch one bounded public source and validate its final URL."""

    if not _allowed_url(url, hosts):
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
            if not _allowed_url(final_url, hosts):
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
        if _allowed_url(candidate, ALLOWED_FILE_HOSTS, pdf=True):
            return candidate
    raise CalendarRefreshError("calendar page has no allowed downloadable calendar")


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract bounded text from the public schedule PDF."""

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        pages = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            text = page.extract_text(extraction_mode="layout") or page.extract_text() or ""
            text = re.sub(r"[\t\f\v ]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                pages.append(text)
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
    pdf_url = calendar_pdf_url(page_html, final_page_url)
    pdf_bytes, final_pdf_url, _ = fetch_public_bytes(
        pdf_url,
        hosts=ALLOWED_FILE_HOSTS,
        maximum=MAX_PDF_BYTES,
        timeout=timeout,
    )
    if not _allowed_url(final_pdf_url, ALLOWED_FILE_HOSTS, pdf=True):
        raise CalendarRefreshError("downloadable calendar redirected to an invalid file")
    blocks = calendar_text_blocks(extract_pdf_text(pdf_bytes))
    if not blocks:
        raise CalendarRefreshError("downloadable calendar contains no readable blocks")

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    source = dict(base_source)
    source["blocks"] = blocks + list(base_source.get("blocks", []))
    source["calendar_source"] = "live_downloadable_calendar"
    source["source_fetched_at"] = fetched_at
    source["calendar_document_url"] = final_pdf_url
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
