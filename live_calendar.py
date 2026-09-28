"""Current public calendar evidence for the Digital Equity Website Guide."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
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
MONTH_NUMBERS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
MONTH_NAMES = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4,
    "MAY": 5, "JUNE": 6, "JULY": 7, "AUGUST": 8,
    "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
}
PDF_MONTH_PATTERN = re.compile(r"^([A-Z]+)\s+(20\d{2})$")
PDF_EVENT_PATTERN = re.compile(
    r"^(MON|TUE|WED|THU|FRI|SAT|SUN)\s*\|\s*([A-Z]{3})\s*(\d{1,2})\s+(.+)$"
)


class CalendarRefreshError(RuntimeError):
    """The live public calendar could not be refreshed safely."""


def normalized_lines(value: str) -> list[str]:
    """Preserve PDF source order while removing extraction-only whitespace."""

    return [
        re.sub(r"\s+", " ", line).strip()
        for line in str(value or "").splitlines()
        if line.strip()
    ]


def calendar_pdf_schedule(source: dict[str, object]) -> dict[str, object]:
    """Pair class titles with the dates printed in the current public PDF."""

    readings = source.get("calendar_pdf_readings")
    if isinstance(readings, list):
        for reading in readings:
            if not isinstance(reading, dict) or reading.get("kind") != "linear":
                continue
            lines = normalized_lines(reading.get("text", ""))
            if any("DIGITAL SKILLS ESSENTIALS CLASSES" in line.upper() for line in lines):
                return _columnar_linear_calendar_schedule(lines)

    blocks = source.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("live calendar did not provide readable PDF source blocks")
    lines = normalized_lines("\n".join(str(block) for block in blocks))
    month_index = next(
        (
            index
            for index, line in enumerate(lines)
            if PDF_MONTH_PATTERN.fullmatch(line.upper())
        ),
        None,
    )
    if month_index is None:
        raise ValueError("downloadable calendar has no readable month and year")

    month_match = PDF_MONTH_PATTERN.fullmatch(lines[month_index].upper())
    assert month_match is not None
    month_name, year_text = month_match.groups()
    year = int(year_text)
    time_index = next(
        (
            index
            for index, line in enumerate(lines[month_index + 1 :], start=month_index + 1)
            if line.upper().startswith("TIME:")
        ),
        None,
    )
    if time_index is None:
        raise ValueError("downloadable calendar has no readable default class time")
    location_lines = lines[month_index + 1 : time_index]
    if len(location_lines) < 2:
        raise ValueError("downloadable calendar has no readable class location")

    events: list[dict[str, str]] = []
    first_event_index = next(
        (
            index
            for index, line in enumerate(lines[time_index + 1 :], start=time_index + 1)
            if PDF_EVENT_PATTERN.fullmatch(line.upper())
        ),
        None,
    )
    event_end = first_event_index or time_index + 1
    for index, line in enumerate(lines[event_end:], start=event_end):
        event_match = PDF_EVENT_PATTERN.fullmatch(line.upper())
        if event_match is None:
            event_end = index
            break
        weekday, event_month, day_text, _ = event_match.groups()
        titled_match = PDF_EVENT_PATTERN.fullmatch(line)
        assert titled_match is not None
        event_date = date(year, MONTH_NUMBERS[event_month], int(day_text))
        events.append({
            "date": event_date.isoformat(),
            "date_label": f"{weekday.title()} | {event_month.title()} {int(day_text)}",
            "title": titled_match.group(4).strip(),
        })
        event_end = index + 1

    if not events:
        # Canva exposes titles in layout order and may place its date column in
        # linear-text order. Pair the two complete source columns only when
        # their lengths match; otherwise leave the raw PDF text as the fallback.
        support_index = next(
            (
                index
                for index, line in enumerate(lines[time_index + 1 :], start=time_index + 1)
                if line.casefold().startswith("tech time")
            ),
            None,
        )
        if support_index is None:
            raise ValueError("downloadable calendar has no readable dated class rows")
        title_lines = lines[time_index + 1 : support_index]
        weekdays = [
            line.upper()
            for line in lines
            if line.upper() in {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}
        ]
        dates = [
            re.fullmatch(r"\|\s*([A-Z]{3})\s*(\d{1,2})", line.upper())
            for line in lines
        ]
        date_parts = [match.groups() for match in dates if match is not None]
        if (
            not title_lines
            or len(weekdays) != len(date_parts)
            or len(title_lines) != len(date_parts)
        ):
            raise ValueError("downloadable calendar has no readable dated class rows")
        for title, weekday, (event_month, day_text) in zip(
            title_lines, weekdays, date_parts
        ):
            event_date = date(year, MONTH_NUMBERS[event_month], int(day_text))
            events.append({
                "date": event_date.isoformat(),
                "date_label": f"{weekday.title()} | {event_month.title()} {int(day_text)}",
                "title": title,
            })
        event_end = support_index

    registration_index = next(
        (
            index
            for index, line in enumerate(lines[event_end:], start=event_end)
            if line.casefold().startswith("for more info or to register")
        ),
        len(lines),
    )
    return {
        "title": " ".join(lines[:month_index]).strip(),
        "month": f"{month_name.title()} {year}",
        "theme": location_lines[0] if len(location_lines) == 3 else "",
        "location": {
            "name": location_lines[-2],
            "address": location_lines[-1],
        },
        "default_hours": lines[time_index][len("TIME:") :].strip(),
        "events": events,
        "support": lines[event_end:registration_index],
        "registration_note": " ".join(lines[registration_index : registration_index + 2]).strip(),
    }


def _columnar_linear_calendar_schedule(lines: list[str]) -> dict[str, object]:
    """Read the current Canva calendar's two text columns without inferring rows."""

    month_index = next(
        (index for index, line in enumerate(lines) if PDF_MONTH_PATTERN.fullmatch(line.upper())),
        None,
    )
    if month_index is None:
        raise ValueError("downloadable calendar has no readable month and year")
    month_match = PDF_MONTH_PATTERN.fullmatch(lines[month_index].upper())
    assert month_match is not None
    month_name, year_text = month_match.groups()
    year = int(year_text)
    month_number = MONTH_NUMBERS[month_name[:3]]
    time_indexes = [
        index for index, line in enumerate(lines)
        if line.upper().startswith("TIME:")
    ]
    if len(time_indexes) < 2:
        raise ValueError("two-column calendar is missing its printed class hours")
    first_time_index, second_time_index = time_indexes[:2]
    location_lines = lines[month_index + 1:first_time_index]
    if len(location_lines) < 2:
        raise ValueError("downloadable calendar has no readable class location")

    support_index = next(
        (
            index for index, line in enumerate(lines[second_time_index + 1:], start=second_time_index + 1)
            if line.casefold().startswith("tech time")
        ),
        None,
    )
    if support_index is None:
        raise ValueError("two-column calendar is missing its support-hours section")

    def merge_continuations(values: list[str]) -> list[str]:
        merged: list[str] = []
        for value in values:
            if value.casefold().startswith("feat."):
                if not merged:
                    raise ValueError("calendar feature note has no preceding class")
                merged[-1] = f"{merged[-1]} ({value})"
            elif value:
                merged.append(value)
        return merged

    main_titles = merge_continuations(lines[second_time_index + 1:support_index])
    weekday_labels = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}

    def is_weekday_row(value: str) -> bool:
        parts = value.upper().split("/")
        return bool(parts) and all(part in weekday_labels for part in parts)

    def date_row(value: str):
        # pypdf can append the next visual text item to a date cell (for
        # example, "| OCT 28Support Desk Kiosk"). Accept only the complete
        # date/time prefix and ignore that attached neighboring text.
        return re.match(
            r"\|\s*([A-Z]{3})\s+(\d{1,2}(?:\s*/\s*\d{1,2})?)"
            r"(?:\s*\|\s*(\d{1,2}(?::\d{2})?\s*[AP]M))?",
            value.upper(),
        )

    right_weekday_start = next(
        (index for index in range(support_index + 1, len(lines)) if is_weekday_row(lines[index])),
        None,
    )
    if right_weekday_start is None:
        raise ValueError("digital-skills column has no printed weekday rows")
    right_weekdays = []
    cursor = right_weekday_start
    while cursor < len(lines) and is_weekday_row(lines[cursor]):
        right_weekdays.append(lines[cursor].upper())
        cursor += 1
    right_date_start = next(
        (index for index in range(cursor, len(lines)) if date_row(lines[index])),
        None,
    )
    if right_date_start is None:
        raise ValueError("digital-skills column has no printed date rows")
    right_titles = lines[cursor:right_date_start]
    right_dates = []
    cursor = right_date_start
    while cursor < len(lines) and date_row(lines[cursor]):
        right_dates.append(lines[cursor])
        cursor += 1
    if not right_weekdays or len(right_weekdays) != len(right_titles) or len(right_titles) != len(right_dates):
        raise ValueError("digital-skills title, weekday, and date columns do not align")

    repeated_header = next(
        (
            index for index in range(cursor, len(lines))
            if "DIGITAL EQUITY PROGRAM" in lines[index].upper()
        ),
        None,
    )
    if repeated_header is None:
        raise ValueError("main class column has no printed date section")
    main_weekday_start = next(
        (index for index in range(repeated_header + 1, len(lines)) if is_weekday_row(lines[index])),
        None,
    )
    if main_weekday_start is None:
        raise ValueError("main class column has no printed weekday rows")
    main_weekdays = []
    cursor = main_weekday_start
    while cursor < len(lines) and is_weekday_row(lines[cursor]):
        main_weekdays.append(lines[cursor].upper())
        cursor += 1
    main_dates = []
    while cursor < len(lines) and date_row(lines[cursor]):
        main_dates.append(lines[cursor])
        cursor += 1
    if not main_titles or len(main_titles) != len(main_weekdays) or len(main_titles) != len(main_dates):
        raise ValueError("main class title, weekday, and date columns do not align")

    def expand_rows(titles: list[str], weekdays: list[str], dates: list[str]) -> list[dict[str, str]]:
        expanded: list[dict[str, str]] = []
        for title, weekday_row, date_text in zip(titles, weekdays, dates):
            match = date_row(date_text)
            if match is None:
                raise ValueError("calendar date row is malformed")
            month_abbr, day_text, special_time = match.groups()
            if month_abbr not in MONTH_NUMBERS:
                raise ValueError("calendar date row has an unknown month")
            event_month = MONTH_NUMBERS[month_abbr]
            event_year = year + (1 if event_month < month_number else 0)
            days = [int(value.strip()) for value in day_text.split("/")]
            weekdays_for_row = weekday_row.split("/")
            if len(days) != len(weekdays_for_row):
                raise ValueError("calendar date and weekday ranges do not align")
            for day_number, weekday in zip(days, weekdays_for_row):
                try:
                    event_date = date(event_year, event_month, day_number)
                except ValueError as error:
                    raise ValueError("calendar contains an invalid date") from error
                if event_date.weekday() != list(("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")).index(weekday):
                    raise ValueError("calendar weekday does not match its printed date")
                event_title = title
                if special_time:
                    time_match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([AP])M", special_time)
                    if time_match is None:
                        raise ValueError("calendar event time is malformed")
                    hour, minute, meridiem = time_match.groups()
                    event_title = f"{event_title} ({int(hour)}:{minute or '00'} {meridiem}M)"
                expanded.append({
                    "date": event_date.isoformat(),
                    "date_label": f"{event_date.strftime('%a')} | {event_date.strftime('%b')} {event_date.day}",
                    "title": event_title,
                })
        return expanded

    events = expand_rows(main_titles, main_weekdays, main_dates)
    events.extend(expand_rows(right_titles, right_weekdays, right_dates))
    events.sort(key=lambda event: (event["date"], event["title"]))

    support: list[str] = []
    for line in lines:
        kiosk = re.search(r"Support Desk Kiosk.*?(?=\s*Tues\s*&\s*Wed\s*\||$)", line, re.I)
        if kiosk and kiosk.group(0) not in support:
            support.append(kiosk.group(0).strip())
        if re.match(r"^(?:Tech Time|Focused\s*\(|Foundations\s*\(|Support Desk Kiosk|Tues\s*&\s*Wed\s*\|)", line, re.I):
            if line not in support:
                support.append(line)
    contact = next(
        (
            re.search(r"Visit\s+FortuneDigitalEquity\.org.*?(?=For more info or to register|$)", line, re.I)
            for line in lines if "Visit FortuneDigitalEquity.org" in line
        ),
        None,
    )
    registration_note = "For more info or to register:"
    if contact:
        registration_note += " " + re.sub(r"\s+", " ", contact.group(0)).strip()
    theme = location_lines[0] if len(location_lines) == 3 else ""
    return {
        "title": "LIC Training Schedule",
        "month": f"{month_name.title()} {year}",
        "theme": theme,
        "location": {"name": location_lines[-2], "address": location_lines[-1]},
        "default_hours": lines[first_time_index][len("TIME:"):].strip(),
        "events": events,
        "support": support,
        "registration_note": registration_note,
    }


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


def extract_pdf_readings(pdf_bytes: bytes) -> list[dict[str, str]]:
    """Keep the bounded layout and linear readings of the public schedule."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        readings: list[dict[str, str]] = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            layout = page.extract_text(extraction_mode="layout") or ""
            linear = page.extract_text() or ""
            page_readings = []
            for kind, text in (("layout", layout), ("linear", linear)):
                text = re.sub(r"[\t\f\v ]+", " ", text)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if text and all(text != item["text"] for item in page_readings):
                    page_readings.append({"kind": kind, "text": text})
            readings.extend(page_readings)
            if sum(len(item["text"]) for item in readings) >= MAX_EXTRACTED_CHARACTERS:
                break
    except Exception as error:
        raise CalendarRefreshError("downloadable calendar text could not be read") from error
    bounded = []
    remaining = MAX_EXTRACTED_CHARACTERS
    for item in readings:
        text = item["text"][:remaining]
        if text:
            bounded.append({"kind": item["kind"], "text": text})
            remaining -= len(text)
        if remaining <= 0:
            break
    if sum(len(item["text"]) for item in bounded) < 80:
        raise CalendarRefreshError("downloadable calendar contains too little readable text")
    return bounded


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Return both bounded public-PDF text readings in their original order."""

    readings = extract_pdf_readings(pdf_bytes)
    result = "\n\n".join(item["text"] for item in readings)[:MAX_EXTRACTED_CHARACTERS].strip()
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
    pdf_readings = extract_pdf_readings(pdf_bytes)
    pdf_text = "\n\n".join(item["text"] for item in pdf_readings)
    blocks = calendar_text_blocks(pdf_text)
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
    try:
        schedule = calendar_pdf_schedule({**source, "calendar_pdf_readings": pdf_readings})
    except ValueError:
        schedule = None
    if schedule:
        default_hours = str(schedule.get("default_hours") or "").strip()
        location = schedule.get("location") or {}
        location_text = " · ".join(
            value
            for value in (
                str(location.get("name") or "").strip(),
                str(location.get("address") or "").strip(),
            )
            if value
        )
        calendar_events = []
        for event in schedule.get("events", []):
            event_date = date.fromisoformat(str(event["date"]))
            title = str(event.get("title") or "").strip()
            specific_time = re.search(
                r"\((\d{1,2}:\d{2}\s*[AP]M)\)",
                title,
                flags=re.I,
            )
            event_hours = (
                f"Starts {specific_time.group(1)}"
                if specific_time
                else default_hours
            )
            details = [
                f"{event_date.strftime('%A, %B')} {event_date.day}, {event_date.year}",
                title,
                event_hours,
                location_text,
            ]
            calendar_events.append({
                "date": event_date.isoformat(),
                "label": " · ".join(value for value in details if value),
            })
        source["calendar_schedule"] = schedule
        source["calendar_events"] = calendar_events
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
        result = source or dict(base_source)
        result["calendar_freshness"] = "stale" if source and stale else "live" if source else "snapshot"
        return result

    def status(self) -> dict:
        with self._lock:
            source = self._source or {}
            fresh = bool(source) and time.monotonic() - self._refreshed_at < self.ttl_seconds
            return {
                "status": ("live" if fresh else "stale") if source else ("refreshing" if self._refreshing else "snapshot"),
                "source_fetched_at": source.get("source_fetched_at"),
                "extracted_characters": source.get("calendar_extracted_characters", 0),
                "structured_events": len(source.get("calendar_events", [])),
                "refresh_interval_seconds": self.ttl_seconds,
                "last_error": self._last_error or None,
            }
