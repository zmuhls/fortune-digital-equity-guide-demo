#!/usr/bin/env python3
"""Network-free tests for the current public calendar source."""

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

    def test_live_source_merges_extracted_schedule_before_static_page_facts(self):
        base = {"id": "calendar", "blocks": ["Class Locations"]}
        responses = [
            (
                b'<a href="/_files/ugd/current.pdf">August calendar</a>',
                live_calendar.CALENDAR_URL,
                "text/html",
            ),
            (b"%PDF fixture", "https://www.fortunedigitalequity.org/_files/ugd/current.pdf", "application/pdf"),
        ]
        schedule = "August 28\nTech Time: Foundations\n1:00 PM\nMain Office (LIC)\n15 spots left"
        with mock.patch.object(live_calendar, "fetch_public_bytes", side_effect=responses), mock.patch.object(
            live_calendar,
            "extract_pdf_text",
            return_value=schedule,
        ):
            source = live_calendar.fetch_live_calendar_source(base)

        self.assertEqual(source["calendar_source"], "live_downloadable_calendar")
        self.assertEqual(source["blocks"][-1], "Class Locations")
        self.assertIn("Tech Time: Foundations", source["blocks"][0])
        self.assertEqual(source["calendar_document_url"], responses[1][1])
        self.assertGreater(source["calendar_extracted_characters"], 40)

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
