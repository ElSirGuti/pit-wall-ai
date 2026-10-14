"""What an input is, and how a binding is written in the configuration.

An input is identified by ``(kind, device, code)``; its human ``name`` is only for messages. Kinds:

* ``key``    a keyboard key, ``code`` is its name (``F9``, ``SPACE``, ``LCTRL``...);
* ``mouse``  a mouse button, ``code`` is ``left``, ``right``, ``middle``, ``x1`` or ``x2``;
* ``joy``    a button of a joystick, wheel or button box, ``device`` is ``vid:pid`` and ``code`` the button
             number (1-based, as the game and Windows' "Game Controllers" panel count them).
"""

from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("key", "mouse", "joy")
MOUSE_BUTTONS = ("left", "right", "middle", "x1", "x2")
REFUSED_ALONE = {
    ("mouse", "left"),
    ("mouse", "right"),
}  # a plain click is never a good push-to-talk


@dataclass(frozen=True)
class InputId:
    kind: str
    code: str
    device: str = ""
    name: str = field(default="", compare=False)  # for messages only, not part of the identity

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown input kind {self.kind!r}")

    @property
    def label(self) -> str:
        if self.kind == "joy":
            dev = self.name or self.device
            return f"{dev}, button {self.code}"
        if self.kind == "mouse":
            return f"mouse {self.code}"
        return f"key {self.code}"

    def to_spec(self) -> dict:
        if self.kind == "joy":
            spec = {"kind": "joy", "device": self.device, "button": int(self.code)}
        elif self.kind == "mouse":
            spec = {"kind": "mouse", "button": self.code}
        else:
            spec = {"kind": "key", "key": self.code}
        if self.name:
            spec["name"] = self.name
        return spec

    @staticmethod
    def from_spec(spec: dict) -> InputId:
        kind = spec.get("kind")
        if kind == "joy":
            return InputId(
                "joy", str(int(spec["button"])), str(spec.get("device", "")), spec.get("name", "")
            )
        if kind == "mouse":
            code = str(spec["button"]).lower()
            if code not in MOUSE_BUTTONS:
                raise ValueError(f"unknown mouse button {code!r}")
            return InputId("mouse", code, "", spec.get("name", ""))
        if kind == "key":
            return InputId("key", str(spec["key"]).upper(), "", spec.get("name", ""))
        raise ValueError(f"unknown input kind {kind!r}")


@dataclass(frozen=True)
class Binding:
    """An input, optionally with modifiers that must be held: ``{kind: key, key: F9, with: [{kind: key, key: LCTRL}]}``."""

    input: InputId
    requires: tuple[InputId, ...] = ()

    @staticmethod
    def from_spec(spec: dict) -> Binding:
        req = tuple(InputId.from_spec(r) for r in (spec.get("with") or ()))
        return Binding(InputId.from_spec(spec), req)

    def to_spec(self) -> dict:
        spec = self.input.to_spec()
        if self.requires:
            spec["with"] = [r.to_spec() for r in self.requires]
        return spec

    @property
    def label(self) -> str:
        return " + ".join([*(r.label for r in self.requires), self.input.label])


@dataclass(frozen=True)
class InputEvent:
    input: InputId
    pressed: bool  # False = released
    t: float = 0.0  # time.perf_counter() when the backend saw it


def refused_alone(i: InputId) -> bool:
    return (i.kind, i.code) in REFUSED_ALONE


# -- key names ---------------------------------------------------------------------------------------------
_VK_NAMES = {
    0x08: "BACKSPACE",
    0x09: "TAB",
    0x0D: "ENTER",
    0x13: "PAUSE",
    0x14: "CAPSLOCK",
    0x1B: "ESC",
    0x20: "SPACE",
    0x21: "PAGEUP",
    0x22: "PAGEDOWN",
    0x23: "END",
    0x24: "HOME",
    0x25: "LEFT",
    0x26: "UP",
    0x27: "RIGHT",
    0x28: "DOWN",
    0x2D: "INSERT",
    0x2E: "DELETE",
    0x5B: "LWIN",
    0x5C: "RWIN",
    0xA0: "LSHIFT",
    0xA1: "RSHIFT",
    0xA2: "LCTRL",
    0xA3: "RCTRL",
    0xA4: "LALT",
    0xA5: "RALT",
    0x10: "SHIFT",
    0x11: "CTRL",
    0x12: "ALT",
    0xBA: ";",
    0xBB: "=",
    0xBC: ",",
    0xBD: "-",
    0xBE: ".",
    0xBF: "/",
    0xC0: "`",
    0xDB: "[",
    0xDC: "\\\\",
    0xDD: "]",
    0xDE: "'",
}
_VK_NAMES.update({0x70 + i: f"F{i + 1}" for i in range(24)})
_VK_NAMES.update({0x60 + i: f"NUM{i}" for i in range(10)})
_VK_NAMES.update({0x30 + i: str(i) for i in range(10)})
_VK_NAMES.update({0x41 + i: chr(ord("A") + i) for i in range(26)})


def vk_name(vk: int) -> str:
    return _VK_NAMES.get(vk, f"VK{vk:02X}")
