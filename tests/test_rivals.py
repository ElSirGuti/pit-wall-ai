from pit_wall_ai.telemetry.rivals import RivalTracker, parse_car

LAP_S = 100.0  # every car laps in 100 s: 1 % of a lap = 1 s


def car(i, dist, *, pos=0, speed=180.0, **kw):
    laps, spline = divmod(dist, 1.0)
    d = {"i": i, "n": f"P{i}", "p": pos, "l": int(laps), "s": spline, "v": speed}
    d.update(kw)
    return d


def run(offsets, *, seconds=120, pace=None, me=0, session=None, cars_fn=None):
    """Cars driving at constant speed, ``offsets`` = {index: lap fraction ahead of the start at t=0}."""
    tr = RivalTracker(track_length_m=5000)
    pace = pace or {}
    for k in range(int(seconds * 2) + 1):
        t = k * 0.5
        cars = [
            car(i, off + t / (LAP_S / pace.get(i, 1.0)), pos=0, speed=180.0)
            for i, off in offsets.items()
        ]
        for c, (i, _) in zip(cars, offsets.items()):
            c["p"] = 0
        tr.update({"t": t, "me": me, "cars": cars, "session": session or {"type": 3, "laps": 10}})
    return tr


def test_gap_to_a_car_ahead_and_behind_in_seconds():
    tr = run({0: 0.50, 1: 0.55, 2: 0.46})  # car 1 is 5 s ahead, car 2 is 4 s behind
    b = tr.board()
    assert round(b.gaps[1].gap_s, 1) == 5.0 and round(b.gaps[2].gap_s, 1) == -4.0
    assert b.on_track(True).car.index == 1 and b.on_track(False).car.index == 2


def test_the_gap_survives_the_start_line():
    tr = run({0: 0.95, 1: 0.98}, seconds=20)  # both cross the line during the run
    assert round(tr.board().gaps[1].gap_s, 1) == 3.0


def test_a_car_closing_in_is_reported():
    # the car ahead is 10 s away at first and 1 % of a lap per ... faster car 0 gains 0.2 s every 10 s
    tr = run({0: 0.40, 1: 0.50}, seconds=100, pace={0: 1.02})
    g = tr.board().gaps[1]
    assert g.gap_s < 10.0 and g.closing_s > 0.3  # shrinking, reported over the last half minute


def test_lapped_cars_are_counted_in_laps_not_seconds():
    tr = run({0: 0.10, 1: 1.30})
    g = tr.board().gaps[1]
    assert g.gap_s is None and g.laps_diff == 1


def test_race_standings_decide_who_is_ahead():
    tr = RivalTracker(track_length_m=5000)
    for k in range(10):
        t = k * 0.5
        tr.update(
            {
                "t": t,
                "me": 0,
                "session": {"type": 3},
                "cars": [
                    car(0, 0.50 + t * 0.01, pos=2),
                    car(1, 0.55 + t * 0.01, pos=1),
                    car(2, 0.45 + t * 0.01, pos=3),
                ],
            }
        )
    b = tr.board()
    assert b.ahead().car.index == 1 and b.behind().car.index == 2


def test_malformed_input_is_ignored():
    tr = RivalTracker()
    assert tr.update({"cars": "no", "t": 1}) is False
    assert tr.update({"cars": [{"x": 1}], "t": 1}) is False
    assert tr.update({"cars": [car(0, 0.1)], "t": "later"}) is False
    assert parse_car({"i": "x"}) is None
    assert tr.board() is None


def test_session_info_and_sectors_are_parsed():
    tr = RivalTracker()
    tr.update(
        {
            "t": 1.0,
            "me": 0,
            "session": {"type": 3, "laps": 0, "dur": 20, "timed": 1, "leader_laps": 7, "over": 1},
            "cars": [
                car(0, 0.1, sp=[31000, 0], ls=[30500, 29000, 33000], b=91000, a=92500, cmp="V")
            ],
        }
    )
    b = tr.board()
    assert b.session.timed and b.session.over and b.session.duration_min == 20
    assert b.me.last_splits == (30500, 29000, 33000) and b.me.compound == "V"
