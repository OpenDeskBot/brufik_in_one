"""跨帧 face_id + 人脸相似性 re-id（鼻尖+特征联合跟踪）。"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Optional

from deskbot_server.constants import FACE_PROFILES_FILE
from deskbot_server.device_data import resolve_json_path
from deskbot_server.face_identity import (
    attach_descriptor,
    descriptor_cosine_similarity,
    ema_update_descriptor,
    is_embedding_vector,
    match_threshold_for_descriptor,
)
from deskbot_server.face_profiles_store import (
    best_profile_similarity,
    load_face_profiles,
    resolve_profile_match,
)


def _nose_xy(face: dict[str, Any]) -> Optional[tuple[float, float]]:
    points = face.get("points") or []
    for p in points:
        if isinstance(p, dict) and p.get("name") == "nose":
            try:
                return float(p["x"]), float(p["y"])
            except (TypeError, ValueError, KeyError):
                return None
    landmarks = face.get("landmarks") or []
    for p in landmarks:
        if isinstance(p, dict) and p.get("name") == "nose":
            try:
                return float(p["x"]), float(p["y"])
            except (TypeError, ValueError, KeyError):
                return None
    return None


@dataclass
class _Track:
    face_id: int
    nose: tuple[float, float]
    descriptor: list[float]
    person_id: Optional[int] = None
    person_name: Optional[str] = None
    lost: int = 0


_active_trackers: list["FaceTracker"] = []


def _register_tracker(tracker: "FaceTracker") -> None:
    _active_trackers.append(tracker)


def reload_all_trackers() -> None:
    for tracker in list(_active_trackers):
        tracker.reload_profiles()


class FaceTracker:
    """为每帧人脸分配 ``face_id``，并通过几何特征匹配已注册 ``person_id`` / 人名。

    改进点（相对纯鼻尖跟踪）：

    - 鼻尖距离 + 特征相似度**联合**关联 track，转头/抖动时不易丢号
    - ``person_id`` 绑定后使用**滞回阈值**（保持阈值 < 匹配阈值），减少闪烁
    - ``face_id`` 单调递增、不在 1–32 间循环复用
    - 档案向量仅在注册时写入文件；运行时**不** EMA 污染 ``face_profiles.json``
    """

    def __init__(
        self,
        *,
        device_id: Optional[str] = None,
        max_dist_px: float = 90.0,
        max_lost_frames: int = 18,
        max_ids: int = 32,
        identity_similarity_threshold: float = 0.40,
        identity_geometry_threshold: float = 0.88,
        descriptor_ema_alpha: float = 0.1,
        identity_keep_margin: float = 0.12,
    ) -> None:
        self.max_dist_px = max(16.0, float(max_dist_px))
        self.max_lost_frames = max(3, int(max_lost_frames))
        self.max_ids = max(4, int(max_ids))
        self.identity_embedding_threshold = max(0.25, min(0.99, float(identity_similarity_threshold)))
        self.identity_geometry_threshold = max(0.75, min(0.99, float(identity_geometry_threshold)))
        self.descriptor_ema_alpha = max(0.05, min(0.35, float(descriptor_ema_alpha)))
        self.identity_keep_margin = max(0.05, min(0.25, float(identity_keep_margin)))
        self._device_id = str(device_id or "").strip() or None
        self._profiles_path = resolve_json_path(FACE_PROFILES_FILE, self._device_id)
        self._next_face_id = 1
        self._tracks: dict[int, _Track] = {}
        self._profiles_mtime: float = 0.0
        self._profiles: list[dict[str, Any]] = load_face_profiles(device_id=self._device_id)
        self._profiles_mtime = self._profiles_file_mtime()
        self._last_faces: list[dict[str, Any]] = []
        _register_tracker(self)

    def _match_threshold(self, desc: list[float]) -> float:
        return match_threshold_for_descriptor(
            desc,
            embedding_threshold=self.identity_embedding_threshold,
            geometry_threshold=self.identity_geometry_threshold,
        )

    def _keep_threshold(self, desc: list[float]) -> float:
        return max(
            0.25 if is_embedding_vector(desc) else 0.70,
            self._match_threshold(desc) - self.identity_keep_margin,
        )

    def _profiles_file_mtime(self) -> float:
        try:
            return os.path.getmtime(self._profiles_path)
        except OSError:
            return 0.0

    def _maybe_reload_profiles(self) -> None:
        mtime = self._profiles_file_mtime()
        if mtime == self._profiles_mtime:
            return
        self._profiles_mtime = mtime
        self._profiles = load_face_profiles(device_id=self._device_id)

    def reload_profiles(self) -> None:
        self._profiles_mtime = self._profiles_file_mtime()
        self._profiles = load_face_profiles(device_id=self._device_id)

    def get_last_faces(self) -> list[dict[str, Any]]:
        return list(self._last_faces)

    def get_face_by_id(self, face_id: int) -> Optional[dict[str, Any]]:
        for face in self._last_faces:
            if int(face.get("face_id") or 0) == int(face_id):
                return face
        return None

    def _alloc_face_id(self) -> int:
        fid = self._next_face_id
        self._next_face_id += 1
        return fid

    def _scaled_max_dist(self, image_w: int) -> float:
        w = max(160, int(image_w or 320))
        return self.max_dist_px * (w / 320.0)

    def _track_match_cost(
        self,
        track: _Track,
        nose: tuple[float, float],
        desc: list[float],
        *,
        max_dist_px: float,
    ) -> float:
        tx, ty = track.nose
        nd = math.hypot(nose[0] - tx, nose[1] - ty)
        if nd > max_dist_px:
            return float("inf")
        sim = descriptor_cosine_similarity(desc, track.descriptor)
        spatial = nd / max_dist_px
        feat = 1.0 - max(-1.0, sim)
        return spatial * 0.35 + feat * 0.65

    def _resolve_person(
        self,
        desc: list[float],
        *,
        locked_person_id: Optional[int],
    ) -> tuple[Optional[dict[str, Any]], float]:
        return resolve_profile_match(
            self._profiles,
            desc,
            match_threshold=self._match_threshold(desc),
            keep_threshold=self._keep_threshold(desc),
            locked_person_id=locked_person_id,
        )

    def _bind_person(self, track: _Track, profile: dict[str, Any], descriptor: list[float]) -> None:
        track.person_id = int(profile["person_id"])
        track.person_name = str(profile["name"])
        track.descriptor = ema_update_descriptor(
            track.descriptor,
            descriptor,
            alpha=self.descriptor_ema_alpha,
        )

    def _try_bind_profile(self, track: _Track, desc: list[float]) -> Optional[float]:
        profile, sim = self._resolve_person(desc, locked_person_id=track.person_id)
        if profile is not None:
            self._bind_person(track, profile, desc)
            return sim
        return None

    def _find_track_for_person(self, person_id: int) -> Optional[tuple[int, _Track]]:
        for tid, track in self._tracks.items():
            if track.person_id == person_id:
                return tid, track
        return None

    def assign_ids(self, faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._maybe_reload_profiles()
        if not faces:
            for tid, track in list(self._tracks.items()):
                track.lost += 1
                if track.lost > self.max_lost_frames:
                    self._tracks.pop(tid, None)
            self._last_faces = []
            return []

        detections: list[tuple[int, tuple[float, float], list[float], dict[str, Any], int]] = []
        for idx, face in enumerate(faces):
            desc = face.get("face_descriptor") or attach_descriptor(face)
            nose = _nose_xy(face)
            if nose is None or desc is None:
                continue
            image_w = int(face.get("image_w") or 0) or 320
            detections.append((idx, nose, desc, face, image_w))

        if not detections:
            for tid, track in list(self._tracks.items()):
                track.lost += 1
                if track.lost > self.max_lost_frames:
                    self._tracks.pop(tid, None)
            self._last_faces = []
            return []

        max_dist = self._scaled_max_dist(max(d[4] for d in detections))

        assigned_track: dict[int, int] = {}
        used_tracks: set[int] = set()
        pairs: list[tuple[float, int, int]] = []
        for det_idx, nose, desc, _face, _iw in detections:
            for tid, track in self._tracks.items():
                cost = self._track_match_cost(track, nose, desc, max_dist_px=max_dist)
                if math.isfinite(cost):
                    pairs.append((cost, det_idx, tid))
        pairs.sort(key=lambda x: x[0])

        for _cost, det_idx, tid in pairs:
            if det_idx in assigned_track or tid in used_tracks:
                continue
            assigned_track[det_idx] = tid
            used_tracks.add(tid)

        out: list[dict[str, Any]] = []
        for det_idx, nose, desc, face, _iw in detections:
            tagged = dict(face)
            track: _Track

            if det_idx in assigned_track:
                tid = assigned_track[det_idx]
                track = self._tracks[tid]
                track.nose = nose
                track.lost = 0
                track.descriptor = ema_update_descriptor(
                    track.descriptor,
                    desc,
                    alpha=self.descriptor_ema_alpha,
                )
                if track.person_id is None:
                    sim = self._try_bind_profile(track, desc)
                    if sim is not None:
                        tagged["identity_score"] = round(sim, 3)
                        tagged["match_source"] = "person_profile"
                else:
                    sim = self._try_bind_profile(track, desc)
                    if sim is not None:
                        tagged["identity_score"] = round(sim, 3)
                tagged["face_id"] = tid
                tagged["face_id_source"] = "spatial_track"
            else:
                profile, sim = self._resolve_person(desc, locked_person_id=None)
                reused_person: Optional[tuple[int, _Track]] = None
                if profile is not None:
                    reused_person = self._find_track_for_person(int(profile["person_id"]))

                track_match: Optional[tuple[int, _Track, float]] = None
                best_tid: Optional[int] = None
                best_track: Optional[_Track] = None
                best_sim = -1.0
                desc_thr = max(
                    0.28 if is_embedding_vector(desc) else 0.75,
                    self._keep_threshold(desc) - 0.05,
                )
                for tid, tr in self._tracks.items():
                    if tid in used_tracks:
                        continue
                    ds = descriptor_cosine_similarity(desc, tr.descriptor)
                    if ds >= desc_thr and ds > best_sim:
                        best_sim = ds
                        best_tid = tid
                        best_track = tr
                if best_tid is not None and best_track is not None:
                    track_match = (best_tid, best_track, best_sim)

                if reused_person is not None:
                    tid, track = reused_person
                    track.nose = nose
                    track.lost = 0
                    if profile is not None:
                        self._bind_person(track, profile, desc)
                        tagged["identity_score"] = round(sim, 3)
                        tagged["match_source"] = "person_profile"
                    used_tracks.add(tid)
                elif track_match is not None:
                    tid, track, tsim = track_match
                    track.nose = nose
                    track.lost = 0
                    track.descriptor = ema_update_descriptor(
                        track.descriptor, desc, alpha=self.descriptor_ema_alpha
                    )
                    if track.person_id is None and profile is not None:
                        self._bind_person(track, profile, desc)
                        tagged["identity_score"] = round(sim, 3)
                        tagged["match_source"] = "person_profile"
                    else:
                        tagged["identity_score"] = round(tsim, 3)
                    tagged["match_source"] = tagged.get("match_source") or "descriptor_track"
                    used_tracks.add(tid)
                elif profile is not None:
                    tid = self._alloc_face_id()
                    track = _Track(face_id=tid, nose=nose, descriptor=list(desc))
                    self._bind_person(track, profile, desc)
                    tagged["identity_score"] = round(sim, 3)
                    tagged["match_source"] = "person_profile"
                    self._tracks[tid] = track
                    used_tracks.add(tid)
                else:
                    tid = self._alloc_face_id()
                    track = _Track(face_id=tid, nose=nose, descriptor=list(desc))
                    self._tracks[tid] = track
                    tagged["match_source"] = "new"
                tagged["face_id"] = tid

            if track.person_id is not None:
                tagged["person_id"] = track.person_id
                tagged["person_name"] = track.person_name
            elif self._profiles:
                _best, best_sim = best_profile_similarity(self._profiles, desc)
                if best_sim >= 0:
                    tagged["identity_score"] = round(best_sim, 3)

            fd = face.get("face_descriptor") or desc
            if isinstance(fd, list):
                tagged["descriptor_dim"] = len(fd)
            dk = face.get("descriptor_kind")
            if dk:
                tagged["descriptor_kind"] = str(dk)

            ms = tagged.get("match_source")
            if ms:
                tagged["face_id_source"] = ms
            out.append(tagged)

        for tid, track in list(self._tracks.items()):
            if tid in used_tracks:
                continue
            track.lost += 1
            if track.lost > self.max_lost_frames:
                self._tracks.pop(tid, None)

        if len(self._tracks) > self.max_ids:
            stale = sorted(
                ((tid, tr.lost) for tid, tr in self._tracks.items() if tid not in used_tracks),
                key=lambda x: -x[1],
            )
            for tid, _lost in stale:
                if len(self._tracks) <= self.max_ids:
                    break
                self._tracks.pop(tid, None)

        out.sort(key=lambda f: int(f.get("face_id") or 0))
        self._last_faces = out
        return out
