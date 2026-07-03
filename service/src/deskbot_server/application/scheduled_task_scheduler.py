"""定时任务调度：到期后调用 LLM 并将结果下发到设备。"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Optional

from deskbot_server.application.chat_flow import (
    _voice_was_played,
    publish_chat_turn,
    run_chat_turn,
)
from deskbot_server.infrastructure.ws.downlink_adapter import (
    WsDownlinkAdapter,
    WsPipelineEventsAdapter,
)
from deskbot_server.scheduled_task_service import (
    claim_due_tasks,
    expire_overdue_active_tasks,
    finish_scheduled_task,
)

if TYPE_CHECKING:
    from deskbot_server.application.chat_service import ChatService
    from deskbot_server.ws.asr_chat_hub import AsrChatHub
    from deskbot_server.ws.device_pipeline import DevicePipelineBroker
    from deskbot_server.ws.registry import DeviceRegistry

logger = logging.getLogger("deskbot-server")


class ScheduledTaskScheduler:
    def __init__(
        self,
        *,
        chat: "ChatService",
        asr_chat_hub: "AsrChatHub",
        registry: "DeviceRegistry",
        dp_broker: "DevicePipelineBroker",
        poll_interval_sec: float = 60.0,
        lookback_minutes: float = 5.0,
    ) -> None:
        self._chat = chat
        self._hub = asr_chat_hub
        self._registry = registry
        self._broker = dp_broker
        self._poll_interval = max(30.0, float(poll_interval_sec))
        self._lookback_minutes = max(1.0, float(lookback_minutes))
        self._task: Optional[asyncio.Task] = None
        self._running_ids: set[str] = set()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="scheduled_task_scheduler")
        logger.info(
            "[scheduler] 定时任务调度已启动 poll_interval=%.1fs lookback=%.1fmin",
            self._poll_interval,
            self._lookback_minutes,
        )

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[scheduler] tick 异常")
            await asyncio.sleep(self._poll_interval)

    async def _tick(self) -> None:
        expired = expire_overdue_active_tasks(lookback_minutes=self._lookback_minutes)
        if expired:
            logger.info("[scheduler] 已标记超时未执行任务数=%d", expired)
        due = claim_due_tasks(limit=10, lookback_minutes=self._lookback_minutes)
        for item in due:
            tid = str(item.get("id") or "")
            if not tid or tid in self._running_ids:
                continue
            self._running_ids.add(tid)
            asyncio.create_task(self._run_one(item), name=f"scheduled_task_{tid[:8]}")

    async def _run_one(self, item: dict) -> None:
        tid = str(item.get("id") or "")
        device_id = str(item.get("device_id") or "").strip()
        description = str(item.get("description") or "").strip()
        try:
            ws = await self._hub.first_ws(device_id)
            if ws is None:
                finish_scheduled_task(
                    tid,
                    ok=False,
                    summary="设备未连接 /asr_chat",
                )
                logger.warning(
                    "[scheduler] 任务失败 device 离线 task_id=%s device_id=%s",
                    tid,
                    device_id,
                )
                return

            req_id = uuid.uuid4().hex[:16]
            user_text = (
                f"[系统定时任务] 请向主人朗声提醒并执行以下任务：{description}\n"
                "要求：need_reply 必须为 true，tts 写直接说给主人听的提醒语，禁止写「已发送」等汇报语。"
            )
            logger.info(
                "[scheduler] 开始执行 task_id=%s device_id=%s session_id=%s desc=%r req=%s",
                tid,
                device_id,
                item.get("session_id"),
                description,
                req_id,
            )
            downlink = WsDownlinkAdapter(
                ws,
                settings=self._chat.settings,
                device_id=device_id,
                dp_broker=self._broker,
            )
            events = WsPipelineEventsAdapter(self._broker, self._registry)
            t0 = asyncio.get_event_loop().time()
            task_session_id = str(item.get("session_id") or "").strip() or None
            turn = await run_chat_turn(
                downlink,
                self._chat,
                user_text,
                request_id=req_id,
                device_id=device_id,
                registry=self._registry,
                t_asr_text=t0,
                force_voice=True,
                reuse_session_id=task_session_id,
            )
            await publish_chat_turn(
                events,
                device_id,
                source="scheduled_task",
                asr_text=user_text,
                t_asr_start=t0,
                t_asr_text=t0,
                turn=turn,
                request_id=req_id,
            )
            voice_ok = _voice_was_played(turn)
            ok = (turn.status or "ok") == "ok" and not turn.error and voice_ok
            if (turn.status or "ok") == "ok" and not turn.error and not voice_ok:
                summary = "LLM 已响应但未下发语音提醒"
                logger.warning(
                    "[scheduler] 任务未播报 task_id=%s device_id=%s need_reply=%s llm_text=%r",
                    tid,
                    device_id,
                    turn.need_reply,
                    (turn.llm_text or "")[:120],
                )
            else:
                summary = (turn.llm_text or "")[:500] if ok else (turn.error or "执行失败")
            finish_scheduled_task(tid, ok=ok, summary=summary)
            logger.info(
                "[scheduler] 任务完成 task_id=%s device_id=%s ok=%s voice_ok=%s summary=%r",
                tid,
                device_id,
                ok,
                voice_ok,
                (summary or "")[:120],
            )
        except Exception as exc:
            finish_scheduled_task(tid, ok=False, summary=str(exc))
            logger.exception(
                "[scheduler] 任务异常 task_id=%s device_id=%s",
                tid,
                device_id,
            )
        finally:
            self._running_ids.discard(tid)
