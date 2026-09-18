"""Adversarial and regression tests using generated data, never redistributed fonts."""
import dataclasses
import json
import os
from pathlib import Path
import random
import struct
import tempfile
import unittest
from tdfuture.cli import restoreBundle, writeAtomic
from tdfuture.codec import decodeFont, encodeFont, packChunk, SIGNATURE
from tdfuture.model import Cell, FontError, Glyph, Clut
from tdfuture.color import BLACK, composite
from tdfuture.classic import importClassic, exportClassic
from tdfuture.render import layoutText, renderText
from test_tdfuture import makeFont, makeClassic
import test_tdfuture


class HardeningTests(unittest.TestCase):
    def testAlphaOverUntouchedCanvasUsesBackground(self):
        font = makeFont(6, 1)
        fg = (65535, 0, 0, 32768)
        font.glyphs[0] = Glyph(0, 1, 1, (Cell(65, fg, (0, 0, 0, 0)),))
        canvas = layoutText(font, 'A')
        self.assertEqual(canvas.cells[0].fg, composite(fg, BLACK))

    def testUnicodeSeparatorsCannotAlterLines(self):
        font = makeFont()
        font.glyphs[0] = Glyph(0, 2, 1, (Cell(0x2028), Cell(0x2029)))
        self.assertEqual(renderText(layoutText(font, 'A'), 'plain'), '??\n')

    def testMetadataKeepsArchiveButGlyphEditsDropIt(self):
        font = decodeFont(encodeFont(importClassic(makeClassic())[0][0]))
        font.meta['description'] = 'A catalog note, not an artwork change'
        self.assertEqual(exportClassic(decodeFont(encodeFont(font)))[0], makeClassic())
        glyph = font.glyphs[font.cmap[65]]
        font.glyphs[glyph.index] = dataclasses.replace(glyph, cells=(Cell(90, 1, 15), glyph.cells[1]))
        self.assertIsNone(decodeFont(encodeFont(font)).classic)

    def testClutCodecAndExplicitRgbPrecision(self):
        font = makeFont(6, 1)
        entries = tuple((r * 65535, g * 65535, b * 65535)
                        for b in range(2) for g in range(2) for r in range(2))
        font.clut = Clut(2, 2, 1, entries)
        self.assertEqual(decodeFont(encodeFont(font)).clut, font.clut)
        font.colorModel = 4
        font.glyphs[0] = Glyph(0, 1, 1, (Cell(65, (1,2,3,4), (0,0,0,0)),))
        with self.assertRaises(FontError):
            encodeFont(font)

    def testSeededCorruptionsProduceStructuredErrors(self):
        rng = random.Random(1729)
        original = encodeFont(makeFont(6, 9))
        chunks, offset = [], 16
        while offset < len(original):
            size = struct.unpack_from('>I', original, offset)[0]
            chunks.append((original[offset+4:offset+8], original[offset+8:offset+8+size]))
            offset += size + 12
        for _ in range(150):
            modified = chunks.copy()
            which = rng.randrange(len(chunks)-1)
            kind, payload = modified[which]
            payload = bytearray(payload)
            payload[rng.randrange(len(payload))] ^= rng.randrange(1,256)
            modified[which] = kind, bytes(payload)
            data = SIGNATURE + b''.join(packChunk(kind, payload) for kind, payload in modified)
            try:
                font = decodeFont(data)
                self.assertEqual(encodeFont(font), data)
            except FontError:
                pass

    def testRestoreRejectsTraversalSymlinksAndTampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.tdf'
            source.write_bytes(makeClassic())
            target = root / 'bundle'
            result = test_tdfuture.CliTests().runCli('convert', source, '--all', '-o', target)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifestPath = target / 'manifest.json'
            manifest = json.loads(manifestPath.read_text())
            for name in ('../input.tdf', '/etc/passwd', r'..\input.tdf'):
                changed = json.loads(json.dumps(manifest))
                changed['subfonts'][0]['file'] = name
                manifestPath.write_text(json.dumps(changed))
                with self.assertRaises(FontError):
                    restoreBundle(manifestPath)
            manifestPath.write_text(json.dumps(manifest))
            member = target / manifest['subfonts'][0]['file']
            contents = member.read_bytes()
            member.write_bytes(contents + b'x')
            with self.assertRaises(FontError):
                restoreBundle(manifestPath)
            member.write_bytes(contents)
            if hasattr(os, 'symlink'):
                backup = root / 'member'
                member.replace(backup)
                try:
                    member.symlink_to(backup)
                except OSError:
                    return
                with self.assertRaises((FontError, OSError)):
                    restoreBundle(manifestPath)

    def testAtomicFailureNeverChangesExistingFile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result'
            path.write_bytes(b'original')
            with self.assertRaises(FontError):
                writeAtomic(path, b'replacement')
            writeAtomic(path, b'replacement', force=True, dryRun=True)
            self.assertEqual(path.read_bytes(), b'original')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def testPngHalfBlocksPreserveSubcellPositions(self):
        import io
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('optional Pillow is not installed')
        from tdfuture.render import Canvas, renderPng
        from tdfuture.color import WHITE
        canvas = Canvas(2, 1, (Cell(0x2580, WHITE, BLACK), Cell(0x2584, WHITE, BLACK)), [])
        image = Image.open(io.BytesIO(renderPng(canvas, cellWidth=8, cellHeight=8)))
        self.assertEqual(image.getpixel((3, 1)), (255,255,255))
        self.assertEqual(image.getpixel((3, 6)), (0,0,0))
        self.assertEqual(image.getpixel((11, 1)), (0,0,0))
        self.assertEqual(image.getpixel((11, 6)), (255,255,255))

    def testOutlineOptionalLocalSmoke(self):
        from tdfuture.importers import importOutline
        fontPath = Path(os.environ.get('TDFX_TEST_OUTLINE', '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'))
        if not fontPath.is_file():
            self.skipTest('optional local outline font not configured')
        try:
            import PIL
            import fontTools
        except ImportError:
            self.skipTest('optional outline dependencies not installed')
        data = fontPath.read_bytes()
        for repertoire in ('half-block', 'braille'):
            font, notices = importOutline(data, ' ABg', repertoire=repertoire)
            decoded = decodeFont(encodeFont(font))
            self.assertEqual(set(decoded.cmap), {32,65,66,103})
            self.assertTrue(all(p.origin == 5 for p in decoded.provenance.values()))
            self.assertIsNone(decoded.source)
            self.assertTrue(layoutText(decoded, 'ABg').width)
        with self.assertRaises(FontError):
            importOutline(data, 'A', embedSource=True)
        with self.assertRaises(FontError):
            importOutline(data, '\U0010ffff')


if __name__ == '__main__':
    unittest.main()
