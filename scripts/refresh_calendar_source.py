#!/usr/bin/env python3
"""Refresh the reviewed, build-time calendar supplement from public Fortune URLs."""

from __future__ import annotations

import argparse
from datetime import date
import json
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import live_calendar


DEFAULT_OUTPUT = ROOT / "calendar-source.json"
AGENDA_SCHEMA_VERSION = 2
SOURCE_SCHEMA_VERSION = 3

MONTH_NUMBERS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
MONTH_NAMES = {
    "JANUARY": 1,
    "FEBRUARY": 2,
    "MARCH": 3,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DECEMBER": 12,
}
PDF_MONTH_PATTERN = re.compile(r"^([A-Z]+)\s+(20\d{2})$")
PDF_EVENT_PATTERN = re.compile(
    r"^(MON|TUE|WED|THU|FRI|SAT|SUN)\s*\|\s*([A-Z]{3})\s*(\d{1,2})\s+(.+)$"
)
RFC3339_Z_PATTERN = re.compile(r"^20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$")


def normalized_lines(value: str) -> list[str]:
    """Preserve source order while removing extraction-only whitespace."""

    return [re.sub(r"\s+", " ", line).strip() for line in str(value or "").splitlines() if line.strip()]


def calendar_pdf_schedule(source: dict[str, object]) -> dict[str, object]:
    """Normalize one current public PDF schedule without inventing class data."""

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
    month_number = MONTH_NAMES[month_name]
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
        title = PDF_EVENT_PATTERN.fullmatch(line)
        assert title is not None
        event_month_number = MONTH_NUMBERS[event_month]
        event_date = date(year, event_month_number, int(day_text))
        events.append(
            {
                "date": event_date.isoformat(),
                "date_label": f"{weekday.title()} | {event_month.title()} {int(day_text)}",
                "title": title.group(4).strip(),
            }
        )
        event_end = index + 1

    if not events:
        # A Canva PDF can expose schedule titles in layout order and its date
        # column in ordinary text order.  Pair only the matching source
        # columns; if either disappears, fail closed instead of fabricating
        # dates for the static mirror.
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
        for title, weekday, (event_month, day_text) in zip(title_lines, weekdays, date_parts):
            event_date = date(year, MONTH_NUMBERS[event_month], int(day_text))
            events.append(
                {
                    "date": event_date.isoformat(),
                    "date_label": f"{weekday.title()} | {event_month.title()} {int(day_text)}",
                    "title": title,
                }
            )
        event_end = support_index
    if not events:
        raise ValueError("downloadable calendar has no usable dated class rows")

    registration_index = next(
        (
            index
            for index, line in enumerate(lines[event_end:], start=event_end)
            if line.casefold().startswith("for more info or to register")
        ),
        len(lines),
    )
    support_lines = lines[event_end:registration_index]
    registration_lines = lines[registration_index : registration_index + 2]
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
        "support": support_lines,
        "registration_note": " ".join(registration_lines).strip(),
    }


def read_agenda(path: pathlib.Path) -> dict[str, object]:
    """Load a normalized, browser-captured public Daily Agenda record."""

    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read calendar agenda capture: {error}") from error
    if not isinstance(record, dict) or record.get("schema_version") != AGENDA_SCHEMA_VERSION:
        raise ValueError("calendar agenda capture has an unsupported schema")
    calendar = record.get("calendar")
    if not isinstance(calendar, dict) or calendar.get("url") != live_calendar.CALENDAR_URL:
        raise ValueError("calendar agenda capture is not from the official calendar")
    captured_at = str(record.get("captured_at") or "")
    if not RFC3339_Z_PATTERN.fullmatch(captured_at):
        raise ValueError("calendar agenda capture has an invalid timestamp")
    document = record.get("pdf")
    if not isinstance(document, dict):
        raise ValueError("calendar agenda capture is missing the downloadable schedule")
    if not live_calendar._official_calendar_pdf_url(str(document.get("url") or "")):
        raise ValueError("calendar agenda capture has an unapproved schedule URL")
    if not str(document.get("label") or "").strip():
        raise ValueError("calendar agenda capture is missing the public schedule label")
    agenda = record.get("agenda")
    if not isinstance(agenda, dict) or agenda.get("source_url") != live_calendar.CALENDAR_URL:
        raise ValueError("calendar agenda capture is missing its public agenda")
    if agenda.get("continuation_url") != live_calendar.CALENDAR_URL:
        raise ValueError("calendar agenda continuation must stay on the official calendar")
    week = record.get("week")
    if not isinstance(week, dict) or not str(week.get("label") or "").strip():
        raise ValueError("calendar agenda capture has no usable visible week")
    days = week.get("days")
    if not isinstance(days, list) or len(days) != 7:
        raise ValueError("calendar agenda capture must retain seven visible days")
    selected = str(week.get("selected") or "").strip()
    if selected not in days:
        raise ValueError("calendar agenda capture has no selected visible day")
    events = agenda.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 40:
        raise ValueError("calendar agenda capture has no usable event rows")
    normalized_events = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("calendar agenda capture has an invalid event row")
        for field in ("date", "date_label", "title", "start_time", "duration", "location"):
            value = str(event.get(field) or "").strip()
            if not value or len(value) > 300:
                raise ValueError(f"calendar agenda event is missing {field}")
        registration = event.get("registration")
        if not isinstance(registration, dict) or registration.get("url") != live_calendar.CALENDAR_URL:
            raise ValueError("calendar agenda event does not keep registration on the official calendar")
        normalized_events.append(
            {
                "date": str(event["date"]).strip(),
                "date_label": str(event["date_label"]).strip(),
                "title": str(event["title"]).strip(),
                "time": str(event["start_time"]).strip(),
                "duration": str(event["duration"]).strip(),
                "location": str(event["location"]).strip(),
                "registration_url": live_calendar.CALENDAR_URL,
            }
        )
    return {
        "captured_at": captured_at,
        "week": {
            "label": str(week["label"]).strip(),
            "days": [str(day).strip() for day in days],
            "selected": selected,
        },
        "events": normalized_events,
        "document": {
            "url": str(document["url"]).strip(),
            "label": str(document["label"]).strip(),
        },
    }


def calendar_record(agenda: dict[str, object]) -> dict[str, object]:
    """Return one source-bound calendar record suitable for both mirrors."""

    source = live_calendar.fetch_live_calendar_source({"id": "calendar", "blocks": []})
    captured_at = str(source["source_fetched_at"])
    if captured_at.endswith("+00:00"):
        captured_at = f"{captured_at[:-6]}Z"
    document = agenda["document"]
    if document.get("url") != source["calendar_document_source_url"]:
        raise ValueError(
            "the rendered calendar and freshly downloaded schedule point to different PDFs; capture again"
        )
    schedule = calendar_pdf_schedule(source)
    return {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "source_url": live_calendar.CALENDAR_URL,
        "captured_at": captured_at,
        "agenda": {
            "captured_at": agenda["captured_at"],
            "week": agenda["week"],
            "events": agenda["events"],
        },
        "document": {
            "url": source["calendar_document_source_url"],
            "label": str(document["label"]).strip(),
            "sha256": source["calendar_document_sha256"],
        },
        "pdf_schedule": schedule,
    }


def write_record(output: pathlib.Path, record: dict[str, object]) -> None:
    """Atomically replace only the explicitly chosen calendar-source file."""

    output = output.resolve()
    if output.parent != ROOT.resolve() or output.name != "calendar-source.json":
        raise ValueError("calendar source output must be the repository-root calendar-source.json")
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--agenda",
        type=pathlib.Path,
        required=True,
        help="normalized Daily Agenda capture produced by capture_calendar_agenda.mjs",
    )
    args = parser.parse_args()
    agenda = read_agenda(args.agenda)
    record = calendar_record(agenda)
    write_record(args.output, record)
    print(
        "refreshed calendar source "
        f"({record['captured_at']}; {record['document']['sha256'][:12]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
