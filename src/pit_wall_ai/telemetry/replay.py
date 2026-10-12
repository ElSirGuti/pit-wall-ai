"""Replay source: plays a recorded ACTI session as if the game were running.

It exists so the whole pipeline can be developed and tested without Assetto Corsa. The mapping from the
logger's channels to :class:`Snapshot` is APPROXIMATE: ACTI exports derived values in its own units, and
the live units are only confirmed with ``pit-wall dump-shm`` (see ``docs/AC_SHARED_MEMORY.md``). Anything
that has no channel in the log stays at zero; ``unmapped_fields`` lists what a given file lacks.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from pathlib import Path

from . import shm_structs as S
from .recording import Recording, load_recording
from .snapshot import Quad, Snapshot, StaticInfo, Vec3
from .source import SourceExhausted, SourceUnavailable

log = logging.getLogger(__name__)

_WHEELS = ("FL", "FR", "RL", "RR")


def _quad(rec: Recording, i: int, pattern: str, scale: float = 1.0) -> Quad:
    v = [rec.value(pattern.format(w=w), i) * scale for w in _WHEELS]
    return (v[0], v[1], v[2], v[3])


def _vec(rec: Recording, i: int, names: tuple[str, str, str], scale: float = 1.0) -> Vec3:
    return (
        rec.value(names[0], i) * scale,
        rec.value(names[1], i) * scale,
        rec.value(names[2], i) * scale,
    )


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000.0)) if math.isfinite(seconds) and seconds > 0 else 0


def static_from_recording(rec: Recording) -> StaticInfo:
    return StaticInfo(
        sm_version="replay",
        ac_version=rec.meta.get("session", "") or "replay",
        car_model=rec.meta.get("vehicle", ""),
        track=rec.meta.get("venue", ""),
        player_name=rec.meta.get("driver", ""),
        max_rpm=int(rec.value("Max RPM", 0)),
        max_fuel=rec.value("Max Fuel", 0),
        max_power=rec.value("Max Power", 0),
        max_torque=rec.value("Max Torque", 0),
        aid_tire_rate=rec.value("AID Tire Wear Rate", 0),
        aid_fuel_rate=rec.value("AID Fuel Rate", 0),
    )


# Channels the mapping below reads. A file missing many of them is reported by ``unmapped_fields``.
USED_CHANNELS = (
    "Throttle Pos",
    "Brake Pos",
    "Clutch Pos",
    "Steering Angle",
    "Gear",
    "Engine RPM",
    "Ground Speed",
    "Fuel Level",
    "Chassis Velocity X",
    "CG Accel Lateral",
    "Tire Pressure FL",
    "Tire Temp Core FL",
    "Tire Temp Inner FL",
    "Tire Temp Middle FL",
    "Tire Temp Outer FL",
    "Brake Temp FL",
    "Car Pos Norm",
    "Lap Time",
    "Last Lap Time",
    "Best Lap Time",
    "Session Lap Count",
    "In Pit",
    "Air Temp",
    "Road Temp",
    "Tire Slip Ratio FL",
    "Tire Load FL",
    "Tire Dirt Level FL",
    "Suspension Travel FL",
    "Wind Speed",
    "Surface Grip",
    "Brake Bias",
    "Num Tires Off Track",
)


def snapshot_from_recording(
    rec: Recording, i: int, static: StaticInfo, *, t: float, session: int, tyre_compound: str = ""
) -> Snapshot:
    v = rec.value
    bias = v("Brake Bias", i)
    grip = v("Surface Grip", i)
    return Snapshot(
        t=t,
        source="replay",
        packet_id=i,
        static=static,
        gas=v("Throttle Pos", i) / 100.0,
        brake=v("Brake Pos", i) / 100.0,
        clutch=v("Clutch Pos", i) / 100.0,
        steer_angle=v("Steering Angle", i),
        gear=int(v("Gear", i)),
        rpm=int(v("Engine RPM", i)),
        speed_kmh=v("Ground Speed", i),
        fuel=v("Fuel Level", i),
        velocity=_vec(rec, i, ("Chassis Velocity X", "Chassis Velocity Y", "Chassis Velocity Z")),
        local_velocity=_vec(
            rec, i, ("Chassis Velocity X", "Chassis Velocity Y", "Chassis Velocity Z")
        ),
        acc_g=_vec(rec, i, ("CG Accel Lateral", "CG Accel Vertical", "CG Accel Longitudinal")),
        heading=0.0,
        pitch=math.radians(v("Chassis Pitch Angle", i)),
        roll=math.radians(v("Chassis Roll Angle", i)),
        tc=v("TC Active", i),
        abs=v("ABS Active", i),
        brake_bias=bias / 100.0 if bias > 1.0 else bias,
        pit_limiter_on=False,
        wheel_slip=_quad(rec, i, "Tire Slip Ratio {w}", 0.01),
        wheel_load=_quad(rec, i, "Tire Load {w}"),
        wheel_angular_speed=_quad(rec, i, "Wheel Angular Speed {w}"),
        tyre_pressure=_quad(rec, i, "Tire Pressure {w}"),
        tyre_wear=_quad(rec, i, "Tire Rubber Grip {w}"),
        tyre_dirty_level=_quad(rec, i, "Tire Dirt Level {w}"),
        tyre_core_temp=_quad(rec, i, "Tire Temp Core {w}"),
        tyre_temp_inner=_quad(rec, i, "Tire Temp Inner {w}"),
        tyre_temp_middle=_quad(rec, i, "Tire Temp Middle {w}"),
        tyre_temp_outer=_quad(rec, i, "Tire Temp Outer {w}"),
        brake_temp=_quad(rec, i, "Brake Temp {w}"),
        suspension_travel=_quad(rec, i, "Suspension Travel {w}"),
        camber_rad=_quad(rec, i, "Camber {w}", math.pi / 180.0),
        tyres_out=int(v("Num Tires Off Track", i)),
        air_temp=v("Air Temp", i),
        road_temp=v("Road Temp", i),
        surface_grip=grip / 100.0 if grip > 1.0 else grip,
        wind_speed=v("Wind Speed", i),
        wind_direction=v("Wind Direction", i),
        status=int(S.GameStatus.LIVE),
        session=session,
        completed_laps=int(v("Session Lap Count", i)),
        position=int(v("Position", i)),
        current_time_ms=_ms(v("Lap Time", i)),
        last_time_ms=_ms(v("Last Lap Time", i)),
        best_time_ms=_ms(v("Best Lap Time", i)),
        last_sector_time_ms=_ms(v("Last Sector Time", i)),
        session_time_left=v("Session Time Left", i),
        normalized_pos=v("Car Pos Norm", i),
        car_coordinates=_vec(rec, i, ("Car Coord X", "Car Coord Y", "Car Coord Z")),
        is_in_pit=v("In Pit", i) > 0,
        is_in_pit_lane=v("In Pit", i) > 0,
        tyre_compound=tyre_compound,
        flags=int(v("Flags", i)),
    )


class ReplaySource:
    """Plays a recording in real time (``speed`` x) from ``start_s``; optionally in a loop."""

    name = "replay"

    def __init__(
        self,
        path: str | Path | Recording,
        *,
        speed: float = 1.0,
        start_s: float = 0.0,
        loop: bool = False,
        session: int = int(S.SessionKind.PRACTICE),
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be > 0")
        self._arg = path
        self.speed = speed
        self.start_s = max(0.0, start_s)
        self.loop = loop
        self.session = session
        self._clock = clock
        self.recording: Recording | None = None
        self._static: StaticInfo | None = None
        self._t0 = 0.0
        self._last_index = -1

    @property
    def unmapped_fields(self) -> list[str]:
        """Channels this file lacks among those the mapping reads (empty before :meth:`open`)."""
        if self.recording is None:
            return []
        return [c for c in USED_CHANNELS if not self.recording.has(c)]

    def open(self) -> None:
        if self.recording is None:
            try:
                self.recording = (
                    self._arg if isinstance(self._arg, Recording) else load_recording(self._arg)
                )
            except (OSError, ValueError) as exc:
                raise SourceUnavailable(str(exc)) from exc
        self._static = static_from_recording(self.recording)
        self._t0 = self._clock()
        self._last_index = -1
        missing = self.unmapped_fields
        if missing:
            log.warning("replay: channels missing in the log", extra={"missing": missing})

    def close(self) -> None:
        self.recording = None if not isinstance(self._arg, Recording) else self.recording

    def _index_now(self) -> int:
        rec = self.recording
        elapsed = (self._clock() - self._t0) * self.speed + self.start_s
        i = int(elapsed * rec.hz)
        if i >= rec.n:
            if not self.loop:
                raise SourceExhausted("end of recording")
            i %= rec.n
        return i

    def read(self) -> Snapshot | None:
        if self.recording is None or self._static is None:
            raise SourceUnavailable("source not open")
        i = self._index_now()
        if i == self._last_index:
            return None
        self._last_index = i
        return snapshot_from_recording(
            self.recording,
            i,
            self._static,
            t=self._clock(),
            session=self.session,
        )
