"""Capture a starter pose from a webcam, video or photo with MediaPipe Hand Landmarker.

    .venv\\Scripts\\python -m pip install mediapipe        (optional dependency, ~100 MB)
    .venv\\Scripts\\python tools\\capture_pose.py --camera 0
    .venv\\Scripts\\python tools\\capture_pose.py --video signer_A.mp4 --send

The landmark -> curl math is in signova/handpose.py (unit-tested). The result is a STARTING
POINT for Pose Studio, not a validated sign. Only use video of people who agreed to it.
The hand_landmarker.task model (~8 MB) is downloaded on first use into models/.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from signova.handpose import average_poses, landmarks_to_pose, pose_to_yaml  # noqa: E402

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
MODEL_PATH = ROOT / "models" / "hand_landmarker.task"


def ensure_model() -> Path:
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {MODEL_URL} ...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def make_landmarker():
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_tasks
        from mediapipe.tasks.python import vision
    except ImportError:
        sys.exit("MediaPipe is not installed. Run:  .venv\\Scripts\\python -m pip install mediapipe")
    options = vision.HandLandmarkerOptions(
        base_options=mp_tasks.BaseOptions(model_asset_path=str(ensure_model())),
        running_mode=vision.RunningMode.IMAGE,
        num_hands=1,
    )
    return mp, vision.HandLandmarker.create_from_options(options)


def detect(mp, landmarker, rgb) -> dict[str, float] | None:
    result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if not result.hand_world_landmarks:
        return None
    return landmarks_to_pose([(p.x, p.y, p.z) for p in result.hand_world_landmarks[0]])


def frames(args: argparse.Namespace):
    import cv2

    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            sys.exit(f"Could not read {args.image}")
        yield cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        return
    cap = cv2.VideoCapture(args.video if args.video else args.camera)
    if not cap.isOpened():
        sys.exit("Could not open the camera / video.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    finally:
        cap.release()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--camera", type=int, default=0, help="webcam index (default 0)")
    src.add_argument("--video", help="video file")
    src.add_argument("--image", help="single photo")
    ap.add_argument("--frames", type=int, default=30, help="average this many frames with a detected hand")
    ap.add_argument("--save", help="write the pose as JSON to this file")
    ap.add_argument(
        "--send", nargs="?", const="http://127.0.0.1:8000", help="POST the pose to a running server"
    )
    args = ap.parse_args()

    print("Reminder: only capture people who agreed to it. The pose is a draft until a signer checks it.")
    mp, landmarker = make_landmarker()
    poses = []
    for rgb in frames(args):
        pose = detect(mp, landmarker, rgb)
        if pose:
            poses.append(pose)
            if len(poses) >= args.frames:
                break
    if not poses:
        print("No hand detected.")
        return 1
    pose = average_poses(poses)
    print(f"Averaged {len(poses)} frame(s):")
    print(json.dumps(pose))
    print("Library frame (paste under `frames:`):")
    print(pose_to_yaml(pose))
    if args.save:
        Path(args.save).write_text(json.dumps(pose, indent=2), encoding="utf-8")
    if args.send:
        import httpx

        r = httpx.post(f"{args.send}/api/pose", json={"pose": pose, "ms": 400}, timeout=5)
        print(f"Sent to {args.send}: HTTP {r.status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
