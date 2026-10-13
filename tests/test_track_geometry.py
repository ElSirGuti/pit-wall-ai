"""Racing-line geometry: parsing, projection, corners, track lookup, names and the `corners` command."""

from __future__ import annotations

import csv
import gzip
import math
import struct
from pathlib import Path

import numpy as np
import pytest

from pit_wall_ai.analysis import circuit_names, tracks
from pit_wall_ai.analysis.track_geometry import (
    FastLaneError,
    analyze_line,
    find_peaks,
    parse_fast_lane,
    savgol,
)
from pit_wall_ai.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def lane_bytes(xs, zs, *, version=7, gap_close=True) -> bytes:
    xs, zs = np.asarray(xs, float), np.asarray(zs, float)
    s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xs), np.diff(zs)))])
    head = struct.pack("<4i", version, len(xs), 90000, len(xs))
    body = b"".join(
        struct.pack("<4fi", float(x), 0.0, float(z), float(d), i)
        for i, (x, z, d) in enumerate(zip(xs, zs, s, strict=True))
    )
    return head + body


def stadium(straight=400.0, radius=100.0, step=1.5, scale=1.0):
    """Anticlockwise stadium: straight along +x, left semicircle, straight back, left semicircle."""
    pts = []
    n = int(straight / step)
    pts += [(i * step, 0.0) for i in range(n)]
    m = int(math.pi * radius / step)
    pts += [
        (straight + radius * math.sin(a), radius - radius * math.cos(a))
        for a in np.linspace(0, math.pi, m, endpoint=False)
    ]
    pts += [(straight - i * step, 2 * radius) for i in range(n)]
    pts += [
        (-radius * math.sin(a), radius + radius * math.cos(a))
        for a in np.linspace(0, math.pi, m, endpoint=False)
    ]
    xs, zs = zip(*pts, strict=True)
    return np.array(xs) * scale, np.array(zs) * scale


@pytest.fixture
def stadium_line():
    xs, zs = stadium()
    return parse_fast_lane(lane_bytes(xs, zs))


# -- numpy replacements ---------------------------------------------------------------------------------
def test_savgol_derivatives_of_a_sine_match_the_analytic_ones():
    n, step = 600, 0.5
    x = np.arange(n) * step
    y = np.sin(x / 20.0)
    d1 = savgol(y, 31, 3, 1, step, wrap=False)
    d2 = savgol(y, 31, 3, 2, step, wrap=False)
    mid = slice(40, n - 40)
    assert np.allclose(d1[mid], np.cos(x / 20.0)[mid] / 20.0, atol=1e-4)
    assert np.allclose(d2[mid], -np.sin(x / 20.0)[mid] / 400.0, atol=5e-5)


def test_find_peaks_height_prominence_and_distance():
    y = np.zeros(100)
    y[10], y[11], y[12] = 0.5, 1.0, 0.5  # a peak of 1.0
    y[20] = 0.7  # a narrow, lower peak close to it
    y[60] = 0.3  # below the height
    y[80:84] = 0.9  # a plateau
    got = find_peaks(y, height=0.5, prominence=0.2, distance=15)
    assert list(got) == [11, 81]  # 20 is within 15 samples of the higher 11; 60 is too low


# -- parsing and projection -----------------------------------------------------------------------------
def test_parse_closes_the_loop_and_reports_the_length(stadium_line):
    expected = 2 * 400 + 2 * math.pi * 100
    assert stadium_line.closed
    assert stadium_line.length_m == pytest.approx(expected, rel=0.01)
    assert stadium_line.x[0] == stadium_line.x[-1]


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        (b"short", "too short"),
        (struct.pack("<4i", 6, 100, 0, 0) + b"\0" * 2000, "version"),
        (struct.pack("<4i", 7, 100, 0, 0) + b"\0" * 20, "truncated"),
        (struct.pack("<4i", 7, 3, 0, 0), "implausible record count"),
    ],
)
def test_bad_files_raise_a_clear_error(data, needle):
    with pytest.raises(FastLaneError, match=needle):
        parse_fast_lane(data)


def test_non_finite_values_are_rejected():
    xs, zs = stadium()
    raw = bytearray(lane_bytes(xs, zs))
    struct.pack_into("<f", raw, 16, float("nan"))
    with pytest.raises(FastLaneError, match="non-finite"):
        parse_fast_lane(bytes(raw))


def test_projection_gives_the_lap_fraction_and_the_distance_to_the_line(stadium_line):
    L = stadium_line.length_m
    f, d = stadium_line.project(200.0, 0.0)  # middle of the first straight
    assert f == pytest.approx(200.0 / L, abs=1e-3) and d == pytest.approx(0.0, abs=0.05)
    f, d = stadium_line.project(200.0, 5.0)  # 5 m inside
    assert f == pytest.approx(200.0 / L, abs=1e-3) and d == pytest.approx(5.0, abs=0.05)
    f_apex, _ = stadium_line.project(500.0, 100.0)  # the far side of the first semicircle
    assert f_apex == pytest.approx((400 + math.pi * 100 / 2) / L, abs=2e-3)


def test_projection_with_a_hint_does_not_jump_to_the_other_straight(stadium_line):
    # a point equidistant (100 m) from both straights is ambiguous without a hint
    L = stadium_line.length_m
    near_first = 0.1 * 400.0 / L * 2.0  # ~ early on the first straight
    f, _ = stadium_line.project(100.0, 100.0, near_fraction=near_first, window_m=150.0)
    assert f < 0.2
    back = (400 + math.pi * 100 + 300) / L
    f2, _ = stadium_line.project(100.0, 100.0, near_fraction=back, window_m=150.0)
    assert f2 > 0.5


# -- corners --------------------------------------------------------------------------------------------
def test_a_stadium_has_two_left_corners_at_the_semicircles(stadium_line):
    g = analyze_line(stadium_line, track="stadium")
    assert len(g.corners) == 2
    c1, c2 = g.corners
    assert (c1.number, c2.number) == (1, 2)
    # anticlockwise in the (x, z) plane is a RIGHT turn in the game (checked against the steering sign)
    assert c1.direction == c2.direction == "right"
    assert c1.min_radius_m == pytest.approx(100.0, rel=0.08)
    # a constant-radius arc has no unique apex: check the extent instead
    arc = math.pi * 100
    assert c1.start_m == pytest.approx(400, abs=45) and c1.end_m == pytest.approx(400 + arc, abs=45)
    assert c1.start_m <= c1.apex_m <= c1.end_m
    assert c2.start_m == pytest.approx(400 + arc + 400, abs=45)
    assert min(c2.end_m, g.length_m - c2.end_m) < 60  # it ends just after the start line (wraps)
    assert c1.kind == "single" and c1.severity == "medium"  # R100: >= 60 m and < 150 m


def test_corner_lookup_by_lap_fraction(stadium_line):
    g = analyze_line(stadium_line)
    c1, c2 = g.corners
    assert g.corner_at(c1.apex_fraction) is c1
    assert g.corner_at(c2.apex_fraction) is c2
    assert g.corner_at(0.1 * c1.start_fraction) is None  # inside the first straight


def test_a_chicane_is_grouped_and_its_sub_apexes_are_kept():
    # straight, a right then a left bend close together, straight
    pts = [(i * 1.5, 0.0) for i in range(200)]
    x0 = pts[-1][0]
    heading = 0.0
    x, z = x0, 0.0
    for sign in (-1, 1):
        for _ in range(60):
            heading += sign * (1.5 / 60.0)
            x += 1.5 * math.cos(heading)
            z += 1.5 * math.sin(heading)
            pts.append((x, z))
    # bring it back round into a loop with a big loose arc (radius ~ 400, not a corner)
    xs, zs = zip(*pts, strict=True)
    line = parse_fast_lane(lane_bytes(xs, zs))
    g = analyze_line(line)
    assert not line.closed
    chic = [c for c in g.corners if c.kind == "chicane"]
    assert len(chic) == 1 and len(chic[0].sub_apexes) == 2
    assert {s.direction for s in chic[0].sub_apexes} == {"left", "right"}


# -- track lookup ---------------------------------------------------------------------------------------
def put_line(root: Path, track: str, layout: str = "", *, scale: float = 1.0) -> Path:
    base = root / track / layout / "ai" if layout else root / track / "ai"
    base.mkdir(parents=True)
    xs, zs = stadium(scale=scale)
    (base / "fast_lane.ai").write_bytes(lane_bytes(xs, zs))
    return base / "fast_lane.ai"


def test_resolve_a_track_without_layout(tmp_path):
    put_line(tmp_path / "tracks", "stad")
    res = tracks.resolve("stad", "", length_m=1428.0, dirs=[tmp_path / "tracks"])
    assert res.ok and res.layout == "" and "matched by length" in res.reason
    assert len(res.geometry.corners) == 2


def test_the_layout_is_chosen_by_name_and_by_length(tmp_path):
    t = tmp_path / "tracks"
    put_line(t, "multi", "short", scale=0.5)
    put_line(t, "multi", "long", scale=1.0)
    by_name = tracks.resolve("multi", "long", length_m=1428.0, dirs=[t])
    assert by_name.ok and by_name.layout == "long"
    # unknown layout (a log): the game's length decides
    by_len = tracks.resolve("multi", None, length_m=714.0, dirs=[t])
    assert by_len.ok and by_len.layout == "short"
    # unknown layout and no length: refuses to guess
    amb = tracks.resolve("multi", None, dirs=[t])
    assert not amb.ok and "no game length" in amb.reason


def test_a_length_mismatch_is_reported_not_forced(tmp_path):
    t = tmp_path / "tracks"
    put_line(t, "stad")
    res = tracks.resolve("stad", "", length_m=2400.0, dirs=[t])
    assert not res.ok and "wrong layout" in res.reason and "1428" in res.reason


def test_the_same_file_in_two_libraries_is_one_candidate(tmp_path):
    a, b = tmp_path / "a" / "tracks", tmp_path / "b" / "tracks"
    put_line(a, "stad")
    put_line(b, "stad")
    res = tracks.resolve("stad", None, dirs=[a, b])
    assert res.ok and "only candidate" in res.reason


def test_missing_track_and_missing_install_have_distinct_reasons(tmp_path):
    t = tmp_path / "tracks"
    t.mkdir()
    assert "no fast_lane.ai" in tracks.resolve("nope", "", dirs=[t]).reason
    assert "no Assetto Corsa tracks folder" in tracks.resolve("nope", "", dirs=[]).reason


def test_tracks_dir_accepts_game_content_and_library_folders(tmp_path):
    game = tmp_path / "steamapps" / "common" / "assettocorsa" / "content" / "tracks"
    game.mkdir(parents=True)
    for given in (tmp_path, game.parent.parent, game.parent, game):
        assert tracks.tracks_dir_from(given) == game
    assert tracks.tracks_dir_from(tmp_path / "elsewhere") is None


# -- names ----------------------------------------------------------------------------------------------
def test_names_are_attached_only_where_the_table_has_a_corner_inside():
    from pit_wall_ai.analysis.track_geometry import Corner, TrackGeometry

    def corner(n, a, apex, b):
        return Corner(n, a, apex, b, 0, 0, 0, 80.0, "left", "medium", "single")

    # Spa table: La Source 0.056, Eau Rouge 0.165, Les Combes 0.353 + Malmedy 0.378
    g = TrackGeometry(
        line=None,  # type: ignore[arg-type]
        corners=[
            corner(1, 0.045, 0.055, 0.065),
            corner(2, 0.15, 0.164, 0.19),
            corner(3, 0.34, 0.348, 0.392),  # holds Les Combes and Malmedy
            corner(4, 0.74, 0.748, 0.756),  # nothing in the table
        ],
        step_m=2.0,
        distance_m=np.zeros(1),
        curvature=np.zeros(1),
        track="spa",
    )
    names = circuit_names.corner_names(g)
    assert names == {1: "La Source", 2: "Eau Rouge / Raidillon", 3: "Les Combes / Malmedy"}
    assert circuit_names.circuit_for("fn_imola")["id"] == "imola"
    assert circuit_names.circuit_for("some_mod_track") is None
    g.track = "magione"
    assert circuit_names.corner_names(g) == {}  # an empty table gives no names


# -- command --------------------------------------------------------------------------------------------
def test_corners_command_lists_numbered_corners(tmp_path, capsys):
    t = tmp_path / "tracks"
    put_line(t, "stad")
    rc = main(["corners", "--track", "stad", "--length", "1428", "--ac-dir", str(t)])
    out = capsys.readouterr().out
    assert rc == 0 and "2 corners" in out and "not verified" in out
    assert " 1  right" in out and " 2  right" in out


def test_corners_command_says_why_there_is_no_geometry(tmp_path, capsys):
    t = tmp_path / "tracks"
    t.mkdir()
    rc = main(["corners", "--track", "ghost", "--ac-dir", str(t)])
    assert rc == 1 and "no fast_lane.ai for track 'ghost'" in capsys.readouterr().err


# -- real data (only with the game installed) -------------------------------------------------------------
@pytest.mark.real_data
def test_normalized_pos_matches_the_racing_line_on_a_real_magione_race():
    res = tracks.resolve("magione", "", length_m=2455.7)
    if not res.ok:
        pytest.skip(f"Magione is not installed: {res.reason}")
    line = res.geometry.line
    with gzip.open(FIXTURES / "live_lfm_magione_pos.csv.gz", "rt") as f:
        rows = [
            (float(r["normalized_pos"]), float(r["x"]), float(r["z"])) for r in csv.DictReader(f)
        ]
    err = []
    for npos, x, z in rows:
        frac, dist = line.project(x, z, near_fraction=npos, window_m=60.0)
        if dist < 8.0:
            err.append((((npos - frac) + 0.5) % 1.0 - 0.5) * line.length_m)
    err = np.abs(np.array(err))
    assert len(err) > 1000
    assert np.percentile(err, 95) < 0.5 and err.max() < 1.0
