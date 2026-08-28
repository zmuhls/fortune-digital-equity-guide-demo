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
        schedule = "August 28\nTech Time: Foundations\n1:00 PM\nMain Office (LIC)\n15 spots left"
        with mock.patch.object(
            live_calendar, "fetch_public_bytes", side_effect=responses
        ) as fetch, mock.patch.object(live_calendar, "extract_pdf_text", return_value=schedule):
            source = live_calendar.fetch_live_calendar_source(base)

        self.assertEqual(source["calendar_source"], "live_downloadable_calendar")
        self.assertEqual(source["blocks"][-1], "Class Locations")
        self.assertIn("Tech Time: Foundations", source["blocks"][0])
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
            live_calendar, "extract_pdf_text"
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
            live_calendar, "extract_pdf_text"
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
