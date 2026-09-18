"""Strict, bounded TDFuture Draft 1 static codec; all integers are decoded explicitly."""
from __future__ import annotations
import struct
import zlib
from .model import (Cell, Chunk, Clut, Font, FontError, Glyph, Provenance, Source,
                    MAX_FILE_BYTES, MAX_CELLS, MAX_CHUNKS, isScalar, stateDigest, artDigest)

SIGNATURE = b'\x13TDFuture\x1a\r\n\x1a\n\x00\x01'
SUPPORTED = {b'FHDR', b'CMAP', b'GLPH', b'META', b'PROV', b'PLTE', b'CLUT',
             b'KERN', b'ZORD', b'SRC ', b'TDFC', b'FEND'}
UNDEFINED = {b'ANIM', b'PROF', b'FX  ', b'SHDR'}


class Reader:
    """Bounds-checked payload cursor. Truncation raises FontError, never struct.error."""
    def __init__(self, data: bytes, label='payload'):
        self.data, self.pos, self.label = data, 0, label

    def take(self, size: int) -> bytes:
        """Consume exactly size bytes or fail without advancing past the buffer."""
        if size < 0 or size > len(self.data) - self.pos:
            raise FontError(f'{self.label}: truncated at byte {self.pos}')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def unpack(self, form: str):
        """Decode a struct whose byte order must be explicit."""
        return struct.unpack(form, self.take(struct.calcsize(form)))

    def end(self):
        """Require payload exhaustion so unknown trailing fields cannot be misinterpreted."""
        if self.pos != len(self.data):
            raise FontError(f'{self.label}: unexpected trailing payload bytes')

    def text(self, size: int) -> str:
        """Decode strict UTF-8 metadata, not replacement characters."""
        try:
            return self.take(size).decode('utf-8')
        except UnicodeError as error:
            raise FontError(f'{self.label}: invalid UTF-8') from error


def packChunk(kind: bytes, payload: bytes) -> bytes:
    """Encode one chunk with CRC-32/ISO-HDLC; useful for independent fixture generation."""
    if len(kind) != 4 or len(payload) > MAX_FILE_BYTES:
        raise FontError('invalid chunk type length or payload size')
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))


def checkKind(kind: bytes):
    if kind in (b'SRC ', b'FX  '):
        return
    if len(kind) != 4 or any(not (65 <= c <= 90 or 97 <= c <= 122) for c in kind):
        raise FontError('chunk type must be four ASCII letters')
    if not 65 <= kind[2] <= 90:
        raise FontError('chunk type reserved third letter must be uppercase')


def checkHeader(font: Font):
    if font.colorModel not in range(7):
        raise FontError('unknown color model')
    if font.cellFlags & ~9:
        raise FontError('depth/layer field widths and reserved cell flags are unsupported in Draft 1')
    if font.colorModel == 0 and font.cellFlags & 1:
        raise FontError('monochrome cells cannot carry background color fields')
    if not 1 <= font.height <= 255 or not 0 <= font.baseline < font.height:
        raise FontError('invalid font height/baseline')
    if not -128 <= font.tracking <= 127 or font.repertoire not in range(9):
        raise FontError('invalid tracking or repertoire')
    if font.capabilities < 0 or font.capabilities & ~0x5b:
        raise FontError('unsupported critical capability: animation, shader, clusters or reserved bits')


def readCell(reader: Reader, font: Font) -> Cell:
    cp, = reader.unpack('>I')
    if not isScalar(cp):
        raise FontError('cell codepoint is not a Unicode scalar')
    model, hasBg = font.colorModel, bool(font.cellFlags & 1)
    if model == 0:
        return Cell(cp)
    if model == 1:
        value, = reader.unpack('>B')
        if not hasBg and value & 0xf0:
            raise FontError('nonzero unused background nibble')
        return Cell(cp, value & 15, value >> 4 if hasBg else None)
    def color():
        if model == 2:
            return reader.unpack('>B')[0]
        count = 4 if model in (4, 6) else 3
        values = reader.unpack('>' + ('B' if model < 5 else 'H') * count)
        if model < 5:
            values = tuple(value * 257 for value in values)
        return values if count == 4 else values + (65535,)
    fg = color()
    return Cell(cp, fg, color() if hasBg else None)


def writeCell(cell: Cell, font: Font) -> bytes:
    if not isScalar(cell.codepoint):
        raise FontError('cell codepoint is not a Unicode scalar')
    output = struct.pack('>I', cell.codepoint)
    model, hasBg = font.colorModel, bool(font.cellFlags & 1)
    if not hasBg and cell.bg is not None:
        raise FontError('background color without FHDR background flag')
    if model == 0:
        if cell.fg is not None or cell.bg is not None:
            raise FontError('monochrome cells must not contain colors')
        return output
    def index(value, maximum):
        if not isinstance(value, int) or not 0 <= value < maximum:
            raise FontError('palette index out of range')
        if font.palette and value >= len(font.palette):
            raise FontError('palette index exceeds PLTE')
        return value
    if model == 1:
        return output + bytes([index(cell.fg, 16) | ((index(cell.bg, 16) << 4) if hasBg else 0)])
    def color(value):
        if model == 2:
            return bytes([index(value, 256)])
        if not isinstance(value, tuple) or len(value) != 4 or any(
                not isinstance(v, int) or not 0 <= v <= 65535 for v in value):
            raise FontError('direct color must be an RGBA16 tuple')
        if model in (3, 5) and value[3] != 65535:
            raise FontError('RGB model cannot silently discard alpha')
        components = value if model in (4, 6) else value[:3]
        if model < 5:
            if any(v % 257 for v in components):
                raise FontError('RGB8/RGBA8 channels must be exact 8-bit expansions; use RGB16/RGBA16')
            return bytes(v // 257 for v in components)
        return struct.pack('>' + 'H' * len(components), *components)
    return output + color(cell.fg) + (color(cell.bg) if hasBg else b'')


def decodeFont(data: bytes) -> Font:
    """Decode an entire .tdfx file; reject corruption, unsupported critical features and resource abuse.

    Input: immutable bytes, at most 32 MiB. Output: editable Font. Raises FontError.
    Example: font = decodeFont(Path('font.tdfx').read_bytes()).
    """
    if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES or not data.startswith(SIGNATURE):
        raise FontError('bad TDFuture signature or file size limit exceeded')
    reader = Reader(data, 'container')
    reader.take(16)
    font = Font()
    seen = set()
    expected = 0
    cellTotal = 0
    ended = False
    for chunkIndex in range(MAX_CHUNKS):
        if reader.pos == len(data):
            break
        length, = reader.unpack('>I')
        if length > MAX_FILE_BYTES or length > 0x7fffffff:
            raise FontError('chunk length exceeds resource limit')
        kind = reader.take(4)
        payload = reader.take(length)
        crc, = reader.unpack('>I')
        if zlib.crc32(kind + payload) != crc:
            raise FontError(f'{kind!r}: CRC mismatch')
        checkKind(kind)
        if chunkIndex == 0 and kind != b'FHDR':
            raise FontError('FHDR must be first')
        if kind in UNDEFINED:
            raise FontError(f'unsupported critical chunk {kind!r}: Draft 1 payload is not fully specified')
        if kind not in SUPPORTED:
            if 65 <= kind[0] <= 90:
                raise FontError(f'unknown critical chunk {kind!r}')
            font.extras.append(Chunk(kind, payload))
            continue
        if kind != b'GLPH' and kind in seen:
            raise FontError(f'duplicate {kind!r} chunk')
        seen.add(kind)
        part = Reader(payload, kind.decode('ascii'))
        if kind == b'FHDR':
            (font.colorModel, font.cellFlags, font.height, font.baseline, expected,
             font.tracking, font.repertoire, font.capabilities) = part.unpack('>BBBBHbBI')
            checkHeader(font)
            if expected == 0:
                raise FontError('a font must contain at least one glyph')
        elif kind == b'CMAP':
            count, = part.unpack('>H')
            last = -1
            for _ in range(count):
                start, end, first = part.unpack('>IIH')
                if (not isScalar(start) or not isScalar(end) or end < start or start <= last
                        or start <= 0xdfff and end >= 0xd800
                        or first + end - start >= expected):
                    raise FontError('invalid, overlapping or out-of-bounds CMAP range')
                if len(font.cmap) + end - start + 1 > MAX_CELLS:
                    raise FontError('CMAP mapping budget exceeded')
                for cp in range(start, end + 1):
                    font.cmap[cp] = first + cp - start
                last = end
        elif kind == b'GLPH':
            index, width, height, left, right, baseline, flags = part.unpack('>HBBbbbB')
            if index >= expected or index in font.glyphs or not width or not height or flags:
                raise FontError('invalid/duplicate glyph index, geometry or reserved glyph flags')
            cellTotal += width * height
            if cellTotal > MAX_CELLS:
                raise FontError('decoded cell budget exceeded')
            cells = []
            while len(cells) < width * height:
                run = part.unpack('>B')[0] if font.cellFlags & 8 else 1
                if not run or run > width * height - len(cells):
                    raise FontError('invalid RLE run or glyph cell overrun')
                cells.extend([readCell(part, font)] * run)
            font.glyphs[index] = Glyph(index, width, height, tuple(cells), left, right, baseline, flags)
        elif kind == b'META':
            count, = part.unpack('>H')
            for _ in range(count):
                key = part.text(part.unpack('>B')[0])
                value = part.text(part.unpack('>H')[0])
                if not key or key in font.meta:
                    raise FontError('empty or duplicate META key')
                font.meta[key] = value
        elif kind == b'PROV':
            count, = part.unpack('>H')
            for _ in range(count):
                index, origin, size = part.unpack('>HBB')
                author = part.text(size)
                year, = part.unpack('>H')
                if index >= expected or index in font.provenance or origin > 5:
                    raise FontError('invalid/duplicate provenance entry')
                font.provenance[index] = Provenance(origin, author, year)
        elif kind == b'PLTE':
            if font.colorModel not in (1, 2) or len(payload) % 8:
                raise FontError('PLTE is only valid for indexed colors and RGBA16 entries')
            limit = 16 if font.colorModel == 1 else 256
            if not 1 <= len(payload) // 8 <= limit:
                raise FontError('invalid palette size')
            font.palette = tuple(part.unpack('>HHHH') for _ in range(len(payload) // 8))
        elif kind == b'KERN':
            font.kernTracking, count = part.unpack('>bH')
            for _ in range(count):
                left, right, adjustment = part.unpack('>IIb')
                if not isScalar(left) or not isScalar(right) or (left, right) in font.kerning:
                    raise FontError('invalid or duplicate KERN pair')
                font.kerning[left, right] = adjustment
        elif kind == b'ZORD':
            font.zOrder, count = part.unpack('>BH')
            if font.zOrder > 4:
                raise FontError('unknown z-order mode')
            for _ in range(count):
                a, b, relation = part.unpack('>IIB')
                if not isScalar(a) or not isScalar(b) or relation > 1:
                    raise FontError('invalid z-order constraint')
                font.zPairs.append((a, b, relation))
        elif kind == b'CLUT':
            size, interpolation, domain = part.unpack('>BBB')
            if not 2 <= size <= 64 or interpolation > 2 or domain > 1:
                raise FontError('invalid CLUT header')
            font.clut = Clut(size, interpolation, domain,
                             tuple(part.unpack('>HHH') for _ in range(size ** 3)))
        elif kind == b'SRC ':
            form, flags, size = part.unpack('>BBI')
            if form > 3 or flags != 1:
                raise FontError('only baked embedded sources are supported; live rasterization is not specified')
            font.source = Source(form, flags, part.take(size))
        elif kind == b'TDFC':
            if len(payload) < 213 or payload[:4] != b'\x55\xaa\x00\xff':
                raise FontError('invalid TDFC subfont header')
            if len(payload) != 213 + struct.unpack_from('<H', payload, 23)[0]:
                raise FontError('TDFC block size does not match payload')
            font.classic = part.take(len(payload))
        elif kind == b'FEND':
            part.end()
            reader.end()
            ended = True
            break
        part.end()
    if not ended or not {b'FHDR', b'CMAP', b'GLPH', b'FEND'} <= seen:
        raise FontError('missing required chunk, FEND, or chunk budget exceeded')
    if set(font.glyphs) != set(range(expected)):
        raise FontError('glyph count or index set does not match FHDR')
    # Palette chunks need not precede glyphs. Validate references after all chunks.
    for glyph in font.glyphs.values():
        for cell in glyph.cells:
            if font.palette:
                for color in (cell.fg, cell.bg):
                    if isinstance(color, int) and color >= len(font.palette):
                        raise FontError('cell index exceeds PLTE')
    font._originalBytes = data
    font._originalState = stateDigest(font)
    font._originalArt = artDigest(font)
    return font


def encodeFont(font: Font) -> bytes:
    """Serialize a Font, preserving byte-identical no-op saves and only safe ancillary data on edits.

    Raises FontError for values not representable without loss. No external
    resources are opened, embedded source programs are never executed.
    """
    try:
        return _encodeFont(font)
    except (struct.error, OverflowError, UnicodeError, TypeError) as error:
        raise FontError(f'invalid font field: {error}') from error


def _encodeFont(font: Font) -> bytes:
    checkHeader(font)
    count = len(font.glyphs)
    if not 1 <= count <= 65535 or set(font.glyphs) != set(range(count)):
        raise FontError('glyph indices must be contiguous from zero, with 1..65535 glyphs')
    if font._originalBytes is not None and font._originalState == stateDigest(font):
        return font._originalBytes
    chunks = [packChunk(b'FHDR', struct.pack('>BBBBHbBI', font.colorModel, font.cellFlags,
              font.height, font.baseline, count, font.tracking, font.repertoire, font.capabilities))]
    ranges = []
    for cp, index in sorted(font.cmap.items()):
        if not isScalar(cp) or not isinstance(index, int) or not 0 <= index < count:
            raise FontError('invalid CMAP scalar or glyph index')
        if ranges and cp == ranges[-1][1] + 1 and index == ranges[-1][2] + cp - ranges[-1][0]:
            ranges[-1] = (ranges[-1][0], cp, ranges[-1][2])
        else:
            ranges.append((cp, cp, index))
    chunks.append(packChunk(b'CMAP', struct.pack('>H', len(ranges)) + b''.join(
        struct.pack('>IIH', *item) for item in ranges)))
    if font.palette:
        if font.colorModel not in (1, 2) or not 1 <= len(font.palette) <= (16 if font.colorModel == 1 else 256):
            raise FontError('invalid PLTE color model or size')
        chunks.append(packChunk(b'PLTE', b''.join(struct.pack('>HHHH', *entry) for entry in font.palette)))
    if font.meta:
        pairs = []
        for key, value in sorted(font.meta.items()):
            keyBytes, valueBytes = key.encode('utf-8'), value.encode('utf-8')
            if not keyBytes:
                raise FontError('empty META key')
            pairs.append(struct.pack('>B', len(keyBytes)) + keyBytes + struct.pack('>H', len(valueBytes)) + valueBytes)
        chunks.append(packChunk(b'META', struct.pack('>H', len(pairs)) + b''.join(pairs)))
    if font.provenance:
        entries = []
        for index, prov in sorted(font.provenance.items()):
            if index not in font.glyphs or not 0 <= prov.origin <= 5:
                raise FontError('invalid provenance index or origin')
            author = prov.author.encode('utf-8')
            entries.append(struct.pack('>HBB', index, prov.origin, len(author)) + author + struct.pack('>H', prov.year))
        chunks.append(packChunk(b'PROV', struct.pack('>H', len(entries)) + b''.join(entries)))
    if font.kerning or font.kernTracking is not None:
        pairs = []
        for (left, right), adjustment in sorted(font.kerning.items()):
            if not isScalar(left) or not isScalar(right):
                raise FontError('KERN key is not a Unicode scalar')
            pairs.append(struct.pack('>IIb', left, right, adjustment))
        chunks.append(packChunk(b'KERN', struct.pack('>bH', font.tracking if font.kernTracking is None
                                                   else font.kernTracking, len(pairs)) + b''.join(pairs)))
    if font.zOrder or font.zPairs:
        if not 0 <= font.zOrder <= 4:
            raise FontError('invalid z-order mode')
        pairs = []
        for a, b, relation in font.zPairs:
            if not isScalar(a) or not isScalar(b) or relation not in (0, 1):
                raise FontError('invalid z-order constraint')
            pairs.append(struct.pack('>IIB', a, b, relation))
        chunks.append(packChunk(b'ZORD', struct.pack('>BH', font.zOrder, len(pairs)) + b''.join(pairs)))
    if font.clut:
        lut = font.clut
        if not 2 <= lut.size <= 64 or lut.interpolation not in (0, 1, 2) or lut.domain not in (0, 1) or len(lut.entries) != lut.size ** 3:
            raise FontError('invalid CLUT')
        chunks.append(packChunk(b'CLUT', struct.pack('>BBB', lut.size, lut.interpolation, lut.domain)
                                + b''.join(struct.pack('>HHH', *entry) for entry in lut.entries)))
    if font.source:
        source = font.source
        if source.format not in range(4) or source.flags != 1:
            raise FontError('only baked SRC sources are supported')
        chunks.append(packChunk(b'SRC ', struct.pack('>BBI', source.format, source.flags, len(source.data)) + source.data))
    total = 0
    for index, glyph in sorted(font.glyphs.items()):
        if glyph.index != index or not 1 <= glyph.width <= 255 or not 1 <= glyph.height <= 255 or glyph.flags:
            raise FontError('invalid glyph index, geometry or reserved flags')
        if len(glyph.cells) != glyph.width * glyph.height:
            raise FontError('glyph cell count does not match its geometry')
        total += len(glyph.cells)
        if total > MAX_CELLS:
            raise FontError('decoded cell budget exceeded')
        payload = bytearray(struct.pack('>HBBbbbB', index, glyph.width, glyph.height, glyph.leftBearing,
                                        glyph.rightBearing, glyph.baselineOffset, glyph.flags))
        if font.cellFlags & 8:
            position = 0
            while position < len(glyph.cells):
                cell = glyph.cells[position]
                run = 1
                while position + run < len(glyph.cells) and run < 255 and glyph.cells[position + run] == cell:
                    run += 1
                payload.append(run)
                payload.extend(writeCell(cell, font))
                position += run
        else:
            for cell in glyph.cells:
                payload.extend(writeCell(cell, font))
        chunks.append(packChunk(b'GLPH', bytes(payload)))
    if font.classic is not None and (font._originalArt is None or font._originalArt == artDigest(font)):
        chunks.append(packChunk(b'TDFC', font.classic))
    for extra in font.extras:
        checkKind(extra.kind)
        if extra.kind in SUPPORTED or extra.kind in UNDEFINED or 65 <= extra.kind[0] <= 90:
            raise FontError('extras must be unknown ancillary chunks')
        if font._originalBytes is None or 97 <= extra.kind[3] <= 122:
            chunks.append(packChunk(extra.kind, extra.payload))
    output = SIGNATURE + b''.join(chunks) + packChunk(b'FEND', b'')
    if len(output) > MAX_FILE_BYTES or len(chunks) + 1 > MAX_CHUNKS:
        raise FontError('encoded file/chunk budget exceeded')
    # Self-validation includes cross-chunk references and TDFC envelope sizes.
    decodeFont(output)
    return output
