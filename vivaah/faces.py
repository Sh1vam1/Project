"""Face detection (OpenCV YuNet, bundled ONNX model, MIT licence).

Faces are the anchor for exposure, white balance and skin protection: they tell
us which pixels are skin, so a sandstone wall or a gold lehenga is never
mistaken for a face.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"
_DETECTOR = None


@dataclass
class Face:
    x: float  # box in pixels of the analysed image
    y: float
    w: float
    h: float
    score: float
    landmarks: np.ndarray  # 5x2: right eye, left eye, nose, right mouth, left mouth

    def to_dict(self, width: int, height: int) -> dict:
        return {
            "box": [round(self.x / width, 4), round(self.y / height, 4),
                    round(self.w / width, 4), round(self.h / height, 4)],
            "score": round(self.score, 3),
        }


def _detector(size: tuple[int, int]):
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = cv2.FaceDetectorYN.create(str(MODEL), "", size, 0.6, 0.3, 5000)
    _DETECTOR.setInputSize(size)
    return _DETECTOR


def detect(rgb8: np.ndarray, min_score: float = 0.6) -> list[Face]:
    """Detect faces in an 8-bit RGB image (any size; best at ~640-1280 px)."""
    h, w = rgb8.shape[:2]
    scale = 1.0
    img = rgb8
    if max(h, w) < 480:  # tiny inputs: upscale so small faces are still found
        scale = 480 / max(h, w)
        img = cv2.resize(rgb8, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_CUBIC)
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    try:
        _, found = _detector((bgr.shape[1], bgr.shape[0])).detect(bgr)
    except cv2.error:
        return []
    faces: list[Face] = []
    if found is None:
        return faces
    for row in found:
        score = float(row[14])
        if score < min_score:
            continue
        x, y, fw, fh = (row[:4] / scale).tolist()
        if fw < 6 or fh < 6:
            continue
        faces.append(Face(x, y, fw, fh, score, (row[4:14] / scale).reshape(5, 2)))
    return faces


def face_skin_mask(shape: tuple[int, int], face: Face) -> np.ndarray:
    """Boolean mask of the cheeks / nose / forehead area of a face (no eyes, hair, lips)."""
    h, w = shape
    mask = np.zeros((h, w), np.uint8)
    re, le, nose, rm, lm = face.landmarks
    eye_y = (re[1] + le[1]) / 2
    mouth_y = (rm[1] + lm[1]) / 2
    eye_dist = max(np.hypot(*(le - re)), 2.0)
    # cheeks + nose: an ellipse between the eyes and the mouth
    cx, cy = nose[0], (eye_y + mouth_y) / 2 + 0.05 * eye_dist
    axes = (max(1, int(eye_dist * 0.85)), max(1, int((mouth_y - eye_y) * 0.55 + 0.1 * eye_dist)))
    cv2.ellipse(mask, (int(cx), int(cy)), axes, 0, 0, 360, 1, -1)
    # forehead band above the eyes
    fy = eye_y - 0.55 * eye_dist
    cv2.ellipse(mask, (int((re[0] + le[0]) / 2), int(fy)),
                (max(1, int(eye_dist * 0.6)), max(1, int(eye_dist * 0.22))), 0, 0, 360, 1, -1)
    # remove eyes and mouth
    r_eye = max(1, int(eye_dist * 0.22))
    for p in (re, le):
        cv2.circle(mask, (int(p[0]), int(p[1])), r_eye, 0, -1)
    cv2.ellipse(mask, (int((rm[0] + lm[0]) / 2), int(mouth_y)),
                (max(1, int(abs(lm[0] - rm[0]) * 0.65)), max(1, int(eye_dist * 0.18))), 0, 0, 360, 0, -1)
    return mask.astype(bool)
