"""``pit-wall debrief FILE``: the debrief of a recording, as a report and as the facts it was built from."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from ..config import AppConfig
from .debrief import build_debrief, to_markdown


def _summary(cfg, a, d, lang, out) -> int:
    from ..chat.session import make_client
    from ..engineer.persona import from_config
    from .debrief_speech import spoken_summary

    client = None
    if not getattr(a, "no_model", False):
        client, why = make_client(cfg)
        if client is None:
            out(f"[no model: {why}; the template is used]")
    text, source = spoken_summary(
        d,
        cfg,
        client,
        lang=lang,
        persona_suffix=from_config(cfg.engineer.persona).system_suffix(lang),
    )
    out(f"[{source}] {text}")
    if getattr(a, "speak", False):
        from ..cli import _make_speech
        from ..voice.speech import SpeechOutput  # noqa: F401

        speech = _make_speech(cfg)
        if not speech.start():
            out(f"voice unavailable: {speech.error}")
            return 1
        import time

        speech.say(text)
        while speech.busy:
            time.sleep(0.2)
        time.sleep(0.5)
        speech.stop()
    return 0


def cmd_debrief(
    cfg: AppConfig, a: argparse.Namespace, *, out: Callable[[str], None] = print
) -> int:
    try:
        d = build_debrief(a.file, cfg, session=a.session, ac_dirs=a.ac_dir or ())
    except (OSError, ValueError) as exc:
        print(f"cannot read {a.file}: {exc}", file=sys.stderr)
        return 2
    if getattr(a, "save", False) or getattr(a, "history", None):
        from datetime import datetime

        from .sessions_log import SessionStore, attach_history

        try:  # the date of the session is the date of the recording, not of this command
            when = datetime.fromtimestamp(Path(a.file).stat().st_mtime)
        except OSError:
            when = None
        attach_history(
            d,
            SessionStore(getattr(a, "history", None) or None),
            save=bool(getattr(a, "save", False)),
            when=when,
        )
    lang = a.lang or (cfg.engineer.language if cfg.engineer.language in ("es", "en") else "en")
    if a.json:
        out(json.dumps(d.facts, ensure_ascii=False, indent=1))
        return 0
    if getattr(a, "summary", False) or getattr(a, "speak", False):
        return _summary(cfg, a, d, lang, out)
    text = to_markdown(d, lang)
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        p.with_suffix(".json").write_text(
            json.dumps(d.facts, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        out(f"written {p} and {p.with_suffix('.json')}")
    else:
        out(text)
    return 0
