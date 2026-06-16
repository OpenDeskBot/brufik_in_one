"""MediaPipe 人脸检测（/domain 层，无 WebSocket 依赖）。"""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Optional

import numpy as np

from deskbot_server.constants import CAMERA_MODEL_DEFAULT_PATH
from deskbot_server.vision.geometry import (
    FACE_FRAME_HEIGHT,
    FACE_FRAME_WIDTH,
    FACE_KEYPOINT_NAMES,
    MP_FACE_5PT_INDICES,
    MP_FACE_DETAIL_INDICES,
    MP_FACE_DETAIL_NAMES,
)
from deskbot_server.vision.undistort import CameraUndistorter

logger = logging.getLogger("deskbot-server")


def resolve_camera_model_path() -> str:
    env = os.environ.get("CAMERA_FACE_LANDMARKER_PATH")
    if env and os.path.isfile(env):
        return env
    return CAMERA_MODEL_DEFAULT_PATH


def _extract_face_landmarks(
    face: Any, *, w: int, h: int, coord_w: int, coord_h: int
) -> Optional[dict[str, Any]]:
    sx = float(coord_w)
    sy = float(coord_h)

    def _scaled(nx: float, ny: float) -> tuple[float, float]:
        x = max(0.0, min(sx, nx * sx))
        y = max(0.0, min(sy, ny * sy))
        return round(x, 2), round(y, 2)

    points: list = []
    for name in FACE_KEYPOINT_NAMES:
        idxs = MP_FACE_5PT_INDICES.get(name)
        if not idxs:
            continue
        try:
            xs = [face[i].x for i in idxs]
            ys = [face[i].y for i in idxs]
        except IndexError:
            continue
        nx = sum(xs) / len(xs)
        ny = sum(ys) / len(ys)
        x, y = _scaled(nx, ny)
        points.append({"name": name, "x": x, "y": y})
    if not points:
        return None

    landmarks: list = []
    for name in MP_FACE_DETAIL_NAMES:
        idx = MP_FACE_DETAIL_INDICES.get(name)
        if idx is None:
            continue
        try:
            lm = face[idx]
        except IndexError:
            continue
        nx = max(0.0, min(1.0, float(lm.x)))
        ny = max(0.0, min(1.0, float(lm.y)))
        landmarks.append({
            "name": name,
            "x": round(nx * sx, 2),
            "y": round(ny * sy, 2),
        })

    return {
        "points": points,
        "landmarks": landmarks,
        "image_w": int(coord_w),
        "image_h": int(coord_h),
    }


class CameraFaceDetector:
    """单条 /asr_chat 连接懒加载的 MediaPipe FaceLandmarker（每 device 连接一个）。"""

    def __init__(
        self,
        *,
        num_faces: int = 5,
        model_path: Optional[str] = None,
        undistorter: Optional[CameraUndistorter] = None,
        min_face_detection_confidence: float = 0.5,
        min_face_presence_confidence: float = 0.5,
        frame_width: int = FACE_FRAME_WIDTH,
        frame_height: int = FACE_FRAME_HEIGHT,
    ) -> None:
        import mediapipe as mp  # type: ignore
        from mediapipe.tasks import python as mp_py  # type: ignore
        from mediapipe.tasks.python import vision as mp_vision  # type: ignore

        self._mp = mp
        self._mp_vision = mp_vision
        self._undistorter = undistorter
        self.num_faces = max(1, int(num_faces))
        self.frame_width = max(160, min(640, int(frame_width)))
        self.frame_height = max(120, min(480, int(frame_height)))

        path = model_path or resolve_camera_model_path()
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"找不到 face_landmarker.task（{path}）。请下载到 "
                "deskbot-server/models/mediapipe/face_landmarker.task，"
                "或设置 CAMERA_FACE_LANDMARKER_PATH。"
            )

        options = mp_vision.FaceLandmarkerOptions(
            base_options=mp_py.BaseOptions(model_asset_path=path),
            running_mode=mp_vision.RunningMode.IMAGE,
            num_faces=self.num_faces,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
            min_face_detection_confidence=min_face_detection_confidence,
            min_face_presence_confidence=min_face_presence_confidence,
        )
        self._landmarker = mp_vision.FaceLandmarker.create_from_options(options)
        self._closed = False
        self._last_bgr: Optional[np.ndarray] = None

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._landmarker.close()
        except Exception:
            pass
        self._closed = True

    @staticmethod
    def _decode_jpeg_to_rgb(buf: bytes):
        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(buf)) as im:
            im = im.convert("RGB")
            return np.array(im, dtype=np.uint8)

    @staticmethod
    def _resize_rgb(rgb: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
        h, w = rgb.shape[:2]
        if w == target_w and h == target_h:
            return rgb
        import cv2  # type: ignore

        return cv2.resize(
            rgb,
            (int(target_w), int(target_h)),
            interpolation=cv2.INTER_LINEAR,
        )

    def detect_faces(self, image_bytes: bytes) -> list[dict[str, Any]]:
        """检测帧内最多 ``num_faces`` 张人脸，返回未带 ``face_id`` 的原始结果列表。"""
        if self._closed:
            return []
        rgb = self._decode_jpeg_to_rgb(image_bytes)
        if self._undistorter is not None:
            rgb = self._undistorter.apply(rgb)
        rgb = self._resize_rgb(rgb, self.frame_width, self.frame_height)
        import cv2  # type: ignore

        self._last_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        h, w = rgb.shape[:2]
        mp_image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=rgb,
        )
        result = self._landmarker.detect(mp_image)
        face_landmarks = getattr(result, "face_landmarks", None) or []
        transform_mats = getattr(result, "facial_transformation_matrixes", None) or []
        out: list[dict[str, Any]] = []
        for idx, face in enumerate(face_landmarks):
            parsed = _extract_face_landmarks(
                face,
                w=w,
                h=h,
                coord_w=self.frame_width,
                coord_h=self.frame_height,
            )
            if not parsed:
                continue
            if idx < len(transform_mats):
                try:
                    mat = np.asarray(transform_mats[idx], dtype=np.float64).reshape(-1)
                    if mat.size == 16:
                        parsed["facial_transform"] = mat.tolist()
                except (TypeError, ValueError):
                    pass
            out.append(parsed)
        return out

    @property
    def last_bgr(self) -> Optional[np.ndarray]:
        return self._last_bgr

    def detect_5pt(self, image_bytes: bytes) -> Optional[dict]:
        """兼容旧接口：仅返回第一张脸。"""
        faces = self.detect_faces(image_bytes)
        return faces[0] if faces else None
