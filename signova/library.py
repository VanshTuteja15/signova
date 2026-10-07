"""The sign library (signs/library.yaml): schema, availability, pose vectors and safe saving.

A sign is *available* only if the configured hand has every joint in its `requires` list.
Unavailable signs are excluded from the gloss vocabulary and shown greyed out in the dashboard.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from .config import (
    JOINT_NAME_PATTERN,
    ConfigError,
    HandConfig,
    default_library_path,
    format_validation_error,
    load_yaml,
)

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
SignKind = Literal["word", "letter", "number"]
SIGN_ID_RE = re.compile(r"^[A-Z0-9_]{1,16}$")
_JOINT_RE = re.compile(JOINT_NAME_PATTERN)

DEFAULT_REST: dict[str, float] = {
    "thumb": 0.15,
    "thumb_rot": 0.2,
    "index": 0.15,
    "middle": 0.15,
    "ring": 0.18,
    "pinky": 0.2,
    "wrist": 0.5,
    "spread_index_middle": 0.0,
    "wrist_flex": 0.5,
}

# Plain words that YAML 1.1 readers would turn into booleans/null; always quote them on save.
_RISKY_KEYS = {"Y", "N", "YES", "NO", "ON", "OFF", "TRUE", "FALSE", "NULL"}


def tokenize(text: str) -> list[str]:
    """Lowercase and split into word tokens (letters, digits, apostrophes)."""
    text = text.lower().replace("’", "'")
    return [t.strip("'") for t in re.findall(r"[a-z0-9']+", text) if t.strip("'")]


def normalize_phrase(text: str) -> str:
    return " ".join(tokenize(text))


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pose: dict[str, Unit]
    hold_ms: int | None = Field(default=None, ge=0, le=10000)
    move_ms: int | None = Field(default=None, ge=0, le=5000)

    @field_validator("pose")
    @classmethod
    def _joint_names(cls, pose: dict[str, float]) -> dict[str, float]:
        for name in pose:
            if not _JOINT_RE.match(name):
                raise ValueError(f"bad joint name {name!r}")
        return pose


class Sign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SignKind
    tier: int = Field(ge=1, le=3)
    english: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    validated_by_signer: bool = False
    reviewer: str | None = None
    validated_on: str | None = None
    notes: str = ""
    frames: list[Frame] = Field(min_length=1, max_length=32)

    @field_validator("english")
    @classmethod
    def _norm_english(cls, phrases: list[str]) -> list[str]:
        out: list[str] = []
        for p in phrases:
            n = normalize_phrase(str(p))
            if n and n not in out:
                out.append(n)
        return out

    @field_validator("requires")
    @classmethod
    def _req_names(cls, req: list[str]) -> list[str]:
        for name in req:
            if not _JOINT_RE.match(name):
                raise ValueError(f"bad joint name {name!r}")
        return list(dict.fromkeys(req))

    @field_validator("validated_on")
    @classmethod
    def _iso_date(cls, v: str | None) -> str | None:
        if v and not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError("validated_on must be YYYY-MM-DD")
        return v or None


class LibraryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    rest: dict[str, Unit] = Field(default_factory=lambda: dict(DEFAULT_REST))
    signs: dict[str, Sign] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> LibraryData:
        seen: dict[str, str] = {}
        for sid, sign in self.signs.items():
            if not SIGN_ID_RE.match(sid):
                raise ValueError(f"sign id {sid!r} must be 1-16 characters of A-Z, 0-9 or _")
            if sign.kind == "letter" and sign.tier < 3 and not re.match(r"^[A-Z]$", sid):
                raise ValueError(f"letter sign {sid!r} must be a single letter A-Z")
            if sign.kind == "number" and not sid.isdigit():
                raise ValueError(f"number sign {sid!r} must use digits as its id")
            for phrase in sign.english:
                if phrase in seen and seen[phrase] != sid:
                    raise ValueError(f"phrase {phrase!r} is used by both {seen[phrase]} and {sid}")
                seen[phrase] = sid
        return self


class Library:
    """A validated sign library bound to a particular hand configuration."""

    def __init__(self, data: LibraryData, hand: HandConfig, path: Path | None = None) -> None:
        self.data = data
        self.hand = hand
        self.path = path
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, hand: HandConfig, path: Path | str | None = None) -> Library:
        p = Path(path) if path else default_library_path()
        return cls(parse_library(load_yaml(p), p.name), hand, p)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], hand: HandConfig) -> Library:
        return cls(parse_library(raw, "library"), hand, None)

    def reload(self) -> None:
        if self.path is not None:
            self.data = parse_library(load_yaml(self.path), self.path.name)

    # ------------------------------------------------------------------ queries
    @property
    def signs(self) -> dict[str, Sign]:
        return self.data.signs

    def ids(self) -> list[str]:
        return list(self.data.signs)

    def get(self, sign_id: str) -> Sign:
        return self.data.signs[sign_id]

    def resolve_id(self, raw: str | None) -> str | None:
        """Case-insensitive lookup. Returns the canonical id, or None if unknown."""
        if not raw:
            return None
        key = str(raw).strip().upper()
        return key if key in self.data.signs else None

    def missing_joints(self, sign_id: str) -> list[str]:
        sign = self.data.signs[sign_id]
        return [j for j in sign.requires if not self.hand.has_joint(j)]

    def available(self, sign_id: str) -> bool:
        return sign_id in self.data.signs and not self.missing_joints(sign_id)

    def unavailable_reason(self, sign_id: str) -> str | None:
        if sign_id not in self.data.signs:
            return f"{sign_id} is not in the library"
        missing = self.missing_joints(sign_id)
        if missing:
            return f"this hand has no {', '.join(missing)} joint"
        return None

    def available_ids(self) -> list[str]:
        return [sid for sid in self.data.signs if self.available(sid)]

    @property
    def letters(self) -> set[str]:
        """Single letters the hand can fingerspell (available letter signs)."""
        return {
            sid
            for sid, s in self.data.signs.items()
            if s.kind == "letter" and len(sid) == 1 and self.available(sid)
        }

    def phrase_map(self) -> dict[tuple[str, ...], str]:
        """English phrase tokens -> sign id, for available signs only."""
        out: dict[tuple[str, ...], str] = {}
        for sid, sign in self.data.signs.items():
            if not self.available(sid):
                continue
            for phrase in sign.english:
                out[tuple(phrase.split())] = sid
        return out

    # ------------------------------------------------------------------ vectors
    def rest_pose(self) -> dict[str, float]:
        return {
            j: self.hand.clamp(j, self.data.rest.get(j, DEFAULT_REST.get(j, 0.0)))
            for j in self.hand.joint_names
        }

    def rest_vector(self) -> list[float]:
        rest = self.rest_pose()
        return [rest[j] for j in self.hand.joint_names]

    def vector(self, pose: dict[str, float]) -> list[float]:
        """Pose dict -> joint vector in hand order. Missing joints fall back to rest."""
        rest = self.rest_pose()
        return [round(self.hand.clamp(j, pose.get(j, rest[j])), 4) for j in self.hand.joint_names]

    def pose_dict(self, vector: list[float]) -> dict[str, float]:
        return dict(zip(self.hand.joint_names, vector, strict=True))

    def frame_vectors(self, sign_id: str) -> list[list[float]]:
        return [self.vector(f.pose) for f in self.data.signs[sign_id].frames]

    # ------------------------------------------------------------------ API views
    def sign_view(self, sign_id: str) -> dict[str, Any]:
        sign = self.data.signs[sign_id]
        missing = self.missing_joints(sign_id)
        return {
            "id": sign_id,
            "kind": sign.kind,
            "tier": sign.tier,
            "english": sign.english,
            "requires": sign.requires,
            "available": not missing,
            "missing_joints": missing,
            "unavailable_reason": self.unavailable_reason(sign_id),
            "validated_by_signer": sign.validated_by_signer,
            "draft": not sign.validated_by_signer,
            "reviewer": sign.reviewer,
            "validated_on": sign.validated_on,
            "notes": sign.notes,
            "frames": [
                {
                    "pose": self.pose_dict(self.vector(f.pose)),
                    "raw": dict(f.pose),  # as stored, including joints this hand lacks
                    "hold_ms": f.hold_ms,
                    "move_ms": f.move_ms,
                }
                for f in sign.frames
            ],
        }

    def to_api(self) -> list[dict[str, Any]]:
        return [self.sign_view(sid) for sid in self.data.signs]

    # ------------------------------------------------------------------ editing
    def suggest_requires(self, sign: Sign) -> list[str]:
        """Joints whose value differs noticeably from rest in any frame (Pose Studio default)."""
        rest = {**DEFAULT_REST, **self.data.rest}
        req = []
        for name in self.hand.joint_names + [j for j in DEFAULT_REST if j not in self.hand.joint_names]:
            if any(
                abs(f.pose.get(name, rest.get(name, 0.0)) - rest.get(name, 0.0)) > 0.05 for f in sign.frames
            ):
                req.append(name)
        return req

    def upsert(self, sign_id: str, sign: Sign | dict[str, Any]) -> Sign:
        sid = sign_id.strip().upper()
        new_sign = sign if isinstance(sign, Sign) else Sign.model_validate(sign)
        with self._lock:
            signs = dict(self.data.signs)
            signs[sid] = new_sign
            new_data = parse_library(
                {"version": self.data.version, "rest": dict(self.data.rest), "signs": _dump_signs(signs)},
                "library",
            )
            self._save(new_data, upsert=sid)
            self.data = new_data
        return new_sign

    def delete(self, sign_id: str) -> None:
        sid = sign_id.strip().upper()
        with self._lock:
            if sid not in self.data.signs:
                raise KeyError(sid)
            signs = {k: v for k, v in self.data.signs.items() if k != sid}
            new_data = parse_library(
                {"version": self.data.version, "rest": dict(self.data.rest), "signs": _dump_signs(signs)},
                "library",
            )
            self._save(new_data, delete=sid)
            self.data = new_data

    def _save(self, new_data: LibraryData, upsert: str | None = None, delete: str | None = None) -> None:
        """Write the YAML file: round-trip to keep comments, validate, keep a .bak, atomic replace."""
        if self.path is None:
            return
        rt = YAML()
        rt.preserve_quotes = True
        rt.width = 120
        rt.indent(mapping=2, sequence=4, offset=2)
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as fh:
                doc = rt.load(fh)
        else:
            doc = CommentedMap()
        if not isinstance(doc, CommentedMap):
            doc = CommentedMap()
        doc.setdefault("version", new_data.version)
        if "rest" not in doc:
            doc["rest"] = _flow_map(new_data.rest)
        if "signs" not in doc or not isinstance(doc["signs"], CommentedMap):
            doc["signs"] = CommentedMap()
        signs_map: CommentedMap = doc["signs"]

        if delete is not None:
            for key in list(signs_map.keys()):
                if str(key).upper() == delete:
                    del signs_map[key]
        if upsert is not None:
            existing = next((k for k in signs_map if str(k).upper() == upsert), None)
            node = _sign_node(new_data.signs[upsert])
            if existing is not None:
                signs_map[existing] = node
            else:
                key = (
                    DoubleQuotedScalarString(upsert)
                    if (not upsert.isalpha() or upsert in _RISKY_KEYS)
                    else upsert
                )
                signs_map[key] = node

        buf = io.StringIO()
        rt.dump(doc, buf)
        text = buf.getvalue()
        # Re-validate exactly what we are about to write.
        parse_library(YAML(typ="safe", pure=True).load(text), self.path.name)

        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + ".bak"))
        os.replace(tmp, self.path)


def parse_library(raw: object, source: str = "library") -> LibraryData:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source} must contain a YAML mapping")
    raw = dict(raw)
    # Keys like 1 or NO must be strings; be forgiving if a YAML 1.1 tool turned them into ints/bools.
    signs = raw.get("signs") or {}
    if isinstance(signs, dict):
        fixed: dict[str, Any] = {}
        for k, v in signs.items():
            key = {True: "YES", False: "NO"}.get(k, str(k)) if isinstance(k, bool) else str(k)
            fixed[key] = v
        raw["signs"] = fixed
    try:
        return LibraryData.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"{source} is invalid: {format_validation_error(exc)}") from exc


def _dump_signs(signs: dict[str, Sign]) -> dict[str, Any]:
    return {k: v.model_dump() for k, v in signs.items()}


def _flow_map(d: dict[str, float]) -> CommentedMap:
    m = CommentedMap((k, float(v)) for k, v in d.items())
    m.fa.set_flow_style()
    return m


def _flow_seq(items: list[str]) -> CommentedSeq:
    s = CommentedSeq(items)
    s.fa.set_flow_style()
    return s


def _sign_node(sign: Sign) -> CommentedMap:
    node = CommentedMap()
    node["kind"] = sign.kind
    node["tier"] = sign.tier
    node["english"] = _flow_seq(list(sign.english))
    node["requires"] = _flow_seq(list(sign.requires))
    node["validated_by_signer"] = sign.validated_by_signer
    if sign.reviewer:
        node["reviewer"] = sign.reviewer
    if sign.validated_on:
        node["validated_on"] = sign.validated_on
    node["notes"] = sign.notes
    frames = CommentedSeq()
    for f in sign.frames:
        fm = CommentedMap()
        fm["pose"] = _flow_map({k: round(v, 3) for k, v in f.pose.items()})
        if f.hold_ms is not None:
            fm["hold_ms"] = f.hold_ms
        if f.move_ms is not None:
            fm["move_ms"] = f.move_ms
        frames.append(fm)
    node["frames"] = frames
    return node
