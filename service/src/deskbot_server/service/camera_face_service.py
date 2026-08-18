"""摄像头人脸服务：多进程识别 + 视频流订阅 + 跟踪/推流。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np

from deskbot_server.service.application.face_tracker import FaceTracker
from deskbot_server.utils.singleton import SingletonMeta
from deskbot_server.vision.undistort import CameraUndistorter, try_build_undistorter

logger = logging.getLogger("deskbot-server")

VideoStreamCallback = Callable[[str, bytes, dict[str, Any]], Awaitable[None]]

# ---------- 多进程池（模块级，供 pickle）----------
_pool: ProcessPoolExecutor | None = None
_worker_detector = None
_worker_opts_key: tuple | None = None


@dataclass(frozen=True)
class CameraFaceRuntime:
    """全局共用的人脸推理参数（所有设备同一套）。"""

    undistorter: CameraUndistorter | None
    min_face_detection_confidence: float
    min_face_presence_confidence: float
    num_faces: int = 5
    face_track_max_dist_px: float = 90.0
    face_track_max_lost_frames: int = 18
    frame_width: int = 320
    frame_height: int = 240
    face_embedding_enabled: bool = True
    identity_similarity_threshold: float = 0.40
    identity_geometry_threshold: float = 0.88


def build_camera_face_runtime(config: dict[str, Any]) -> CameraFaceRuntime:
    """从 yaml + 全局 camera_face 配置文件构建运行时（不按 device 区分）。"""
    raw = dict(config.get("camera_face") or {})
    try:
        from deskbot_server.dao.camera_face_config_store import load_camera_face_cfg_file

        file_cfg = load_camera_face_cfg_file(device_id=None)
        if file_cfg:
            raw = {**raw, **file_cfg}
            from deskbot_server.vision.camera_face_tune import apply_camera_face_tune

            apply_camera_face_tune(file_cfg)
    except Exception:
        pass

    md_raw = os.environ.get("CAMERA_MIN_FACE_DETECTION_CONFIDENCE")
    mp_raw = os.environ.get("CAMERA_MIN_FACE_PRESENCE_CONFIDENCE")
    nf_raw = os.environ.get("CAMERA_NUM_FACES")

    md = float(md_raw) if md_raw not in (None, "") else float(raw.get("min_face_detection_confidence", 0.5))
    mp = float(mp_raw) if mp_raw not in (None, "") else float(raw.get("min_face_presence_confidence", 0.5))
    nf = int(nf_raw) if nf_raw not in (None, "") else int(raw.get("num_faces", 5))

    md = max(0.05, min(0.95, md))
    mp = max(0.05, min(0.95, mp))
    nf = max(1, min(10, nf))

    track_max_dist = float(raw.get("face_track_max_dist_px", 90.0))
    track_max_lost = int(raw.get("face_track_max_lost_frames", 18))
    track_max_dist = max(16.0, min(240.0, track_max_dist))
    track_max_lost = max(1, min(60, track_max_lost))

    fw = int(raw.get("frame_width", 320))
    fh = int(raw.get("frame_height", 240))
    fw = max(160, min(640, fw))
    fh = max(120, min(480, fh))

    fe_raw = raw.get("face_embedding_enabled", True)
    face_embedding_enabled = str(fe_raw).strip().lower() not in ("0", "false", "no", "off")
    ist_default = 0.40 if face_embedding_enabled else 0.82
    ist = float(raw.get("identity_similarity_threshold", ist_default))
    ist = max(0.25, min(0.99, ist))
    ist_geo = float(raw.get("identity_similarity_threshold_geometry", 0.88))
    ist_geo = max(0.75, min(0.99, ist_geo))

    ud = try_build_undistorter(raw)
    logger.info(
        "[camera_face] frame=%dx%d num_faces=%d min_det=%.2f min_presence=%.2f "
        "track_dist=%.0fpx track_lost=%d（undistort %s）",
        fw,
        fh,
        nf,
        md,
        mp,
        track_max_dist,
        track_max_lost,
        "开启" if ud is not None else "关闭",
    )
    return CameraFaceRuntime(
        undistorter=ud,
        min_face_detection_confidence=md,
        min_face_presence_confidence=mp,
        num_faces=nf,
        face_track_max_dist_px=track_max_dist,
        face_track_max_lost_frames=track_max_lost,
        frame_width=fw,
        frame_height=fh,
        face_embedding_enabled=face_embedding_enabled,
        identity_similarity_threshold=ist,
        identity_geometry_threshold=ist_geo,
    )


def _opts_from_runtime(runtime: CameraFaceRuntime) -> dict[str, Any]:
    undistort = None
    ud = runtime.undistorter
    if ud is not None:
        undistort = (
            int(ud.calib_w),
            int(ud.calib_h),
            tuple(np.asarray(ud.camera_matrix, dtype=np.float64).reshape(-1).tolist()),
            tuple(np.asarray(ud.dist_coeffs, dtype=np.float64).reshape(-1).tolist()),
            float(ud.alpha),
        )
    return {
        "num_faces": int(runtime.num_faces),
        "min_face_detection_confidence": float(runtime.min_face_detection_confidence),
        "min_face_presence_confidence": float(runtime.min_face_presence_confidence),
        "frame_width": int(runtime.frame_width),
        "frame_height": int(runtime.frame_height),
        "undistort": undistort,
    }


def _opts_key(opts: dict[str, Any]) -> tuple:
    return (
        opts.get("num_faces"),
        opts.get("min_face_detection_confidence"),
        opts.get("min_face_presence_confidence"),
        opts.get("frame_width"),
        opts.get("frame_height"),
        opts.get("undistort"),
    )


def _build_undistorter(undistort: Any):
    if not undistort:
        return None
    cw, ch, k_flat, dist_flat, alpha = undistort
    return CameraUndistorter(
        calib_w=int(cw),
        calib_h=int(ch),
        camera_matrix=np.asarray(k_flat, dtype=np.float64).reshape(3, 3),
        dist_coeffs=np.asarray(dist_flat, dtype=np.float64).reshape(-1, 1),
        alpha=float(alpha),
    )


def _ensure_worker_detector(opts: dict[str, Any]):
    global _worker_detector, _worker_opts_key
    key = _opts_key(opts)
    if _worker_detector is not None and _worker_opts_key == key:
        return _worker_detector
    if _worker_detector is not None:
        try:
            _worker_detector.close()
        except Exception:
            pass
        _worker_detector = None

    from deskbot_server.service.application.face_detector import CameraFaceDetector

    _worker_detector = CameraFaceDetector(
        num_faces=int(opts.get("num_faces") or 5),
        undistorter=_build_undistorter(opts.get("undistort")),
        min_face_detection_confidence=float(opts.get("min_face_detection_confidence") or 0.5),
        min_face_presence_confidence=float(opts.get("min_face_presence_confidence") or 0.5),
        frame_width=int(opts.get("frame_width") or 320),
        frame_height=int(opts.get("frame_height") or 240),
    )
    _worker_opts_key = key
    return _worker_detector


def _mp_recognize(image: bytes, opts: dict[str, Any]) -> list[dict[str, Any]]:
    """进程池入口：JPEG → [{landmarks, embedding, ...}, ...]。"""
    from deskbot_server.vision.face_identity import attach_descriptors_to_faces, deduplicate_overlapping_faces

    detector = _ensure_worker_detector(opts)
    faces = detector.detect_faces(image)
    faces = deduplicate_overlapping_faces(faces)
    attach_descriptors_to_faces(faces, bgr_image=detector.last_bgr)

    out: list[dict[str, Any]] = []
    for face in faces or []:
        if not isinstance(face, dict):
            continue
        emb = face.get("embedding") or face.get("face_descriptor")
        if emb is not None and not isinstance(emb, list):
            emb = list(emb)
        row: dict[str, Any] = {
            "landmarks": face.get("landmarks") or [],
            "embedding": emb,
            "image_w": int(face.get("image_w") or opts.get("frame_width") or 320),
            "image_h": int(face.get("image_h") or opts.get("frame_height") or 240),
        }
        if emb is not None:
            row["face_descriptor"] = emb
        if face.get("descriptor_kind"):
            row["descriptor_kind"] = face["descriptor_kind"]
        if face.get("facial_transform") is not None:
            row["facial_transform"] = face["facial_transform"]
        out.append(row)
    return out


class CameraFaceService(metaclass=SingletonMeta):
    """设备 JPEG 帧处理入口（全局一套 runtime）。

    - ``recognize``：多进程识别 → landmarks / embedding
    - ``register_face_embedding``：档案写入
    - ``process``：跟踪、缓存、交互反馈；有订阅时 ``try_emit`` 推流
    """

    def __init__(self) -> None:
        self._runtime: CameraFaceRuntime | None = None
        self._recognize_opts: dict[str, Any] | None = None
        self._trackers: dict[str, FaceTracker] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._frame_count: dict[str, int] = {}
        self._video_subs: dict[str, tuple[str | None, VideoStreamCallback]] = {}
        self._video_subs_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ----- 进程池生命周期 -----

    @classmethod
    def start_pool(cls, max_workers: int = 0) -> None:
        global _pool
        cls.shutdown_pool()
        cpu = os.cpu_count() or 2
        n = int(max_workers) if max_workers and max_workers > 0 else max(1, min(4, cpu))
        n = max(1, min(n, cpu))
        _pool = ProcessPoolExecutor(max_workers=n)
        logger.info("[CameraFaceService] 人脸识别进程池 workers=%d", n)

    @classmethod
    def shutdown_pool(cls) -> None:
        global _pool
        if _pool is None:
            return
        try:
            _pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            _pool.shutdown(wait=False)
        except Exception:
            logger.warning("[CameraFaceService] 进程池 shutdown 异常", exc_info=True)
        _pool = None

    # ----- 配置 -----

    def configure(self, runtime: CameraFaceRuntime) -> None:
        self._runtime = runtime
        self._recognize_opts = _opts_from_runtime(runtime)

    def is_configured(self) -> bool:
        return self._runtime is not None

    @property
    def runtime(self) -> CameraFaceRuntime:
        if self._runtime is None:
            raise RuntimeError("CameraFaceService 尚未 configure")
        return self._runtime

    def _note_loop(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    # ----- 核心：多进程识别 -----

    async def recognize(self, image: bytes) -> list[dict[str, Any]]:
        """多进程人脸识别，返回 ``[{landmarks, embedding, image_w, image_h}, ...]``。"""
        opts = self._recognize_opts
        if opts is None:
            opts = _opts_from_runtime(self.runtime)
            self._recognize_opts = opts
        loop = asyncio.get_running_loop()
        if _pool is None:
            return await loop.run_in_executor(None, _mp_recognize, image, opts)
        return await loop.run_in_executor(_pool, _mp_recognize, image, opts)

    # ----- 档案：embedding 查 / 写 -----

    def register_face_embedding(
        self, name: str, embedding: list[float], *, device_id: str | None = None
    ) -> dict[str, Any]:
        """将 embedding 写入 ``face_profiles``，并刷新 tracker。"""
        from deskbot_server.dao.camera_face_config_store import load_camera_face_cfg_file
        from deskbot_server.service.face_profile_service import upsert_profile
        from deskbot_server.service.application.face_tracker import reload_all_trackers

        name = str(name or "").strip()
        if not name:
            raise ValueError("name required")
        if not isinstance(embedding, list) or len(embedding) < 4:
            raise ValueError("embedding required")
        try:
            vec = [float(x) for x in embedding]
        except (TypeError, ValueError) as exc:
            raise ValueError("embedding must be float vector") from exc

        cfg = load_camera_face_cfg_file(device_id=device_id) or {}
        merge_thr = float(cfg.get("identity_similarity_threshold", self.runtime.identity_similarity_threshold))
        profile = upsert_profile(str(device_id or ""), name=name, descriptor=vec, merge_threshold=merge_thr)
        reload_all_trackers()
        return {
            "id": int(profile["id"]),
            "name": str(profile["name"]),
            "descriptor_kind": str(profile.get("descriptor_kind") or ""),
        }

    # ----- 视频流订阅 / 抓拍 -----

    async def subscribe_video_stream(
        self, conn_id: str, callback: VideoStreamCallback, *, device_id: str | None = None
    ) -> None:
        self._note_loop()
        flt = str(device_id).strip() if device_id else None
        async with self._video_subs_lock:
            self._video_subs[str(conn_id)] = (flt or None, callback)
        logger.info("[CameraFaceService] 视频流订阅 conn_id=%s device_filter=%s", conn_id, flt)

    async def unsubscribe_video_stream(self, conn_id: str) -> None:
        async with self._video_subs_lock:
            removed = self._video_subs.pop(str(conn_id), None)
        if removed is not None:
            logger.info("[CameraFaceService] 视频流取消订阅 conn_id=%s", conn_id)

    async def try_emit_video_frame(
        self, device_id: str, frame_bytes: bytes, *, meta: dict[str, Any] | None = None
    ) -> None:
        """有匹配订阅者才推流；无订阅者时直接返回。"""
        if not frame_bytes:
            return
        device_id = str(device_id or "unknown")
        async with self._video_subs_lock:
            targets = [(cid, cb) for cid, (flt, cb) in self._video_subs.items() if not flt or flt == device_id]
        if not targets:
            return
        payload = {
            "type": "camera_frame",
            "device_id": device_id,
            "size": len(frame_bytes),
            "ts": time.time(),
            "t_mono": time.monotonic(),
        }
        if meta:
            payload.update(meta)
        for conn_id, callback in targets:
            try:
                await callback(device_id, frame_bytes, payload)
            except Exception as exc:
                logger.warning(
                    "[CameraFaceService] 视频流回调失败 conn_id=%s device_id=%s: %s", conn_id, device_id, exc
                )

    async def capture_frame_async(self, device_id: str, *, timeout_s: float = 4.0) -> dict[str, Any]:
        """临时订阅视频流，拿到一帧后取消订阅。"""
        self._note_loop()
        dev = str(device_id or "").strip()
        if not dev:
            return {"ok": False, "error": "缺少 device_id"}

        conn_id = f"capture:{dev}:{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()

        async def _on_frame(_device_id: str, frame: bytes, meta: dict[str, Any]) -> None:
            if fut.done() or not frame:
                return
            fut.set_result(
                {
                    "jpeg": bytes(frame),
                    "ts": float(meta.get("ts") or time.time()),
                    "width": int(meta.get("frame_w") or 0),
                    "height": int(meta.get("frame_h") or 0),
                    "source": str(meta.get("source") or "video_stream"),
                }
            )

        await self.subscribe_video_stream(conn_id, _on_frame, device_id=dev)
        try:
            row = await asyncio.wait_for(fut, timeout=max(0.1, float(timeout_s)))
        except TimeoutError:
            return {
                "ok": False,
                "error": (
                    f"等待相机帧超时（{timeout_s:.1f}s）；请确认设备已连接且相机上行已开启（收音/播报期间可能暂停上传）"
                ),
            }
        finally:
            await self.unsubscribe_video_stream(conn_id)

        return self._row_to_capture_result(row)

    @staticmethod
    def _row_to_capture_result(row: dict[str, Any]) -> dict[str, Any]:
        import base64

        jpeg = row["jpeg"]
        return {
            "ok": True,
            "ts": row["ts"],
            "width": row["width"],
            "height": row["height"],
            "source": row["source"],
            "jpeg_bytes": len(jpeg),
            "jpeg_base64": base64.standard_b64encode(jpeg).decode("ascii"),
        }

    def _tracker_for(self, device_id: str) -> FaceTracker:
        tr = self._trackers.get(device_id)
        if tr is not None:
            return tr
        rt = self.runtime
        tr = FaceTracker(
            device_id=device_id,
            max_dist_px=rt.face_track_max_dist_px,
            max_lost_frames=rt.face_track_max_lost_frames,
            identity_similarity_threshold=rt.identity_similarity_threshold,
            identity_geometry_threshold=rt.identity_geometry_threshold,
        )
        self._trackers[device_id] = tr
        return tr

    # ----- 上行帧处理 -----

    async def process(
        self, device_id: str, frame_bytes: bytes, *, frame_source: str = "asr_chat", log_channel: str = "/asr_chat"
    ) -> None:
        """读到一帧 JPEG：识别 → 跟踪 / 缓存 / 推流。"""
        self._note_loop()
        nbytes = len(frame_bytes or b"")
        if not self.is_configured():
            logger.info(
                "[camera] device_id=%s bytes=%d accepted=false reason=not_configured channel=%s",
                device_id,
                nbytes,
                log_channel,
            )
            return

        prev = self._inflight.get(device_id)
        if prev is not None and not prev.done():
            logger.info(
                "[camera] device_id=%s bytes=%d accepted=false reason=busy channel=%s", device_id, nbytes, log_channel
            )
            return

        task = asyncio.create_task(
            self._detect_then_post(
                device_id=device_id, frame_bytes=frame_bytes, frame_source=frame_source, log_channel=log_channel
            ),
            name=f"camera_face:{device_id}",
        )
        self._inflight[device_id] = task

    async def _detect_then_post(
        self, *, device_id: str, frame_bytes: bytes, frame_source: str, log_channel: str
    ) -> None:
        from deskbot_server.utils.concurrency import face_infer_slot
        from deskbot_server.vision.geometry import FACE_FRAME_HEIGHT, FACE_FRAME_WIDTH

        runtime = self.runtime
        nbytes = len(frame_bytes or b"")
        t0 = time.monotonic()
        try:
            async with face_infer_slot():
                faces = await self.recognize(frame_bytes)
        except Exception as exc:
            infer_ms = (time.monotonic() - t0) * 1000.0
            logger.info(
                "[camera] device_id=%s bytes=%d accepted=true infer_ms=%.1f faces=- "
                "status=recognize_error channel=%s err=%s",
                device_id,
                nbytes,
                infer_ms,
                log_channel,
                exc,
            )
            await self.try_emit_video_frame(
                device_id,
                frame_bytes,
                meta={
                    "detected": False,
                    "frame_w": int(runtime.frame_width or FACE_FRAME_WIDTH),
                    "frame_h": int(runtime.frame_height or FACE_FRAME_HEIGHT),
                    "source": frame_source,
                },
            )
            return

        infer_ms = (time.monotonic() - t0) * 1000.0
        n_faces = len(faces or [])
        self._frame_count[device_id] = self._frame_count.get(device_id, 0) + 1
        logger.info(
            "[camera] device_id=%s bytes=%d accepted=true infer_ms=%.1f faces=%d status=ok channel=%s source=%s",
            device_id,
            nbytes,
            infer_ms,
            n_faces,
            log_channel,
            frame_source,
        )

        await self._after_recognize(
            device_id=device_id,
            frame_bytes=frame_bytes,
            faces=faces,
            frame_source=frame_source,
            log_channel=log_channel,
        )

    def _preview_meta(self, *, frame_source: str, detect: dict[str, Any] | None = None) -> dict[str, Any]:
        from deskbot_server.vision.geometry import FACE_FRAME_HEIGHT, FACE_FRAME_WIDTH

        runtime = self.runtime
        if detect and detect.get("landmarks"):
            return {
                "detected": True,
                "landmarks": detect["landmarks"],
                "frame_w": detect["image_w"],
                "frame_h": detect["image_h"],
                "yaw_deg": detect["yaw_deg"],
                "pitch_deg": detect["pitch_deg"],
                "iris_offsets": detect["iris_offsets"],
                "face_score": detect.get("face_score"),
                "frontal_score": detect["frontal_score"],
                "is_frontal": detect["is_frontal"],
                "confidence": detect.get("face_score"),
                "faces": detect.get("faces"),
                "face_count": detect.get("face_count"),
                "face_id": detect.get("face_id"),
                "source": frame_source,
            }
        return {
            "detected": False,
            "frame_w": int(runtime.frame_width or FACE_FRAME_WIDTH),
            "frame_h": int(runtime.frame_height or FACE_FRAME_HEIGHT),
            "source": frame_source,
        }

    async def _after_recognize(
        self, *, device_id: str, frame_bytes: bytes, faces: list[dict[str, Any]], frame_source: str, log_channel: str
    ) -> None:
        from deskbot_server.service.application.camera_frame import analyze_face_detections
        from deskbot_server.service.application.camera_servo_follower import camera_servo_follower_tick
        from deskbot_server.service.application.face_snapshot_cache import update_device_faces
        from deskbot_server.service.application.interaction_feedback import clear_face_analysis, note_face_analysis

        detect: dict[str, Any] | None = None
        try:
            tracker = self._tracker_for(device_id)
            tagged = tracker.assign_ids(list(faces or []))
            update_device_faces(device_id, tagged)
            detect = analyze_face_detections(tagged)
        except Exception as exc:
            logger.warning("[%s] 人脸后处理失败 device_id=%s: %s", log_channel, device_id, exc)
            await self.try_emit_video_frame(device_id, frame_bytes, meta=self._preview_meta(frame_source=frame_source))
            return

        if not detect or not detect.get("landmarks"):
            clear_face_analysis(device_id)
            try:
                from deskbot_server.service.live_service import LiveService

                LiveService().on_face_lost(device_id)
            except Exception:
                pass
            await self.try_emit_video_frame(device_id, frame_bytes, meta=self._preview_meta(frame_source=frame_source))
            return

        note_face_analysis(device_id, detect)
        try:
            from deskbot_server.controller.runtime import get_runtime
            from deskbot_server.service.live_service import LiveService

            runtime = get_runtime()
            live = LiveService()
            await live.on_face_tick(device_id, detect)
            if not live.owns_face_tracking(device_id):
                await camera_servo_follower_tick(runtime.device_ws, device_id, detect)
        except RuntimeError:
            # 独立的人脸配置/测试路径没有完整应用运行时，跳过设备下发。
            pass
        except Exception:
            logger.exception("[camera_face] 人脸跟随下发失败 device_id=%s", device_id)
        # 正脸 / landmarks 等信息随 JPEG 经 try_emit 推给 /camera_view，不再单独 broadcast face_pos
        await self.try_emit_video_frame(
            device_id, frame_bytes, meta=self._preview_meta(frame_source=frame_source, detect=detect)
        )


# ---------- 相机抓拍辅助函数 ----------

_DEFAULT_CAPTURE_FPS = 5
_DEFAULT_WAIT_TIMEOUT_S = 4.0


async def request_camera_fps_boost(device_id: str, device_ws: Any, *, cam_fps: int = _DEFAULT_CAPTURE_FPS) -> None:
    """通过 pb 提示设备提高相机上行帧率（经 /asr_chat）。"""
    dev = str(device_id or "").strip()
    if not dev or device_ws is None:
        return
    try:
        from deskbot_server.pb.cam_signal import build_cam_fps_signal_pb

        payload = build_cam_fps_signal_pb(cam_fps=cam_fps)
        n = await device_ws.send(dev, payload)
        logger.info("[capture_camera] cam_fps=%d boost device_id=%s delivered=%s", cam_fps, dev, n)
    except Exception as exc:
        logger.warning("[capture_camera] cam_fps boost failed device_id=%s: %s", dev, exc)


async def capture_camera_for_device_async(
    device_id: str,
    *,
    device_ws: Any = None,
    cam_fps: int = _DEFAULT_CAPTURE_FPS,
    wait_timeout_s: float = _DEFAULT_WAIT_TIMEOUT_S,
) -> dict[str, Any]:
    """异步抓拍：可选提升 cam_fps，临时订阅视频流取一帧后取消订阅。"""
    dev = str(device_id or "").strip()
    if not dev:
        return {"ok": False, "error": "缺少 device_id"}

    await request_camera_fps_boost(dev, device_ws, cam_fps=cam_fps)
    return await CameraFaceService().capture_frame_async(dev, timeout_s=wait_timeout_s)
