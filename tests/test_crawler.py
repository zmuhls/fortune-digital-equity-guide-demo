#!/usr/bin/env python3
"""Network-free tests for the public Wix sitemap crawler's pure functions."""

import importlib.util
import gzip
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock


DEMO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = DEMO / "scripts" / "rebuild_site_index.py"
SPEC = importlib.util.spec_from_file_location("fortune_site_crawler", SCRIPT)
crawler = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(crawler)


def row(path, kind="pages"):
    return {
        "url": f"https://www.fortunedigitalequity.org{path}",
        "sitemap_kind": kind,
        "lastmod": "",
    }


class NoNetworkTestCase(unittest.TestCase):
    def setUp(self):
        network_guard = mock.patch.object(
            crawler,
            "fetch",
            side_effect=AssertionError("unit tests must not make network requests"),
        )
        network_guard.start()
        self.addCleanup(network_guard.stop)


class CanonicalizationTests(NoNetworkTestCase):
    def test_relative_and_same_host_urls_receive_one_canonical_shape(self):
        fixtures = {
            "/trainings/": "https://www.fortunedigitalequity.org/trainings",
            "https://fortunedigitalequity.org/about/?draft=1#staff": "https://www.fortunedigitalequity.org/about",
            "https://www.fortunedigitalequity.org/": "https://www.fortunedigitalequity.org/",
        }
        for value, expected in fixtures.items():
            with self.subTest(value=value):
                self.assertEqual(crawler.canonical_url(value), expected)

    def test_external_hosts_are_rejected(self):
        for value in (
            "https://example.org/trainings",
            "https://fortunedigitalequity.org.example.org/about",
        ):
            with self.subTest(value=value):
                self.assertEqual(crawler.canonical_url(value), "")

    def test_unicode_service_path_keeps_its_canonical_public_url(self):
        value = "https://www.fortunedigitalequity.org/service-page/alfabetización-digital-básica-en-español"
        self.assertEqual(crawler.canonical_url(value), value)


class AuthorityTests(NoNetworkTestCase):
    def test_current_pages_and_active_services_are_answer_sources(self):
        for record in (row("/trainings"), row("/service-page/intro-to-computers", "booking-services")):
            with self.subTest(record=record):
                self.assertEqual(crawler.authority_for(record)[0], "answer")

    def test_posts_past_tech_fairs_and_archived_services_are_archives(self):
        fixtures = (
            row("/post/older-update", "blog-posts"),
            row("/techfair/techfair22"),
            row("/service-page/excel-archive", "booking-services"),
        )
        for record in fixtures:
            with self.subTest(record=record):
                self.assertEqual(crawler.authority_for(record)[0], "archive")

    def test_news_and_blog_categories_are_navigation_only(self):
        fixtures = (
            row("/news"),
            row("/news/general", "blog-categories"),
        )
        for record in fixtures:
            with self.subTest(record=record):
                self.assertEqual(crawler.authority_for(record)[0], "navigation")

    def test_public_author_profiles_are_excluded(self):
        self.assertEqual(
            crawler.authority_for(row("/profile/jschwartz/profile", "profiles"))[0],
            "excluded",
        )

    def test_test_member_duplicate_and_sample_routes_are_excluded(self):
        fixtures = (
            row("/test"),
            row("/members"),
            row("/service-page/sample-class", "booking-services"),
            row("/service-page/identity-theft-how-to-minimize-risk-1", "booking-services"),
        )
        for record in fixtures:
            with self.subTest(record=record):
                self.assertEqual(crawler.authority_for(record)[0], "excluded")

    def test_refresh_retains_recorded_authority_for_existing_url(self):
        record = row("/news")
        previous = {
            "authority": "archive",
            "authority_reason": "news index; posts are historical and date-bound",
        }

        self.assertEqual(
            crawler.reviewed_authority(record, previous),
            (
                "archive",
                "news index; posts are historical and date-bound",
            ),
        )

    def test_new_sitemap_url_is_held_out_of_answers_pending_review(self):
        self.assertEqual(
            crawler.reviewed_authority(row("/calendar/test")),
            (
                "excluded",
                "new public URL pending Fortune staff source review",
            ),
        )

    def test_new_blog_and_pagination_routes_receive_non_answer_classifications(self):
        self.assertEqual(
            crawler.reviewed_authority(row("/post/older-update", "blog-posts"))[0],
            "archive",
        )
        self.assertEqual(
            crawler.reviewed_authority(row("/news/page/2", "blog-categories"))[0],
            "navigation",
        )


class RecordUtilityTests(NoNetworkTestCase):
    def test_page_ids_are_stable_distinct_and_kind_prefixed(self):
        training = row("/trainings")
        service = row("/service-page/intro-to-computers", "booking-services")

        self.assertEqual(crawler.page_id(training), crawler.page_id(dict(training)))
        self.assertEqual(crawler.page_id(training), "page-trainings-f2e3ea17")
        self.assertTrue(crawler.page_id(service).startswith("service-"))
        self.assertNotEqual(crawler.page_id(training), crawler.page_id(service))

    def test_clean_blocks_normalizes_deduplicates_and_drops_boilerplate(self):
        blocks = crawler.clean_blocks([
            " Top of page ",
            "A useful public sentence. ",
            "A   useful public sentence.",
            "a useful public sentence.",
            "x",
            "A second useful sentence.",
        ])

        self.assertEqual(
            blocks,
            ["A useful public sentence.", "A second useful sentence."],
        )

    def test_internal_links_are_canonical_deduplicated_and_host_filtered(self):
        base = "https://www.fortunedigitalequity.org/trainings"
        links = crawler.internal_links(base, [
            "/about/",
            "https://fortunedigitalequity.org/about?draft=1#staff",
            "../contact/",
            "#classes",
            "https://example.org/contact",
        ])

        self.assertEqual(
            links,
            [
                "https://www.fortunedigitalequity.org/about",
                "https://www.fortunedigitalequity.org/contact",
            ],
        )
        self.assertTrue(all("fortunedigitalequity.org" in link for link in links))

    def test_page_extractor_ignores_scripts_and_keeps_public_internal_links(self):
        parser = crawler.PageExtractor()
        parser.feed("""
          <html><head><title>Public page</title></head><body>
          <main data-main-content="true">
            <h1>Digital skills</h1>
            <p>Learn computer basics.</p>
            <script>privateTrackerValue = 123;</script>
            <div data-replica-embed-placeholder="true"><p>Open embedded content from example.org</p></div>
            <a data-replica-live-action="true" href="/contact">Submit on Fortune's live site</a>
            <p data-replica-static-preview-note="true">Static preview — use the link for the live content.</p>
            <a href="/contact">Contact staff</a>
          </main>
          </body></html>
        """)

        self.assertEqual(" ".join(parser.title_parts), "Public page")
        self.assertIn("Digital skills", parser.headings)
        self.assertIn("Learn computer basics.", parser.blocks)
        self.assertNotIn("privateTrackerValue", " ".join(parser.blocks))
        self.assertNotIn("Open embedded content", " ".join(parser.blocks))
        self.assertNotIn("Submit on Fortune's live site", " ".join(parser.blocks))
        self.assertNotIn("Static preview", " ".join(parser.blocks))
        self.assertIn("/contact", parser.links)


class RenderedSnapshotRefreshTests(NoNetworkTestCase):
    def test_calendar_rows_keep_dates_attached_to_each_event(self):
        markup = """
        <li data-hook="daily-agenda-day">
          <span data-hook="daily-agenda-day-date">August 21</span>
          <li data-hook="daily-agenda-slot">
            <span aria-hidden="false">Tech Time: Foundations</span>
            <span aria-hidden="false">1:00 pm (1 hr)</span>
            <span aria-hidden="false">Raysean Richardson</span>
            <span aria-hidden="false">Main Office (LIC)</span>
            <span aria-hidden="false">15 spots left</span>
          </li>
        </li>
        <li data-hook="daily-agenda-day">
          <span data-hook="daily-agenda-day-date">August 24</span>
          <li data-hook="daily-agenda-slot">
            <span aria-hidden="false">Open Computer Lab Session</span>
            <span aria-hidden="false">1:30 pm (30 min)</span>
            <span aria-hidden="false">Milo Jones</span>
            <span aria-hidden="false">Main Office (LIC)</span>
          </li>
        </li>
        """

        events = crawler.calendar_events_from_snapshot(
            markup,
            "2026-08-20T21:47:14.793Z",
        )

        self.assertEqual([event["date"] for event in events], ["2026-08-21", "2026-08-24"])
        self.assertIn("Tech Time: Foundations · 1:00 pm", events[0]["label"])
        self.assertIn("Raysean Richardson · Main Office (LIC)", events[0]["label"])
        self.assertIn("Open Computer Lab Session · 1:30 pm", events[1]["label"])
        self.assertNotIn("Tech Time", events[1]["label"])

    def test_rendered_snapshot_refresh_uses_visible_full_text_not_thin_raw_blocks(self):
        page = {
            **row("/contact"),
            "id": "page-contact-test",
            "authority": "answer",
            "authority_reason": "current public page",
            "volatile": False,
            "status": 200,
            "title": "Old contact title",
            "description": "",
            "headings": ["Frequently Asked Questions"],
            "blocks": ["Raw crawl only retained the question."],
            "internal_links": [],
            "content_characters": 39,
            "content_hash": "old",
            "source_owner": "Fortune staff",
            "approval_state": "reviewed",
            "reviewed_on": "2026-08-20",
        }
        html = """<!doctype html><html><head><title>Contact | Fortune</title>
        <meta name=\"description\" content=\"Contact the Digital Equity Program.\"></head>
        <body><main id=\"PAGES_CONTAINER\"><h1>Frequently Asked Questions</h1>
        <section data-replica-static-disclosure><h2>Can I walk in?</h2>
        <p>Yes. Walk-in attendance is allowed for regular classes, with priority for advance registration.</p>
        </section><a href=\"/support\">Individual Support</a></main></body></html>"""
        expanded = html.encode("utf-8")
        compressed = gzip.compress(expanded, mtime=0)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            snapshots = root / "replica-snapshots"
            snapshots.mkdir()
            filename = "replica-snapshots/page-contact-test.html.gz"
            (root / filename).write_bytes(compressed)
            manifest = {
                "route_count": 1,
                "pages": [{
                    "id": page["id"],
                    "url": page["url"],
                    "final_url": page["url"],
                    "status": 200,
                    "file": filename,
                    "site_revision": 2063,
                    "source_sha256": hashlib.sha256(expanded).hexdigest(),
                    "snapshot_sha256": hashlib.sha256(compressed).hexdigest(),
                }],
            }
            refreshed = crawler.rendered_snapshot_pages({"pages": [page]}, manifest, snapshots)

        self.assertEqual(len(refreshed), 1)
        result = refreshed[0]
        self.assertEqual(result["title"], "Contact | Fortune")
        self.assertEqual(result["description"], "Contact the Digital Equity Program.")
        self.assertIn("Frequently Asked Questions", result["headings"])
        self.assertIn(
            "Yes. Walk-in attendance is allowed for regular classes, with priority for advance registration.",
            result["blocks"],
        )
        self.assertEqual(result["internal_links"], ["https://www.fortunedigitalequity.org/support"])
        self.assertEqual(result["source_owner"], "Fortune staff")
        self.assertEqual(result["approval_state"], "reviewed")
        self.assertEqual(result["rendered_snapshot"]["site_revision"], 2063)

    def test_rendered_snapshot_refresh_rejects_route_or_hash_drift(self):
        page = {**row("/contact"), "id": "page-contact-test"}
        manifest = {
            "route_count": 1,
            "pages": [{
                "id": page["id"],
                "url": page["url"],
                "final_url": page["url"],
                "status": 200,
                "file": "replica-snapshots/page-contact-test.html.gz",
                "site_revision": 2063,
                "source_sha256": "0" * 64,
                "snapshot_sha256": "0" * 64,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            snapshots = pathlib.Path(directory) / "replica-snapshots"
            snapshots.mkdir()
            with self.assertRaisesRegex(RuntimeError, "cannot read rendered snapshot"):
                crawler.rendered_snapshot_pages({"pages": [page]}, manifest, snapshots)


if __name__ == "__main__":
    unittest.main()
