"""ctypes definitions of the three Assetto Corsa shared-memory pages.

The game publishes three named memory pages (``physics``, ``graphics``, ``static``). Their byte layout
(field order and types, 4-byte packed) is a fact of the game's binary interface, so these structures
necessarily follow it; the Python names, grouping and documentation are this project's own (snake_case,
not the game's identifiers). The layout is checked at runtime: the source refuses to read when the page
the game mapped is smaller than the structure (a game version with a different layout).
See ``docs/AC_SHARED_MEMORY.md`` for what is and is not verified, and ``PROVENANCE.md``.

Units and value ranges are deliberately NOT asserted here: ``pit-wall dump-shm`` shows the live
values so they can be confirmed against the game, and the README table tracks the status of each.
"""

from __future__ import annotations

import ctypes
import enum
from ctypes import c_float, c_int32, c_wchar


class GameStatus(enum.IntEnum):
    OFF = 0
    REPLAY = 1
    LIVE = 2
    PAUSE = 3


class SessionKind(enum.IntEnum):
    UNKNOWN = -1
    PRACTICE = 0
    QUALIFY = 1
    RACE = 2
    HOTLAP = 3
    TIME_ATTACK = 4
    DRIFT = 5
    DRAG = 6


def _name_of(enum_cls, value: int) -> str:
    try:
        return enum_cls(value).name.lower()
    except ValueError:
        return str(value)


def status_name(value: int) -> str:
    return _name_of(GameStatus, value)


def session_name(value: int) -> str:
    return _name_of(SessionKind, value)


# Array helpers: a value per wheel (four), per axis (three), or a 3 x 4 matrix.
_w4 = c_float * 4
_v3 = c_float * 3
_m34 = (c_float * 4) * 3


class PhysicsPage(ctypes.Structure):
    """High-rate page: inputs, motion, tyres, brakes."""

    _pack_ = 4
    _fields_ = [
        # packet counter, driver inputs, engine
        ("packet_id", c_int32),
        ("gas", c_float),
        ("brake", c_float),
        ("fuel", c_float),
        ("gear", c_int32),
        ("rpm", c_int32),
        ("steer_angle", c_float),
        ("speed_kmh", c_float),
        # motion
        ("velocity", _v3),
        ("acc_g", _v3),
        # per-wheel state
        ("wheel_slip", _w4),
        ("wheel_load", _w4),
        ("tyre_pressure", _w4),
        ("wheel_angular_speed", _w4),
        ("tyre_wear", _w4),
        ("tyre_dirty_level", _w4),
        ("tyre_core_temp", _w4),
        ("camber", _w4),
        ("suspension_travel", _w4),
        # aids, attitude, damage
        ("drs", c_float),
        ("tc", c_float),
        ("heading", c_float),
        ("pitch", c_float),
        ("roll", c_float),
        ("cg_height", c_float),
        ("car_damage", c_float * 5),
        ("tyres_out", c_int32),
        ("pit_limiter_on", c_int32),
        ("abs", c_float),
        # hybrid / energy systems
        ("kers_charge", c_float),
        ("kers_input", c_float),
        ("auto_shifter_on", c_int32),
        ("ride_height", c_float * 2),
        ("turbo_boost", c_float),
        ("ballast", c_float),
        ("air_density", c_float),
        # environment and chassis rates
        ("air_temp", c_float),
        ("road_temp", c_float),
        ("local_angular_vel", _v3),
        ("final_ff", c_float),
        ("performance_meter", c_float),
        ("engine_brake", c_int32),
        ("ers_recovery_level", c_int32),
        ("ers_power_level", c_int32),
        ("ers_heat_charging", c_int32),
        ("ers_is_charging", c_int32),
        ("kers_current_kj", c_float),
        ("drs_available", c_int32),
        ("drs_enabled", c_int32),
        # brakes, clutch, tyre temperature by zone
        ("brake_temp", _w4),
        ("clutch", c_float),
        ("tyre_temp_inner", _w4),
        ("tyre_temp_middle", _w4),
        ("tyre_temp_outer", _w4),
        ("is_ai_controlled", c_int32),
        # tyre contact patch geometry
        ("tyre_contact_point", _m34),
        ("tyre_contact_normal", _m34),
        ("tyre_contact_heading", _m34),
        ("brake_bias", c_float),
        ("local_velocity", _v3),
    ]


class GraphicsPage(ctypes.Structure):
    """Session, timing and position page."""

    _pack_ = 4
    _fields_ = [
        ("packet_id", c_int32),
        ("status", c_int32),
        ("session", c_int32),
        # human-readable times (text) followed by the numeric ones
        ("current_time_text", c_wchar * 15),
        ("last_time_text", c_wchar * 15),
        ("best_time_text", c_wchar * 15),
        ("split_text", c_wchar * 15),
        ("completed_laps", c_int32),
        ("position", c_int32),
        ("current_time_ms", c_int32),
        ("last_time_ms", c_int32),
        ("best_time_ms", c_int32),
        ("session_time_left", c_float),
        ("distance_traveled", c_float),
        ("is_in_pit", c_int32),
        ("sector_index", c_int32),
        ("last_sector_time_ms", c_int32),
        ("number_of_laps", c_int32),
        ("tyre_compound", c_wchar * 33),
        ("replay_time_multiplier", c_float),
        ("normalized_pos", c_float),
        ("car_coordinates", _v3),
        # extended block
        ("penalty_time", c_float),
        ("flags", c_int32),
        ("ideal_line_on", c_int32),
        ("is_in_pit_lane", c_int32),
        ("surface_grip", c_float),
        ("mandatory_pit_done", c_int32),
        ("wind_speed", c_float),
        ("wind_direction", c_float),
    ]


class StaticPage(ctypes.Structure):
    """Constants of the car, the track and the session (rewritten when a session starts)."""

    _pack_ = 4
    _fields_ = [
        ("sm_version", c_wchar * 15),
        ("ac_version", c_wchar * 15),
        ("number_of_sessions", c_int32),
        ("num_cars", c_int32),
        ("car_model", c_wchar * 33),
        ("track", c_wchar * 33),
        ("player_name", c_wchar * 33),
        ("player_surname", c_wchar * 33),
        ("player_nick", c_wchar * 33),
        ("sector_count", c_int32),
        # car
        ("max_torque", c_float),
        ("max_power", c_float),
        ("max_rpm", c_int32),
        ("max_fuel", c_float),
        ("suspension_max_travel", _w4),
        ("tyre_radius", _w4),
        ("max_turbo_boost", c_float),
        ("_unused_a", c_float),
        ("_unused_b", c_float),
        # session rules and aids
        ("penalties_enabled", c_int32),
        ("aid_fuel_rate", c_float),
        ("aid_tyre_rate", c_float),
        ("aid_mechanical_damage", c_float),
        ("aid_allow_tyre_blankets", c_int32),
        ("aid_stability", c_float),
        ("aid_auto_clutch", c_int32),
        ("aid_auto_blip", c_int32),
        ("has_drs", c_int32),
        ("has_ers", c_int32),
        ("has_kers", c_int32),
        ("kers_max_j", c_float),
        ("engine_brake_settings_count", c_int32),
        ("ers_power_controller_count", c_int32),
        ("track_length_m", c_float),
        ("track_configuration", c_wchar * 33),
        ("ers_max_j", c_float),
        ("is_timed_race", c_int32),
        ("has_extra_lap", c_int32),
        ("car_skin", c_wchar * 33),
        ("reversed_grid_positions", c_int32),
        ("pit_window_start", c_int32),
        ("pit_window_end", c_int32),
    ]


PAGES = {"physics": PhysicsPage, "graphics": GraphicsPage, "static": StaticPage}
