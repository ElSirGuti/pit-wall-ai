"""Where the program keeps what it writes for the user: ``~/.pit-wall-ai`` (``PIT_WALL_AI_HOME`` moves it).

Everything local and deletable lives there: the alert memory, the sessions file, the debrief reports, the checklist, the
voice cache and the downloaded voices. Tests and portable installs point the variable somewhere else.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "PIT_WALL_AI_HOME"


def home_dir() -> Path:
    base = os.environ.get(ENV_HOME)
    return Path(base) if base else Path.home() / ".pit-wall-ai"
