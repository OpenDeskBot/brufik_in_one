"""配置服务门面：封装调试偏好、表情场景、舵机配置等 dao 操作，供 controller 调用。"""

from deskbot_server.dao.debug_prefs_store import (
    debug_prefs_snapshot,
    get_camera_servo_auto_mode,
    get_live_service_enabled,
    normalize_camera_servo_auto_mode,
    persist_asr_auto_reply,
    persist_camera_servo_auto_mode,
    persist_live_service,
    set_camera_servo_auto_mode,
    set_live_service_enabled,
)
from deskbot_server.dao.face_design_store import (
    _load_face_design_cached,
    build_face_expression_catalog,
    ensure_face_design_file,
    resolve_face_expression,
)
from deskbot_server.dao.face_expr_scenes_store import (
    design_frames_to_pb_chain,
    find_design_scene_by_name,
    load_face_expr_scenes_file,
)
from deskbot_server.dao.scene_playbooks_store import (
    find_playbook_by_name,
    load_scene_playbooks_file,
    normalize_playbook,
)
from deskbot_server.dao.servo_config_store import (
    load_servo_cfg_file,
    normalize_servo_document,
    save_servo_cfg_file,
    servo_limits,
)

__all__ = [
    "_load_face_design_cached",
    "build_face_expression_catalog",
    "debug_prefs_snapshot",
    "design_frames_to_pb_chain",
    "ensure_face_design_file",
    "find_design_scene_by_name",
    "find_playbook_by_name",
    "get_camera_servo_auto_mode",
    "get_live_service_enabled",
    "load_face_expr_scenes_file",
    "load_scene_playbooks_file",
    "load_servo_cfg_file",
    "normalize_camera_servo_auto_mode",
    "normalize_playbook",
    "normalize_servo_document",
    "persist_asr_auto_reply",
    "persist_camera_servo_auto_mode",
    "persist_live_service",
    "resolve_face_expression",
    "save_servo_cfg_file",
    "servo_limits",
    "set_camera_servo_auto_mode",
    "set_live_service_enabled",
]
