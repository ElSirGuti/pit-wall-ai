"""Find the racing line of a track in the user's Assetto Corsa installs (read-only).

A track is identified by the folder name the game reports (``static.track``) and, for tracks with several
layouts, the layout folder (``static.track_configuration``)::

    <tracks>/<track>/ai/fast_lane.ai                  (no layout)
    <tracks>/<track>/<layout>/ai/fast_lane.ai         (with layout)

Several libraries can hold the same track (a second drive, a mod copy). The game's own track length is used
to pick the right file; when it cannot be decided the answer is "unknown" with the reason, never a guess.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .track_geometry import FastLaneError, RacingLine, TrackGeometry, analyze_line, parse_fast_lane

log = logging.getLogger(__name__)

# where Steam libraries usually are; the user's configured folders always come first
_STEAM_GUESSES = (
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
    r"D:\SteamLibrary",
    r"D:\Steam",
    r"E:\SteamLibrary",
    r"F:\SteamLibrary",
)
_GAME = Path("steamapps") / "common" / "assettocorsa"
LENGTH_TOLERANCE = (
    0.015  # the line is within ~0.1-1 % of the official length; 1.5 % separates layouts
)


def tracks_dir_from(path: str | Path) -> Path | None:
    """Accept the game folder, its ``content`` folder, a Steam library or the ``tracks`` folder itself."""
    p = Path(os.path.expandvars(str(path))).expanduser()
    for cand in (p, p / "tracks", p / "content" / "tracks", p / _GAME / "content" / "tracks"):
        if cand.name.lower() == "tracks" and cand.is_dir():
            return cand
    return None


def find_tracks_dirs(configured: Iterable[str] = ()) -> list[Path]:
    """Configured folders first, then the usual Steam locations. Existing ones only, no duplicates."""
    out: list[Path] = []
    for raw in [*configured, *os.environ.get("AC_PATH", "").split(os.pathsep), *_STEAM_GUESSES]:
        if not raw:
            continue
        d = tracks_dir_from(raw)
        if d is not None and d not in out:
            out.append(d)
    return out


@dataclass(frozen=True)
class LineCandidate:
    path: Path
    track: str
    layout: str
    library: Path
    length_m: float | None = None  # of the parsed line
    error: str = ""


@dataclass
class Resolution:
    """What was found for one track, and why."""

    track: str
    layout: str
    geometry: TrackGeometry | None = None
    candidate: LineCandidate | None = None
    game_length_m: float | None = None
    reason: str = ""  # why there is no geometry, or how it was chosen
    candidates: list[LineCandidate] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.geometry is not None


def list_layouts(track_dir: Path) -> list[str]:
    """Sub-folders that hold their own ``ai/fast_lane.ai``."""
    if not track_dir.is_dir():
        return []
    return sorted(
        d.name for d in track_dir.iterdir() if d.is_dir() and (d / "ai" / "fast_lane.ai").is_file()
    )


def _candidates(track: str, layout: str | None, dirs: Iterable[Path]) -> list[LineCandidate]:
    found: list[LineCandidate] = []
    for lib in dirs:
        base = lib / track
        if not base.is_dir():
            continue
        if layout is None:  # unknown layout: the base line and every layout folder
            if (base / "ai" / "fast_lane.ai").is_file():
                found.append(LineCandidate(base / "ai" / "fast_lane.ai", track, "", lib))
            for lay in list_layouts(base):
                found.append(LineCandidate(base / lay / "ai" / "fast_lane.ai", track, lay, lib))
        else:
            p = (
                (base / layout / "ai" / "fast_lane.ai")
                if layout
                else (base / "ai" / "fast_lane.ai")
            )
            if p.is_file():
                found.append(LineCandidate(p, track, layout, lib))
    return found


def resolve(
    track: str,
    layout: str | None = "",
    *,
    length_m: float | None = None,
    dirs: Iterable[Path] | None = None,
    configured: Iterable[str] = (),
    tolerance: float = LENGTH_TOLERANCE,
) -> Resolution:
    """Find, parse and analyse the line of ``track``.

    ``layout=""`` is "no layout"; ``layout=None`` means "unknown" (logs without that information): every
    layout is tried and the game's track length must pick one.
    """
    res = Resolution(track=track, layout=layout or "", game_length_m=length_m)
    libs = list(dirs) if dirs is not None else find_tracks_dirs(configured)
    if not libs:
        res.reason = "no Assetto Corsa tracks folder found (set analysis.ac_dirs)"
        return res
    parsed: list[tuple[LineCandidate, RacingLine]] = []
    seen: set[tuple[str, str]] = set()
    for c in _candidates(track, layout, libs):
        try:
            key = (c.layout, hashlib.sha1(c.path.read_bytes()).hexdigest())
        except OSError:
            key = (c.layout, str(c.path))
        if key in seen:  # the same file in two libraries is one candidate
            continue
        seen.add(key)
        try:
            line = parse_fast_lane(c.path)
        except FastLaneError as exc:
            res.candidates.append(
                LineCandidate(c.path, c.track, c.layout, c.library, error=str(exc))
            )
            continue
        cand = LineCandidate(c.path, c.track, c.layout, c.library, length_m=line.length_m)
        res.candidates.append(cand)
        parsed.append((cand, line))
    if not parsed:
        res.reason = (
            f"no fast_lane.ai for track {track!r}"
            + (f" layout {layout!r}" if layout else "")
            + " in the installed libraries"
        )
        return res
    chosen: tuple[LineCandidate, RacingLine] | None = None
    if length_m:
        near = [
            (abs(line.length_m - length_m) / length_m, c, line)
            for c, line in parsed
            if abs(line.length_m - length_m) / length_m <= tolerance
        ]
        if not near:
            sizes = ", ".join(f"{c.length_m:.0f} m" for c, _ in parsed)
            res.reason = (
                f"no line of {track!r} matches the game's length {length_m:.0f} m (found {sizes}):"
                " wrong layout or a different version of the track"
            )
            return res
        near.sort(key=lambda t: t[0])
        if len(near) > 1 and near[1][0] - near[0][0] < 1e-4:
            same = {(c.layout, round(line.length_m, 1)) for _, c, line in near}
            if len(same) > 1:
                res.reason = (
                    f"{len(near)} lines of {track!r} have the same length: cannot tell which"
                )
                return res
        chosen = (near[0][1], near[0][2])
        res.reason = f"matched by length ({near[0][0] * 100:.2f} % off)"
    elif len(parsed) == 1:
        chosen = parsed[0]
        res.reason = "only candidate; the game's length was not available to confirm it"
    else:
        res.reason = f"{len(parsed)} candidate lines and no game length to choose between them"
        return res
    cand, line = chosen
    res.candidate = cand
    res.layout = cand.layout
    res.geometry = analyze_line(line, track=track, layout=cand.layout)
    from .circuit_names import attach_official

    attach_official(res.geometry, track)
    return res
