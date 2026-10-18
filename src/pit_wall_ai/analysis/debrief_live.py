"""The debrief at the end of a live session (``docs/PHASE7_PLAN.md`` step 3).

It listens to the same samples as the rest of the engineer, keeps a few aggregates (``SessionStats``) and, when a race
finishes or a qualifying ends (the milestones the engineer already detects), or when the driver asks, builds the
debrief from the laps the tracker holds: it writes the report, keeps the session in ``sessions.jsonl`` for "against
last time", and says the short version unless quiet mode is on.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from ..engineer.persona import from_config
from ..events import SessionChanged
from ..telemetry.shm_structs import GameStatus
from .debrief import Debrief, SessionStats, build_from_parts, to_markdown
from .debrief_speech import spoken_summary
from .sessions_log import SessionStore, attach_history

log = logging.getLogger(__name__)
DEFAULT_DIR = Path.home() / ".pit-wall-ai" / "debriefs"
TRIGGERS = ("race_finished", "quali_final")


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_") or "x"


class LiveDebrief:
    def __init__(
        self,
        cfg,
        speaker,
        *,
        track_analysis,
        client=None,
        quiet=lambda: False,
        threaded: bool = True,
        delay_s: float = 12.0,
        clock=time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.dcfg = cfg.engineer.debrief
        self.speaker = speaker
        self.track = track_analysis
        self.client = client
        self.quiet = quiet
        self.threaded = threaded
        self.delay_s = delay_s
        self.stats = SessionStats()
        self._laps: list = []
        self._tracker = None
        self._static = None
        self.store = SessionStore(self.dcfg.sessions_path) if self.dcfg.history else None
        self.last: Debrief | None = None
        self.last_text = ""
        self.reports: list[Path] = []
        self._done_session: str | None = None

    # -- feeding -----------------------------------------------------------------------------------------
    def on_sample(self, snap, state, tracker, events) -> None:
        if any(isinstance(e, SessionChanged) and e.reason != "first_sample" for e in events):
            self.stats = SessionStats()
            self._laps = []
            self._done_session = None
        self._tracker = tracker
        if snap.status != int(GameStatus.LIVE):
            return
        self.stats.update(snap)
        if snap.static is not None and snap.static.track:
            self._static = snap.static
        if tracker is not None and len(tracker.laps) != len(self._laps):
            self._laps = list(tracker.laps)

    def on_milestone(self, m, snap, state, tracker) -> None:
        """Called by the milestones hook the moment a result is detected (before this sample is fed)."""
        if not self.dcfg.enabled or m.kind not in TRIGGERS:
            return
        if tracker is not None and tracker.laps:
            self._laps = list(tracker.laps)
        self.trigger(why=m.kind, speak=self.dcfg.speak)

    # -- building ---------------------------------------------------------------------------------------
    def build(self) -> Debrief | None:
        static = self._static
        if static is None or not self._laps:
            return None
        info = self.track.for_static(static) if self.track is not None else None
        geometry = info.geometry if info is not None else None
        d = build_from_parts(self.stats, list(self._laps), static, geometry, self.cfg)
        if self.store is not None:
            attach_history(d, self.store, save=True)
        return d

    def trigger(self, *, why: str, speak: bool) -> None:
        key = f"{id(self.stats)}:{why}"
        if self._done_session == key:
            return
        self._done_session = key
        if self.threaded:
            threading.Thread(
                target=self._run, args=(why, speak), name="debrief", daemon=True
            ).start()
        else:
            self._run(why, speak, delay=False)

    def _run(self, why: str, speak: bool, delay: bool = True) -> None:
        try:
            d = self.build()
            if d is None or not d.facts.get("pace"):
                log.info("no debrief: not enough laps", extra={"why": why})
                return
            lang = self.cfg.engineer.language
            self.last = d
            self._write(d, lang)
            text, source = spoken_summary(
                d,
                self.cfg,
                self.client,
                lang=lang,
                persona_suffix=from_config(self.cfg.engineer.persona).system_suffix(lang),
            )
            self.last_text = text
            log.info("debrief ready", extra={"why": why, "source": source})
            if speak and text:
                if delay and self.delay_s > 0:
                    time.sleep(self.delay_s)  # the result message goes first
                if not self.quiet():
                    self.speaker.say(
                        text, interrupt=False, meta={"rule": "debrief", "source": source}
                    )
        except Exception:  # noqa: BLE001 - the debrief must never break the run
            log.exception("the debrief failed")

    def _write(self, d: Debrief, lang: str) -> None:
        folder = Path(self.dcfg.dir) if self.dcfg.dir else DEFAULT_DIR
        r = d.facts.get("result", {})
        name = f"{datetime.now():%Y%m%d_%H%M%S}_{_slug(r.get('track', ''))}_{_slug(r.get('car', ''))}_{r.get('session', '')}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            md = folder / f"{name}.md"
            md.write_text(to_markdown(d, lang if lang in ("es", "en") else "en"), encoding="utf-8")
            from .debrief_html import to_html

            md.with_suffix(".html").write_text(
                to_html(d, lang if lang in ("es", "en") else "en"), encoding="utf-8"
            )
            md.with_suffix(".json").write_text(
                json.dumps(d.facts, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            self.reports.append(md)
        except OSError:
            log.exception("could not write the debrief report")

    # -- on request -------------------------------------------------------------------------------------
    def request(self) -> str:
        """The spoken summary of the session so far (the chat asks for it: "dame el resumen de la sesión")."""
        lang = self.cfg.engineer.language
        d = self.build() if self.store is None else self._build_without_saving()
        if d is None or not d.facts.get("pace"):
            return {
                "es": "Todavía no hay vueltas válidas para hacer el resumen.",
                "en": "There are no valid laps yet for a summary.",
                "de": "Es gibt noch keine gültigen Runden für eine Zusammenfassung.",
            }.get(lang, "There are no valid laps yet for a summary.")
        self.last = d
        self._write(d, lang)
        text, _ = spoken_summary(
            d,
            self.cfg,
            self.client,
            lang=lang,
            persona_suffix=from_config(self.cfg.engineer.persona).system_suffix(lang),
        )
        return text

    def _build_without_saving(self) -> Debrief | None:
        """On demand the comparison is shown but the session is not stored (it is not finished)."""
        static = self._static
        if static is None or not self._laps:
            return None
        info = self.track.for_static(static) if self.track is not None else None
        d = build_from_parts(
            self.stats, list(self._laps), static, info.geometry if info else None, self.cfg
        )
        if self.store is not None:
            attach_history(d, self.store, save=False)
        return d
