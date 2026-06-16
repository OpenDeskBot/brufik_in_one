"""摄像头人脸场景描述（位置、坐标），供 LLM system prompt。"""
from __future__ import annotations

import math
from typing import Any, Optional

from deskbot_server.camera_face_tune import get_horizontal_fov_deg
from deskbot_server.debug_prefs_store import get_camera_servo_auto_mode
from deskbot_server.face_snapshot_cache import list_device_faces


def _nose_xy(face: dict[str, Any]) -> tuple[float, float, int, int] | None:
    w = int(face.get("image_w") or 0) or 320
    h = int(face.get("image_h") or 0) or 240
    for src in (face.get("landmarks"), face.get("points")):
        if not isinstance(src, list):
            continue
        for p in src:
            if not isinstance(p, dict) or p.get("name") != "nose":
                continue
            try:
                return float(p["x"]), float(p["y"]), w, h
            except (TypeError, ValueError, KeyError):
                continue
    return None


def describe_face_screen_position(face: dict[str, Any]) -> dict[str, Any]:
    """鼻尖在画面中的方位与归一化坐标。"""
    nose = _nose_xy(face)
    if nose is None:
        return {"label": "位置未知", "nx_pct": None, "ny_pct": None, "screen_yaw": None, "screen_pitch": None}
    nx, ny, w, h = nose
    nx_pct = round(nx / w * 100, 1)
    ny_pct = round(ny / h * 100, 1)
    x_ratio = nx / w
    y_ratio = ny / h

    if x_ratio < 0.35:
        h_label = "左"
    elif x_ratio > 0.65:
        h_label = "右"
    else:
        h_label = "中"

    if y_ratio < 0.35:
        v_label = "上"
    elif y_ratio > 0.65:
        v_label = "下"
    else:
        v_label = "中"

    if h_label == "中" and v_label == "中":
        label = "画面中央"
    elif h_label == "中":
        label = f"画面{v_label}方"
    elif v_label == "中":
        label = f"画面{h_label}侧"
    else:
        label = f"画面{h_label}{v_label}"

    hfov_rad = math.radians(get_horizontal_fov_deg())
    vfov_rad = 2 * math.atan(math.tan(hfov_rad / 2) * (h / w))
    dx = nx - w / 2
    dy = ny - h / 2
    r2d = 180 / math.pi
    screen_yaw = round(math.atan((2 * dx * math.tan(hfov_rad / 2)) / w) * r2d, 1)
    screen_pitch = round(math.atan((2 * dy * math.tan(vfov_rad / 2)) / h) * r2d, 1)

    return {
        "label": label,
        "nx_pct": nx_pct,
        "ny_pct": ny_pct,
        "screen_yaw": screen_yaw,
        "screen_pitch": screen_pitch,
    }


def _follow_mode_label(mode: str) -> str:
    labels = {
        "": "关闭",
        "follow": "跟随人脸",
        "follow_frontal": "跟随正脸",
        "gaze": "注视感知",
    }
    return labels.get(mode, mode or "关闭")


def list_faces_for_prompt(device_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    device_id = str(device_id or "").strip()
    if not device_id:
        return []
    cap = max(1, min(int(limit), 10))
    faces = list_device_faces(device_id)
    rows: list[dict[str, Any]] = []
    for fid in sorted(faces.keys()):
        face = faces[fid]
        if not isinstance(face, dict):
            continue
        pos = describe_face_screen_position(face)
        name = str(face.get("person_name") or "").strip()
        score = face.get("identity_score")
        try:
            conf = round(float(score), 3) if score is not None else None
        except (TypeError, ValueError):
            conf = None
        pid = face.get("person_id")
        rows.append(
            {
                "face_id": int(fid),
                "person_id": int(pid) if pid is not None else None,
                "person_name": name or None,
                "identity_score": conf,
                **pos,
            }
        )
    rows.sort(key=lambda r: (-(r.get("identity_score") or 0.0), r["face_id"]))
    return rows[:cap]


def llm_face_scene_prompt_appendix(device_id: Optional[str] = None) -> str:
    """当前画面人脸（含 face_id、姓名、置信度、画面方位与归一化坐标）。"""
    device_id = str(device_id or "").strip()
    if not device_id:
        return ""
    rows = list_faces_for_prompt(device_id, limit=5)
    follow = _follow_mode_label(get_camera_servo_auto_mode())
    header = f"摄像头人脸跟随模式：{follow}。"
    if not rows:
        return header + "\n当前摄像头画面中未检测到人脸。"
    if follow == "关闭":
        header += (
            " 画面有人时建议优先用 ``set_camera_follow`` 开启 ``follow`` 或 ``gaze``，"
            "让机器人转向注视对方（礼貌）；主人未开口时可 ``need_reply: false`` 仅执行该工具。"
        )
    lines: list[str] = []
    for i, row in enumerate(rows, 1):
        fid = row["face_id"]
        name_part = row["person_name"] or "未注册"
        conf = row.get("identity_score")
        conf_part = f"，匹配置信度 {conf:.3f}" if conf is not None else ""
        pid_part = ""
        if row.get("person_id") is not None:
            pid_part = f"，person_id={row['person_id']}"
        pos = row.get("label") or "位置未知"
        nx = row.get("nx_pct")
        ny = row.get("ny_pct")
        coord = f"坐标约 ({nx}%, {ny}%)" if nx is not None and ny is not None else ""
        yaw = row.get("screen_yaw")
        pitch = row.get("screen_pitch")
        angle = ""
        if yaw is not None and pitch is not None:
            angle = f"，相对画面中心偏航 {yaw:+.1f}°/俯仰 {pitch:+.1f}°"
        lines.append(
            f"  {i}. face_id={fid} {name_part}{pid_part}{conf_part}；{pos}{coord}{angle}"
        )
    body = "\n".join(lines)
    return (
        header
        + "\n当前摄像头画面人脸（至多 5 人；注册人脸时用 ``register_face`` 并指定 ``face_id``；"
        "多人时请向用户澄清后再注册）：\n"
        f"{body}"
    )
