"""InsightFace ArcFace 人脸 embedding（512 维，与 MediaPipe 检测配合）。"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("deskbot-server")

FACE_EMBEDDING_DIM = 512
_LEGACY_GEOMETRIC_DIM = 9

_engine_lock = threading.Lock()
_engine: Optional["FaceEmbeddingEngine"] = None
_engine_init_error: Optional[str] = None


def is_embedding_vector(desc: list[float] | None) -> bool:
    return isinstance(desc, list) and len(desc) >= 64


def is_legacy_geometric_vector(desc: list[float] | None) -> bool:
    return isinstance(desc, list) and 4 <= len(desc) < 64


def _resolve_recognition_onnx(pack: str) -> str:
    """定位 pack 内 ArcFace recognition ONNX（不经过 FaceAnalysis，避免强依赖 detection）。"""
    import glob
    import os.path as osp

    from insightface.utils import ensure_available  # type: ignore

    override = (os.environ.get("FACE_EMBEDDING_RECOGNITION_ONNX") or "").strip()
    if override and osp.isfile(override):
        return override

    model_dir = ensure_available("models", pack)
    preferred = osp.join(model_dir, "w600k_mbf.onnx")
    if osp.isfile(preferred):
        return preferred

    for path in sorted(glob.glob(osp.join(model_dir, "*.onnx"))):
        name = osp.basename(path).lower()
        if "w600k" in name or "arcface" in name or "glintr" in name:
            return path
    raise FileNotFoundError(f"未找到 InsightFace recognition 模型 pack={pack} dir={model_dir}")


class FaceEmbeddingEngine:
    """懒加载 InsightFace 识别模型（``buffalo_s`` 包内 ``w600k_mbf``）。"""

    def __init__(self, *, model_pack: str = "buffalo_s") -> None:
        import cv2  # noqa: F401
        from insightface.model_zoo import get_model  # type: ignore

        pack = (os.environ.get("FACE_EMBEDDING_MODEL") or model_pack).strip() or "buffalo_s"
        providers = ["CPUExecutionProvider"]
        cuda = (os.environ.get("FACE_EMBEDDING_USE_CUDA") or "").strip().lower()
        if cuda in ("1", "true", "yes"):
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        model_path = _resolve_recognition_onnx(pack)
        rec = get_model(model_path, providers=providers)
        if rec is None or getattr(rec, "taskname", None) != "recognition":
            raise RuntimeError(f"InsightFace 模型不是 recognition: {model_path}")
        rec.prepare(ctx_id=-1)
        self._rec = rec
        logger.info(
            "[face_embedding] InsightFace 识别模型已加载 pack=%s path=%s dim=%d",
            pack,
            model_path,
            FACE_EMBEDDING_DIM,
        )

    def compute(
        self,
        bgr: np.ndarray,
        points: list,
        *,
        landmarks: list | None = None,
    ) -> Optional[list[float]]:
        from insightface.utils import face_align  # type: ignore

        kps = _points_to_kps5(points, landmarks)
        if kps is None:
            return None
        if bgr is None or bgr.size == 0:
            return None
        try:
            aimg = face_align.norm_crop(bgr, landmark=kps, image_size=112)
            feat = self._rec.get_feat(aimg)
            vec = np.asarray(feat, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vec))
            if norm < 1e-6:
                return None
            vec = vec / norm
            return [round(float(x), 6) for x in vec.tolist()]
        except Exception as exc:
            logger.debug("[face_embedding] compute failed: %s", exc)
            return None


def _points_to_kps5(
    points: list,
    landmarks: list | None,
) -> Optional[np.ndarray]:
    """MediaPipe 五点 → InsightFace 对齐用 5×2 关键点。"""
    by: dict[str, tuple[float, float]] = {}
    for p in points or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        try:
            by[str(p["name"])] = (float(p["x"]), float(p["y"]))
        except (TypeError, ValueError, KeyError):
            continue
    order = ("left_eye", "right_eye", "nose", "mouth_left", "mouth_right")
    if all(k in by for k in order):
        return np.array([by[k] for k in order], dtype=np.float32)

    for p in landmarks or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        name = str(p["name"])
        if name in order and name not in by:
            try:
                by[name] = (float(p["x"]), float(p["y"]))
            except (TypeError, ValueError, KeyError):
                continue
    if all(k in by for k in order):
        return np.array([by[k] for k in order], dtype=np.float32)
    return None


def get_face_embedding_engine() -> Optional[FaceEmbeddingEngine]:
    global _engine, _engine_init_error
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        if _engine_init_error is not None:
            return None
        try:
            _engine = FaceEmbeddingEngine()
        except Exception as exc:
            _engine_init_error = str(exc) or f"{type(exc).__name__}"
            logger.warning(
                "[face_embedding] 初始化失败，将回退几何特征: %s",
                _engine_init_error,
            )
            return None
    return _engine


def compute_face_embedding(
    bgr: np.ndarray,
    points: list,
    *,
    landmarks: list | None = None,
) -> Optional[list[float]]:
    eng = get_face_embedding_engine()
    if eng is None:
        return None
    return eng.compute(bgr, points, landmarks=landmarks)


def rgb_to_bgr(rgb: np.ndarray) -> np.ndarray:
    import cv2  # type: ignore

    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
