"""Generated fixtures only: no third-party font assets are redistributed."""
import dataclasses
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tdfuture.model import Cell, Chunk, Font, Glyph, FontError, Provenance
from tdfuture.codec import SIGNATURE, decodeFont, encodeFont, packChunk
from tdfuture.classic import CLASSIC_MAGIC, importClassic, exportClassic
from tdfuture.color import toOklab, fromOklab, rotateHue, composite
from tdfuture.render import layoutText, renderText, renderHtml

ROOT = Path(__file__).resolve().parents[1]


def makeClassic(fontType=2, spacing=1, trailer=b'', extraRows=False):
    """Construct one classic subfont with two glyphs and nonzero reserved bytes."""
    header = bytearray(213)
    header[:4] = b'\x55\xaa\x00\xff'
    header[4] = 7
    header[5:17] = b'TestArt     '
    header[17:21] = b'\x19\x94\x42\x17'
    header[21] = fontType
    header[22] = spacing
    header[25:213] = b'\xff' * 188
    body = bytearray()
    for cp in (65, 66):
        struct.pack_into('<H', header, 25 + (cp - 33) * 2, len(body))
        body.extend(b'\x02\x01')
        if fontType == 2:
            body.extend(bytes([cp, 0xf1, 32, 0, 38, 13 if extraRows else 0]))
            if extraRows:
                body.extend(b'X\x0f\x00')
        elif fontType == 0:
            body.extend(b'AO&\x00')
        else:
            body.extend(bytes([cp, 32, 38, 0]))
    struct.pack_into('<H', header, 23, len(body))
    return CLASSIC_MAGIC + bytes(header) + bytes(body) + trailer


def makeFont(model=0, flags=0):
    color = None if model == 0 else 1 if model < 3 else (65535, 0, 0, 65535)
    bg = (0 if model < 3 else (0, 0, 0, 65535)) if flags & 1 else None
    glyphs = {
        0: Glyph(0, 2, 1, (Cell(65, color, bg), Cell(0, color, bg))),
        1: Glyph(1, 2, 1, (Cell(66, color, bg), Cell(32, color, bg))),
    }
    return Font(colorModel=model, cellFlags=flags, height=1, glyphs=glyphs,
                cmap={65: 0, 66: 1}, meta={'title': 'Fixture'},
                provenance={0: Provenance(4, 'test generator', 2026),
                            1: Provenance(4, 'test generator', 2026)})


class CodecTests(unittest.TestCase):
    def testEveryColorModelAndRle(self):
        for model in range(7):
            for bg in (0, 1):
                if model == 0 and bg:
                    continue
                for rle in (0, 8):
                    with self.subTest(model=model, bg=bg, rle=rle):
                        font = makeFont(model, bg | rle)
                        encoded = encodeFont(font)
                        self.assertEqual(encoded[:16], SIGNATURE)
                        restored = decodeFont(encoded)
                        self.assertEqual(restored.glyphs, font.glyphs)
                        self.assertEqual(encodeFont(restored), encoded)

    def testCrcAndTruncation(self):
        data = encodeFont(makeFont())
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(FontError):
                decodeFont(data[:length])
        broken = bytearray(data)
        broken[30] ^= 1
        with self.assertRaisesRegex(FontError, 'CRC'):
            decodeFont(bytes(broken))
        with self.assertRaises(FontError):
            decodeFont(data + b'junk')

    def testUnknownChunksAndSafeCopy(self):
        font = makeFont()
        font.extras = [Chunk(b'aaBc', b'safe'), Chunk(b'aaBC', b'unsafe')]
        data = encodeFont(font)
        loaded = decodeFont(data)
        self.assertEqual(len(loaded.extras), 2)
        self.assertEqual(encodeFont(loaded), data)
        loaded.meta['title'] = 'Edited'
        self.assertEqual(decodeFont(encodeFont(loaded)).extras,
                         [Chunk(b'aaBc', b'safe')])
        critical = data[:-12] + packChunk(b'ZZZZ', b'') + data[-12:]
        with self.assertRaisesRegex(FontError, 'critical'):
            decodeFont(critical)
        reserved = data[:-12] + packChunk(b'abcd', b'') + data[-12:]
        with self.assertRaises(FontError):
            decodeFont(reserved)

    def testDuplicateHeaderAndInvalidCmap(self):
        data = encodeFont(makeFont())
        with self.assertRaises(FontError):
            decodeFont(data[:40] + data[16:40] + data[40:])
        for cp in (-1, 0xD800, 0x110000):
            font = makeFont()
            font.cmap = {cp: 0}
            with self.assertRaises(FontError):
                encodeFont(font)
        font = makeFont()
        font.cmap = {65: 999}
        with self.assertRaises(FontError):
            encodeFont(font)

    def testUndefinedFeaturesRefuse(self):
        for flags in (2, 4, 16):
            font = makeFont()
            font.cellFlags = flags
            with self.assertRaises(FontError):
                encodeFont(font)
        for capabilities in (4, 32, 128, 256):
            font = makeFont()
            font.capabilities = capabilities
            with self.assertRaises(FontError):
                encodeFont(font)

    def testOversizedAndMalformedRle(self):
        with self.assertRaises(FontError):
            decodeFont(SIGNATURE + b'\x7f\xff\xff\xffFHDR')
        font = makeFont(0, 8)
        data = encodeFont(font)
        pos = data.index(b'GLPH')
        size = struct.unpack_from('>I', data, pos - 4)[0]
        payload = bytearray(data[pos + 4:pos + 4 + size])
        payload[8] = 0
        damaged = data[:pos - 4] + packChunk(b'GLPH', bytes(payload)) + data[pos + 8 + size:]
        with self.assertRaises(FontError):
            decodeFont(damaged)


class ClassicTests(unittest.TestCase):
    def testAllTypesRoundTrip(self):
        for fontType in (0, 1, 2):
            with self.subTest(fontType=fontType):
                data = makeClassic(fontType)
                fonts, trailer = importClassic(data)
                self.assertFalse(trailer)
                self.assertEqual(len(fonts), 1)
                font = decodeFont(encodeFont(fonts[0]))
                output, losses = exportClassic(font)
                self.assertEqual(output, data)
                self.assertFalse(losses)
                self.assertEqual(font.provenance[font.cmap[32]].origin, 4)

    def testChainAndTrailer(self):
        first, second = makeClassic(0), makeClassic(2, extraRows=True)
        fonts, tail = importClassic(first + second[20:] + b'historical trailer')
        self.assertEqual(len(fonts), 2)
        self.assertEqual(tail, b'historical trailer')
        output = CLASSIC_MAGIC + b''.join(exportClassic(f)[0][20:] for f in fonts) + tail
        self.assertEqual(output, first + second[20:] + tail)
        self.assertEqual(fonts[1].height, 2)

    def testIceBackgroundAndDescender(self):
        font = importClassic(makeClassic())[0][0]
        glyph = font.glyphs[font.cmap[65]]
        self.assertEqual(glyph.width, 2)
        self.assertEqual(glyph.cells[0].bg, 15)
        self.assertEqual(glyph.cells[1].fg, 0)

    def testChangedGlyphDoesNotReturnStaleArchive(self):
        font = decodeFont(encodeFont(importClassic(makeClassic())[0][0]))
        glyph = font.glyphs[font.cmap[65]]
        font.glyphs[glyph.index] = dataclasses.replace(glyph, cells=(Cell(90, 1, 15), glyph.cells[1]))
        with self.assertRaises(FontError):
            exportClassic(font)
        output, losses = exportClassic(font, allowLossy=True)
        self.assertNotEqual(output, makeClassic())
        self.assertTrue(losses)
        restored = importClassic(output)[0][0]
        self.assertEqual(restored.glyphs[restored.cmap[65]].cells[0].codepoint, 90)

    def testTruncatedAndBadOffset(self):
        data = makeClassic()
        for length in (0, 19, 20, 100, len(data) - 1):
            with self.assertRaises(FontError):
                importClassic(data[:length])
        bad = bytearray(data)
        struct.pack_into('<H', bad, 20 + 25 + (65 - 33) * 2, 65534)
        with self.assertRaises(FontError):
            importClassic(bytes(bad))


class ColorTests(unittest.TestCase):
    def testOklabRoundTrip(self):
        for rgb in ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.), (.25, .5, .75)):
            out = fromOklab(toOklab(rgb))
            for actual, expected in zip(out, rgb):
                self.assertAlmostEqual(actual, expected, places=5)

    def testHueAndAlpha(self):
        color = (20000, 40000, 50000, 32768)
        self.assertEqual(rotateHue(color, 0), color)
        self.assertEqual(rotateHue(color, 90)[3], 32768)
        self.assertEqual(composite((65535, 0, 0, 0), color), color)
        self.assertEqual(composite((65535, 0, 0, 65535), color), (65535, 0, 0, 65535))


class RenderTests(unittest.TestCase):
    def testBasicAndNegativeKerning(self):
        font = makeFont()
        self.assertEqual(renderText(layoutText(font, 'AB'), 'plain').strip('\n'), 'A B ')
        font.kerning[(65, 66)] = -1
        self.assertEqual(renderText(layoutText(font, 'AB'), 'plain').strip('\n'), 'AB ')

    def testZOrderAndCycleDiagnostic(self):
        font = makeFont()
        font.kerning = {(65, 66): -2, (66, 65): -2}
        font.zOrder = 1
        self.assertTrue(renderText(layoutText(font, 'AB'), 'plain').startswith('A'))
        font.zPairs = [(65, 66, 1), (66, 65, 1)]
        canvas = layoutText(font, 'ABA')
        self.assertTrue(any('cycle' in warning.lower() for warning in canvas.warnings))

    def testSafeProfilesAndHtml(self):
        font = makeFont()
        font.glyphs[0] = Glyph(0, 2, 1, (Cell(27), Cell(0x202e)))
        canvas = layoutText(font, 'A')
        output = renderText(canvas, 'ssh-banner')
        self.assertTrue(all(ch == '\n' or 32 <= ord(ch) <= 126 for ch in output))
        font.glyphs[0] = Glyph(0, 2, 1, (Cell(60), Cell(38)))
        html = renderHtml(layoutText(font, 'A'), '<unsafe>')
        self.assertIn('&lt;', html)
        self.assertIn('&amp;', html)
        self.assertNotIn('<unsafe>', html)
        with self.assertRaises(FontError):
            renderText(layoutText(font, 'A' * 41), 'ssh-banner')

    def testMissingAndResourceBounds(self):
        with self.assertRaises(FontError):
            layoutText(makeFont(), 'X')
        with self.assertRaises(FontError):
            layoutText(makeFont(), 'A' * 100000)


class CliTests(unittest.TestCase):
    def runCli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'tdfx'), *map(str, args)],
                              cwd=ROOT, capture_output=True, timeout=15)

    def testConversionRestoreDryRunAndNoClobber(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.tdf'
            source.write_bytes(makeClassic(trailer=b'archival tail'))
            target = root / 'collection'
            self.assertEqual(self.runCli('convert', source, '--all', '-o', target, '--dry-run').returncode, 0)
            self.assertFalse(target.exists())
            proc = self.runCli('convert', source, '--all', '-o', target)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            restored = root / 'roundtrip.tdf'
            proc = self.runCli('restore', target / 'manifest.json', '-o', restored)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(restored.read_bytes(), source.read_bytes())
            self.assertNotEqual(self.runCli('restore', target / 'manifest.json', '-o', restored).returncode, 0)
            fontPath = next(target.glob('*.tdfx'))
            proc = self.runCli('render', fontPath, 'AB', '--profile', 'ssh-banner')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(b'\x1b', proc.stdout)

    def testHelpAndInvalidInput(self):
        self.assertEqual(self.runCli('--help').returncode, 0)
        self.assertNotEqual(self.runCli('inspect', '/nonexistent-font').returncode, 0)

class ImporterTests(unittest.TestCase):
    def testFigletHardblankAndSmushingGate(self):
        from tdfuture.importers import importFiglet
        data = ('flf2a$ 1 1 4 -1 0 0 0\n' + ''.join(chr(cp) + '$@@\n' for cp in range(32, 127))).encode()
        font, losses = importFiglet(data)
        self.assertFalse(losses)
        self.assertEqual(font.glyphs[font.cmap[65]].cells, (Cell(65), Cell(32)))
        self.assertEqual(font.glyphs[font.cmap[32]].cells[0].codepoint, 0)
        encodeFont(font)
        smushed = data.replace(b'4 -1 0 0 0', b'4 0 0 0 64')
        with self.assertRaises(FontError):
            importFiglet(smushed)
        font, losses = importFiglet(smushed, allowLossy=True)
        self.assertTrue(losses)

    def testPngAndClut(self):
        from tdfuture.render import renderPng
        from tdfuture.model import Clut
        from tdfuture.color import applyClut
        try:
            import PIL
        except ImportError:
            self.skipTest('optional Pillow is not installed')
        self.assertTrue(renderPng(layoutText(makeFont(), 'AB')).startswith(b'\x89PNG\r\n\x1a\n'))
        entries = tuple((r*65535,g*65535,b*65535) for b in range(2) for g in range(2) for r in range(2))
        for method in (1, 2):
            color = (10000,20000,30000,40000)
            self.assertEqual(applyClut(color, Clut(2,method,0,entries)), color)


if __name__ == '__main__':
    unittest.main()
