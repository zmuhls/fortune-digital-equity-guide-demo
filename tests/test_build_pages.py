#!/usr/bin/env python3
"""Network-free tests for the allowlisted GitHub Pages builder."""

import importlib.util
import json
import pathlib
import re
import tempfile
import unittest
from unittest import mock


DEMO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = DEMO / "scripts" / "build_pages.py"
SPEC = importlib.util.spec_from_file_location("fortune_build_pages", SCRIPT)
build_pages = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_pages)


class IndexRouteTests(unittest.TestCase):
    def test_real_index_loads_all_184_unique_routes(self):
        routes = build_pages.load_routes()

        self.assertEqual(len(routes), 184)
        self.assertEqual(len({route["path"] for route in routes}), 184)
        self.assertEqual(len({route["pageId"] for route in routes}), 184)
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

    def test_unsafe_external_query_fragment_and_traversal_routes_are_rejected(self):
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
        page = {
            "url": "https://www.fortunedigitalequity.org/",
            "id": "page-home",
        }
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


class RouteShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (DEMO / "index.html").read_text(encoding="utf-8")

    def test_nested_shell_sets_page_context_and_two_level_asset_prefix(self):
        route = {
            "path": "/service-page/intro-to-computers",
            "sourceUrl": "https://www.fortunedigitalequity.org/service-page/intro-to-computers",
            "pageId": "service-intro",
        }
        shell = build_pages.render_shell(self.template, route)

        self.assertNotIn(build_pages.ROUTE_MARKER, shell)
        self.assertIn('href="../../styles.css?v=20260726-view-filter"', shell)
        self.assertIn('src="../../config.js"', shell)
        self.assertIn('src="../../site.js"', shell)
        self.assertIn('src="../../app.js"', shell)
        self.assertIn('window.FORTUNE_ASSET_BASE = "../../"', shell)
        self.assertIn("window.FORTUNE_STATIC_ROUTES = true", shell)
        self.assertIn(
            "window.FORTUNE_ROUTE_URL = window.FORTUNE_ROUTE_CONFIG.sourceUrl",
            shell,
        )
        payload = re.search(
            r"window\.FORTUNE_ROUTE_CONFIG = Object\.freeze\((\{.*?\})\);",
            shell,
        )
        self.assertIsNotNone(payload)
        self.assertEqual(json.loads(payload.group(1)), route)

    def test_root_shell_keeps_root_relative_assets(self):
        route = {
            "path": "/",
            "sourceUrl": "https://www.fortunedigitalequity.org/",
            "pageId": "page-home",
        }
        shell = build_pages.render_shell(self.template, route)

        self.assertIn('href="styles.css?v=20260726-view-filter"', shell)
        self.assertIn('src="site.js"', shell)
        self.assertIn('window.FORTUNE_ASSET_BASE = ""', shell)
        self.assertNotIn('href="../styles.css"', shell)


class ArtifactTests(unittest.TestCase):
    def _write_sources(self, root):
        (root / "index.html").write_text(
            """<!doctype html>
<link rel="stylesheet" href="styles.css">
<!-- ROUTE_CONFIG -->
<script src="config.js"></script>
<script src="site.js"></script>
<script src="app.js"></script>
""",
            encoding="utf-8",
        )
        for asset in build_pages.SHARED_ASSETS:
            if asset == "site-index.json":
                content = (DEMO / asset).read_text(encoding="utf-8")
            else:
                content = f"/* public fixture: {asset} */\n"
            (root / asset).write_text(content, encoding="utf-8")

        (root / "server.py").write_text(
            'OLLAMA_API_KEY = "fixture-that-must-not-publish"\n',
            encoding="utf-8",
        )
        (root / ".env").write_text("PRIVATE_FIXTURE=1\n", encoding="utf-8")
        (root / "deployment").mkdir()
        (root / "deployment" / "private.md").write_text("staff only\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "fixture.py").write_text("assert True\n", encoding="utf-8")

    def test_build_contains_only_allowlisted_assets_and_all_route_shells(self):
        routes = build_pages.load_routes()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            self._write_sources(root)
            output = root / "_site"
            with mock.patch.multiple(
                build_pages,
                ROOT=root,
                INDEX_PATH=root / "site-index.json",
                TEMPLATE_PATH=root / "index.html",
                OUTPUT_PATH=output,
            ):
                counts = build_pages.build(routes)

            actual = {
                pathlib.PurePosixPath(path.relative_to(output).as_posix())
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, build_pages.expected_files(routes))
            self.assertEqual(counts["indexed_routes"], 184)
            self.assertEqual(counts["route_shells"], 184)
            self.assertEqual(
                counts["allowlisted_root_files"],
                len(build_pages.SHARED_ASSETS) + 1,
            )
            self.assertEqual(
                counts["total_files"],
                len(routes) + len(build_pages.SHARED_ASSETS),
            )
            self.assertEqual(
                {path.name for path in output.iterdir() if path.is_file()},
                {"index.html", *build_pages.SHARED_ASSETS},
            )
            published_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("fixture-that-must-not-publish", published_text)
            self.assertFalse(any(path.is_symlink() for path in output.rglob("*")))

    def test_output_validator_rejects_backend_or_private_extras(self):
        routes = [{
            "path": "/",
            "sourceUrl": "https://www.fortunedigitalequity.org/",
            "pageId": "page-home",
        }]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for asset in build_pages.SHARED_ASSETS:
                (root / asset).write_text("public\n", encoding="utf-8")
            (root / "index.html").write_text(
                "window.FORTUNE_ROUTE_CONFIG\n"
                "window.FORTUNE_ROUTE_URL\n"
                "window.FORTUNE_STATIC_ROUTES = true\n"
                "window.FORTUNE_ASSET_BASE\n",
                encoding="utf-8",
            )
            (root / "server.py").write_text("private\n", encoding="utf-8")

            with self.assertRaisesRegex(build_pages.BuildError, "outside the allowlist"):
                build_pages.validate_output(root, routes)


if __name__ == "__main__":
    unittest.main()
