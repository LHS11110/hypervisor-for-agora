#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_image', ROOT / 'scripts/verify-image.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
CONFIG = json.loads((ROOT / 'config/ubuntu-image.json').read_text())
FIXTURE = ROOT / 'tests/fixtures/ubuntu'


class ImageVerification(unittest.TestCase):
    def test_official_signature_and_pin(self):
        verify.verify_manifest(CONFIG, FIXTURE)

    def test_modified_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            shutil.copytree(FIXTURE, path, dirs_exist_ok=True)
            with (path / 'SHA256SUMS').open('a') as f:
                f.write('tampered\n')
            with self.assertRaises(ValueError):
                verify.verify_manifest(CONFIG, path)

    def test_unexpected_signer_and_digest_rejected(self):
        for key, value in [('signing_fingerprint', '0' * 40), ('sha256', '0' * 64)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify.verify_manifest({**CONFIG, key: value}, FIXTURE)

    def test_modified_image_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            shutil.copytree(FIXTURE, path, dirs_exist_ok=True)
            (path / CONFIG['filename']).write_bytes(b'untrusted image')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                verify.verify_image(CONFIG, path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
