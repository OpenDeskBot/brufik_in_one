"""米家 IoT 服务：按设备目录持久化授权与家庭树，供 Web / LLM tool 调用。"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Optional, TypeVar

from deskbot_server.device_data import device_data_dir

logger = logging.getLogger("deskbot-server")

T = TypeVar("T")

_IOTCTL_ROOT = Path(__file__).resolve().parent / "iotctl"
_iotctl_root_s = str(_IOTCTL_ROOT)
# 避免误加载 hardware/miot-ctl 等旧副本（曾含 scene.home_name bug）
for _name in list(sys.modules):
    if _name == "miot_ctl" or _name.startswith("miot_ctl."):
        _mod = sys.modules.get(_name)
        _file = getattr(_mod, "__file__", "") or ""
        if _file and _iotctl_root_s not in _file:
            del sys.modules[_name]
if _iotctl_root_s not in sys.path:
    sys.path.insert(0, _iotctl_root_s)

from miot_ctl.session import MiotSession  # noqa: E402
from miot_ctl.util import (  # noqa: E402
    code_msg,
    load_json,
)

# 错误码 → 给用户的解决建议
_ERROR_HINTS: dict[int, str] = {
    -704042011: "请确认设备已通电并连上家里的 Wi-Fi，可在米家 App 查看是否在线。",
    -704042001: "设备可能已从账号移除，请在「米家」页点击刷新设备列表。",
    -704090001: "设备可能已从账号移除，请在「米家」页点击刷新设备列表。",
    -704040003: "该设备没有这个属性，可先用 miot action=spec 查看可用能力。",
    -704040005: "该设备没有这个动作，可先用 miot action=spec 查看可用能力。",
    -704030013: "该属性不可读。",
    -704030023: "该属性不可写（可能是只读状态）。",
    -704220043: "写入的值不正确，请检查开关用 true/false，亮度用数字。",
    -704053100: "设备当前无法执行此操作，请稍后再试或在米家 App 中确认。",
    -704083036: "操作超时，请确认设备在线后重试。",
    -704012906: "米家授权失效，请到网站「米家」页重新绑定账号。",
    -705201023: "写入失败，请确认设备在线且参数正确。",
    -706012023: "写入失败，请确认设备在线且参数正确。",
    -705201015: "动作执行失败，请确认设备支持该动作且参数正确。",
}

_MAX_PROMPT_DEVICES = 40
_MAX_PROMPT_SCENES = 20


def miot_data_home(device_id: str) -> Path:
    """``data/device/{device_id}/miot/``。"""
    return device_data_dir(device_id) / "miot"


def miot_sdk_available() -> tuple[bool, str]:
    try:
        import miot  # noqa: F401

        return True, ""
    except Exception as exc:  # pragma: no cover
        return False, (
            f"未安装 miloco-miot：{exc}。"
            "请在 service 目录执行："
            "pip install --no-deps src/deskbot_server/iotctl/wheels/miloco_miot-*.whl"
        )


def _fmt_ts(ts: Any) -> str | None:
    if ts is None:
        return None
    try:
        t = int(ts)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def enrich_status(status: dict[str, Any]) -> dict[str, Any]:
    out = dict(status)
    out["last_refreshed_at_fmt"] = _fmt_ts(status.get("last_refreshed_at"))
    out["expires_ts_fmt"] = _fmt_ts(status.get("expires_ts"))
    out["next_refresh_at_fmt"] = _fmt_ts(status.get("next_refresh_at"))
    out["bound_at_fmt"] = _fmt_ts(status.get("bound_at"))
    out["last_synced_at_fmt"] = _fmt_ts(status.get("last_synced_at"))
    expires = status.get("expires_ts")
    if isinstance(expires, (int, float)) and expires > 0:
        remain = int(expires) - int(time.time())
        out["expires_in_sec"] = remain
        out["expires_in_human"] = _human_duration(remain)
    next_r = status.get("next_refresh_at")
    if isinstance(next_r, (int, float)) and next_r > 0:
        out["refresh_in_sec"] = int(next_r) - int(time.time())
    return out


def _human_duration(sec: int) -> str:
    if sec <= 0:
        return "已过期"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 48:
        return f"约 {h // 24} 天"
    if h > 0:
        return f"{h} 小时 {m} 分"
    if m > 0:
        return f"{m} 分 {s} 秒"
    return f"{s} 秒"


def annotate_miot_results(results: list[dict] | dict | Any) -> list[dict] | dict | Any:
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                _annotate_one(item)
        return results
    if isinstance(results, dict):
        _annotate_one(results)
    return results


def parse_auth_payload(payload: str) -> tuple[str, str]:
    """解析授权回调：完整 URL / query / base64 JSON → (code, state)。"""
    import base64
    import binascii
    import json
    from urllib.parse import parse_qs, urlparse

    text = (payload or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("授权信息为空")

    if "code=" in text and "state=" in text:
        query = urlparse(text).query if "://" in text else text.lstrip("?")
        params = parse_qs(query)
        code = (params.get("code") or [""])[0].strip()
        state = (params.get("state") or [""])[0].strip()
        if code and state:
            return code, state

    try:
        decoded = base64.b64decode(text).decode("utf-8")
        data = json.loads(decoded)
        code = str(data["code"]).strip()
        state = str(data["state"]).strip()
        if code and state:
            return code, state
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
    ):
        pass

    raise ValueError(
        "授权信息格式错误。请粘贴浏览器地址栏完整 URL（含 code 与 state）"
    )


def _annotate_one(item: dict) -> None:
    code = item.get("code")
    msg = code_msg(code)
    if msg:
        item["code_msg"] = msg
    if isinstance(code, int) and code in _ERROR_HINTS:
        item["hint"] = _ERROR_HINTS[code]
    elif isinstance(code, int) and code not in (0, -702000000, -702010000) and code < 0:
        item["hint"] = item.get("hint") or "请在米家 App 确认设备状态后重试，或到网站「米家」页刷新列表。"


def error_payload(exc: BaseException, *, need_bind: bool = False) -> dict[str, Any]:
    text = str(exc).strip() or exc.__class__.__name__
    low = text.lower()
    hint = "请稍后重试；若持续失败，可到网站「米家」页查看授权状态并刷新设备。"
    if need_bind or "未绑定" in text or "授权" in text or "auth" in low:
        need_bind = True
        hint = "请打开网站侧栏「米家」，点击绑定小米账号并完成授权。"
    elif "离线" in text:
        hint = "设备当前离线，请通电并连网后再试。"
    elif "state 不匹配" in text:
        hint = "请重新点击「开始绑定」，不要使用旧的回调链接。"
    elif "不存在" in text or "未找到" in text:
        hint = "请确认名称是否正确，或先刷新设备列表；也可在 prompt 中的设备清单里核对。"
    elif "不可写" in text or "不是动作" in text or "spec" in low:
        hint = "可先调用 miot action=spec 查看该设备支持的属性/动作。"
    return {
        "ok": False,
        "error": text,
        "hint": hint,
        "need_bind": need_bind,
        "solution": hint,
    }


def _run(fn: Callable[[MiotSession], Awaitable[T]], *, device_id: str) -> T:
    async def _wrapper() -> T:
        session = MiotSession(home=miot_data_home(device_id))
        try:
            return await fn(session)
        finally:
            await session.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_wrapper())

    # 已在事件循环中（少见）：放到独立线程跑
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _wrapper()).result()


def get_bind_url(device_id: str) -> str:
    return _run(lambda s: s.bind_url(), device_id=device_id)


def authorize_and_sync(device_id: str, code: str, state: str) -> dict[str, Any]:
    async def _do(s: MiotSession) -> dict[str, Any]:
        info = await s.authorize(code, state)
        homes = await s.sync_homes()
        status = enrich_status(await s.status())
        return {
            "ok": True,
            "nickname": getattr(info.user_info, "nickname", None) if info.user_info else None,
            "status": status,
            "homes": homes,
        }

    return _run(_do, device_id=device_id)


def unbind(device_id: str) -> None:
    _run(lambda s: s.unbind(), device_id=device_id)


def get_status(device_id: str, *, refresh: bool = False) -> dict[str, Any]:
    async def _do(s: MiotSession) -> dict[str, Any]:
        if refresh:
            try:
                await s.ensure_fresh_token()
            except Exception as exc:
                st = enrich_status(await s.status())
                st["refresh_error"] = str(exc)
                return st
        return enrich_status(await s.status())

    return _run(_do, device_id=device_id)


def sync_homes(device_id: str) -> dict[str, Any]:
    return _run(lambda s: s.sync_homes(), device_id=device_id)


def load_homes_cache(device_id: str) -> dict[str, Any]:
    path = miot_data_home(device_id) / "homes.json"
    return load_json(path)


def list_devices_cached_or_live(
    device_id: str, *, live: bool = False, online_only: bool = False
) -> list[dict[str, Any]]:
    if live:
        rows = _run(lambda s: s.list_devices(), device_id=device_id)
    else:
        cache = load_homes_cache(device_id)
        rows = list(cache.get("devices") or [])
        if not rows:
            rows = _run(lambda s: s.list_devices(), device_id=device_id)
    if online_only:
        rows = [r for r in rows if r.get("online")]
    return rows


def resolve_device(
    device_id: str,
    *,
    did: str | None = None,
    name: str | None = None,
    room: str | None = None,
) -> dict[str, Any]:
    """按 did 或名称（可加房间）解析设备；歧义时抛错并带候选。"""
    did_s = str(did or "").strip()
    name_s = str(name or "").strip()
    room_s = str(room or "").strip()
    rows = list_devices_cached_or_live(device_id)
    if did_s:
        for r in rows:
            if str(r.get("did")) == did_s:
                return r
        raise ValueError(f"未找到设备 did={did_s}")
    if not name_s:
        raise ValueError("请提供设备 name 或 did")

    def _match(r: dict) -> bool:
        n = str(r.get("name") or "")
        if name_s not in n and n != name_s:
            return False
        if room_s:
            rn = str(r.get("room_name") or "")
            if room_s not in rn and rn != room_s:
                return False
        return True

    exact = [r for r in rows if str(r.get("name") or "") == name_s]
    if room_s:
        exact = [
            r
            for r in exact
            if room_s in str(r.get("room_name") or "")
            or str(r.get("room_name") or "") == room_s
        ]
    if len(exact) == 1:
        return exact[0]

    fuzzy = [r for r in rows if _match(r)]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if not fuzzy and not exact:
        raise ValueError(f"未找到名为「{name_s}」的设备，请先 list 或刷新设备列表")
    cands = exact or fuzzy
    labels = [
        f"{c.get('room_name') or ''}·{c.get('name')}({c.get('did')})" for c in cands[:8]
    ]
    raise ValueError(f"「{name_s}」匹配到多个设备，请加 room 或改用 did：{', '.join(labels)}")


def resolve_scene(
    device_id: str, *, scene_id: str | None = None, scene_name: str | None = None
) -> dict[str, Any]:
    sid = str(scene_id or "").strip()
    sname = str(scene_name or "").strip()
    cache = load_homes_cache(device_id)
    scenes = list(cache.get("scenes") or [])
    if not scenes:
        scenes = _run(lambda s: s.list_scenes(), device_id=device_id)
    if sid:
        for sc in scenes:
            if str(sc.get("scene_id")) == sid:
                return sc
        raise ValueError(f"场景不存在: {sid}")
    if not sname:
        raise ValueError("请提供 scene_name 或 scene_id")
    exact = [s for s in scenes if str(s.get("scene_name") or "") == sname]
    if len(exact) == 1:
        return exact[0]
    fuzzy = [s for s in scenes if sname in str(s.get("scene_name") or "")]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if not fuzzy:
        raise ValueError(f"未找到场景「{sname}」")
    labels = [f"{s.get('scene_name')}({s.get('scene_id')})" for s in fuzzy[:8]]
    raise ValueError(f"场景名歧义，请改用 scene_id：{', '.join(labels)}")


def llm_miot_prompt_appendix(device_id: Optional[str] = None) -> str:
    """注入 system prompt：绑定状态 + 家庭/房间/设备摘要。"""
    did = str(device_id or "").strip()
    if not did:
        return ""
    ok_sdk, sdk_err = miot_sdk_available()
    if not ok_sdk:
        return f"米家智能家居：不可用（{sdk_err}）"

    home = miot_data_home(did)
    if not (home / "auth.json").is_file():
        return (
            "米家智能家居：未绑定。"
            "用户若要控制米家设备，请提示到网站「米家」页完成小米账号授权；"
            "此时不要调用 miot 工具（除非 action=status）。"
        )

    status = enrich_status(
        {
            **load_json(home / "meta.json"),
            "bound": True,
            "token_valid": True,
        }
    )
    # 用缓存的 meta；若缺 expires 再读 auth
    try:
        st = get_status(did, refresh=False)
        status = st
    except Exception:
        pass

    cache = load_homes_cache(did)
    lines: list[str] = [
        "米家智能家居（可用 ``miot`` 工具控制；优先用名称，歧义时加 room 或 did）：",
        f"  账号: {status.get('nickname') or '已绑定'}"
        f"；token{'有效' if status.get('token_valid') else '可能过期（将自动续期）'}"
        f"；设备缓存 {cache.get('device_count') or len(cache.get('devices') or [])} 台"
        f"；场景 {cache.get('scene_count') or len(cache.get('scenes') or [])} 个。",
    ]
    if status.get("last_refreshed_at_fmt"):
        lines.append(
            f"  授权续期: 上次 {status.get('last_refreshed_at_fmt')}"
            f"，有效至 {status.get('expires_ts_fmt')}"
            f"，下次续期约 {status.get('next_refresh_at_fmt')}"
            f"（剩余 {status.get('expires_in_human') or '?'}）。"
        )

    devices = list(cache.get("devices") or [])
    if not devices and cache.get("homes"):
        for h in cache["homes"]:
            for room in h.get("rooms") or []:
                for d in room.get("devices") or []:
                    devices.append(
                        {
                            **d,
                            "home_name": h.get("home_name"),
                            "room_name": room.get("room_name"),
                        }
                    )

    # 在线优先，截断
    devices_sorted = sorted(
        devices,
        key=lambda d: (0 if d.get("online") else 1, d.get("home_name") or "", d.get("room_name") or "", d.get("name") or ""),
    )
    shown = devices_sorted[:_MAX_PROMPT_DEVICES]
    by_room: dict[str, list[str]] = {}
    for d in shown:
        room = f"{d.get('home_name') or ''}/{d.get('room_name') or '未分配'}".strip("/")
        mark = "✓" if d.get("online") else "✗"
        by_room.setdefault(room or "未分配", []).append(
            f"{d.get('name')}[{d.get('did')}]{mark}"
        )
    if by_room:
        lines.append("  设备（✓在线 ✗离线）：")
        for room, items in list(by_room.items())[:30]:
            lines.append(f"    · {room}: " + "；".join(items))
        if len(devices_sorted) > len(shown):
            lines.append(f"    …另有 {len(devices_sorted) - len(shown)} 台未列出，可用 miot action=list 查看全部。")
    else:
        lines.append("  设备列表为空，请先在网站刷新，或调用 miot action=sync。")

    scenes = list(cache.get("scenes") or [])[:_MAX_PROMPT_SCENES]
    if scenes:
        names = "、".join(
            f"{s.get('scene_name')}({s.get('scene_id')})" for s in scenes
        )
        lines.append(f"  手动场景: {names}")

    lines.append(
        "  控制示例: {\"tool\":\"miot\",\"action\":\"set\",\"name\":\"台灯\",\"key\":\"on\",\"value\":true}"
    )
    return "\n".join(lines)
