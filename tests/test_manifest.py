import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicPluginTests(unittest.TestCase):
    def test_manifest_and_qml_identity_match(self):
        manifest = json.loads((ROOT / 'manifest.json').read_text())
        self.assertEqual(manifest['schemaVersion'], 1)
        self.assertRegex(manifest['id'], r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
        self.assertFalse(manifest['id'].startswith('omarchy.'))
        self.assertRegex(manifest['version'], r'^\d+\.\d+\.\d+$')
        self.assertIn('bar-widget', manifest['kinds'])
        widget = ROOT / manifest['entryPoints']['barWidget']
        self.assertTrue(widget.is_file())
        self.assertTrue(widget.resolve().is_relative_to(ROOT))
        self.assertIn(f'moduleName: "{manifest["id"]}"', widget.read_text())

    def test_public_assets_and_license_exist(self):
        manifest = json.loads((ROOT / 'manifest.json').read_text())
        self.assertEqual(manifest['license'], 'MIT')
        self.assertIn('MIT License', (ROOT / 'LICENSE').read_text())
        self.assertRegex(manifest['homepage'], r'^https://github.com/[^/]+/[^/]+$')
        self.assertTrue((ROOT / 'docs/preview.svg').is_file())

    def test_top_bar_remains_icon_only(self):
        source = (ROOT / 'CloudStatus.qml').read_text()
        start = source.index('  Row {\n    id: barRow')
        stop = source.index('  MouseArea {', start)
        bar = source[start:stop]
        self.assertEqual(len(re.findall(r'\bText\s*\{', bar)), 1)
        self.assertIn('text: root.glyph', bar)
        self.assertNotIn('barProgress', source)


if __name__ == '__main__':
    unittest.main()
