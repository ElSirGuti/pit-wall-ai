"""Audio devices by name, whatever the host API calls them.

Windows lists every device once per host API (MME, DirectSound, WASAPI, WDM-KS), and MME cuts names at 31
characters. The user picks a device by a part of its name (``"seiren"``, ``"headset"``); this module finds it,
preferring the host API that behaves best for capture and playback, and says so when it is ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# the order in which a duplicate of the same device is preferred; WDM-KS is exclusive-mode and left out
HOSTAPI_PREFERENCE = ("Windows WASAPI", "Windows DirectSound", "MME")
LOW_QUALITY_RATE = (
    16000  # a device that only offers this or less is telephone quality (Bluetooth hands-free)
)


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    hostapi: str
    max_input: int
    max_output: int
    default_rate: float

    @property
    def is_input(self) -> bool:
        return self.max_input > 0

    @property
    def is_output(self) -> bool:
        return self.max_output > 0

    @property
    def narrowband(self) -> bool:
        return 0 < self.default_rate <= 8000


class DeviceLister(Protocol):
    def devices(self) -> list[AudioDevice]: ...


class DeviceNotFound(LookupError):
    pass


def usable(devices: list[AudioDevice], *, kind: str) -> list[AudioDevice]:
    """One entry per physical device: the preferred host API wins; WDM-KS and the 'mapper' are dropped."""
    want_in = kind == "input"
    pool = [d for d in devices if (d.is_input if want_in else d.is_output)]
    pool = [
        d for d in pool if d.hostapi in HOSTAPI_PREFERENCE and "sound mapper" not in d.name.lower()
    ]
    best: dict[str, AudioDevice] = {}
    rank = {h: i for i, h in enumerate(HOSTAPI_PREFERENCE)}
    for d in pool:
        key = _key(d.name)
        if key not in best or rank[d.hostapi] < rank[best[key].hostapi]:
            best[key] = d
    return sorted(best.values(), key=lambda d: d.name.lower())


def _key(name: str) -> str:
    """MME cuts at 31 characters: compare on the shortest common prefix of the lowercase name."""
    return name.lower().strip()[:28]


def find_device(
    devices: list[AudioDevice], query: str | None, *, kind: str
) -> tuple[AudioDevice | None, list[AudioDevice]]:
    """(the device, the other candidates). ``query=None`` means the system default (``None``, ``[]``)."""
    if not query:
        return None, []
    q = query.lower().strip()
    pool = usable(devices, kind=kind)
    hits = [d for d in pool if q in d.name.lower() or _key(d.name).startswith(q[:28])]
    if not hits:
        names = ", ".join(d.name for d in pool) or "none"
        raise DeviceNotFound(f"no {kind} device matches {query!r}; available: {names}")
    return hits[0], hits[1:]
