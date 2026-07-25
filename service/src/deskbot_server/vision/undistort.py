"""广角摄像头可选畸变矫正：在服务端人脸检测前对帧做 undistort。

标定得到 ``camera_matrix`` 与 ``dist_coeffs`` 后写入 ``config.yaml`` 的 ``camera_face.undistort``
或通过 ``CAMERA_CALIB_JSON`` 指向 JSON。矫正仅在推理路径启用；转发给 ``/camera_view``
的仍是设备上传的原始 JPEG。

分辨率与标定不一致时，按 ``calib_width`` × ``calib_height`` 对 fx/fy/cx/cy 做比例缩放
（畸变系数不变），与 OpenCV 常用用法一致。

标定 JSON 示例::

    {
      "image_width": 640,
      "image_height": 480,
      "camera_matrix": [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]],
      "dist_coeffs": [k1, k2, p1, p2, k3]
    }

可选环境变量（覆盖 YAML）：``CAMERA_UNDISTORT=1``、``CAMERA_CALIB_JSON``、
``CAMERA_MIN_FACE_DETECTION_CONFIDENCE``、``CAMERA_MIN_FACE_PRESENCE_CONFIDENCE``。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import cv2  # type: ignore
import numpy as np

from deskbot_server.vision.geometry import DEFAULT_HORIZONTAL_FOV_DEG, estimate_camera_matrix_from_fov

logger = logging.getLogger("deskbot-server")


def _scale_intrinsics(K: np.ndarray, calib_w: int, calib_h: int, w: int, h: int) -> np.ndarray:
    sx = float(w) / float(max(calib_w, 1))
    sy = float(h) / float(max(calib_h, 1))
    Ks = K.astype(np.float64).copy()
    Ks[0, 0] *= sx
    Ks[1, 1] *= sy
    Ks[0, 2] *= sx
    Ks[1, 2] *= sy
    return Ks


class CameraUndistorter:
    """按帧尺寸缓存 remap，避免每帧 ``cv2.undistort`` 重复求解映射。"""

    def __init__(
        self, calib_w: int, calib_h: int, camera_matrix: np.ndarray, dist_coeffs: np.ndarray, alpha: float = 1.0
    ) -> None:
        self.calib_w = calib_w
        self.calib_h = calib_h
        self.camera_matrix = camera_matrix
        self.dist_coeffs = dist_coeffs
        self.alpha = alpha
        self._map1: Optional[np.ndarray] = None
        self._map2: Optional[np.ndarray] = None
        self._cached_wh: Optional[tuple[int, int]] = None

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            return rgb
        h, w = rgb.shape[0], rgb.shape[1]
        K = _scale_intrinsics(self.camera_matrix, self.calib_w, self.calib_h, w, h)
        dist = self.dist_coeffs.astype(np.float64).reshape(-1, 1)

        if self._cached_wh != (w, h):
            new_K, _roi = cv2.getOptimalNewCameraMatrix(K, dist, (w, h), alpha=float(self.alpha))
            self._map1, self._map2 = cv2.initUndistortRectifyMap(K, dist, None, new_K, (w, h), cv2.CV_16SC2)
            self._cached_wh = (w, h)

        # INTER_LINEAR 与常见实时预览一致；人脸特征点对轻度平滑不敏感。
        return cv2.remap(rgb, self._map1, self._map2, cv2.INTER_LINEAR)


def _load_calibration_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("标定 JSON 根必须是对象")
    return data


def _parse_K_dist_from_mapping(obj: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, int, int]:
    cw = int(obj.get("image_width") or obj.get("calib_width") or 0)
    ch = int(obj.get("image_height") or obj.get("calib_height") or 0)
    if cw <= 0 or ch <= 0:
        raise ValueError("须含正整数 image_width/image_height 或 calib_width/calib_height")
    K_raw = obj.get("camera_matrix")
    if K_raw is None:
        raise ValueError("缺少 camera_matrix")
    K = np.array(K_raw, dtype=np.float64)
    if K.shape != (3, 3):
        raise ValueError("camera_matrix 须为 3×3")

    dist_raw = obj.get("dist_coeffs") or obj.get("distortion_coefficients")
    if dist_raw is None:
        raise ValueError("缺少 dist_coeffs")
    dist = np.array(dist_raw, dtype=np.float64).reshape(-1, 1)

    if K[0, 0] <= 1e-6 or K[1, 1] <= 1e-6:
        raise ValueError("camera_matrix 焦距 fx/fy 无效")

    return K, dist, cw, ch


def try_build_undistorter(camera_face_cfg: dict[str, Any]) -> Optional[CameraUndistorter]:
    ud = dict(camera_face_cfg.get("undistort") or {})
    env_on = os.environ.get("CAMERA_UNDISTORT", "").strip().lower() in ("1", "true", "yes", "on")
    if not ud.get("enabled") and not env_on:
        return None

    calib_path = (
        os.environ.get("CAMERA_CALIB_JSON", "").strip()
        or str(ud.get("calibration_json") or ud.get("calibration_file") or "").strip()
    )

    K: Optional[np.ndarray] = None
    dist: Optional[np.ndarray] = None
    cw: int = int(ud.get("calib_width") or ud.get("image_width") or 0)
    ch: int = int(ud.get("calib_height") or ud.get("image_height") or 0)
    alpha = float(ud.get("alpha", 1.0))

    if calib_path:
        try:
            blob = _load_calibration_json(calib_path)
            K, dist, cw, ch = _parse_K_dist_from_mapping(blob)
        except OSError as e:
            logger.error("[camera_face] 读取标定文件失败 path=%s: %s", calib_path, e)
            return None
        except (KeyError, TypeError, ValueError) as e:
            logger.error("[camera_face] 标定 JSON 无效 path=%s: %s", calib_path, e)
            return None
    else:
        cm = ud.get("camera_matrix")
        dc = ud.get("dist_coeffs")
        if cw <= 0 or ch <= 0:
            cw = int(ud.get("calib_width") or ud.get("image_width") or camera_face_cfg.get("frame_width") or 320)
            ch = int(ud.get("calib_height") or ud.get("image_height") or camera_face_cfg.get("frame_height") or 240)
        if not cm or not dc:
            use_fov = ud.get("use_fov_estimate", True)
            hfov = float(camera_face_cfg.get("horizontal_fov_deg") or DEFAULT_HORIZONTAL_FOV_DEG)
            if use_fov and cw > 0 and ch > 0:
                K = np.array(estimate_camera_matrix_from_fov(cw, ch, hfov), dtype=np.float64)
                default_dc = ud.get("default_dist_coeffs") or [-0.28, 0.09, 0.0, 0.0, 0.0]
                try:
                    dist = np.array(default_dc, dtype=np.float64).reshape(-1, 1)
                except (TypeError, ValueError) as e:
                    logger.error("[camera_face] default_dist_coeffs 无效: %s", e)
                    return None
                logger.info(
                    "[camera_face] undistort 由水平 FOV=%.1f° 估计内参（%d×%d），使用默认畸变系数（建议标定替换）",
                    hfov,
                    cw,
                    ch,
                )
            else:
                logger.warning(
                    "[camera_face] 已启用 undistort 但未配置 calibration_json/camera_matrix，"
                    "且未启用 use_fov_estimate，跳过矫正"
                )
                return None
        else:
            try:
                K = np.array(cm, dtype=np.float64)
                dist = np.array(dc, dtype=np.float64).reshape(-1, 1)
            except (TypeError, ValueError) as e:
                logger.error("[camera_face] camera_matrix/dist_coeffs 解析失败: %s", e)
                return None
            if cw <= 0 or ch <= 0:
                logger.error("[camera_face] undistort 须在 YAML 中提供 calib_width/calib_height（标定分辨率）")
                return None
            if K.shape != (3, 3):
                logger.error("[camera_face] camera_matrix 须为 3×3")
                return None

    assert K is not None and dist is not None

    try:
        u = CameraUndistorter(calib_w=cw, calib_h=ch, camera_matrix=K, dist_coeffs=dist, alpha=alpha)
    except Exception as e:
        logger.error("[camera_face] CameraUndistorter 初始化失败: %s", e)
        return None

    logger.info("[camera_face] 广角畸变矫正已启用（标定 %d×%d，alpha=%.3f）", cw, ch, alpha)
    return u
