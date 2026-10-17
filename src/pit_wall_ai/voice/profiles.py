"""Voice profiles: one local file per voice (``voice_profiles/<id>.yaml``), and the ``http`` engine for a local server.

A profile picks an engine and its settings and gives the engineer a name; the persona (the style of the wording)
stays separate, so any voice goes with any style. Profiles are local and git-ignored.

A zero-shot profile points at a reference clip that **the user supplies and has the right to use** (their own voice, a
voice they were given permission for, or a clearly licensed one). Nothing is uploaded: the clip stays on the PC and is
only handed to the user's own local server. This project ships no voice of a real person and no tool to extract one.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .tts import TtsUnavailable


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str | None = None  # what the engineer calls themself
    engine: Literal["sapi", "piper", "http", "command"] = "sapi"
    model: str | None = None  # sapi: part of the voice name; piper: the .onnx path
    reference_audio: str | None = (
        None  # zero-shot engines only: a clip the user has the right to use
    )
    language: Literal["es", "en", "de"] | None = None
    radio_effect: Literal["off", "light", "strong"] = "off"
    http_url: str | None = None
    http_body: str | None = None
    command: str | None = None
    piper_exe: str | None = None
    notes: str = Field(default="", description="free text; never used")


def load_profile(profile_id: str, folder: str | Path = "voice_profiles") -> VoiceProfile:
    path = Path(folder) / f"{profile_id}.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise TtsUnavailable(f"voice profile {profile_id!r} not found in {folder}: {exc}") from exc
    raw.setdefault("id", profile_id)
    try:
        return VoiceProfile.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - a typo in a profile is reported as such
        raise TtsUnavailable(f"voice profile {path} is not valid: {exc}") from exc


def apply_profile(cfg, profile: VoiceProfile):
    """The config with the profile's engine and settings in place of the ``engineer.voice`` ones."""
    v = cfg.engineer.voice
    upd: dict = {"backend": profile.engine, "radio_effect": profile.radio_effect}
    if profile.engine == "sapi" and profile.model:
        upd["name"] = profile.model
    if profile.engine == "piper":
        upd["piper_model"] = profile.model
        if profile.piper_exe:
            upd["piper_exe"] = profile.piper_exe
    if profile.engine == "http":
        upd.update(
            http_url=profile.http_url,
            http_body=profile.http_body,
            reference_audio=profile.reference_audio,
        )
    if profile.engine == "command":
        upd["command"] = profile.command
    eng_upd: dict = {"voice": v.model_copy(update=upd)}
    if profile.language:
        eng_upd["language"] = profile.language
    if profile.display_name:
        eng_upd["persona"] = cfg.engineer.persona.model_copy(update={"name": profile.display_name})
    return cfg.model_copy(update={"engineer": cfg.engineer.model_copy(update=eng_upd)})


# -- the http engine ------------------------------------------------------------------------------------------
DEFAULT_BODY = '{"text": {text}, "language": {language}, "speaker_wav": {reference}}'


def render_http(
    url: str,
    body_template: str | None,
    text: str,
    out: str,
    *,
    language: str = "es",
    reference: str | None = None,
    timeout_s: float = 30.0,
    opener=urllib.request.urlopen,
) -> None:
    """POST the sentence to a local synthesis server and write the WAV it answers with.

    ``body_template`` is JSON text with ``{text}``, ``{language}`` and ``{reference}`` where a JSON value goes; each is
    replaced by its JSON-encoded value, so quotes in the sentence cannot break the request. Only loopback addresses
    are accepted: the audio and the clip never leave the PC.
    """
    host = url.split("//", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise TtsUnavailable(f"the http voice only talks to this machine, not to {host!r}")
    body = body_template or DEFAULT_BODY
    for key, value in (
        ("{text}", text),
        ("{language}", language),
        ("{reference}", reference or ""),
    ):
        body = body.replace(key, json.dumps(value, ensure_ascii=False))
    req = urllib.request.Request(  # noqa: S310 - loopback only, checked above
        url, data=body.encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with opener(req, timeout=timeout_s) as r:
            audio = r.read()
    except OSError as exc:
        raise TtsUnavailable(f"the local voice server did not answer: {exc}") from exc
    if not audio.startswith(b"RIFF"):
        raise TtsUnavailable("the local voice server did not answer with a WAV file")
    Path(out).write_bytes(audio)
