"""The mobile mirror must be a source capture, not desktop content scaled down."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_mobile_fixture', ROOT / 'scripts/build_pages.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class NativeMobileTests(unittest.TestCase):
    def route(self, path):
        return {'path': path, 'sourceUrl': 'https://www.fortunedigitalequity.org' + path,
                'pageId': 'page-fixture', 'page': {}}

    def test_mobile_variant_keeps_canonical_route_context_and_same_root_assets(self):
        route = self.route('/service-page/example')
        source = '<html><head><title>Example</title></head><body>Example</body></html>'
        rendered = builder.render_visual_snapshot_page(route, '../../', [route], source, has_mobile_variant=True)
        self.assertIn('data-mobile-src="../../service-page/example/replica-mobile.html"', rendered)
        self.assertIn('data-source-url="https://www.fortunedigitalequity.org/service-page/example"', rendered)
        self.assertIn('data-replica-layout="desktop"', rendered)

    def test_native_mobile_capture_does_not_load_itself_recursively(self):
        route = self.route('/')
        source = '<html><head></head><body class="device-mobile-optimized">Mobile</body></html>'
        rendered = builder.render_visual_snapshot_page(route, '', [route], source, mobile_layout=True)
        self.assertIn('data-replica-layout="mobile"', rendered)
        self.assertIn('data-mobile-src=""', rendered)
        self.assertIn('device-mobile-optimized', rendered)

    def test_mobile_output_is_explicitly_allowlisted_for_every_route(self):
        routes = [self.route('/'), self.route('/about'), self.route('/service-page/example')]
        files = builder.expected_files(routes, with_mobile=True)
        self.assertEqual({str(path) for path in files if path.name == 'replica-mobile.html'},
                         {'replica-mobile.html', 'about/replica-mobile.html', 'service-page/example/replica-mobile.html'})
        self.assertNotIn(Path('replica-mobile.html'), builder.expected_files(routes))


if __name__ == '__main__':
    unittest.main()
