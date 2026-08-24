# TDFuture — Specification, Draft 1

**File extension:** `.tdfx`  **Media type:** `application/vnd.tdfuture`
**Status:** DRAFT. This is a design document. Nothing in this file is
implemented yet — see §14 for what exists today and what does not.

TDFuture is a successor to TheDraw's `.TDF` font format
([TDF-CLASSIC.md](TDF-CLASSIC.md)). It keeps what made TDF good — a font is a
grid of coloured character cells, drawn by a human, that renders into a
terminal — and removes the 1990 constraints: 16 colours, 94 ASCII slots, CP437
only, one global letter-spacing value, no metadata, no alpha, no motion.

## Design principles

1. **The cell grid is sacred.** TDFuture glyphs are still made of character
   cells. This is not a vector format wearing a costume. The aesthetic comes
   from the constraint.
2. **Lossless round-trip with classic TDF.** Every classic `.tdf` sub-font
   converts to TDFuture and back byte-identically. §12.
3. **Credit is structural.** The format records who drew each glyph. A glyph
   authored in 2026 and a glyph drawn by an artist in 1994 are distinguishable
   by any reader, forever, without convention or trust. §5.
4. **Degrade, don't fail.** A truecolour animated font must render acceptably
   as static 16-colour ASCII on a VT100. Renderers negotiate down; files
   declare what they need. §10.
5. **Unknown chunks are skippable.** Chunk criticality is encoded in the name,
   PNG-style, so the format can grow without breaking old readers. §2.2.

---

## 1. Container

```
+--------------------------------+
| signature (16 bytes)           |
+--------------------------------+
| chunk: FHDR  (must be first)   |
+--------------------------------+
| chunk: ...                     |
+--------------------------------+
| chunk: FEND  (must be last)    |
+--------------------------------+
```

### 1.1 Signature

16 bytes, fixed:

```
13 54 44 46 75 74 75 72  65 1A 0D 0A 1A 0A 00 01
   T  D  F  u  t  u  r    e
```

- `0x13` and `TDFuture` echo the classic magic, so `file(1)` heuristics and a
  human `hexdump` both recognise the lineage.
- `0x1A 0D 0A 1A 0A` is the PNG-style transfer trap: it catches CRLF↔LF
  mangling and DOS EOF truncation.
- `00 01` is the container version, big-endian. This document specifies
  version 1.

### 1.2 Byte order

**All multi-byte integers in TDFuture are big-endian**, unlike classic TDF.
This is deliberate: it makes the format hostile to the "cast a pointer and
hope" parsing that produced the alignment and endianness bugs catalogued in
TDF-CLASSIC §2.5 and §2.6. Every field must be decoded byte-by-byte.

---

## 2. Chunks

### 2.1 Layout

```
| length (u32) | type (4 bytes) | payload (length bytes) | crc32 (u32) |
```

- `length` counts payload bytes only. Maximum 2³¹−1.
- `type` is four ASCII letters.
- `crc32` is CRC-32/ISO-HDLC over `type` + `payload`, not over `length`.

### 2.2 Criticality and safety, encoded in the name

Each of the four letters' case carries meaning:

| Letter | Uppercase | Lowercase |
|---|---|---|
| 1 | **Critical** — a reader that does not understand it must refuse the file | **Ancillary** — safe to skip |
| 2 | **Public** — defined by this spec | **Private** — application-specific |
| 3 | reserved, must be uppercase in version 1 | — |
| 4 | **Unsafe to copy** — an editor that modifies the font must drop it | **Safe to copy** — may be preserved blindly |

So `FHDR` is critical/public/copy-unsafe; `meta` would be ancillary and safely
copyable. This is PNG's scheme and it is adopted wholesale because it solved
exactly this problem once already.

### 2.3 Chunk types in version 1

| Type | Required | Purpose |
|---|---|---|
| `FHDR` | yes, first | Font header: geometry, colour model, capabilities |
| `CMAP` | yes | Unicode codepoint → glyph index |
| `GLPH` | yes, ≥1 | One glyph's cell data |
| `META` | no | Authorship, licensing, provenance (§5) |
| `PROV` | no | Per-glyph provenance (§5.2) |
| `PLTE` | no | Palette, for indexed colour modes |
| `CLUT` | no | Colour lookup table / grade (§6.3) |
| `KERN` | no | Kerning and tracking (§7) |
| `ZORD` | no | Glyph stacking order (§8) |
| `ANIM` | no | Animation tracks (§9) |
| `PROF` | no | Render profile constraints (§10) |
| `SRC ` | no | Embedded source outline font (§11.1) |
| `FX  ` | no | Declarative effects applied to rasterised outlines (§11.2) |
| `SHDR` | no | Shader and 3D scene description (§13) |
| `TDFC` | no | Verbatim classic TDF sub-font, for round-trip (§12) |
| `FEND` | yes, last | End of font; zero-length payload |

A `.tdfx` file holds exactly one font. The classic format's sub-font chaining
is replaced by ordinary files in an ordinary directory, or by a `.tdfxc`
collection (§1.3 of a future revision — not specified here).

---

## 3. `FHDR` — font header

| Offset | Size | Field |
|---:|---:|---|
| 0 | 1 | Colour model (§3.1) |
| 1 | 1 | Cell flags (§3.2) |
| 2 | 1 | Nominal cell height, rows |
| 3 | 1 | Baseline row |
| 4 | 2 | Glyph count |
| 6 | 1 | Default tracking, signed, columns |
| 7 | 1 | Repertoire hint (§3.3) |
| 8 | 4 | Capability flags (§3.4) |

### 3.1 Colour model

| Value | Model | Bytes/colour | Notes |
|---:|---|---:|---|
| 0 | None | 0 | Monochrome. Classic block/outline fonts. |
| 1 | Indexed 16 | ½ | Classic DOS attribute. Round-trips to TDF. |
| 2 | Indexed 256 | 1 | xterm-256 |
| 3 | RGB8 | 3 | 24-bit truecolour |
| 4 | RGBA8 | 4 | truecolour + alpha |
| 5 | RGB16 | 6 | 16 bits per channel |
| 6 | RGBA16 | 8 | 16 bits per channel + alpha |

Models 5 and 6 exist because effects compose. Gradients, hue rotation, and
colour morphing applied in sequence to 8-bit channels band visibly; 16-bit
working precision does not. Terminals cannot display it — but the PNG, HEIF,
and video outputs can, and the intermediate arithmetic always benefits.

Alpha is straight (non-premultiplied), 0 = fully transparent.

### 3.2 Cell flags

| Bit | Meaning |
|---:|---|
| 0 | Cells carry a background colour as well as a foreground |
| 1 | Cells carry a per-cell depth value (§13.2) |
| 2 | Cells carry a per-cell layer index (§8.3) |
| 3 | Glyph rows are run-length encoded (§4.3) |
| 4–7 | Reserved, must be zero |

### 3.3 Repertoire hint

Which character repertoire the glyph *art* is drawn from — not the encoding of
the file, which is always Unicode.

| Value | Repertoire |
|---:|---|
| 0 | Unspecified |
| 1 | CP437 (IBM PC / DOS) |
| 2 | CP850 |
| 3 | Amiga Topaz |
| 4 | PETSCII (Commodore) |
| 5 | ATASCII (Atari) |
| 6 | Unicode block elements + box drawing |
| 7 | Unicode braille patterns (§6.5) |
| 8 | Plain 7-bit ASCII |

This drives fallback. A font drawn in CP437 half-blocks rendered on a terminal
with no CP437 coverage can be remapped to the Unicode block elements; a font
drawn in braille cannot be meaningfully remapped to ASCII, and the renderer
should say so rather than emit rubbish.

### 3.4 Capability flags

A bitfield of what a renderer must support to render this font *as intended*.
A renderer that cannot must either degrade explicitly (§10) or refuse.

| Bit | Capability |
|---:|---|
| 0 | Truecolour required |
| 1 | Alpha compositing required |
| 2 | Animation present |
| 3 | Negative kerning / glyph overlap present |
| 4 | Non-trivial z-order present |
| 5 | Shader required |
| 6 | Non-BMP codepoints present |
| 7 | Grapheme clusters wider than one codepoint present |

---

## 4. Glyphs

### 4.1 `CMAP`

A sorted list of ranges mapping Unicode scalar values to glyph indices:

```
| range count (u16) |
| start (u32) | end (u32) | first glyph index (u16) |   x count
```

Ranges are inclusive and must be sorted ascending and non-overlapping, so a
lookup is a binary search. Codepoint `start + n` maps to glyph
`first_glyph_index + n`.

This replaces the classic format's fixed 94-slot ASCII table. A TDFuture font
may define one glyph or a million, anywhere in Unicode.

**Grapheme clusters.** Where a font needs a glyph for a multi-codepoint cluster
(a flag, an emoji ZWJ sequence, a combining sequence), it is declared through a
`CMAP` extension range with a cluster table. Capability bit 7 signals this.
Fonts that do not need it pay nothing.

### 4.2 `GLPH`

| Offset | Size | Field |
|---:|---:|---|
| 0 | 2 | Glyph index |
| 2 | 1 | Width, columns |
| 3 | 1 | Height, rows |
| 4 | 1 | Left side bearing, signed |
| 5 | 1 | Right side bearing, signed |
| 6 | 1 | Baseline offset, signed |
| 7 | 1 | Flags |
| 8 | … | Cell data |

Cells are stored row-major, **exactly `width × height` cells, no separators**.
The classic format's `0x0D`-separated ragged rows and `0x00` terminator are
gone: they made the stream self-desynchronising (TDF-CLASSIC §3.3) and saved
almost nothing.

Each cell is:

```
| codepoint (u32) | fg (per colour model) | [bg] | [depth] | [layer] |
```

Codepoint `0x00000000` means **transparent** — not "space". A space is
`U+0020` and is opaque: it paints its background and occludes glyphs behind it.
The distinction does not exist in classic TDF and is what makes overlap (§7.2)
and layering (§8) work.

### 4.3 Run-length encoding

When `FHDR` cell flag 3 is set, cell data is RLE'd. A run is:

```
| count (u8, 1-255) | cell |
```

Set the flag per font, not per glyph, so a decoder has one path. Given that
most glyph area in these fonts is transparent or flat colour, RLE typically
halves file size; it is optional because for tiny glyphs it does not.

---

## 5. Provenance — `META` and `PROV`

This is the chunk that would not have existed in 1994, and it is the one this
format most needs.

These fonts are art. Most of the classic corpus is unsigned, or signed by
hiding a tag inside a rarely-used glyph — `fonts/kevin2.tdf` puts
`FONT BY STRYCHNINE` in its exclamation mark (TDF-CLASSIC §6.4). That was the
only mechanism available. It should not be the mechanism any more.

### 5.1 `META`

A list of key/value pairs, keys from a registered set, values UTF-8:

```
| pair count (u16) |
| key length (u8) | key | value length (u16) | value |   x count
```

Registered keys:

| Key | Meaning |
|---|---|
| `title` | Font name, unlimited length (classic TDF capped at 12) |
| `author` | Who drew it |
| `group` | Demoscene/art group affiliation |
| `year` | Year of creation, or range |
| `license` | SPDX identifier where applicable |
| `source` | Where this font came from |
| `derived-from` | The font this was derived from |
| `description` | Free text |
| `charset-note` | Notes on repertoire and fallback |
| `tool` | What produced this file |

### 5.2 `PROV` — per-glyph provenance

```
| entry count (u16) |
| glyph index (u16) | origin (u8) | author length (u8) | author | year (u16) |
```

`origin`:

| Value | Meaning |
|---:|---|
| 0 | Original — drawn by the font's author as part of the original font |
| 1 | Authored — drawn later, by hand, by someone extending the font |
| 2 | Adapted — derived by hand from another glyph in this font |
| 3 | Borrowed — copied from a different font, `author` names it |
| 4 | Generated — produced by an algorithm or a model, not drawn by hand |
| 5 | Rasterised — produced from an outline font (§11) |

**A reader must be able to answer "who drew this letter" for any glyph.** A
tool that adds glyphs to a font and does not write `PROV` is
non-conforming.

This is not bookkeeping for its own sake. Classic TDF fonts define digits in
46% of sub-fonts and punctuation in 18% (TDF-CLASSIC corpus figures). Filling
those gaps is worth doing — a font you cannot type a `@` in is a font you
cannot use — and new glyphs drawn to match a 1994 font's style are real work
and real art. What must never happen is that they become silently
indistinguishable from the original artist's hand. `origin` keeps both facts
true at once: the font is complete, *and* the record is honest.

---

## 6. Colour

### 6.1 `PLTE`

For colour models 1 and 2. A flat array of RGBA16 entries — the palette is
always stored at full precision regardless of the font's colour model, so
palette-based fonts can be re-graded losslessly.

### 6.2 Hue rotation

Not stored; a render-time operation, parameterised in `ANIM` (§9) or on the
command line. Defined precisely so every implementation agrees:

1. Convert each channel to linear light (undo sRGB transfer).
2. Convert to OKLCh.
3. Add θ to `h`, modulo 360°.
4. Convert back; re-apply the transfer function.

**OKLCh, not HSV.** HSV hue rotation changes perceived lightness — rotating a
saturated blue to yellow makes it visibly brighter, so an animated hue cycle
pulses in brightness even at constant "value". OKLCh is perceptually uniform,
so a hue cycle reads as a pure colour sweep. This matters more here than in
most graphics work, because these palettes are heavily saturated.

Alpha is never modified by hue rotation.

### 6.3 `CLUT` — colour grading

A 3D lookup table applied to final RGB, before alpha compositing:

| Offset | Size | Field |
|---:|---:|---|
| 0 | 1 | Cube size N per axis (2–64) |
| 1 | 1 | Interpolation: 0 nearest, 1 trilinear, 2 tetrahedral |
| 2 | 1 | Domain: 0 sRGB, 1 linear |
| 3 | … | N³ RGB16 entries, R fastest |

This is the `.cube` LUT model from colour grading, and `.cube` files convert
directly. It gives one mechanism for "make this font look like a different
palette" — a CGA grade, a Commodore grade, a monochrome amber grade — without
touching glyph data.

For indexed fonts a 1D palette remap is the degenerate case and is cheaper;
readers should special-case it.

### 6.4 Terminal quantisation

When the target terminal supports fewer colours than the font, quantise in
OKLab, not RGB — nearest-neighbour in RGB picks visibly wrong colours at the
16-colour level. The 16-colour target palette is not fixed by this spec, since
terminal themes vary; renderers should use the terminal's actual palette when
it can be queried (OSC 4) and a documented default otherwise.

### 6.5 Sub-cell resolution

A cell is one character, but a character can carry sub-cell detail. Three
ladders, in increasing resolution and decreasing terminal compatibility:

| Repertoire | Sub-cell grid | Notes |
|---|---|---|
| Half blocks `▀▄` | 1×2 | Universal. fg/bg gives 2 independent pixels per cell |
| Quadrants `▘▝▖▗` | 2×2 | Wide support |
| Sextants | 2×3 | Unicode 13, patchy fonts |
| Octants | 2×4 | Unicode 16, rare |
| Braille `⠀-⣿` | 2×4 | Excellent coverage, but monochrome per cell |

Declared via the repertoire hint (§3.3) so a renderer can pick the best ladder
the target supports and degrade down it.

---

## 7. `KERN` — spacing

Classic TDF had one global spacing byte. TDFuture has three levels.

### 7.1 Structure

```
| tracking (i8) |                      global, added to every gap
| pair count (u16) |
| left codepoint (u32) | right codepoint (u32) | adjust (i8) |   x count
```

Plus per-glyph side bearings in `GLPH` (§4.2). Effective gap between glyphs
*a* and *b*:

```
gap = tracking + rsb(a) + lsb(b) + pair_adjust(a, b)
```

All values are in whole columns. There are no fractional columns; this is a
character grid.

### 7.2 Negative kerning and overlap

`gap` may be negative: glyphs overlap. This is the interesting case and it is
why transparency (§4.2) and z-order (§8) exist.

When two glyphs overlap, each contested cell is resolved by:

1. If exactly one glyph's cell is transparent (codepoint 0), the other wins.
2. Otherwise, the glyph that is **in front** per §8 wins.
3. If the front glyph's cell has alpha < 1, it composites over the back one.

Overlap is how you get script fonts whose letters actually connect, and how you
get the layered chrome-and-shadow look without baking it into the glyphs.

---

## 8. `ZORD` — stacking order

Which glyph is in front when glyphs overlap.

### 8.1 Base order

```
| base order (u8) |
```

| Value | Order |
|---:|---|
| 0 | Left-to-right: later characters in front |
| 1 | Right-to-left: earlier characters in front |
| 2 | Centre-out: middle of the string in front |
| 3 | Centre-in: middle of the string behind |
| 4 | Explicit only: no default, every pair must be declared |

### 8.2 Pair overrides

```
| pair count (u16) |
| char A (u32) | char B (u32) | relation (u8) |   x count
```

`relation`: 0 = A behind B, 1 = A in front of B.

This is the "`a` goes behind `b` but in front of `c`" matrix. It is a set of
pairwise constraints over the *characters* of the string being rendered.

**Cycles.** Pairwise constraints can contradict: `a` in front of `b`, `b` in
front of `c`, `c` in front of `a` is expressible and has no consistent
front-to-back ordering. A renderer must:

1. Build a directed graph over the glyph instances in the string.
2. Topologically sort it.
3. If a cycle is detected, break it by discarding the constraint whose two
   glyphs are furthest apart in the string, and repeat.
4. Emit a diagnostic naming the discarded constraints.

Renderers must not silently produce arbitrary order, and must not fail the
render. A cyclic z-order is a font bug the *font author* needs to see.

Note the ordering is over glyph *instances*, not characters: in `abca`, the two
`a`s are separate nodes, and a constraint on `(a, c)` applies to both.

### 8.3 Layers

For explicit control independent of string position, cells may carry a layer
index (`FHDR` cell flag 2). Higher layer wins, and layer is checked before any
§8.1/§8.2 ordering. This is the escape hatch for a glyph that must always be
behind everything — a drop shadow drawn as part of the glyph.

---

## 9. `ANIM` — animation

```
| frame count (u16) |
| frame rate, fps x256 (u16) |
| loop mode (u8) |       0 once, 1 loop, 2 ping-pong
| track count (u16) |
| track ...             |
```

### 9.1 Tracks

```
| track type (u8) | target (u8) | scope length (u16) | scope | keyframes |
```

`target` selects what the track drives: whole font, one glyph index, one
character position in the string, or one layer.

| Type | Track | Parameters |
|---:|---|---|
| 0 | Hue rotate | start θ, end θ, direction, revolutions |
| 1 | Colour morph | source colour, destination colour |
| 2 | Palette cycle | palette index range, step per frame |
| 3 | Reveal | per-character or per-cell, order, frames per unit |
| 4 | Alpha fade | start alpha, end alpha |
| 5 | Translate | start offset, end offset, in cells |
| 6 | Layer shuffle | permutation over frames |
| 7 | Shader uniform | uniform name, start value, end value |

### 9.2 Keyframes and easing

```
| keyframe count (u16) |
| frame (u16) | easing (u8) | value ... |   x count
```

Values are interpolated between keyframes with the easing function named on the
*earlier* keyframe of the pair.

| Value | Easing |
|---:|---|
| 0 | Step (no interpolation) |
| 1 | Linear |
| 2 | Ease-in (cubic) |
| 3 | Ease-out (cubic) |
| 4 | Ease-in-out (cubic) |
| 5 | Ease-in-out (sine) |
| 6 | Back (overshoot) |
| 7 | Elastic |
| 8 | Bounce |
| 9 | Cubic Bézier, four f16 control values follow |

Colour interpolation for track types 0, 1 and 4 is in **OKLab** (or OKLCh for
hue-rotate), never in sRGB. Interpolating sRGB from red to green passes through
a muddy dark olive; OKLab passes through a clean yellow. For a format whose
entire purpose is colour movement, this is the difference between looking like
2026 and looking like a 1996 GIF.

Alpha interpolates linearly, separately, always.

### 9.3 Reveal

Track type 3 is the "typewriter" effect, generalised.

| Order | Meaning |
|---:|---|
| 0 | Left to right, one character at a time |
| 1 | Right to left |
| 2 | Centre out |
| 3 | Random, seeded — the seed is stored so the animation is reproducible |
| 4 | By cell, left-to-right top-to-bottom, ignoring character boundaries |
| 5 | By cell, scanline dissolve, seeded |
| 6 | By layer |

The stored seed matters: an animation that renders differently each time cannot
be encoded to a video file that matches the terminal preview.

---

## 10. `PROF` — render profiles

Declares which output profiles a font is *known* to survive. Renderers enforce
the profile regardless; this chunk is the font author's assertion, useful for
tooling and for warning at edit time.

| Profile | Constraints |
|---|---|
| `ssh-banner` | 7-bit ASCII only. **No escape sequences of any kind.** No CR. ≤ 80 columns, ≤ 24 rows. No animation. |
| `motd` | UTF-8 permitted. SGR colour permitted. No cursor movement, no clear-screen, no scroll region. No animation. |
| `irc` | mIRC colour codes, ≤ 16 colours, ≤ 400 bytes per line. |
| `ansi-classic` | CP437 + SGR 30–47/90–107. iCE backgrounds permitted. |
| `ansi-truecolor` | SGR 38;2/48;2. |
| `html` | Self-contained, inline styles, no external resources. |
| `raster` | PNG/HEIF/video. No terminal constraints. |

### 10.1 Why `ssh-banner` is strict

An SSH banner is displayed by the client **before authentication**, from a host
the user has not yet verified. If the banner may contain escape sequences, a
hostile or compromised server can move the cursor, rewrite text the user
already read, alter the terminal title, switch character sets, or on some
terminals trigger a response the shell will later execute. This is a real class
of terminal injection.

Therefore the `ssh-banner` profile forbids **all** bytes outside
`0x20`–`0x7E`, plus `\n`. Not "escapes we think are safe" — all of them. A
colour font rendered to this profile loses its colour; that is the correct
outcome, and the renderer must say so rather than quietly emitting escapes.

`motd` is displayed post-authentication on a host the user has already
authenticated to, so colour is acceptable there; cursor movement still is not,
because the MOTD shares the screen with the login sequence.

---

## 11. Outline fonts as input

### 11.1 `SRC ` — embedded source

```
| format (u8) |          0 TTF, 1 OTF, 2 WOFF2, 3 FIGlet .flf
| flags (u8) |           bit 0: glyphs in this file are baked from this source
| size (u32) |
| data |
```

Two modes:

- **Baked** — glyphs are materialised into `GLPH` chunks at build time.
  `SRC ` is retained for provenance and re-baking at a different size. This is
  the normal case: the result is a real cell font that renders anywhere.
- **Live** — the renderer rasterises at render time. Higher fidelity, but the
  font is only usable by renderers with a rasteriser.

Rasterised glyphs must carry `PROV` origin 5 (§5.2).

Embedding a licensed typeface redistributes it. The `license` metadata key is
not decoration.

### 11.2 `FX  ` — declarative effects

Effects applied to a rasterised outline before it is quantised to cells. An
ordered list; order is significant.

| Effect | Parameters |
|---|---|
| Gradient | linear/radial/conic, angle, stops (position + RGBA16) |
| Outline | width in sub-cells, colour, inside/outside/centre |
| Shadow | offset, blur, colour, alpha |
| Bevel | light angle, depth, highlight and shadow colours |
| Emboss | angle, depth |
| Glow | radius, colour, inner/outer |
| Scanlines | period, intensity, phase |
| Dither | ordered/Floyd–Steinberg/blue-noise, target palette |
| Noise | amount, seed, monochrome/colour |
| Chromatic aberration | offset per channel |

Rasterisation happens at a sub-cell resolution (§6.5) chosen by the target
repertoire, effects are applied at that resolution in linear light, and the
result is then reduced to cells. Doing it in that order is what keeps a
gradient smooth across a glyph instead of banding to one colour per cell.

### 11.3 FIGlet import

FIGlet `.flf` fonts map onto TDFuture directly:

| FIGlet | TDFuture |
|---|---|
| Character height | `FHDR` cell height |
| Baseline | `FHDR` baseline row |
| Max length / hardblank | Glyph width; hardblank → `U+0020` opaque, ordinary blank → codepoint 0 transparent |
| Old layout / full layout smushing | `KERN` negative pair values + `ZORD` |
| Right-to-left flag | `ZORD` base order 1 |
| Codetag characters | `CMAP` ranges |

FIGlet's *smushing* — where adjacent characters merge and the overlapping
column is replaced by a combined character — is the one thing that does not map
cleanly, because TDFuture resolves overlap by occlusion (§7.2), not by
substitution. Smushing is therefore preserved as a `KERN` flag with the
original smush ruleset recorded, and a conforming renderer that imports FIGlet
applies the FIGlet rules. Fonts authored natively in TDFuture should not use
it.

The hardblank distinction is the important half, and it is exactly the
opaque-space-versus-transparent distinction TDFuture already needed for §7.2.
FIGlet got there first, in 1991.

---

## 12. `TDFC` — classic round-trip

Holds one classic TDF sub-font verbatim: the 213-byte header and its glyph
data block, exactly as they appeared in the source `.tdf`.

A converter writing TDFuture from classic TDF must emit `TDFC`. A converter
writing classic TDF from TDFuture must, if `TDFC` is present and no glyph has
been modified since, emit those bytes unchanged. This makes the round-trip
byte-exact rather than merely equivalent, which matters for an archival corpus
where files are historical artefacts.

Down-conversion from a TDFuture font that has been edited is lossy by
construction — truecolour, alpha, Unicode beyond the 94 ASCII slots, animation,
kerning, and z-order have no representation in the classic format. A converter
must report exactly what it dropped.

---

## 13. `SHDR` — shaders and 3D

The most speculative part of this spec. It is included because "the glyphs are
cells in a grid" and "the glyphs are textured quads in a 3D scene" are
compatible ideas, and the second one is what a 1994 demo would have done if it
could.

### 13.1 Model

Each cell is a unit quad on the XY plane. A string is laid out in the grid, then
transformed. Rendering happens off-grid at sub-cell resolution and is
re-quantised to cells for terminal output, or emitted directly for raster
output.

Coordinate system: right-handed, +X right, +Y up, +Z toward the viewer. One
unit = one cell width. Cell aspect ratio is declared (terminal cells are
roughly 1:2) so rotations do not shear.

### 13.2 Depth

With `FHDR` cell flag 1, each cell carries a signed depth in cell units,
extruding the glyph into Z. This gives real 3D letterforms from a 2D cell font
— the depth channel is a heightmap. Combined with a light direction it produces
shading that the classic block fonts faked by hand.

### 13.3 Scene

```
| camera type (u8) |         0 orthographic, 1 perspective
| fov (f16) | near (f16) | far (f16) |
| eye (3 x f16) | target (3 x f16) | up (3 x f16) |
| light count (u8) | lights |
| stage count (u8) | stages |
```

Camera parameters are animatable via `ANIM` track type 7.

### 13.4 Shader stages

A shader stage is a program in a **restricted, non-Turing-complete** language:
fixed loop bounds, no recursion, no unbounded memory, a fixed instruction
budget per invocation. This is a hard requirement, not a simplification. A font
file is untrusted data that arrives over the network and gets rendered by an
SSH banner. Arbitrary code execution in that position is a catastrophe, and
"we'll sandbox it later" is how that catastrophe happens.

Stage inputs: cell position, UV within cell, depth, colour, time/frame, and
declared uniforms. Output: RGBA16 plus an optional codepoint override, so a
shader can change *which character* a cell uses — selecting from a shading
ramp by luminance. That is the one shader capability with no equivalent in
conventional graphics, and it is the most characteristically ANSI thing in this
document.

---

## 14. Implementation status

Nothing in this document is implemented. To be explicit about what exists in
this repository today:

**Exists and is verified:**

- A complete, tested reader for the **classic** TDF format — all three font
  types, sub-font chaining, iCE colour, bounds-checked. `tdfiglet.c`.
- [TDF-CLASSIC.md](TDF-CLASSIC.md), verified against 3711 sub-fonts.
- This design document.

**Does not exist:**

- Any TDFuture reader or writer. No `.tdfx` file has ever been written.
- The `tdfx` editing tool.
- TTF/OTF rasterisation, `FX`, gradients, outlines.
- FIGlet import.
- Animation, and every raster/video output (PNG, HEIF, H.264, H.265, AV1, VP8).
- Shaders and the 3D pipeline.
- Any glyph authored to fill the digit and punctuation gaps.

The order these should be built in is roughly: container and codec →
classic→TDFuture converter with `TDFC` round-trip → static ANSI and HTML
renderers → PNG → glyph authoring tooling → animation → video → outline import
→ shaders. Each earlier item is a dependency of, or a test harness for, the
next.

---

## Appendix A: what was left out, and why

**Fractional cell positioning.** Sub-cell glyph offsets were considered and
rejected. The grid is the aesthetic. Once glyphs can sit at arbitrary
positions, the format is a bad vector format instead of a good cell format.

**Scripting.** No general-purpose embedded language, for the reasons in §13.4.

**Per-cell fonts.** Letting individual cells specify a different typeface would
make terminal rendering unpredictable and raster rendering a font-management
problem. The repertoire hint (§3.3) covers the real need.

**Compression of the whole file.** Individual chunks may be RLE'd (§4.3);
whole-file compression is left to the transport. These files are small.

**Right-to-left and vertical text.** Deliberately deferred, not dismissed.
Doing it properly means the bidi algorithm and vertical layout, and doing it
badly is worse than not doing it. The `ZORD` base order already has the
right-to-left case for the shallow version.
