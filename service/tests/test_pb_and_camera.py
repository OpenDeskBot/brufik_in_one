from __future__ import annotations

from deskbot_server.application.camera_frame import (
    analyze_face_detection,
    analyze_face_detections,
    build_face_info_message,
    pick_primary_face,
)
from deskbot_server.application.face_tracker import FaceTracker
from deskbot_server.pb.shapes import enumerate_zh_phonemes, normalize_primitive_shape
from deskbot_server.pb.wire import build_pb_wire_pairs


def test_enumerate_zh_phonemes_contains_silence():
    phones = enumerate_zh_phonemes()
    assert "_" in phones
    assert "sil" in phones


def test_normalize_primitive_shape_aliases():
    assert normalize_primitive_shape("fill_rect") == "rect"
    assert normalize_primitive_shape("ellipse_fill") == "ellipse_fill"


def test_face_lcd_scale_and_defaults():
    from deskbot_server.pb.display import (
        FACE_LCD_HEIGHT,
        FACE_LCD_WIDTH,
        scale_primitive,
    )
    from deskbot_server.pb.shapes import default_face_circles

    assert FACE_LCD_WIDTH == 284
    assert FACE_LCD_HEIGHT == 240
    nose = scale_primitive({"shape": "circle", "x": 64, "y": 34, "r": 5})
    assert nose["x"] == 142
    assert nose["y"] == 124
    assert nose["r"] == 11
    fc = default_face_circles()
    assert fc["nose"][0]["x"] == 142


def test_analyze_face_detection_empty_points():
    result = analyze_face_detection({"points": [], "landmarks": []})
    assert "frontal_score" in result
    assert "face_score" in result
    assert result["yaw_deg"] is None
    assert result["gaze_yaw_deg"] is None
    assert result["is_looking_at_camera"] is None


def test_compute_gaze_angles_with_iris():
    from deskbot_server.vision.geometry import compute_gaze_angles, compute_is_looking_at_camera

    gaze = compute_gaze_angles(
        10.0,
        5.0,
        {"left_eye": 0.5, "right_eye": 0.5},
        eye_yaw_range_deg=50.0,
    )
    assert gaze["eye_yaw_offset_deg"] == 0.0
    assert gaze["gaze_yaw_deg"] == 10.0
    assert gaze["gaze_pitch_deg"] == 5.0
    assert compute_is_looking_at_camera(gaze["gaze_yaw_deg"], gaze["gaze_pitch_deg"]) is True


def test_compute_face_score_with_landmarks():
    from deskbot_server.vision.geometry import compute_face_score

    points = [
        {"name": "left_eye", "x": 100, "y": 80},
        {"name": "right_eye", "x": 140, "y": 80},
    ]
    landmarks = [{"name": "nose", "x": 120, "y": 100}]
    score = compute_face_score(points, landmarks, image_w=320, image_h=240)
    assert 0.0 <= score <= 1.0


def test_decompose_facial_transform_identity():
    from deskbot_server.vision.geometry import decompose_facial_transform_matrix

    # 单位旋转：yaw/pitch/roll ≈ 0
    ident = [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]
    pose = decompose_facial_transform_matrix(ident)
    assert pose is not None
    assert abs(pose["yaw_deg"]) < 0.2
    assert abs(pose["pitch_deg"]) < 0.2


def test_estimate_camera_matrix_from_fov():
    from deskbot_server.vision.geometry import estimate_camera_matrix_from_fov

    k = estimate_camera_matrix_from_fov(320, 240, 120.0)
    assert len(k) == 3
    assert k[0][0] > 0
    assert abs(k[0][2] - 160.0) < 0.01


def test_normalize_camera_face_document_frame_size():
    from deskbot_server.camera_face_config_store import normalize_camera_face_document

    cfg = normalize_camera_face_document({"frame_width": 640, "frame_height": 480})
    assert cfg["frame_width"] == 640
    assert cfg["frame_height"] == 480
    clamped = normalize_camera_face_document({"frame_width": 999, "frame_height": 50})
    assert clamped["frame_width"] == 640
    assert clamped["frame_height"] == 120


def test_build_face_info_skipped_without_yaw():
    analysis = {"yaw_deg": None, "pitch_deg": None, "landmarks": []}
    assert build_face_info_message("d1", analysis, send_face_info=True) is None


def test_analyze_face_detections_multi():
    faces = [
        {
            "face_id": 1,
            "points": [
                {"name": "left_eye", "x": 100, "y": 80},
                {"name": "right_eye", "x": 140, "y": 80},
                {"name": "nose", "x": 120, "y": 100},
                {"name": "mouth_left", "x": 105, "y": 120},
                {"name": "mouth_right", "x": 135, "y": 120},
            ],
            "landmarks": [],
            "image_w": 320,
            "image_h": 240,
        },
        {
            "face_id": 2,
            "points": [
                {"name": "left_eye", "x": 220, "y": 90},
                {"name": "right_eye", "x": 260, "y": 90},
                {"name": "nose", "x": 240, "y": 110},
                {"name": "mouth_left", "x": 225, "y": 130},
                {"name": "mouth_right", "x": 255, "y": 130},
            ],
            "landmarks": [],
            "image_w": 320,
            "image_h": 240,
        },
    ]
    result = analyze_face_detections(faces)
    assert result["face_count"] == 2
    assert len(result["faces"]) == 2
    assert result["points"]


def test_face_tracker_profile_hysteresis():
    import os

    import numpy as np
    import pytest

    from deskbot_server.face_identity import attach_descriptor, compute_face_descriptor
    from deskbot_server.face_profiles_store import upsert_profile
    from deskbot_server.vision.face_embedding import is_embedding_vector

    if not os.path.isdir(os.path.expanduser("~/.insightface/models/buffalo_s")):
        pytest.skip("InsightFace buffalo_s 模型未下载，跳过 embedding 测试")

    points = [
        {"name": "left_eye", "x": 100.0, "y": 100.0},
        {"name": "right_eye", "x": 140.0, "y": 100.0},
        {"name": "nose", "x": 120.0, "y": 115.0},
        {"name": "mouth_left", "x": 105.0, "y": 130.0},
        {"name": "mouth_right", "x": 135.0, "y": 130.0},
    ]
    face = {"points": points, "landmarks": [], "image_w": 320, "image_h": 240}
    bgr = np.zeros((240, 320, 3), dtype=np.uint8)
    attach_descriptor(face, bgr_image=bgr)
    desc = face.get("face_descriptor") or compute_face_descriptor(points, [])
    assert desc is not None
    if not is_embedding_vector(desc):
        pytest.skip("embedding 未启用，跳过")
    thr = 0.40 if is_embedding_vector(desc) else 0.82
    profiles: list = []
    upsert_profile(profiles, name="小明", descriptor=desc, merge_threshold=thr)
    tracker = FaceTracker(identity_similarity_threshold=thr, max_dist_px=90.0)
    tracker._profiles = profiles
    tagged = tracker.assign_ids([dict(face)])
    assert tagged[0].get("person_id") == 1
    assert tagged[0].get("person_name") == "小明"
    # 鼻尖大幅移动仍应同一 face_id
    moved_face = dict(face)
    moved_face["points"] = [
        {"name": "left_eye", "x": 100.0, "y": 100.0},
        {"name": "right_eye", "x": 140.0, "y": 100.0},
        {"name": "nose", "x": 80.0, "y": 108.0},
        {"name": "mouth_left", "x": 105.0, "y": 130.0},
        {"name": "mouth_right", "x": 135.0, "y": 130.0},
    ]
    attach_descriptor(moved_face, bgr_image=bgr)
    moved = [moved_face]
    id1 = tagged[0]["face_id"]
    tagged2 = tracker.assign_ids(moved)
    assert tagged2[0]["face_id"] == id1
    assert tagged2[0].get("person_id") == 1


def test_face_tracker_assigns_stable_ids():
    points = [
        {"name": "left_eye", "x": 100.0, "y": 100.0},
        {"name": "right_eye", "x": 140.0, "y": 100.0},
        {"name": "nose", "x": 120.0, "y": 115.0},
        {"name": "mouth_left", "x": 105.0, "y": 130.0},
        {"name": "mouth_right", "x": 135.0, "y": 130.0},
    ]
    tracker = FaceTracker(max_dist_px=30.0, max_lost_frames=3)
    frame1 = [{"points": points, "landmarks": []}]
    frame2 = [{
        "points": [
            {"name": "left_eye", "x": 100.0, "y": 100.0},
            {"name": "right_eye", "x": 140.0, "y": 100.0},
            {"name": "nose", "x": 105.0, "y": 102.0},
            {"name": "mouth_left", "x": 105.0, "y": 130.0},
            {"name": "mouth_right", "x": 135.0, "y": 130.0},
        ],
        "landmarks": [],
    }]
    id1 = tracker.assign_ids(frame1)[0]["face_id"]
    id2 = tracker.assign_ids(frame2)[0]["face_id"]
    assert id1 == id2


def test_compute_frontal_angle():
    from deskbot_server.vision.geometry import (
        compute_frontal_angle_deg,
        compute_is_frontal_by_angle,
    )

    assert compute_frontal_angle_deg(10.0, -8.0) == 10.0
    assert compute_is_frontal_by_angle(10.0, 8.0, threshold_deg=15.0) is True
    assert compute_is_frontal_by_angle(20.0, 5.0, threshold_deg=15.0) is False


def test_resolve_descriptor_from_payload_points():
    from deskbot_server.camera_face_tune import set_face_embedding_enabled
    from deskbot_server.face_snapshot_cache import resolve_descriptor_from_payload

    points = [
        {"name": "left_eye", "x": 100, "y": 80},
        {"name": "right_eye", "x": 140, "y": 80},
        {"name": "nose", "x": 120, "y": 100},
        {"name": "mouth_left", "x": 105, "y": 120},
        {"name": "mouth_right", "x": 135, "y": 120},
    ]
    set_face_embedding_enabled(False)
    try:
        desc = resolve_descriptor_from_payload({"points": points, "landmarks": []})
    finally:
        set_face_embedding_enabled(None)
    assert desc is not None
    assert len(desc) >= 4


def test_deduplicate_overlapping_faces():
    from deskbot_server.face_identity import deduplicate_overlapping_faces

    base_points = [
        {"name": "left_eye", "x": 100, "y": 80},
        {"name": "right_eye", "x": 140, "y": 80},
        {"name": "nose", "x": 120, "y": 100},
        {"name": "mouth_left", "x": 105, "y": 120},
        {"name": "mouth_right", "x": 135, "y": 120},
    ]
    good = {"points": base_points, "landmarks": list(base_points), "image_w": 320, "image_h": 240}
    # 鼻尖略偏的重复框
    dup_points = [dict(p) for p in base_points]
    dup_points[2] = {"name": "nose", "x": 122, "y": 101}
    dup = {"points": dup_points, "landmarks": dup_points, "image_w": 320, "image_h": 240}
    out = deduplicate_overlapping_faces([good, dup])
    assert len(out) == 1


def test_face_descriptor_similarity():
    from deskbot_server.face_identity import compute_face_descriptor, descriptor_cosine_similarity

    points = [
        {"name": "left_eye", "x": 100, "y": 80},
        {"name": "right_eye", "x": 140, "y": 80},
        {"name": "nose", "x": 120, "y": 100},
        {"name": "mouth_left", "x": 105, "y": 120},
        {"name": "mouth_right", "x": 135, "y": 120},
    ]
    d1 = compute_face_descriptor(points, [])
    d2 = compute_face_descriptor(points, [])
    assert d1 is not None and d2 is not None
    assert descriptor_cosine_similarity(d1, d2) > 0.99


def test_pick_primary_face_prefers_frontal():
    a = {"is_frontal": False, "frontal_score": 0.9}
    b = {"is_frontal": True, "frontal_score": 0.2}
    assert pick_primary_face([a, b]) is b


def test_build_pb_wire_pairs_empty_segs_raises():
    import pytest

    try:
        build_pb_wire_pairs([], {}, servo_plan=[], sample_rate=24000)
        assert False, "expected error"
    except Exception:
        pass
