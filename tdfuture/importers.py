"""FIGlet cell import and optional baked TTF/OTF rasterization with honest provenance."""
from __future__ import annotations
import hashlib
import io
import math
from pathlib import Path
from .model import Cell, Font, FontError, Glyph, Provenance, Source, MAX_FILE_BYTES, MAX_CELLS, isScalar


def importFiglet(data: bytes, allowLossy: bool = False, embedSource: bool = False) -> tuple[Font, list[str]]:
    """Import .flf glyph cells, hardblanks and Unicode codetags.

    Smushing/kerning rules have no defined Draft 1 wire representation. They
    require allowLossy=True and become explicit full-width layout, with a report.
    UTF-8 is preferred; legacy non-UTF-8 input is decoded as CP437.
    """
    if len(data) > MAX_FILE_BYTES:
        raise FontError('FIGlet source exceeds file limit')
    try:
        text = data.decode('utf-8')
        encoding = 'UTF-8'
    except UnicodeError:
        text, encoding = data.decode('cp437'), 'CP437'
    lines = text.splitlines()
    if not lines or not lines[0].startswith('flf2a') or len(lines[0]) < 6:
        raise FontError('bad FIGlet signature')
    hardblank = lines[0][5]
    try:
        values = list(map(int, lines[0][6:].split()))
        height, baseline, maxLength, oldLayout, comments = values[:5]
    except (ValueError, IndexError) as error:
        raise FontError('invalid FIGlet header') from error
    if not 1 <= height <= 255 or not 0 <= baseline <= height or comments < 0 or comments > len(lines) - 1:
        raise FontError('invalid FIGlet height, baseline or comment count')
    direction = values[5] if len(values) > 5 else 0
    layout = values[6] if len(values) > 6 else (0 if oldLayout < 0 else 64 if oldLayout == 0 else 128 | oldLayout)
    losses = []
    if layout != 0:
        losses.append('FIGlet kerning/smushing rules are recorded as metadata but rendered full-width; Draft 1 lacks their wire encoding')
    if direction:
        losses.append('FIGlet right-to-left print direction is recorded but not applied; bidi layout is deferred')
    if losses and not allowLossy:
        raise FontError('; '.join(losses) + '; use --allow-lossy to acknowledge')
    position = 1 + comments
    font = Font(height=height, baseline=max(0, min(height - 1, baseline - 1)), repertoire=8)
    font.meta = {'title': 'Imported FIGlet font', 'tool': 'tdfx 0.1.0',
                 'description': '\n'.join(lines[1:position])[:60000],
                 'figlet-layout': str(layout), 'figlet-direction': str(direction),
                 'figlet-encoding': encoding, 'source-sha256': hashlib.sha256(data).hexdigest()}
    cellTotal = 0
    def glyph(cp):
        nonlocal position, cellTotal
        if position + height > len(lines):
            raise FontError(f'truncated FIGlet glyph U+{cp:04X}')
        rawRows = lines[position:position + height]
        position += height
        rows = []
        for rowIndex, row in enumerate(rawRows):
            if not row:
                raise FontError('FIGlet glyph row lacks an endmark')
            endmark = rawRows[0][-1]
            if not row.endswith(endmark):
                raise FontError('inconsistent FIGlet endmark')
            row = row[:-1]
            if rowIndex == height - 1:
                if not row.endswith(endmark):
                    raise FontError('FIGlet final glyph row requires a double endmark')
                row = row[:-1]
            rows.append(row)
        if not isScalar(cp) or cp in font.cmap:
            raise FontError('invalid or duplicate FIGlet codetag')
        width = max(1, max(map(len, rows)))
        if width > 255:
            raise FontError('FIGlet glyph exceeds 255 columns')
        cells = []
        for row in rows:
            for ch in row.ljust(width):
                cells.append(Cell(32 if ch == hardblank else 0 if ch == ' ' else ord(ch)))
        cellTotal += len(cells)
        if cellTotal > MAX_CELLS:
            raise FontError('FIGlet decoded cell budget exceeded')
        index = len(font.glyphs)
        font.cmap[cp] = index
        font.glyphs[index] = Glyph(index, width, height, tuple(cells))
        font.provenance[index] = Provenance(3, 'FIGlet source font', 0)
        if cp > 0xffff:
            font.capabilities |= 64
    for cp in range(32, 127):
        glyph(cp)
    # Historic mandatory German slots precede optional tagged Unicode glyphs.
    german = (196,214,220,228,246,252,223)
    for cp in german:
        if position >= len(lines) or _isCodetag(lines[position]):
            break
        glyph(cp)
    while position < len(lines):
        tag = lines[position].strip()
        position += 1
        if not tag:
            continue
        try:
            token = tag.split()[0]
            cp = int(token, 16) if token.lower().startswith('0x') else int(token, 8) if len(token) > 1 and token.startswith('0') else int(token)
        except ValueError as error:
            raise FontError('invalid FIGlet codetag') from error
        if cp < 0:
            if position + height > len(lines):
                raise FontError('truncated negative FIGlet codetag glyph')
            position += height
        else:
            glyph(cp)
    if embedSource:
        font.source = Source(3, 1, data)
    return font, losses


def _isCodetag(line: str) -> bool:
    token = line.split()[0] if line.split() else ''
    try:
        int(token, 0)
        return True
    except ValueError:
        return token.isdigit() or token.startswith('-') and token[1:].isdigit()


def importOutline(data: bytes, characters: str, size: int = 24, repertoire: str = 'half-block',
                  title: str = 'Rasterized outline', author: str = '', licenseId: str = '',
                  embedSource: bool = False) -> tuple[Font, list[str]]:
    """Bake a TTF/OTF into half-block RGBA16 or monochrome braille cell glyphs.

    Requires Pillow and fontTools. Checks the source cmap so missing characters
    are not silently replaced by .notdef. Every generated glyph has origin=5.
    Source embedding is opt-in and requires a caller-supplied license label; that
    label is an assertion, not a license-rights validation.
    """
    if len(data) > MAX_FILE_BYTES or not 4 <= size <= 128:
        raise FontError('outline source size or raster size limit exceeded')
    codepoints = sorted(set(map(ord, characters)))
    if not codepoints or len(codepoints) > 4096 or not all(isScalar(cp) for cp in codepoints):
        raise FontError('outline import requires 1..4096 Unicode scalars')
    if repertoire not in ('half-block', 'braille'):
        raise FontError('outline repertoire must be half-block or braille')
    if embedSource and not licenseId:
        raise FontError('embedding an outline source requires --license and permission to redistribute that font')
    try:
        from PIL import Image, ImageDraw, ImageFont
        from fontTools.ttLib import TTFont
    except ImportError as error:
        raise FontError('outline conversion requires Pillow and fontTools') from error
    try:
        with TTFont(io.BytesIO(data), lazy=True) as source:
            cmap = source.getBestCmap() or {}
            missing = [cp for cp in codepoints if cp not in cmap]
        if missing:
            raise FontError('outline has no source glyph for ' + ', '.join(f'U+{cp:04X}' for cp in missing[:20]))
        raster = ImageFont.truetype(io.BytesIO(data), size)
    except FontError:
        raise
    except Exception as error:
        raise FontError(f'cannot decode outline font: {type(error).__name__}: {error}') from error
    ascent, descent = raster.getmetrics()
    gridWidth, gridHeight = (1, 2) if repertoire == 'half-block' else (2, 4)
    height = math.ceil((ascent + descent) / gridHeight)
    font = Font(colorModel=6 if repertoire == 'half-block' else 0,
                cellFlags=1 if repertoire == 'half-block' else 0,
                height=height, baseline=min(height - 1, ascent // gridHeight),
                repertoire=6 if repertoire == 'half-block' else 7,
                capabilities=3 if repertoire == 'half-block' else 0)
    font.meta = {'title': title, 'tool': 'tdfx 0.1.0', 'source-sha256': hashlib.sha256(data).hexdigest(),
                 'raster-size': str(size), 'raster-repertoire': repertoire}
    if author:
        font.meta['author'] = author
    if licenseId:
        font.meta['license'] = licenseId
    total = 0
    for cp in codepoints:
        character = chr(cp)
        left, top, right, bottom = raster.getbbox(character, anchor='ls')
        shift = max(0, -left)
        width = max(1, math.ceil(max(right + shift, raster.getlength(character) + shift) / gridWidth))
        if width > 255 or height > 255:
            raise FontError('outline glyph exceeds cell geometry limits')
        mask = Image.new('L', (width * gridWidth, height * gridHeight))
        ImageDraw.Draw(mask).text((shift, ascent), character, anchor='ls', font=raster, fill=255)
        cells = []
        for y in range(height):
            for x in range(width):
                if repertoire == 'half-block':
                    a, b = mask.getpixel((x, y * 2)), mask.getpixel((x, y * 2 + 1))
                    cells.append(Cell(0x2580 if a or b else 0, (65535,65535,65535,a*257),
                                      (65535,65535,65535,b*257)))
                else:
                    bits = ((0,0,0),(0,1,1),(0,2,2),(1,0,3),(1,1,4),(1,2,5),(0,3,6),(1,3,7))
                    value = sum(1 << bit for dx,dy,bit in bits if mask.getpixel((x*2+dx,y*4+dy)) >= 128)
                    cells.append(Cell(0x2800 + value if value else 0))
        total += len(cells)
        if total > MAX_CELLS:
            raise FontError('outline decoded cell budget exceeded')
        index = len(font.glyphs)
        font.glyphs[index] = Glyph(index, width, height, tuple(cells))
        font.cmap[cp] = index
        font.provenance[index] = Provenance(5, author, 0)
        if cp > 0xffff:
            font.capabilities |= 64
    if embedSource:
        font.source = Source(1 if data.startswith(b'OTTO') else 0, 1, data)
    return font, ['outline is baked at the chosen raster size; outline hinting, kerning and shaping are not retained']
