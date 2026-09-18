"""Classic TheDraw import, verified archival export, and explicitly lossy down-conversion."""
from __future__ import annotations
import struct
from .model import Cell, Font, FontError, Glyph, Provenance, MAX_FILE_BYTES, MAX_CELLS, artDigest
from .color import resolveColor, nearestColor, composite, applyClut, BLACK

CLASSIC_MAGIC = b'\x13TheDraw FONTS file\x1a'
MARKER = b'\x55\xaa\x00\xff'
OUTLINE_MAP = (32,205,196,179,186,213,187,214,191,200,190,192,189,181,199,32)


def importClassic(data: bytes) -> tuple[list[Font], bytes]:
    """Import all subfonts and return (fonts, unconsumed historical trailer).

    Every imported font retains its exact 213-byte header plus glyph block in
    TDFC. Malformed/truncated glyphs and unsupported geometry raise FontError.
    Synthesized space is marked origin=4; original glyph artists stay unknown.
    """
    if len(data) > MAX_FILE_BYTES or not data.startswith(CLASSIC_MAGIC):
        raise FontError('bad classic TDF signature or file size limit exceeded')
    offset, fonts, cellBudget = len(CLASSIC_MAGIC), [], 0
    while data[offset:offset + 4] == MARKER:
        if len(fonts) >= 4096:
            raise FontError('classic subfont budget exceeded')
        if len(data) - offset < 213:
            raise FontError('truncated classic subfont header')
        header = data[offset:offset + 213]
        size, = struct.unpack_from('<H', header, 23)
        end = offset + 213 + size
        if end > len(data):
            raise FontError('truncated classic glyph data block')
        fontType, spacing = header[21], header[22]
        if fontType not in (0, 1, 2):
            raise FontError(f'unknown classic font type {fontType}')
        if spacing > 127:
            raise FontError('classic spacing exceeds Draft 1 signed tracking range; source left unchanged')
        font = Font(colorModel=1 if fontType == 2 else 0,
                    cellFlags=1 if fontType == 2 else 0, tracking=spacing, repertoire=1)
        title = header[5:5 + min(12, header[4])].decode('cp437').rstrip(' \x00')
        font.meta = {'title': title, 'tool': 'tdfx 0.1.0',
                     'description': 'Imported classic TheDraw subfont. Artist unknown unless supplied separately.',
                     'classic-type': str(fontType)}
        block = data[offset + 213:end]
        for cp in range(33, 127):
            glyphOffset, = struct.unpack_from('<H', header, 25 + (cp - 33) * 2)
            if glyphOffset == 0xffff:
                continue
            if glyphOffset + 2 > len(block):
                raise FontError(f'classic glyph U+{cp:04X}: offset outside data block')
            width, height = block[glyphOffset:glyphOffset + 2]
            position, rows = glyphOffset + 2, [[]]
            terminated = False
            while position < len(block):
                ch = block[position]
                position += 1
                if ch == 0:
                    terminated = True
                    break
                if ch == 13:
                    rows.append([])
                    if len(rows) > 256:
                        raise FontError('classic glyph exceeds 255 rows')
                    continue
                # A descender has NO attribute, even in a color stream.
                if ch == 38 and position < len(block) and block[position] in (0, 13):
                    continue
                attribute = 15
                if fontType == 2:
                    if position == len(block):
                        raise FontError('truncated classic color attribute')
                    attribute = block[position]
                    position += 1
                if fontType == 0 and 0x40 <= ch <= 0x4f:
                    ch = OUTLINE_MAP[ch - 0x40]
                if ch < 32:
                    ch = 32
                scalar = ord(bytes([ch]).decode('cp437'))
                rows[-1].append(Cell(scalar, attribute & 15, attribute >> 4)
                                if fontType == 2 else Cell(scalar))
                if len(rows[-1]) > 255:
                    raise FontError('classic glyph exceeds 255 columns')
            if not terminated:
                raise FontError(f'classic glyph U+{cp:04X}: missing terminator')
            while len(rows) > 1 and not rows[-1]:
                rows.pop()
            width = max(1, width, max(map(len, rows)))
            height = max(1, height, len(rows))
            if width > 255 or height > 255:
                raise FontError('classic glyph exceeds TDFuture geometry limits')
            pad = Cell(32, 0, 0) if fontType == 2 else Cell(32)
            cells = []
            for row in range(height):
                line = rows[row] if row < len(rows) else []
                cells.extend(line + [pad] * (width - len(line)))
            index = len(font.glyphs)
            font.glyphs[index] = Glyph(index, width, height, tuple(cells))
            font.cmap[cp] = index
            font.provenance[index] = Provenance(0)
            cellBudget += len(cells)
            if cellBudget > MAX_CELLS:
                raise FontError('classic collection decoded cell budget exceeded; convert smaller source files')
        font.height = max((g.height for g in font.glyphs.values()), default=1)
        mean = sum(g.width for g in font.glyphs.values()) // max(1, len(font.glyphs))
        width = max(1, (mean + 1) // 2)
        index = len(font.glyphs)
        pad = Cell(32, 0, 0) if fontType == 2 else Cell(32)
        font.glyphs[index] = Glyph(index, width, font.height, (pad,) * (width * font.height))
        font.cmap[32] = index
        font.provenance[index] = Provenance(4, 'tdfx synthesized word space', 0)
        cellBudget += width * font.height
        if cellBudget > MAX_CELLS:
            raise FontError('classic collection decoded cell budget exceeded')
        font.classic = data[offset:end]
        font._originalArt = artDigest(font)
        fonts.append(font)
        offset = end
    if not fonts:
        raise FontError('classic TDF contains no complete subfont')
    return fonts, data[offset:]


def exportClassic(font: Font, allowLossy: bool = False) -> tuple[bytes, list[str]]:
    """Export a classic .tdf, byte-identically when its archived glyph state still matches.

    Never trusts a stored digest. TDFC is decoded independently and compared to
    the current font. Edited/native fonts require allowLossy=True; all classes
    of discarded/quantized information are returned in the loss report.
    """
    if font.classic is not None:
        archived, trailer = importClassic(CLASSIC_MAGIC + font.classic)
        if not trailer and len(archived) == 1 and artDigest(archived[0]) == artDigest(font):
            return CLASSIC_MAGIC + font.classic, []
    if not allowLossy:
        raise FontError('lossless classic export requires unmodified TDFC; use --allow-lossy to acknowledge down-conversion')
    losses = {'archival byte identity is unavailable; a new color subfont is generated',
              'metadata and per-glyph provenance are not representable in classic TDF'}
    if font.source:
        losses.add('embedded source is omitted')
    if font.extras:
        losses.add('ancillary extension chunks are omitted')
    if font.kerning or font.zPairs or font.zOrder or font.kernTracking is not None:
        losses.add('pair kerning and z-order are omitted; only nonnegative global tracking survives')
    if font.clut:
        losses.add('CLUT is baked into colors and quantized to the DOS palette')
    if font.colorModel != 1 or font.palette:
        losses.add('colors are flattened onto black and quantized to the default DOS 16-color palette')
    title = font.meta.get('title', 'Converted').encode('cp437', 'replace')
    if len(title) > 12:
        losses.add('font title is truncated to 12 CP437 bytes')
    title = title[:12]
    header = bytearray(213)
    header[:4] = MARKER
    header[4] = len(title)
    header[5:17] = title.ljust(12, b' ')
    header[21] = 2
    tracking = font.tracking if font.kernTracking is None else font.kernTracking
    if tracking < 0:
        losses.add('negative tracking becomes zero')
    header[22] = max(0, min(255, tracking))
    header[25:213] = b'\xff' * 188
    block = bytearray()
    for cp, index in sorted(font.cmap.items()):
        if not 33 <= cp <= 126:
            losses.add(f'U+{cp:04X} has no classic glyph slot and is omitted (space is synthesized by classic renderers)')
            continue
        glyph = font.glyphs[index]
        if glyph.leftBearing or glyph.rightBearing or glyph.baselineOffset:
            losses.add('glyph bearings and baseline offsets are omitted')
        if not 1 <= glyph.width <= 255 or not 1 <= glyph.height <= 255:
            raise FontError('glyph geometry cannot fit classic TDF')
        if len(block) >= 65535:
            raise FontError('classic glyph data block exceeds 65535 bytes')
        struct.pack_into('<H', header, 25 + (cp - 33) * 2, len(block))
        block.extend(bytes([glyph.width, glyph.height]))
        for row in range(glyph.height):
            if row:
                block.append(13)
            for cell in glyph.cells[row * glyph.width:(row + 1) * glyph.width]:
                scalar = cell.codepoint
                if scalar == 0:
                    losses.add('transparent cells become opaque spaces')
                    scalar = 32
                try:
                    ch = chr(scalar).encode('cp437')[0]
                except (UnicodeError, ValueError):
                    losses.add(f'cell U+{scalar:04X} is not CP437 and becomes ?')
                    ch = 63
                if ch < 32 or ch == 127:
                    losses.add('control-code cells become spaces')
                    ch = 32
                fg = resolveColor(cell.fg, font.colorModel, font.palette)
                bg = resolveColor(cell.bg, font.colorModel, font.palette, True)
                if font.clut:
                    fg, bg = applyClut(fg, font.clut), applyClut(bg, font.clut)
                if fg[3] != 65535 or bg[3] not in (0, 65535):
                    losses.add('alpha is flattened onto the cell background and black canvas')
                bg = composite(bg, BLACK)
                fg = composite(fg, bg)
                attribute = nearestColor(fg) | nearestColor(bg) << 4
                if ch == 38 and attribute in (0, 13):
                    losses.add('ambiguous ampersand/descender encoding becomes ?')
                    ch = 63
                block.extend(bytes([ch, attribute]))
        block.append(0)
    if len(block) > 65535:
        raise FontError('classic glyph data block exceeds 65535 bytes; split/reduce font')
    struct.pack_into('<H', header, 23, len(block))
    return CLASSIC_MAGIC + bytes(header) + bytes(block), sorted(losses)
