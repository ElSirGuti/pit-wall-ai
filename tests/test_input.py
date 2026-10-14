"""Buttons: bindings, hold/tap/chord logic with a fake backend, the joystick poller, the real Windows hooks."""

from __future__ import annotations

import sys
import threading
import time

import pytest
import yaml

from pit_wall_ai.config import AppConfig, load_config
from pit_wall_ai.input.actions import ActionManager, BindingError, parse_bindings
from pit_wall_ai.input.backends import FakeBackend, WindowsHookBackend, WinMMJoystickBackend
from pit_wall_ai.input.cli_input import bind, capture_binding
from pit_wall_ai.input.model import Binding, InputEvent, InputId, vk_name

G29 = "046d:c24f"
PAD7 = InputId("joy", "7", G29, "G29")
PAD8 = InputId("joy", "8", G29, "G29")
F9 = InputId("key", "F9")
CTRL = InputId("key", "LCTRL")
X2 = InputId("mouse", "x2")
LEFT = InputId("mouse", "left")


def ev(i: InputId, pressed: bool) -> InputEvent:
    return InputEvent(i, pressed, 0.0)


def manager(spec: dict):
    calls: list[tuple[str, str]] = []
    m = ActionManager(parse_bindings(spec), lambda a, p: calls.append((a, p)))
    return m, calls


# -- model ---------------------------------------------------------------------------------------------------
def test_an_input_is_identified_by_kind_device_and_code_not_by_its_name():
    assert InputId("joy", "7", G29, "G29") == InputId("joy", "7", G29, "another name")
    assert InputId("joy", "7", G29) != InputId("joy", "8", G29)
    assert InputId("joy", "7", G29) != InputId("joy", "7", "1234:5678")
    with pytest.raises(ValueError):
        InputId("touchpad", "1")


def test_a_binding_round_trips_through_its_yaml_spec():
    b = Binding(F9, (CTRL,))
    spec = b.to_spec()
    assert spec == {"kind": "key", "key": "F9", "with": [{"kind": "key", "key": "LCTRL"}]}
    assert Binding.from_spec(yaml.safe_load(yaml.safe_dump(spec))) == b
    joy = Binding.from_spec({"kind": "joy", "device": G29, "button": 7, "name": "G29"})
    assert joy.input == PAD7 and joy.to_spec()["button"] == 7
    assert Binding.from_spec({"kind": "mouse", "button": "X2"}).input == X2


def test_key_names():
    assert vk_name(0x78) == "F9" and vk_name(0x20) == "SPACE" and vk_name(0x41) == "A"
    assert vk_name(0xA2) == "LCTRL" and vk_name(0xFF) == "VKFF"


# -- parsing and conflicts ----------------------------------------------------------------------------------------
def test_unknown_actions_and_inputs_are_rejected_with_a_reason():
    with pytest.raises(BindingError, match="unknown action"):
        parse_bindings({"launch_missiles": [{"kind": "key", "key": "F1"}]})
    with pytest.raises(BindingError, match="invalid binding"):
        parse_bindings({"push_to_talk": [{"kind": "touchpad"}]})
    with pytest.raises(BindingError, match="invalid binding"):
        parse_bindings({"push_to_talk": [{"kind": "mouse", "button": "tilt"}]})


def test_the_same_input_on_two_actions_is_a_conflict():
    spec = {
        "push_to_talk": [{"kind": "key", "key": "F9"}],
        "cancel": [{"kind": "key", "key": "F9"}],
    }
    with pytest.raises(BindingError, match="bound to both"):
        parse_bindings(spec)


def test_the_same_key_with_a_modifier_is_a_different_binding():
    spec = {
        "push_to_talk": [{"kind": "key", "key": "F9"}],
        "cancel": [{"kind": "key", "key": "F9", "with": [{"kind": "key", "key": "LCTRL"}]}],
    }
    assert set(parse_bindings(spec)) == {"push_to_talk", "cancel"}


# -- hold, tap, chords ---------------------------------------------------------------------------------------------
def test_push_to_talk_is_held_from_press_to_release():
    m, calls = manager({"push_to_talk": [{"kind": "joy", "device": G29, "button": 7}]})
    m.feed(ev(PAD7, True))
    assert calls == [("push_to_talk", "press")] and m.holding("push_to_talk")
    m.feed(ev(PAD7, False))
    assert calls == [("push_to_talk", "press"), ("push_to_talk", "release")]
    assert not m.holding("push_to_talk")


def test_keyboard_auto_repeat_does_not_press_again():
    m, calls = manager({"push_to_talk": [{"kind": "key", "key": "F9"}]})
    for _ in range(5):
        m.feed(ev(F9, True))  # the OS repeats the key down while it is held
    m.feed(ev(F9, False))
    assert calls == [("push_to_talk", "press"), ("push_to_talk", "release")]


def test_two_bindings_of_one_hold_action_release_only_when_both_are_up():
    m, calls = manager(
        {
            "push_to_talk": [
                {"kind": "joy", "device": G29, "button": 7},
                {"kind": "key", "key": "F9"},
            ]
        }
    )
    m.feed(ev(PAD7, True))
    m.feed(ev(F9, True))
    m.feed(ev(PAD7, False))
    assert calls == [("push_to_talk", "press")]  # the key still holds it
    m.feed(ev(F9, False))
    assert calls[-1] == ("push_to_talk", "release") and len(calls) == 2


def test_tap_actions_fire_once_per_press():
    m, calls = manager({"cancel": [{"kind": "mouse", "button": "x2"}]})
    m.feed(ev(X2, True))
    m.feed(ev(X2, False))
    m.feed(ev(X2, True))
    assert calls == [("cancel", "tap"), ("cancel", "tap")]


def test_a_chord_needs_its_modifier_down():
    m, calls = manager(
        {"cancel": [{"kind": "key", "key": "F9", "with": [{"kind": "key", "key": "LCTRL"}]}]}
    )
    m.feed(ev(F9, True))
    m.feed(ev(F9, False))
    assert calls == []  # without Ctrl: nothing
    m.feed(ev(CTRL, True))
    m.feed(ev(F9, True))
    assert calls == [("cancel", "tap")]


def test_releasing_the_modifier_of_a_held_chord_releases_the_action():
    m, calls = manager(
        {"push_to_talk": [{"kind": "key", "key": "F9", "with": [{"kind": "key", "key": "LCTRL"}]}]}
    )
    m.feed(ev(CTRL, True))
    m.feed(ev(F9, True))
    m.feed(ev(CTRL, False))
    assert calls == [("push_to_talk", "press"), ("push_to_talk", "release")]


def test_unbound_inputs_do_nothing_and_a_broken_callback_does_not_kill_the_manager():
    m = ActionManager(
        parse_bindings({"push_to_talk": [{"kind": "key", "key": "F9"}]}),
        lambda a, p: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    m.feed(ev(InputId("key", "F1"), True))
    m.feed(ev(F9, True))  # the callback raises: logged, not propagated
    m.feed(ev(F9, False))


def test_devices_going_away_release_what_was_held():
    m, calls = manager({"push_to_talk": [{"kind": "joy", "device": G29, "button": 7}]})
    m.feed(ev(PAD7, True))
    m.release_all()
    assert calls[-1] == ("push_to_talk", "release") and not m.holding("push_to_talk")


# -- the joystick poller --------------------------------------------------------------------------------------------
class FakeJoyApi:
    def __init__(self):
        self.state: dict[int, int | None] = {0: 0, 2: 0}
        self.buttons = {0: 25, 2: 0}

    def num_devs(self):
        return 4

    def poll(self, dev):
        return self.state.get(dev)

    def caps(self, dev):
        return G29, self.buttons.get(dev, 0)

    def product_name(self, device_id):
        return "Logitech G29"


def run_poller(api, script, *, wait=0.05):
    be = WinMMJoystickBackend(poll_hz=1000, api=api)
    got: list[InputEvent] = []
    be.start(got.append)
    try:
        time.sleep(wait)
        for step in script:
            step()
            time.sleep(wait)
    finally:
        be.stop()
    return got


def test_the_poller_turns_button_bits_into_press_and_release_events():
    api = FakeJoyApi()
    got = run_poller(
        api, [lambda: api.state.__setitem__(0, 1 << 6), lambda: api.state.__setitem__(0, 0)]
    )
    assert [(e.input.code, e.pressed) for e in got] == [("7", True), ("7", False)]
    assert (
        got[0].input.device == G29
        and got[0].input.name == "Logitech G29"
        and got[0].input.kind == "joy"
    )


def test_what_is_already_down_when_it_starts_is_not_a_press():
    api = FakeJoyApi()
    api.state[0] = 1 << 2
    got = run_poller(api, [lambda: api.state.__setitem__(0, 0)])
    assert [(e.input.code, e.pressed) for e in got] == [
        ("3", False)
    ]  # only the release is an event


def test_a_device_without_buttons_is_ignored():
    api = FakeJoyApi()
    got = run_poller(api, [lambda: api.state.__setitem__(2, 0xFF)])
    assert got == []


def test_unplugging_releases_the_held_buttons():
    api = FakeJoyApi()
    got = run_poller(
        api, [lambda: api.state.__setitem__(0, 1 << 6), lambda: api.state.__setitem__(0, None)]
    )
    assert [(e.input.code, e.pressed) for e in got] == [("7", True), ("7", False)]


def test_several_buttons_at_once_are_separate_events():
    api = FakeJoyApi()
    got = run_poller(api, [lambda: api.state.__setitem__(0, (1 << 0) | (1 << 9))])
    assert sorted(e.input.code for e in got) == ["1", "10"] and all(e.pressed for e in got)


# -- bind -----------------------------------------------------------------------------------------------------------
def feed_later(be: FakeBackend, events, delay=0.05):
    def run():
        time.sleep(delay)
        for e in events:
            be.press(e) if e[1] else be.release(e[0])

    threading.Thread(target=run, daemon=True).start()


def feed(be: FakeBackend, *steps):
    """steps: (InputId, pressed)"""

    def run():
        time.sleep(0.1)
        for i, pressed in steps:
            (be.press if pressed else be.release)(i)
            time.sleep(0.02)

    threading.Thread(target=run, daemon=True).start()


def test_capture_takes_the_first_press_of_any_device():
    be = FakeBackend()
    feed(be, (PAD7, True), (PAD7, False))
    b = capture_binding(be, timeout_s=3)
    assert b == Binding(PAD7)


def test_capture_refuses_a_plain_click_and_waits_for_something_else():
    be = FakeBackend()
    lines: list[str] = []
    feed(be, (LEFT, True), (LEFT, False), (X2, True))
    b = capture_binding(be, timeout_s=3, out=lines.append)
    assert b == Binding(X2) and any("plain click" in s for s in lines)


def test_capture_turns_held_modifiers_into_a_chord():
    be = FakeBackend()
    feed(be, (CTRL, True), (F9, True))
    b = capture_binding(be, timeout_s=3)
    assert b == Binding(F9, (CTRL,))


def test_capture_times_out_without_a_press():
    assert capture_binding(FakeBackend(), timeout_s=0.3) is None


def test_bind_saves_to_the_bindings_file_and_the_config_reads_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config" / "bindings.yaml"
    be = FakeBackend()
    feed(be, (PAD7, True))
    lines: list[str] = []
    rc = bind("push_to_talk", AppConfig(), be, path=path, timeout_s=3, out=lines.append)
    assert rc == 0 and path.is_file()
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["input"]["bindings"]["push_to_talk"] == [PAD7.to_spec()]
    cfg = load_config()  # picks config/bindings.yaml up from the working directory
    assert cfg.input.bindings["push_to_talk"][0]["button"] == 7
    # a second binding for the same action is added, not substituted
    be2 = FakeBackend()
    feed(be2, (F9, True))
    bind("push_to_talk", cfg, be2, path=path, timeout_s=3, out=lines.append)
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(saved["input"]["bindings"]["push_to_talk"]) == 2


def test_bind_refuses_an_input_already_used_by_another_action(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config" / "bindings.yaml"
    cfg = AppConfig.model_validate({"input": {"bindings": {"cancel": [PAD7.to_spec()]}}})
    be = FakeBackend()
    feed(be, (PAD7, True))
    lines: list[str] = []
    rc = bind("push_to_talk", cfg, be, path=path, timeout_s=3, out=lines.append)
    assert rc == 1 and any("bound to both" in s for s in lines) and not path.exists()


def test_bind_rejects_an_unknown_action(tmp_path):
    lines: list[str] = []
    assert bind("fly", AppConfig(), FakeBackend(), path=tmp_path / "b.yaml", out=lines.append) == 2


# -- the real Windows hooks (an injected key, no human needed) -----------------------------------------------------
@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows hooks")
def test_the_global_keyboard_hook_sees_an_injected_key_and_passes_it_on():
    import ctypes

    be = WindowsHookBackend()
    got: list[InputEvent] = []
    be.start(got.append)
    try:
        VK_F24 = 0x87  # no real keyboard has it, nobody binds it
        ctypes.windll.user32.keybd_event(VK_F24, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_F24, 0, 2, 0)  # KEYEVENTF_KEYUP
        deadline = time.monotonic() + 2.0
        while len(got) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        be.stop()
    assert [(e.input.code, e.pressed) for e in got] == [("F24", True), ("F24", False)]
    assert got[0].t > 0
