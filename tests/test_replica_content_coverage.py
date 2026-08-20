#!/usr/bin/env python3
"""Release contracts for public material that Wix only reveals interactively.

These checks intentionally read the reviewed capture rather than making network
requests.  A source change can legitimately require changing a threshold after
review, but silently regressing a full public collection to its first page or
hiding an FAQ behind an inert button must fail before publication.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import re
import unittest
from urllib.parse import urlsplit


ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "replica-manifest.json"
SNAPSHOTS = ROOT / "replica-snapshots"


def normalized_path(url: str) -> str:
    return urlsplit(url).path.rstrip("/") or "/"


class ReplicaContentCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.pages = {
            normalized_path(page["url"]): page
            for page in cls.manifest["pages"]
        }

    def html_for(self, route: str) -> str:
        page = self.pages[route]
        return gzip.decompress((ROOT / page["file"]).read_bytes()).decode("utf-8")

    def static_content_for(self, route: str) -> dict:
        return self.pages[route]["static_content"]

    def visible_text_for(self, route: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", self.html_for(route))).strip()

    def test_faq_and_support_material_is_open_and_readable(self):
        expected = {"/": 8, "/contact": 4, "/support": 5}
        for route, count in expected.items():
            with self.subTest(route=route):
                html = self.html_for(route)
                open_disclosures = re.findall(
                    r"<details\b(?=[^>]*data-replica-static-disclosure=)(?=[^>]*\bopen(?:\s|=|>))[^>]*>",
                    html,
                    re.IGNORECASE,
                )
                self.assertEqual(len(open_disclosures), count)
                self.assertNotRegex(
                    html,
                    r"<button\b[^>]*data-hook=[\"']accordion-item-header[\"']",
                )
                self.assertEqual(self.static_content_for(route)["wix_accordions"], count)

        home = self.visible_text_for("/")
        contact = self.visible_text_for("/contact")
        support = self.visible_text_for("/support")
        self.assertIn("For regularly scheduled classes we allow walk-in attendance", home)
        self.assertIn("at least 5 Digital Equity classes", contact)
        self.assertIn("Tech Time Foundations", support)
        self.assertIn("Open Lab", support)

    def test_finite_catalogs_are_expanded_in_full(self):
        for route in ("/catalog", "/workshops"):
            with self.subTest(route=route):
                html = self.html_for(route)
                service_paths = {
                    normalized_path(url)
                    for url in re.findall(r'''href=[\"']([^\"']*/service-page/[^\"'#?]+)[\"']''', html)
                }
                progressive = self.static_content_for(route)["progressive_collections"]
                self.assertGreaterEqual(len(service_paths), 64)
                self.assertEqual(progressive["load_more_clicks"], 5)
                self.assertGreaterEqual(progressive["after"]["service_page_links"], 64)
                self.assertGreater(progressive["after"]["service_page_links"], progressive["before"]["service_page_links"])
                self.assertNotIn("load-services-button-button", html)

    def test_calendar_is_a_bounded_current_slice_with_a_live_continuation(self):
        html = self.html_for("/calendar")
        progressive = self.static_content_for("/calendar")["progressive_collections"]
        horizon = progressive["calendar_horizon"]
        self.assertEqual(horizon["clicks"], 9)
        self.assertEqual(horizon["limit"], 9)
        self.assertTrue(horizon["continuation_removed"])
        self.assertGreater(progressive["after"]["visible_text_characters"], progressive["before"]["visible_text_characters"])
        self.assertIn("data-replica-live-calendar-note", html)
        self.assertIn("View the live calendar at Fortune.", html)
        self.assertNotIn("daily-agenda-load-more-button", html)

    def test_progressive_tech_fair_galleries_keep_their_public_media(self):
        for route in ("/techfair/techfair25", "/techfair/techfair26"):
            with self.subTest(route=route):
                progressive = self.static_content_for(route)["progressive_collections"]
                self.assertGreaterEqual(progressive["load_more_clicks"], 1)
                self.assertGreaterEqual(progressive["after"]["images"], 55)
                self.assertTrue(
                    progressive["after"]["images"] > progressive["before"]["images"]
                    or progressive["controls_retired_without_growth"] >= 1,
                )

    def test_capture_has_no_submission_or_error_state_noise(self):
        phrases = (
            "An error occurred. Try again later",
            "Your content has been submitted",
            "Widget Didn't Load",
            "Widget Didn’t Load",
        )
        for page in self.pages.values():
            with self.subTest(route=page["path"]):
                html = gzip.decompress((ROOT / page["file"]).read_bytes()).decode("utf-8")
                visible_markup = re.sub(r"<style\b.*?</style>", "", html, flags=re.IGNORECASE | re.DOTALL)
                for phrase in phrases:
                    self.assertNotIn(phrase, html)
                self.assertNotRegex(visible_markup, r"<(?:button|input|textarea|select)\b")

    def test_static_cleanup_keeps_meaningful_public_control_labels(self):
        """Removing inert Wix controls must not remove the public labels they carried."""
        deiqa = self.visible_text_for("/deiqa")
        tech_fair_qa = self.visible_text_for("/techfair/qa")
        self.assertIn("Speaker: Stanley Richards", deiqa)
        self.assertIn("Speaker:", tech_fair_qa)
        self.assertGreaterEqual(
            self.html_for("/techfair/qa").count("data-replica-static-control-label"),
            6,
        )


if __name__ == "__main__":
    unittest.main()
