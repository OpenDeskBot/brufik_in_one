"""封装 OAuth + 云 API + spec 解析。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from miot.cloud import MIoTHttpClient, MIoTOAuth2Client
from miot.const import SYSTEM_LANGUAGE_DEFAULT
from miot.spec import MIoTSpecParser
from miot.storage import MIoTStorage
from miot.types import (
    MIoTActionParam,
    MIoTGetPropertyParam,
    MIoTOauthInfo,
    MIoTSetPropertyParam,
)

from miot_ctl.util import (
    TOKEN_REFRESH_SKEW_SEC,
    api_iid_to_parts,
    auth_path,
    config_path,
    data_dir,
    homes_path,
    lite_iid_to_api,
    load_json,
    meta_path,
    resolve_spec_key,
    save_json,
)


class MiotSession:
    def __init__(self, home: Path | str | None = None) -> None:
        self._home = data_dir(home)
        cfg = load_json(config_path(self._home))
        if "uuid" not in cfg:
            cfg["uuid"] = uuid.uuid4().hex
            save_json(config_path(self._home), cfg)
        self.uuid = cfg["uuid"]
        self.cloud_server = cfg.get("cloud_server", "cn")
        self.redirect_uri = cfg.get(
            "redirect_uri", "https://mico.api.mijia.tech/login_redirect"
        )
        # 旧默认 https://127.0.0.1 会落到无法打开的页面；迁移到官方回调展示页
        if self.redirect_uri.rstrip("/") in ("https://127.0.0.1", "http://127.0.0.1"):
            self.redirect_uri = "https://mico.api.mijia.tech/login_redirect"
            cfg["redirect_uri"] = self.redirect_uri
            save_json(config_path(self._home), cfg)
        self.lang = cfg.get("lang", SYSTEM_LANGUAGE_DEFAULT)

        self._oauth: MIoTOAuth2Client | None = None
        self._http: MIoTHttpClient | None = None
        self._spec: MIoTSpecParser | None = None
        self._auth: MIoTOauthInfo | None = None

    @property
    def home(self) -> Path:
        return self._home

    async def _ensure_clients(self) -> None:
        if self._oauth is None:
            self._oauth = MIoTOAuth2Client(
                redirect_uri=self.redirect_uri,
                cloud_server=self.cloud_server,
                uuid=self.uuid,
            )
        if self._http is None:
            token = self._auth.access_token if self._auth else ""
            self._http = MIoTHttpClient(
                cloud_server=self.cloud_server,
                access_token=token,
            )

    async def _ensure_spec(self) -> MIoTSpecParser:
        await self._ensure_clients()
        if self._spec is None:
            storage = MIoTStorage(str(self._home / "cache"))
            self._spec = MIoTSpecParser(storage=storage, lang=self.lang)
        return self._spec

    def _load_auth(self) -> MIoTOauthInfo | None:
        raw = load_json(auth_path(self._home))
        if not raw.get("access_token") or not raw.get("refresh_token"):
            return None
        # 忽略我们额外写入的字段，只取 OAuth 模型需要的键
        payload = {
            k: raw[k]
            for k in ("access_token", "refresh_token", "expires_ts", "user_info")
            if k in raw
        }
        return MIoTOauthInfo(**payload)

    def _save_auth(self, info: MIoTOauthInfo, *, refreshed: bool = False) -> None:
        save_json(auth_path(self._home), info.model_dump())
        meta = load_json(meta_path(self._home))
        now = int(time.time())
        if refreshed or not meta.get("last_refreshed_at"):
            meta["last_refreshed_at"] = now
        meta["expires_ts"] = int(info.expires_ts)
        meta["next_refresh_at"] = int(info.expires_ts) - TOKEN_REFRESH_SKEW_SEC
        if info.user_info and getattr(info.user_info, "nickname", None):
            meta["nickname"] = info.user_info.nickname
        if not meta.get("bound_at"):
            meta["bound_at"] = now
        save_json(meta_path(self._home), meta)

    def _clear_meta_bound(self) -> None:
        path = meta_path(self._home)
        if path.is_file():
            path.unlink()
        homes = homes_path(self._home)
        if homes.is_file():
            homes.unlink()

    async def _ensure_auth(self) -> MIoTOauthInfo:
        await self._ensure_clients()
        assert self._oauth and self._http

        if self._auth is None:
            self._auth = self._load_auth()

        if self._auth and self._auth.expires_ts > int(time.time()) + TOKEN_REFRESH_SKEW_SEC:
            self._http.update_http_header(access_token=self._auth.access_token)
            return self._auth

        if self._auth and self._auth.refresh_token:
            self._auth = await self._oauth.refresh_access_token_async(
                self._auth.refresh_token
            )
            self._http.update_http_header(access_token=self._auth.access_token)
            self._save_auth(self._auth, refreshed=True)
            return self._auth

        raise RuntimeError("未绑定小米账号，请先在网站「米家」页完成授权")

    async def bind_url(self) -> str:
        await self._ensure_clients()
        assert self._oauth
        return self._oauth.gen_auth_url()

    async def authorize(self, code: str, state: str) -> MIoTOauthInfo:
        await self._ensure_clients()
        assert self._oauth and self._http
        if not await self._oauth.check_state_async(state):
            raise ValueError("state 不匹配，请重新开始绑定后再授权")
        self._auth = await self._oauth.get_access_token_async(code)
        self._http.update_http_header(access_token=self._auth.access_token)
        try:
            self._auth.user_info = await self._http.get_user_info_async()
        except Exception:
            pass
        self._save_auth(self._auth, refreshed=True)
        return self._auth

    async def unbind(self) -> None:
        self._auth = None
        if auth_path(self._home).is_file():
            auth_path(self._home).unlink()
        self._clear_meta_bound()

    async def status(self) -> dict[str, Any]:
        info = self._load_auth()
        meta = load_json(meta_path(self._home))
        if not info:
            return {
                "bound": False,
                "token_valid": False,
                "last_refreshed_at": meta.get("last_refreshed_at"),
                "expires_ts": meta.get("expires_ts"),
                "next_refresh_at": meta.get("next_refresh_at"),
                "nickname": meta.get("nickname"),
            }
        now = int(time.time())
        valid = info.expires_ts > now
        last_refreshed = meta.get("last_refreshed_at")
        expires_ts = int(info.expires_ts)
        next_refresh = int(meta.get("next_refresh_at") or (expires_ts - TOKEN_REFRESH_SKEW_SEC))
        nickname = meta.get("nickname")
        if not nickname and info.user_info:
            nickname = getattr(info.user_info, "nickname", None)
        return {
            "bound": True,
            "token_valid": valid,
            "expires_ts": expires_ts,
            "last_refreshed_at": last_refreshed,
            "next_refresh_at": next_refresh,
            "nickname": nickname,
            "bound_at": meta.get("bound_at"),
            "last_synced_at": meta.get("last_synced_at"),
        }

    async def ensure_fresh_token(self) -> dict[str, Any]:
        """主动触发一次鉴权/续期，返回最新 status。"""
        await self._ensure_auth()
        return await self.status()

    async def list_devices(self) -> list[dict[str, Any]]:
        await self._ensure_auth()
        assert self._http
        devices = await self._http.get_devices_async()
        rows = []
        for did, dev in devices.items():
            rows.append(
                {
                    "did": did,
                    "name": dev.name,
                    "model": dev.model,
                    "online": dev.online,
                    "home_name": dev.home_name,
                    "room_name": dev.room_name,
                }
            )
        rows.sort(
            key=lambda x: (x.get("home_name") or "", x.get("room_name") or "", x["name"])
        )
        return rows

    async def get_device(self, did: str) -> dict[str, Any]:
        await self._ensure_auth()
        assert self._http
        devices = await self._http.get_devices_async()
        if did not in devices:
            raise ValueError(f"设备不存在: {did}")
        return devices[did].model_dump()

    async def get_spec(self, did: str) -> dict[str, Any]:
        await self._ensure_auth()
        assert self._http
        spec = await self._ensure_spec()
        devices = await self._http.get_devices_async()
        if did not in devices:
            raise ValueError(f"设备不存在: {did}")
        dev = devices[did]
        lite = await spec.parse_lite_async(urn=dev.urn)
        if not lite:
            raise RuntimeError(f"无法解析设备 spec: {dev.urn}")
        out = {}
        for key, item in lite.items():
            out[key] = {
                "api_iid": lite_iid_to_api(item.iid),
                "description": item.description,
                "type_name": item.type_name,
                "format": item.format,
                "readable": item.readable,
                "writable": item.writeable,
            }
        return out

    async def set_property(self, did: str, key: str, value: Any) -> list[dict]:
        await self._ensure_auth()
        assert self._http
        spec = await self._ensure_spec()
        devices = await self._http.get_devices_async()
        if did not in devices:
            raise ValueError(f"设备不存在: {did}")
        lite = await spec.parse_lite_async(urn=devices[did].urn)
        if not lite:
            raise RuntimeError("无法解析设备 spec")

        item = resolve_spec_key(lite, key, writable=True)
        if item.iid.startswith("action."):
            raise ValueError(f"'{key}' 是动作，请用 action 调用")
        _, siid, piid = api_iid_to_parts(lite_iid_to_api(item.iid))
        params = [MIoTSetPropertyParam(did=did, siid=siid, piid=piid, value=value)]
        return await self._http.set_props_async(params)

    async def get_properties(self, did: str, keys: list[str] | None = None) -> list[dict]:
        await self._ensure_auth()
        assert self._http
        spec = await self._ensure_spec()
        devices = await self._http.get_devices_async()
        if did not in devices:
            raise ValueError(f"设备不存在: {did}")
        lite = await spec.parse_lite_async(urn=devices[did].urn)
        if not lite:
            raise RuntimeError("无法解析设备 spec")

        params: list[MIoTGetPropertyParam] = []
        if not keys:
            for item in lite.values():
                if not item.readable or not item.iid.startswith("prop."):
                    continue
                _, siid, piid = api_iid_to_parts(lite_iid_to_api(item.iid))
                params.append(MIoTGetPropertyParam(did=did, siid=siid, piid=piid))
        else:
            for key in keys:
                item = resolve_spec_key(lite, key, writable=None)
                if not item.readable:
                    raise ValueError(f"'{key}' 不可读")
                _, siid, piid = api_iid_to_parts(lite_iid_to_api(item.iid))
                params.append(MIoTGetPropertyParam(did=did, siid=siid, piid=piid))

        if not params:
            return []
        return await self._http.get_props_async(params)

    async def call_action(self, did: str, key: str, args: list[Any]) -> dict:
        await self._ensure_auth()
        assert self._http
        spec = await self._ensure_spec()
        devices = await self._http.get_devices_async()
        if did not in devices:
            raise ValueError(f"设备不存在: {did}")
        lite = await spec.parse_lite_async(urn=devices[did].urn)
        if not lite:
            raise RuntimeError("无法解析设备 spec")

        item = resolve_spec_key(lite, key, writable=True)
        if not item.iid.startswith("action."):
            raise ValueError(f"'{key}' 不是动作")
        _, siid, aiid = api_iid_to_parts(lite_iid_to_api(item.iid))
        param = MIoTActionParam(did=did, siid=siid, aiid=aiid, in_=args)
        return await self._http.action_async(param)

    async def list_scenes(self) -> list[dict[str, Any]]:
        await self._ensure_auth()
        assert self._http
        scenes = await self._http.get_manual_scenes_async()
        # 用设备列表推断 home_id/room_id → 名称（场景对象本身只有 id）
        home_names: dict[str, str] = {}
        room_names: dict[str, str] = {}
        try:
            devices = await self._http.get_devices_async()
            for dev in devices.values():
                hid = str(getattr(dev, "home_id", None) or "")
                rid = str(getattr(dev, "room_id", None) or "")
                if hid and getattr(dev, "home_name", None):
                    home_names.setdefault(hid, str(dev.home_name))
                if rid and getattr(dev, "room_name", None):
                    room_names.setdefault(rid, str(dev.room_name))
        except Exception:
            pass
        rows = []
        for sid, scene in scenes.items():
            hid = str(getattr(scene, "home_id", None) or "")
            rid = str(getattr(scene, "room_id", None) or "")
            rows.append(
                {
                    "scene_id": scene.scene_id,
                    "scene_name": scene.scene_name,
                    "home_id": hid or None,
                    "room_id": rid or None,
                    "home_name": home_names.get(hid) or None,
                    "room_name": room_names.get(rid) or None,
                }
            )
        rows.sort(key=lambda x: (x.get("home_name") or "", x["scene_name"]))
        return rows

    async def run_scene(self, scene_id: str) -> Any:
        await self._ensure_auth()
        assert self._http
        scenes = await self._http.get_manual_scenes_async()
        if scene_id not in scenes:
            raise ValueError(f"场景不存在: {scene_id}")
        return await self._http.run_manual_scene_async(scenes[scene_id])

    async def sync_homes(self) -> dict[str, Any]:
        """拉取家庭-房间-设备与场景，写入 homes.json。"""
        devices = await self.list_devices()
        try:
            scenes = await self.list_scenes()
        except Exception:
            # 场景字段因 SDK 版本差异可能失败；设备列表仍应保存
            scenes = []
        homes: dict[str, dict[str, Any]] = {}
        for d in devices:
            home_name = str(d.get("home_name") or "默认家庭").strip() or "默认家庭"
            room_name = str(d.get("room_name") or "未分配房间").strip() or "未分配房间"
            home = homes.setdefault(
                home_name,
                {"home_name": home_name, "rooms": {}},
            )
            room = home["rooms"].setdefault(
                room_name,
                {"room_name": room_name, "devices": []},
            )
            room["devices"].append(
                {
                    "did": d["did"],
                    "name": d["name"],
                    "model": d.get("model") or "",
                    "online": bool(d.get("online")),
                }
            )

        home_list = []
        for home in homes.values():
            rooms = list(home["rooms"].values())
            rooms.sort(key=lambda r: r["room_name"])
            for room in rooms:
                room["devices"].sort(key=lambda x: x["name"])
            home_list.append({"home_name": home["home_name"], "rooms": rooms})
        home_list.sort(key=lambda h: h["home_name"])

        payload = {
            "synced_at": int(time.time()),
            "device_count": len(devices),
            "scene_count": len(scenes),
            "homes": home_list,
            "scenes": scenes,
            "devices": devices,
        }
        save_json(homes_path(self._home), payload)
        meta = load_json(meta_path(self._home))
        meta["last_synced_at"] = payload["synced_at"]
        meta["device_count"] = payload["device_count"]
        meta["scene_count"] = payload["scene_count"]
        save_json(meta_path(self._home), meta)
        return payload

    def load_homes_cache(self) -> dict[str, Any]:
        return load_json(homes_path(self._home))

    async def close(self) -> None:
        if self._oauth:
            await self._oauth.deinit_async()
        if self._http:
            await self._http.deinit_async()
