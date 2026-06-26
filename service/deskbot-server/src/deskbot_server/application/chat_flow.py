from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Optional

from deskbot_server.application.llm_tool_loop import complete_llm_with_tool_loop
from deskbot_server.auto_reply import get_asr_voice_auto_reply_enabled
from deskbot_server.core.ports.downlink import DownlinkPort, PipelineEventsPort
from deskbot_server.core.types import ChatTurnResult
from deskbot_server.device_volume_store import persist_device_volume
from deskbot_server.pb.llm_display import build_capture_image_for_display
from deskbot_server.pb.scenes import _pb_scene_entry_by_name, _prepare_pb_scene_chain_frames
from deskbot_server.pb.shapes import PB_ACTION_APPEND, PB_ACTION_REPLACE
from deskbot_server.pb.wire import build_pb_wire_pairs, device_pb_json_msg, pb_wire_json_bytes
from deskbot_server.tts.text_split import split_tts_by_punctuation
from deskbot_server.util import _ms_between

if TYPE_CHECKING:
    from deskbot_server.application.chat_service import ChatService
    from deskbot_server.ws.device_pipeline import DevicePipelineBroker
    from deskbot_server.ws.registry import DeviceRegistry

logger = logging.getLogger("deskbot-server")

_SCHEDULED_TASK_PREFIX = "[系统定时任务]"


class _TtsPrefetch:
    """LLM 流式输出中 ``tts`` 就绪后提前启动音素 TTS 合成。"""

    def __init__(self, chat: "ChatService") -> None:
        self._chat = chat
        self.task: asyncio.Task | None = None

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
        self.task = None

    async def on_ready(self, tts: str) -> None:
        text = (tts or "").strip()
        if not text:
            return
        self.cancel()
        self.task = asyncio.create_task(self._chat.tts_phoneme_segments(text))
        logger.info("[LLM] 流式 tts 就绪，提前启动 TTS prefetch text=%r", text[:80])


def _is_scheduled_task_user_text(user_text: str) -> bool:
    return str(user_text or "").strip().startswith(_SCHEDULED_TASK_PREFIX)


def _scheduled_task_description(user_text: str) -> str:
    text = str(user_text or "").strip().split("\n", 1)[0]
    m = re.search(
        r"请(?:向主人朗声提醒并)?执行以下任务(?:并向主人汇报结果)?[:：](.+)$",
        text,
    )
    if m:
        return m.group(1).strip()
    return text.replace(_SCHEDULED_TASK_PREFIX, "").strip()


def _scheduled_reminder_tts(description: str) -> str:
    desc = str(description or "").strip()
    if not desc:
        return "主人，提醒时间到了。"
    if desc.startswith("提醒"):
        body = desc[2:].strip() or "一下"
        return f"主人，该{body}啦。"
    return f"主人，{desc}。"


def _scheduled_tts_looks_like_meta_report(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return True
    meta_markers = ("已发送", "已提醒", "已完成", "已执行", "汇报", "任务完成", "提醒过了")
    return any(m in t for m in meta_markers)


def _voice_was_played(result: ChatTurnResult) -> bool:
    if result.voice_auto_reply_off or result.error or result.status != "ok":
        return False
    if result.t_tts_synth_end is None or result.t_llm_end is None:
        return False
    return result.t_tts_synth_end > result.t_llm_end + 0.05


async def run_chat_turn(
    downlink: DownlinkPort,
    chat: ChatService,
    user_text: str,
    *,
    request_id: Optional[str] = None,
    device_id: Optional[str] = None,
    registry: Optional[DeviceRegistry] = None,
    t_asr_start: Optional[float] = None,
    t_asr_text: Optional[float] = None,
    force_voice: bool = False,
    pipeline_broker: Optional["DevicePipelineBroker"] = None,
    reuse_session_id: Optional[str] = None,
) -> ChatTurnResult:
    """在已有用户侧文本后执行 LLM + TTS/pb 管道（应用层，不依赖 WebSocket 类型）。"""
    result = ChatTurnResult()
    is_scheduled = _is_scheduled_task_user_text(user_text)
    sched_desc = _scheduled_task_description(user_text) if is_scheduled else ""

    try:
        if not force_voice and not get_asr_voice_auto_reply_enabled():
            now_m = time.monotonic()
            result.t_llm_end = now_m
            result.t_tts_synth_end = now_m
            result.t_tts_end = now_m
            result.voice_auto_reply_off = True
            logger.info(
                "[asr] 自动应答已关闭，跳过 LLM/TTS device_id=%s req=%s user=%r",
                device_id,
                request_id,
                (user_text or "")[:120],
            )
            await downlink.emit_stage(
                "voice_auto_reply_off",
                request_id=request_id,
                send_client=False,
                event_fields={
                    "asr_text": user_text,
                    "asr_ms": _ms_between(t_asr_start, t_asr_text),
                    "source": "asr" if t_asr_start is not None else "text",
                    "status": "ok",
                },
            )
            return result

        ack_ctx = None
        if registry is not None and device_id:
            ack_ctx = await registry.pb_ack_llm_context(device_id)

        session_id: Optional[str] = None
        history_messages: list[dict[str, str]] | None = None
        if device_id:
            from deskbot_server.session_store import (
                ensure_active_session,
                session_history_for_llm,
            )

            if reuse_session_id:
                session_id = str(reuse_session_id).strip()
                if session_id:
                    history_messages = session_history_for_llm(device_id, session_id)
            else:
                active = ensure_active_session(device_id, user_text=user_text)
                session_id = str(active.get("session_id") or "")
                if session_id:
                    history_messages = session_history_for_llm(device_id, session_id)

        tts_prefetch = _TtsPrefetch(chat)
        parsed, llm_tools, tool_results, answer = await complete_llm_with_tool_loop(
            chat,
            user_text,
            device_id=device_id,
            session_id=session_id,
            device_context=ack_ctx,
            history_messages=history_messages,
            request_id=request_id,
            dp_broker=pipeline_broker,
            pipeline_source="asr" if t_asr_start is not None else "text",
            on_tts_ready=tts_prefetch.on_ready,
        )

        reply_text = parsed["reply"]
        llm_scenes = list(parsed.get("scenes") or [])
        llm_moves = list(parsed.get("moves") or [])
        llm_anims = list(parsed.get("anims") or [])
        need_reply = bool(parsed.get("need_reply", True))
        if is_scheduled:
            need_reply = True

        if parsed.get("volume") is not None and device_id:
            persist_device_volume(parsed["volume"], device_id=device_id)

        display_images = list(parsed.get("images") or [])
        for tr in tool_results:
            if tr.get("ok") and tr.get("jpeg_base64"):
                try:
                    display_images.append(
                        build_capture_image_for_display(str(tr["jpeg_base64"]))
                    )
                except ValueError:
                    pass
        parsed["images"] = display_images

        result.llm_text = reply_text
        result.llm_raw = answer or parsed.get("raw") or ""
        result.scenes = llm_scenes
        result.moves = llm_moves
        result.anims = llm_anims
        result.tools = llm_tools
        result.tool_results = tool_results
        result.servo = list(parsed.get("servo") or [])
        result.need_reply = need_reply
        result.json_ok = parsed["json_ok"]
        result.t_llm_end = time.monotonic()

        if device_id and session_id:
            from deskbot_server.session_store import append_turn

            assistant_text = (reply_text or "").strip() or (answer or "").strip()
            try:
                append_turn(device_id, session_id, user_text, assistant_text)
            except Exception:
                logger.exception(
                    "[session] 保存对话失败 device_id=%s session_id=%s req=%s",
                    device_id,
                    session_id,
                    request_id,
                )

        llm_ms = _ms_between(t_asr_text, result.t_llm_end)
        logger.info(
            "[LLM] 回复 device_id=%s req=%s llm_ms=%s json_ok=%s need_reply=%s json=%s",
            device_id,
            request_id,
            llm_ms,
            parsed["json_ok"],
            need_reply,
            parsed["raw"],
        )
        await downlink.emit_stage(
            "llm_done",
            request_id=request_id,
            send_client=False,
            event_fields={
                "asr_text": user_text,
                "asr_ms": _ms_between(t_asr_start, t_asr_text),
                "llm_text": reply_text,
                "llm_raw": result.llm_raw,
                "llm_ms": llm_ms,
                "source": "asr" if t_asr_start is not None else "text",
            },
        )

        if not parsed["json_ok"]:
            logger.warning(
                "[LLM] 输出未通过 JSON 解析，按整段文本走 TTS。device_id=%s req=%s",
                device_id,
                request_id,
            )

        if not need_reply and not is_scheduled:
            has_motion = bool(llm_moves or llm_anims or parsed.get("screen_text") or parsed.get("images"))
            if has_motion:
                logger.info(
                    "[LLM] need_reply=false 但有 moves/anims/屏幕内容，下发动作 pb device_id=%s req=%s",
                    device_id,
                    request_id,
                )
                try:
                    await _run_pb_playback(
                        downlink,
                        chat,
                        reply_text="",
                        parsed=parsed,
                        llm_scenes=[],
                        request_id=request_id,
                        device_id=device_id,
                        result=result,
                        t_asr_start=t_asr_start,
                        motion_only=True,
                    )
                except Exception as pb_exc:
                    logger.exception("[LLM] need_reply=false 动作 pb 失败")
                    result.status = "error"
                    result.error = f"motion_pb: {pb_exc}"
                return result
            logger.info(
                "[LLM] need_reply=false，跳过 TTS/pb。device_id=%s req=%s",
                device_id,
                request_id,
            )
            result.t_tts_end = time.monotonic()
            return result

        playback_text = (reply_text or "").strip()
        if is_scheduled and (
            not playback_text or _scheduled_tts_looks_like_meta_report(playback_text)
        ):
            playback_text = _scheduled_reminder_tts(sched_desc)
            logger.info(
                "[scheduler] 定时任务使用兜底提醒语 device_id=%s req=%s tts=%r",
                device_id,
                request_id,
                playback_text,
            )
        if not playback_text:
            if llm_moves or llm_anims:
                playback_text = "。"
                logger.info(
                    "[LLM] tts 为空但有 moves/anims，使用占位 TTS device_id=%s req=%s",
                    device_id,
                    request_id,
                )
            else:
                logger.info(
                    "[LLM] tts 为空且无 moves/anims，跳过 TTS/pb device_id=%s req=%s",
                    device_id,
                    request_id,
                )
                result.t_tts_end = time.monotonic()
                return result

        await downlink.emit_stage(
            "tts_start",
            request_id=request_id,
            send_client=False,
            event_fields={
                "asr_text": user_text,
                "llm_text": reply_text,
                "tts_text": playback_text,
                "source": "asr" if t_asr_start is not None else "text",
            },
        )
        try:
            await _run_pb_playback(
                downlink,
                chat,
                reply_text=playback_text,
                parsed=parsed,
                llm_scenes=llm_scenes if not llm_anims else [],
                request_id=request_id,
                device_id=device_id,
                result=result,
                t_asr_start=t_asr_start,
                prefetch_tts=tts_prefetch.task,
            )
        except Exception as tts_exc:
            tts_prefetch.cancel()
            logger.exception("TTS 流程失败")
            result.status = "error"
            result.error = f"tts: {tts_exc}"
    except Exception as llm_exc:
        logger.exception("LLM 流程失败")
        result.status = "error"
        result.error = f"llm: {llm_exc}"

    return result


async def run_device_tts_only(
    downlink: DownlinkPort,
    chat: "ChatService",
    text: str,
    *,
    request_id: Optional[str] = None,
    device_id: Optional[str] = None,
    scenes: Optional[list] = None,
    moves: Optional[list] = None,
    anims: Optional[list] = None,
) -> ChatTurnResult:
    """跳过 LLM，将给定文本走音素 TTS 并下发 pb；可选在同一条链锁内追加场景 pb 帧。"""
    reply_text = (text or "").strip()
    result = ChatTurnResult()
    result.llm_text = reply_text
    result.t_llm_end = time.monotonic()
    await downlink.emit_stage(
        "tts_start",
        request_id=request_id,
        send_client=False,
        event_fields={
            "tts_text": reply_text,
            "source": "device_tts",
        },
    )
    parsed = {
        "reply": reply_text,
        "servo": [],
        "scenes": [],
        "json_ok": True,
        "need_reply": True,
        "raw": reply_text,
        "moves": list(moves or []),
        "anims": list(anims or []),
    }
    if not reply_text:
        result.status = "error"
        result.error = "empty text"
        return result
    try:
        scene_list = [
            str(s).strip()
            for s in (scenes or [])
            if isinstance(s, str) and str(s).strip()
        ]
        if parsed["moves"] or parsed["anims"]:
            scene_list = []
        await _run_pb_playback(
            downlink,
            chat,
            reply_text=reply_text,
            parsed=parsed,
            llm_scenes=scene_list,
            request_id=request_id,
            device_id=device_id,
            result=result,
            t_asr_start=result.t_llm_end,
        )
    except Exception as tts_exc:
        logger.exception("[device_tts] TTS 流程失败 device_id=%s", device_id)
        result.status = "error"
        result.error = f"tts: {tts_exc}"
    return result


async def run_device_playbook(
    downlink: DownlinkPort,
    chat: "ChatService",
    playbook: dict,
    *,
    request_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> ChatTurnResult:
    """场景编排：TTS 与表情/舵机轨在同一条 pb 链内交错下发（非 TTS 结束后再播）。"""
    from deskbot_server.scene_playbook_runner import playbook_to_llm_plan

    text, moves, anims = playbook_to_llm_plan(playbook)
    if not text.strip():
        if moves or anims:
            text = "。"
        else:
            result = ChatTurnResult()
            result.status = "error"
            result.error = "empty playbook"
            return result
    return await run_device_tts_only(
        downlink,
        chat,
        text,
        request_id=request_id,
        device_id=device_id,
        scenes=None,
        moves=moves,
        anims=anims,
    )


async def _send_pb_pairs(
    downlink: DownlinkPort,
    *,
    pairs: list[tuple[dict, list[bytes]]],
    pb_req: str,
    device_id: Optional[str],
    n_pb: int,
) -> bool:
    """下发一组 pb wire 帧；返回是否因失败而中止。"""
    from deskbot_server.constants import PB_WAIT_ACK
    from deskbot_server.ws.pb_ack_waiter import pb_ack_gate, pb_wait_ack_timeout_sec

    if device_id and PB_WAIT_ACK:
        await pb_ack_gate.begin_req(device_id, pb_req)

    pb_aborted = False
    for i, (msg, binaries) in enumerate(pairs):
        wire_text = device_pb_json_msg(msg)
        logger.info(
            "[pb TX] %d/%d wire_json bytes=%d %s",
            i + 1,
            n_pb,
            pb_wire_json_bytes(msg),
            wire_text,
        )
        ok = await downlink.send_pb_wire(wire_text, binaries=binaries)
        if binaries:
            logger.info(
                "[pb TX] %d/%d binary idx=%s parts=%d total_bytes=%d ok=%s",
                i + 1,
                n_pb,
                msg.get("idx"),
                len(binaries),
                sum(len(b) for b in binaries),
                ok,
            )
        elif not ok:
            logger.warning(
                "[pb TX] %d/%d JSON 下发失败 idx=%s device_id=%s",
                i + 1,
                n_pb,
                msg.get("idx"),
                device_id,
            )
        if not ok:
            pb_aborted = True
            logger.error(
                "[pb TX] 中止下发 device_id=%s pb_req=%s 失败于 %d/%d idx=%s"
                "（常见：上一包 binary 后 ESP32 断线）",
                device_id,
                pb_req,
                i + 1,
                n_pb,
                msg.get("idx"),
            )
            break
        if (
            not pb_aborted
            and binaries
            and int((msg.get("audio") or {}).get("next_bin_len") or 0) > 0
            and device_id
            and PB_WAIT_ACK
        ):
            ack_ok = await pb_ack_gate.wait_idx(
                device_id,
                pb_req,
                int(msg.get("idx") or 0),
                timeout=pb_wait_ack_timeout_sec(),
            )
            if not ack_ok:
                pb_aborted = True
                logger.error(
                    "[pb TX] 中止下发 device_id=%s pb_req=%s 未收到 idx>=%s 的 pb_ack",
                    device_id,
                    pb_req,
                    msg.get("idx"),
                )
                break
    return pb_aborted


async def _run_pb_playback(
    downlink: DownlinkPort,
    chat: ChatService,
    *,
    reply_text: str,
    parsed: dict,
    llm_scenes: list,
    request_id: Optional[str],
    device_id: Optional[str],
    result: ChatTurnResult,
    t_asr_start: Optional[float],
    motion_only: bool = False,
    prefetch_tts: asyncio.Task | None = None,
) -> None:
    if motion_only:
        sr_pb = int(chat.tts_cfg.get("sample_rate") or 24000)
        segs: list[dict] = []
        from deskbot_server.pb.llm_plan import expand_llm_anims, expand_llm_moves
        from deskbot_server.pb.servo_pcm import _silence_phoneme_seg

        move_steps = expand_llm_moves(list(parsed.get("moves") or []), device_id=device_id)
        anim_frames = expand_llm_anims(list(parsed.get("anims") or []), device_id=device_id)
        if not move_steps and anim_frames:
            total_ms = sum(max(1, int(f.get("ms") or 40)) for f in anim_frames)
            segs = [_silence_phoneme_seg(total_ms, sr_pb)]
        text_chunks = [""]
    else:
        if prefetch_tts is not None:
            text_chunks = [reply_text]
        else:
            text_chunks = split_tts_by_punctuation(reply_text)
        if len(text_chunks) > 1:
            logger.info(
                "[TTS] 按标点分 %d 段 device_id=%s req=%s chunks=%s",
                len(text_chunks),
                device_id,
                request_id,
                text_chunks,
            )

    n_scene_pb = 0
    pb_aborted = False
    total_pb = 0
    chunk_is_last = True
    prefetch_tts_task: asyncio.Task | None = prefetch_tts

    async with downlink.pb_serial_chain():
        for chunk_i, chunk_text in enumerate(text_chunks):
            if motion_only:
                segs_local = segs
                sr_pb = int(chat.tts_cfg.get("sample_rate") or 24000)
            else:
                if prefetch_tts_task is None:
                    prefetch_tts_task = asyncio.create_task(
                        chat.tts_phoneme_segments(chunk_text)
                    )
                sr_pb, segs_local = await prefetch_tts_task
                prefetch_tts_task = None
                result.t_tts_synth_end = time.monotonic()
                pcm_ok = any(len(s.get("pcm") or b"") > 0 for s in segs_local)
                if not segs_local or not pcm_ok:
                    raise RuntimeError(f"phoneme TTS 无分片或无 PCM: {chunk_text!r}")
                if chunk_i + 1 < len(text_chunks):
                    prefetch_tts_task = asyncio.create_task(
                        chat.tts_phoneme_segments(text_chunks[chunk_i + 1])
                    )

            chunk_is_first = chunk_i == 0
            chunk_is_last = chunk_i == len(text_chunks) - 1
            pairs, pb_req, n_pb, sr_pb = build_pb_wire_pairs(
                segs_local,
                chat.tts_cfg,
                servo_plan=list(parsed.get("servo") or [])
                if chunk_is_first and not parsed.get("moves")
                else None,
                moves=list(parsed.get("moves") or []) if chunk_is_first else None,
                anims=list(parsed.get("anims") or []) if chunk_is_first else None,
                sample_rate=sr_pb,
                request_id=(
                    f"{request_id}_{chunk_i}" if request_id and len(text_chunks) > 1 else request_id
                ),
                random_servo_cfg=chat.settings.pb_random_servo_cfg() if chunk_is_first else None,
                volume=parsed.get("volume") if chunk_is_first else None,
                cam_fps=parsed.get("cam_fps") if chunk_is_first else None,
                device_id=device_id,
                screen_text=parsed.get("screen_text") if chunk_is_first else None,
                screen_text_color=parsed.get("screen_text_color") if chunk_is_first else None,
                images=list(parsed.get("images") or []) if chunk_is_first else None,
                action=PB_ACTION_REPLACE if chunk_is_first else PB_ACTION_APPEND,
            )
            total_pb += n_pb

            frame_overview = [
                {
                    "i": i,
                    "type": m.get("type"),
                    "idx": m.get("idx"),
                    "chunk_ms": m.get("chunk_ms"),
                    "anim_n": len(m.get("anim") or []),
                    "phonemes": [
                        str(x.get("phoneme"))
                        for x in (m.get("anim") or [])
                        if isinstance(x, dict) and x.get("phoneme")
                    ],
                    "action": m.get("action"),
                    "bin_bytes": sum(len(b) for b in bins),
                }
                for i, (m, bins) in enumerate(pairs)
            ]
            logger.info(
                "[pb TX] 段 %d/%d TTS=%r pb_req=%s segments=%d sr=%s",
                chunk_i + 1,
                len(text_chunks),
                chunk_text,
                pb_req,
                n_pb,
                sr_pb,
            )
            logger.info("[pb TX] 帧序一览 %s", json.dumps(frame_overview, ensure_ascii=False))

            pb_aborted = await _send_pb_pairs(
                downlink,
                pairs=pairs,
                pb_req=pb_req,
                device_id=device_id,
                n_pb=n_pb,
            )
            if pb_aborted:
                if prefetch_tts_task is not None:
                    prefetch_tts_task.cancel()
                break

        if prefetch_tts_task is not None:
            prefetch_tts_task.cancel()

        if not pb_aborted and chunk_is_last:
            for sc_name in llm_scenes:
                if not isinstance(sc_name, str):
                    continue
                sc_key = sc_name.strip()
                if not sc_key or _pb_scene_entry_by_name({}, sc_key, device_id=device_id) is None:
                    if sc_key:
                        logger.warning(
                            "[pb TX] LLM scenes 跳过未知场景 %r device_id=%s req=%s",
                            sc_key,
                            device_id,
                            request_id,
                        )
                    continue
                sreq = uuid.uuid4().hex[:16]
                sframes = _prepare_pb_scene_chain_frames(
                    sc_key, runtime_req=sreq, device_id=device_id
                )
                if not sframes:
                    continue
                for one in sframes:
                    await downlink.send_pb_wire(device_pb_json_msg(one), None)
                    n_scene_pb += 1

    logger.info(
        "[pb TX] 下发结束 device_id=%s request_id=%s 语音 JSON=%d%s%s",
        device_id,
        request_id,
        total_pb,
        "（已中止）" if pb_aborted else "",
        f"；LLM scenes 追加 {n_scene_pb} 条" if n_scene_pb else "",
    )
    result.t_tts_end = time.monotonic()


async def publish_chat_turn(
    events: PipelineEventsPort,
    device_id: Optional[str],
    *,
    source: str,
    asr_text: Optional[str],
    t_asr_start: Optional[float],
    t_asr_text: Optional[float],
    turn: ChatTurnResult,
    request_id: Optional[str] = None,
) -> None:
    if not device_id:
        return
    flow = turn.as_dict()
    t_llm_end = flow.get("t_llm_end")
    t_tts_synth_end = flow.get("t_tts_synth_end")
    t_tts_end = flow.get("t_tts_end")
    end_t = t_tts_end or t_llm_end or t_asr_text
    evt = {
        "device_id": device_id,
        "request_id": request_id,
        "asr_text": asr_text,
        "asr_ms": _ms_between(t_asr_start, t_asr_text) if source == "asr" else None,
        "llm_text": flow.get("llm_text"),
        "llm_raw": flow.get("llm_raw"),
        "moves": list(flow.get("moves") or []),
        "anims": list(flow.get("anims") or []),
        "tools": list(flow.get("tools") or []),
        "tool_results": list(flow.get("tool_results") or []),
        "scenes": list(flow.get("scenes") or []),
        "json_ok": bool(flow.get("json_ok")),
        "need_reply": bool(flow.get("need_reply", True)),
        "voice_auto_reply_off": bool(flow.get("voice_auto_reply_off")),
        "llm_ms": _ms_between(t_asr_text, t_llm_end),
        "tts_text": flow.get("llm_text"),
        "tts_ms": _ms_between(t_llm_end, t_tts_synth_end),
        "pb_ms": _ms_between(t_tts_synth_end, t_tts_end),
        "e2e_ms": _ms_between(t_asr_start, end_t),
        "status": flow.get("status") or "ok",
        "error": flow.get("error"),
        "source": source,
    }
    await events.publish_turn(evt)
    await events.touch_device(device_id, evt["status"])
