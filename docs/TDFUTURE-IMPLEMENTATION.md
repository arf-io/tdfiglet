# TDFuture static implementation profile 0.1

Baseline: `cdfa4e1819edc18e255248f9815c3b9badb093c8`.
The original `TDFUTURE.md` is retained as Draft 1. This addendum records concrete
implementation choices and remaining work. It is not a signed replacement
specification or a declaration of full Draft 1 conformance.

## Implemented

| Area | Behavior |
|---|---|
| Container | Signature, big-endian fields, CRC-32, first FHDR / final empty FEND, bounded lengths, duplicate validation |
| Chunk semantics | Unknown critical refusal, ancillary retention, no-op byte preservation, unsafe ancillary removal after edits |
| Glyphs | Unicode scalar CMAP, all seven color models, background flag, rectangular cells, RLE |
| Attribution | META, PROV, explicit unknown authors, generated space origin 4, raster origin 5 |
| Color | RGBA16 working values, PLTE, OKLab quantisation, OKLCh hue, linear-light straight-alpha compositing |
| Grading | CLUT nearest, trilinear and tetrahedral interpolation in sRGB or linear domain |
| Layout | Signed tracking and bearings, pair kerning, transparent cells versus opaque spaces, base/pair z-order |
| Cycles | Glyph-instance graph, deterministic topological ordering, furthest cyclic constraint removed with diagnostic |
| Classic | All three input types, descenders, extent expansion, iCE, exact TDFC archive, explicit lossy export |
| Collections | New-directory manifest, member/archive/source hashes, trailer preservation, exact whole-file restore |
| Imports | FIGlet full-width cell art; optional baked TTF/OTF half-block or braille; opt-in SRC preservation |
| Outputs | Static plain, SSH banner, MOTD, CP437 ANSI, truecolor ANSI, mIRC, HTML, PNG and resolved JSON |

## Draft ambiguities resolved narrowly

1. Chunk names normally contain four ASCII letters with the third uppercase.
   The draft's explicitly listed `SRC ` and `FX  ` are recognized exceptions.
   `SRC ` is supported only in baked mode; `FX  ` is refused.
2. `FHDR.glyph_count` and glyph indices are u16: this implementation permits
   1..65535 glyphs, not the prose claim of a million glyph indices.
3. Indexed16 cells consume one byte: foreground is the low nibble, background
   the high nibble. With no background flag, the high nibble must be zero.
4. Palette entries are RGBA16. Indexed16 defaults to DOS order; indexed256
   defaults to xterm order. A supplied palette overrides those defaults.
5. KERN tracking overrides FHDR default tracking, not adds to it. Between two
   glyphs, gap is tracking + right bearing + left bearing + pair adjustment.
6. Baseline offset is an additive row displacement. The final canvas includes
   negative offsets without clipping. First/last exterior bearings do not add
   canvas padding. Ambiguous terminal-width characters use the terminal's
   usual one-column interpretation; wide and combining characters are refused
   as cell art through explicit replacement diagnostics.
7. RGB8/RGBA8 model values must be exact multiples of 257 in the RGBA16 model.
   Writers refuse precision loss; callers can choose RGB16/RGBA16 explicitly.
8. In cell overlap the front codepoint wins, with foreground and background
   colors composited separately in linear light. Empty canvas is black.
   Raster outputs in this release are 8-bit RGB, not high-bit-depth RGBA.
9. TDFC contains exactly the 213-byte classic header (including marker) and its
   declared glyph block. Whole-file magic, multiple subfonts and trailing data
   belong to the converter's manifest collection, not a new `.tdfxc` format.
10. An unchanged decoded TDFuture file is saved byte-for-byte. After edits,
    canonical chunks are serialized and unknown unsafe-to-copy chunks dropped.
    Render-affecting edits drop TDFC; exact classic export also independently
    compares the embedded source so a malicious stale archive cannot pass.

These decisions are documented here to make interoperability review possible.
They must not be mistaken for wire details already settled in Draft 1.

## Explicitly deferred or rejected

- Per-cell depth and layer fields: their byte widths/encodings are not defined.
- Grapheme cluster CMAP extension: no complete binary grammar is defined.
- ANIM: target encodings, scope payloads and typed keyframe values are incomplete.
  No animation player or binary animation writer is provided.
- PROF storage: no payload grammar is defined. CLI output profiles are enforced
  independently and do not pretend to read a stored PROF chunk.
- FX, SHDR, 3D scenes and shader language: not implemented; never executed.
- FIGlet smushing/fitting and RTL shaping: the proposed KERN flag is not actually
  specified in the KERN structure. Full-width degradation is explicitly gated.
- Video/HEIF, 16-bit raster output, font-editing UI, .cube file importer, live
  outline rasterisation, WOFF2 conversion, automatic punctuation/digit authoring.
- Terminal OSC palette querying, bidi layout, wide-cell/grapheme rendering,
  terminal-specific braille/octant fallback negotiation.

Unsupported critical chunks and capability flags fail explicitly. They are not
silently skipped or reported as implemented. Existing spec and classic C source
remain intact for further reviewed work.

## Limits and provenance

32 MiB input/output font limit; one million decoded cells; one million CMAP
mappings; 100000 chunks; 4096 classic subfonts; 4096 text instances. Pairwise
z-order is limited to 512 instances per line and 8192 distinct graph edges.
Raster output is limited to 32 million pixels. Width/height fields remain u8.
Classic spacing above signed i8 range is refused on import, not wrapped.

The classic converter rejects malformed streams rather than copying uncertain
rendered interpretations. Its byte-exact restoration claim applies to accepted
inputs, not every damaged historical file. Corpus-wide conversion has not been
claimed from synthetic tests. Authorship labels are assertions, not signatures.
Generated synthetic spacing never silently becomes the historical artist's work.

## Validation

Run `make test` or `python3 -m unittest discover -s tests -v`. Tests generate their
fixtures in memory. Optional outline tests use `TDFX_TEST_OUTLINE` or a local
DejaVu Sans Mono installation; no font binary is included. `make` enables a
separate smoke test for the unchanged C executable across all three classic types.
CI builds that executable before running the suite on Linux and macOS.
