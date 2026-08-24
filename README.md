# tdfiglet

Because your figlet ascii sucks.

![screenshot](https://git.trollforge.org/tdfiglet/plain/screenshot.png)

1198 TheDraw font files are included, holding **3711 fonts** between them — a
`.tdf` file is a chain of sub-fonts, not a single font. All three TheDraw font
types render: colour, block, and outline. Descenders render too, so the tail of
a `Q` and the hook of a `g` are no longer clipped.

## Installation

```
make
sudo make install
```

## Usage

If you're just trying to spam irc `tdfiglet -cm yes hello` will suffice.

```
usage: tdfiglet [options] input

    -f [font] Specify font file used.  Append :n to pick a
              sub-font, e.g. -f tdfonts_org:3
    -n [n]    Select sub-font by index.  Default is 0.
    -L        List the sub-fonts in a font file and exit.
    -j l|r|c  Justify left, right, or center.  Default is left.
    -w n      Set screen width.  Default is 80.
    -c a|m    Color format ANSI or mirc.  Default is ANSI.
    -e u|a    Encode as unicode or ASCII.  Default is unicode.
    -i        Print font details.
    -r        Use random font.
    -v        Warn about characters the font has no glyph for.
    -h        Print usage.
```

### Sub-fonts

Most `.tdf` files contain several fonts. `-L` lists them:

```
$ tdfiglet -L unused-fonts/tdfonts-2.tdf
file: unused-fonts/tdfonts-2.tdf
sub-fonts: 29
   0  Outline       outline  spacing  2  94/94 glyphs
   1  BigOutline    outline  spacing  2  94/94 glyphs
   2  ThickOutline  block    spacing  2  94/94 glyphs
   3  Medium        block    spacing  2  94/94 glyphs
   ...
```

Pick one with `-f name:n` or `-n`:

```
$ tdfiglet -f tdfonts-2:1 hello
```

### Missing glyphs

TheDraw fonts are frequently incomplete. Across the bundled corpus, letters are
present in ~99.5% of fonts, but **digits in only 46%** and **punctuation in
18%**; just 299 of 3711 sub-fonts define all 94 ASCII characters. Characters
with no glyph are skipped. Use `-v` to be told when that happens, or `-i` to see
a font's coverage before you commit to it.

## Documentation

- [docs/TDF-CLASSIC.md](docs/TDF-CLASSIC.md) — the TheDraw `.TDF` format,
  reverse-engineered and verified against all 3711 bundled sub-fonts. Covers
  the sub-font chain, all three glyph encodings, the outline character table,
  iCE colour, and the corpus anomalies a parser has to survive.
- [docs/TDFUTURE.md](docs/TDFUTURE.md) — draft specification for a successor
  format: Unicode, truecolour and alpha, embedded metadata and per-glyph
  provenance, kerning and stacking order, animation, and raster/video output.
  Design only; nothing in it is implemented.
