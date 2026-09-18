"""Bounded, explicit cell-font data model; no terminal escape strings are stored."""
from __future__ import annotations
from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
import json
from typing import TypeAlias

RGBA: TypeAlias = tuple[int, int, int, int]
Color: TypeAlias = int | RGBA | None
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_CELLS = 1_000_000
MAX_CHUNKS = 100_000
MAX_INSTANCES = 4096


class FontError(ValueError):
    """Invalid, unsupported or resource-exhausting font/input; safe to report to users."""


@dataclass(frozen=True)
class Cell:
    """One cell: scalar 0 is transparent; U+0020 is an opaque space.

    Indexed colors are integers. Direct colors are straight RGBA16 tuples.
    Monochrome colors and absent backgrounds are None.
    """
    codepoint: int
    fg: Color = None
    bg: Color = None


@dataclass(frozen=True)
class Glyph:
    """Row-major rectangular glyph with signed whole-column side bearings."""
    index: int
    width: int
    height: int
    cells: tuple[Cell, ...]
    leftBearing: int = 0
    rightBearing: int = 0
    baselineOffset: int = 0
    flags: int = 0


@dataclass(frozen=True)
class Provenance:
    """Origin 0..5, an explicitly known author (empty means unknown), and year (0 unknown)."""
    origin: int
    author: str = ''
    year: int = 0


@dataclass(frozen=True)
class Chunk:
    """Opaque ancillary chunk; copy safety is the case of the fourth byte."""
    kind: bytes
    payload: bytes


@dataclass(frozen=True)
class Clut:
    """RGB16 cube, R-fastest; interpolation 0/1/2, domain 0 sRGB or 1 linear."""
    size: int
    interpolation: int
    domain: int
    entries: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True)
class Source:
    """Explicitly embedded source: 0 TTF, 1 OTF, 2 WOFF2, 3 FLF; flags=1 means baked."""
    format: int
    flags: int
    data: bytes


@dataclass
class Font:
    """Editable font. encodeFont validates every public field before serialization.

    Private snapshots support byte-preserving no-op saves. They never establish
    trust in TDFC; archival export independently re-decodes and compares it.
    """
    colorModel: int = 0
    cellFlags: int = 0
    height: int = 1
    baseline: int = 0
    tracking: int = 0
    repertoire: int = 0
    capabilities: int = 0
    glyphs: dict[int, Glyph] = field(default_factory=dict)
    cmap: dict[int, int] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)
    provenance: dict[int, Provenance] = field(default_factory=dict)
    palette: tuple[RGBA, ...] = ()
    kerning: dict[tuple[int, int], int] = field(default_factory=dict)
    kernTracking: int | None = None
    zOrder: int = 0
    zPairs: list[tuple[int, int, int]] = field(default_factory=list)
    clut: Clut | None = None
    classic: bytes | None = None
    source: Source | None = None
    extras: list[Chunk] = field(default_factory=list)
    _originalBytes: bytes | None = field(default=None, repr=False, compare=False)
    _originalState: str | None = field(default=None, repr=False, compare=False)
    _originalArt: str | None = field(default=None, repr=False, compare=False)


def isScalar(value: int) -> bool:
    """Return whether value is a Unicode scalar, including the transparent sentinel 0."""
    return isinstance(value, int) and 0 <= value <= 0x10ffff and not 0xd800 <= value <= 0xdfff


def stateDigest(value: object) -> str:
    """Hash a model deterministically, excluding private snapshots; not a signature.

    Example: stateDigest(font) changes when cells, metadata or ancillary data change.
    """
    def normalize(item):
        if is_dataclass(item):
            return {f.name: normalize(getattr(item, f.name)) for f in fields(item)
                    if not f.name.startswith('_')}
        if isinstance(item, bytes):
            return {'bytes-sha256': hashlib.sha256(item).hexdigest(), 'length': len(item)}
        if isinstance(item, dict):
            return sorted(((normalize(key), normalize(val)) for key, val in item.items()),
                          key=lambda pair: json.dumps(pair[0], sort_keys=True))
        if isinstance(item, (list, tuple)):
            return [normalize(val) for val in item]
        return item
    return hashlib.sha256(json.dumps(normalize(value), ensure_ascii=True,
                                     separators=(',', ':'), sort_keys=True).encode()).hexdigest()


def artDigest(font: Font) -> str:
    """Fingerprint render-affecting state, excluding attribution and archive snapshots."""
    return stateDigest((font.colorModel, font.cellFlags & ~8, font.height, font.baseline,
                        font.tracking, font.repertoire, font.capabilities, font.glyphs,
                        font.cmap, font.palette, font.kerning, font.kernTracking,
                        font.zOrder, font.zPairs, font.clut, font.source))
