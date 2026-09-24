"""Service availability uses its own source capture date, including mobile."""

import importlib.util
import gzip
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("service_assets_builder", ROOT / "scripts/build_pages.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ServiceAssetTests(unittest.TestCase):
    def render(self, captured_at, mobile=False):
        route = {"path": "/service-page/example", "sourceUrl": "https://www.fortunedigitalequity.org/service-page/example", "pageId": "service-example", "page": {}}
        return builder.render_visual_snapshot_page(
            route, "../../", [route], "<html><head></head><body><main>Example</main></body></html>",
            captured_at=captured_at, mobile_layout=mobile,
        )

    def test_service_runtime_and_calendar_styles_are_public_shared_assets(self):
        self.assertIn("replica-services.js", builder.SHARED_ASSETS)
        self.assertIn("replica-calendar.css", builder.SHARED_ASSETS)
        rendered = self.render("2026-09-21T20:47:21.464Z")
        self.assertIn('href="../../replica-calendar.css?v=', rendered)
        self.assertEqual(rendered.count("<script"), 1)
        self.assertIn('name="fortune-replica-captured-at" content="2026-09-21T20:47:21.464Z"', rendered)
        self.assertIn('data-captured-at="2026-09-21T20:47:21.464Z"', rendered)

    def test_mobile_output_keeps_its_own_capture_timestamp(self):
        desktop = self.render("2026-09-21T20:47:21.464Z")
        mobile = self.render("2026-09-24T03:00:00Z", mobile=True)
        self.assertIn('content="2026-09-24T03:00:00Z"', mobile)
        self.assertNotIn("2026-09-21T20:47:21.464Z", mobile)
        self.assertNotIn("2026-09-24T03:00:00Z", desktop)
        self.assertIn('data-replica-layout="mobile"', mobile)

    def test_calendar_notice_uses_layout_capture_date_in_site_timezone(self):
        route = {"path": "/calendar", "sourceUrl": builder.CALENDAR_SOURCE_URL, "pageId": "calendar", "page": {"source_captured_at": "2026-09-21T20:47:21.464Z"}}
        rendered = builder.render_visual_snapshot_page(
            route, "../", [route], '<html><head></head><body><main><div data-hook="DailyAgenda-wrapper"></div></main></body></html>',
            captured_at="2026-09-24T03:00:00Z", mobile_layout=True,
        )
        self.assertIn("Schedule captured 2026-09-23.", rendered)
        self.assertNotIn("Schedule captured 2026-09-21", rendered)

    def test_loaded_page_metadata_inherits_the_actual_source_manifest_timestamp(self):
        routes = builder.load_routes()
        snapshots = builder.load_snapshots(routes)
        manifest = json.loads((ROOT / "replica-manifest.json").read_text())
        by_url = {page["url"]: page for page in manifest["pages"]}
        for url, snapshot in snapshots.items():
            self.assertEqual(snapshot["captured_at"], by_url[url].get("captured_at") or manifest["captured_at"])

    def test_speaker_controls_preserve_source_style_and_open_actual_question_forms(self):
        manifest = json.loads((ROOT / "replica-manifest.json").read_text())
        for page in manifest["pages"]:
            if page["path"] not in {"/deiqa", "/events/ai-qa", "/techfair/qa"}:
                continue
            for mobile in (False, True):
                with self.subTest(path=page["path"], mobile=mobile):
                    path = ROOT / page["file"]
                    if mobile:
                        path = ROOT / "replica-mobile" / page["file"]
                    with gzip.open(path, "rt") as source:
                        captured = source.read()
                    route = {"path": page["path"], "sourceUrl": page["url"], "pageId": page["id"], "page": {}}
                    rendered = builder.render_visual_snapshot_page(route, "../", [route], captured, mobile_layout=mobile)
                    self.assertIn('data-replica-question-form="true"', rendered)
                    self.assertEqual(rendered.count('data-replica-question-form="true"'), 6 if page["path"] == "/techfair/qa" else 1)
                    self.assertIn(f'href="{page["url"]}" target="_blank" rel="noopener noreferrer"', rendered)
                    self.assertIn('title="Open question form on Fortune’s site"', rendered)
                    self.assertRegex(rendered, r'class="StylableButton2545352419__root style-[a-z0-9]+__root wixui-button"')
                    self.assertNotIn('data-replica-static-control-label="true">Speaker:', rendered)
                    self.assertNotIn('data-replica-static-control-label="true">Panel:', rendered)

    def test_all_captured_post_print_and_gallery_controls_have_exact_source_handoffs(self):
        manifest = json.loads((ROOT / "replica-manifest.json").read_text())
        posts = [page for page in manifest["pages"] if page["path"].startswith("/post/")]
        self.assertEqual(len(posts), 20)
        for page in posts:
            for mobile in (False, True):
                with self.subTest(path=page["path"], mobile=mobile):
                    path = ROOT / page["file"] if not mobile else ROOT / "replica-mobile" / page["file"]
                    with gzip.open(path, "rt") as source:
                        captured = source.read()
                    route = {"path": page["path"], "sourceUrl": page["url"], "pageId": page["id"], "page": {}}
                    rendered = builder.render_visual_snapshot_page(route, "../", [route], captured, mobile_layout=mobile)
                    self.assertIn('data-replica-post-action="true"', rendered)
                    self.assertGreaterEqual(rendered.count('data-replica-post-icon="true"'), 5)
                    self.assertIn(builder.POST_CONTROL_ICONS["Print Post"], rendered)
                    self.assertNotIn('>Share via Facebook on the Digital Equity site</a>', rendered)
                    self.assertIn(f'href="{page["url"]}" target="_blank" rel="noopener noreferrer"', rendered)
                    if page["path"] in builder.POST_GALLERIES:
                        gallery = builder.POST_GALLERIES[page["path"]]["native" if mobile else "desktop"]
                        self.assertEqual(gallery["image_count"], 13 if page["path"].endswith("intro-to-robotics") else 2)
                        self.assertIn(gallery["css"], rendered)
                        self.assertIn('name="fortune-replica-gallery-captured-at"', rendered)
                        for image in gallery["images"]:
                            self.assertIn(f'src="{image["src"]}"', rendered)
                            self.assertIn(image["alt"], rendered)
                        self.assertNotIn('<button', gallery["html"])
                    for label in ("Print Post", "Previous gallery item", "Next gallery item"):
                        self.assertNotIn(f'data-replica-static-control-label="true">{label}', rendered)
                        if label in builder.POST_GALLERY_CONTROLS and f'>{label}</p>' in captured:
                            gallery = builder.POST_GALLERY_CONTROLS[label]
                            self.assertIn(gallery["icon"], rendered)
                            self.assertIn(f'class="{gallery["class"]}" data-hook="{gallery["data_hook"]}"', rendered)
                            self.assertNotIn(f'>{label}</a>', rendered)

    def test_native_class_filter_hands_off_to_actual_widget_without_invented_category_routes(self):
        manifest = json.loads((ROOT / "replica-mobile/replica-manifest.json").read_text())
        for page in manifest["pages"]:
            if page["path"] not in {"/catalog", "/workshops"}:
                continue
            with gzip.open(ROOT / "replica-mobile" / page["file"], "rt") as source:
                captured = source.read()
            route = {"path": page["path"], "sourceUrl": page["url"], "pageId": page["id"], "page": {}}
            rendered = builder.render_visual_snapshot_page(route, "../", [route], captured, mobile_layout=True)
            self.assertIn('data-replica-class-filter="true"', rendered)
            self.assertIn('title="Filter classes on Fortune’s site"', rendered)
            self.assertIn(f'href="{page["url"]}" target="_blank" rel="noopener noreferrer"', rendered)
            self.assertNotIn('data-replica-static-control-label="true">Categories filter', rendered)


if __name__ == "__main__":
    unittest.main()
