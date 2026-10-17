from types import SimpleNamespace

from pit_wall_ai.analysis.checklist import Checklist
from pit_wall_ai.analysis.checklist_edit import apply_ops, edit_with_model, parse_ops
from pit_wall_ai.analysis.laps import LapRecord


def lap(n, compound, fuel_start, *, valid=True):
    return LapRecord(
        number=n,
        time_ms=80000,
        fuel_start=fuel_start,
        fuel_end=fuel_start - 1.3,
        compound=compound,
        coverage=1.0,
        suspect=not valid,
    )


def stint(c, compound, laps, fuel=None):
    p = {"compound": compound, "laps": laps}
    if fuel is not None:
        p["fuel_l"] = fuel
    return c.apply({"op": "add", "kind": "stint", "params": p})


def test_add_remove_skip_replace_reorder_and_rules_are_validated_operations():
    c = Checklist()
    assert stint(c, "Medium", 5, 20).ok and stint(c, "Soft", 3).ok
    assert [i.id for i in c.visible()] == ["C1", "C2"]
    assert c.apply({"op": "replace", "id": "C1", "params": {"compound": "Hard"}}).ok
    assert c.get("C1").params["compound"] == "Hard" and c.get("C1").params["laps"] == 5
    assert c.apply({"op": "reorder", "id": "C2", "position": 0}).ok and c.visible()[0].id == "C2"
    assert (
        c.apply({"op": "skip", "id": "C1", "reason": "no time"}).ok
        and c.get("C1").status == "skipped"
    )
    assert c.apply({"op": "remove", "id": "C2", "reason": "dry"}).ok and c.get("C2") is None
    assert c.apply({"op": "set_rule", "rule": "mandatory_stop", "value": "one"}).ok
    assert c.rules == {"mandatory_stop": "one"}


def test_invalid_operations_are_refused_with_a_reason_and_change_nothing():
    c = Checklist()
    stint(c, "Medium", 5)
    before = c.render()
    for bad, why in (
        ({"op": "teleport"}, "unknown operation"),
        ({"op": "add", "kind": "party"}, "kind must be"),
        ({"op": "add", "kind": "stint", "params": {"compound": "x"}}, "needs laps"),
        ({"op": "add", "kind": "stint", "params": {"laps": 500}}, "between 1 and 200"),
        (
            {"op": "add", "kind": "stint", "params": {"laps": 3, "colour": "red"}},
            "unknown parameter",
        ),
        ({"op": "remove", "id": "C9"}, "no item"),
        ({"op": "set_rule", "rule": "mandatory_stop", "value": "two"}, "none, one or n"),
        ({"op": "reorder", "id": "C1", "position": 7}, "valid index"),
    ):
        r = c.apply(bad)
        assert not r.ok and why in r.message, (bad, r.message)
    assert c.render() == before


def test_every_change_can_be_undone():
    c = Checklist()
    stint(c, "Medium", 5)
    c.apply({"op": "remove", "id": "C1"})
    assert c.get("C1") is None
    assert c.undo().ok and c.get("C1") is not None
    assert c.undo().ok and not c.visible()  # the add itself
    assert not c.undo().ok


def test_a_wet_tyre_on_a_dry_track_is_flagged_but_the_order_stands():
    c = Checklist()
    r = c.apply(
        {"op": "add", "kind": "stint", "params": {"compound": "Wet", "laps": 3}}, conditions="dry"
    )
    assert r.ok and r.warnings and "wet tyre" in r.warnings[0] and c.get("C1")
    r = c.apply(
        {"op": "add", "kind": "stint", "params": {"compound": "Medium", "laps": 3}},
        conditions="wet",
    )
    assert r.ok and "not a wet tyre" in r.warnings[0]
    assert not c.apply(
        {"op": "add", "kind": "stint", "params": {"compound": "Wet", "laps": 3}}
    ).warnings


def test_items_tick_themselves_in_order_from_the_laps_driven():
    c = Checklist()
    stint(c, "Medium", 3, 20)
    stint(c, "Soft", 2, 15)
    laps = [lap(1, "Medium", 20.0), lap(2, "Medium", 18.7), lap(3, "Medium", 17.4)]
    assert c.autotick(laps) == ["C1 done (laps 1-3)"]
    laps += [lap(4, "Soft", 15.2), lap(5, "Soft", 13.9)]
    assert c.autotick(laps) == ["C2 done (laps 4-5)"]
    assert [i.status for i in c.visible()] == ["done", "done"]


def test_a_partial_stint_is_active_and_a_wrong_fuel_load_is_shown_not_guessed():
    c = Checklist()
    stint(c, "Medium", 4, 20)
    assert c.autotick([lap(1, "Medium", 20.0), lap(2, "Medium", 18.7)]) == ["C1 active (2/4 laps)"]
    d = Checklist()
    stint(d, "Medium", 2, 20)
    assert d.autotick([lap(1, "Medium", 8.0), lap(2, "Medium", 6.7)]) == []
    assert d.get("C1").status == "pending" and "not 20 L" in d.get("C1").note


def test_an_incident_lap_breaks_the_run():
    c = Checklist()
    stint(c, "Medium", 3)
    laps = [lap(1, "Medium", 20.0), lap(2, "Medium", 18.7, valid=False), lap(3, "Medium", 17.4)]
    assert c.autotick(laps) == ["C1 active (1/3 laps)"]


def test_the_list_survives_a_file(tmp_path):
    c = Checklist()
    stint(c, "Medium", 3, 20)
    c.apply({"op": "set_rule", "rule": "window", "value": [8, 12]})
    c.save(tmp_path / "c.json")
    d = Checklist.load(tmp_path / "c.json")
    assert d.render() == c.render() and d.rules["window"] == [8, 12]
    assert Checklist.load(tmp_path / "missing.json").visible() == []


class Client:
    def __init__(self, text):
        self.text = text

    def ask(self, request, urgent=False):
        assert "items" in request.context  # the model sees the checklist and its ids
        return SimpleNamespace(text=self.text)


def test_the_model_edits_through_operations_and_a_bad_set_is_all_or_nothing():
    c = Checklist()
    stint(c, "Wet", 3)
    ok, msg = edit_with_model(
        Client('```json\n[{"op":"remove","id":"C1","reason":"está seco"}]\n```'),
        c,
        "quita lo de mojado, está seco",
    )
    assert ok and "- C1" in msg and c.get("C1") is None
    ok, msg = edit_with_model(
        Client(
            '[{"op":"add","kind":"quali_sim","params":{"fuel_l":15}},{"op":"remove","id":"C77"}]'
        ),
        c,
        "x",
    )
    assert not ok and "no item" in msg and not c.visible()  # the add was undone
    assert not edit_with_model(Client("no json"), c, "x")[0]
    assert not edit_with_model(Client("[]"), c, "x")[0]
    assert not edit_with_model(None, c, "x")[0]


def test_parse_ops_finds_the_array_in_a_fence_and_refuses_the_rest():
    assert parse_ops('texto [{"op":"skip","id":"C1"}] fin') == [{"op": "skip", "id": "C1"}]
    assert parse_ops("[1, 2]") is None and parse_ops("{}") is None


def test_apply_ops_reports_a_guard_warning():
    c = Checklist()
    ok, msg = apply_ops(
        c,
        [{"op": "add", "kind": "stint", "params": {"compound": "Rain", "laps": 2}}],
        conditions="dry",
    )
    assert ok and "Aviso" in msg and "tu orden manda" in msg
