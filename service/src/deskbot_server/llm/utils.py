"""LLM 输出解析等纯文本工具，独立于 funasr/torch 等重依赖，
供 ``deskbot_server`` 主服务与 ``web/app.py`` 共享使用。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from deskbot_server.constants import SERVO_CFG_FILE
from deskbot_server.device_data import resolve_json_path
from deskbot_server.face_design_store import resolve_face_design_path
from deskbot_server.face_expr_scenes_store import load_face_expr_scenes_file
from deskbot_server.pb.llm_display import parse_llm_images
from deskbot_server.pb.servo_pcm import parse_pb_volume, parse_pb_cam_fps
from deskbot_server.servo_config_store import load_servo_cfg_file

_LLM_APPENDIX_CACHE: dict[str, tuple[float, str]] = {}


def _face_expr_scene_entries(*, device_id: Optional[str] = None) -> list[dict[str, Any]]:
    try:
        rows = load_face_expr_scenes_file(seed_if_missing=False, device_id=device_id)
    except (OSError, ValueError, json.JSONDecodeError):
        rows = None
    if not rows:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        frames = row.get("frames")
        if name and isinstance(frames, list) and frames:
            out.append(row)
    out.sort(key=lambda r: (str(r.get("name") or "").lower(), str(r.get("name") or "")))
    return out


def _cached_appendix(cache_key: str, mtime_path: str, build_fn) -> str:
    global _LLM_APPENDIX_CACHE
    try:
        mtime = os.path.getmtime(mtime_path)
    except OSError:
        return ""
    cached = _LLM_APPENDIX_CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    text = build_fn()
    _LLM_APPENDIX_CACHE[cache_key] = (mtime, text)
    return text


def llm_pb_moves_prompt_appendix(*, device_id: Optional[str] = None) -> str:
    """供 system prompt 追加：合法 ``moves`` 预设 id、label 与默认时长。"""
    def _build() -> str:
        try:
            cfg = load_servo_cfg_file(device_id=device_id)
        except (OSError, ValueError):
            return ""
        if not cfg:
            return ""
        lines: list[str] = []
        for preset in cfg.get("presets") or []:
            if not isinstance(preset, dict):
                continue
            pid = str(preset.get("id") or "").strip()
            label = str(preset.get("label") or "").strip()
            if not pid:
                continue
            default_ms = sum(max(1, int(s.get("ms") or 0)) for s in (preset.get("steps") or []))
            lines.append(f"      - {pid}: {label or pid}（默认 {default_ms} ms）")
        if not lines:
            return ""
        body = "\n".join(lines)
        return (
            "  - moves: 数组。每项 ``{\"move\": \"预设动作id\", \"ms\": 执行时长}``。"
            "``move`` 须从下列预设中选取；``ms`` 为该动作整体期望时长（毫秒），"
            "服务端会按预设各 step 默认时长比例缩放，**ms 越大越慢、越小越快**。\n"
            f"    可用预设动作：\n{body}\n"
            "    不需要动作时写 []。\n"
        )

    mtime_path = resolve_json_path(SERVO_CFG_FILE, device_id)
    cache_key = f"moves:{device_id or ''}"
    return _cached_appendix(cache_key, mtime_path, _build)


def llm_pb_anims_prompt_appendix(*, device_id: Optional[str] = None) -> str:
    """供 system prompt 追加：合法 ``anims`` 场景 name、title 与默认时长。"""
    def _build() -> str:
        rows = _face_expr_scene_entries(device_id=device_id)
        if not rows:
            return ""
        lines: list[str] = []
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            title = str(row.get("title") or name).strip()
            default_ms = sum(max(1, int(fr.get("ms") or 0)) for fr in (row.get("frames") or []))
            lines.append(f"      - {name}: {title}（默认 {default_ms} ms）")
        body = "\n".join(lines)
        return (
            "  - anims: 数组。每项 ``{\"anim\": \"场景name\", \"ms\": 执行时长, \"bg\"?, \"color\"?}``。"
            "``anim`` 须与 ``data/deskbot-face.json`` 的 ``emotions[].name``（或 ``alias``）一致；"
            "``ms`` 为该段表情动画整体期望时长，服务端按各帧默认时长比例缩放，"
            "**ms 越大越慢、越小越快**。未知名会回退 ``default`` / ``idle``，仍无则跳过。\n"
            "    可选 ``bg``：全屏背景色字符串（``#RGB`` / ``#RRGGBB`` / 命名色）；"
            "可选 ``color``：该段默认前景色（未单独设色的 text 图元会继承）。\n"
            f"    可用表情动画：\n{body}\n"
            "    不需要时写 []。有 TTS 音素时分片口型仍由音素驱动，其它图层用所选 anim。\n"
        )

    def _face_anim_mtime_path() -> str:
        return resolve_face_design_path(device_id=device_id)

    mtime_path = _face_anim_mtime_path()
    cache_key = f"anims:{device_id or ''}"
    return _cached_appendix(cache_key, mtime_path, _build)


def llm_pb_plan_prompt_appendix(*, device_id: Optional[str] = None) -> str:
    """moves + anims 附录合并（替代旧 ``scenes`` / ``servo`` 直写说明）。"""
    parts = [
        llm_pb_moves_prompt_appendix(device_id=device_id),
        llm_pb_anims_prompt_appendix(device_id=device_id),
    ]
    return "".join(p for p in parts if p)


def llm_pb_scenes_prompt_appendix(*, device_id: Optional[str] = None) -> str:
    """兼容旧调用名；返回 moves/anims 计划附录。"""
    return llm_pb_plan_prompt_appendix(device_id=device_id)


def llm_memory_prompt_appendix(device_id: Optional[str] = None) -> str:
    """长期记忆列表，注入 system prompt。"""
    from deskbot_server.memory_store import list_memory_for_device

    rows = list_memory_for_device(device_id, limit=30)
    if not rows:
        return "长期记忆：暂无。"
    lines: list[str] = []
    for e in rows:
        eid = str(e.get("id") or "")
        text = str(e.get("text") or "").strip()
        if text:
            lines.append(f"  - [{eid}] {text}")
    return "长期记忆（可用 memory_delete 删除，id 见方括号）：\n" + "\n".join(lines)


def llm_device_screen_appendix(device_id: Optional[str] = None) -> str:
    """屏幕分辨率与当前音量，注入 system prompt。"""
    from deskbot_server.device_volume_store import get_device_volume
    from deskbot_server.pb.display import FACE_LCD_HEIGHT, FACE_LCD_WIDTH

    vol = get_device_volume(device_id)
    return (
        "设备屏幕与音量：\n"
        f"  - 逻辑分辨率：**{FACE_LCD_WIDTH}×{FACE_LCD_HEIGHT}** 像素，原点左上角 (0,0)。\n"
        f"  - 当前播放音量：**{vol}**（0–100）。JSON 中写 ``volume`` 会下发到 ESP32 并**持久保存**；"
        "省略则保持当前音量。\n"
        "  - 屏幕仅支持 ``images`` 数组展示图片（``{b64, x?, y?, w?, h?}``，base64 JPEG/PNG），"
        "服务端转为 pb 下发；**不要**使用 ``screen_text`` 等屏幕文字字段。\n"
    )


def llm_tools_prompt_appendix() -> str:
    """LLM 可返回的 tools 数组说明。"""
    return (
        "可用工具（可选 ``tools`` 数组；需要工具时 ``tools`` 非空、``tts`` 可留空，"
        "服务端执行后会再次调用你；最终回复时 ``tools`` 写 [] 并填写 ``tts``）："
        "用户已说话时优先在 ``tts`` 里正常回答；不要只返回 tools 而省略完整 JSON 对象。\n"
        "  - register_face: {\"tool\":\"register_face\",\"name\":\"姓名\",\"face_id\":1}\n"
        "    将当前画面 face_id 的人脸注册/更新到档案（embedding 512 维）；"
        "face_id 见每轮 user 消息「图像识别」；仅一张脸时可省略 face_id；多人须指定 face_id 或先向用户澄清。\n"
        "  - capture_camera: {\"tool\":\"capture_camera\"}\n"
        "    获取 ESP32 **最近上传**的一帧相机 JPEG（返回 ``jpeg_base64`` 与尺寸）。"
        "用于：给主人「拍照」后在屏幕 ``images`` 展示；或结合画面内容做判断。"
        "若返回无帧，请提示主人确认相机上行已开启。\n"
        "  - memory_add: {\"tool\":\"memory_add\",\"text\":\"要记住的内容\"}\n"
        "  - memory_delete: {\"tool\":\"memory_delete\",\"id\":\"记忆id\"}\n"
        "  - schedule_task: cron 定时任务增删改查（北京时间东八区）。"
        "**用户要求定时/提醒时，必须调用本工具，禁止仅用 tts 口头答应。**\n"
        "    示例：主人说「两分钟后请我喝水」→ 第一轮 JSON：\n"
        "    {\"tools\":[{\"tool\":\"schedule_task\",\"action\":\"create\",\"task\":\"提醒喝水\","
        "\"delay_minutes\":2,\"task_kind\":\"once\"}],\"tts\":\"\"}\n"
        "    工具成功后第二轮：{\"tools\":[],\"tts\":\"好，两分钟后提醒你喝水。\"}\n"
        "    创建时无需填写 session_id（服务端自动绑定当前 session）。\n"
        "    **先判断一次性还是周期性**：\n"
        "    · 一次性 once：如「明天9点提醒」→ task_kind=once + cron \"0 9 13 6 *\"（分 时 日 月 周）\n"
        "    · 周期性 recurring：如「每天8点」→ task_kind=recurring + cron \"0 8 * * *\"\n"
        "    · 相对延迟：delay_minutes 填数字（「两分钟」→ 2）\n"
        "    · 查询列表：{\"action\":\"list\"}；读取：{\"action\":\"get\",\"id\":\"…\"}\n"
        "    · 修改：{\"action\":\"update\",\"id\":\"…\",\"cron\":\"0 9 * * *\",\"task\":\"…\",\"enabled\":true}\n"
        "    · 删除：{\"action\":\"delete\",\"id\":\"…\"}\n"
        "  - webfetch: {\"tool\":\"webfetch\",\"url\":\"https://…\"} 抓取网页文本\n"
        "  - websearch: {\"tool\":\"websearch\",\"query\":\"搜索词\"} 网络搜索摘要\n"
        "  - read: {\"tool\":\"read\",\"path\":\"notes.txt\"} 读取本设备 tmp 目录文件\n"
        "  - write: {\"tool\":\"write\",\"path\":\"notes.txt\",\"content\":\"…\"} 写入本设备 tmp 目录\n"
        "    read/write 路径仅限 data/device/{device_id}/tmp/ 下，禁止 .. 与绝对路径。\n"
        "  - session: 查询当前与最近对话 session（服务端按 10 分钟无对话自动开新 session）\n"
        "    · 当前 session：{\"tool\":\"session\",\"action\":\"current\"}\n"
        "    · 最近列表：{\"tool\":\"session\",\"action\":\"list\",\"limit\":10}\n"
        "    · 读取详情：{\"tool\":\"session\",\"action\":\"get\",\"session_id\":\"…\"}（省略 id 则读当前）\n"
    )


def llm_static_context_prompt_appendix(device_id: Optional[str] = None) -> str:
    """长期记忆 + 工具说明（传感器/人脸见每轮 user 消息）。"""
    parts = [llm_memory_prompt_appendix(device_id), llm_tools_prompt_appendix()]
    return "\n\n".join(p for p in parts if p)


def llm_face_context_prompt_appendix(device_id: Optional[str] = None) -> str:
    """兼容旧调用名；人脸已移至 user 消息，此处仅记忆与工具。"""
    return llm_static_context_prompt_appendix(device_id)


def llm_recognized_faces_prompt_appendix(device_id: Optional[str] = None) -> str:
    """兼容旧调用名。"""
    return llm_static_context_prompt_appendix(device_id)


_LLM_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)


def _parse_need_reply_value(v: Any) -> bool:
    """JSON 里 ``need_reply`` 的宽松解析；缺省由调用方视为需要回复。"""
    if v is False or v == 0:
        return False
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("false", "0", "no", "否", "不需要", "不用", "none"):
            return False
        if s in ("true", "1", "yes", "是", "需要"):
            return True
        return bool(s)
    return bool(v)


def _parsed_json_need_reply(parsed: dict) -> bool:
    if "need_reply" not in parsed:
        return True
    return _parse_need_reply_value(parsed.get("need_reply"))


def parse_servo_plan_item(obj: Any) -> Optional[dict[str, Any]]:
    """解析 ``servo`` 数组单条：延时 ``hold_ms`` / ``hold``+``ms``，或标准 ``xm``…``ms``。"""
    if not isinstance(obj, dict):
        return None
    if obj.get("hold") is True or obj.get("hold") == 1:
        try:
            h = int(obj.get("ms", obj.get("hold_ms", 0)))
        except (TypeError, ValueError):
            h = 0
        if h > 0:
            return {"_hold_ms": min(h, 30_000)}
    if "hold_ms" in obj:
        try:
            h = int(obj["hold_ms"])
        except (TypeError, ValueError):
            h = 0
        if h > 0:
            return {"_hold_ms": min(h, 30_000)}
    return normalize_pb_servo_dict(obj)


def normalize_pb_servo_dict(obj: Any) -> Optional[dict[str, int]]:
    """校验并归一化单条 pb 舵机指令（``xm``/``ym``/``x``/``y``/``ms``），非法则 ``None``。"""
    if not isinstance(obj, dict):
        return None
    try:
        xm = int(obj.get("xm", 0))
        ym = int(obj.get("ym", 0))
        x = int(obj.get("x", 0))
        y = int(obj.get("y", 0))
        ms = int(obj.get("ms", 0))
    except (TypeError, ValueError):
        return None
    if xm not in (0, 1, 2) or ym not in (0, 1, 2):
        return None
    if ms <= 0:
        return None
    return {"xm": xm, "ym": ym, "x": x, "y": y, "ms": ms}


def coerce_pb_v2_downlink_payload(payload: Any) -> dict[str, Any]:
    """pb v2 下行：``servo`` 须为数组；兼容误写成单对象的历史调用。"""
    if not isinstance(payload, dict):
        return {}
    servo = payload.get("servo")
    if not isinstance(servo, dict):
        return payload
    norm = normalize_pb_servo_dict(servo)
    out = dict(payload)
    if norm:
        out["servo"] = [norm]
    else:
        out.pop("servo", None)
    return out


def _parse_llm_move_items(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        move_id = str(item.get("move") or "").strip()
        try:
            ms = int(item.get("ms", 0))
        except (TypeError, ValueError):
            continue
        if not move_id or ms <= 0:
            continue
        out.append({"move": move_id, "ms": ms})
    return out


def _parse_llm_anim_items(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        anim_name = str(item.get("anim") or "").strip()
        try:
            ms = int(item.get("ms", 0))
        except (TypeError, ValueError):
            continue
        if not anim_name or ms <= 0:
            continue
        out.append({"anim": anim_name, "ms": ms})
    return out


def _parse_llm_tool_items(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or item.get("name") or "").strip()
        if not tool:
            continue
        row = dict(item)
        row["tool"] = tool
        out.append(row)
    return out


def _coerce_llm_reply_object(obj: Any) -> Optional[dict[str, Any]]:
    """把 LLM 误输出的「仅 tools 数组 / 单条 tool 对象」规范为完整 JSON 对象。"""
    if isinstance(obj, list):
        tools = _parse_llm_tool_items(obj)
        if tools:
            return {"tools": tools, "tts": "", "need_reply": True}
        return None
    if not isinstance(obj, dict):
        return None
    if (obj.get("tool") or obj.get("name")) and "tools" not in obj:
        tools = _parse_llm_tool_items([obj])
        if tools:
            out: dict[str, Any] = {"tools": tools}
            for key in (
                "need_reply",
                "tts",
                "reply",
                "moves",
                "anims",
                "volume",
                "cam_fps",
                "images",
                "scenes",
                "servo",
            ):
                if key in obj:
                    out[key] = obj[key]
            out.setdefault("tts", "")
            return out
    return obj


def parse_llm_reply(raw: str) -> dict:
    """把 LLM 输出尝试解析为约定 JSON。

    格式 ``{"need_reply", "tts", "volume?", "moves", "anims", "tools": [...]}``；
    仍兼容旧版 ``servo`` / ``scenes`` 与 ``reply`` 字段。

    失败时把整段文本当作 ``reply`` 返回，**不抛异常**。
    """
    text = (raw or "").strip()
    parsed: Optional[dict] = None

    candidates = []
    if text:
        candidates.append(text)
        m = _LLM_JSON_FENCE_RE.search(text)
        if m:
            candidates.append(m.group(1))
        try:
            i = text.index("{")
            j = text.rindex("}")
            if j > i:
                candidates.append(text[i : j + 1])
        except ValueError:
            pass

        try:
            i = text.index("[")
            j = text.rindex("]")
            if j > i:
                candidates.append(text[i : j + 1])
        except ValueError:
            pass

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (TypeError, ValueError):
            continue
        coerced = _coerce_llm_reply_object(obj)
        if isinstance(coerced, dict):
            parsed = coerced
            break

    servo_out: list[Any] = []
    moves_out: list[dict[str, Any]] = []
    anims_out: list[dict[str, Any]] = []
    if isinstance(parsed, dict):
        raw_servo = parsed.get("servo")
        if isinstance(raw_servo, dict):
            raw_servo = [raw_servo]
        if isinstance(raw_servo, (list, tuple)):
            for item in raw_servo:
                ent = parse_servo_plan_item(item)
                if ent:
                    servo_out.append(ent)
        moves_out = _parse_llm_move_items(parsed.get("moves"))
        anims_out = _parse_llm_anim_items(parsed.get("anims"))
        tools_out = _parse_llm_tool_items(parsed.get("tools"))
        reply_tts = parsed.get("tts")
        reply_legacy = parsed.get("reply")
        reply: str
        if isinstance(reply_tts, str) and reply_tts.strip():
            reply = reply_tts.strip()
        elif isinstance(reply_legacy, str) and reply_legacy.strip():
            reply = reply_legacy.strip()
        else:
            # 合法 JSON 但 tts/reply 均为空：勿把整段 JSON 当朗读文本
            reply = ""
        scenes_out: list[str] = []
        raw_scenes = parsed.get("scenes")
        if isinstance(raw_scenes, str):
            raw_scenes = [raw_scenes]
        if isinstance(raw_scenes, (list, tuple)):
            for x in raw_scenes:
                if isinstance(x, str):
                    v = x.strip()
                    if v:
                        scenes_out.append(v)
        vol = parse_pb_volume(parsed.get("volume"))
        cam_fps = parse_pb_cam_fps(parsed.get("cam_fps"))
        images_out = parse_llm_images(parsed.get("images"))
        return {
            "reply": reply,
            "moves": moves_out,
            "anims": anims_out,
            "tools": tools_out,
            "scenes": scenes_out,
            "servo": servo_out,
            "volume": vol,
            "cam_fps": cam_fps,
            "images": images_out,
            "need_reply": _parsed_json_need_reply(parsed),
            "json_ok": True,
            "raw": text,
        }

    return {
        "reply": text,
        "moves": [],
        "anims": [],
        "tools": [],
        "scenes": [],
        "servo": [],
        "volume": None,
        "cam_fps": None,
        "images": [],
        "need_reply": True,
        "json_ok": False,
        "raw": text,
    }


__all__ = [
    "llm_device_screen_appendix",
    "llm_face_context_prompt_appendix",
    "llm_memory_prompt_appendix",
    "llm_pb_anims_prompt_appendix",
    "llm_pb_moves_prompt_appendix",
    "llm_pb_plan_prompt_appendix",
    "llm_pb_scenes_prompt_appendix",
    "llm_recognized_faces_prompt_appendix",
    "llm_static_context_prompt_appendix",
    "llm_tools_prompt_appendix",
    "coerce_pb_v2_downlink_payload",
    "normalize_pb_servo_dict",
    "parse_llm_reply",
    "parse_servo_plan_item",
]
