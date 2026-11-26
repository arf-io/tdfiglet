"""Deterministic static layout and safe plain, ANSI, mIRC, HTML and PNG backends."""
from __future__ import annotations
from dataclasses import dataclass
import heapq
import html
import io
import unicodedata
from .model import Cell, Font, FontError, MAX_CELLS, MAX_INSTANCES
from .color import (BLACK, WHITE, ANSI_TO_DOS, composite, resolveColor, nearestColor,
                    rotateHue, applyClut)


@dataclass
class Canvas:
    """Resolved RGBA16 cell grid plus explicit layout/degradation diagnostics."""
    width: int
    height: int
    cells: tuple[Cell, ...]
    warnings: list[str]


def instanceOrder(font: Font, instances, warnings: list[str]) -> list[int]:
    """Resolve pair overrides on instances; discard the furthest cyclic edge with a diagnostic."""
    count = len(instances)
    def priority(index):
        if font.zOrder == 1:
            return (-index, index)
        if font.zOrder == 2:
            return (-abs(index - (count - 1) / 2), index)
        if font.zOrder == 3:
            return (abs(index - (count - 1) / 2), index)
        return (index, index)
    if not font.zPairs:
        if font.zOrder == 4 and count > 1:
            raise FontError('explicit z-order requires a relation for every pair of instances')
        return sorted(range(count), key=priority)
    if count > 512:
        raise FontError('pairwise z-order is limited to 512 instances per line')
    graph = [set() for _ in instances]
    covered = set()
    positions = {}
    for index, instance in enumerate(instances):
        positions.setdefault(instance[0], []).append(index)
    for a, b, relation in font.zPairs:
        for first in positions.get(a, ()):
            for second in positions.get(b, ()):
                if first == second:
                    continue
                behind, front = (first, second) if relation == 0 else (second, first)
                graph[behind].add(front)
                covered.add(tuple(sorted((first, second))))
    if sum(map(len, graph)) > 8192:
        raise FontError('z-order constraint budget exceeded')
    if font.zOrder == 4 and len(covered) != count * (count - 1) // 2:
        raise FontError('explicit z-order is incomplete for the requested text')
    while True:
        indegree = [0] * count
        for edges in graph:
            for vertex in edges:
                indegree[vertex] += 1
        ready = [(priority(i), i) for i, degree in enumerate(indegree) if not degree]
        heapq.heapify(ready)
        order = []
        while ready:
            _, index = heapq.heappop(ready)
            order.append(index)
            for vertex in sorted(graph[index]):
                indegree[vertex] -= 1
                if not indegree[vertex]:
                    heapq.heappush(ready, (priority(vertex), vertex))
        if len(order) == count:
            return order
        # Tarjan SCC isolates cycles; downstream acyclic edges must not be discarded.
        sequence, stack, active, number, low, groups = [0], [], set(), {}, {}, {}
        def visit(vertex):
            number[vertex] = low[vertex] = sequence[0]
            sequence[0] += 1
            stack.append(vertex)
            active.add(vertex)
            for neighbor in sorted(graph[vertex]):
                if neighbor not in number:
                    visit(neighbor)
                    low[vertex] = min(low[vertex], low[neighbor])
                elif neighbor in active:
                    low[vertex] = min(low[vertex], number[neighbor])
            if low[vertex] == number[vertex]:
                while True:
                    member = stack.pop()
                    active.remove(member)
                    groups[member] = vertex
                    if member == vertex:
                        break
        for index in range(count):
            if index not in number:
                visit(index)
        cyclic = [(a, b) for a, edges in enumerate(graph) for b in edges if groups[a] == groups[b]]
        a, b = max(cyclic, key=lambda pair: (abs(pair[0] - pair[1]), pair[0], pair[1]))
        graph[a].remove(b)
        warnings.append(f'z-order cycle: discarded U+{instances[a][0]:04X}@{a} behind U+{instances[b][0]:04X}@{b}')


def layoutText(font: Font, text: str, missing: str = 'error', hue: float = 0) -> Canvas:
    """Lay out text with integer kerning and per-instance z-order, bounded to one million cells.

    Missing glyph policy: error, skip, or space. Newlines create independent
    lines; baselineOffset is an additive vertical row displacement in this
    implementation profile. Returns resolved colors and diagnostics, not ANSI.
    """
    if len(text) > MAX_INSTANCES:
        raise FontError(f'text exceeds {MAX_INSTANCES} glyph-instance limit')
    if missing not in ('error', 'skip', 'space'):
        raise FontError('unknown missing-glyph policy')
    warnings, lines = [], []
    totalHeight, maxWidth = 0, 0
    for line in text.split('\n'):
        instances = []
        x, previous = 0, None
        for character in line:
            cp = ord(character)
            index = font.cmap.get(cp)
            if index is None:
                if missing == 'error':
                    raise FontError(f'font has no glyph for U+{cp:04X}')
                warnings.append(f'missing glyph U+{cp:04X}: {missing}')
                if missing == 'skip':
                    continue
                cp, index = 32, font.cmap.get(32)
                if index is None:
                    raise FontError('space fallback requested but font has no space glyph')
            glyph = font.glyphs[index]
            if previous is not None:
                oldCp, oldGlyph = previous
                tracking = font.tracking if font.kernTracking is None else font.kernTracking
                x += tracking + oldGlyph.rightBearing + glyph.leftBearing + font.kerning.get((oldCp, cp), 0)
            instances.append((cp, glyph, x, glyph.baselineOffset))
            x += glyph.width
            previous = (cp, glyph)
        minX = min((item[2] for item in instances), default=0)
        minY = min(0, min((item[3] for item in instances), default=0))
        width = max((item[2] + item[1].width for item in instances), default=0) - minX
        height = max(font.height, max((item[3] + item[1].height for item in instances), default=0)) - minY
        maxWidth = max(maxWidth, width)
        totalHeight += height
        if maxWidth * totalHeight > MAX_CELLS or totalHeight > MAX_CELLS:
            raise FontError('rendered canvas exceeds cell budget')
        lines.append((instances, minX, minY, height))
    blank = Cell(0, WHITE, BLACK)
    cells = [blank] * (maxWidth * totalHeight)
    top = 0
    for instances, minX, minY, height in lines:
        for index in instanceOrder(font, instances, warnings):
            _, glyph, x, y = instances[index]
            for position, cell in enumerate(glyph.cells):
                if not cell.codepoint:
                    continue
                fg = resolveColor(cell.fg, font.colorModel, font.palette)
                bg = resolveColor(cell.bg, font.colorModel, font.palette, True)
                if font.clut:
                    fg, bg = applyClut(fg, font.clut), applyClut(bg, font.clut)
                if hue:
                    fg, bg = rotateHue(fg, hue), rotateHue(bg, hue)
                if fg[3] == 0 and bg[3] == 0:
                    continue
                target = (top + y - minY + position // glyph.width) * maxWidth + x - minX + position % glyph.width
                back = cells[target]
                cells[target] = Cell(cell.codepoint, composite(fg, back.fg if back.codepoint else back.bg), composite(bg, back.bg))
        top += height
    return Canvas(maxWidth, totalHeight, tuple(cells), warnings)


def displayCharacter(codepoint: int, asciiOnly: bool = False) -> tuple[str, str | None]:
    """Map a scalar to one safe terminal column; report controls, wide cells and repertoire loss."""
    if codepoint == 0:
        return ' ', None
    character = chr(codepoint)
    category = unicodedata.category(character)
    if category[0] in ('C', 'M') or category in ('Zl', 'Zp') or unicodedata.east_asian_width(character) in ('W', 'F'):
        return '?', f'cell U+{codepoint:04X} is not a safe single-column display character; replaced by ?'
    if asciiOnly and not 32 <= codepoint <= 126:
        if 0x2500 <= codepoint <= 0x257f:
            replacement = '-'
        elif 0x2580 <= codepoint <= 0x259f:
            replacement = '#'
        else:
            replacement = '?'
        return replacement, f'cell U+{codepoint:04X} degraded to ASCII {replacement!r}'
    return character, None


def renderText(canvas: Canvas, profile: str = 'ansi-truecolor') -> str:
    """Render static text; ssh-banner is printable ASCII+LF, motd emits SGR only.

    ansi-classic is returned as Unicode with CP437-representable art; the CLI
    encodes that profile as CP437 bytes. IRC lines include at most 400 UTF-8 bytes.
    Oversize security profiles fail instead of truncating or emitting controls.
    """
    profiles = ('plain', 'ssh-banner', 'motd', 'ansi-classic', 'ansi-truecolor', 'irc')
    if profile not in profiles:
        raise FontError(f'unknown text profile {profile}')
    if profile == 'ssh-banner' and (canvas.width > 80 or canvas.height > 24):
        raise FontError('ssh-banner requires at most 80 columns and 24 rows')
    if profile == 'ssh-banner':
        canvas.warnings.append('ssh-banner: color is omitted and non-ASCII artwork is explicitly degraded')
    if profile in ('ansi-classic', 'irc'):
        canvas.warnings.append(f'{profile}: colors use the documented default 16-color palette')
    output, notices = [], set()
    mirc = (1,2,3,10,5,6,7,15,14,12,9,11,4,13,8,0)
    for row in range(canvas.height):
        pieces, previous = [], None
        for cell in canvas.cells[row * canvas.width:(row + 1) * canvas.width]:
            character, warning = displayCharacter(cell.codepoint, profile == 'ssh-banner')
            if warning:
                notices.add(warning)
            if profile == 'ansi-classic':
                try:
                    character.encode('cp437')
                except UnicodeError:
                    notices.add(f'cell U+{cell.codepoint:04X} is not CP437; replaced by ?')
                    character = '?'
            if profile not in ('plain', 'ssh-banner'):
                fg, bg = composite(cell.fg, BLACK), composite(cell.bg, BLACK)
                if profile in ('ansi-classic', 'irc'):
                    colors = (nearestColor(fg), nearestColor(bg))
                else:
                    colors = (tuple(round(v / 257) for v in fg[:3]), tuple(round(v / 257) for v in bg[:3]))
                if colors != previous:
                    if profile == 'irc':
                        pieces.append(f'\x03{mirc[colors[0]]:02d},{mirc[colors[1]]:02d}')
                    elif profile == 'ansi-classic':
                        a, b = ANSI_TO_DOS[colors[0]], ANSI_TO_DOS[colors[1]]
                        pieces.append(f'\x1b[{30+a if a < 8 else 90+a-8};{40+b if b < 8 else 100+b-8}m')
                    else:
                        pieces.append('\x1b[38;2;'+ ';'.join(map(str,colors[0])) + ';48;2;' + ';'.join(map(str,colors[1])) + 'm')
                    previous = colors
            pieces.append(character)
        if profile not in ('plain', 'ssh-banner'):
            pieces.append('\x0f' if profile == 'irc' else '\x1b[0m')
        line = ''.join(pieces)
        if profile == 'irc' and len(line.encode('utf-8')) > 400:
            raise FontError('IRC profile exceeds 400 bytes per line including formatting')
        output.append(line)
    canvas.warnings.extend(sorted(notices))
    return '\n'.join(output) + '\n'


def renderHtml(canvas: Canvas, title: str = 'TDFuture') -> str:
    """Return self-contained HTML with escaped text/title and inline numeric colors only."""
    lines, notices = [], set()
    for row in range(canvas.height):
        parts = []
        for cell in canvas.cells[row * canvas.width:(row + 1) * canvas.width]:
            character, warning = displayCharacter(cell.codepoint)
            if warning:
                notices.add(warning)
            fg = ','.join(str(round(v / 257)) for v in composite(cell.fg, BLACK)[:3])
            bg = ','.join(str(round(v / 257)) for v in composite(cell.bg, BLACK)[:3])
            parts.append(f'<span style="color:rgb({fg});background-color:rgb({bg})">{html.escape(character)}</span>')
        lines.append(''.join(parts))
    canvas.warnings.extend(sorted(notices))
    return ('<!doctype html><html><head><meta charset="utf-8"><title>' + html.escape(title)
            + '</title></head><body style="margin:0;background:#000"><pre style="font-family:monospace;line-height:1;margin:0">'
            + '\n'.join(lines) + '</pre></body></html>\n')


def drawSubcells(draw, codepoint: int, width: int, height: int, color) -> bool:
    """Draw block/braille cell geometry directly, independent of a typeface's bearings.

    Returns True for handled cell artwork. Coordinates follow the cell grid, so
    upper/lower blocks cannot become identical through glyph-box centering.
    """
    rectangles = None
    if codepoint == 0x2580:
        rectangles = [(0,0,8,4)]
    elif 0x2581 <= codepoint <= 0x2588:
        rectangles = [(0,8-(codepoint-0x2580),8,8)]
    elif 0x2589 <= codepoint <= 0x258f:
        rectangles = [(0,0,0x2590-codepoint,8)]
    elif codepoint == 0x2590:
        rectangles = [(4,0,8,8)]
    elif codepoint in (0x2594, 0x2595):
        rectangles = [(0,0,8,1)] if codepoint == 0x2594 else [(7,0,8,8)]
    elif 0x2596 <= codepoint <= 0x259f:
        quadrants = ((2,), (3,), (0,), (0,2,3), (0,3), (0,1,2),
                     (0,1,3), (1,), (1,2), (1,2,3))[codepoint-0x2596]
        rectangles = [(q%2*4,q//2*4,q%2*4+4,q//2*4+4) for q in quadrants]
    if rectangles is not None:
        for x1,y1,x2,y2 in rectangles:
            bounds = (x1*width//8,y1*height//8,x2*width//8-1,y2*height//8-1)
            if bounds[2] >= bounds[0] and bounds[3] >= bounds[1]:
                draw.rectangle(bounds, fill=color)
        return True
    if 0x2591 <= codepoint <= 0x2593:
        bayer = ((0,8,2,10),(12,4,14,6),(3,11,1,9),(15,7,13,5))
        threshold = (codepoint-0x2590)*4
        for y in range(height):
            for x in range(width):
                if bayer[y%4][x%4] < threshold:
                    draw.point((x,y), fill=color)
        return True
    if 0x2800 <= codepoint <= 0x28ff:
        bits = ((0,0,0),(0,1,1),(0,2,2),(1,0,3),(1,1,4),(1,2,5),(0,3,6),(1,3,7))
        radius = max(.25, min(width/4,height/8)*.6)
        for x,y,bit in bits:
            if (codepoint-0x2800) & (1 << bit):
                cx,cy = (x+.5)*width/2,(y+.5)*height/4
                draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius), fill=color)
        return True
    return False


def renderPng(canvas: Canvas, cellFont: str | None = None, cellWidth: int = 12, cellHeight: int = 24) -> bytes:
    """Rasterize to PNG using optional Pillow. Non-block/braille Unicode artwork requires an explicit local cell font.

    The cell font is never embedded or redistributed. Image dimensions are
    bounded to 32 million pixels; missing dependencies raise FontError.
    """
    if not 1 <= cellWidth <= 128 or not 1 <= cellHeight <= 256:
        raise FontError('invalid raster cell dimensions')
    width, height = max(1, canvas.width * cellWidth), max(1, canvas.height * cellHeight)
    if width * height > 32_000_000:
        raise FontError('raster exceeds 32 million pixel limit')
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:
        raise FontError('PNG output requires Pillow (python3 -m pip install Pillow)') from error
    if not cellFont and any(cell.codepoint > 126 and not 0x2580 <= cell.codepoint <= 0x259f
                            and not 0x2800 <= cell.codepoint <= 0x28ff for cell in canvas.cells):
        raise FontError('Unicode PNG artwork requires --cell-font pointing to a local monospace TTF/OTF')
    try:
        font = ImageFont.truetype(cellFont, max(1, cellHeight - 2)) if cellFont else ImageFont.load_default()
        if cellFont:
            ascent, descent = font.getmetrics()
            scale = min(1, cellHeight / max(1, ascent + descent), cellWidth / max(1, font.getlength('M')))
            font = ImageFont.truetype(cellFont, max(1, int((cellHeight - 2) * scale)))
    except OSError as error:
        raise FontError(f'cannot load cell font: {error}') from error
    image = Image.new('RGB', (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    for index, cell in enumerate(canvas.cells):
        x, y = (index % canvas.width) * cellWidth, (index // canvas.width) * cellHeight
        fg = tuple(round(v / 257) for v in composite(cell.fg, BLACK)[:3])
        bg = tuple(round(v / 257) for v in composite(cell.bg, BLACK)[:3])
        draw.rectangle((x, y, x + cellWidth - 1, y + cellHeight - 1), fill=bg)
        character, warning = displayCharacter(cell.codepoint)
        if warning and warning not in canvas.warnings:
            canvas.warnings.append(warning)
        # Draw into a per-cell tile so wide font metrics cannot bleed into neighbors.
        tile = Image.new('RGB', (cellWidth, cellHeight), bg)
        tileDraw = ImageDraw.Draw(tile)
        if not drawSubcells(tileDraw, cell.codepoint, cellWidth, cellHeight, fg):
            ascent, descent = font.getmetrics()
            baseline = (cellHeight - ascent - descent) // 2 + ascent
            tileDraw.text(((cellWidth - font.getlength(character)) / 2, baseline),
                          character, font=font, fill=fg, anchor='ls')
        image.paste(tile, (x, y))
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()
