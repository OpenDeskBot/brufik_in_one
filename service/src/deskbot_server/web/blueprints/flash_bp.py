"""网页 ROM 烧录：页面与 REST API。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from deskbot_server.infrastructure.flash.rom_flash import (
    flash_manager,
    list_roms,
    list_serial_ports,
    resolve_rom_path,
    validate_port,
    validate_rom_id,
)
from deskbot_server.web.flaskish import FlaskishAPIRoute, jsonify, login_required, render_template, request

router = APIRouter(route_class=FlaskishAPIRoute, tags=["flash"])


@router.get("/flash")
@login_required
def flash_page():
    return render_template("app2c/flash_rom.html", active_nav="flash")


@router.get("/api/flash/ports")
@login_required
def api_flash_ports():
    return jsonify({"ok": True, "ports": list_serial_ports()})


@router.get("/api/flash/roms")
@login_required
def api_flash_roms():
    return jsonify({"ok": True, "roms": [r.to_dict() for r in list_roms()]})


@router.get("/api/flash/roms/{rom_id}/download")
@login_required
def api_flash_rom_download(rom_id: str):
    try:
        path = resolve_rom_path(validate_rom_id(rom_id))
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except (PermissionError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.get("/api/flash/status")
@login_required
def api_flash_status():
    since = request.args.get("since", 0, type=int)
    return jsonify({"ok": True, **flash_manager.status(), "log": flash_manager.log_snapshot(since=since)})


@router.post("/api/flash/build")
@login_required
def api_flash_build():
    try:
        job = flash_manager.start_build()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "job": job.to_dict()})


@router.post("/api/flash/upload")
@login_required
def api_flash_upload():
    body = request.get_json(silent=True) or {}
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
@login_required
def api_flash_cancel():
    cancelled = flash_manager.cancel()
    return jsonify({"ok": True, "cancelled": cancelled})


@router.post("/api/flash/monitor/start")
@login_required
def api_flash_monitor_start():
    body = request.get_json(silent=True) or {}
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
@login_required
def api_flash_monitor_stop():
    flash_manager.serial.stop()
    return jsonify({"ok": True})


@router.post("/api/flash/monitor/send")
@login_required
def api_flash_monitor_send():
    body = request.get_json(silent=True) or {}
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
