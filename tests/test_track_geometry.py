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
    assert names == {
        1: "La Source",
        2: "Eau Rouge / Raidillon 1",  # two table corners on the same apex are joined
        3: "Les Combes 1 / Les Combes 2",
    }  # Malmedy (0.376) is 0.028 from the apex: not attached; 0.748 has no name on the map
    assert circuit_names.circuit_for("fn_imola")["id"] == "imola"
    assert circuit_names.circuit_for("some_mod_track") is None
    g.track = "zandvoort"
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


def test_a_complex_is_numbered_per_apex_and_each_apex_can_take_its_own_name():
    import numpy as np

    from pit_wall_ai.analysis.circuit_names import corner_names
    from pit_wall_ai.analysis.track_geometry import Corner, SubApex, TrackGeometry

    L = 6946.0
    subs = [
        SubApex(2420 / L, 2420.0, 63.0, "right", 2354.0, 2500.0),  # Les Combes
        SubApex(2626 / L, 2626.0, 90.0, "left", 2560.0, 2722.0),  # Malmedy
    ]
    complex_ = Corner(
        1,
        2354 / L,
        2420 / L,
        2722 / L,
        2354.0,
        2420.0,
        2722.0,
        63.0,
        "right",
        "medium",
        "chicane",
        subs,
    )
    single = Corner(
        2,
        3000 / L,
        3050 / L,
        3174 / L,
        3000.0,
        3050.0,
        3174.0,
        50.0,
        "right",
        "slow",
        "single",
        [SubApex(3050 / L, 3050.0, 50.0, "right")],
    )
    from pit_wall_ai.analysis.track_geometry import RacingLine

    line = RacingLine(np.array([0.0, L]), np.zeros(2), np.array([0.0, L]), True, 0.0)
    g = TrackGeometry(
        line=line,
        corners=[complex_, single],
        step_m=2.0,
        distance_m=np.zeros(1),
        curvature=np.zeros(1),
        track="spa",
    )
    assert [(c.number, round(c.apex_m)) for c in g.apexes] == [(1, 2420), (2, 2626), (3, 3050)]
    assert [c.direction for c in g.apexes] == ["right", "left", "right"]
    assert (g.apexes[0].start_m, g.apexes[0].end_m) == (2354.0, 2500.0)
    assert [c.number for c in g.corners] == [1, 2]  # the grouped view is unchanged
    assert corner_names(g) == {1: "Les Combes 1 / Les Combes 2", 2: "Malmedy", 3: "Bruxelles"}


@pytest.mark.real_data
def test_magione_has_eleven_numbered_corners_and_the_long_straight_ends_at_curvone():
    res = tracks.resolve("magione", "", length_m=2455.7)
    if not res.ok:
        pytest.skip(f"Magione is not installed: {res.reason}")
    apexes = res.geometry.apexes
    names = circuit_names.corner_names(res.geometry, "magione")
    assert len(apexes) == 11 and len(names) == 11  # the 11 numbers of the circuit map
    assert (
        names[6] == "Curvone" and 1700 < apexes[5].apex_m < 1760
    )  # braking after the long straight
    assert apexes[4].apex_m < 1000 < 1600 < apexes[5].start_m  # ~800 m without a corner before it
    assert names[1] == "Curva Traliccio" and names[11] == "Prima Esse"


# -- talking about a corner -------------------------------------------------------------------------------
def _magione_geometry():
    res = tracks.resolve("magione", "", length_m=2455.7)
    if not res.ok:
        pytest.skip(f"Magione is not installed: {res.reason}")
    return res.geometry, circuit_names.corner_names(res.geometry, "magione")


@pytest.mark.real_data
def test_corner_descriptions_answer_which_one_is_that():
    from pit_wall_ai.analysis.corner_text import describe_corner

    g, names = _magione_geometry()
    assert describe_corner(g, 11, "es", names).startswith(
        "Prima Esse, que es la curva 11, la última"
    )
    assert describe_corner(g, 6, "es", names).startswith(
        "Curvone, que es la curva 6, la primera tras la recta larga"
    )
    assert "la última antes de la recta larga" in describe_corner(g, 5, "es", names)
    assert describe_corner(g, 1, "es", names).startswith(
        "Curva Traliccio, que es la curva 1, la primera"
    )
    assert "the last one" in describe_corner(g, 11, "en", names)


@pytest.mark.real_data
def test_a_corner_can_be_found_by_name_number_or_ordinal():
    from pit_wall_ai.analysis.corner_text import find_corner

    g, names = _magione_geometry()
    assert find_corner(g, "la prima esse", names) == [11]
    assert find_corner(g, "PRIMA   ESSE?", names) == [11]
    assert find_corner(g, "curva 6", names) == [6]
    assert find_corner(g, "el curvone", names) == [6]
    assert sorted(find_corner(g, "esse", names)) == [3, 10, 11]  # ambiguous: three esses
    assert find_corner(g, "la última curva", names) == [11]
    assert find_corner(g, "monza", names) == []
    assert find_corner(g, "curva 40", names) == []


def test_corner_text_on_a_synthetic_track(stadium_line):
    from pit_wall_ai.analysis.corner_text import describe_corner, find_corner

    g = analyze_line(stadium_line, track="stadium")
    assert describe_corner(g, 2, "es").startswith("la curva 2, la última")
    assert describe_corner(g, 2, "es").endswith("a derechas")
    assert describe_corner(g, 1, "en").startswith("corner 1, the first one")
    assert find_corner(g, "curva 2") == [2]


def test_numbering_verified_flag_comes_from_the_table():
    assert circuit_names.numbering_verified("magione") is True
    assert circuit_names.numbering_verified("imola") is True  # the author's map, 2026-10-15
    assert circuit_names.numbering_verified("spa") is True
    assert circuit_names.numbering_verified("brands_hatch") is False
    assert circuit_names.numbering_verified("some_mod_track") is False


def test_monza_and_mugello_tables_cover_their_corners_in_order():
    monza = circuit_names.circuit_for("monza")["corners"]
    # the F1-style map: 11 turns; the Curva Grande is one turn with two apexes on the line
    assert [c.get("order") for c in monza] == [1, 2, 3, None, 4, 5, 6, 7, 8, 9, 10, 11]
    assert (
        monza[0]["name"].startswith("Variante del Rettifilo") and monza[-1]["name"] == "Parabolica"
    )
    mugello = circuit_names.circuit_for("mugello")["corners"]
    assert [c["order"] for c in mugello] == list(range(1, 16))
    assert [c["name"] for c in mugello][-1] == "Bucine"
    for table in (monza, mugello):
        fr = [c["apex_fraction"] for c in table]
        assert fr == sorted(fr) and 0 < fr[0] and fr[-1] < 1
    # Monza was verified with the author's map; Mugello has not been
    assert circuit_names.numbering_verified("monza") and not circuit_names.numbering_verified(
        "mugello"
    )
    assert circuit_names.table_names("monza")[11] == "Parabolica"
    assert circuit_names.table_names("mugello") is None


def test_named_straights_are_found_by_position_and_by_question():
    st = circuit_names.straights_for("ks_laguna_seca")
    assert [s.name for s in st] == ["Rahal Straight"]
    assert circuit_names.straight_at("laguna_seca", 0.6).name == "Rahal Straight"
    assert circuit_names.straight_at("laguna_seca", 0.2) is None
    for q in ("¿qué es la recta Rahal?", "where is the Rahal Straight", "Rahal"):
        assert circuit_names.find_straight("laguna_seca", q).name == "Rahal Straight"
    assert circuit_names.find_straight("laguna_seca", "la recta principal") is None
    wrap = circuit_names.Straight("Pit straight", 0.9, 0.1)  # crosses the start line
    assert wrap.contains(0.95) and wrap.contains(0.05) and not wrap.contains(0.5)
    assert abs(wrap.fraction_of_lap - 0.2) < 1e-9


def test_the_chat_says_between_which_corners_a_straight_runs():
    from types import SimpleNamespace

    from pit_wall_ai.chat import local as L
    from pit_wall_ai.chat.facts import FactSheet

    apexes = [
        SimpleNamespace(number=n, apex_fraction=f)
        for n, f in ((5, 0.43), (6, 0.542), (7, 0.659), (8, 0.685))
    ]
    track = SimpleNamespace(
        track="ks_laguna_seca", names={8: "Corkscrew 1"}, geometry=SimpleNamespace(apexes=apexes)
    )
    r = L.route("¿Qué es la recta Rahal?", track, "es")
    assert r is not None and r.intents[0].value == "straight"
    a = L.answer(r, FactSheet(lang="es"), lang="es", track=track)
    assert "Rahal Straight" in a.text and "curva 6" in a.text and "Corkscrew 1" in a.text
    assert L.route("¿cuánta gasolina queda?", track, "es").intents[0].value == "fuel"


def _geom(length, corners):
    from types import SimpleNamespace

    return SimpleNamespace(
        length_m=length,
        corners=[SimpleNamespace(start_m=a, end_m=b) for a, b in corners],
        apexes=[
            SimpleNamespace(number=i + 1, apex_fraction=((a + b) / 2) / length)
            for i, (a, b) in enumerate(corners)
        ],
    )


def test_generic_main_and_back_straights_come_from_the_gaps_between_corners():
    # a lap of 4000 m: corners at 500-600 (just after the line), 1500-1600, 2900-3000 and 3600-3700
    g = _geom(4000.0, [(500, 600), (1500, 1600), (2900, 3000), (3600, 3700)])
    got = {s.role: s for s in circuit_names.generic_straights(g)}
    assert set(got) == {"main", "back"}
    # main: from the end of the last corner (3700) over the line to the start of the first (500)
    assert got["main"].contains(0.0) and abs(got["main"].fraction_of_lap * 4000 - 800) < 1
    # back: the longest of the others, 1600 -> 2900 (1300 m)
    assert abs(got["back"].fraction_of_lap * 4000 - 1300) < 1
    assert circuit_names.straight_label(got["main"], "es") == "la recta principal"
    assert circuit_names.straight_label(got["back"], "de") == "die Gegengerade"


def test_a_short_gap_is_not_a_straight_and_a_named_one_hides_the_generic_one():
    short = _geom(4000.0, [(100, 900), (1000, 2000), (2100, 3000), (3100, 3900)])
    assert circuit_names.generic_straights(short) == []  # every gap is 100 to 200 m
    g = _geom(
        3559.0, [(100, 200), (450, 650), (1200, 1300), (1900, 2000), (2300, 2400), (3000, 3100)]
    )
    # laguna_seca has the Rahal Straight (0.542 -> 0.685 = 1929 m -> 2438 m): a generic back straight inside it is dropped
    roles = [s.role for s in circuit_names.all_straights("laguna_seca", g)]
    assert roles[0] == "named" and roles.count("back") <= 1


def test_the_chat_finds_the_main_and_the_back_straight():
    from types import SimpleNamespace

    from pit_wall_ai.chat import local as L
    from pit_wall_ai.chat.facts import FactSheet

    g = _geom(4000.0, [(500, 600), (1500, 1600), (2900, 3000), (3600, 3700)])
    track = SimpleNamespace(track="some_mod_track", names={}, geometry=g)
    for q, role in (
        ("¿cuál es la recta principal?", "main"),
        ("¿dónde está la recta trasera?", "back"),
        ("what is the back straight", "back"),
        ("Was ist die Gegengerade?", "back"),
    ):
        lang = "de" if "Gegen" in q else ("en" if q.startswith("what") else "es")
        r = L.route(q, track, lang)
        assert r is not None and r.intents[0].value == "straight", q
        assert f"straight:{role}:" in r.flags[0]
    a = L.answer(
        L.route("¿cuál es la recta principal?", track, "es"),
        FactSheet(lang="es"),
        lang="es",
        track=track,
    )
    assert a.text.startswith("La recta principal es la recta que va de la curva 4 a la curva 1")


def test_monzas_straights_are_named_and_answer_to_the_generic_words():
    st = {s.role: s for s in circuit_names.straights_for("monza")}
    assert st["main"].name == "Rettifilo Tribune" and st["back"].name == "Curva del Serraglio"
    assert st["main"].contains(0.0)  # the pit straight crosses the start line
    for q, name in (
        ("¿cuál es la recta trasera?", "Curva del Serraglio"),
        ("what is the back straight", "Curva del Serraglio"),
        ("¿qué es la recta principal?", "Rettifilo Tribune"),
        ("¿qué es la Curva del Serraglio?", "Curva del Serraglio"),
    ):
        assert circuit_names.find_straight("monza", q).name == name, q
    # no generic duplicate next to the named ones
    g = _geom(
        5759.0, [(900, 1000), (1100, 1200), (2000, 2100), (3500, 3600), (3700, 3800), (5100, 5200)]
    )
    roles = [s.role for s in circuit_names.all_straights("monza", g)]
    assert roles.count("main") == 1 and roles.count("back") == 1
    assert circuit_names.straight_label(st["back"], "es") == "Curva del Serraglio"


def test_the_map_svg_has_the_line_the_numbers_and_the_names(tmp_path):
    from types import SimpleNamespace

    from pit_wall_ai.analysis.render import render_svg, write_svg

    t = np.linspace(0, 2 * np.pi, 200, endpoint=False)
    x, z = 300 * np.cos(t), 200 * np.sin(t)
    s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(z)))])
    line = SimpleNamespace(x=x, z=z, s=s, length_m=float(s[-1]))
    apexes = [
        SimpleNamespace(number=1, apex_fraction=0.25),
        SimpleNamespace(number=2, apex_fraction=0.75),
    ]
    g = SimpleNamespace(line=line, apexes=apexes)
    st = circuit_names.Straight("main", 0.9, 0.1, "main")
    svg = render_svg(g, {1: "Alpha", 2: "Beta <&>"}, [st], title="Test & circuit")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert ">1<" in svg and ">2<" in svg and "Alpha" in svg
    assert "Beta &lt;&amp;&gt;" in svg and "Test &amp; circuit" in svg  # escaped
    assert write_svg(tmp_path / "a" / "m.svg", g, {}, ()).is_file()


def test_a_map_numbering_is_kept_apart_from_the_apex_numbering():
    from types import SimpleNamespace

    apexes = [
        SimpleNamespace(number=i + 1, apex_fraction=f)
        for i, f in enumerate((0.143, 0.156, 0.179, 0.271, 0.288, 0.349, 0.481, 0.682, 0.691))
    ]
    g = SimpleNamespace(apexes=apexes, track="imola")
    m = circuit_names.official_map(g, "imola")
    # the author's map: Tamburello 2-4, Villeneuve 5-6, Tosa 7, Piratella 9, Variante Alta 14-15
    assert m == {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 9, 8: 14, 9: 15}
    circuit_names.attach_official(g, "imola")
    assert circuit_names.official_of(g, 8) == 14 and circuit_names.official_of(g, 7) == 9
    assert (
        circuit_names.apex_of(g, 14) == 8 and circuit_names.apex_of(g, 8) is None
    )  # 8 is a kink with no apex
    # a circuit without a map list keeps the apex numbers
    h = SimpleNamespace(apexes=apexes, track="zandvoort")
    circuit_names.attach_official(h, "zandvoort")
    assert circuit_names.official_of(h, 7) == 7 and circuit_names.apex_of(h, 7) == 7


def test_the_chat_speaks_the_map_numbers_and_understands_them():
    from types import SimpleNamespace

    from pit_wall_ai.analysis.corner_text import describe_corner, find_corner

    class G(SimpleNamespace):
        pass

    apexes = [
        SimpleNamespace(
            number=i + 1,
            apex_fraction=f,
            direction="left",
            apex_m=f * 4800,
            start_m=f * 4800 - 20,
            end_m=f * 4800 + 20,
            kind="single",
        )
        for i, f in enumerate((0.143, 0.156, 0.179, 0.271, 0.288, 0.349, 0.481, 0.682, 0.691))
    ]
    g = G(apexes=apexes, track="imola", length_m=4800.0)
    circuit_names.attach_official(g, "imola")
    names = {8: "Variante Alta 1"}
    assert find_corner(g, "curva 14", names) == [8]  # the driver says the map's number
    assert find_corner(g, "curva 8", names) == []  # a number the map has no apex for
    text = describe_corner(g, 8, "es", names)
    assert text.startswith("Variante Alta 1, que es la curva 14")
    assert "curva 8" not in text


def test_imola_is_verified_with_the_numbers_of_the_authors_map():
    assert circuit_names.numbering_verified("imola")
    names = circuit_names.table_names("imola")
    assert (
        names[2].startswith("Variante Tamburello")
        and names[7] == "Tosa"
        and names[9] == "Piratella"
    )
    assert names[17] == "Rivazza 1" and names[18] == "Rivazza 2"
    assert (
        1 not in names and 8 not in names and 10 not in names and 19 not in names
    )  # no names there


def test_layout_aware_table():
    from pit_wall_ai.analysis import circuit_names as CN

    gp = CN.circuit_for_layout("ks_red_bull_ring", "layout_gp", 4318)
    assert gp and gp["corners"]
    # the national layout is 2.3 km: the GP fractions do not apply, so no names rather than wrong ones
    nat = CN.circuit_for_layout("ks_red_bull_ring", "layout_national", 2317)
    assert (
        nat
        and [e["name"] for e in nat["corners"]][0] == "Niki Lauda Kurve"
        and nat["straights"] == []
    )
    # an unknown layout of the same circuit with a different length gets nothing
    assert CN.circuit_for_layout("ks_red_bull_ring", "layout_xyz", 2336) is None
    assert CN.numbering_verified("ks_red_bull_ring", "layout_national", 2317) is True
    assert CN.numbering_verified("ks_red_bull_ring", "layout_gp", 4318) is True


def test_reverse_layout_reuses_names_in_reverse_order():
    from pit_wall_ai.analysis import circuit_names as CN

    base = CN.circuit_for("monza")
    rev = CN.circuit_for_layout("monza", "monza_reverse", base["length_m"])
    assert rev and not rev["numbering_verified"]
    assert [e["name"] for e in rev["corners"]] == [e["name"] for e in reversed(base["corners"])]
    f0 = base["corners"][0]["apex_fraction"]
    assert abs(rev["corners"][-1]["apex_fraction"] - (1 - f0)) < 1e-3
    assert CN.table_names("monza", "monza_reverse", base["length_m"]) is None
