#!/usr/bin/env python3
"""Network-free tests for the current public calendar source."""

import hashlib
import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import live_calendar


class LiveCalendarTests(unittest.TestCase):
    def test_current_october_pdf_columnar_schedule_keeps_source_rows_aligned(self):
        linear = """OCTOBER 2026
CYBERSECURITY AWARENESS MONTH
LIC: MAIN SERVICE CENTER
29-76 NORTHERN BLVD, ROOM 133
TIME: 2:00 PM - 3:30 PM (UNLESS NOTED)
DIGITAL SKILLS ESSENTIALS CLASSES
29-76 NORTHERN BLVD, ROOM 133
TIME: 2:00 PM - 3:30 PM
Viewing: Bridging The Gap Webinar
No Class
Recognizing Online Threats
Spotting Digital Scams
feat. the TSS Foundation
Protecting Personal Privacy
Securing Your Devices
Navigating AI Safely
feat. the AI Safety Awareness Project
Movie Viewing: Mercy
Tech Time Focused & Foundations (TTF) sessions available by Appointment ONLY
Focused (1-on-1): Mon, Tue, & Wed | 10 AM - 11:30 AM
Foundations (Practice): Mon & Fri | 1 - 2 PM | LIC Room 133
WED
WED
WED
WED
Navigating Internet Browsers
Intro to Email
Navigating Windows Desktop
Navigating the Cloud
| OCT 7
| OCT 14
| OCT 21
| OCT 28Support Desk Kiosk (SDK) available weekly
Tues & Wed | LIC Cafeteria | 11:30 - 1:30 PM
Visit FortuneDigitalEquity.org (QR Code) or email FSTrain@FortuneSociety.orgFor more info or to register:
DIGITAL EQUITY PROGRAM LIC Training
SchedulE
MON
TUE/THU
TUE
THU
TUE
THU
TUE
THU
| OCT 5 | 1 PM
| OCT 6/8
| OCT 13
| OCT 15
| OCT 20
| OCT 22
| OCT 27
| OCT 29"""
        schedule = live_calendar.calendar_pdf_schedule({
            "calendar_pdf_readings": [
                {"kind": "layout", "text": "OCTOBER 2026"},
                {"kind": "linear", "text": linear},
            ]
        })

        self.assertEqual(schedule["month"], "October 2026")
        self.assertEqual(schedule["theme"], "CYBERSECURITY AWARENESS MONTH")
        self.assertEqual(len(schedule["events"]), 13)
        self.assertEqual(
            schedule["events"][0],
            {
                "date": "2026-10-05",
                "date_label": "Mon | Oct 5",
                "title": "Viewing: Bridging The Gap Webinar (1:00 PM)",
            },
        )
        self.assertEqual(
            [event["date"] for event in schedule["events"] if event["title"] == "No Class"],
            ["2026-10-06", "2026-10-08"],
        )
        self.assertIn("Navigating AI Safely (feat. the AI Safety Awareness Project)", [
            event["title"] for event in schedule["events"]
        ])
        self.assertEqual(schedule["events"][-1]["date"], "2026-10-29")
        self.assertIn("Support Desk Kiosk", " ".join(schedule["support"]))
        self.assertIn("FSTrain@FortuneSociety.org", schedule["registration_note"])
        mismatched = linear.replace("| OCT 7\n", "| OCT 6\n", 1)
        with self.assertRaisesRegex(ValueError, "weekday does not match"):
            live_calendar.calendar_pdf_schedule({
                "calendar_pdf_readings": [{"kind": "linear", "text": mismatched}]
            })

    def test_calendar_pdf_link_must_stay_on_an_approved_public_host(self):
        page = '<a href="/_files/ugd/current_schedule.pdf?download=1">Schedule</a>'
        self.assertEqual(
            live_calendar.calendar_pdf_url(page),
            "https://www.fortunedigitalequity.org/_files/ugd/current_schedule.pdf?download=1",
        )
        with self.assertRaises(live_calendar.CalendarRefreshError):
            live_calendar.calendar_pdf_url('<a href="https://example.org/schedule.pdf">Schedule</a>')
        with self.assertRaises(live_calendar.CalendarRefreshError):
            live_calendar.calendar_pdf_url('<a href="/documents/schedule.pdf">Schedule</a>')

    def test_calendar_pdf_redirect_allows_only_bounded_filesusr_pdf_routes(self):
        source_url = "https://www.fortunedigitalequity.org/_files/ugd/current_schedule.pdf"
        redirect_url = "https://03d919e5-5c1e-4845-ab5a-446f1da5e87a.filesusr.com/ugd/current_schedule.pdf"
        self.assertTrue(live_calendar._official_calendar_pdf_url(source_url))
        self.assertTrue(live_calendar._allowed_calendar_pdf_result_url(source_url))
        self.assertTrue(live_calendar._allowed_calendar_pdf_result_url(redirect_url))
        self.assertFalse(
            live_calendar._allowed_calendar_pdf_result_url(
                "https://filesusr.com/ugd/current_schedule.pdf"
            )
        )
        self.assertFalse(
            live_calendar._allowed_calendar_pdf_result_url(
                "https://03d919e5-5c1e-4845-ab5a-446f1da5e87a.filesusr.com/files/current_schedule.pdf"
            )
        )
        self.assertFalse(
            live_calendar._allowed_calendar_pdf_result_url(
                "http://03d919e5-5c1e-4845-ab5a-446f1da5e87a.filesusr.com/ugd/current_schedule.pdf"
            )
        )

    def test_fetch_accepts_the_checked_filesusr_redirect(self):
        class Response:
            headers = {"Content-Length": "16", "Content-Type": "application/pdf"}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return "https://03d919e5-5c1e-4845-ab5a-446f1da5e87a.filesusr.com/ugd/current.pdf"

            def read(self, _):
                return b"%PDF-1.7 fixture"

        source_url = "https://www.fortunedigitalequity.org/_files/ugd/current.pdf"
        with mock.patch.object(live_calendar.urllib.request, "urlopen", return_value=Response()):
            body, final_url, content_type = live_calendar.fetch_public_bytes(
                source_url,
                hosts=live_calendar.ALLOWED_PAGE_HOSTS,
                maximum=1024,
                allowed_initial_url=live_calendar._official_calendar_pdf_url,
                allowed_final_url=live_calendar._allowed_calendar_pdf_result_url,
            )

        self.assertEqual(body, b"%PDF-1.7 fixture")
        self.assertTrue(live_calendar._filesusr_calendar_pdf_url(final_url))
        self.assertEqual(content_type, "application/pdf")

    def test_live_source_merges_extracted_schedule_before_static_page_facts(self):
        base = {"id": "calendar", "blocks": ["Class Locations"]}
        responses = [
            (
                b'<a href="/_files/ugd/current.pdf">August calendar</a>',
                live_calendar.CALENDAR_URL,
                "text/html",
            ),
            (
                b"%PDF-1.7 fixture",
                "https://03d919e5-5c1e-4845-ab5a-446f1da5e87a.filesusr.com/ugd/current.pdf",
                "application/pdf; charset=binary",
            ),
        ]
        schedule = """DIGITAL EQUITY PROGRAM LIC Training Schedule
September 2026
AI AWARENESS MONTH
LIC: MAIN SERVICE CENTER
29-76 NORTHERN BLVD, ROOM 133
TIME: 2:00 PM - 3:30 PM (UNLESS STATED OTHERWISE)
What Is AI? (1:30 PM)
Communicating With AI
Tech Time Focused & Foundations sessions available by appointment only
TUE
WED
| SEP 1
| SEP 2
For more info or to register:
Visit the Digital Equity site
"""
        with mock.patch.object(
            live_calendar, "fetch_public_bytes", side_effect=responses
        ) as fetch, mock.patch.object(
            live_calendar,
            "extract_pdf_readings",
            return_value=[{"kind": "linear", "text": schedule}],
        ):
            source = live_calendar.fetch_live_calendar_source(base)

        self.assertEqual(source["calendar_source"], "live_downloadable_calendar")
        self.assertEqual(source["blocks"][-1], "Class Locations")
        self.assertIn("DIGITAL EQUITY PROGRAM", source["blocks"][0])
        self.assertEqual(len(source["calendar_schedule"]["events"]), 2)
        self.assertEqual(
            source["calendar_schedule"]["events"][0],
            {"date": "2026-09-01", "date_label": "Tue | Sep 1", "title": "What Is AI? (1:30 PM)"},
        )
        self.assertIn("Tuesday, September 1, 2026", source["calendar_events"][0]["label"])
        self.assertIn("Starts 1:30 PM", source["calendar_events"][0]["label"])
        self.assertNotIn("2:00 PM - 3:30 PM", source["calendar_events"][0]["label"])
        self.assertIn("2:00 PM - 3:30 PM", source["calendar_events"][1]["label"])
        self.assertEqual(source["calendar_document_url"], responses[1][1])
        self.assertEqual(
            source["calendar_document_source_url"],
            "https://www.fortunedigitalequity.org/_files/ugd/current.pdf",
        )
        self.assertEqual(source["calendar_document_final_url"], responses[1][1])
        self.assertEqual(
            source["calendar_document_sha256"], hashlib.sha256(responses[1][0]).hexdigest()
        )
        pdf_call = fetch.call_args_list[1].kwargs
        self.assertIs(pdf_call["allowed_initial_url"], live_calendar._official_calendar_pdf_url)
        self.assertIs(pdf_call["allowed_final_url"], live_calendar._allowed_calendar_pdf_result_url)
        self.assertGreater(source["calendar_extracted_characters"], 40)

    def test_live_source_rejects_non_pdf_content_before_extraction(self):
        responses = [
            (
                b'<a href="/_files/ugd/current.pdf">August calendar</a>',
                live_calendar.CALENDAR_URL,
                "text/html",
            ),
            (
                b"<html>not a PDF</html>",
                "https://03d919e5-5c1e-4845-ab5a-446f1da5e87a.filesusr.com/ugd/current.pdf",
                "text/html",
            ),
        ]
        with mock.patch.object(live_calendar, "fetch_public_bytes", side_effect=responses), mock.patch.object(
            live_calendar, "extract_pdf_readings"
        ) as extract:
            with self.assertRaises(live_calendar.CalendarRefreshError):
                live_calendar.fetch_live_calendar_source({"id": "calendar", "blocks": []})
        extract.assert_not_called()

    def test_live_source_rejects_pdf_content_type_without_pdf_bytes(self):
        responses = [
            (
                b'<a href="/_files/ugd/current.pdf">August calendar</a>',
                live_calendar.CALENDAR_URL,
                "text/html",
            ),
            (
                b"not a PDF",
                "https://03d919e5-5c1e-4845-ab5a-446f1da5e87a.filesusr.com/ugd/current.pdf",
                "application/pdf",
            ),
        ]
        with mock.patch.object(live_calendar, "fetch_public_bytes", side_effect=responses), mock.patch.object(
            live_calendar, "extract_pdf_readings"
        ) as extract:
            with self.assertRaises(live_calendar.CalendarRefreshError):
                live_calendar.fetch_live_calendar_source({"id": "calendar", "blocks": []})
        extract.assert_not_called()

    def test_cache_retains_latest_good_calendar_when_refresh_fails(self):
        calls = []

        def fetcher(base):
            calls.append(True)
            if len(calls) > 1:
                raise live_calendar.CalendarRefreshError("offline")
            return {**base, "calendar_source": "live_downloadable_calendar", "source_fetched_at": "now"}

        cache = live_calendar.LiveCalendarCache(ttl_seconds=60, fetcher=fetcher)
        base = {"id": "calendar", "blocks": ["fallback"]}
        self.assertTrue(cache.refresh(base))
        self.assertFalse(cache.refresh(base))
        self.assertEqual(cache.source(base)["calendar_source"], "live_downloadable_calendar")
        self.assertEqual(cache.status()["status"], "live")
        self.assertEqual(cache.status()["last_error"], "CalendarRefreshError")

    def test_calendar_blocks_preserve_source_order_and_bound_size(self):
        blocks = live_calendar.calendar_text_blocks("First line\nSecond line\nThird line", maximum=22)
        self.assertEqual(blocks, ["First line\nSecond line", "Third line"])
        self.assertTrue(all(len(block) <= 22 for block in blocks))


if __name__ == "__main__":
    unittest.main(verbosity=2)
