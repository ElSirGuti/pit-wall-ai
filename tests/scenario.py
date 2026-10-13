"""A synthetic session as a list of snapshots, with the timing behaviour of the game as the tracker assumes it."""

from __future__ import annotations

from dataclasses import replace

from pit_wall_ai.telemetry.snapshot import Snapshot, StaticInfo


def make_session(
    lap_times_s,
    *,
    hz: float = 20.0,
    pit_wait_s: float = 6.0,
    session: int = 0,
    track: str = "hockenheim",
    car: str = "supra",
    sector_fracs=(0.30, 0.65),
    sector_index: bool = True,
    sector_time_channel: bool = True,
    confirm_delay_samples: int = 0,
    report_last_time: bool = True,
    report_completed_laps: bool = True,
    pit_lane_at_end: bool = False,
    offtrack_laps=(),
    t0: float = 1000.0,
    start_laps: int = 0,
    session_time_left_ms: float = 2_400_000.0,
) -> list[Snapshot]:
    """``lap_times_s[0]`` is the out lap (it starts in the pit box at 90 % of the lap); the rest are flying.

    The game's lap timer starts at 0 at the start of the session, so the out lap includes the pit wait.
    """
    static = StaticInfo(
        sm_version="1.7", ac_version="1.16.4", car_model=car, track=track, sector_count=3
    )
    dt = 1.0 / hz
    out: list[Snapshot] = []
    i = 0
    completed = start_laps
    last_time = 0
    last_sector = 0
    n = len(lap_times_s)

    def snap(pos, in_pit, speed, elapsed_ms, fuel, tyres_out, sector, lap_no):
        nonlocal i
        s = Snapshot(
            t=t0 + i * dt,
            source="test",
            packet_id=i,
            static=static,
            speed_kmh=speed,
            fuel=fuel,
            normalized_pos=pos,
            is_in_pit=in_pit,
            is_in_pit_lane=in_pit,
            status=2,
            session=session,
            completed_laps=completed if report_completed_laps else start_laps,
            last_time_ms=last_time if report_last_time else 0,
            last_sector_time_ms=last_sector if sector_time_channel else 0,
            sector_index=sector if sector_index else 0,
            current_time_ms=int(elapsed_ms),
            session_time_left=session_time_left_ms - i * dt * 1000.0,
            tyres_out=tyres_out,
            gas=0.8,
            brake=0.0,
            steer_angle=0.1,
            tyre_pressure=(26.0,) * 4,
        )
        i += 1
        return s

    fuel = 45.0
    # pit wait
    for k in range(int(pit_wait_s * hz)):
        out.append(snap(0.9, True, 0.0, k * dt * 1000, fuel, 0, 0, 1))
    pending_confirm: list[tuple[int, int]] = []  # (samples to wait, value)
    for lap_idx, D in enumerate(lap_times_s):
        start_pos = 0.9 if lap_idx == 0 else 0.0
        span = 1.0 - start_pos
        steps = max(2, int(round(D * span * hz)))
        base_elapsed = pit_wait_s * 1000 if lap_idx == 0 else 0
        last_sector_in_lap = 0
        for k in range(steps):
            frac = k / steps
            pos = start_pos + span * frac
            elapsed = base_elapsed + k * dt * 1000
            sector = 0 if pos < sector_fracs[0] else 1 if pos < sector_fracs[1] else 2
            in_pit = (pit_lane_at_end and lap_idx == n - 1 and frac > 0.97) or (
                lap_idx == 0 and k < 1
            )
            if sector_time_channel and lap_idx > 0:
                if sector >= 1 and last_sector_in_lap == 0:
                    last_sector = int(sector_fracs[0] * D * 1000)
                    last_sector_in_lap = 1
                if sector >= 2 and last_sector_in_lap == 1:
                    last_sector = int((sector_fracs[1] - sector_fracs[0]) * D * 1000)
                    last_sector_in_lap = 2
            if pending_confirm:
                wait, val = pending_confirm[0]
                if wait <= 0:
                    last_time = val
                    pending_confirm.pop(0)
                else:
                    pending_confirm[0] = (wait - 1, val)
            tyres_out = 4 if (lap_idx in offtrack_laps and 0.4 < frac < 0.6) else 0
            fuel -= 0.001
            out.append(snap(pos, in_pit, 150.0, elapsed, fuel, tyres_out, sector, lap_idx + 1))
        # crossing
        completed += 1
        if confirm_delay_samples == 0:
            last_time = (
                int(round(D * 1000)) if lap_idx > 0 else int(round((pit_wait_s + D * span) * 1000))
            )
        else:
            val = (
                int(round(D * 1000)) if lap_idx > 0 else int(round((pit_wait_s + D * span) * 1000))
            )
            pending_confirm.append((confirm_delay_samples, val))
    # one more sample after the last crossing so the closing lap is seen
    for _ in range(int(2 * hz)):
        out.append(snap(0.01, pit_lane_at_end, 150.0, 0, fuel, 0, 0, n + 1))
        if pending_confirm:
            wait, val = pending_confirm[0]
            if wait <= 0:
                last_time = val
                pending_confirm.pop(0)
            else:
                pending_confirm[0] = (wait - 1, val)
    return out


def with_status(snaps, **kw):
    return [replace(s, **kw) for s in snaps]
