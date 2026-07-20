"""LLM ``miot`` 工具：查看/控制米家设备与场景。"""

from __future__ import annotations

import logging
from typing import Any

from deskbot_server import miot_service as ms

logger = logging.getLogger("deskbot-server")

# miot_service 已把 iotctl 加入 sys.path
from miot_ctl.util import MIOT_OK_CODES, infer_value  # noqa: E402


def execute_miot_tool(raw: dict[str, Any], *, device_id: str) -> dict[str, Any]:
    """执行 LLM miot 工具调用。"""
    tool = "miot"
    ok_sdk, sdk_err = ms.miot_sdk_available()
    if not ok_sdk:
        return {"tool": tool, **ms.error_payload(RuntimeError(sdk_err), need_bind=False)}

    action = str(raw.get("action") or raw.get("op") or "list").strip().lower()
    try:
        if action in ("status", "auth_status"):
            st = ms.get_status(device_id, refresh=False)
            if st.get("bound"):
                st = ms.get_status(device_id, refresh=True)
            return {"tool": tool, "action": action, "ok": True, **st}

        if action in ("sync", "refresh", "sync_homes"):
            homes = ms.sync_homes(device_id)
            return {
                "tool": tool,
                "action": action,
                "ok": True,
                "device_count": homes.get("device_count"),
                "scene_count": homes.get("scene_count"),
                "synced_at": homes.get("synced_at"),
                "homes": homes.get("homes"),
            }

        if action in ("list", "devices"):
            online_only = bool(raw.get("online") or raw.get("online_only"))
            live = bool(raw.get("live") or raw.get("force_live"))
            rows = ms.list_devices_cached_or_live(
                device_id, live=live, online_only=online_only
            )
            return {
                "tool": tool,
                "action": action,
                "ok": True,
                "count": len(rows),
                "devices": rows,
            }

        if action in ("scenes", "list_scenes"):
            cache = ms.load_homes_cache(device_id)
            scenes = list(cache.get("scenes") or [])
            if not scenes or raw.get("live"):
                scenes = ms._run(lambda s: s.list_scenes(), device_id=device_id)
            return {
                "tool": tool,
                "action": action,
                "ok": True,
                "count": len(scenes),
                "scenes": scenes,
            }

        if action in ("get", "device"):
            dev = ms.resolve_device(
                device_id,
                did=str(raw.get("did") or ""),
                name=str(raw.get("name") or raw.get("device") or ""),
                room=str(raw.get("room") or raw.get("room_name") or ""),
            )
            detail = ms._run(lambda s: s.get_device(dev["did"]), device_id=device_id)
            return {"tool": tool, "action": action, "ok": True, "device": detail}

        if action == "spec":
            dev = ms.resolve_device(
                device_id,
                did=str(raw.get("did") or ""),
                name=str(raw.get("name") or raw.get("device") or ""),
                room=str(raw.get("room") or raw.get("room_name") or ""),
            )
            spec = ms._run(lambda s: s.get_spec(dev["did"]), device_id=device_id)
            compact = []
            for item in spec.values():
                compact.append(
                    {
                        "type_name": item.get("type_name"),
                        "api_iid": item.get("api_iid"),
                        "rw": ("r" if item.get("readable") else "")
                        + ("w" if item.get("writable") else ""),
                        "description": item.get("description"),
                    }
                )
            return {
                "tool": tool,
                "action": action,
                "ok": True,
                "did": dev["did"],
                "name": dev.get("name"),
                "spec": compact,
            }

        if action in ("props", "get_props", "properties"):
            dev = ms.resolve_device(
                device_id,
                did=str(raw.get("did") or ""),
                name=str(raw.get("name") or raw.get("device") or ""),
                room=str(raw.get("room") or raw.get("room_name") or ""),
            )
            keys_raw = raw.get("keys") or raw.get("key")
            keys: list[str] | None
            if keys_raw is None or keys_raw == "":
                keys = None
            elif isinstance(keys_raw, list):
                keys = [str(k) for k in keys_raw]
            else:
                keys = [str(keys_raw)]
            results = ms._run(
                lambda s: s.get_properties(dev["did"], keys), device_id=device_id
            )
            ms.annotate_miot_results(results)
            failed = _first_failure(results)
            out: dict[str, Any] = {
                "tool": tool,
                "action": action,
                "ok": failed is None,
                "did": dev["did"],
                "name": dev.get("name"),
                "properties": results,
            }
            if failed:
                out.update(_failure_fields(failed))
            return out

        if action in ("set", "set_prop", "write"):
            dev = ms.resolve_device(
                device_id,
                did=str(raw.get("did") or ""),
                name=str(raw.get("name") or raw.get("device") or ""),
                room=str(raw.get("room") or raw.get("room_name") or ""),
            )
            key = str(raw.get("key") or raw.get("prop") or raw.get("property") or "").strip()
            if not key:
                raise ValueError("set 需要 key（如 on、brightness）")
            value = raw.get("value")
            if isinstance(value, str):
                value = infer_value(value)
            results = ms._run(
                lambda s: s.set_property(dev["did"], key, value), device_id=device_id
            )
            ms.annotate_miot_results(results)
            failed = _first_failure(results)
            out = {
                "tool": tool,
                "action": action,
                "ok": failed is None,
                "did": dev["did"],
                "name": dev.get("name"),
                "key": key,
                "value": value,
                "results": results,
            }
            if failed:
                out.update(_failure_fields(failed))
            return out

        if action in ("action", "call", "call_action"):
            dev = ms.resolve_device(
                device_id,
                did=str(raw.get("did") or ""),
                name=str(raw.get("name") or raw.get("device") or ""),
                room=str(raw.get("room") or raw.get("room_name") or ""),
            )
            key = str(raw.get("key") or raw.get("action_key") or "").strip()
            if not key:
                raise ValueError("action 需要 key（如 play-text）")
            args_raw = raw.get("args") or raw.get("in") or []
            if not isinstance(args_raw, list):
                args_raw = [args_raw]
            args = [infer_value(a) if isinstance(a, str) else a for a in args_raw]
            result = ms._run(
                lambda s: s.call_action(dev["did"], key, args), device_id=device_id
            )
            ms.annotate_miot_results(result)
            failed = _first_failure(result if isinstance(result, list) else [result])
            out = {
                "tool": tool,
                "action": action,
                "ok": failed is None,
                "did": dev["did"],
                "name": dev.get("name"),
                "key": key,
                "args": args,
                "result": result,
            }
            if failed:
                out.update(_failure_fields(failed))
            return out

        if action in ("run_scene", "scene_run", "scene"):
            sc = ms.resolve_scene(
                device_id,
                scene_id=str(raw.get("scene_id") or ""),
                scene_name=str(raw.get("scene_name") or raw.get("name") or ""),
            )
            result = ms._run(
                lambda s: s.run_scene(str(sc["scene_id"])), device_id=device_id
            )
            return {
                "tool": tool,
                "action": action,
                "ok": True,
                "scene_id": sc.get("scene_id"),
                "scene_name": sc.get("scene_name"),
                "result": result,
            }

        raise ValueError(
            f"未知 miot action: {action}。"
            "支持: status/sync/list/scenes/get/spec/props/set/action/run_scene"
        )
    except Exception as exc:
        logger.warning("[miot tool] action=%s 失败: %s", action, exc)
        need_bind = "未绑定" in str(exc) or "授权" in str(exc)
        return {
            "tool": tool,
            "action": action,
            **ms.error_payload(exc, need_bind=need_bind),
        }


def _first_failure(results: list | dict | Any) -> dict | None:
    items = results if isinstance(results, list) else [results]
    for item in items:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if isinstance(code, int) and code < 0 and code not in MIOT_OK_CODES:
            return item
    return None


def _failure_fields(failed: dict) -> dict[str, Any]:
    return {
        "error": failed.get("code_msg") or f"设备返回错误码 {failed.get('code')}",
        "hint": failed.get("hint")
        or "请确认设备在线与参数正确；必要时到网站「米家」页刷新列表。",
        "solution": failed.get("hint")
        or "请确认设备在线与参数正确；必要时到网站「米家」页刷新列表。",
        "code": failed.get("code"),
    }
