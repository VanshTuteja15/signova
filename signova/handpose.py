"""Hand landmarks (MediaPipe's 21-point model) -> a starter SIGNOVA pose.

Pure functions, no MediaPipe import, so they are unit-tested on synthetic landmarks.
Used by tools/capture_pose.py. The result is a *starting point* for Pose Studio, not a
validated sign: a fluent signer still has to check it.

Landmark indices: 0 wrist; thumb 1 CMC, 2 MCP, 3 IP, 4 tip; index 5-8; middle 9-12;
ring 13-16; pinky 17-20 (MCP, PIP, DIP, tip).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

FINGER_CHAINS = {
    "index": (0, 5, 6, 7, 8),
    "middle": (0, 9, 10, 11, 12),
    "ring": (0, 13, 14, 15, 16),
    "pinky": (0, 17, 18, 19, 20),
}
THUMB_CHAIN = (1, 2, 3, 4)
# Same mapping as the 3D hand / firmware docs: curl 1.0 = 85 + 100 + 70 degrees of flexion.
FINGER_FULL_DEG = 85.0 + 100.0 + 70.0
THUMB_FULL_DEG = 55.0 + 60.0  # MCP + IP flexion at thumb bend 1.0
# Thumb tip position along the index-MCP -> pinky-MCP axis (0 = at the index knuckle, 1 = at the pinky
# knuckle). Out to the side is negative; across the palm is positive.
THUMB_OPEN_T = -0.6
THUMB_ACROSS_T = 0.6

Landmarks = Sequence[Sequence[float]]


def _as_array(landmarks: Landmarks) -> np.ndarray:
    pts = np.asarray(landmarks, dtype=float)
    if pts.shape != (21, 3):
        raise ValueError(f"expected 21 landmarks with x, y, z; got shape {pts.shape}")
    return pts


def bend_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Flexion at joint b: angle between segment a->b and segment b->c (0 = straight)."""
    u, v = b - a, c - b
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu < 1e-9 or nv < 1e-9:
        return 0.0
    cos = float(np.clip(np.dot(u, v) / (nu * nv), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos)))


def chain_bend(pts: np.ndarray, chain: Sequence[int]) -> float:
    return sum(
        bend_deg(pts[chain[i - 1]], pts[chain[i]], pts[chain[i + 1]]) for i in range(1, len(chain) - 1)
    )


def _unit(x: float) -> float:
    return round(float(min(1.0, max(0.0, x))), 3)


def landmarks_to_pose(landmarks: Landmarks) -> dict[str, float]:
    """21 landmarks (world or normalised coordinates) -> {thumb, thumb_rot, index, middle, ring, pinky}.

    Wrist rotation can't be judged reliably from one camera view, so it is left out
    (missing joints fall back to the library's rest pose).
    """
    pts = _as_array(landmarks)
    pose = {name: _unit(chain_bend(pts, chain) / FINGER_FULL_DEG) for name, chain in FINGER_CHAINS.items()}
    pose["thumb"] = _unit(chain_bend(pts, THUMB_CHAIN) / THUMB_FULL_DEG)
    axis = pts[17] - pts[5]
    denom = float(np.dot(axis, axis))
    t = float(np.dot(pts[4] - pts[5], axis) / denom) if denom > 1e-12 else 0.0
    pose["thumb_rot"] = _unit((t - THUMB_OPEN_T) / (THUMB_ACROSS_T - THUMB_OPEN_T))
    return {k: pose[k] for k in ("thumb", "thumb_rot", "index", "middle", "ring", "pinky")}


def average_poses(poses: Sequence[dict[str, float]]) -> dict[str, float]:
    """Average several frames (reduces landmark jitter)."""
    if not poses:
        raise ValueError("no poses to average")
    keys = poses[0].keys()
    return {k: round(sum(p[k] for p in poses) / len(poses), 3) for k in keys}


def pose_to_yaml(pose: dict[str, float]) -> str:
    """One library frame line, ready to paste under `frames:` in signs/library.yaml."""
    inner = ", ".join(f"{k}: {v:g}" for k, v in pose.items())
    return f"      - pose: {{{inner}}}"
