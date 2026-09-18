"""Exercise the unchanged C renderer when make has produced its executable."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_tdfuture import makeClassic, ROOT


@unittest.skipUnless((ROOT / 'tdfiglet').is_file(), 'build the optional C executable with make first')
class ClassicBinaryTests(unittest.TestCase):
    def testAllClassicTypesAndIceBackground(self):
        with tempfile.TemporaryDirectory() as directory:
            for fontType in (0, 1, 2):
                path = Path(directory) / 'generated.tdf'
                path.write_bytes(makeClassic(fontType))
                listing = subprocess.run([str(ROOT / 'tdfiglet'), '-L', str(path)],
                                         capture_output=True, timeout=10)
                self.assertEqual(listing.returncode, 0, listing.stderr)
                self.assertIn(b'sub-fonts: 1', listing.stdout)
                output = subprocess.run([str(ROOT / 'tdfiglet'), '-f', str(path), 'AB'],
                                        capture_output=True, timeout=10)
                self.assertEqual(output.returncode, 0, output.stderr)
                self.assertIn('═'.encode() if fontType == 0 else b'A', output.stdout)
                if fontType == 2:
                    self.assertIn(b'\x1b[34;107m', output.stdout)


if __name__ == '__main__':
    unittest.main()
