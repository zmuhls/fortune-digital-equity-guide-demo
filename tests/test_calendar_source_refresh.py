#!/usr/bin/env python3
"""Network-free tests for the committed static-calendar refresh artifact."""

import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "refresh_calendar_source.py"
SPEC = importlib.util.spec_from_file_location("refresh_calendar_source", SCRIPT)
refresh = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(refresh)


class CalendarSourceRefreshTests(unittest.TestCase):
    @staticmethod
    def agenda_record():
        return {
            "schema_version": 2,
            "captured_at": "2026-08-28T01:59:29.556Z",
            "calendar": {
                "url": "https://www.fortunedigitalequity.org/calendar",
                "label": "View the official calendar",
            },
            "week": {
                "label": "Week starting Sunday, August 23",
                "days": [f"Day {index}" for index in range(7)],
                "selected": "Day 5",
            },
            "agenda": {
                "source_url": "https://www.fortunedigitalequity.org/calendar",
                "continuation_url": "https://www.fortunedigitalequity.org/calendar",
                "events": [
                    {
                        "date": "2026-08-28",
                        "date_label": "Friday, August 28, 2026",
                        "title": "Tech Time: Foundations",
                        "start_time": "1:00 pm",
                        "duration": "1 hr",
                        "location": "Main Office (LIC)",
                        "registration": {
                            "url": "https://www.fortunedigitalequity.org/calendar",
                            "label": "Check availability and register",
                        },
                    }
                ],
            },
            "pdf": {
                "url": "https://www.fortunedigitalequity.org/_files/ugd/current.pdf",
                "label": "AI Month Class Schedule",
            },
        }

    @staticmethod
    def live_pdf_source():
        return {
            "calendar_document_source_url": "https://www.fortunedigitalequity.org/_files/ugd/current.pdf",
            "calendar_document_final_url": "https://uuid.filesusr.com/ugd/current.pdf",
            "calendar_document_sha256": "a" * 64,
            "source_fetched_at": "2026-08-27T03:27:56Z",
            "blocks": [
                "DIGITAL EQUITY PROGRAM\nLIC Training Schedule\nSEPTEMBER 2026\nAI AWARENESS MONTH\nLIC: MAIN SERVICE CENTER\n29-76 NORTHERN BLVD, ROOM 133\nTIME: 2:00 PM - 3:30 PM (UNLESS STATED OTHERWISE)\nTUE | SEP 1 What Is AI?\nWED | SEP 2 AI In Everyday Systems\nTech Time sessions available by Appointment ONLY\nFor more info or to register:\nVisit FortuneDigitalEquity.org"
            ],
        }

    def normalized_agenda_record(self, value=None):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "agenda.json"
            path.write_text(json.dumps(value or self.agenda_record()), encoding="utf-8")
            return refresh.read_agenda(path)

    def test_record_joins_rendered_agenda_and_checked_official_pdf(self):
        live = {
            **self.live_pdf_source(),
        }
        with mock.patch.object(refresh.live_calendar, "fetch_live_calendar_source", return_value=live):
            record = refresh.calendar_record(self.normalized_agenda_record())

        self.assertEqual(record["schema_version"], 3)
        self.assertEqual(record["source_url"], refresh.live_calendar.CALENDAR_URL)
        self.assertEqual(record["document"]["url"], live["calendar_document_source_url"])
        self.assertNotIn("filesusr.com", record["document"]["url"])
        self.assertEqual(record["document"]["sha256"], "a" * 64)
        self.assertEqual(record["agenda"]["events"][0]["title"], "Tech Time: Foundations")
        self.assertEqual(record["pdf_schedule"]["month"], "September 2026")
        self.assertEqual(record["pdf_schedule"]["events"][0]["title"], "What Is AI?")

    def test_refresh_rejects_agenda_and_pdf_mismatch(self):
        agenda = self.agenda_record()
        agenda["pdf"]["url"] = "https://www.fortunedigitalequity.org/_files/ugd/other.pdf"
        with mock.patch.object(
            refresh.live_calendar,
            "fetch_live_calendar_source",
            return_value=self.live_pdf_source(),
        ):
            with self.assertRaisesRegex(ValueError, "different PDFs"):
                refresh.calendar_record(self.normalized_agenda_record(agenda))

    def test_pdf_schedule_pairs_a_separate_canva_date_column_without_guessing(self):
        source = {
            "blocks": [
                "DIGITAL EQUITY PROGRAM\nLIC Training Schedule\nSEPTEMBER 2026\nAI AWARENESS MONTH\nLIC: MAIN SERVICE CENTER\n29-76 NORTHERN BLVD, ROOM 133\nTIME: 2:00 PM - 3:30 PM\nWhat Is AI?\nAI In Everyday Systems\nTech Time sessions available by Appointment ONLY\nFor more info or to register:\nVisit FortuneDigitalEquity.org\nTUE\nWED\n| SEP 1\n| SEP 2"
            ]
        }

        schedule = refresh.calendar_pdf_schedule(source)

        self.assertEqual(
            schedule["events"],
            [
                {
                    "date": "2026-09-01",
                    "date_label": "Tue | Sep 1",
                    "title": "What Is AI?",
                },
                {
                    "date": "2026-09-02",
                    "date_label": "Wed | Sep 2",
                    "title": "AI In Everyday Systems",
                },
            ],
        )

    def test_read_agenda_rejects_nonofficial_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "agenda.json"
            agenda = self.agenda_record()
            agenda["agenda"]["events"][0]["registration"]["url"] = "https://example.org/booking"
            path.write_text(json.dumps(agenda), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "registration"):
                refresh.read_agenda(path)

    def test_write_replaces_only_a_root_calendar_source_file(self):
        record = {"schema_version": 2}
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = root / "calendar-source.json"
            with mock.patch.object(refresh, "ROOT", root):
                refresh.write_record(output, record)
                self.assertEqual(json.loads(output.read_text()), record)
                with self.assertRaises(ValueError):
                    refresh.write_record(root / "nested" / "calendar-source.json", record)


if __name__ == "__main__":
    unittest.main()
