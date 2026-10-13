"""Simple live dashboard for the terminal (rich). Pure rendering: ``render_dashboard`` builds a view of
one snapshot; ``run_dashboard`` refreshes it ~10 times a second from the poller's buffer."""

from __future__ import annotations

import threading

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .poller import PollerStats, TelemetryPoller
from .snapshot import CORNERS, Snapshot

_STATUS_STYLE = {
    "running": "green",
    "waiting": "yellow",
    "ended": "cyan",
    "stopped": "dim",
    "starting": "dim",
}


def bar(value: float, width: int = 20, maximum: float = 1.0, style: str = "green") -> Text:
    """A horizontal bar (value / maximum, clamped)."""
    frac = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
    filled = int(round(frac * width))
    text = Text("█" * filled, style=style)
    text.append("░" * (width - filled), style="dim")
    return text


def fmt_time(ms: int) -> str:
    if ms <= 0:
        return "--:--.---"
    m, rest = divmod(ms, 60_000)
    return f"{m}:{rest / 1000:06.3f}"


def _driving(s: Snapshot) -> Table:
    t = Table.grid(padding=(0, 1))
    t.add_column(style="bold", justify="right")
    t.add_column()
    t.add_row("Speed", f"{s.speed_kmh:6.1f} km/h")
    t.add_row("RPM / Gear", f"{s.rpm:5d} / {s.gear}")
    t.add_row("Throttle", Text.assemble(bar(s.gas, style="green"), f" {s.gas * 100:5.1f}%"))
    t.add_row("Brake", Text.assemble(bar(s.brake, style="red"), f" {s.brake * 100:5.1f}%"))
    t.add_row("Steer", f"{s.steer_angle:+8.2f}")
    t.add_row("Fuel", f"{s.fuel:6.2f}")
    t.add_row("G (x,y,z)", " ".join(f"{g:+5.2f}" for g in s.acc_g))
    t.add_row("TC / ABS", f"{s.tc:.2f} / {s.abs:.2f}")
    return t


def _timing(s: Snapshot) -> Table:
    t = Table.grid(padding=(0, 1))
    t.add_column(style="bold", justify="right")
    t.add_column()
    t.add_row("Session", f"{s.session_name} ({s.status_name})")
    t.add_row("Lap", f"{s.completed_laps}" + (f" / {s.number_of_laps}" if s.number_of_laps else ""))
    t.add_row("Current", fmt_time(s.current_time_ms))
    t.add_row("Last", fmt_time(s.last_time_ms))
    t.add_row("Best", fmt_time(s.best_time_ms))
    t.add_row(
        "Lap pos",
        Text.assemble(bar(s.normalized_pos, 20, style="cyan"), f" {s.normalized_pos:.3f}"),
    )
    t.add_row("Pit", "in pit lane" if s.is_in_pit_lane else ("in pit" if s.is_in_pit else "-"))
    t.add_row("Tyres off", str(s.tyres_out))
    return t


def _tyres(s: Snapshot) -> Table:
    t = Table(title="Tyres / Brakes", expand=False, show_edge=False, pad_edge=False)
    for col in ("", "Core", "In", "Mid", "Out", "Press", "Wear", "Dirt", "Brake T"):
        t.add_column(col, justify="right")
    for i, c in enumerate(CORNERS):
        t.add_row(
            c,
            f"{s.tyre_core_temp[i]:.0f}",
            f"{s.tyre_temp_inner[i]:.0f}",
            f"{s.tyre_temp_middle[i]:.0f}",
            f"{s.tyre_temp_outer[i]:.0f}",
            f"{s.tyre_pressure[i]:.1f}",
            f"{s.tyre_wear[i]:.1f}",
            f"{s.tyre_dirty_level[i]:.2f}",
            f"{s.brake_temp[i]:.0f}",
        )
    return t


def _footer(stats: PollerStats, source: str, s: Snapshot | None) -> Text:
    style = _STATUS_STYLE.get(stats.status, "white")
    text = Text.assemble(
        ("source ", "dim"),
        (source, "bold"),
        ("  status ", "dim"),
        (stats.status, style),
        ("  rate ", "dim"),
        f"{stats.measured_hz:5.1f} Hz",
        ("  samples ", "dim"),
        str(stats.samples),
        ("  dropped ", "dim"),
        str(stats.dropped),
        ("  errors ", "dim"),
        str(stats.errors),
    )
    if s is not None and s.static.connected:
        text.append(f"\n{s.static.car_model or '?'} @ {s.static.track or '?'}", style="dim")
    if stats.last_error and stats.status == "waiting":
        text.append(f"\n{stats.last_error}", style="yellow")
    return text


def _ms(ms: float | None, signed: bool = False) -> str:
    if ms is None:
        return "-"
    sign = "+" if signed and ms >= 0 else "-" if ms < 0 else ""
    return f"{sign}{abs(ms) / 1000:.3f}" if signed else fmt_time(int(ms))


def _session_panel(view) -> Panel:
    st = view.state
    t = Table.grid(padding=(0, 1))
    t.add_column(style="bold", justify="right")
    t.add_column()
    kind = st.session + ("" if st.kind_verified_live else " (code not verified live)")
    t.add_row("Session", f"{kind}  ·  {st.phase}")
    t.add_row("Track / car", f"{st.track or '?'} / {st.car or '?'}")
    cur = view.current
    if cur is not None:
        delta = _ms(cur.delta_ms, signed=True) if cur.delta_ms is not None else "-"
        style = "red" if cur.delta_ms and cur.delta_ms > 0 else "green"
        t.add_row(
            "Lap",
            f"{cur.number}  sector {cur.sector}  {fmt_time(cur.elapsed_ms)}"
            + ("  (out lap)" if cur.out_lap else ""),
        )
        t.add_row(
            "Delta",
            Text(f"{delta} s vs lap {cur.reference_number}", style=style)
            if cur.reference_number
            else "no reference yet",
        )
    best = view.best
    t.add_row("Best", f"{fmt_time(best.time_ms)} (lap {best.number})" if best else "-")
    if st.session_time_left_s is not None:
        m, sec = divmod(int(st.session_time_left_s), 60)
        t.add_row("Time left", f"{m}:{sec:02d}")
    return Panel(t, title="Session")


def _laps_table(view, rows: int = 6) -> Table:
    t = Table(title="Laps", show_edge=False, pad_edge=False)
    for col in ("#", "Time", "vs best", "S1", "S2", "S3", "Fuel", "Notes"):
        t.add_column(col, justify="right")
    best = view.best.time_ms if view.best else None
    for lap in view.laps[-rows:]:
        secs = [f"{x / 1000:.3f}" for x in lap.sectors_ms] + ["-"] * (3 - len(lap.sectors_ms))
        notes = ",".join(
            n
            for n, on in (
                ("out", lap.out_lap),
                ("in", lap.in_lap),
                ("cut?", lap.suspect),
                ("est", lap.time_source == "estimated"),
            )
            if on
        )
        mark = "★" if view.best is lap else ""
        t.add_row(
            f"{lap.number}{mark}",
            fmt_time(lap.time_ms),
            _ms(lap.time_ms - best, signed=True) if best else "-",
            *secs[:3],
            f"{lap.fuel_used:.2f}",
            notes,
        )
    return t


def render_dashboard(
    snap: Snapshot | None, stats: PollerStats, *, source: str = "?", view=None
) -> RenderableType:
    if snap is None:
        msg = (
            "Waiting for telemetry..."
            if stats.status in ("waiting", "starting", "running")
            else "No data"
        )
        return Panel(
            Group(Text(msg, style="yellow"), _footer(stats, source, None)), title="pit-wall-ai"
        )
    top = Table.grid(expand=True, padding=(0, 3))
    top.add_column()
    top.add_column()
    if view is not None:
        top.add_row(Panel(_driving(snap), title="Driving"), _session_panel(view))
        parts = [top, _tyres(snap)]
        if view.laps:
            parts.append(_laps_table(view))
        parts.append(_footer(stats, source, snap))
        return Panel(Group(*parts), title="pit-wall-ai")
    top.add_row(Panel(_driving(snap), title="Driving"), Panel(_timing(snap), title="Timing"))
    return Panel(Group(top, _tyres(snap), _footer(stats, source, snap)), title="pit-wall-ai")


def run_dashboard(
    poller: TelemetryPoller,
    *,
    refresh_hz: float = 10.0,
    console: Console | None = None,
    stop: threading.Event | None = None,
    duration_s: float | None = None,
    engine=None,
) -> None:
    """Show the dashboard until Ctrl+C, ``stop`` is set, the source ends or ``duration_s`` passes."""
    stop = stop or threading.Event()
    console = console or Console()
    period = 1.0 / refresh_hz
    elapsed = 0.0
    with Live(console=console, refresh_per_second=refresh_hz, transient=False) as live:
        while not stop.is_set():
            live.update(
                render_dashboard(
                    poller.buffer.latest(),
                    poller.stats,
                    source=poller.source.name,
                    view=engine.view() if engine is not None else None,
                )
            )
            if poller.stats.status == "ended" or (duration_s is not None and elapsed >= duration_s):
                break
            stop.wait(period)
            elapsed += period
