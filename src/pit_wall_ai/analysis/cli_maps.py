"""``pit-wall circuit-maps``: one drawing per circuit of the table, to check the numbering against real maps.

Reads the racing lines of the game installed on this PC (nothing is shipped), writes one SVG per circuit and
an ``index.html`` that lists them with their corners and the checklist the author goes through.
"""

from __future__ import annotations

import argparse
import html
import logging
from pathlib import Path

from ..config import AppConfig
from . import circuit_names as CN
from .render import render_svg
from .tracks import find_tracks_dirs, list_layouts, resolve


def _tracks_of(circuit: dict, libs: list[Path]) -> list[str]:
    """Folder names in the libraries that are this circuit: the aliases that exist as folders."""
    found: list[str] = []
    for alias in [circuit["id"], *circuit.get("aliases", [])]:
        for lib in libs:
            if (lib / alias).is_dir() and alias not in found:
                found.append(alias)
    return found


def _best_line(circuit: dict, tracks: list[str], libs: list[Path]):
    """The standard line of the circuit's folders: no reverse, the plain folder before a named layout, and
    the closest to the table's length (within 3 %) among those."""
    want = float(circuit.get("length_m") or 0)
    cands = []
    for t in tracks:
        layouts: list[str] = [""]
        for lib in libs:
            layouts += list_layouts(lib / t)
        for layout in dict.fromkeys(layouts):
            r = resolve(t, layout, length_m=None, dirs=libs)
            if not r.ok or r.geometry is None:
                continue
            err = abs(r.geometry.length_m - want) / want if want else 0.0
            if err > 0.03:
                continue
            reverse = "reverse" in r.layout.lower() or r.layout.lower().endswith("_rev")
            cands.append(((reverse, bool(r.layout), err), r))
    return min(cands, key=lambda c: c[0])[1] if cands else None


def cmd_circuit_maps(cfg: AppConfig, a: argparse.Namespace, *, out=print) -> int:
    logging.getLogger("pit_wall_ai.analysis").setLevel(logging.ERROR)
    libs = find_tracks_dirs([*(a.ac_dir or []), *cfg.analysis.ac_dirs])
    if not libs:
        out("no Assetto Corsa tracks folder found (set analysis.ac_dirs)")
        return 1
    dest = Path(a.out)
    dest.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    made = 0
    only = {x.lower() for x in (a.only or [])}
    for circuit in CN._table():
        if only and circuit["id"].lower() not in only:
            continue
        tracks = _tracks_of(circuit, libs)
        res = _best_line(circuit, tracks, libs)
        if res is None or res.geometry is None:
            out(f"{circuit['id']:20s} no racing line found on this PC")
            continue
        g = res.geometry
        names = CN.corner_names(g, circuit["id"])
        straights = CN.all_straights(circuit["id"], g)
        svg = render_svg(
            g, names, straights, title=f"{circuit['name']} ({res.track}/{res.layout or '-'})"
        )
        (dest / f"{circuit['id']}.svg").write_text(svg, encoding="utf-8")
        made += 1
        rows = "".join(
            f"<tr><td>{c.number}</td><td>{'left' if c.direction == 'left' else 'right'}</td>"
            f"<td>{c.min_radius_m:.0f} m</td><td>{c.apex_m / g.length_m:.3f}</td>"
            f"<td>{html.escape(names.get(c.number, ''))}</td></tr>"
            for c in g.apexes
        )
        sts = "".join(
            f"<li>{html.escape(CN.straight_label(s, 'en'))} ({s.role})</li>" for s in straights
        )
        ok = "yes" if circuit.get("numbering_verified") else "no"
        cards.append(
            f"<section><h2>{html.escape(circuit['name'])}</h2>"
            f"<p>{len(g.apexes)} apexes on a {g.length_m:.0f} m line; table length {circuit.get('length_m')} m; "
            f"numbering verified: <b>{ok}</b></p>"
            f"<img src='{circuit['id']}.svg' width='560'>"
            f"<table border='1' cellpadding='4' style='border-collapse:collapse'>"
            f"<tr><th>#</th><th>dir</th><th>Rmin</th><th>lap</th><th>name</th></tr>{rows}</table>"
            f"<ul>{sts}</ul>"
            f"<p>Checklist: [ ] the number of corners matches the map &nbsp; [ ] the order matches "
            f"&nbsp; [ ] left/right match &nbsp; [ ] the names are in the right place</p></section>"
        )
        out(
            f"{circuit['id']:20s} {len(g.apexes):2d} apexes  {len(names):2d} named  -> {circuit['id']}.svg"
        )
    index = dest / "index.html"
    index.write_text(
        "<!doctype html><meta charset='utf-8'><title>Circuit maps</title>"
        "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px}section{margin:0 0 48px;"
        "border-bottom:1px solid #ccc;padding-bottom:24px}table{margin:12px 0}</style>"
        "<h1>Racing lines with their numbered corners</h1>"
        "<p>Generated locally from the game's racing lines; not part of the repository. Compare each drawing "
        "with the real circuit map: the numbers are the <i>apexes of the game's line</i>, which may not be "
        "the numbering of a published map (chicanes and kinks).</p>" + "".join(cards),
        encoding="utf-8",
    )
    out(f"\n{made} circuits drawn; open {index}")
    return 0 if made else 1
