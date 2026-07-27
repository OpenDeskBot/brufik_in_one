"""网页 ROM 烧录：页面与 REST API。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from deskbot_server.infrastructure.flash.rom_flash import (
    flash_manager,
    list_roms,
    list_serial_ports,
    resolve_rom_path,
    validate_port,
    validate_rom_id,
)
from deskbot_server.web.deps import RequireUser
from deskbot_server.web.view_helpers import ViewAPIRoute, args_get, get_json, jsonify, render_template

router = APIRouter(route_class=ViewAPIRoute, tags=["flash"])


@router.get("/flash")
def flash_page(request: Request, user: RequireUser):
    return render_template(request, "app2c/flash_rom.html", active_nav="flash")


@router.get("/api/flash/ports")
def api_flash_ports(request: Request, user: RequireUser):
    return jsonify({"ok": True, "ports": list_serial_ports()})


@router.get("/api/flash/roms")
def api_flash_roms(request: Request, user: RequireUser):
    return jsonify({"ok": True, "roms": [r.to_dict() for r in list_roms()]})


@router.get("/api/flash/roms/{rom_id}/download")
def api_flash_rom_download(request: Request, rom_id: str, user: RequireUser):
    try:
        path = resolve_rom_path(validate_rom_id(rom_id))
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except (PermissionError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    from fastapi.responses import FileResponse

    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.get("/api/flash/status")
def api_flash_status(request: Request, user: RequireUser):
    since = args_get(request, "since", 0, type=int)
    return jsonify({"ok": True, **flash_manager.status(), "log": flash_manager.log_snapshot(since=since)})


@router.post("/api/flash/build")
def api_flash_build(request: Request, user: RequireUser):
    try:
        job = flash_manager.start_build()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "job": job.to_dict()})


@router.post("/api/flash/upload")
def api_flash_upload(request: Request, user: RequireUser):
    body = get_json(request, silent=True) or {}
    port = (body.get("port") or "").strip()
    rom_id = (body.get("rom_id") or "source").strip()
    try:
        job = flash_manager.start_upload(port, rom_id)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "job": job.to_dict()})


@router.post("/api/flash/cancel")
def api_flash_cancel(request: Request, user: RequireUser):
    cancelled = flash_manager.cancel()
    return jsonify({"ok": True, "cancelled": cancelled})


@router.post("/api/flash/monitor/start")
def api_flash_monitor_start(request: Request, user: RequireUser):
    body = get_json(request, silent=True) or {}
    port = (body.get("port") or "").strip()
    try:
        port = validate_port(port)
        flash_manager.cancel()
        flash_manager.free_serial_port(port)
        flash_manager.serial.start(port)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "port": port})


@router.post("/api/flash/monitor/stop")
def api_flash_monitor_stop(request: Request, user: RequireUser):
    flash_manager.serial.stop()
    return jsonify({"ok": True})


@router.post("/api/flash/monitor/send")
def api_flash_monitor_send(request: Request, user: RequireUser):
    body = get_json(request, silent=True) or {}
    text = body.get("text")
    if text is None or str(text).strip() == "":
        return jsonify({"ok": False, "error": "text 不能为空"}), 400
    try:
        flash_manager.serial.write(str(text))
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


ENDPOINTS = {
    "flash.flash_page": "/flash",
    "flash.api_flash_ports": "/api/flash/ports",
    "flash.api_flash_roms": "/api/flash/roms",
    "flash.api_flash_rom_download": "/api/flash/roms/{rom_id}/download",
    "flash.api_flash_status": "/api/flash/status",
    "flash.api_flash_build": "/api/flash/build",
    "flash.api_flash_upload": "/api/flash/upload",
    "flash.api_flash_cancel": "/api/flash/cancel",
    "flash.api_flash_monitor_start": "/api/flash/monitor/start",
    "flash.api_flash_monitor_stop": "/api/flash/monitor/stop",
    "flash.api_flash_monitor_send": "/api/flash/monitor/send",
}
