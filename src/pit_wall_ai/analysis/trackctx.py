"""The track of the current session, resolved once and shared (the spoken hint and the chat use it)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import AnalysisConfig
from ..telemetry.snapshot import StaticInfo
from .circuit_names import corner_names, numbering_verified
from .track_geometry import TrackGeometry
from .tracks import Resolution, resolve

log = logging.getLogger(__name__)


@dataclass
class TrackInfo:
    track: str
    layout: str
    resolution: Resolution
    names: dict[int, str] = field(default_factory=dict)
    verified: bool = False

    @property
    def geometry(self) -> TrackGeometry | None:
        return self.resolution.geometry

    @property
    def reason(self) -> str:
        return self.resolution.reason


class TrackAnalysis:
    """Resolves ``static.track`` / ``track_configuration`` / length to a geometry, caching the answer."""

    def __init__(self, cfg: AnalysisConfig | None = None) -> None:
        self.cfg = cfg or AnalysisConfig()
        self._key: tuple | None = None
        self._info: TrackInfo | None = None

    def reset(self) -> None:
        self._key = None

    def for_static(self, st: StaticInfo) -> TrackInfo | None:
        if not st.track:
            return None
        key = (st.track, st.track_configuration, round(st.track_length_m))
        if key == self._key:
            return self._info
        self._key = key
        res = resolve(
            st.track,
            st.track_configuration,
            length_m=st.track_length_m or None,
            configured=self.cfg.ac_dirs,
        )
        info = TrackInfo(st.track, res.layout, res)
        if res.ok:
            info.names = corner_names(res.geometry, st.track)
            info.verified = st.track in self.cfg.verified_numbering or numbering_verified(st.track)
            log.info(
                "braking analysis ready",
                extra={
                    "track": st.track,
                    "layout": res.layout,
                    "corners": len(res.geometry.apexes),
                    "source": str(res.candidate.path),
                },
            )
        else:
            log.warning(
                "braking analysis off for this track",
                extra={"track": st.track, "layout": st.track_configuration, "reason": res.reason},
            )
        self._info = info
        return info
