"""PbService：PbSeq 构建服务（单例）。

提供两个核心功能：
1. 按场景名获取 PbSeq —— 供 LiveService 等直接入队发送。
2. 通过 LLM + TTS 结果组装 PbSeq —— 供 chat_flow 等直接入队发送。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from deskbot_server.dao.face_expr_scenes_store import (
    design_frames_to_pb_chain,
    find_design_scene_by_name,
    load_face_expr_scenes_file,
)
from deskbot_server.model.pb_seq import PbAction, PbBlock, PbSeq
from deskbot_server.pb.servo_pcm import attach_pb_device_hints_from_config
from deskbot_server.pb.shapes import (
    PB_ACTION_APPEND,
    PB_ACTION_REPLACE,
    PB_LEVEL_IDLE,
    PB_LEVEL_TASK,
    apply_pb_dispatch_fields,
)
from deskbot_server.pb.wire import build_pb_wire_pairs
from deskbot_server.utils.singleton import SingletonMeta

logger = logging.getLogger("deskbot-server")


class PbService(metaclass=SingletonMeta):
    """PbSeq 构建服务。零 IO（TTS 合成由调用方完成），纯数据组装。"""

    # ------------------------------------------------------------------
    # 功能1：按场景名获取 PbSeq
    # ------------------------------------------------------------------

    def get_scene_pb_seq(
        self,
        scene_name: str,
        *,
        device_id: str | None = None,
        level: int = PB_LEVEL_IDLE,
        action: PbAction = PbAction.REPLACE,
    ) -> PbSeq | None:
        """从 ``deskbot-face.json`` 加载指定场景，返回可直接入队的 ``PbSeq``。

        Args:
            scene_name: 场景名（不区分大小写）。
            device_id: 设备 ID（用于加载设备特定配置）。
            level: 序列优先级（默认 idle=0）。
            action: 队列调度策略（默认 REPLACE）。

        Returns:
            场景 PbSeq；场景不存在或无有效帧时返回 ``None``。
        """
        rows = load_face_expr_scenes_file(seed_if_missing=True, device_id=device_id) or []
        ent = find_design_scene_by_name(rows, scene_name)
        if ent is None:
            logger.debug("[pb_service] 场景 %r 未找到 device_id=%s", scene_name, device_id)
            return None

        req_id = uuid.uuid4().hex[:16]
        pairs = design_frames_to_pb_chain(ent.get("frames") or [], runtime_req=req_id)
        if not pairs:
            logger.debug("[pb_service] 场景 %r 无有效帧 device_id=%s", scene_name, device_id)
            return None

        frames = [msg for msg, _bins in pairs]
        apply_pb_dispatch_fields(frames, action=PB_ACTION_APPEND, level=level)
        attach_pb_device_hints_from_config(frames)

        pb_seq = PbSeq.from_wire_pairs(pairs, level=level)
        # 覆盖 action 为调用方指定值（from_wire_pairs 从 wire 读取，可能不一致）
        object.__setattr__(pb_seq, "action", action)
        logger.info(
            "[pb_service] scene=%s device_id=%s req=%s blocks=%d level=%d action=%s",
            scene_name, device_id, req_id, pb_seq.block_count, level, action.wire,
        )
        return pb_seq

    # ------------------------------------------------------------------
    # 功能2：通过 LLM + TTS 结果组装 PbSeq
    # ------------------------------------------------------------------

    def build_pb_seq_from_tts(
        self,
        segs: list[dict[str, Any]],
        tts_cfg: dict[str, Any],
        *,
        moves: list[dict[str, Any]] | None = None,
        anims: list[dict[str, Any]] | None = None,
        sample_rate: int,
        request_id: str | None = None,
        device_id: str | None = None,
        level: int = PB_LEVEL_TASK,
        action: str = PB_ACTION_REPLACE,
        volume: int | None = None,
        cam_fps: int | None = None,
        leading_move_steps: int = 0,
        random_servo_cfg: dict[str, Any] | None = None,
        servo_plan: list[dict[str, Any]] | None = None,
    ) -> PbSeq | None:
        """将 TTS 音素分片 + LLM moves/anims 组装为可直接入队的 ``PbSeq``。

        Args:
            segs: TTS 音素分片列表（含 ``pcm`` / ``ms`` / ``phoneme``）。
            tts_cfg: TTS 配置（含 ``sample_rate`` 等）。
            moves: LLM 返回的舵机动作列表。
            anims: LLM 返回的表情动画列表。
            sample_rate: 音频采样率。
            request_id: 请求 ID（不传则自动生成）。
            device_id: 设备 ID。
            level: 序列优先级（默认 task=1）。
            action: 队列调度策略（默认 replace）。
            volume: 音量 0-100。
            cam_fps: 相机帧率。
            leading_move_steps: 前置舵机步数。
            random_servo_cfg: 随机舵机配置。
            servo_plan: 舵机计划（与 moves 互斥）。

        Returns:
            组装好的 PbSeq；无有效分片时返回 ``None``。
        """
        if not segs:
            return None

        pairs, pb_req, n_pb, sr_pb = build_pb_wire_pairs(
            segs,
            tts_cfg,
            servo_plan=servo_plan,
            moves=moves,
            anims=anims,
            sample_rate=sample_rate,
            request_id=request_id,
            random_servo_cfg=random_servo_cfg,
            volume=volume,
            cam_fps=cam_fps,
            device_id=device_id,
            action=action,
            leading_move_steps=leading_move_steps,
        )
        if not pairs:
            return None

        # 从 wire 帧中读取实际 level（build_pb_wire_pairs 可能写入了不同值）
        task_level = int((pairs[0][0].get("level") or level)) if pairs else level
        pb_seq = PbSeq.from_wire_pairs(pairs, level=task_level)
        logger.info(
            "[pb_service] tts assembled req=%s level=%d blocks=%d sr=%d",
            pb_seq.req, pb_seq.level, pb_seq.block_count, sr_pb,
        )
        return pb_seq
