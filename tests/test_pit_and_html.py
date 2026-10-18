import argparse

from pit_wall_ai.analysis.cli_plan import cmd_plan
from pit_wall_ai.analysis.debrief import Debrief, pit_block, to_markdown
from pit_wall_ai.analysis.debrief_html import lap_chart, loss_chart, to_html
from pit_wall_ai.analysis.laps import LapRecord
from pit_wall_ai.analysis.pit_loss import measure, median_loss, rank_stop_laps
from pit_wall_ai.analysis.sessions_log import SessionStore, pit_loss_for, summary_of
from pit_wall_ai.config import AppConfig


def lap(n, ms, **kw):
    return LapRecord(number=n, time_ms=ms, coverage=1.0, **kw)


LAPS = [
    lap(1, 80000),
    lap(2, 79000),
    lap(3, 79500, in_lap=True),
    lap(4, 101500, out_lap=True),
    lap(5, 79200),
    lap(6, 79100),
]


def test_a_stop_costs_the_in_and_out_laps_over_two_typical_laps():
    stops = measure(LAPS)
    assert len(stops) == 1 and stops[0].in_lap == 3 and stops[0].out_lap == 4
    # typical lap = median of the clean laps (80000, 79000, 79200, 79100) = 79150; (79500 + 101500) - 2 x 79150
    assert stops[0].loss_s == 22.7 and median_loss(stops) == 22.7
    assert (
        measure([lap(1, 80000), lap(2, 79500, in_lap=True), lap(3, 99000, out_lap=True)]) == []
    )  # no reference


def test_the_debrief_has_the_pit_block_and_the_sessions_file_keeps_the_loss(tmp_path):
    block = pit_block(LAPS)
    assert block["median_loss_s"] == 22.7 and block["stops"][0]["in_lap"] == 3
    d = Debrief(
        facts={
            "result": {
                "session": "race",
                "track": "magione",
                "layout": "",
                "car": "gt4",
                "laps": 6,
            },
            "pace": {"best_ms": 79000, "best_lap": 2, "valid_laps": 4},
            "pit": block,
        }
    )
    assert "Paradas en boxes" in to_markdown(d, "es") and "22,7 s" in to_markdown(d, "es")
    store = SessionStore(tmp_path / "s.jsonl")
    store.add(summary_of(d))
    assert (
        pit_loss_for(store, "magione", "gt4") == 22.7
        and pit_loss_for(store, "monza", "gt4") is None
    )


def test_the_best_lap_to_stop_is_the_middle_one_and_the_window_limits_it():
    ranked = rank_stop_laps(20, 80.0, 0.05, 22.0)
    assert ranked[0].delta_s == 0.0 and ranked[0].stop_lap in (9, 10, 11)
    assert ranked[-1].delta_s > ranked[0].delta_s
    inside = rank_stop_laps(20, 80.0, 0.05, 22.0, window=(4, 6))
    assert {o.stop_lap for o in inside} == {4, 5, 6} and inside[0].stop_lap == 6
    assert rank_stop_laps(20, 80.0, 0.05, 22.0, window=(30, 40)) == []
    assert (
        rank_stop_laps(20, 80.0, 0.05, 22.0, max_stint=9) == []
    )  # 20 laps cannot be done in two 9-lap stints


def test_the_html_has_the_report_and_two_inline_charts_and_no_scripts():
    d = Debrief(
        facts={
            "result": {"session": "race", "track": "magione", "car": "x", "laps": 4},
            "pace": {"best_ms": 77737, "best_lap": 3, "valid_laps": 3},
            "time_loss": {
                "corners": [
                    {
                        "corner": 7,
                        "apex": 7,
                        "name": "",
                        "mean_loss_s": 0.56,
                        "laps": 7,
                        "brake_earlier_m": None,
                    }
                ]
            },
        },
        series={
            "laps": [
                {"n": i + 1, "ms": ms, "valid": v, "compound": "H"}
                for i, (ms, v) in enumerate(
                    [(80000, True), (78500, True), (77737, True), (83000, False)]
                )
            ]
        },
    )
    h = to_html(d, "es")
    assert h.startswith("<!doctype html>") and h.count("<svg") == 2 and "<script" not in h
    assert "Resumen de la sesión" in h and "0,56 s" in h and "1:17,737" in h
    assert lap_chart([{"n": 1, "ms": 80000, "valid": True}], "es") == ""  # one lap is not a chart
    assert loss_chart([], "es") == ""


def test_the_plan_command_prints_fuel_stops_and_the_ranking(capsys):
    a = argparse.Namespace(
        laps=20,
        minutes=None,
        lap_time=80.0,
        fuel_per_lap=2.0,
        spread=0.05,
        profile_laps=5,
        tank=25.0,
        stops=None,
        window="8-12",
        deg=0.05,
        pit_loss=22.0,
    )
    lines: list[str] = []
    assert cmd_plan(AppConfig(), a, out=lines.append) == 0
    text = "\n".join(lines)
    assert (
        "race: 20 laps" in text
        and "1 stop(s): in on lap 10" in text
        and "best laps to come in" in text
    )
    a.laps = None
    assert cmd_plan(AppConfig(), a, out=lines.append) == 2
