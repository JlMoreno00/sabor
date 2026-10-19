"""Release migration documentation checks."""
import unittest
from pathlib import Path

class ReleaseDocsTests(unittest.TestCase):
    def test_confirmation_migration_is_documented(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('README.md', 'CHANGELOG.md', 'examples/openclaw/TOOLS.md'):
            with self.subTest(path=name):
                self.assertIn('confirm=True', (root / name).read_text())
        changelog = (root / 'CHANGELOG.md').read_text()
        self.assertIn('## [0.1.1] - 2026-10-19', changelog)
        self.assertIn('Breaking for 0.x callers', changelog)
