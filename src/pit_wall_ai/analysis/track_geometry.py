"""Where the corners of a circuit are, from the racing line the AI follows in the installed game.

The geometry is generated **locally** from ``<tracks>/<track>/[<layout>/]ai/fast_lane.ai`` and is never part
of this repository (derived data of the game's files); the game folder is only ever read.

``fast_lane.ai`` (format version 7)::

    header   4 x int32 little endian: version, record_count, lap_time, sample_count
    records  record_count x 20 bytes: x f32, y f32, z f32, s f32 (cumulative distance), id i32
    ...      other blocks that are not needed

The horizontal plane is (x, z), the same frame as ``car_coordinates`` of the shared memory. Handedness
(checked live, see docs/TRACK_GEOMETRY.md): a path that turns anticlockwise in the (x, z) plane is a RIGHT
turn in the game; ``curvature`` is signed so that a LEFT turn is positive.

The corner segmentation is the one of the sibling project ``motorsport-analytics-pipeline`` (see
PROVENANCE.md), re-implemented on numpy only (no scipy): Savitzky-Golay curvature, peaks of |k|, grouped into
complexes. It describes the *geometry*: a bend taken flat out is still a corner here.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FAST_LANE_VERSION = 7
HEADER_BYTES = 16
RECORD_BYTES = 20

RESAMPLE_M = 2.0  # uniform spacing of the resampled line
SMOOTH_WINDOW_M = 62.0  # Savitzky-Golay window, cubic
CLOSE_GAP_M = 25.0  # end-to-start gap below which the line is a closed circuit
PEAK_MIN_CURVATURE = 1.0 / 400.0  # corners with a minimum radius up to 400 m
PEAK_PROMINENCE = 1.0 / 600.0
PEAK_DISTANCE_M = 40.0
EDGE_CURVATURE = 1.0 / 600.0  # corner extent: |k| above this ...
EDGE_PEAK_SHARE = 0.20  # ... or above 20 % of its peak, whichever is larger
COMPLEX_GAP_M = 120.0  # apexes closer than this form one complex
CHICANE_GAP_M = 200.0  # opposite-sign apexes closer than this form a chicane
SLOW_RADIUS_M = 60.0
MEDIUM_RADIUS_M = 150.0


class FastLaneError(ValueError):
    """The fast_lane.ai file is missing, truncated, corrupt or of an unsupported version."""


# -- numpy replacements for the two scipy functions the segmentation needs -------------------------------
def _sg_coeffs(window: int, order: int, deriv: int, delta: float) -> np.ndarray:
    """Savitzky-Golay convolution coefficients (the same definition as scipy.signal.savgol_coeffs)."""
    half = window // 2
    x = np.arange(-half, half + 1, dtype=float)
    vander = np.vander(x, order + 1, increasing=True)  # columns x^0 .. x^order
    pinv = np.linalg.pinv(vander)  # (order+1, window)
    return pinv[deriv] * math.factorial(deriv) / (delta**deriv)


def savgol(
    y: np.ndarray, window: int, order: int, deriv: int, delta: float, wrap: bool
) -> np.ndarray:
    c = _sg_coeffs(window, order, deriv, delta)
    half = window // 2
    padded = np.pad(y, half, mode="wrap" if wrap else "edge")
    # np.convolve flips the kernel: correlate with the coefficients as in scipy's convolution of reversed c
    return np.convolve(padded, c[::-1], mode="valid")


def find_peaks(y: np.ndarray, height: float, prominence: float, distance: int) -> np.ndarray:
    """Local maxima above ``height`` with at least ``prominence``, at least ``distance`` samples apart
    (the highest wins), like scipy.signal.find_peaks for these three arguments."""
    n = len(y)
    cand = []
    i = 1
    while i < n - 1:
        if y[i - 1] < y[i]:
            j = i
            while j + 1 < n and y[j + 1] == y[i]:
                j += 1
            if j + 1 < n and y[j + 1] < y[i]:
                cand.append((i + j) // 2)
            i = j
        i += 1
    cand = [p for p in cand if y[p] >= height]
    keep = []
    for p in cand:
        # prominence: lowest point to the nearest higher sample on each side (or the border)
        left_min = y[p]
        k = p
        while k >= 0 and y[k] <= y[p]:
            left_min = min(left_min, y[k])
            k -= 1
        right_min = y[p]
        k = p
        while k < n and y[k] <= y[p]:
            right_min = min(right_min, y[k])
            k += 1
        if y[p] - max(left_min, right_min) >= prominence:
            keep.append(p)
    # distance: highest first, drop neighbours closer than ``distance``
    order = sorted(keep, key=lambda p: -y[p])
    taken: list[int] = []
    for p in order:
        if all(abs(p - q) >= distance for q in taken):
            taken.append(p)
    return np.array(sorted(taken), dtype=int)


# -- data -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SubApex:
    fraction: float
    distance_m: float
    radius_m: float
    direction: str  # left | right


@dataclass
class Corner:
    number: int
    start_fraction: float
    apex_fraction: float
    end_fraction: float
    start_m: float
    apex_m: float
    end_m: float
    min_radius_m: float
    direction: str  # of the tightest sub-apex
    severity: str  # slow | medium | fast (geometry, not what a given car does)
    kind: str  # single | chicane | compound
    sub_apexes: list[SubApex] = field(default_factory=list)


@dataclass
class RacingLine:
    """The parsed line, closed into a loop, plus everything that needs only its shape."""

    x: np.ndarray
    z: np.ndarray
    s: np.ndarray  # cumulative distance of the closed polyline (len = n + 1, last = closed length)
    closed: bool
    closure_gap_m: float
    source: str = ""

    @property
    def length_m(self) -> float:
        return float(self.s[-1])

    def point_at(self, fraction: float) -> tuple[float, float]:
        d = (fraction % 1.0) * self.length_m
        return float(np.interp(d, self.s, self.x)), float(np.interp(d, self.s, self.z))

    def project(
        self, px: float, pz: float, near_fraction: float | None = None, window_m: float = 60.0
    ) -> tuple[float, float]:
        """Closest point of the line to (px, pz): (fraction of the lap, distance to the line in m).

        With ``near_fraction`` the search is limited to ``window_m`` around it, which avoids jumping to
        another stretch of the track that happens to be close (hairpins, parallel straights).
        """
        n = len(self.x) - 1
        x0, z0 = self.x[:-1], self.z[:-1]
        dx, dz = self.x[1:] - x0, self.z[1:] - z0
        if near_fraction is not None:
            centre = (near_fraction % 1.0) * self.length_m
            d = np.abs(
                ((self.s[:-1] - centre) + self.length_m / 2) % self.length_m - self.length_m / 2
            )
            idx = np.nonzero(d <= window_m)[0]
            if idx.size == 0:
                idx = np.arange(n)
        else:
            idx = np.arange(n)
        sx, sz = x0[idx], z0[idx]
        ex, ez = dx[idx], dz[idx]
        l2 = np.maximum(ex * ex + ez * ez, 1e-9)
        t = np.clip(((px - sx) * ex + (pz - sz) * ez) / l2, 0.0, 1.0)
        dist = np.hypot(px - (sx + t * ex), pz - (sz + t * ez))
        j = int(np.argmin(dist))
        s = self.s[idx[j]] + t[j] * math.sqrt(float(l2[j]))
        return (s / self.length_m) % 1.0, float(dist[j])


@dataclass
class TrackGeometry:
    line: RacingLine
    corners: list[Corner]
    step_m: float
    distance_m: np.ndarray
    curvature: np.ndarray  # signed 1/m, positive = left
    layout: str = ""
    track: str = ""

    @property
    def length_m(self) -> float:
        return self.line.length_m

    def corner_at(self, fraction: float) -> Corner | None:
        """The corner whose start..end contains ``fraction`` (wrap aware)."""
        f = fraction % 1.0
        for c in self.corners:
            a, b = c.start_fraction % 1.0, c.end_fraction % 1.0
            if (a <= f <= b) if a <= b else (f >= a or f <= b):
                return c
        return None


# -- parsing ----------------------------------------------------------------------------------------------
def parse_fast_lane(source: bytes | str | Path) -> RacingLine:
    if isinstance(source, bytes | bytearray | memoryview):
        raw, name = bytes(source), "<bytes>"
    else:
        name = str(source)
        try:
            raw = Path(source).read_bytes()
        except OSError as exc:
            raise FastLaneError(f"{name}: cannot read the file ({exc})") from exc
    if len(raw) < HEADER_BYTES:
        raise FastLaneError(f"{name}: too short for a fast_lane.ai header")
    version, count, _lap_time, _samples = struct.unpack_from("<4i", raw, 0)
    if version != FAST_LANE_VERSION:
        raise FastLaneError(f"{name}: unsupported fast_lane.ai version {version}")
    if not 10 <= count <= 5_000_000:
        raise FastLaneError(f"{name}: implausible record count {count}")
    if len(raw) < HEADER_BYTES + count * RECORD_BYTES:
        raise FastLaneError(f"{name}: truncated")
    dt = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("s", "<f4"), ("id", "<i4")])
    rec = np.frombuffer(raw, dtype=dt, count=count, offset=HEADER_BYTES)
    x, z, s = (rec[k].astype(float) for k in ("x", "z", "s"))
    if not (np.isfinite(x).all() and np.isfinite(z).all() and np.isfinite(s).all()):
        raise FastLaneError(f"{name}: non-finite values (corrupt file)")
    if s[-1] <= 0 or np.any(np.diff(s) < 0):
        raise FastLaneError(f"{name}: the distance column is not usable")
    step = np.hypot(np.diff(x), np.diff(z))
    if step.size and float(np.median(step)) > 20.0:
        raise FastLaneError(f"{name}: implausible sample spacing")
    gap = float(math.hypot(x[-1] - x[0], z[-1] - z[0]))
    closed = gap <= CLOSE_GAP_M
    if closed:  # close the loop: the last point links to the first
        x = np.append(x, x[0])
        z = np.append(z, z[0])
        s = np.append(s, s[-1] + gap)
    return RacingLine(x=x, z=z, s=s, closed=closed, closure_gap_m=gap, source=name)


# -- curvature and corners --------------------------------------------------------------------------------
def curvature_profile(
    line: RacingLine, ds: float = RESAMPLE_M, window_m: float = SMOOTH_WINDOW_M
) -> tuple[np.ndarray, np.ndarray, float]:
    """(distance of each sample, signed curvature in 1/m, step in m); positive = left turn."""
    keep = np.concatenate([[True], np.diff(line.s) > 1e-6])
    s, x, z = line.s[keep], line.x[keep], line.z[keep]
    length = float(s[-1] - s[0])
    if line.closed:
        n = max(8, round(length / ds))
        grid = s[0] + np.arange(n) * (length / n)
        step = length / n
    else:
        n = max(8, round(length / ds) + 1)
        grid = np.linspace(s[0], s[-1], n)
        step = length / (n - 1)
    gx, gz = np.interp(grid, s, x), np.interp(grid, s, z)
    win = round(window_m / step) | 1
    win = max(5, min(win, (n // 2) * 2 - 1))
    wrap = line.closed
    dx, dz = savgol(gx, win, 3, 1, step, wrap), savgol(gz, win, 3, 1, step, wrap)
    ddx, ddz = savgol(gx, win, 3, 2, step, wrap), savgol(gz, win, 3, 2, step, wrap)
    den = np.power(dx * dx + dz * dz, 1.5)
    k = np.divide(dz * ddx - dx * ddz, den, out=np.zeros_like(den), where=den > 1e-12)
    return grid - grid[0], k, step


def _segment(
    dist: np.ndarray, k: np.ndarray, closed: bool, length: float, step: float
) -> list[Corner]:
    n = len(k)
    ak = np.abs(k)
    ext = np.concatenate([ak, ak, ak]) if closed else ak
    pk = find_peaks(
        ext,
        height=PEAK_MIN_CURVATURE,
        prominence=PEAK_PROMINENCE,
        distance=max(1, round(PEAK_DISTANCE_M / step)),
    )
    if closed:
        pk = pk[(pk >= n) & (pk < 2 * n)] - n
    if pk.size == 0:
        return []

    def at(i: int) -> float:
        return float(ak[i % n]) if closed else float(ak[min(max(i, 0), n - 1)])

    peaks = [int(p) for p in pk]
    m = len(peaks)
    valleys: list[int | None] = []
    for a in range(m):
        i0 = peaks[a]
        i1 = peaks[a + 1] if a + 1 < m else (peaks[0] + n if closed else None)
        if i1 is None:
            valleys.append(None)
            continue
        seg = np.array([at(i) for i in range(i0, i1 + 1)])
        valleys.append(i0 + int(np.argmin(seg)))

    def bound(a: int, side: int) -> int:
        if side > 0:
            v = valleys[a]
            return v if v is not None else (n - 1 if not closed else peaks[a] + n // 2)
        prev = (
            valleys[a - 1]
            if a > 0
            else (valleys[-1] - n if closed and valleys[-1] is not None else None)
        )
        return prev if prev is not None else 0

    def extent(a: int) -> tuple[int, int]:
        i = peaks[a]
        sign = 1.0 if k[i] >= 0 else -1.0
        edge = max(EDGE_CURVATURE, EDGE_PEAK_SHARE * ak[i])
        lo, hi = bound(a, -1), bound(a, +1)
        j = i
        while j > lo and (k[j % n] * sign) >= edge:
            j -= 1
        s0 = j
        j = i
        while j < hi and (k[j % n] * sign) >= edge:
            j += 1
        return s0, j

    sub = []
    for a in range(m):
        i = peaks[a]
        s0, s1 = extent(a)
        sub.append(
            {
                "i": i,
                "s0": s0,
                "s1": s1,
                "sign": 1.0 if k[i] >= 0 else -1.0,
                "radius": 1.0 / float(ak[i]),
                "d": float(dist[i]),
            }
        )

    groups: list[list[dict]] = [[sub[0]]]
    for cur in sub[1:]:
        prev = groups[-1][-1]
        gap_m = (cur["i"] - prev["i"]) * step
        if gap_m < COMPLEX_GAP_M or (cur["sign"] != prev["sign"] and gap_m < CHICANE_GAP_M):
            groups[-1].append(cur)
        else:
            groups.append([cur])
    if closed and len(groups) > 1:  # the last and the first group may touch across the start line
        first, last = groups[0][0], groups[-1][-1]
        gap_m = (first["i"] + n - last["i"]) * step
        if gap_m < COMPLEX_GAP_M or (first["sign"] != last["sign"] and gap_m < CHICANE_GAP_M):
            merged = groups.pop()
            for g in groups[0]:
                g["_wrapped"] = True
            groups[0] = merged + groups[0]

    def frac(i: int) -> float:
        return ((i % n) * step / length) if closed else float(dist[min(max(i, 0), n - 1)]) / length

    corners: list[Corner] = []
    for g in groups:
        tight = min(g, key=lambda e: e["radius"])
        t_i = tight["i"]
        s0 = g[0]["s0"] + (n if g[0].get("_wrapped") else 0)
        s1 = g[-1]["s1"] + (n if g[-1].get("_wrapped") else 0)
        kind = "single"
        if len(g) > 1:
            kind = "chicane" if len({e["sign"] for e in g}) > 1 else "compound"
        subs = [
            SubApex(
                fraction=frac(e["i"]),
                distance_m=float(e["d"]),
                radius_m=float(e["radius"]),
                direction="left" if e["sign"] > 0 else "right",
            )
            for e in g
        ]
        severity = (
            "slow"
            if tight["radius"] < SLOW_RADIUS_M
            else "medium"
            if tight["radius"] < MEDIUM_RADIUS_M
            else "fast"
        )
        corners.append(
            Corner(
                number=0,
                start_fraction=frac(s0),
                apex_fraction=frac(t_i),
                end_fraction=frac(s1),
                start_m=float(dist[s0 % n]),
                apex_m=float(dist[t_i % n]),
                end_m=float(dist[s1 % n]),
                min_radius_m=float(tight["radius"]),
                direction="left" if tight["sign"] > 0 else "right",
                severity=severity,
                kind=kind,
                sub_apexes=subs,
            )
        )
    corners.sort(key=lambda c: c.apex_fraction)
    for i, c in enumerate(corners, 1):
        c.number = i
    return corners


def analyze_line(line: RacingLine, *, track: str = "", layout: str = "") -> TrackGeometry:
    dist, k, step = curvature_profile(line)
    corners = _segment(dist, k, line.closed, line.length_m, step)
    return TrackGeometry(
        line=line,
        corners=corners,
        step_m=step,
        distance_m=dist,
        curvature=k,
        track=track,
        layout=layout,
    )
