"""Hand configuration (config/hand.yaml) and application settings.

The joint list always comes from the hand config, never from code. Joint values are
normalised 0.0 (open / neutral) .. 1.0 (fully closed / rotated) everywhere in Python.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
"""Project root (the folder containing config/, signs/, dashboard/)."""

KNOWN_JOINTS = (
    "thumb",
    "thumb_rot",
    "index",
    "middle",
    "ring",
    "pinky",
    "wrist",
    "spread_index_middle",
    "wrist_flex",
)
"""Joints the dashboard's 3D hand knows how to draw. Other names are allowed but not drawn."""

JOINT_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"

TransportMode = Literal["sim", "emulator", "serial"]


class ConfigError(Exception):
    """A config or library file is missing or invalid. The message is user-facing."""


class JointSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=JOINT_NAME_PATTERN)
    label: str | None = None
    min: float = Field(default=0.0, ge=0.0, le=1.0)
    max: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_range(self) -> JointSpec:
        if self.min >= self.max:
            raise ValueError(f"joint {self.name}: min ({self.min}) must be below max ({self.max})")
        if not self.label:
            self.label = self.name.replace("_", " ").capitalize()
        return self


class TransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: TransportMode = "sim"
    port: str | None = None
    baud: int = Field(default=115200, gt=0)
    heartbeat_s: float = Field(default=1.0, gt=0.05, le=4.0)


class TimingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    move_ms: int = Field(default=300, ge=0, le=5000)
    word_hold_ms: int = Field(default=800, ge=0, le=10000)
    letter_hold_ms: int = Field(default=450, ge=0, le=10000)
    bounce_ms: int = Field(default=150, ge=0, le=2000)
    bounce_fraction: float = Field(default=0.15, ge=0.0, le=1.0)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    done_timeout_extra_ms: int = Field(default=1500, ge=100, le=30000)


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_range_ms: int = Field(default=250, ge=0, le=5000)
    watchdog_ms: int = Field(default=5000, ge=500, le=600000)


class HandConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "SIGNOVA hand"
    driver: Literal["pca9685", "feetech"] = "pca9685"
    joints: list[JointSpec] = Field(min_length=1, max_length=16)
    transport: TransportConfig = TransportConfig()
    timing: TimingConfig = TimingConfig()
    limits: LimitsConfig = LimitsConfig()

    @field_validator("joints")
    @classmethod
    def _unique_joints(cls, joints: list[JointSpec]) -> list[JointSpec]:
        names = [j.name for j in joints]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate joint names: {', '.join(dupes)}")
        return joints

    @property
    def joint_names(self) -> list[str]:
        return [j.name for j in self.joints]

    def has_joint(self, name: str) -> bool:
        return name in self.joint_names

    def joint(self, name: str) -> JointSpec:
        for j in self.joints:
            if j.name == name:
                return j
        raise KeyError(name)

    def clamp(self, name: str, value: float) -> float:
        """Clamp a normalised value to 0..1 and to the joint's soft limits."""
        j = self.joint(name)
        return max(j.min, min(j.max, max(0.0, min(1.0, float(value)))))


def load_yaml(path: Path) -> object:
    """Load YAML 1.2 (so NO / Y / ON stay strings) and return plain Python objects."""
    yaml = YAML(typ="safe", pure=True)
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"File not found: {path}") from exc
    except Exception as exc:  # ruamel raises many error types; surface one friendly message
        raise ConfigError(f"Could not read {path.name}: {exc}") from exc


def format_validation_error(exc: Exception) -> str:
    """Turn a pydantic ValidationError into short, human-readable lines."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)
    lines = []
    for err in errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        lines.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(lines)


def load_hand_config(path: Path | str | None = None) -> HandConfig:
    p = Path(path) if path else default_hand_path()
    data = load_yaml(p)
    if not isinstance(data, dict):
        raise ConfigError(f"{p.name} must contain a YAML mapping")
    try:
        return HandConfig.model_validate(data)
    except Exception as exc:
        raise ConfigError(f"{p.name} is invalid: {format_validation_error(exc)}") from exc


def _env_path(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    if not value:
        return default
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def default_hand_path() -> Path:
    return _env_path("SIGNOVA_HAND_CONFIG", ROOT / "config" / "hand.yaml")


def default_library_path() -> Path:
    return _env_path("SIGNOVA_LIBRARY", ROOT / "signs" / "library.yaml")


def default_data_dir() -> Path:
    return _env_path("SIGNOVA_DATA_DIR", ROOT / "data")


def load_dotenv_once() -> None:
    """Load a git-ignored .env from the project root, if present. Never overrides real env vars."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is required, but stay friendly
        return
    env = ROOT / ".env"
    if env.exists():
        load_dotenv(env, override=False)
