# The TheDraw Font Format (`.TDF`) — Classic Specification

**Status:** descriptive. This documents the format as it exists in the wild, in
files written by TheDraw 4.x (Ian E. Davis, 1986–1993) and by the font editors
that followed it.

**Provenance of this document.** Every structural claim below was verified
against the 1198 `.tdf` files bundled with `tdfiglet` (1071 in `fonts/`, 127 in
`unused-fonts/`), containing 3711 sub-fonts between them. Where a claim rests on
an external source rather than on that corpus, it is marked *[external]* and
attributed. Where the corpus contradicts a widely-repeated claim, that is called
out explicitly.

Primary external reference: Roy of Superior Art Creations,
["TheDraw Fonts File (.TDF) Specifications"](http://www.roysac.com/blog/2014/04/thedraw-fonts-file-tdf-specifications/) (2014).
Secondary: [Synchronet wiki, TheDraw Fonts](http://wiki.synchro.net/custom:thedrawfonts).

---

## 1. File structure

A `.tdf` file is a **magic string followed by a chain of one or more
sub-fonts**. It is not a single font. This is the single most commonly missed
property of the format, and the reason many `.tdf` readers appear to work while
silently discarding most of their input.

```
+---------------------------------------+
| file magic (20 bytes)                 |
+---------------------------------------+
| sub-font 0: header (213) + glyph data |
+---------------------------------------+
| sub-font 1: header (213) + glyph data |
+---------------------------------------+
| ...                                   |
+---------------------------------------+
```

### 1.1 File magic

Offset 0, 20 bytes, always exactly:

```
13 54 68 65 44 72 61 77  20 46 4F 4E 54 53 20 66    .TheDraw FONTS f
69 6C 65 1A                                         ile.
```

That is `0x13`, the ASCII text `TheDraw FONTS file`, then `0x1A` (DOS EOF, so
`TYPE`ing the file on DOS stops here). Note the lowercase `f` in `file` —
several third-party descriptions capitalise it as `FONTS File`. All 1198 corpus
files use lowercase.

**Verified:** 1198/1198 files match this magic exactly.

### 1.2 The sub-font chain

The first sub-font header begins at offset 20, immediately after the magic.
Each header declares the size of its own glyph data block, and the next
sub-font's header begins immediately after that block:

```
next_header_offset = this_header_offset + 213 + blocksize
```

A reader walks the chain until the bytes at the computed offset are no longer a
valid sub-font marker, or the file ends.

**Verified:** this relation holds for every sub-font boundary in 1197 of 1198
files. The single exception is discussed in §6.1.

Sub-font counts observed in the corpus:

| sub-fonts per file | files |
|---|---|
| 1 | 642 |
| 2 | 2 |
| 3 | 8 |
| 4 | 53 |
| 5 | 269 |
| 6 | 187 |
| 7 | 22 |
| 8 | 4 |
| 9–34 | 11 |

The largest observed is 34 sub-fonts in one file. *[external]* The roysac notes
state 34 is the format's practical maximum; the corpus is consistent with that
but does not prove it, since a 35th would simply extend the chain.

---

## 2. Sub-font header

213 bytes. All offsets below are **relative to the start of the header**.

| Offset | Size | Field | Notes |
|---:|---:|---|---|
| 0 | 4 | Marker | Always `55 AA 00 FF` |
| 4 | 1 | Name length | 0–12 |
| 5 | 12 | Font name | CP437, padded with `0x00` |
| 17 | 4 | Unused | zero in 3710 of 3711 corpus sub-fonts |
| 21 | 1 | Font type | 0 = outline, 1 = block, 2 = color |
| 22 | 1 | Letter spacing | Columns inserted between glyphs |
| 23 | 2 | Block size | uint16 **little endian** |
| 25 | 188 | Glyph offset table | 94 × uint16 LE |
| 213 | — | Glyph data begins | |

### 2.1 Marker

`55 AA 00 FF`. Present at the start of every sub-font header.

Do **not** locate sub-fonts by scanning the file for this byte sequence. Glyph
data is arbitrary binary and can contain it by coincidence. Walk the chain via
`blocksize` (§1.2) instead.

### 2.2 Name

`namelen` at +4 gives the used length; the field at +5 is a fixed 12 bytes
regardless. Names longer than 12 are not representable. A robust reader clamps
`namelen` to 12 — the corpus never exceeds it, but nothing in the format
prevents a hostile file from claiming 255 and walking a reader off the field.

Encoding is CP437. Names in the corpus are plain ASCII in practice.

### 2.3 Font type

| Value | Type | Bytes per cell | Colour |
|---:|---|---:|---|
| 0 | Outline | 1 | none; indices into a line-draw table (§4.3) |
| 1 | Block | 1 | none; literal CP437 characters |
| 2 | Color | 2 | character + attribute pair |

Type distribution across the 3711 corpus sub-fonts:

| Type | `fonts/` | `unused-fonts/` | total |
|---|---:|---:|---:|
| Outline (0) | 0 | 7 | 7 |
| Block (1) | 0 | 163 | 163 |
| Color (2) | 3474 | 67 | 3541 |

The clean split is an artefact of curation, not of the format: `fonts/` was
assembled by keeping whatever the colour-only `tdfiglet` parser accepted.

### 2.4 Letter spacing

Number of blank columns a renderer inserts between adjacent glyphs. *[external]*
roysac gives the valid range as 1–41 (`0x01`–`0x29`). Corpus values are 0–4 and
overwhelmingly 1 or 2: spacing 2 in 2875 sub-fonts, 1 in 825, 0 in 7, 3 in 3,
and 4 in a single font. Note that 0 occurs, below roysac's stated minimum.

Spacing is advisory. It is not baked into the glyph bitmaps, and a renderer may
override it — this is the hook a modern renderer uses for tracking control.

### 2.5 Block size

uint16 little endian: the length in bytes of this sub-font's glyph data,
starting at header offset 213. Used to find the next sub-font (§1.2).

> **Implementation note.** `tdfiglet` read this as `(uint16_t)map[43]` — a
> single byte widened to 16 bits, discarding the high byte. Harmless while the
> field was unused, immediately fatal once sub-font chaining depends on it.
> Corpus block sizes reach 25 177, so the high byte matters for most files.

### 2.6 Glyph offset table

94 uint16 little-endian entries, one per printable ASCII character from `!`
(0x21) through `~` (0x7E), in ASCII order. Entry *i* is the **offset of that
glyph's data relative to header offset 213**.

The sentinel `0xFFFF` means *this font has no glyph for this character*. It is
common: only 299 of 3711 sub-fonts define all 94.

Space (0x20) is **not** in the table. Renderers synthesise it, typically as
`spacing` blank columns, or as a run of blanks matching the font's height.

The table is only byte-aligned within the file (header offset 25 is odd
relative to any 2-byte boundary in most files), so reading it by casting a
`uint8_t *` to `uint16_t *` is unaligned access — undefined behaviour in C, and
a real fault on strict-alignment targets. Decode it byte-by-byte.

---

## 3. Glyph data

Each glyph begins with a 2-byte header:

| Offset | Size | Field |
|---:|---:|---|
| 0 | 1 | Width in columns |
| 1 | 1 | Height in rows |
| 2 | … | Cell stream |

*[external]* TheDraw's editor limited glyphs to 30 columns × 12 rows. The corpus
respects this exactly: the largest width observed across all 3711 sub-fonts is
30 and the largest height is 12. The file format itself does not enforce it —
both fields are a full byte — so a reader must not assume 30×12 buffers are
sufficient for untrusted input.

### 3.1 Cell stream

A byte stream terminated by `0x00`, with `0x0D` (carriage return) as the row
separator:

```
<row 0 cells> 0D <row 1 cells> 0D ... <row n cells> 00
```

There is no trailing `0x0D` before the terminator. A glyph of height *h*
contains *h−1* separators.

Rows are **not** padded to the declared width. A row may be shorter than
`width`; the remaining cells are transparent. Renderers pad to a rectangle of
`width` × (font height) at load time.

The declared `height` is per-glyph. The **font height** — the row count every
glyph is padded to for a common baseline — is the maximum `height` over all
defined glyphs in the sub-font, and must be computed in a pass before any glyph
is rasterised.

### 3.2 Cell encoding by font type

**Color (type 2)** — two bytes per cell:

```
<character> <attribute>
```

`character` is CP437. `attribute` is a standard DOS text attribute byte:

```
bit  7   6   5   4   3   2   1   0
     |   +---+---+   |   +---+---+
     |       |       |       +------ foreground colour (0-15)
     |       |       +-------------- foreground intensity
     |       +---------------------- background colour (0-7)
     +------------------------------ background intensity / blink
```

i.e. low nibble = foreground 0–15, high nibble = background 0–15.

**Block (type 1)** and **Outline (type 0)** — one byte per cell, no attribute.
Colour is the renderer's choice. TheDraw drew these in the current editor
colour.

> A reader that assumes the colour layout for all three types will consume two
> bytes per cell in block and outline fonts, mistaking every second character
> for an attribute, and produce shredded output rather than an obvious error.

### 3.3 The `0x0D` ambiguity

In colour fonts, `0x0D` is only a row separator when it appears in **character**
position. A `0x0D` in attribute position is an ordinary attribute value
(black on bright-black). A decoder that scans for `0x0D` without tracking cell
phase will desynchronise. Consume strictly in pairs.

---

## 4. Colour and character mapping

### 4.1 CP437

All character bytes are IBM CP437. The line-drawing range (0xB0–0xDF) is what
gives these fonts their look: `░▒▓█▄▀` shading blocks and the box-drawing set.

Rendering on a modern terminal requires transcoding to UTF-8. Byte 0x00 in a
cell stream is the terminator, never a character; bytes 0x01–0x1F are
displayable glyphs in CP437 (`☺☻♥♦♣♠`…) but are usually replaced with blanks by
renderers, since terminals interpret them as control codes.

### 4.2 Attribute to ANSI SGR

Foreground, attribute low nibble:

| | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| colour | black | blue | green | cyan | red | magenta | brown | grey |
| SGR | 30 | 34 | 32 | 36 | 31 | 35 | 33 | 37 |

| | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|
| colour | dk grey | br blue | br green | br cyan | br red | pink | yellow | white |
| SGR | 90 | 94 | 92 | 96 | 91 | 95 | 93 | 97 |

Background, attribute high nibble. Values 0–7 map to SGR 40–47. Values 8–15 are
where implementations diverge:

- On DOS text hardware in its default mode, the high bit meant **blink**.
- TheDraw and the ANSI art scene ran displays in **iCE colour** mode, where the
  same bit selects a **bright background** instead. This is what the artists
  were looking at while drawing.

The scene-faithful mapping is therefore 8–15 → SGR 100–107.

**Verified:** 8552 cells across 12 corpus fonts set a background nibble ≥ 8
(0.05% of 16 519 441 colour cells). Values seen: 8, 12, 13. Small, but these
fonts render wrong without it.

> **Implementation note.** `tdfiglet`'s background table had **eight** entries
> and was indexed with the full four-bit nibble, so all 8552 of those cells read
> out of bounds and emitted whatever followed the array in memory. This is a
> genuine out-of-bounds read, not merely a cosmetic issue.

### 4.3 Outline font character table

Outline fonts (type 0) do not store characters. Each cell byte is a **code**
selecting a piece of line-drawing, so a single font can be re-skinned with a
different table. Codes run `@` (0x40) through `O` (0x4F).

| Code | Byte | CP437 | Char | Meaning |
|---|---:|---:|:---:|---|
| `@` | 0x40 | 32 | | leading space filler |
| `A` | 0x41 | 205 | `═` | horizontal beam, double |
| `B` | 0x42 | 196 | `─` | horizontal beam, single |
| `C` | 0x43 | 179 | `│` | vertical beam, single |
| `D` | 0x44 | 186 | `║` | vertical beam, double |
| `E` | 0x45 | 213 | `╒` | upper-left outer corner |
| `F` | 0x46 | 187 | `╗` | upper-right outer corner |
| `G` | 0x47 | 214 | `╓` | up-to-right inner corner |
| `H` | 0x48 | 191 | `┐` | right-to-down inner corner |
| `I` | 0x49 | 200 | `╚` | lower-left inner corner |
| `J` | 0x4A | 190 | `╛` | lower-right inner corner |
| `K` | 0x4B | 192 | `└` | lower-left outer corner |
| `L` | 0x4C | 189 | `╜` | lower-right outer corner |
| `M` | 0x4D | 181 | `╡` | reserved |
| `N` | 0x4E | 199 | `╟` | reserved |
| `O` | 0x4F | — | | hard space — glyph interior, renders blank |

One code outside the `@`–`O` run also appears:

| Byte | | Meaning |
|---:|---|---|
| 0x26 | `&` | descender mark — renders blank |

*[external]* Table from roysac. **Verified here** by decoding
`unused-fonts/tdfonts_org.tdf` with it: all 94 glyphs render as legible outlined
characters. `T`, `D` and `F`:

```
╒═════╗  ╒══════╗  ╒═════╗
└─┐ ╓─╜  └┐ ╓─┐ ║  │ ╓───╜
  │ ║     │ ║ │ ║  │ ╚═╗
  │ ║     │ ║ │ ║  │ ╓─╜
  │ ║    ╒╛ ╚═╛ ║  │ ║
  └─╜    └──────╜  └─╜
```

**Corrected from the external source:** roysac lists code `O` as CP437 **247**
(`≈`). Treating it as a literal 247 fills every glyph interior with `≈`, which
is plainly not the intent — 247 is a *storage* marker distinguishing "interior
of the glyph" from "outside the glyph", and it renders as blank. Compare, same
glyphs, `O` rendered literally as 247:

```
╒═════╗  ╒══════╗  ╒═════╗
└─┐≈╓─╜  └┐≈╓─┐≈║  │≈╓───╜
  │≈║     │≈║ │≈║  │≈╚═╗
```

Corpus bytes observed across every glyph of all 7 outline sub-fonts: 0x26 and
0x40–0x4F only, with 0x0D as the row separator. No outline font in the corpus
uses any other value, so the table above is complete for this corpus.

---

## 5. Rendering algorithm

```
1. Verify magic.
2. Walk the sub-font chain; select one.
3. Decode the header. Clamp namelen to 12. Read blocksize as uint16 LE.
   Copy the 94-entry offset table out byte-by-byte.
4. Pass 1 — font height:
     for each glyph offset != 0xFFFF:
         reject the offset if it does not leave 2 bytes inside the data block
         font_height = max(font_height, glyph.height)
5. Pass 2 — rasterise:
     for each surviving glyph:
         allocate width x font_height cells, filled with transparent
         walk the cell stream, tracking (row, col)
           0x0D -> row++, col = 0
           otherwise -> consume 1 byte (block/outline) or 2 (color)
                        map through the outline table if type 0
                        write, then col++
         discard cells at row >= font_height or col >= width
6. Lay out: for each output row, for each input character, emit that glyph's
   row followed by `spacing` blanks.
```

Characters with no glyph are the renderer's policy call: drop, substitute, or
error.

---

## 6. Corpus anomalies

Real-world files are not clean. Observed:

### 6.1 Trailing garbage

`fonts/guardf2.tdf` is 5303 bytes; its single sub-font ends at 5174, leaving 129
trailing bytes that are not a valid sub-font header. Chain-walking handles this
naturally: the loop stops when the marker does not match. Marker-scanning
readers may or may not, depending on the garbage.

This is the only file of 1198 whose chain does not exactly tile the file.

### 6.2 Stray header bytes

`fonts/cfh-hoer.tdf` sub-font 0 (`cFh hOEr`) has `74 00 00 00` in the four
"unused" bytes at header offset 17, where every other sub-font in the corpus has
zeroes. Treat the field as reserved-and-ignored rather than as a validity check;
rejecting files on it would reject a real font.

### 6.3 Degenerate glyphs

Glyphs with `width = 1, height = 1` and an empty cell stream are common — a
compact way to say "defined, but blank". They are legal and must not be treated
as corruption.

### 6.4 Oversized glyphs

`fonts/kevin2.tdf` glyph `!` is 30×10 and contains an artist credit
(`FONT BY STRYCHNINE`) rather than an exclamation mark. Signing a font by
hiding the tag in a rarely-used glyph slot was common practice. A renderer
should not "fix" this.

### 6.5 Hostile input

The format has no checksums, no length validation, and offsets that can point
anywhere. A reader must bounds-check every glyph offset against the declared
block size **and** the actual file length, and must clamp row/column while
walking a cell stream. A glyph header claiming 255×255 in a 40-byte block is
representable and must not be trusted.

Additionally, if the file is `mmap`ed `PROT_READ`, the offset table cannot be
edited in place to retire bad entries — copy it out first.

---

## 7. Known limits

| Property | Limit | Source |
|---|---|---|
| Glyph width | 30 columns | *[external]* editor limit |
| Glyph height | 12 rows | *[external]* editor limit |
| Font name | 12 characters | header field size |
| Glyph offset | 65534 | uint16, 0xFFFF reserved |
| Block size | 65535 bytes | uint16 |
| Sub-fonts per file | 34 | *[external]* |
| Character set | 94 (ASCII `!`–`~`) | offset table size |
| Colours | 16 fg / 16 bg | DOS attribute byte |
| Encoding | CP437 | fixed |

These limits are the motivation for the successor format described in
[TDFUTURE.md](TDFUTURE.md): no lowercase-beyond-ASCII, no Unicode, no
truecolour, no alpha, no metadata, no kerning beyond a single global spacing
value, and no way to record who drew the thing.

---

## 8. Reference implementation

`tdfiglet.c` in this repository implements this specification: all three font
types, sub-font chaining, iCE colour backgrounds, and bounds-checked parsing.

Verification performed against this document:

- 3711 of 3711 corpus sub-fonts parse and render, with no crashes, hangs, or
  empty output.
- All 3711 sub-fonts additionally render clean under AddressSanitizer and
  UndefinedBehaviorSanitizer.
- 1500 mutated font files (truncation, bit flips, forged block sizes, forged
  offset tables, forged type and spacing bytes) run clean under
  AddressSanitizer and UndefinedBehaviorSanitizer, with no crashes or hangs.
