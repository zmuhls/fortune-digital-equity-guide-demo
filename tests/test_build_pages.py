#!/usr/bin/env python3
"""Network-free contracts for the reviewed GitHub Pages replica builder."""

import gzip
import hashlib
import html
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


DEMO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = DEMO / "scripts" / "build_pages.py"
SPEC = importlib.util.spec_from_file_location("fortune_build_pages", SCRIPT)
build_pages = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_pages)


HOME_ROUTE = {
    "path": "/",
    "sourceUrl": "https://www.fortunedigitalequity.org/",
    "pageId": "page-home-33c2c081",
    "page": {
        "id": "page-home-33c2c081",
        "url": "https://www.fortunedigitalequity.org/",
        "title": "Digital Equity Hub",
        "description": "Digital tools, workshops, access, and support.",
        "headings": ["Frequently Asked Questions"],
        "blocks": [
            "A Digital Navigator helping a Fortune Digital Equity student",
            "Frequently Asked Questions",
            "Can I attend a class without registering?",
        ],
        "authority": "answer",
        "status": 200,
        "lastmod": "2026-08-17",
    },
}


def snapshot_document(label="Home"):
    return (
        "<!doctype html><html lang=\"en\"><head>"
        f"<title>{label}</title><style>@font-face{{src:url('//static.parastorage.com/font.woff2')}}main{{color:#281a39}}</style>"
        "</head><body><main><h1>Public page</h1><span data-sr-only=\"true\">Context</span></main></body></html>"
    )


class IndexRouteTests(unittest.TestCase):
    def test_real_index_loads_all_138_current_public_html_routes(self):
        routes = build_pages.load_routes()

        self.assertEqual(len(routes), 138)
        self.assertEqual(len({route["path"] for route in routes}), 138)
        self.assertEqual(len({route["pageId"] for route in routes}), 138)
        self.assertIn("/", {route["path"] for route in routes})

    def test_route_path_canonicalizes_trailing_slash(self):
        self.assertEqual(
            build_pages.route_path("https://www.fortunedigitalequity.org/about/"),
            "/about",
        )
        self.assertEqual(
            build_pages.route_path("https://fortunedigitalequity.org/"),
            "/",
        )

    def test_external_query_fragment_and_traversal_routes_are_rejected(self):
        unsafe = (
            "https://example.org/about",
            "http://www.fortunedigitalequity.org/about",
            "https://www.fortunedigitalequity.org/about?draft=1",
            "https://www.fortunedigitalequity.org/about#staff",
            "https://www.fortunedigitalequity.org/../private",
        )
        for url in unsafe:
            with self.subTest(url=url), self.assertRaises(build_pages.BuildError):
                build_pages.route_path(url)

    def test_index_count_and_duplicate_routes_are_rejected(self):
        page = {"url": HOME_ROUTE["sourceUrl"], "id": HOME_ROUTE["pageId"]}
        fixtures = (
            {"unique_urls": 2, "pages": [page]},
            {"unique_urls": 2, "pages": [page, dict(page, id="page-home-copy")]},
        )
        for document in fixtures:
            with self.subTest(document=document), tempfile.TemporaryDirectory() as directory:
                index_path = pathlib.Path(directory) / "site-index.json"
                index_path.write_text(json.dumps(document), encoding="utf-8")
                with mock.patch.object(build_pages, "INDEX_PATH", index_path):
                    with self.assertRaises(build_pages.BuildError):
                        build_pages.load_routes()


class SnapshotRenderingTests(unittest.TestCase):
    def test_text_shell_css_is_small_and_contains_no_visual_assets(self):
        shell_css = (DEMO / "replica-shell.css").read_text(encoding="utf-8")

        self.assertLess(len(shell_css.encode("utf-8")), 5000)
        self.assertIn(".source-document", shell_css)
        self.assertRegex(
            shell_css,
            r"\.source-faq dd\s*\{[^}]*display:\s*block",
        )
        self.assertIn(".source-outline", shell_css)
        self.assertIn(".source-navigation", shell_css)
        self.assertIn(".source-list", shell_css)
        self.assertIn(".source-breadcrumb", shell_css)
        self.assertIn(".source-disclosure", shell_css)
        self.assertIn("#fortune-sidecar-host", shell_css)
        self.assertNotIn("background-image", shell_css)
        self.assertNotIn("url(", shell_css)

    def test_nested_text_source_uses_depth_aware_assets_and_one_trusted_script(self):
        route = {
            "path": "/service-page/understanding-computers",
            "sourceUrl": "https://www.fortunedigitalequity.org/service-page/understanding-computers",
            "pageId": "service-intro",
            "page": {
                "title": "Understanding Computers",
                "description": "Learn the basic parts of a computer and how they work.",
                "headings": ["What you will learn"],
                "blocks": [
                    "Image of a laptop computer",
                    "A collage of class photos",
                    "an icon representing email safety",
                    "Logo for a partner organization",
                    "What you will learn",
                    "Identify hardware, software, storage, and common ports.",
                ],
                "authority": "answer",
                "status": 200,
            },
        }

        shell = build_pages.render_text_page(route, "../../")

        self.assertIn(build_pages.REPLICA_MARKER, shell)
        self.assertIn('data-fortune-text-view="true"', shell)
        self.assertIn(
            f'href="../../replica-shell.css?v={build_pages.REPLICA_SHELL_CSS_VERSION}"',
            shell,
        )
        self.assertIn(
            f'src="../../replica-shell.js?v={build_pages.REPLICA_SHELL_JS_VERSION}"',
            shell,
        )
        self.assertIn(f'data-source-url="{route["sourceUrl"]}"', shell)
        self.assertEqual(shell.lower().count("<script"), 1)
        self.assertIn("Learn the basic parts of a computer", shell)
        self.assertIn("Identify hardware, software, storage", shell)
        self.assertNotIn("Image of a laptop", shell)
        self.assertNotIn("collage of class photos", shell)
        self.assertNotIn("icon representing email safety", shell)
        self.assertNotIn("Logo for a partner", shell)
        self.assertNotIn("<img", shell.lower())
        self.assertNotIn("<style", shell.lower())
        self.assertNotIn("static.wix", shell.lower())
        self.assertIn("form-action &#x27;none&#x27;", shell)
        self.assertIn("img-src &#x27;none&#x27;", shell)
        self.assertIn('content="noindex,nofollow,noarchive"', shell)

    def test_root_text_source_keeps_root_relative_assets(self):
        shell = build_pages.render_text_page(HOME_ROUTE, "")

        self.assertIn(
            f'href="replica-shell.css?v={build_pages.REPLICA_SHELL_CSS_VERSION}"',
            shell,
        )
        self.assertIn(
            f'src="replica-shell.js?v={build_pages.REPLICA_SHELL_JS_VERSION}"',
            shell,
        )
        self.assertNotIn('href="../replica-shell.css', shell)

    def test_detected_faq_pairs_use_visible_semantic_question_and_answer_markup(self):
        route = {
            **HOME_ROUTE,
            "page": {
                **HOME_ROUTE["page"],
                "headings": ["Frequently Asked Questions", "More information"],
                "blocks": [
                    "Frequently Asked Questions",
                    "Can I attend a class without registering?",
                    "Yes. You can attend regular classes without registering.",
                    "What should I bring?",
                    "Bring the information you need for the class.",
                    "More information",
                    "This remains ordinary source text.",
                ],
            },
        }

        shell = build_pages.render_text_page(route, "")

        self.assertIn('<section class="source-faq" aria-labelledby="source-faq-1">', shell)
        self.assertIn('<h2 id="source-faq-1">Frequently Asked Questions</h2>', shell)
        self.assertIn('<dl class="source-faq__list">', shell)
        self.assertIn('<dt>Can I attend a class without registering?</dt>', shell)
        self.assertIn('<dd>Yes. You can attend regular classes without registering.</dd>', shell)
        self.assertIn('<dt>What should I bring?</dt>', shell)
        self.assertIn('<dd>Bring the information you need for the class.</dd>', shell)
        self.assertNotIn('<p>Can I attend a class without registering?</p>', shell)
        self.assertNotIn("<details", shell)
        self.assertIn('<h2 id="source-section-1">More information</h2>', shell)
        self.assertIn('<p>This remains ordinary source text.</p>', shell)

    def test_current_home_and_contact_faqs_render_all_source_backed_answers(self):
        routes = {
            route["path"]: route
            for route in build_pages.load_routes()
            if route["path"] in {"/", "/contact"}
        }

        for path in ("/", "/contact"):
            with self.subTest(path=path):
                shell = build_pages.render_text_page(routes[path], "")
                self.assertIn('<section class="source-faq"', shell)
                self.assertEqual(shell.count("<dt>"), 4)
                self.assertEqual(shell.count("<dd>"), 4)
                self.assertNotIn("<details", shell)
                fragments = build_pages.source_fragments(routes[path]["page"])
                faq_index = next(
                    index
                    for index, (text, is_heading) in enumerate(fragments)
                    if is_heading and build_pages.is_faq_heading(text)
                )
                for index in range(faq_index + 1, faq_index + 9, 2):
                    question, question_is_heading = fragments[index]
                    answer, answer_is_heading = fragments[index + 1]
                    self.assertFalse(question_is_heading)
                    self.assertFalse(answer_is_heading)
                    self.assertTrue(build_pages.is_faq_question(question))
                    self.assertIn(f"<dt>{html.escape(question)}</dt>", shell)
                    self.assertIn(f"<dd>{html.escape(answer)}</dd>", shell)

    def test_public_reference_route_keeps_snapshot_text_outside_guide_retrieval(self):
        route = {
            **HOME_ROUTE,
            "page": {
                **HOME_ROUTE["page"],
                "authority": "archive",
                "blocks": ["A stale class schedule that the model cannot retrieve."],
            },
        }
        shell = build_pages.render_text_page(route, "")

        self.assertIn("Public source snapshot", shell)
        self.assertIn("A stale class schedule that the model cannot retrieve.", shell)

    @staticmethod
    def _current_snapshot_route(path):
        routes = build_pages.load_routes()
        snapshots = build_pages.load_snapshots(routes)
        route = next(route for route in routes if route["path"] == path)
        return routes, route, snapshots[route["sourceUrl"]]["html"]

    def test_current_home_snapshot_preserves_sections_faqs_and_all_21_text_links(self):
        routes, route, snapshot_html = self._current_snapshot_route("/")
        projection = build_pages.render_snapshot_source(
            snapshot_html, route["page"]["title"], "", routes
        )
        self.assertIsNotNone(projection)
        outline, content, footer = projection

        headings = (
            "WELCOME! TO THE FORTUNE SOCIETY DIGITAL EQUITY HUB",
            "Choose A Service",
            "Explore Learning Paths (coming soon)",
            "Hear From Past Participants",
            "Still Not Sure Where to Start?",
            "Frequently Asked Questions",
            "Explore More",
        )
        position = -1
        for heading in headings:
            position = content.find(heading, position + 1)
            self.assertGreaterEqual(position, 0, heading)
            self.assertIn(heading, outline)

        questions_and_answers = (
            (
                "Do I need to attend all scheduled classes in a month?",
                "Regularly scheduled Fortune Digital Equity classes have rolling attendance",
            ),
            (
                "Do I need to register for a class in order to attend?",
                "we allow walk-in attendance",
            ),
            (
                "Can I get assistance with digital skills not listed in the class catalog",
                "we'll schedule some one-on-one time with you",
            ),
            (
                "Do I automatically qualify for a laptop as a Fortune Society participant?",
                "Laptop access and supplies are limited",
            ),
        )
        for question, answer in questions_and_answers:
            self.assertIn(question, content)
            self.assertIn(answer, html.unescape(content))

        action_labels = (
            "CHOOSE A SERVICE", "EXPLORE LEARNING PATHS", "MORE ABOUT US",
            "FIND A WORKSHOP", "VIEW CALENDAR", "GET A DEVICE", "INTERNSHIP",
            "GET SUPPORT", "EXPLORE TOOLS", "CONTACT US", "ATTEND AN OPEN LAB",
            "MORE", "OTHER RESOURCES", "VOLUNTEER", "SPECIAL EVENTS", "DONATE",
            "TECH FAIR", "PROGRAM UPDATES",
        )
        for label in action_labels:
            self.assertIn(f">{label}</a>", content)
        self.assertEqual(content.count('class="source-standalone-link"'), 18)
        self.assertIn('href="mediakit/">Media Kit</a>', footer)
        self.assertIn('href="tel:(212) 691-7554">(212) 691-7554</a>', footer)
        self.assertIn('href="mailto:fstrain@fortunesociety.org">FSTrain@FortuneSociety.org</a>', footer)
        self.assertEqual(footer.count("<li>"), 3)
        self.assertNotIn("Facebook", footer)
        self.assertNotIn("SITE_FOOTER", content)

    def test_source_header_navigation_is_captured_grouped_and_localized(self):
        routes, _, snapshot_html = self._current_snapshot_route("/")
        navigation = build_pages.render_source_navigation(snapshot_html, "", routes, "/")

        self.assertIn('<nav class="source-navigation" aria-label="Site">', navigation)
        self.assertNotIn("<details", navigation)
        self.assertNotIn("<summary", navigation)
        self.assertIn('<a aria-current="page" href="index.html">HOME</a>', navigation)
        self.assertIn('<a href="about/">ABOUT</a>', navigation)
        self.assertIn('<span class="source-navigation__label">SERVICES</span>', navigation)
        self.assertIn('<span class="source-navigation__label">RESOURCES</span>', navigation)
        self.assertIn('<a href="calendar/">CALENDAR</a>', navigation)
        self.assertIn('<a href="contact/">CONTACT</a>', navigation)

        service_labels = (
            "Regular Workshops", "Individual Support", "Special Events &amp; Sessions",
            "Professional Digital Foundations", "Microsoft Certifications", "Tech Fair",
        )
        resource_labels = (
            "Practice Your Skills", "Device Distribution", "Find Opportunities",
            "Other Digital Resources",
        )
        for label in (*service_labels, *resource_labels):
            self.assertIn(f">{label}</a>", navigation)
        self.assertNotIn("fortunedigitalequity.org", navigation)

        nested_navigation = build_pages.render_source_navigation(
            snapshot_html, "../../", routes, "/workshops"
        )
        self.assertIn('<a href="../../index.html">HOME</a>', nested_navigation)
        self.assertIn(
            '<a aria-current="page" href="../../workshops/">Regular Workshops</a>',
            nested_navigation,
        )

    def test_snapshot_primary_heading_is_one_visible_source_h1(self):
        expected_headings = {
            "/": "WELCOME! TO THE FORTUNE SOCIETY DIGITAL EQUITY HUB",
            "/workshops": "Digital Skills Workshops",
            "/service-page/intro-to-email": "Intro to Email",
        }
        routes = build_pages.load_routes()
        snapshots = build_pages.load_snapshots(routes)
        home_snapshot = snapshots[next(route["sourceUrl"] for route in routes if route["path"] == "/")]["html"]

        for path, expected_heading in expected_headings.items():
            with self.subTest(path=path):
                route = next(route for route in routes if route["path"] == path)
                depth = 0 if path == "/" else len(path.strip("/").split("/"))
                shell = build_pages.render_text_page(
                    route,
                    "../" * depth,
                    routes,
                    snapshots[route["sourceUrl"]]["html"],
                    home_snapshot,
                )
                self.assertEqual(shell.count("<h1"), 1)
                self.assertIn(f">{expected_heading}</h1>", shell)
                self.assertLess(shell.find(f">{expected_heading}</h1>"), shell.find("<footer"))

    def test_current_workshops_snapshot_keeps_linked_category_and_course_lists(self):
        routes, route, snapshot_html = self._current_snapshot_route("/workshops")
        projection = build_pages.render_snapshot_source(
            snapshot_html, route["page"]["title"], "", routes
        )
        self.assertIsNotNone(projection)
        _, content, _ = projection

        self.assertIn('<ul class="source-list">', content)
        self.assertIn("All Services", content)
        self.assertIn("Digital Knowledge Base", content)
        self.assertEqual(content.count('<li><a href="service-page/'), 64)
        self.assertIn(
            '<li><a href="service-page/intro-to-email/">Intro to Email</a></li>',
            content,
        )
        self.assertIn(
            '<li><a href="service-page/excel-formulas-functions/">Excel - Formulas &amp; Functions</a></li>',
            content,
        )
        self.assertNotIn('href="https://www.fortunedigitalequity.org/service-page/', content)

    def test_current_service_snapshot_keeps_breadcrumb_and_visible_source_order(self):
        routes, route, snapshot_html = self._current_snapshot_route("/service-page/intro-to-email")
        projection = build_pages.render_snapshot_source(
            snapshot_html, route["page"]["title"], "../../", routes
        )
        self.assertIsNotNone(projection)
        _, content, _ = projection

        expected_order = (
            "Service Details",
            'class="source-breadcrumb"',
            "This service is not available, please contact for more information.",
            "Intro to Email",
            "Main Office (LIC) | SRP (Bronx) | Fortune Academy (Harlem)",
            "Description",
            "Upcoming Sessions",
        )
        position = -1
        for value in expected_order:
            position = content.find(value, position + 1)
            self.assertGreaterEqual(position, 0, value)
        self.assertIn('href="../../index.html">Home</a>', content)
        self.assertIn('href="../../catalog/">Service list</a>', content)
        self.assertNotIn("No sessions in the next", content)
        self.assertNotIn("Time Zone:", content)

    def test_invalid_or_active_snapshot_markup_is_rejected_before_build(self):
        fixtures = (
            "<script src=\"https://example.org/run.js\"></script>",
            "<form action=\"https://example.org/collect\"></form>",
            "<iframe src=\"https://example.org\"></iframe>",
            "<div onclick=\"run()\"></div>",
            "<a href=\"javascript:run()\">run</a>",
            '<script id="wix-viewer-model">X-XSRF-TOKEN</script>',
        )
        for active in fixtures:
            with self.subTest(active=active), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                html_value = snapshot_document().replace("</body>", active + "</body>")
                self._write_manifest_fixture(root, html_value)
                with self._patched_snapshot_paths(root):
                    with self.assertRaises(build_pages.BuildError):
                        build_pages.load_snapshots([HOME_ROUTE])

    @staticmethod
    def _write_manifest_fixture(root, html_value, *, revision=1837):
        snapshots = root / "replica-snapshots"
        snapshots.mkdir()
        expanded = html_value.encode()
        compressed = gzip.compress(expanded, compresslevel=9, mtime=0)
        relative = f"replica-snapshots/{HOME_ROUTE['pageId']}.html.gz"
        (root / relative).write_bytes(compressed)
        manifest = {
            "captured_at": "2026-08-03T21:00:00+00:00",
            "source_origin": "https://www.fortunedigitalequity.org",
            "route_count": 1,
            "capture": {
                "browser": {"name": "firefox", "version": "153.0"},
                "viewport": {"width": 1440, "height": 1200},
            },
            "pages": [{
                "id": HOME_ROUTE["pageId"],
                "url": HOME_ROUTE["sourceUrl"],
                "final_url": HOME_ROUTE["sourceUrl"],
                "path": HOME_ROUTE["path"],
                "file": relative,
                "status": 200,
                "title": "Home",
                "site_revision": revision,
                "etag": 'W/"fixture"',
                "source_bytes": len(expanded),
                "snapshot_bytes": len(compressed),
                "source_sha256": hashlib.sha256(expanded).hexdigest(),
                "snapshot_sha256": hashlib.sha256(compressed).hexdigest(),
            }],
        }
        (root / "replica-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    @staticmethod
    def _patched_snapshot_paths(root):
        return mock.patch.multiple(
            build_pages,
            ROOT=root,
            SNAPSHOT_ROOT=root / "replica-snapshots",
            MANIFEST_PATH=root / "replica-manifest.json",
        )

    def test_manifest_hash_revision_and_route_parity_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self._write_manifest_fixture(root, snapshot_document())
            with self._patched_snapshot_paths(root):
                loaded = build_pages.load_snapshots([HOME_ROUTE])
                self.assertEqual(set(loaded), {HOME_ROUTE["sourceUrl"]})

            manifest_path = root / "replica-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["pages"][0]["snapshot_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self._patched_snapshot_paths(root):
                with self.assertRaisesRegex(build_pages.BuildError, "hash or size"):
                    build_pages.load_snapshots([HOME_ROUTE])


class ArtifactTests(unittest.TestCase):
    def _write_public_sources(self, root):
        (root / "index.html").write_text(
            "<!doctype html><html><head></head><body>"
            "<aside class=\"guide\"></aside><script src=\"app.js\"></script>"
            "</body></html>",
            encoding="utf-8",
        )
        for asset in build_pages.SHARED_ASSETS:
            (root / asset).write_text(f"public fixture: {asset}\n", encoding="utf-8")

        (root / "server.py").write_text(
            'OLLAMA_API_KEY = "fixture-that-must-not-publish"\n', encoding="utf-8"
        )
        (root / ".env").write_text("PRIVATE_FIXTURE=1\n", encoding="utf-8")

    def test_build_contains_only_replica_routes_sidecar_and_allowlisted_assets(self):
        routes = [
            HOME_ROUTE,
            {
                "path": "/about",
                "sourceUrl": "https://www.fortunedigitalequity.org/about",
                "pageId": "page-about",
                "page": {
                    "title": "About Digital Equity",
                    "description": "About the Digital Equity program.",
                    "headings": [],
                    "blocks": ["The program supports Fortune participants."],
                    "authority": "answer",
                    "status": 200,
                },
            },
        ]
        snapshots = {
            route["sourceUrl"]: {"html": snapshot_document(route["pageId"])}
            for route in routes
        }
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = root / "_site"
            self._write_public_sources(root)
            with mock.patch.multiple(
                build_pages,
                ROOT=root,
                OUTPUT_PATH=output,
                SIDECAR_TEMPLATE_PATH=root / "index.html",
            ):
                counts = build_pages.build(routes, snapshots)

            actual = {
                pathlib.PurePosixPath(path.relative_to(output).as_posix())
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, build_pages.expected_files(routes))
            self.assertEqual(counts["indexed_routes"], 2)
            self.assertEqual(counts["replica_routes"], 2)
            self.assertEqual(counts["total_files"], len(routes) + len(build_pages.SHARED_ASSETS) + 1)
            self.assertTrue((output / "sidecar.html").is_file())
            self.assertIn(build_pages.REPLICA_MARKER, (output / "about" / "index.html").read_text())
            published_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("fixture-that-must-not-publish", published_text)
            self.assertFalse(any(path.is_symlink() for path in output.rglob("*")))

    def test_output_validator_rejects_backend_or_private_extras(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for asset in build_pages.SHARED_ASSETS:
                (root / asset).write_text("public\n", encoding="utf-8")
            (root / build_pages.SIDECAR_OUTPUT).write_text("sidecar\n", encoding="utf-8")
            (root / "index.html").write_text(
                f"<html {build_pages.REPLICA_MARKER} data-fortune-text-view=\"true\"><body>"
                '<script src="replica-shell.js"></script></body></html>',
                encoding="utf-8",
            )
            (root / "server.py").write_text("private\n", encoding="utf-8")

            with self.assertRaisesRegex(build_pages.BuildError, "outside the allowlist"):
                build_pages.validate_output(root, [HOME_ROUTE])


if __name__ == "__main__":
    unittest.main()
