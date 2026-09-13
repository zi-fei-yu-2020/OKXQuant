"""In-process SPA regressions: no lifespan, worker, credentials or external APIs."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import dashboard.app as dashboard


SPA_PATHS = (
    '/', '/trading', '/factors', '/news', '/lab', '/history',
    '/docs', '/docs/', '/docs/getting-started', '/doc',
    '/admin', '/admin/', '/admin/settings',
)


class OKXQuantFrontendFallbackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dist = Path(temp.name) / 'dist'
        dist_patch = patch.object(dashboard, 'VUE_DIST_DIR', str(self.dist))
        dist_patch.start()
        self.addCleanup(dist_patch.stop)
        # Do not enter TestClient's context: that would start ASGI lifespan.
        self.client = TestClient(dashboard.app, follow_redirects=False)
        self.addCleanup(self.client.close)
        for name in ('start_dashboard_background_worker', 'request_cache_refresh', 'update_cache_cycle'):
            guard = patch.object(dashboard, name, side_effect=AssertionError('SPA must not start real work'))
            guard.start()
            self.addCleanup(guard.stop)

    def assert_build_missing(self):
        for path in SPA_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertIn('okxquant_frontend/dist/index.html', response.text)
                self.assertIn('npm run build', response.text)
                self.assertIn('no-store', response.headers['cache-control'])
                self.assertNotIn('location', response.headers)
                self.assertNotIn('window.location', response.text)
                self.assertNotIn('<script', response.text.lower())
                self.assertNotIn('http-equiv', response.text.lower())

    def test_missing_dist_returns_503_instead_of_self_redirect(self):
        self.assert_build_missing()

    def test_dist_without_index_returns_503(self):
        self.dist.mkdir()
        self.assert_build_missing()

    def test_built_shell_still_served_for_all_spa_routes(self):
        self.dist.mkdir()
        shell = '<!doctype html><html><body>SPA test shell</body></html>'
        (self.dist / 'index.html').write_text(shell, encoding='utf-8')
        for path in SPA_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.text, shell)
                self.assertIn('no-store', response.headers['cache-control'])


if __name__ == '__main__':
    unittest.main()
