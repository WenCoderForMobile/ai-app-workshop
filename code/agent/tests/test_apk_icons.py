import sys
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from program_agent.apk_builder import ApkBuilder


class ApkIconTest(unittest.TestCase):
    def test_existing_project_gets_icons_and_manifest_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root/'plugin_src'
            manifest = src/'app/src/main/AndroidManifest.xml'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('<manifest><application /></manifest>')
            builder = ApkBuilder(Path(__file__).resolve().parents[1])
            builder._sync_app_icon(src)
            app = ET.parse(manifest).getroot().find('application')
            ns = '{http://schemas.android.com/apk/res/android}'
            self.assertEqual(app.get(ns+'icon'), '@mipmap/ic_factory_app')
            self.assertEqual(app.get(ns+'roundIcon'), '@mipmap/ic_factory_app_round')
            icons = list((src/'app/src/main/res').glob('mipmap-*/*.png'))
            self.assertEqual(len(icons), 10)
            self.assertTrue(all(p.read_bytes().startswith(b'\x89PNG\r\n\x1a\n') for p in icons))
            self.assertIsNone(ET.parse(manifest).getroot().find('.//intent-filter'))

    def test_custom_project_icon_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            manifest = src/'app/src/main/AndroidManifest.xml'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:icon="@mipmap/custom" android:roundIcon="@mipmap/custom_round" /></manifest>')
            ApkBuilder(Path(__file__).resolve().parents[1])._sync_app_icon(src)
            self.assertIn('@mipmap/custom"', manifest.read_text())
            self.assertIn('@mipmap/custom_round', manifest.read_text())
