#!/usr/bin/env python3
"""Network-free contracts for the reviewed GitHub Pages replica builder."""

import gzip
import hashlib
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
    def test_nested_replica_uses_depth_aware_assets_and_one_trusted_script(self):
        route = {
            "path": "/service-page/understanding-computers",
            "sourceUrl": "https://www.fortunedigitalequity.org/service-page/understanding-computers",
            "pageId": "service-intro",
        }

        shell = build_pages.render_snapshot(snapshot_document(), route, "../../")

        self.assertIn(build_pages.REPLICA_MARKER, shell)
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
        self.assertIn("form-action &#x27;none&#x27;", shell)
        self.assertIn('content="noindex,nofollow,noarchive"', shell)
        self.assertLess(shell.index("Content-Security-Policy"), shell.index("<style>"))
        self.assertIn("url('https://static.parastorage.com/font.woff2')", shell)

    def test_root_replica_keeps_root_relative_assets(self):
        shell = build_pages.render_snapshot(snapshot_document(), HOME_ROUTE, "")

        self.assertIn(
            f'href="replica-shell.css?v={build_pages.REPLICA_SHELL_CSS_VERSION}"',
            shell,
        )
        self.assertIn(
            f'src="replica-shell.js?v={build_pages.REPLICA_SHELL_JS_VERSION}"',
            shell,
        )
        self.assertNotIn('href="../replica-shell.css', shell)

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
                f"<html {build_pages.REPLICA_MARKER}><body>"
                '<script src="replica-shell.js"></script></body></html>',
                encoding="utf-8",
            )
            (root / "server.py").write_text("private\n", encoding="utf-8")

            with self.assertRaisesRegex(build_pages.BuildError, "outside the allowlist"):
                build_pages.validate_output(root, [HOME_ROUTE])


if __name__ == "__main__":
    unittest.main()
