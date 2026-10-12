"""Typed, immutable view of one telemetry sample, independent of where it came from.

Every source (live shared memory, a replayed log, a test double) produces :class:`Snapshot`; nothing
downstream knows about ctypes. Units are the game's raw units unless a field says otherwise; they are
confirmed at runtime with ``pit-wall dump-shm`` (``docs/AC_SHARED_MEMORY.md``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import shm_structs as S

Quad = tuple[float, float, float, float]
Vec3 = tuple[float, float, float]
CORNERS = ("FL", "FR", "RL", "RR")  # order of every per-wheel tuple (to be confirmed, see docs)
_ZERO4: Quad = (0.0, 0.0, 0.0, 0.0)
_ZERO3: Vec3 = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class StaticInfo:
    """Car / track / session constants (the ``static`` page). Changes only between sessions."""

    sm_version: str = ""
    ac_version: str = ""
    car_model: str = ""
    track: str = ""
    track_configuration: str = ""
    player_name: str = ""
    player_surname: str = ""
    num_cars: int = 0
    sector_count: int = 0
    max_rpm: int = 0
    max_fuel: float = 0.0
    max_power: float = 0.0
    max_torque: float = 0.0
    track_length_m: float = 0.0
    tyre_radius: Quad = _ZERO4
    pit_window_start: int = 0
    pit_window_end: int = 0
    is_timed_race: bool = False
    has_extra_lap: bool = False
    aid_fuel_rate: float = 0.0
    aid_tire_rate: float = 0.0

    @property
    def connected(self) -> bool:
        """The game fills the version strings; an all-zero page means "AC is not running"."""
        return bool(self.sm_version or self.ac_version or self.car_model or self.track)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One sample of the car, the session and the timing."""

    # ── bookkeeping ─────────────────────────────────────────────────────────────
    t: float  # monotonic seconds (time.perf_counter) when it was read
    source: str  # "ac", "replay", "test"...
    packet_id: int  # physics packetId (replay: sample index)
    static: StaticInfo = field(default_factory=StaticInfo)
    # ── driver inputs ───────────────────────────────────────────────────────────
    gas: float = 0.0
    brake: float = 0.0
    clutch: float = 0.0
    steer_angle: float = 0.0
    gear: int = 0  # raw game value; the mapping to R/N/1.. is confirmed with dump-shm
    rpm: int = 0
    # ── car state ───────────────────────────────────────────────────────────────
    speed_kmh: float = 0.0
    fuel: float = 0.0
    velocity: Vec3 = _ZERO3
    local_velocity: Vec3 = _ZERO3  # body frame: lets the slip angle be computed directly
    acc_g: Vec3 = _ZERO3
    heading: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    tc: float = 0.0
    abs: float = 0.0
    brake_bias: float = 0.0
    drs: float = 0.0
    pit_limiter_on: bool = False
    car_damage: tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0)
    # ── per wheel (CORNERS order) ───────────────────────────────────────────────
    wheel_slip: Quad = _ZERO4
    wheel_load: Quad = _ZERO4
    wheel_angular_speed: Quad = _ZERO4
    tyre_pressure: Quad = _ZERO4
    tyre_wear: Quad = _ZERO4
    tyre_dirty_level: Quad = _ZERO4
    tyre_core_temp: Quad = _ZERO4
    tyre_temp_inner: Quad = _ZERO4
    tyre_temp_middle: Quad = _ZERO4
    tyre_temp_outer: Quad = _ZERO4
    brake_temp: Quad = _ZERO4
    suspension_travel: Quad = _ZERO4
    camber_rad: Quad = _ZERO4
    tyres_out: int = 0
    # ── environment ─────────────────────────────────────────────────────────────
    air_temp: float = 0.0
    road_temp: float = 0.0
    surface_grip: float = 0.0
    wind_speed: float = 0.0
    wind_direction: float = 0.0
    # ── session and timing (graphics page) ──────────────────────────────────────
    status: int = int(S.GameStatus.OFF)
    session: int = int(S.SessionKind.UNKNOWN)
    completed_laps: int = 0
    number_of_laps: int = 0
    position: int = 0
    current_time_ms: int = 0
    last_time_ms: int = 0
    best_time_ms: int = 0
    last_sector_time_ms: int = 0
    sector_index: int = 0
    session_time_left: float = 0.0
    distance_traveled: float = 0.0
    normalized_pos: float = 0.0  # 0..1 around the lap: the key for corners, deltas and references
    car_coordinates: Vec3 = _ZERO3
    is_in_pit: bool = False
    is_in_pit_lane: bool = False
    tyre_compound: str = ""
    penalty_time: float = 0.0
    flags: int = 0
    ideal_line_on: bool = False
    mandatory_pit_done: bool = False

    @property
    def status_name(self) -> str:
        return S.status_name(self.status)

    @property
    def session_name(self) -> str:
        return S.session_name(self.session)


def _q(arr) -> Quad:
    return (float(arr[0]), float(arr[1]), float(arr[2]), float(arr[3]))


def _v3(arr) -> Vec3:
    return (float(arr[0]), float(arr[1]), float(arr[2]))


def static_from_struct(s: S.StaticPage) -> StaticInfo:
    return StaticInfo(
        sm_version=s.sm_version,
        ac_version=s.ac_version,
        car_model=s.car_model,
        track=s.track,
        track_configuration=s.track_configuration,
        player_name=s.player_name,
        player_surname=s.player_surname,
        num_cars=s.num_cars,
        sector_count=s.sector_count,
        max_rpm=s.max_rpm,
        max_fuel=s.max_fuel,
        max_power=s.max_power,
        max_torque=s.max_torque,
        track_length_m=s.track_length_m,
        tyre_radius=_q(s.tyre_radius),
        pit_window_start=s.pit_window_start,
        pit_window_end=s.pit_window_end,
        is_timed_race=bool(s.is_timed_race),
        has_extra_lap=bool(s.has_extra_lap),
        aid_fuel_rate=s.aid_fuel_rate,
        aid_tire_rate=s.aid_tyre_rate,
    )


def snapshot_from_structs(
    physics: S.PhysicsPage,
    graphics: S.GraphicsPage,
    static: StaticInfo,
    *,
    t: float,
    source: str = "ac",
) -> Snapshot:
    """Copy the raw pages into an immutable :class:`Snapshot` (no ctypes object escapes)."""
    p, g = physics, graphics
    return Snapshot(
        t=t,
        source=source,
        packet_id=p.packet_id,
        static=static,
        gas=p.gas,
        brake=p.brake,
        clutch=p.clutch,
        steer_angle=p.steer_angle,
        gear=p.gear,
        rpm=p.rpm,
        speed_kmh=p.speed_kmh,
        fuel=p.fuel,
        velocity=_v3(p.velocity),
        local_velocity=_v3(p.local_velocity),
        acc_g=_v3(p.acc_g),
        heading=p.heading,
        pitch=p.pitch,
        roll=p.roll,
        tc=p.tc,
        abs=p.abs,
        brake_bias=p.brake_bias,
        drs=p.drs,
        pit_limiter_on=bool(p.pit_limiter_on),
        car_damage=(
            float(p.car_damage[0]),
            float(p.car_damage[1]),
            float(p.car_damage[2]),
            float(p.car_damage[3]),
            float(p.car_damage[4]),
        ),
        wheel_slip=_q(p.wheel_slip),
        wheel_load=_q(p.wheel_load),
        wheel_angular_speed=_q(p.wheel_angular_speed),
        tyre_pressure=_q(p.tyre_pressure),
        tyre_wear=_q(p.tyre_wear),
        tyre_dirty_level=_q(p.tyre_dirty_level),
        tyre_core_temp=_q(p.tyre_core_temp),
        tyre_temp_inner=_q(p.tyre_temp_inner),
        tyre_temp_middle=_q(p.tyre_temp_middle),
        tyre_temp_outer=_q(p.tyre_temp_outer),
        brake_temp=_q(p.brake_temp),
        suspension_travel=_q(p.suspension_travel),
        camber_rad=_q(p.camber),
        tyres_out=p.tyres_out,
        air_temp=p.air_temp,
        road_temp=p.road_temp,
        surface_grip=g.surface_grip,
        wind_speed=g.wind_speed,
        wind_direction=g.wind_direction,
        status=g.status,
        session=g.session,
        completed_laps=g.completed_laps,
        number_of_laps=g.number_of_laps,
        position=g.position,
        current_time_ms=g.current_time_ms,
        last_time_ms=g.last_time_ms,
        best_time_ms=g.best_time_ms,
        last_sector_time_ms=g.last_sector_time_ms,
        sector_index=g.sector_index,
        session_time_left=g.session_time_left,
        distance_traveled=g.distance_traveled,
        normalized_pos=g.normalized_pos,
        car_coordinates=_v3(g.car_coordinates),
        is_in_pit=bool(g.is_in_pit),
        is_in_pit_lane=bool(g.is_in_pit_lane),
        tyre_compound=g.tyre_compound,
        penalty_time=g.penalty_time,
        flags=g.flags,
        ideal_line_on=bool(g.ideal_line_on),
        mandatory_pit_done=bool(g.mandatory_pit_done),
    )
