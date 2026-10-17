from types import SimpleNamespace

from pit_wall_ai.analysis.debrief import (
    Debrief,
    incidents_block,
    pace_block,
    penalties_block,
    result_block,
    stints_block,
    to_markdown,
)
from pit_wall_ai.analysis.laps import LapRecord
from pit_wall_ai.telemetry.snapshot import Snapshot


def lap(n, ms, sectors=(), **kw):
    return LapRecord(
        number=n,
        time_ms=ms,
        sectors_ms=sectors,
        coverage=1.0,
        compound="S",
        fuel_start=20 - n,
        fuel_end=19 - n,
        **kw,
    )


LAPS = [
    lap(1, 88000, (50000, 38000)),  # a standing start: slow, valid
    lap(2, 78000, (44500, 33500)),
    lap(3, 78500, (44000, 34500)),
    lap(4, 77800, (44300, 33500)),
    lap(5, 78200, (44100, 34100), suspect=True),
]


def test_pace_has_the_best_lap_the_ideal_lap_and_a_consistency_that_ignores_slow_laps():
    p = pace_block(LAPS)
    assert p["best_ms"] == 77800 and p["best_lap"] == 4 and p["valid_laps"] == 4
    assert p["ideal_ms"] == 44000 + 33500 and p["ideal_gap_ms"] == 77800 - 77500
    assert p["consistency_laps"] == 3  # the standing-start lap is not pace
    assert pace_block([]) is None


def test_the_trend_leaves_out_the_slow_laps_and_the_fuel_is_per_lap():
    st = stints_block(LAPS)
    assert st["stints"][0]["laps"] == 5 and st["stints"][0]["trend_laps"] == 3
    assert (
        abs(st["stints"][0]["trend_s_per_lap"]) < 0.5
    )  # the 88 s start would have said -4 s per lap
    assert st["fuel_per_lap"] == 1.0


def test_incidents_and_blocks_with_nothing_to_say_are_left_out():
    assert incidents_block(LAPS) == {"laps_with_incidents": [5], "laps": 5}
    assert incidents_block([lap(2, 78000)]) is None


def _snap(t, **kw):
    return Snapshot(t=t, source="test", packet_id=int(t * 10), **kw)


def test_penalties_flags_and_contacts_come_from_the_samples():
    snaps = [
        _snap(0.0),
        _snap(1.0, penalty_time=10.0, flags=6),
        _snap(2.0, penalty_time=9.0, flags=6, car_damage=(0.0, 0.0, 12.0, 0.0, 12.0)),
        _snap(3.0, flags=5, car_damage=(0.0, 0.0, 12.0, 0.0, 12.0)),
    ]
    pe = penalties_block(snaps)
    assert pe["max_penalty_s"] == 10.0 and pe["flags"] == [
        "penalty"
    ]  # the chequered flag is not a finding
    assert pe["contacts"] == 1 and pe["worst_contact"] == 12.0
    assert penalties_block([_snap(0.0), _snap(1.0)]) is None


def test_the_result_names_the_session_and_the_places_gained():
    snaps = [_snap(0.0, position=11), _snap(1.0, position=10)]
    static = SimpleNamespace(
        track="magione", track_configuration="", car_model="maserati", num_cars=16
    )
    r = result_block(snaps, LAPS, static)
    assert r["position_start"] == 11 and r["position_end"] == 10 and r["laps"] == 5


def test_the_report_is_in_spanish_with_decimal_commas_and_says_what_it_could_not_do():
    d = Debrief(
        facts={
            "result": {
                "session": "race",
                "track": "magione",
                "car": "x",
                "laps": 8,
                "position_start": 11,
                "position_end": 10,
            },
            "pace": {
                "best_ms": 77737,
                "best_lap": 5,
                "valid_laps": 6,
                "ideal_ms": 77737,
                "ideal_gap_ms": 0,
            },
            "time_loss": {
                "corners": [
                    {
                        "corner": 7,
                        "apex": 7,
                        "name": "Curva 7",
                        "mean_loss_s": 0.56,
                        "laps": 7,
                        "brake_earlier_m": None,
                    }
                ]
            },
            "penalties": {
                "max_penalty_s": 10.0,
                "flags": ["penalty"],
                "contacts": 1,
                "worst_contact": 30.0,
            },
        },
        notes=["tyres"],
    )
    md = to_markdown(d, "es")
    assert "1:17,737" in md and "0,56 s" in md and "Salida 11.º, llegada 10.º" in md
    assert "* curva 7: 0,56 s" in md and "Curva 7 (curva 7)" not in md  # the placeholder is not a name
    assert "de penalización (blanquinegra)" in md and "1 contacto;" in md
    assert "neumáticos: faltan datos" in md
    assert "Best lap 1:17.737" in to_markdown(d, "en") or "1:17.737" in to_markdown(d, "en")
