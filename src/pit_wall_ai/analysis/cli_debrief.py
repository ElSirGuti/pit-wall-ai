"""``pit-wall debrief FILE``: the debrief of a recording, as a report and as the facts it was built from."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from ..config import AppConfig
from .debrief import build_debrief, to_markdown


def cmd_debrief(
    cfg: AppConfig, a: argparse.Namespace, *, out: Callable[[str], None] = print
) -> int:
    try:
        d = build_debrief(a.file, cfg, session=a.session, ac_dirs=a.ac_dir or ())
    except (OSError, ValueError) as exc:
        print(f"cannot read {a.file}: {exc}", file=sys.stderr)
        return 2
    lang = a.lang or (cfg.engineer.language if cfg.engineer.language in ("es", "en") else "en")
    if a.json:
        out(json.dumps(d.facts, ensure_ascii=False, indent=1))
        return 0
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
