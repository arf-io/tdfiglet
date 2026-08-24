# tdfiglet / TDFuture — HANDOFF

Last updated: 2026-08-24. Branch: `master`.

## Status: classic renderer fixed and verified; both specs written

Everything below is **verified by observation** against the 1198 `.tdf` files
in `fonts/` + `unused-fonts/`, not assumed.

### Headline result

`tdfiglet` went from **1071 renderable sub-fonts to 3711** — every sub-font in
the repository. Both numbers were measured, not estimated: the 1071 figure comes
from building the pre-change `tdfiglet.c` and running it over all 1198 files.

## What was established this session

### 1. The "extrafonts" the user means is `unused-fonts/` (moved there in b63560a)

127 files. They are **not corrupt**. Every one of the 1198 `.tdf` files in the
repo has a valid `\x13TheDraw FONTS file\x1a` magic. They are rejected by
`tdfiglet.c:loadfont()` for exactly two reasons:

- `font->fonttype != COLOR_FNT` -> hard `exit()`. Across `unused-fonts/`, the
  sub-font type census is **163 Block (0x01), 67 Color (0x02), 7 Outline (0x00)**.
- The parser reads only the **first** sub-font, at hardcoded offset 233. A single
  `.tdf` may hold many sub-fonts. Distribution in `unused-fonts/`: 121 files hold
  1 font, and single files hold 5, 11, 18, 19, 29 and 34 fonts. Those 67 Color
  sub-fonts are mostly unreachable because sub-font #1 in their file is Block.

`fonts/` contains 3474 sub-fonts, **all** type Color — i.e. the working set was
selected by "does the current parser accept it", which is why the rest look broken.

### 2. Header layout is CONFIRMED, not assumed

From a `55 AA 00 FF` marker at offset `j`:
`j+4` namelen | `j+5..j+16` name (12B) | `j+21` fonttype | `j+22` spacing |
`j+23..24` blocksize (uint16 LE) | `j+25` charlist (94 x uint16 LE) | `j+213` glyph data.

Verified by asserting `seq[i+1] == seq[i] + 213 + blocksize[i]` over every
sub-font in all 1198 files: **1197 pass**. The single failure is
`fonts/guardf2.tdf` (5303 bytes, 129 trailing bytes past the declared end) —
benign trailing garbage, glyph data itself is intact.

### 3. Glyph encodings confirmed empirically

- **Color (0x02)**: `width, height`, then cells of `(char, attribute)` pairs,
  `\r` = end of row, `\0` = end of glyph. (What tdfiglet already does.)
- **Block (0x01)**: identical but **one byte per cell, no attribute byte**.
  Verified by dumping `unused-fonts/standard.tdf` — glyphs render as clean
  ASCII art.
- **Outline (0x00)**: one byte per cell; cell bytes fall in `0x40..0x4F` plus
  `0x26`. These are indices into a CP437 line-draw table.

### 4. Outline table — MINED, not guessed, then verified by rendering

Source: <http://www.roysac.com/blog/2014/04/thedraw-fonts-file-tdf-specifications/>

    idx  '@'  A    B    C    D    E    F    G    H    I    J    K    L    M    N    O
    CP437 sp 205  196  179  186  213  187  214  191  200  190  192  189  181  199  247
    plus 0x26 ('&') = descender mark -> blank

Verified by rendering `unused-fonts/tdfonts_org.tdf` with this table: `A`, `B`,
`G`, `8` all come out as legible outlined capitals. The table is correct.

### 5. Glyph coverage across all 3474 sub-fonts in `fonts/` — the reality check

    upper  89859/90324   99.5%
    lower  90001/90324   99.6%
    digit  16077/34740   46.3%
    punct  19696/111168  17.7%
    sub-fonts with all 94 glyphs present: 166

**This kills the naive reading of "expand every font to full A-Za-z0-9+punct".**
Letters are already ~99.5% present. The real gap is digits (~54% missing) and
punctuation (~82% missing), and those glyphs are hand-drawn ANSI art. They
cannot be synthesized in-style without fabricating art — which the global
reality-test directive forbids presenting as font coverage.

## What was changed this session

### `tdfiglet.c` — rewritten parser, same CLI plus four new flags

- **Block fonts (type 1)** render. One byte per cell, no attribute byte.
- **Outline fonts (type 0)** render, via the CP437 line-draw table in §4.3 of
  the classic spec.
- **Sub-font chaining.** The parser walks the `blocksize` chain instead of
  reading offset 233 and stopping. New flags: `-L` lists a file's sub-fonts,
  `-n N` and `-f name:N` select one.
- **`-v`** warns on characters the font has no glyph for.
- **iCE colour.** Background nibbles 8-15 now emit SGR 100-107.
- **Descenders.** `0x26` followed by `0x0D`/`0x00` is a descender mark, not a
  character. Glyph rows below that mark used to be clipped (declared height
  excludes them) and the mark itself used to render as a literal `&` that
  desynchronised the colour-font byte stream. See "the descender find" below.
- **Spaces.** `' '` has no glyph (the offset table starts at `!`), so
  `tdfiglet 'HELLO WORLD'` used to print `HELLOWORLD`. A space is now
  synthesised at half the font's mean glyph width.
- **`-r`** now picks a random sub-font, not always sub-font 0.

### Bugs fixed (all five from the list below, plus two found while testing)

1. `blocksize` now a real uint16 LE read.
2. The bounds check now actually checks glyph offsets against the data window.
3. `opt.width` is an `int`.
4. `lastcolor` initialised.
5. `munmap`/`free` on error paths; `fontpath()` uses `snprintf`.
6. **Out-of-bounds read**: `bgacolors[]` had 8 entries and was indexed with the
   full 4-bit background nibble. 8552 cells across 12 fonts hit it. Table is
   now 16 entries with iCE colours.
7. **Segfault on read-only mapping**: my first cut retired bad glyph offsets by
   writing `NO_GLYPH` into `font->charlist`, which aliased the `PROT_READ`
   mmap. Found by fuzzing (108/400 crashes), not by reading. The offset table
   is now copied out byte-by-byte, which also fixes unaligned access.

### The descender find (worth not re-deriving)

Chasing why 130 glyphs draw more rows than they declare turned up an
undocumented control byte. `0x26` in **character** position, when immediately
followed by `0x0D` or `0x00`, is a **descender mark**: it ends the row, flags
it as below-baseline, and carries **no attribute byte even in colour fonts**.

The narrow "followed by `0x0D`/`0x00`" test matters — `0x26` is also a literal
ampersand and 78 corpus cells use it as one. The discriminating measurement:
parsing all 234473 corpus glyphs **with** the rule leaves zero glyphs whose row
exceeds its declared width; **without** it, 81. A wrong rule desynchronises
colour fonts (2 bytes/cell) and shows up immediately as over-wide rows.

This is documented in `docs/TDF-CLASSIC.md` §3.2. It is not in the roysac
notes, which mention `0x26` only as an outline-font "descender mark" — it is
not outline-specific.

### Verification actually performed

- **3711/3711** sub-fonts render: no crashes, hangs, errors, or empty output.
- **1058/1071** colour fonts are byte-identical to the old binary across the
  full 94-character set. All 13 differences were classified, not assumed:
  7 are colour-codes-only (the iCE fix, `[94;30m` -> `[94;100m`) and 6 are
  content changes from the descender fix, all of which restore clipped art
  (`fonts/metal.tdf` comma gains its tail; `fonts/keys.tdf` `&` stops
  rendering a stray `&` and a shifted row).
- Block/outline geometry checked structurally rather than by eye: across all
  13759 glyphs of the 170 block and outline sub-fonts, max row width equals
  declared width in 13710 cases and is never less, which is what rules out a
  2-bytes-per-cell encoding (that would give roughly half-width rows).
- **ASAN+UBSAN over the full real corpus**: 3711 sub-fonts, 0 failures.
  Re-run after the descender change; still 0.
- **ASAN+UBSAN fuzz, 1500 mutated files** (truncation, bit flips, forged
  blocksize/offset-table/type/spacing): 0 failures.

- `render-all-td-figlet-fonts.sh` runs end to end and now enumerates
  sub-fonts: a 3-file test set expanded to 19 sub-fonts, all rendering, all 19
  outputs distinct. `--no-subfonts` restores the old file-at-a-time behaviour.

Scripts kept at `$SCRATCH/sweep.sh`, `$SCRATCH/asansweep.sh`; the pre-change
binary is at `$SCRATCH/tdfiglet.orig` for re-running the comparison, where
`$SCRATCH` = `/tmp/claude-1000/-home-hedon-Augments-tdfiglet/1146162c-7709-43ad-8ff9-1e93dc378b3b/scratchpad`
(session-scoped — regenerate rather than relying on it).

### `render-all-td-figlet-fonts.sh` (untracked, left untracked)

Now expands each `.tdf` into one render target per sub-font via `-L`, so it
covers 3711 fonts instead of 1198 files. Added `--no-subfonts` to opt out,
with help text and shell completions updated to match. It writes `-i` output
to a file rather than parsing it, so the new multi-line `-i` format did not
break it — but info files now carry type/sub-font/spacing lines they did not
before.

### Docs written

- `docs/TDF-CLASSIC.md` — the classic format, every structural claim verified
  against the corpus and marked `[external]` where it is not. Includes a
  correction to the published roysac table (outline code `O` is a hard-space
  marker rendering blank, not a literal CP437 247).
- `docs/TDFUTURE.md` — draft spec for the successor format. Design only.
- `README.md` updated. `Makefile` now installs `unused-fonts/*` too.

## Original bug list (all now fixed — kept for reference)

1. `font->blocksize = (uint16_t)map[43]` reads **one byte**, not a uint16 LE.
   Harmless today (blocksize is unused); fatal the moment multi-font traversal
   depends on it.
2. The bounds check `charlist[i] + &map[233] > map + st.st_size` uses the ASCII
   `charlist` **string**, not `font->charlist` offsets. It validates nothing.
   Must be fixed before parsing files the old code rejected.
3. `opt.width` is `uint8_t` — `-w 100` is fine, `-w 300` silently wraps.
4. `printrow()` uses `lastcolor` uninitialised on the first cell (guarded by
   `i == 0`, so benign, but it is read-before-set in the general expression).
5. `loadfont()` never `munmap`s and leaks `fn` on the error paths.

## Next actions, in order

The user chose "classic fix + specs together" for session 1 and explicitly
deferred the modern feature set. They also **overrode** the initial reading on
missing glyphs: creating new ANSI art glyphs is wanted, framed as creation
rather than fabrication. The honesty requirement is *labelling*, which is why
`docs/TDFUTURE.md` §5.2 makes per-glyph provenance a structural, mandatory
part of the format rather than a convention.

1. **Author glyphs.** Digits and punctuation are the real gap (46% / 18%
   coverage). This is hand work per font family, not a batch job. Start with
   the fonts that are one or two glyphs short of complete — query the corpus
   for sub-fonts missing < 5 glyphs, they are the cheapest wins.
2. **Build the TDFuture container** (§2 of the spec) and the
   classic -> TDFuture converter with `TDFC` byte-exact round-trip. Everything
   else depends on this.
3. Static renderers: ANSI, then HTML.
4. PNG, then animation, then video.
5. Outline (TTF/OTF) import, then shaders.

Deferred and explicitly NOT done — do not let a later session assume otherwise:
TTF/OTF reading, gradients/outlines/effects, LUT and hue rotation, banner-safe
profiles as code, animated output in any format, PNG/HEIF/H.264/H.265/AV1/VP8,
the `tdfx` editing tool, FIGlet import, shaders and 3D. `docs/TDFUTURE.md` §14
carries the same list and is the authoritative copy.

## Not done: wiki and Asana

Global directives require wiki documentation and Asana tracking for work of
this scope. Neither was reachable/attempted in this session. This is an open
item, not a completed one.

## Pointers

- `tdfiglet.c` — the entire classic renderer, ~570 lines, mmap + iconv.
- `render-all-td-figlet-fonts.sh` — batch renderer, produces `tdfiglet-output/`.
- Format reference: roysac.com link above; Synchronet wiki
  <http://wiki.synchro.net/custom:thedrawfonts>.
- Untracked dirs present in the worktree: `menusamples/`, `mx.arf.io/`,
  `tdfiglet-output/`, plus the built `tdfiglet` binary.
