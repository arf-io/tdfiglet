"""sRGB/linear-light/OKLab operations and deterministic, theme-independent palettes."""
from __future__ import annotations
from functools import lru_cache
import math
from .model import RGBA, Clut, FontError

DOS_RGB = ((0,0,0),(0,0,170),(0,170,0),(0,170,170),(170,0,0),(170,0,170),
           (170,85,0),(170,170,170),(85,85,85),(85,85,255),(85,255,85),
           (85,255,255),(255,85,85),(255,85,255),(255,255,85),(255,255,255))
DOS_PALETTE = tuple((r*257,g*257,b*257,65535) for r,g,b in DOS_RGB)
ANSI_TO_DOS = (0,4,2,6,1,5,3,7,8,12,10,14,9,13,11,15)
XTERM_PALETTE = (tuple(DOS_PALETTE[i] for i in ANSI_TO_DOS)
                + tuple((r*257,g*257,b*257,65535)
                        for r in (0,95,135,175,215,255)
                        for g in (0,95,135,175,215,255)
                        for b in (0,95,135,175,215,255))
                + tuple(((8+10*i)*257,)*3+(65535,) for i in range(24)))
BLACK = (0,0,0,65535)
WHITE = (65535,65535,65535,65535)


def linearize(value: float) -> float:
    """Decode an sRGB channel to linear light."""
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def delinearize(value: float) -> float:
    """Encode a linear-light channel to sRGB (gamut clipping is caller-controlled)."""
    return 12.92 * value if value <= .0031308 else 1.055 * value ** (1/2.4) - .055


def toOklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert unit sRGB channels to OKLab, using signed cube roots."""
    r,g,b = map(linearize, rgb)
    l = .4122214708*r + .5363325363*g + .0514459929*b
    m = .2119034982*r + .6806995451*g + .1073969566*b
    s = .0883024619*r + .2817188376*g + .6299787005*b
    l,m,s = (math.copysign(abs(v)**(1/3),v) for v in (l,m,s))
    return (.2104542553*l+.793617785*m-.0040720468*s,
            1.9779984951*l-2.428592205*m+.4505937099*s,
            .0259040371*l+.7827717662*m-.808675766*s)


def fromOklab(lab: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert OKLab to sRGB; return unclipped channels for intermediate arithmetic."""
    light,a,b = lab
    l = (light+.3963377774*a+.2158037573*b)**3
    m = (light-.1055613458*a-.0638541728*b)**3
    s = (light-.0894841775*a-1.291485548*b)**3
    return tuple(delinearize(v) for v in (
        4.0767416621*l-3.3077115913*m+.2309699292*s,
        -1.2684380046*l+2.6097574011*m-.3413193965*s,
        -.0041960863*l-.7034186147*m+1.707614701*s))


def channel16(value: float) -> int:
    """Round and clamp a unit channel to 16 bits."""
    return round(max(0., min(1., value)) * 65535)


@lru_cache(maxsize=16384)
def rotateHue(color: RGBA, degrees: float) -> RGBA:
    """Rotate OKLCh hue in degrees, preserve alpha, then clip to the sRGB gamut."""
    if not math.isfinite(degrees):
        raise FontError('hue must be finite')
    if degrees % 360 == 0:
        return color
    light,a,b = toOklab(tuple(c/65535 for c in color[:3]))
    angle = math.radians(degrees % 360)
    a,b = a*math.cos(angle)-b*math.sin(angle), a*math.sin(angle)+b*math.cos(angle)
    return tuple(channel16(v) for v in fromOklab((light,a,b))) + (color[3],)


def mixColor(first: RGBA, second: RGBA, amount: float) -> RGBA:
    """Interpolate colors in OKLab and alpha linearly; amount is clamped to [0,1]."""
    amount = max(0., min(1., amount))
    a = toOklab(tuple(c/65535 for c in first[:3]))
    b = toOklab(tuple(c/65535 for c in second[:3]))
    rgb = fromOklab(tuple(x+(y-x)*amount for x,y in zip(a,b)))
    return tuple(channel16(v) for v in rgb)+(round(first[3]+(second[3]-first[3])*amount),)


@lru_cache(maxsize=16384)
def composite(front: RGBA, back: RGBA) -> RGBA:
    """Straight-alpha source-over in linear light, returned as straight sRGB RGBA16."""
    if front[3] == 0:
        return back
    if front[3] == 65535:
        return front
    alpha = front[3]/65535
    backAlpha = back[3]/65535
    outputAlpha = alpha + backAlpha*(1-alpha)
    if outputAlpha == 0:
        return (0,0,0,0)
    rgb = tuple(delinearize((linearize(f/65535)*alpha
                            +linearize(b/65535)*backAlpha*(1-alpha))/outputAlpha)
                for f,b in zip(front[:3],back[:3]))
    return tuple(channel16(v) for v in rgb)+(channel16(outputAlpha),)


@lru_cache(maxsize=16384)
def nearestColor(color: RGBA, palette: tuple[RGBA, ...] = DOS_PALETTE) -> int:
    """Return nearest palette index by squared OKLab distance (alpha ignored)."""
    source = toOklab(tuple(c/65535 for c in color[:3]))
    return min(range(len(palette)), key=lambda i: sum((a-b)**2 for a,b in
               zip(source, paletteLabs(palette)[i])))


@lru_cache(maxsize=16)
def paletteLabs(palette: tuple[RGBA, ...]):
    """Cached conversion of an immutable target palette."""
    return tuple(toOklab(tuple(c/65535 for c in color[:3])) for color in palette)


def applyClut(color: RGBA, lut: Clut) -> RGBA:
    """Apply an R-fastest 3D LUT with nearest, trilinear or tetrahedral interpolation."""
    n = lut.size
    values = tuple(c/65535 for c in color[:3])
    if lut.domain:
        values = tuple(map(linearize, values))
    xyz = tuple(max(0.,min(1.,c))*(n-1) for c in values)
    def at(x,y,z):
        return lut.entries[x+n*y+n*n*z]
    if lut.interpolation == 0:
        rgb = at(*(min(n-1,int(v+.5)) for v in xyz))
    else:
        low = tuple(min(n-2,int(v)) for v in xyz)
        frac = tuple(v-i for v,i in zip(xyz,low))
        if lut.interpolation == 1:
            accum = [0.,0.,0.]
            for z in (0,1):
                for y in (0,1):
                    for x in (0,1):
                        weight = ((frac[0] if x else 1-frac[0])
                                  *(frac[1] if y else 1-frac[1])
                                  *(frac[2] if z else 1-frac[2]))
                        value = at(low[0]+x,low[1]+y,low[2]+z)
                        for channel in range(3):
                            accum[channel] += value[channel]*weight
            rgb = accum
        else:
            # The sorted fractional axes select one of the cube's six tetrahedra.
            axes = sorted(range(3), key=lambda axis: (-frac[axis],axis))
            coords = list(low)
            vertices = [at(*coords)]
            for axis in axes:
                coords[axis] += 1
                vertices.append(at(*coords))
            f0,f1,f2 = (frac[axis] for axis in axes)
            weights = (1-f0,f0-f1,f1-f2,f2)
            rgb = tuple(sum(vertex[c]*w for vertex,w in zip(vertices,weights)) for c in range(3))
    if lut.domain:
        rgb = tuple(channel16(delinearize(c/65535)) for c in rgb)
    else:
        rgb = tuple(round(c) for c in rgb)
    return rgb+(color[3],)


def resolveColor(color, model: int, palette: tuple[RGBA, ...], background=False) -> RGBA:
    """Resolve an indexed/direct/monochrome color to RGBA16; absent background is transparent."""
    if color is None:
        return (0,0,0,0) if background else WHITE
    if isinstance(color,int):
        chosen = palette or (DOS_PALETTE if model == 1 else XTERM_PALETTE)
        if not 0 <= color < len(chosen):
            raise FontError('palette index out of range')
        return chosen[color]
    return color
