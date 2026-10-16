"""Application configuration: YAML file + ``.env`` for secrets, validated with pydantic.

Resolution order: explicit path > ``PIT_WALL_CONFIG`` > ``config/local.yaml`` (if present) > built-in
defaults (the same ones ``config/default.yaml`` documents). Secrets (API keys) are never part of the
config: they are read from the environment at the moment of use, so they cannot end up in a log or a dump.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

SessionName = Literal["practice", "qualify", "race", "hotlap", "time_attack", "drift", "drag"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in the YAML is an error, not a silent no-op


class ReplayConfig(_Model):
    file: str | None = None
    speed: float = Field(1.0, gt=0, le=50)
    start_s: float = Field(0.0, ge=0)
    loop: bool = False
    session: SessionName = "practice"


class CspBridgeConfig(_Model):
    """Real tyre wear from the CSP Lua app in ``tools/csp_bridge`` (the classic shared memory keeps it at 100 %)."""

    enabled: bool = True  # listens on 127.0.0.1 only; harmless when the app is not installed
    port: int = Field(28271, ge=1024, le=65535)
    max_age_s: float = Field(3.0, gt=0)  # older than this = the app is not sending


class TelemetryConfig(_Model):
    source: Literal["ac", "replay"] = "ac"
    hz: float = Field(60.0, gt=0, le=240)
    buffer_seconds: float = Field(30.0, gt=0, le=3600)
    retry_s: float = Field(1.0, gt=0)
    replay: ReplayConfig = ReplayConfig()
    csp_bridge: CspBridgeConfig = CspBridgeConfig()


class LoggingConfig(_Model):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_logs: bool = False
    file: str | None = None


class RateLimitConfig(_Model):
    """Adaptive pacing: starts at ``initial_rpm``, backs off on a 429, creeps up while calls succeed."""

    adaptive: bool = True
    initial_rpm: float = Field(6.0, gt=0)
    min_rpm: float = Field(1.0, gt=0)
    max_rpm: float = Field(10.0, gt=0)
    increase_every_ok: int = Field(10, ge=1)
    increase_step_rpm: float = Field(0.5, gt=0)
    decrease_factor: float = Field(0.5, gt=0, lt=1)
    cooldown_after_429_s: float = Field(15.0, ge=0)
    # hard cap of requests per local day; None = no cap. Failed requests count against the provider's
    # own daily quota too, so the default stays under the 500/day seen for the Flash-Lite models.
    daily_budget: int | None = Field(400, ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> RateLimitConfig:
        if not self.min_rpm <= self.initial_rpm <= self.max_rpm:
            raise ValueError("need min_rpm <= initial_rpm <= max_rpm")
        return self


class LLMConfig(_Model):
    provider: str = "mock"  # "mock" works offline; "gemini" talks to Google over HTTPS
    # Nothing is sent to a cloud model unless this is true (the free tier may use inputs to improve
    # Google's products). With it false only the mock provider and the local answers work.
    allow_cloud: bool = False
    model: str = "gemini-flash-lite-latest"  # the ONLY place a model name lives; `pit-wall llm-check` lists valid ones
    # Tried in order when the model above is overloaded (503) or times out. Names only here, in YAML.
    fallback_models: list[str] = Field(default_factory=lambda: ["gemini-3.1-flash-lite"])
    # Tokens the model may spend thinking before it answers (counted in the output budget). None = do not
    # send the setting; 0 = no thinking where the model allows it. Lower = faster, cheaper, shorter answers.
    thinking_budget: int | None = Field(None, ge=0)
    # Per model, wins over thinking_budget. Not every model accepts the setting (the Flash-Lite alias
    # answered 400 to 0 on 2026-10-14, gemini-3.5-flash accepted it): list only the ones that do.
    thinking_budgets: dict[str, int] = Field(default_factory=dict)
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    temperature: float = Field(0.2, ge=0, le=2)
    api_key_env: str = "GEMINI_API_KEY"
    timeout_s: float = Field(
        3.5, gt=0
    )  # per model: with a reserve model a dead call costs at most twice this
    max_output_tokens: int = Field(300, ge=16)
    cache_ttl_s: float = Field(20.0, ge=0)
    rate_limit: RateLimitConfig = RateLimitConfig()

    def api_key(self, env: Mapping[str, str] | None = None) -> str | None:
        """The first key from the environment (never stored on the object)."""
        keys = self.api_keys(env)
        return keys[0] if keys else None

    def api_keys(self, env: Mapping[str, str] | None = None) -> list[str]:
        """Every key given, in order of use: ``NAME``, ``NAME_2`` ... ``NAME_9`` and ``NAME_LIST`` (commas).

        More than one is a reserve: when the first is over its quota the next one is used.
        """
        e = env if env is not None else os.environ
        found: list[str] = []
        names = [self.api_key_env, *(f"{self.api_key_env}_{n}" for n in range(2, 10))]
        for name in names:
            v = (e.get(name) or "").strip()
            if v:
                found.append(v)
        for v in (e.get(f"{self.api_key_env}_LIST") or "").split(","):
            if v.strip():
                found.append(v.strip())
        return list(dict.fromkeys(found))


# ── engineer (rule-based alerts + voice) ─────────────────────────────────────────────────────────────
class CalmConfig(_Model):
    """When is the driver free to listen? Low and medium priority messages wait for this."""

    brake_below: float = Field(0.10, ge=0, le=1)  # brake pedal 0..1
    steer_below: float = Field(0.15, ge=0, le=1)  # fraction of the steering lock, 0..1
    hold_s: float = Field(0.30, ge=0)  # how long both must hold
    min_gap_s: float = Field(1.5, ge=0)  # silence between two messages


class VoiceConfig(_Model):
    backend: Literal["sapi", "command", "console", "none"] = (
        "sapi"  # sapi = Windows voices; command = your own synthesiser; console = print only
    )
    # Part of the name of the audio output the voice speaks through (a headset, speakers...); None = default.
    # `pit-wall devices` lists them. For backend: command, `command` is the template (see default.yaml).
    output_device: str | None = None
    command: str | None = None
    name: str | None = None  # e.g. "Sabina"; None = first voice of the language
    rate: int = Field(1, ge=-10, le=10)  # SAPI speed, 0 = normal
    volume: int = Field(100, ge=0, le=100)


class PolicyConfig(_Model):
    """What may speak in each kind of session. Priorities: low < medium < high < critical."""

    min_priority: Literal["low", "medium", "high", "critical"] = "low"
    allow_rules: list[str] = Field(default_factory=list)  # spoken even below ``min_priority``


class LockUpRule(_Model):
    enabled: bool = True
    ratio: float = Field(0.70, gt=0, lt=1)  # wheel speed / car speed below this = locked
    min_speed_kmh: float = Field(50.0, ge=0)
    min_brake: float = Field(0.40, ge=0, le=1)  # only while really braking
    hold_s: float = Field(0.25, ge=0)
    cooldown_s: float = Field(12.0, ge=0)


class WheelspinRule(_Model):
    enabled: bool = False  # off by default: it is chatty on slow corners
    ratio: float = Field(1.30, gt=1)
    min_gas: float = Field(0.70, ge=0, le=1)
    min_speed_kmh: float = Field(20.0, ge=0)
    hold_s: float = Field(0.40, ge=0)
    cooldown_s: float = Field(20.0, ge=0)


class SpinRule(_Model):
    enabled: bool = True
    slip_angle_deg: float = Field(
        60.0, gt=0, lt=180
    )  # angle between the car and its direction of travel
    min_speed_kmh: float = Field(30.0, ge=0)
    hold_s: float = Field(0.15, ge=0)
    cooldown_s: float = Field(10.0, ge=0)


class OffTrackRule(_Model):
    enabled: bool = True
    wheels_out: int = Field(3, ge=1, le=4)
    hold_s: float = Field(1.0, ge=0)
    min_speed_kmh: float = Field(20.0, ge=0)
    cooldown_s: float = Field(20.0, ge=0)


class FuelRule(_Model):
    enabled: bool = True
    warn_laps: float = Field(2.0, gt=0)  # "fuel for N laps" at or below this
    critical_laps: float = Field(0.8, gt=0)  # interrupts at or below this
    laps_for_estimate: int = Field(2, ge=1)  # timed laps used to average the consumption
    cooldown_s: float = Field(90.0, ge=0)


class TyreTempRule(_Model):
    enabled: bool = True
    hot_c: float = Field(115.0, gt=0)  # core temperature. CALIBRATE per compound (see below)
    rearm_c: float = Field(105.0, gt=0)  # must drop below this before it can warn again
    hold_s: float = Field(3.0, ge=0)
    cooldown_s: float = Field(90.0, ge=0)


class TyrePressureRule(_Model):
    enabled: bool = True
    high_psi: float = Field(40.0, gt=0)
    puncture_drop_psi: float = Field(
        4.0, gt=0
    )  # a drop this fast is a puncture, not a cooling tyre
    puncture_window_s: float = Field(3.0, gt=0)
    cooldown_s: float = Field(60.0, ge=0)


class BrakeTempRule(_Model):
    enabled: bool = True
    hot_c: float = Field(800.0, gt=0)
    alive_range_c: float = Field(8.0, gt=0)  # the sensor must move this much, else it is ignored
    cooldown_s: float = Field(90.0, ge=0)


class PenaltyRule(_Model):
    enabled: bool = True
    cooldown_s: float = Field(5.0, ge=0)


class FlagsRule(_Model):
    enabled: bool = False  # UNVERIFIED: no flag has been seen in a real capture
    cooldown_s: float = Field(10.0, ge=0)


class DamageRule(_Model):
    enabled: bool = False  # UNVERIFIED: no damage has been seen in a real capture
    min_increase: float = Field(5.0, gt=0)
    cooldown_s: float = Field(10.0, ge=0)


class LapSummaryRule(_Model):
    enabled: bool = True
    include_delta: bool = True


class BestSectorRule(_Model):
    enabled: bool = False  # chatty: one per sector, every lap
    cooldown_s: float = Field(0.0, ge=0)


class BrakingHintRule(_Model):
    enabled: bool = True  # needs the track's racing line in the local game install
    cooldown_s: float = Field(0.0, ge=0)


class RulesConfig(_Model):
    lockup: LockUpRule = LockUpRule()
    wheelspin: WheelspinRule = WheelspinRule()
    spin: SpinRule = SpinRule()
    offtrack: OffTrackRule = OffTrackRule()
    fuel: FuelRule = FuelRule()
    tyre_temp: TyreTempRule = TyreTempRule()
    tyre_pressure: TyrePressureRule = TyrePressureRule()
    brake_temp: BrakeTempRule = BrakeTempRule()
    penalty: PenaltyRule = PenaltyRule()
    flags: FlagsRule = FlagsRule()
    damage: DamageRule = DamageRule()
    lap_summary: LapSummaryRule = LapSummaryRule()
    best_sector: BestSectorRule = BestSectorRule()
    braking_hint: BrakingHintRule = BrakingHintRule()


class PoliciesConfig(_Model):
    practice: PolicyConfig = PolicyConfig(min_priority="low")
    qualify: PolicyConfig = PolicyConfig(min_priority="medium")
    race: PolicyConfig = PolicyConfig(
        min_priority="critical", allow_rules=["lap_summary", "fuel_low", "penalty"]
    )
    other: PolicyConfig = PolicyConfig(min_priority="medium")


class PersonaConfig(_Model):
    """Who the engineer is and how they speak (changes the wording, never the data)."""

    name: str | None = None  # how the engineer introduces themselves; None = the voice's name
    driver_name: str | None = None  # how the engineer calls the driver
    style: Literal["neutral", "brief", "calm", "technical", "warm", "direct"] = "neutral"
    custom: str | None = Field(None, max_length=400)  # the driver's own style notes


class MilestonesConfig(_Model):
    """Pole, grid and result messages: "tenemos pole provisional", "felicidades por esa remontada"."""

    enabled: bool = True
    use_model: bool = (
        True  # wording written by the model (new every time); False = the fixed variants only
    )


class MemoryConfig(_Model):
    """What the engineer said, kept between sessions in a local file (``pit-wall`` never sends it anywhere)."""

    enabled: bool = True
    path: str | None = None  # None = ~/.pit-wall-ai/memory.jsonl
    max_records: int = Field(500, ge=50, le=20000)


class EngineerConfig(_Model):
    enabled: bool = False  # opt in: `pit-wall run --voice` or enabled: true
    language: Literal["es", "en", "de"] = "es"
    voice: VoiceConfig = VoiceConfig()
    persona: PersonaConfig = PersonaConfig()
    milestones: MilestonesConfig = MilestonesConfig()
    memory: MemoryConfig = MemoryConfig()
    calm: CalmConfig = CalmConfig()
    policies: PoliciesConfig = PoliciesConfig()
    rules: RulesConfig = RulesConfig()
    startup_quiet_s: float = Field(5.0, ge=0)  # silence right after a session starts
    # the game's shared-memory tyre wear does not move in this setup (the tyre app shows 18 % where it says 100 %):
    # off, the engineer does not state a wear it cannot know; on, it reads that channel
    wear_channel: bool = False


class BrakingConfig(_Model):
    """How braking is measured per corner and when a difference is worth saying."""

    brake_on: float = Field(0.10, gt=0, lt=1)  # pedal above this = braking
    min_peak: float = Field(
        0.30, gt=0, le=1
    )  # an event must reach this to count as the corner's braking
    lookback_m: float = Field(
        350.0, gt=0
    )  # how far before the corner entry the braking is searched
    merge_gap_m: float = Field(20.0, ge=0)  # events closer than this are one braking
    throttle_on: float = Field(
        0.50, gt=0, le=1
    )  # throttle above this after the slowest point = back on power
    min_distance_m: float = Field(
        10.0, gt=0
    )  # a braking-point difference smaller than this is not said
    min_time_s: float = Field(0.10, gt=0)  # a time loss in the corner smaller than this is not said
    big_loss_s: float = Field(
        0.50, gt=0
    )  # a loss this big is said even without a braking point to compare


class AnalysisConfig(_Model):
    """Track geometry and braking analysis (phase 4). The geometry is generated locally from the game."""

    ac_dirs: list[str] = Field(
        default_factory=list
    )  # game / Steam library / tracks folders to search
    braking: BrakingConfig = BrakingConfig()
    # circuits whose corner NUMBERING the user has checked against the real track: only for those does
    # the voice say "corner N". Without a name or a verified number it describes the stretch.
    verified_numbering: list[str] = Field(default_factory=list)
    # laps of the same set of tyres (incident laps left out) before the engineer says where they wear most
    tyre_corners_min_laps: int = Field(6, ge=3, le=40)


class ChatConfig(_Model):
    """The text conversation with the engineer (phase 5)."""

    history_turns: int = Field(
        6, ge=0, le=30
    )  # previous question/answer pairs sent with a question
    stale_after_s: float = Field(5.0, gt=0)  # older data is announced as such in a model answer


class InputConfig(_Model):
    """Buttons bound to actions (see docs/INPUT_BINDINGS.md). ``pit-wall bind`` writes ``config/bindings.yaml``."""

    poll_hz: float = Field(
        200.0, ge=10, le=1000
    )  # joystick polling; keyboard and mouse are event driven
    bindings: dict[str, list[dict]] = Field(default_factory=dict)


class AudioConfig(_Model):
    """The microphone for push-to-talk (phase 6). 16 kHz mono is what the recogniser wants."""

    input_device: str | None = (
        None  # part of the name; None = the system default. `pit-wall devices`
    )
    preroll_ms: int = Field(200, ge=0, le=2000)  # audio kept from before the button went down
    tail_ms: int = Field(150, ge=0, le=2000)  # audio kept after it went up
    min_utterance_ms: int = Field(300, ge=50)  # shorter than this is an accidental touch
    keep_audio: bool = False  # keep the last utterances on disk for debugging (never committed)


class RadioConfig(_Model):
    """How the conversation shares the channel with the alerts (phase 6, step 4)."""

    priority: Literal["first_to_start", "driver"] = "first_to_start"
    # "do not bother me": any later word of the driver ends it (False: only "you can talk" does)
    quiet_ends_on_speech: bool = True
    ack_after_s: float = Field(
        1.5, ge=0.0, le=10.0
    )  # "one moment" when the answer takes longer (0 = never)


class SttConfig(_Model):
    """Speech recognition (phase 6). Where it runs is a user setting: iRacing is CPU-hungry, AC is not."""

    backend: str = "faster-whisper"
    model: str = "small"  # tiny | base | small | medium | a path or a Hugging Face id
    device: str = "auto"  # auto | cpu | cuda
    compute_type: str = "auto"  # auto | int8 | float16 | int8_float16 ...
    threads: int = Field(4, ge=1, le=32)  # CPU threads used by the recogniser
    beam_size: int = Field(1, ge=1, le=10)
    language: str | None = None  # None = the engineer's language
    model_dir: str | None = None  # where the models are kept; None = the Hugging Face cache


class AppConfig(_Model):
    telemetry: TelemetryConfig = TelemetryConfig()
    logging: LoggingConfig = LoggingConfig()
    llm: LLMConfig = LLMConfig()
    engineer: EngineerConfig = EngineerConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    chat: ChatConfig = ChatConfig()
    input: InputConfig = InputConfig()
    audio: AudioConfig = AudioConfig()
    stt: SttConfig = SttConfig()
    radio: RadioConfig = RadioConfig()


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = (
            _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        )
    return out


def load_config(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> AppConfig:
    """Load and validate the configuration. Loads ``.env`` into the process environment first."""
    environ = os.environ if env is None else env
    if env is None:
        load_dotenv(override=False)
    chosen = path or environ.get("PIT_WALL_CONFIG")
    if (
        not chosen and env is None and Path("config/local.yaml").is_file()
    ):  # an explicit env is hermetic
        chosen = "config/local.yaml"
    data: dict = {}
    if chosen:
        p = Path(chosen)
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        try:
            loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{p}: invalid YAML: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"{p}: the top level must be a mapping")
        data = _deep_merge(data, loaded)
    bindings_file = Path("config/bindings.yaml")  # written by `pit-wall bind`, git-ignored
    if env is None and bindings_file.is_file():
        try:
            data = _deep_merge(
                data, yaml.safe_load(bindings_file.read_text(encoding="utf-8")) or {}
            )
        except yaml.YAMLError as exc:
            raise ConfigError(f"{bindings_file}: invalid YAML: {exc}") from exc
    try:
        return AppConfig.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError
        raise ConfigError(str(exc)) from exc
