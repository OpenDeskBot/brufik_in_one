"""人脸关键点几何：5 点/9 点坐标系与朝向估算。"""
from __future__ import annotations

import math
from typing import Optional

from deskbot_server.constants import (
    CAMERA_VIEW_PATH,
    DEVICE_PIPELINE_MAX_EVENTS,
    DEVICE_PIPELINE_PATH,
)

FACE_FRAME_WIDTH = 320
FACE_FRAME_HEIGHT = 240
FACE_KEYPOINT_NAMES = (
    "left_eye",
    "right_eye",
    "nose",
    "mouth_left",
    "mouth_right",
)
# 5 点 frontal_score 路径的正脸判定阈值（仅供老 face_pos / camera_ack 使用，
# 前端调试页面对照该字段；不参与下发给机器人的 face_info）。
FRONTAL_THRESHOLD = 0.4
# 9 点 yaw_deg 路径的正脸判定阈值（°）：|yaw_deg| < 该值视为正脸——
# 这是下发给 ESP32 机器人的 face_info.is_frontal 的派生口径。
FRONTAL_YAW_THRESHOLD_DEG = 15.0
# 虹膜 0→1 横向扫过的近似总角度（单眼），用于 offset = (ratio−0.5)·range/2。
EYE_YAW_RANGE_DEG = 50.0
# 默认水平视场角（°），用于画面角估算与无标定 undistort 内参估计。
DEFAULT_HORIZONTAL_FOV_DEG = 120.0
# 正脸角度默认阈值（°）：max(|yaw|, |pitch|) ≤ 该值视为正脸朝镜头。
FRONTAL_ANGLE_THRESHOLD_DEG = 15.0

# MediaPipe Face Mesh 468 点中用于构造 5 点关键点的索引；
# 命名沿用「图像视角」（image-left / image-right），与 /face_pos 历史协议一致。
# - left_eye_center  ≈ (33  + 133) / 2  （图像左侧那只眼的内外眼角连线中点）
# - right_eye_center ≈ (362 + 263) / 2  （图像右侧那只眼）
# - nose             = 1                 （鼻尖）
# - mouth_left       = 61                （图像左侧嘴角）
# - mouth_right      = 291               （图像右侧嘴角）
MP_FACE_5PT_INDICES: dict = {
    "left_eye": (33, 133),
    "right_eye": (362, 263),
    "nose": (1,),
    "mouth_left": (61,),
    "mouth_right": (291,),
}

# 调试页面在原始图像上叠加的 9 个细化关键点（眼角 + 瞳孔 + 鼻尖 + 嘴角）。
# 命名沿用「图像视角」（即 left_eye_* 是图像左侧那只眼的角点 / 瞳孔，
# right_eye_* 是图像右侧那只眼），与上面 MP_FACE_5PT_INDICES 的 left_eye /
# right_eye 完全一致；
# inner = 靠近鼻子那个角，outer = 远离鼻子那个角；iris = 虹膜中心（≈瞳孔）。
# iris 索引 468 / 473 仅当模型输出 478 点（含 iris）时存在；
# face_landmarker.task v1 默认输出 478 点，缺失时静默跳过。
MP_FACE_DETAIL_INDICES: dict = {
    "left_eye_outer": 33,
    "left_eye_inner": 133,
    "left_eye_iris": 468,
    "right_eye_inner": 362,
    "right_eye_outer": 263,
    "right_eye_iris": 473,
    "nose": 1,
    "mouth_left": 61,
    "mouth_right": 291,
}
# 9 点的稳定输出顺序（前端按这个顺序画，方便复用同色映射）。
MP_FACE_DETAIL_NAMES: tuple = (
    "left_eye_outer",
    "left_eye_inner",
    "left_eye_iris",
    "right_eye_inner",
    "right_eye_outer",
    "right_eye_iris",
    "nose",
    "mouth_left",
    "mouth_right",
)

def compute_frontal_score(points: list) -> float:
    """根据 5 点人脸关键点估算正脸朝向摄像头的程度，返回 [0, 1]。

    输入 ``points`` 形如 ``[{"name": "left_eye", "x": ..., "y": ...}, ...]``，
    5 个点齐全才能打出有意义的分。判定分解为 5 个子维度后加权：

    - 眼睛水平（roll）：双眼连线越水平越好
    - 鼻子横向（yaw）：鼻尖 x 坐标越靠近两眼中点越好
    - 嘴部对齐：嘴中点 x 与双眼中点 x 越接近越好（对抗 yaw）
    - 嘴部水平：嘴部连线是否近似水平
    - 纵向比例：鼻尖到眼中点的距离 / 眼距 在 ~0.55 附近最正
    """
    by_name = {
        p["name"]: p
        for p in (points or [])
        if isinstance(p, dict) and p.get("name") and "x" in p and "y" in p
    }
    le = by_name.get("left_eye")
    re_ = by_name.get("right_eye")
    ns = by_name.get("nose")
    ml = by_name.get("mouth_left")
    mr = by_name.get("mouth_right")
    if not (le and re_ and ns and ml and mr):
        return 0.0

    try:
        lex, ley = float(le["x"]), float(le["y"])
        rex, rey = float(re_["x"]), float(re_["y"])
        nsx, nsy = float(ns["x"]), float(ns["y"])
        mlx, mly = float(ml["x"]), float(ml["y"])
        mrx, mry = float(mr["x"]), float(mr["y"])
    except (TypeError, ValueError):
        return 0.0

    eye_dx = rex - lex
    eye_dy = rey - ley
    eye_dist = math.hypot(eye_dx, eye_dy)
    if eye_dist < 1e-3:
        return 0.0

    # 1) roll：眼连线越水平越好
    roll_ratio = abs(eye_dy) / eye_dist
    s_roll = max(0.0, 1.0 - roll_ratio * 4.0)

    # 2) yaw：鼻尖相对双眼中点的横向偏移
    eye_cx = (lex + rex) / 2.0
    yaw_ratio = abs(nsx - eye_cx) / eye_dist
    s_yaw = max(0.0, 1.0 - yaw_ratio * 2.5)

    # 3) 嘴部中点对齐双眼中点（增强 yaw 判定鲁棒性）
    mouth_cx = (mlx + mrx) / 2.0
    mouth_off = abs(mouth_cx - eye_cx) / eye_dist
    s_mouth_align = max(0.0, 1.0 - mouth_off * 2.5)

    # 4) 嘴部水平（roll 的辅助信号）
    mouth_dx = mrx - mlx
    mouth_dy = mry - mly
    mouth_len = math.hypot(mouth_dx, mouth_dy)
    if mouth_len < 1e-3:
        s_mouth_level = 0.0
    else:
        s_mouth_level = max(0.0, 1.0 - abs(mouth_dy) / mouth_len * 3.0)

    # 5) 纵向比例：鼻尖到眼中点的距离与眼距比例，理想 ~0.55
    eye_cy = (ley + rey) / 2.0
    nose_to_eye = nsy - eye_cy
    if nose_to_eye <= 0:
        s_ratio = 0.0
    else:
        ratio = nose_to_eye / eye_dist
        s_ratio = max(0.0, 1.0 - abs(ratio - 0.55) * 2.5)

    score = (
        0.25 * s_roll
        + 0.30 * s_yaw
        + 0.15 * s_mouth_align
        + 0.10 * s_mouth_level
        + 0.20 * s_ratio
    )
    return round(max(0.0, min(1.0, score)), 3)


def compute_face_yaw_deg(landmarks: list) -> Optional[float]:
    """根据 9 点 landmarks 估算用户面向相机的 **yaw 角**（左右转头），单位**度**。

    符号约定（**前提：摄像头未做水平镜像**——大多数 ESP32 / 后置摄像头是
    "传感器原始方向" 直接推流，符合该假设；前置/自拍摄像头通常预先做了
    镜像，此时把返回值取反即可，或将下方 ``YAW_SIGN`` 改成 ``-1``）：

    - **正对镜头 → 0°**
    - **用户向自身右侧转头 → 负值**（朝 −90° 趋近）
    - **用户向自身左侧转头 → 正值**（朝 +90° 趋近）

    缺关键点（外眼角任一 + 鼻尖任一缺失）或眼距过小时返回 ``None``，
    调用方据此判断"无数据"，不要把它当 0° 显示成"正脸"。

    判定原理（弱透视 + 单眼几何，纯 2D；对脸大小无关——全部用比例归一化）：

    1. 取两外眼角 ``L = (lx, ly)`` (MediaPipe idx 33) 与
       ``R = (rx, ry)`` (MediaPipe idx 263)，眼连线方向向量
       ``e = R − L``，眼距 ``d = ‖e‖``，中点 ``M = (L + R) / 2``。
    2. 把鼻尖 ``N`` 相对 ``M`` 的偏移投影到 ``e`` 方向，再除以 ``d/2``
       归一化到 ``[−1, +1]``：

           ratio = 2 · ((N − M) · e) / d²

       含义：``ratio = 0`` 鼻尖在两外眼角中点；``ratio = +1`` 鼻尖
       压在 R（图像 x 较大那侧的外眼角）；``ratio = −1`` 鼻尖压在 L。
       投影到 ``e`` 方向（而不是简单的 ``Δx``）让算法对 roll（歪头）
       自动鲁棒——即便头向一侧倾，沿眼连线的水平分量仍被正确归一化。
    3. 弱透视下鼻尖横向偏移 ≈ ``depth_nose · sin(yaw)``；取
       ``yaw = arcsin(clamp(ratio, −1, 1))``，把单调的鼻尖偏移映射成
       角度，**端点恰好 ±90°**，中段近似线性。
    4. 符号一致性证明：MediaPipe ``left_eye_outer`` (33) 在图像 x 较小
       那一侧（图像观察者视角的"左"）。摄像头不镜像下，那一侧对应的是
       **用户的右脸侧**。当用户向自身右侧转头，鼻尖会朝用户右侧偏移
       → 在图像里就是朝 x 较小那侧偏 → ``(N − M) · e < 0`` →
       ``ratio < 0`` → ``yaw < 0``。与"右负左正"完全吻合。

    精度说明：仅基于 2D 投影 + arcsin，并未做完整 PnP（需要 3D 模型 +
    相机内参）。在 ``|yaw| ≲ 60°`` 内数值大体可信；接近 90° 时
    MediaPipe 自身在外眼角检测上已不稳，返回值仅作"方向 + 幅度"参考，
    不宜直接当硬阈值。
    """
    by = {
        p["name"]: p
        for p in (landmarks or [])
        if isinstance(p, dict) and p.get("name") and "x" in p and "y" in p
    }
    L_out = by.get("left_eye_outer")
    R_out = by.get("right_eye_outer")
    NS = by.get("nose")
    if not (L_out and R_out and NS):
        return None

    try:
        lx, ly = float(L_out["x"]), float(L_out["y"])
        rx, ry = float(R_out["x"]), float(R_out["y"])
        nx, ny = float(NS["x"]), float(NS["y"])
    except (TypeError, ValueError):
        return None

    ex = rx - lx
    ey = ry - ly
    eye_dist_sq = ex * ex + ey * ey
    if eye_dist_sq < 1.0:  # < 1 像素² ⇒ 检测异常
        return None

    mx = (lx + rx) * 0.5
    my = (ly + ry) * 0.5
    # ratio = (N - M) 在 e 方向上的投影 / (eye_dist / 2)
    ratio = 2.0 * ((nx - mx) * ex + (ny - my) * ey) / eye_dist_sq
    ratio = max(-1.0, min(1.0, ratio))
    yaw_deg = math.degrees(math.asin(ratio))
    # 摄像头镜像与否调这个符号（参见函数顶部 docstring）
    YAW_SIGN = 1
    return round(YAW_SIGN * yaw_deg, 1)


def compute_face_pitch_deg(landmarks: list) -> Optional[float]:
    """根据 9 点 landmarks 估算用户面向相机的 **pitch 角**（上下转头/俯仰），
    单位**度**。

    符号约定：

    - **正对镜头 → 0°**
    - **用户抬头（仰视，下巴抬起）→ 正值**（朝 +90° 趋近）
    - **用户低头（俯视，下巴收起）→ 负值**（朝 −90° 趋近）

    缺关键点（外眼角任一 / 鼻尖 / 任一嘴角缺失）或脸纵轴过短时返回 ``None``。

    判定原理（脸自身坐标系 + 弱透视，**对 roll 自动鲁棒**）：

    1. 取两外眼角中点 ``M_eye = (L_outer + R_outer) / 2`` 与两嘴角中点
       ``M_mouth = (mouth_left + mouth_right) / 2``，构造脸的"纵轴向量"
       ``v = M_mouth − M_eye``。``v`` 会随 roll（歪头）一起旋转——
       因此后续基于它的所有度量都对 roll 不敏感（与 :func:`compute_face_yaw_deg`
       用眼连线 ``e`` 投影是同一思路，只是这里把方向换成了"脸纵轴"）。

    2. 把鼻尖 ``N`` 沿 ``v`` 方向投影、并除以 ``|v|²`` 归一化为
       ``t = (N − M_eye) · v / |v|² ∈ ≈[0, 1]``。``t`` 表示鼻尖在
       "眼—嘴" 这条纵段中的相对位置：正脸时鼻尖处于纵段的上半，
       经验值 ``REF ≈ 0.45``。

    3. 用 ``t`` 偏离 ``REF`` 的程度做 **不对称** 归一化（向上空间
       ``REF``、向下空间 ``1 − REF``）：

           delta = REF − t
           norm  = delta / REF        # 抬头 (delta > 0) ⇒ norm ∈ [0, +1]
           norm  = delta / (1 − REF)  # 低头 (delta < 0) ⇒ norm ∈ [-1, 0]

       端点：``t = 0`` （鼻尖压在 M_eye）⇒ norm = +1（抬头到顶）；
       ``t = 1``（鼻尖压在 M_mouth）⇒ norm = −1（低头到底）。

    4. 弱透视下鼻尖的纵向偏移 ≈ ``sin(pitch)``，用 ``arcsin`` 把 ``norm``
       映射回度，端点正好 ±90°，中段近似线性：

           pitch_deg = arcsin(clamp(norm, −1, 1))

    精度说明：``REF = 0.45`` 是统计经验值，因人而异（实际 0.40–0.50），
    所以正脸附近 ±5° 范围内的零点可能略有偏移。短时变化的方向与
    幅度仍然准确，足够驱动机器人云台跟随。如需更准，可在 ESP32 端做
    一次"正对镜头几秒、记录 t 均值当作 REF"的标定。
    """
    by = {
        p["name"]: p
        for p in (landmarks or [])
        if isinstance(p, dict) and p.get("name") and "x" in p and "y" in p
    }
    L_out = by.get("left_eye_outer")
    R_out = by.get("right_eye_outer")
    NS = by.get("nose")
    M_l = by.get("mouth_left")
    M_r = by.get("mouth_right")
    if not (L_out and R_out and NS and M_l and M_r):
        return None

    try:
        lx, ly = float(L_out["x"]), float(L_out["y"])
        rx, ry = float(R_out["x"]), float(R_out["y"])
        nx, ny = float(NS["x"]), float(NS["y"])
        mlx, mly = float(M_l["x"]), float(M_l["y"])
        mrx, mry = float(M_r["x"]), float(M_r["y"])
    except (TypeError, ValueError):
        return None

    me_x = (lx + rx) * 0.5
    me_y = (ly + ry) * 0.5
    mm_x = (mlx + mrx) * 0.5
    mm_y = (mly + mry) * 0.5
    vx = mm_x - me_x
    vy = mm_y - me_y
    v_sq = vx * vx + vy * vy
    if v_sq < 1.0:  # 脸纵轴 < 1 像素²，检测异常
        return None

    t = ((nx - me_x) * vx + (ny - me_y) * vy) / v_sq
    REF = 0.45
    delta = REF - t
    if delta >= 0:
        norm = delta / REF
    else:
        norm = delta / (1.0 - REF)
    norm = max(-1.0, min(1.0, norm))
    pitch_deg = math.degrees(math.asin(norm))
    # 摄像头若做了**垂直**镜像（极少见），把这里改成 -1
    PITCH_SIGN = 1
    return round(PITCH_SIGN * pitch_deg, 1)


def compute_eye_iris_offsets(landmarks: list) -> dict:
    """计算两只眼睛的虹膜（瞳孔）在内外眼角之间的**水平归一化位置**。

    对于每只眼，取该眼的两个眼角（``*_eye_outer`` / ``*_eye_inner``）和虹膜
    （``*_eye_iris``）三个 landmark：

    - **起点 A** = 两眼角中**图像 x 较小**那个（"左眼角"）
    - **终点 B** = 两眼角中**图像 x 较大**那个（"右眼角"）
    - 虹膜在 ``A → B`` 方向上的投影长度 / ``|AB|`` 即 ratio：

        ratio = (P − A) · (B − A) / |B − A|²,  clamp 到 [0, 1]

      含义（与用户给出的口径一致）：

      - ``ratio = 0``：虹膜贴在 **左眼角**（图像 x 较小那个眼角）
      - ``ratio = 0.5``：虹膜正好在内外眼角连线**中点**
      - ``ratio = 1``：虹膜贴在 **右眼角**（图像 x 较大那个眼角）

    用沿眼角连线投影（不是简单的 ``Δx``）的好处：歪头时眼连线不再水平，
    投影法仍能正确反映"沿这只眼的横向"位置——和 yaw 函数的做法是一脉相承的
    "脸自身坐标系"思路。

    返回值固定形如 ``{"left_eye": float|None, "right_eye": float|None}``。
    某只眼的三点（外角 / 内角 / iris）任一缺失，或两眼角距 < 1 像素时，
    对应 key 为 ``None``，**不**让整个 dict 变 None——调用方按 key 各自处理。

    iris 索引（468 / 473）仅在带 iris 的 face_landmarker 模型上才会输出；
    没有 iris 的旧模型下两只眼都会是 ``None``。
    """
    by = {
        p["name"]: p
        for p in (landmarks or [])
        if isinstance(p, dict) and p.get("name") and "x" in p and "y" in p
    }
    out: dict = {"left_eye": None, "right_eye": None}
    for eye_key, outer_name, inner_name, iris_name in (
        ("left_eye",  "left_eye_outer",  "left_eye_inner",  "left_eye_iris"),
        ("right_eye", "right_eye_outer", "right_eye_inner", "right_eye_iris"),
    ):
        O = by.get(outer_name)
        I = by.get(inner_name)
        P = by.get(iris_name)
        if not (O and I and P):
            continue
        try:
            ox, oy = float(O["x"]), float(O["y"])
            ix, iy = float(I["x"]), float(I["y"])
            px, py = float(P["x"]), float(P["y"])
        except (TypeError, ValueError):
            continue
        # 起点 A = 图像 x 较小那个眼角；终点 B = x 较大的。
        # （"左眼角 / 右眼角" 即图像观察者视角下的左右。）
        if ox <= ix:
            ax, ay, bx, by_ = ox, oy, ix, iy
        else:
            ax, ay, bx, by_ = ix, iy, ox, oy
        dx = bx - ax
        dy = by_ - ay
        d_sq = dx * dx + dy * dy
        if d_sq < 1.0:
            continue
        ratio = ((px - ax) * dx + (py - ay) * dy) / d_sq
        # 物理上瞳孔不会在眼角之外，越界（极少数估计偏差）视为贴边
        ratio = max(0.0, min(1.0, ratio))
        out[eye_key] = round(ratio, 3)
    return out


def compute_face_score(
    points: list,
    landmarks: list,
    *,
    image_w: int = FACE_FRAME_WIDTH,
    image_h: int = FACE_FRAME_HEIGHT,
) -> float:
    """人脸检测质量分 [0, 1]：细化 landmark 完整度 + 脸在画面中的尺寸。

    MediaPipe FaceLandmarker 不对外暴露逐脸 detection confidence；此处用
    可观测质量作代理：关键点越全、眼距越大（脸越近/越大），分数越高。
    """
    by_detail = {
        p["name"]: p
        for p in (landmarks or [])
        if isinstance(p, dict) and p.get("name") and "x" in p and "y" in p
    }
    n_detail = len(MP_FACE_DETAIL_NAMES)
    completeness = (
        sum(1 for name in MP_FACE_DETAIL_NAMES if name in by_detail) / n_detail
        if n_detail
        else 0.0
    )

    by5 = {
        p["name"]: p
        for p in (points or [])
        if isinstance(p, dict) and p.get("name") and "x" in p and "y" in p
    }
    le = by5.get("left_eye")
    re_ = by5.get("right_eye")
    size_score = 0.0
    if le and re_:
        try:
            lex, ley = float(le["x"]), float(le["y"])
            rex, rey = float(re_["x"]), float(re_["y"])
            eye_dist = math.hypot(rex - lex, rey - ley)
            ref = max(float(image_w), 1.0) * 0.12
            size_score = min(1.0, eye_dist / ref)
        except (TypeError, ValueError):
            size_score = 0.0

    score = 0.55 * completeness + 0.45 * size_score
    return round(max(0.0, min(1.0, score)), 3)


def compute_frontal_angle_deg(
    yaw_deg: Optional[float],
    pitch_deg: Optional[float],
) -> Optional[float]:
    """正脸角度（°）：头部相对镜头轴线的偏差，取 ``max(|yaw|, |pitch|)``。

    正对镜头 → 0°；转头/抬头幅度越大值越大。与 ``frontal_score``（几何分）不同，
    这是纯姿态角口径，用于「跟随正脸」与表格展示。
    """
    if yaw_deg is None and pitch_deg is None:
        return None
    y = abs(float(yaw_deg or 0.0))
    p = abs(float(pitch_deg or 0.0))
    return round(max(y, p), 1)


def compute_is_frontal_by_angle(
    yaw_deg: Optional[float],
    pitch_deg: Optional[float],
    *,
    threshold_deg: float = FRONTAL_ANGLE_THRESHOLD_DEG,
) -> Optional[bool]:
    """按正脸角度阈值判定是否正对镜头。"""
    angle = compute_frontal_angle_deg(yaw_deg, pitch_deg)
    if angle is None:
        return None
    return angle <= float(threshold_deg)


def decompose_facial_transform_matrix(matrix: list | tuple) -> Optional[dict[str, float]]:
    """从 MediaPipe ``facial_transformation_matrixes``（4×4 行主序）分解头部位姿角。

    矩阵将 canonical face model 映射到检测脸在相机坐标系中的位姿；取旋转子矩阵
    做 XYZ 欧拉分解。符号与 :func:`compute_face_yaw_deg` / pitch 尽量对齐：
    yaw 右负左正，pitch 抬头正低头负。
    """
    try:
        m = [float(x) for x in matrix]
    except (TypeError, ValueError):
        return None
    if len(m) != 16:
        return None

    r00, r01, r02 = m[0], m[1], m[2]
    r10, r11, r12 = m[4], m[5], m[6]
    r20, r21, r22 = m[8], m[9], m[10]

    sy = math.hypot(r00, r10)
    if sy >= 1e-6:
        pitch = math.atan2(r21, r22)
        yaw = math.atan2(-r20, sy)
        roll = math.atan2(r10, r00)
    else:
        pitch = math.atan2(-r12, r11)
        yaw = math.atan2(-r20, sy)
        roll = 0.0

    # MediaPipe 矩阵系与 2D 启发式符号可能差一个负号；实测对齐后取反 yaw/pitch。
    MP_YAW_SIGN = -1
    MP_PITCH_SIGN = -1
    MP_ROLL_SIGN = 1
    return {
        "yaw_deg": round(math.degrees(yaw) * MP_YAW_SIGN, 1),
        "pitch_deg": round(math.degrees(pitch) * MP_PITCH_SIGN, 1),
        "roll_deg": round(math.degrees(roll) * MP_ROLL_SIGN, 1),
    }


def compute_eye_yaw_offset_deg(
    iris_offsets: dict,
    *,
    eye_yaw_range_deg: float = EYE_YAW_RANGE_DEG,
) -> Optional[float]:
    """瞳孔相对人脸的左右偏角（°）：虹膜在眼角连线 0.5 为 0，向图像 x 大侧为正。"""
    vals: list[float] = []
    for key in ("left_eye", "right_eye"):
        v = (iris_offsets or {}).get(key)
        if v is None:
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    half_range = max(1.0, float(eye_yaw_range_deg)) * 0.5
    norm = max(-1.0, min(1.0, (avg - 0.5) * 2.0))
    return round(norm * half_range, 1)


def compute_gaze_angles(
    face_yaw_deg: Optional[float],
    face_pitch_deg: Optional[float],
    iris_offsets: dict,
    *,
    eye_yaw_range_deg: float = EYE_YAW_RANGE_DEG,
) -> dict[str, Optional[float]]:
    """合成注视角：头相对镜头 yaw/pitch + 眼相对头的偏角 → 视线相对镜头。

    当前模型仅有虹膜横向 offset，pitch 方向暂只用 head pitch。
    """
    eye_yaw = compute_eye_yaw_offset_deg(
        iris_offsets, eye_yaw_range_deg=eye_yaw_range_deg
    )
    gaze_yaw: Optional[float] = None
    if face_yaw_deg is not None:
        gaze_yaw = round(float(face_yaw_deg) + (eye_yaw or 0.0), 1)
    elif eye_yaw is not None:
        gaze_yaw = eye_yaw
    gaze_pitch = face_pitch_deg
    return {
        "eye_yaw_offset_deg": eye_yaw,
        "gaze_yaw_deg": gaze_yaw,
        "gaze_pitch_deg": gaze_pitch,
    }


def compute_is_looking_at_camera(
    gaze_yaw_deg: Optional[float],
    gaze_pitch_deg: Optional[float],
    *,
    yaw_threshold_deg: float = FRONTAL_YAW_THRESHOLD_DEG,
    pitch_threshold_deg: float = FRONTAL_YAW_THRESHOLD_DEG,
) -> Optional[bool]:
    """视线是否朝向镜头：合成 yaw/pitch 均在阈值内为 True。"""
    if gaze_yaw_deg is None and gaze_pitch_deg is None:
        return None
    if gaze_yaw_deg is not None and abs(gaze_yaw_deg) >= yaw_threshold_deg:
        return False
    if gaze_pitch_deg is not None and abs(gaze_pitch_deg) >= pitch_threshold_deg:
        return False
    return True


def estimate_camera_matrix_from_fov(
    width: int,
    height: int,
    horizontal_fov_deg: float,
) -> list[list[float]]:
    """由水平 FOV 与分辨率估计 pinhole 内参 3×3（fx=fy，主点在中心）。"""
    w = max(int(width), 1)
    h = max(int(height), 1)
    hfov = max(10.0, min(170.0, float(horizontal_fov_deg)))
    hfov_rad = math.radians(hfov)
    fx = (w / 2.0) / math.tan(hfov_rad / 2.0)
    fy = fx
    cx = w / 2.0
    cy = h / 2.0
    return [
        [fx, 0.0, cx],
        [0.0, fy, cy],
        [0.0, 0.0, 1.0],
    ]
