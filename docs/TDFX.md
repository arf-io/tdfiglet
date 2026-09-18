# tdfx: conversion and static TDFuture rendering

`tdfx` is an optional Python 3.10+ companion to the existing C `tdfiglet`.
It implements the static subset described in [the implementation profile](TDFUTURE-IMPLEMENTATION.md).
It is not a claim that every feature in the Draft 1 design is complete.
The existing C program, command-line interface and font files are unchanged.

## Installation and tests

The codec, classic converter, FIGlet importer, ANSI/HTML renderers and tests use
only Python's standard library. PNG and TTF/OTF conversion are optional.

```sh
python3 tdfx --help
make test

# Optional dependencies, isolated from system Python:
python3 -m venv .venv
.venv/bin/python -m pip install Pillow fontTools
.venv/bin/python tdfx --help
.venv/bin/python -m unittest discover -s tests -v

# Install only the companion; no font installation or deletion:
make install-tdfx PREFIX="$HOME/.local"
"$HOME/.local/bin/tdfx" --version
```

The installed launcher uses `python3` from PATH. Activate the virtual environment
when using optional dependencies installed there. `DESTDIR` staging is supported
by `install-tdfx`; the existing C installation target is unchanged.

## Classic TDF: convert every subfont and restore the entire original file

A classic `.tdf` may contain multiple subfonts and historical trailing bytes.
A single `.tdfx` holds one subfont. Use the collection workflow for whole-file
archival restoration, not repeated single-subfont exports.

```sh
python3 tdfx inspect ./fonts/brndamgx.tdf
python3 tdfx convert ./fonts/brndamgx.tdf --all --rle -o ./converted --dry-run
python3 tdfx convert ./fonts/brndamgx.tdf --all --rle -o ./converted
python3 tdfx validate ./converted/0000.tdfx
python3 tdfx restore ./converted/manifest.json -o ./restored.tdf
cmp ./fonts/brndamgx.tdf ./restored.tdf
```

`converted` must not exist. The manifest is written last and records the original
file SHA-256, each converted member SHA-256, each original subfont SHA-256,
original byte length, and the complete trailer. Restoration verifies every hash
and the reconstructed whole file. Relative traversal, absolute member paths,
duplicate members and symlinked members are rejected. These hashes detect
corruption; they are not signatures and do not prove authorship.

One-subfont conversion and exact export:

```sh
python3 tdfx convert ./fonts/brndamgx.tdf --subfont 0 -o ./one.tdfx
python3 tdfx convert ./one.tdfx --to tdf -o ./one-restored.tdf
```

Each import stores the exact classic header and glyph block in `TDFC`. Exact
export independently re-decodes the archive and compares render-affecting state.
Editing a glyph cannot return a stale archived glyph. Metadata-only edits keep
the archive. A changed or native font requires `--allow-lossy` for classic export;
the converter reports discarded information to stderr.

```sh
python3 tdfx convert ./edited.tdfx --to tdf --allow-lossy -o ./legacy.tdf
```

The converter is strict on malformed/truncated classic inputs. The C renderer
retains its existing best-effort behavior. Conversion never repairs bad input
silently or overwrites the source implicitly.

## FIGlet input

```sh
python3 tdfx convert ./source.flf -o ./imported.tdfx
# Explicit acknowledgement when the source requests fitting/smushing or RTL:
python3 tdfx convert ./source.flf --allow-lossy -o ./full-width.tdfx
```

ASCII, optional German slots and tagged Unicode characters are read. Hardblanks
become opaque U+0020; ordinary blanks become transparent cells. Attribution and
original layout flags are recorded. Smushing/fitting and right-to-left layout
are not implemented; those fonts require explicit full-width degradation.
`--embed-source` includes source bytes only when explicitly requested. Ensure
that redistribution is permitted before sharing embedded source material.

## TTF/OTF input

```sh
.venv/bin/python tdfx convert /path/to/your-font.ttf --chars 'Hello 0123456789' \
  --size 24 --repertoire half-block -o ./outline.tdfx
.venv/bin/python tdfx convert /path/to/your-font.otf --chars 'Hello' \
  --size 32 --repertoire braille -o ./braille.tdfx
```

Half-block mode stores antialiased upper/lower coverage in RGBA16 foreground and
background channels. Braille uses a deterministic 2x4 threshold mask. Every
glyph is marked `PROV` origin 5 (rasterised). Source cmap coverage is checked:
missing characters fail, not silently render `.notdef`. Size-specific raster
metrics replace outline hinting, kerning and shaping, with an explicit notice.

Sources are not embedded by default. Explicit embedding requires
`--embed-source --license YOUR-LICENSE-LABEL`; a label is an assertion of rights,
not license validation. `--author` records only the author supplied by the user.
WOFF2 decoding, complex shaping and live outline rasterisation are not supported.

## Render

```sh
python3 tdfx render ./one.tdfx 'HELLO' --profile ansi-truecolor --hue 90
python3 tdfx render ./one.tdfx 'HELLO' --profile html -o ./preview.html
python3 tdfx render ./one.tdfx 'HELLO' --profile ssh-banner -o ./banner.txt
python3 tdfx render ./one.tdfx 'HELLO' --profile json -o ./cells.json
.venv/bin/python tdfx render ./outline.tdfx 'Hello' --profile png \
  --cell-font /path/to/local-monospace.ttf --cell-width 12 --cell-height 24 \
  -o ./preview.png
```

Profiles: `plain`, `ssh-banner`, `motd`, `ansi-classic`, `ansi-truecolor`, `irc`,
`html`, `png`, `json`. Missing glyphs fail by default; explicitly select
`--missing skip` or `--missing space` (the latter requires a space glyph).

`ssh-banner` permits only printable ASCII and LF, at most 80 columns and 24 rows.
`motd` permits UTF-8 and SGR only. Unsafe Unicode controls, combining/wide cells
and line/paragraph separators are replaced with a diagnostic. `ansi-classic`
emits CP437 bytes and 16-color SGR, including bright iCE backgrounds. IRC output
uses two-digit mIRC colors and enforces 400 bytes per line including formatting.
Color fallback uses a documented DOS16 palette, not a queried terminal theme.

HTML escapes content and has no external resources. PNG output is 8-bit RGB on
black; Block and braille art are drawn geometrically. Other Unicode art requires a local
monospace cell font, which is never embedded.
Text outputs also composite on black. `json` exposes the resolved cell grid for
other programs; it is render output, not an editable font interchange schema.

## File safety and exit codes

Single-file outputs use a same-directory temporary file and atomic no-clobber
commit. Existing files require `--force`; directories and symlinks are never
replaced. `--dry-run` validates and reports without creating files/directories.
Keep source and destination directories under your control during conversion;
this is not a sandbox for concurrently attacker-controlled directory trees.

Success is 0, input/format/I/O error is 2, interruption is 130. Diagnostics go to
stderr with untrusted text JSON-escaped. Core formats never execute embedded
code or shader programs. Outline inputs invoke optional native raster libraries;
run untrusted outline processing with ordinary OS resource isolation.

## Library APIs

```python
from pathlib import Path
from tdfuture.classic import importClassic, exportClassic
from tdfuture.codec import decodeFont, encodeFont
from tdfuture.render import layoutText, renderHtml

fonts, trailer = importClassic(Path('source.tdf').read_bytes())
font = decodeFont(encodeFont(fonts[0]))
restored, losses = exportClassic(font)  # fails if exact export is no longer possible
html = renderHtml(layoutText(font, 'HELLO'), font.meta.get('title', 'TDFuture'))
```

The dataclass model is public and mutable at the font level. `encodeFont` validates
before writing. Cells and glyphs are immutable; use `dataclasses.replace` to edit
a glyph and record appropriate provenance for authored/generated additions.
