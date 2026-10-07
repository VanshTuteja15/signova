"""Landmark -> pose math (signova/handpose.py) on synthetic MediaPipe-style hands."""

from __future__ import annotations

import math

import numpy as np
import pytest

from signova.handpose import average_poses, bend_deg, landmarks_to_pose, pose_to_yaml

Z = np.array([0.0, 0.0, 1.0])  # palm normal (toward the viewer / palm side)
MCPS = {"index": (0.030, 0.090), "middle": (0.010, 0.095), "ring": (-0.010, 0.090), "pinky": (-0.030, 0.080)}
FIRST = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}
LENS = (0.040, 0.025, 0.020)


def chain(
    start: np.ndarray, d0: np.ndarray, lens: tuple[float, ...], bends: tuple[float, ...]
) -> list[np.ndarray]:
    """Points along a chain whose direction starts at d0 and bends toward +z by each angle in turn."""
    pts = [start]
    phi = 0.0
    p = start
    for length, bend in zip(lens, bends, strict=True):
        phi += math.radians(bend)
        d = math.cos(phi) * d0 + math.sin(phi) * Z
        p = p + length * d
        pts.append(p)
    return pts


def make_hand(
    curls: dict[str, tuple[float, float, float]] | None = None,
    thumb_bends: tuple[float, float] = (0.0, 0.0),
    thumb_across: bool = False,
) -> list[tuple[float, float, float]]:
    curls = curls or {}
    pts: list[np.ndarray] = [np.zeros(3)] * 21
    pts[0] = np.zeros(3)  # wrist
    for name, (x, y) in MCPS.items():
        mcp = np.array([x, y, 0.0])
        d0 = mcp / np.linalg.norm(mcp)
        # MCP flexion is measured against wrist->MCP, so the chain starts at the MCP
        seg = chain(mcp, d0, LENS, curls.get(name, (0.0, 0.0, 0.0)))
        for k, p in enumerate(seg):
            pts[FIRST[name] + k] = p
    cmc = np.array([0.025, 0.020, 0.0])
    pts[1] = cmc
    d0 = np.array([-0.55, 0.55, 0.62]) if thumb_across else np.array([0.80, 0.60, 0.0])
    d0 = d0 / np.linalg.norm(d0)
    mcp = cmc + 0.035 * d0
    pts[2] = mcp
    seg = chain(mcp, d0, (0.030, 0.025), thumb_bends)
    pts[3], pts[4] = seg[1], seg[2]
    return [tuple(float(v) for v in p) for p in pts]


def test_open_hand_is_all_zero() -> None:
    pose = landmarks_to_pose(make_hand())
    assert pose == {"thumb": 0.0, "thumb_rot": 0.0, "index": 0.0, "middle": 0.0, "ring": 0.0, "pinky": 0.0}


def test_full_and_half_curl() -> None:
    full = (85.0, 100.0, 70.0)
    half = (42.5, 50.0, 35.0)
    pose = landmarks_to_pose(make_hand({"index": full, "middle": half, "ring": full, "pinky": (0, 0, 0)}))
    assert pose["index"] == pytest.approx(1.0, abs=0.01)
    assert pose["middle"] == pytest.approx(0.5, abs=0.01)
    assert pose["ring"] == pytest.approx(1.0, abs=0.01)
    assert pose["pinky"] == pytest.approx(0.0, abs=0.01)


def test_thumb_bend_and_rotation() -> None:
    bent = landmarks_to_pose(make_hand(thumb_bends=(55.0, 60.0)))
    assert bent["thumb"] == pytest.approx(1.0, abs=0.01)
    across = landmarks_to_pose(make_hand(thumb_across=True))
    assert across["thumb_rot"] > 0.8
    assert landmarks_to_pose(make_hand())["thumb_rot"] == 0.0


def test_ily_like_hand_matches_library_draft() -> None:
    full = (85.0, 100.0, 70.0)
    pose = landmarks_to_pose(make_hand({"middle": full, "ring": full}))
    library_ily = {"thumb": 0.0, "thumb_rot": 0.0, "index": 0.0, "middle": 1.0, "ring": 1.0, "pinky": 0.0}
    for joint, value in library_ily.items():
        assert pose[joint] == pytest.approx(value, abs=0.1), joint


def test_values_are_clamped() -> None:
    pose = landmarks_to_pose(make_hand({"index": (120.0, 120.0, 120.0)}))
    assert pose["index"] == 1.0


def test_bad_input() -> None:
    with pytest.raises(ValueError, match="21 landmarks"):
        landmarks_to_pose([(0, 0, 0)] * 20)
    a = np.zeros(3)
    assert bend_deg(a, a, np.ones(3)) == 0.0  # degenerate segment


def test_average_and_yaml() -> None:
    avg = average_poses([{"index": 0.2, "thumb": 1.0}, {"index": 0.4, "thumb": 0.0}])
    assert avg == {"index": 0.3, "thumb": 0.5}
    with pytest.raises(ValueError):
        average_poses([])
    assert pose_to_yaml({"thumb": 0.7, "index": 0.0}) == "      - pose: {thumb: 0.7, index: 0}"
