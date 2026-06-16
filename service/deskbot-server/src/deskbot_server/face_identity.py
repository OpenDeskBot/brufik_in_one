"""人脸相似性特征与跨帧/跨次 re-id（InsightFace embedding 优先，几何特征回退）。"""
from __future__ import annotations

import base64
import io
import math
from typing import Any, Optional

from deskbot_server.camera_face_tune import get_face_embedding_enabled
from deskbot_server.vision.face_embedding import (
    is_embedding_vector,
    is_legacy_geometric_vector,
)
from deskbot_server.vision.geometry import compute_eye_iris_offsets


def compute_face_descriptor(
    points: list,
    landmarks: list | None = None,
) -> Optional[list[float]]:
    """提取尺度不变的人脸几何特征向量（L2 归一化，适合余弦相似度）。

    特征仅依赖五官相对比例，与脸在画面中的位置无关；对小幅 yaw/pitch 有一定鲁棒性，
    侧脸过大或遮挡时相似度会下降。
    """
    by = {
        p["name"]: p
        for p in (points or [])
        if isinstance(p, dict) and p.get("name") and "x" in p and "y" in p
    }
    le = by.get("left_eye")
    re_ = by.get("right_eye")
    ns = by.get("nose")
    ml = by.get("mouth_left")
    mr = by.get("mouth_right")
    if not (le and re_ and ns and ml and mr):
        return None
    try:
        lex, ley = float(le["x"]), float(le["y"])
        rex, rey = float(re_["x"]), float(re_["y"])
        nsx, nsy = float(ns["x"]), float(ns["y"])
        mlx, mly = float(ml["x"]), float(ml["y"])
        mrx, mry = float(mr["x"]), float(mr["y"])
    except (TypeError, ValueError):
        return None

    eye_dist = math.hypot(rex - lex, rey - ley)
    if eye_dist < 1e-3:
        return None

    eye_cx = (lex + rex) * 0.5
    eye_cy = (ley + rey) * 0.5
    mouth_cx = (mlx + mrx) * 0.5
    mouth_cy = (mly + mry) * 0.5
    mouth_w = math.hypot(mrx - mlx, mry - mly)

    feats: list[float] = [
        (nsx - eye_cx) / eye_dist,
        (nsy - eye_cy) / eye_dist,
        mouth_w / eye_dist,
        (mouth_cx - eye_cx) / eye_dist,
        (mouth_cy - eye_cy) / eye_dist,
        (nsy - eye_cy) / eye_dist,
        abs(rey - ley) / eye_dist,
    ]

    iris = compute_eye_iris_offsets(landmarks or [])
    for key in ("left_eye", "right_eye"):
        v = iris.get(key)
        feats.append((float(v) - 0.5) if v is not None else 0.0)

    norm = math.sqrt(sum(x * x for x in feats))
    if norm < 1e-6:
        return None
    return [round(x / norm, 6) for x in feats]


def descriptor_cosine_similarity(a: list[float], b: list[float]) -> float:
    """两特征向量余弦相似度 [-1, 1]；输入须等长。"""
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    return max(-1.0, min(1.0, dot))


def ema_update_descriptor(
    prev: list[float] | None,
    sample: list[float],
    *,
    alpha: float = 0.2,
) -> list[float]:
    """对 profile / track 特征做指数滑动平均并重新归一化。"""
    if prev is None or len(prev) != len(sample):
        return list(sample)
    a = max(0.05, min(0.5, float(alpha)))
    merged = [(1.0 - a) * p + a * s for p, s in zip(prev, sample)]
    norm = math.sqrt(sum(x * x for x in merged))
    if norm < 1e-6:
        return list(sample)
    return [round(x / norm, 6) for x in merged]


def descriptor_dim_label(desc: list[float] | None) -> str:
    if not isinstance(desc, list):
        return "—"
    n = len(desc)
    if is_embedding_vector(desc):
        return f"embedding/{n}"
    if is_legacy_geometric_vector(desc):
        return f"geometry/{n}"
    return f"vec/{n}"


def match_threshold_for_descriptor(
    desc: list[float],
    *,
    embedding_threshold: float,
    geometry_threshold: float,
) -> float:
    """按向量类型选用阈值：embedding 约 0.40，几何约 0.88。"""
    if is_embedding_vector(desc):
        return max(0.25, min(0.99, float(embedding_threshold)))
    return max(0.75, min(0.99, float(geometry_threshold)))


def attach_descriptor(
    face: dict[str, Any],
    *,
    bgr_image: Any = None,
) -> Optional[list[float]]:
    """为单张检出脸附加 ``face_descriptor``（embedding 或 9 维几何）。"""
    existing = face.get("face_descriptor")
    if isinstance(existing, list) and existing and bgr_image is None:
        return existing

    points = face.get("points") or []
    landmarks = face.get("landmarks") or []
    desc: Optional[list[float]] = None
    kind = "geometry"

    if get_face_embedding_enabled() and bgr_image is not None:
        from deskbot_server.vision.face_embedding import compute_face_embedding

        desc = compute_face_embedding(bgr_image, points, landmarks=landmarks)
        if desc is not None:
            kind = "embedding"

    if desc is None:
        desc = compute_face_descriptor(points, landmarks)
        kind = "geometry"

    if desc is not None:
        face["face_descriptor"] = desc
        face["descriptor_kind"] = kind
    return desc


def attach_descriptors_to_faces(
    faces: list[dict[str, Any]],
    *,
    bgr_image: Any = None,
) -> None:
    for face in faces or []:
        if isinstance(face, dict):
            attach_descriptor(face, bgr_image=bgr_image)


def descriptor_from_jpeg_bytes(
    jpeg_bytes: bytes,
    points: list,
    *,
    landmarks: list | None = None,
) -> Optional[list[float]]:
    if not get_face_embedding_enabled() or not jpeg_bytes:
        return None
    try:
        import numpy as np
        from PIL import Image  # type: ignore

        from deskbot_server.vision.face_embedding import compute_face_embedding, rgb_to_bgr

        with Image.open(io.BytesIO(jpeg_bytes)) as im:
            rgb = np.array(im.convert("RGB"), dtype=np.uint8)
        return compute_face_embedding(rgb_to_bgr(rgb), points, landmarks=landmarks)
    except Exception:
        return None


def descriptor_from_jpeg_base64(
    b64: str,
    points: list,
    *,
    landmarks: list | None = None,
) -> Optional[list[float]]:
    raw = (b64 or "").strip()
    if not raw:
        return None
    if "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        return descriptor_from_jpeg_bytes(base64.b64decode(raw), points, landmarks=landmarks)
    except Exception:
        return None


def _points_bbox(face: dict[str, Any]) -> Optional[tuple[float, float, float, float]]:
    """返回 (min_x, min_y, max_x, max_y)。"""
    xs: list[float] = []
    ys: list[float] = []
    for p in (face.get("landmarks") or face.get("points") or []):
        if not isinstance(p, dict) or "x" not in p or "y" not in p:
            continue
        try:
            xs.append(float(p["x"]))
            ys.append(float(p["y"]))
        except (TypeError, ValueError):
            continue
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_iou(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def _eye_distance(face: dict[str, Any]) -> Optional[float]:
    by = {
        p["name"]: p
        for p in (face.get("points") or [])
        if isinstance(p, dict) and p.get("name") and "x" in p and "y" in p
    }
    le = by.get("left_eye")
    re_ = by.get("right_eye")
    if not (le and re_):
        return None
    try:
        return math.hypot(float(re_["x"]) - float(le["x"]), float(re_["y"]) - float(le["y"]))
    except (TypeError, ValueError):
        return None


def _nose_xy(face: dict[str, Any]) -> Optional[tuple[float, float]]:
    for p in face.get("points") or []:
        if isinstance(p, dict) and p.get("name") == "nose":
            try:
                return float(p["x"]), float(p["y"])
            except (TypeError, ValueError, KeyError):
                return None
    for p in face.get("landmarks") or []:
        if isinstance(p, dict) and p.get("name") == "nose":
            try:
                return float(p["x"]), float(p["y"])
            except (TypeError, ValueError, KeyError):
                return None
    return None


def face_quality_score(face: dict[str, Any]) -> float:
    from deskbot_server.vision.geometry import compute_face_score

    w = int(face.get("image_w") or 320)
    h = int(face.get("image_h") or 240)
    return compute_face_score(face.get("points") or [], face.get("landmarks") or [], image_w=w, image_h=h)


def deduplicate_overlapping_faces(
    faces: list[dict[str, Any]],
    *,
    iou_threshold: float = 0.35,
    descriptor_sim_threshold: float = 0.92,
    embedding_sim_threshold: float = 0.55,
    nose_dist_ratio: float = 0.4,
) -> list[dict[str, Any]]:
    """去掉同一帧内重叠/重复的检出（MediaPipe 偶发一张脸多个框）。

    按 ``face_quality_score`` 从高到低贪心保留；若与已保留框 IoU 过高、
    特征余弦相似度过高、或鼻尖距离 < 眼距×ratio，则视为同一张脸并丢弃。
    """
    if len(faces) <= 1:
        return list(faces or [])

    candidates: list[tuple[float, dict[str, Any], tuple[float, float, float, float], list[float]]] = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        bbox = _points_bbox(face)
        if bbox is None:
            continue
        desc = attach_descriptor(face) or []
        q = face_quality_score(face)
        candidates.append((q, face, bbox, desc))

    if not candidates:
        return []

    candidates.sort(key=lambda x: -x[0])

    kept: list[dict[str, Any]] = []
    kept_meta: list[tuple[tuple[float, float, float, float], list[float], Optional[tuple[float, float]], Optional[float]]] = []

    for q, face, bbox, desc in candidates:
        is_dup = False
        nose = _nose_xy(face)
        eye_d = _eye_distance(face)
        for kbbox, kdesc, knose, keye in kept_meta:
            if _bbox_iou(bbox, kbbox) >= iou_threshold:
                is_dup = True
                break
            if desc and kdesc:
                sim_thr = (
                    embedding_sim_threshold
                    if is_embedding_vector(desc)
                    else descriptor_sim_threshold
                )
                if descriptor_cosine_similarity(desc, kdesc) >= sim_thr:
                    is_dup = True
                    break
            if nose and knose and eye_d and keye:
                ref = min(eye_d, keye) * nose_dist_ratio
                if math.hypot(nose[0] - knose[0], nose[1] - knose[1]) < ref:
                    is_dup = True
                    break
        if is_dup:
            continue
        kept.append(face)
        kept_meta.append((bbox, desc, nose, eye_d))

    return kept
