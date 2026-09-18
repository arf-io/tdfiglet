"""Command-line conversion, archival restoration, validation and safe static rendering."""
from __future__ import annotations
import argparse
import base64
import binascii
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from . import __version__
from .model import FontError, MAX_FILE_BYTES
from .codec import SIGNATURE, decodeFont, encodeFont
from .classic import CLASSIC_MAGIC, importClassic, exportClassic
from .importers import importFiglet, importOutline
from .render import layoutText, renderText, renderHtml, renderPng


def readBytes(path: Path, noFollow: bool = False) -> bytes:
    """Read a bounded regular file; archival members reject final-component symlinks.

    Nonblocking open followed by fstat also prevents an untrusted FIFO from
    hanging conversion. O_NOFOLLOW closes the symlink-check race on POSIX.
    """
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
    if noFollow:
        if path.is_symlink():
            raise FontError('manifest member must not be a symbolic link')
        flags |= getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode):
            raise FontError('input must be a regular file')
        if details.st_size > MAX_FILE_BYTES:
            raise FontError(f'file exceeds {MAX_FILE_BYTES} bytes: {path.name}')
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise FontError(f'file exceeds {MAX_FILE_BYTES} bytes: {path.name}')
    return data


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def diagnostic(message: str):
    """Escape untrusted font metadata and paths before writing a terminal diagnostic."""
    print('tdfx: ' + json.dumps(message, ensure_ascii=True), file=sys.stderr)


def writeAtomic(path: Path, data: bytes, force: bool = False, dryRun: bool = False):
    """Commit one file using a same-directory temporary file and atomic no-clobber link.

    Default never overwrites. force uses atomic replace. A dry-run does not
    create directories or temporary files. Parent directories must already exist.
    """
    if path.exists() or path.is_symlink():
        if not force:
            raise FontError(f'output exists; use --force to replace: {path.name}')
        if path.is_dir() or path.is_symlink():
            raise FontError('refusing to replace a directory or symbolic link')
    if not path.parent.is_dir():
        raise FontError(f'output parent directory does not exist: {path.parent}')
    if dryRun:
        diagnostic(f'dry-run: would write {len(data)} bytes to {path}; SHA256 {sha256(data)}')
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.tdfx-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        if force:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
            os.unlink(temporary)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def loadFonts(path: Path, args):
    """Detect the source format by signature, with explicit outline opt-in via extension."""
    data = readBytes(path)
    if data.startswith(SIGNATURE):
        return [decodeFont(data)], b'', data, []
    if data.startswith(CLASSIC_MAGIC):
        fonts, trailer = importClassic(data)
        return fonts, trailer, data, []
    if data.startswith(b'flf2a'):
        font, losses = importFiglet(data, getattr(args, 'allow_lossy', False), getattr(args, 'embed_source', False))
        return [font], b'', data, losses
    if path.suffix.lower() in ('.ttf', '.otf'):
        font, losses = importOutline(data, getattr(args, 'chars', ''.join(map(chr, range(32,127)))),
                                     getattr(args, 'size', 24), getattr(args, 'repertoire', 'half-block'),
                                     path.stem, getattr(args, 'author', '') or '', getattr(args, 'license', '') or '',
                                     getattr(args, 'embed_source', False))
        return [font], b'', data, losses
    raise FontError('unrecognized font: expected TDF, TDFuture, FIGlet FLF, TTF or OTF')


def selectedFont(fonts, subfont: int):
    if not 0 <= subfont < len(fonts):
        raise FontError(f'subfont index {subfont} outside 0..{len(fonts)-1}')
    return fonts[subfont]


def describeFont(font, index=0):
    return {'index': index, 'metadata': font.meta, 'glyphs': len(font.glyphs),
            'mappedCodepoints': len(font.cmap), 'height': font.height, 'baseline': font.baseline,
            'colorModel': font.colorModel, 'cellFlags': font.cellFlags, 'capabilities': font.capabilities,
            'tracking': font.tracking, 'hasClassicArchive': font.classic is not None,
            'provenanceEntries': len(font.provenance), 'unknownChunks': [c.kind.decode('ascii') for c in font.extras]}


def convertBundle(fonts, trailer: bytes, source: bytes, sourceName: str, destination: Path, args):
    """Write a collection directory with a final manifest commit marker; roll back on failure."""
    if destination.exists() or destination.is_symlink():
        raise FontError('collection output must be a new directory; existing collections are never overwritten')
    if not destination.parent.is_dir():
        raise FontError('collection parent directory must exist')
    outputs, entries = [], []
    for index, font in enumerate(fonts):
        if args.author:
            font.meta['author'] = args.author
        if args.license:
            font.meta['license'] = args.license
        if args.rle:
            font.cellFlags |= 8
        name = f'{index:04d}.tdfx'
        data = encodeFont(font)
        restored, losses = exportClassic(font)
        if losses:
            raise FontError('collection archival conversion unexpectedly became lossy')
        outputs.append((name, data))
        entries.append({'file': name, 'tdfxSha256': sha256(data), 'classicSha256': sha256(restored[20:]),
                        'title': font.meta.get('title', '')})
    manifest = {'format': 'tdfx-classic-collection', 'version': 1, 'sourceName': sourceName,
                'sourceSha256': sha256(source), 'sourceBytes': len(source),
                'trailerBase64': base64.b64encode(trailer).decode('ascii'), 'subfonts': entries}
    outputs.append(('manifest.json', (json.dumps(manifest, ensure_ascii=True, indent=2) + '\n').encode()))
    if args.dry_run:
        diagnostic(f'dry-run: would create {len(fonts)} font files and manifest in {destination}; no files written')
        return
    destination.mkdir(mode=0o755)
    try:
        for name, data in outputs:
            writeAtomic(destination / name, data)
    except BaseException:
        # Only remove the new directory created by this invocation.
        shutil.rmtree(destination)
        raise
    diagnostic(f'created collection with {len(fonts)} subfonts; source SHA256 {sha256(source)}')


def restoreBundle(manifestPath: Path) -> bytes:
    """Validate hashes, reject escaping/symlinked member paths, and reconstruct the exact source TDF."""
    try:
        manifest = json.loads(readBytes(manifestPath))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise FontError('invalid collection manifest JSON') from error
    if not isinstance(manifest, dict) or manifest.get('format') != 'tdfx-classic-collection' or manifest.get('version') != 1:
        raise FontError('unsupported collection manifest format/version')
    entries = manifest.get('subfonts')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 4096:
        raise FontError('invalid collection subfont list')
    pieces, seen, total = [CLASSIC_MAGIC], set(), 20
    for entry in entries:
        if not isinstance(entry, dict):
            raise FontError('invalid manifest subfont entry')
        name = entry.get('file')
        if not isinstance(name, str) or not name or name in ('.','..') or '/' in name or '\\' in name or '\x00' in name or Path(name).is_absolute():
            raise FontError('manifest member must be a local basename, not a path')
        if name in seen:
            raise FontError('duplicate manifest member')
        seen.add(name)
        path = manifestPath.parent / name
        if path.is_symlink() or not path.is_file():
            raise FontError('manifest member must be a regular, non-symlink file')
        data = readBytes(path, noFollow=True)
        if sha256(data) != entry.get('tdfxSha256'):
            raise FontError(f'TDFuture member hash mismatch: {name}')
        restored, losses = exportClassic(decodeFont(data))
        if losses or sha256(restored[20:]) != entry.get('classicSha256'):
            raise FontError(f'classic archive hash mismatch: {name}')
        pieces.append(restored[20:])
        total += len(restored) - 20
        if total > MAX_FILE_BYTES:
            raise FontError('restored collection exceeds file limit')
    try:
        trailer = base64.b64decode(manifest.get('trailerBase64', ''), validate=True)
    except (ValueError, TypeError, binascii.Error) as error:
        raise FontError('invalid trailer encoding') from error
    total += len(trailer)
    if total > MAX_FILE_BYTES:
        raise FontError('restored collection exceeds file limit')
    result = b''.join(pieces) + trailer
    if len(result) != manifest.get('sourceBytes') or sha256(result) != manifest.get('sourceSha256'):
        raise FontError('whole-source SHA256/length mismatch; manifest is not a verified restoration')
    return result


def buildParser() -> argparse.ArgumentParser:
    """Construct the public CLI parser; --help is available without optional dependencies."""
    parser = argparse.ArgumentParser(prog='tdfx', description='TDFuture static codec, font converter and archival restoration tool')
    parser.add_argument('--version', action='version', version=f'tdfx {__version__}')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'validate'):
        command = commands.add_parser(name, help='inspect metadata' if name == 'inspect' else 'validate structure and resource limits')
        command.add_argument('source', type=Path)
    convert = commands.add_parser('convert', help='convert TDF/TDFX/FLF/TTF/OTF fonts; source files are never modified implicitly')
    convert.add_argument('source', type=Path)
    convert.add_argument('-o', '--output', required=True, type=Path)
    convert.add_argument('--to', choices=('tdfx','tdf'), help='output format; inferred from output suffix otherwise')
    convert.add_argument('--all', action='store_true', help='archive every classic subfont and trailer into a new collection directory')
    convert.add_argument('--subfont', type=int, default=0)
    convert.add_argument('--allow-lossy', action='store_true', help='acknowledge explicitly reported losses; never assumed')
    convert.add_argument('--rle', action='store_true', help='encode TDFuture glyphs with run-length compression')
    convert.add_argument('--embed-source', action='store_true', help='embed FLF or outline source bytes; requires redistribution rights')
    convert.add_argument('--chars', default=''.join(map(chr,range(32,127))), help='outline characters to bake (default printable ASCII)')
    convert.add_argument('--size', type=int, default=24, help='outline raster height in pixels, 4..128')
    convert.add_argument('--repertoire', choices=('half-block','braille'), default='half-block')
    convert.add_argument('--author', help='explicitly known source artist; never guessed')
    convert.add_argument('--license', help='source license identifier or permission label')
    restore = commands.add_parser('restore', help='restore a whole classic file from a verified collection manifest')
    restore.add_argument('manifest', type=Path)
    restore.add_argument('-o','--output', required=True, type=Path)
    render = commands.add_parser('render', help='render a static cell font without executing font-supplied code')
    render.add_argument('source', type=Path)
    render.add_argument('text')
    render.add_argument('--subfont', type=int, default=0)
    render.add_argument('--profile', choices=('plain','ssh-banner','motd','ansi-classic','ansi-truecolor','irc','html','png','json'), default='ansi-truecolor')
    render.add_argument('--missing', choices=('error','skip','space'), default='error')
    render.add_argument('--hue', type=float, default=0, help='OKLCh hue rotation in degrees')
    render.add_argument('--cell-font', help='local monospace TTF/OTF for Unicode PNG artwork; not embedded')
    render.add_argument('--cell-width', type=int, default=12)
    render.add_argument('--cell-height', type=int, default=24)
    render.add_argument('-o','--output', type=Path, help='default stdout; PNG must use an output file')
    for command in (convert, restore, render):
        command.add_argument('--force', action='store_true', help='atomically replace a regular output file, never a collection directory')
        command.add_argument('--dry-run', action='store_true', help='validate and report outputs without creating any files')
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run tdfx. Return 0 on success, 2 on invalid input/I/O failure, 130 on interruption."""
    args = buildParser().parse_args(argv)
    try:
        if args.command == 'restore':
            result = restoreBundle(args.manifest)
            writeAtomic(args.output, result, args.force, args.dry_run)
            diagnostic(f'verified whole-source SHA256 {sha256(result)}')
            return 0
        fonts, trailer, source, losses = loadFonts(args.source, args)
        for loss in losses:
            diagnostic('conversion: ' + loss)
        if args.command in ('inspect','validate'):
            # Re-encoding exercises writer representability and cross-chunk checks.
            for font in fonts:
                encodeFont(font)
            result = {'format': 'tdfx-inspection', 'valid': True, 'sourceSha256': sha256(source),
                      'sourceBytes': len(source), 'trailerBytes': len(trailer),
                      'fonts': [describeFont(font, index) for index, font in enumerate(fonts)]}
            print(json.dumps(result, ensure_ascii=True, indent=2))
            return 0
        if args.command == 'convert':
            if args.all:
                if not source.startswith(CLASSIC_MAGIC) or args.to == 'tdf' or args.subfont:
                    raise FontError('--all requires classic TDF input, TDFuture output and no subfont selection')
                convertBundle(fonts, trailer, source, args.source.name, args.output, args)
                return 0
            font = selectedFont(fonts, args.subfont)
            if args.author:
                font.meta['author'] = args.author
            if args.license:
                font.meta['license'] = args.license
            targetFormat = args.to or ('tdf' if args.output.suffix.lower() == '.tdf' else 'tdfx')
            if args.rle:
                font.cellFlags |= 8
            if targetFormat == 'tdf':
                result, extraLosses = exportClassic(font, args.allow_lossy)
                for loss in extraLosses:
                    diagnostic('loss: ' + loss)
            else:
                result = encodeFont(font)
            if source.startswith(CLASSIC_MAGIC) and (len(fonts) > 1 or trailer):
                diagnostic('selected-subfont conversion does not retain other subfonts or file trailer; use --all for whole-file restoration')
            writeAtomic(args.output, result, args.force, args.dry_run)
            return 0
        if not math.isfinite(args.hue):
            raise FontError('hue must be finite')
        font = selectedFont(fonts, args.subfont)
        canvas = layoutText(font, args.text, args.missing, args.hue)
        if args.profile == 'html':
            result = renderHtml(canvas, font.meta.get('title','TDFuture')).encode('utf-8')
        elif args.profile == 'png':
            if not args.output:
                raise FontError('PNG rendering requires -o/--output to avoid binary terminal output')
            result = renderPng(canvas, args.cell_font, args.cell_width, args.cell_height)
        elif args.profile == 'json':
            result = (json.dumps({'format':'tdfx-cell-grid','version':1,'width':canvas.width,'height':canvas.height,
                                 'cells':[[cell.codepoint,cell.fg,cell.bg] for cell in canvas.cells],
                                 'warnings':canvas.warnings}, ensure_ascii=True) + '\n').encode()
        else:
            result = renderText(canvas, args.profile).encode('cp437' if args.profile == 'ansi-classic' else 'utf-8')
        for warning in dict.fromkeys(canvas.warnings):
            diagnostic(warning)
        if args.output:
            writeAtomic(args.output, result, args.force, args.dry_run)
        elif args.dry_run:
            diagnostic(f'dry-run: would write {len(result)} bytes to stdout')
        else:
            sys.stdout.buffer.write(result)
        return 0
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        diagnostic('interrupted; no incomplete single-file output committed')
        return 130
    except (FontError, OSError, UnicodeError) as error:
        diagnostic(str(error))
        return 2
