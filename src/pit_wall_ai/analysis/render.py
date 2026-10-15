"""An SVG of the racing line with its numbered corners, to check the numbering against a circuit map.

The geometry comes from the game's own racing line and is generated locally: the picture is for the author's
eyes (compare the numbers with the real map), not something the repository ships. The orientation is the
game's (``x`` to the right, ``z`` down, handedness kept) and may be turned or mirrored with respect to a published map; the start
line and the driving direction are drawn to help.
"""

from __future__ import annotations

import html
import math
from pathlib import Path

import numpy as np

SIZE = 1000.0
MARGIN = 70.0
STRAIGHT_COLOURS = {"main": "#2e7d32", "back": "#1565c0", "named": "#ef6c00"}


def _project(x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    xmin, xmax = float(x.min()), float(x.max())
    zmin, zmax = float(z.min()), float(z.max())
    span = max(xmax - xmin, zmax - zmin, 1.0)
    scale = (SIZE - 2 * MARGIN) / span
    px = MARGIN + (x - xmin) * scale + ((SIZE - 2 * MARGIN) - (xmax - xmin) * scale) / 2
    # the game's z grows towards the bottom of a map (north is -z): plotting it as is keeps the handedness
    py = MARGIN + (z - zmin) * scale + ((SIZE - 2 * MARGIN) - (zmax - zmin) * scale) / 2
    return px, py


def render_svg(
    geometry,
    names: dict[int, str] | None = None,
    straights=(),
    *,
    title: str = "",
) -> str:
    """The SVG text. ``straights`` are :class:`circuit_names.Straight` objects (named, main, back)."""
    names = names or {}
    line = geometry.line
    n = len(line.x)
    px, py = _project(line.x, line.z)
    length = float(line.length_m)
    # distance along the loop of every vertex, to place fractions on the drawing
    s = np.asarray(line.s[:n], dtype=float)

    def at(fraction: float) -> tuple[float, float]:
        d = (fraction % 1.0) * length
        return float(np.interp(d, s, px)), float(np.interp(d, s, py))

    def path_for(a_frac: float, b_frac: float) -> str:
        a = (a_frac % 1.0) * length
        b = (b_frac % 1.0) * length
        if a <= b:
            idx = [i for i in range(n) if a <= s[i] <= b]
        else:
            idx = [i for i in range(n) if s[i] >= a or s[i] <= b]
        pts = [at(a_frac), *[(float(px[i]), float(py[i])) for i in idx], at(b_frac)]
        return "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE:.0f} {SIZE:.0f}" '
        f'width="900" height="900" font-family="Segoe UI, Arial, sans-serif">',
        f'<rect width="{SIZE:.0f}" height="{SIZE:.0f}" fill="#fafafa"/>',
    ]
    base = "M " + " L ".join(f"{float(px[i]):.1f},{float(py[i]):.1f}" for i in range(n)) + " Z"
    out.append(
        f'<path d="{base}" fill="none" stroke="#222" stroke-width="7" stroke-linejoin="round"/>'
    )
    for st in straights:
        colour = STRAIGHT_COLOURS.get(st.role, STRAIGHT_COLOURS["named"])
        out.append(
            f'<path d="{path_for(st.start, st.end)}" fill="none" stroke="{colour}" stroke-width="7" '
            f'stroke-linecap="round" opacity="0.85"/>'
        )
        mx, my = at(st.start + ((st.end - st.start) % 1.0) / 2)
        label = html.escape(st.name if st.name not in ("main", "back") else st.role)
        out.append(
            f'<text x="{mx:.1f}" y="{my - 14:.1f}" font-size="19" fill="{colour}" text-anchor="middle" '
            f'font-weight="600">{label}</text>'
        )
    # start / finish line and the driving direction
    sx, sy = at(0.0)
    ax, ay = at(0.004)
    ang = math.degrees(math.atan2(ay - sy, ax - sx))
    out.append(
        f'<g transform="translate({sx:.1f},{sy:.1f}) rotate({ang:.1f})">'
        f'<rect x="-3" y="-17" width="6" height="34" fill="#000"/>'
        f'<path d="M 12,0 L 0,-9 L 0,9 Z" fill="#c62828"/></g>'
    )
    for c in geometry.apexes:
        x, y = at(c.apex_fraction)
        name = names.get(c.number, "")
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="15" fill="#fff" stroke="#c62828" stroke-width="3"/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{y + 6:.1f}" font-size="17" text-anchor="middle" font-weight="700">{c.number}</text>'
        )
        if name:
            out.append(
                f'<text x="{x + 20:.1f}" y="{y - 12:.1f}" font-size="15" fill="#333">{html.escape(name)}</text>'
            )
    if title:
        out.append(
            f'<text x="20" y="34" font-size="24" font-weight="700">{html.escape(title)}</text>'
        )
    out.append(
        f'<text x="20" y="{SIZE - 18:.0f}" font-size="14" fill="#666">numbers = apexes of the game\'s racing line '
        f"(red flag = start, black bar = line); orientation of the game</text>"
    )
    out.append("</svg>")
    return "\n".join(out)


def write_svg(path: str | Path, geometry, names=None, straights=(), *, title: str = "") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_svg(geometry, names, straights, title=title), encoding="utf-8")
    return p
