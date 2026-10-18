"""The debrief as one self-contained HTML page: the report and two small charts (lap times, corners that cost time).

No scripts and no external files: inline SVG and CSS, light and dark by the system's preference, readable on a phone.
"""

from __future__ import annotations

import html

from .debrief import Debrief, to_markdown

_CSS = """
:root{--bg:#fff;--fg:#1c1c1e;--muted:#6b6b70;--line:#d8d8de;--ok:#1a7f4b;--bad:#b3261e;--bar:#3b6fd4;--card:#f6f6f8}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--fg:#ececf0;--muted:#9a9aa3;--line:#33333a;--ok:#4cc38a;--bad:#ff7b72;--bar:#7aa2ff;--card:#1d1d21}}
body{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;margin:0;padding:16px;max-width:760px;margin-inline:auto}
h1{font-size:1.4rem;margin:.2em 0}h2{font-size:1.05rem;margin:1.4em 0 .3em;border-bottom:1px solid var(--line);padding-bottom:.2em}
ul{padding-left:1.2em}li{margin:.15em 0}.card{background:var(--card);border-radius:10px;padding:10px 12px;margin:10px 0}
svg{width:100%;height:auto;display:block}text{fill:var(--muted);font-size:11px}.axis{stroke:var(--line)}
"""


def _fmt(ms: float) -> str:
    m, s = divmod(ms / 1000.0, 60)
    return f"{int(m)}:{s:06.3f}"


def lap_chart(laps: list[dict], lang: str = "es") -> str:
    """Lap time against lap number: valid laps filled, the others hollow, the best one in green."""
    good = [x for x in laps if x["ms"] > 0]
    if len(good) < 2:
        return ""
    valid = [x["ms"] for x in good if x["valid"]] or [x["ms"] for x in good]
    best = min(valid)
    lo, hi = best * 0.99, min(max(x["ms"] for x in good), best * 1.12)
    w, h, pl, pr, pt, pb = 640, 220, 52, 12, 12, 26
    n = len(good)

    def X(i: int) -> float:
        return pl + (w - pl - pr) * (i / max(1, n - 1))

    def Y(ms: float) -> float:
        return pt + (h - pt - pb) * (1 - (min(ms, hi) - lo) / (hi - lo))

    pts = " ".join(f"{X(i):.1f},{Y(x['ms']):.1f}" for i, x in enumerate(good))
    out = [
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{"Tiempos por vuelta" if lang == "es" else "Lap times"}">'
    ]
    for k in range(5):
        ms = lo + (hi - lo) * k / 4
        y = Y(ms)
        out.append(f'<line class="axis" x1="{pl}" x2="{w - pr}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text x="{pl - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(ms)}</text>')
    out.append(f'<polyline points="{pts}" fill="none" stroke="var(--bar)" stroke-width="1.5"/>')
    for i, x in enumerate(good):
        cx, cy = X(i), Y(x["ms"])
        if x["ms"] == best and x["valid"]:
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="var(--ok)"/>')
        elif x["valid"]:
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.5" fill="var(--bar)"/>')
        else:
            out.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.5" fill="none" stroke="var(--bad)" stroke-width="1.5"/>'
            )
        out.append(f'<text x="{cx:.1f}" y="{h - 8}" text-anchor="middle">{x["n"]}</text>')
    out.append("</svg>")
    return "".join(out)


def loss_chart(corners: list[dict], lang: str = "es") -> str:
    """Horizontal bars: the mean time lost per lap in each of the worst corners."""
    if not corners:
        return ""
    top = max(c["mean_loss_s"] for c in corners) or 1.0
    row, w, label_w = 28, 640, 190
    out = [
        f'<svg viewBox="0 0 {w} {row * len(corners) + 8}" role="img" aria-label="{"Tiempo perdido por curva" if lang == "es" else "Time lost per corner"}">'
    ]
    for i, c in enumerate(corners):
        name = c.get("name") or (
            f"curva {c['corner']}" if lang == "es" else f"corner {c['corner']}"
        )
        if c.get("name") and c.get("corner") is not None:
            name += f" ({c['corner']})"
        y = 4 + i * row
        bar = (w - label_w - 70) * c["mean_loss_s"] / top
        out.append(f'<text x="0" y="{y + 16}">{html.escape(name)}</text>')
        out.append(
            f'<rect x="{label_w}" y="{y + 3}" width="{bar:.1f}" height="16" rx="3" fill="var(--bar)"/>'
        )
        val = f"{c['mean_loss_s']:.2f} s"
        val = val.replace(".", ",") if lang == "es" else val
        out.append(f'<text x="{label_w + bar + 6:.1f}" y="{y + 16}">{val}</text>')
    out.append("</svg>")
    return "".join(out)


def _markdown_to_html(md: str) -> str:
    out, in_list = [], False
    for line in md.splitlines():
        if line.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("# "):
            out.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.strip():
            out.append(f"<p>{html.escape(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def to_html(d: Debrief, lang: str = "es") -> str:
    lang = "es" if lang == "es" else "en"
    body = _markdown_to_html(to_markdown(d, lang))
    charts = []
    laps = lap_chart(d.series.get("laps", []), lang)
    if laps:
        charts.append(
            f'<h2>{"Vueltas" if lang == "es" else "Laps"}</h2><div class="card">{laps}</div>'
        )
    loss = loss_chart(d.facts.get("time_loss", {}).get("corners", []), lang)
    if loss:
        charts.append(
            f'<h2>{"Curvas que más tiempo cuestan" if lang == "es" else "Corners that cost the most"}</h2><div class="card">{loss}</div>'
        )
    title = "Resumen de la sesión" if lang == "es" else "Session debrief"
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>'
        f"<style>{_CSS}</style></head><body>{body}{''.join(charts)}</body></html>"
    )
