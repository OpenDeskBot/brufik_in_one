"""PB 下行序列数据模型。

与 ``hardware/firmware/pb_model.h`` 一一对应，将 pb wire 消息从 ``dict[str, Any]``
提升为类型安全的 frozen dataclass。纯数据定义，零业务逻辑、零 IO。

wire 格式细节见 ``docs/esp32_pb_protocol.md``。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


# ---------------------------------------------------------------------------
# 枚举：与 C++ PbModelType 对齐
# ---------------------------------------------------------------------------


class PbType(IntEnum):
    """pb 下行消息类型（``msg["type"]``）。"""

    START = 0
    CHUNK = 1
    END = 2
    SINGLE = 3
    CANCEL = 4

    @classmethod
    def from_wire(cls, raw: str) -> PbType:
        """从 wire 字符串解析（``"pb_start"`` → ``START``）。"""
        _MAP = {
            "pb_start": cls.START,
            "pb_chunk": cls.CHUNK,
            "pb_end": cls.END,
            "pb_single": cls.SINGLE,
            "pb_cancel": cls.CANCEL,
        }
        return _MAP.get(str(raw).strip().lower(), cls.CANCEL)

    @property
    def wire(self) -> str:
        """返回 wire 字符串（``START`` → ``"pb_start"``）。"""
        _REV = {
            PbType.START: "pb_start",
            PbType.CHUNK: "pb_chunk",
            PbType.END: "pb_end",
            PbType.SINGLE: "pb_single",
            PbType.CANCEL: "pb_cancel",
        }
        return _REV.get(self, "pb_cancel")

    @property
    def is_play(self) -> bool:
        """``pb_start`` / ``pb_chunk`` / ``pb_end`` / ``pb_single`` 可入队播放。"""
        return self in _PLAY_TYPES


_PLAY_TYPES = frozenset({PbType.START, PbType.CHUNK, PbType.END, PbType.SINGLE})


class PbAction(IntEnum):
    """队列调度 action（``msg["action"]``），仅用于 PbSeq 级别。"""

    DEFAULT = 0
    APPEND = 1
    REPLACE = 2

    @classmethod
    def from_wire(cls, raw: str) -> PbAction:
        _MAP = {"default": cls.DEFAULT, "append": cls.APPEND, "replace": cls.REPLACE}
        return _MAP.get(str(raw).strip().lower(), cls.DEFAULT)

    @property
    def wire(self) -> str:
        _REV = {PbAction.DEFAULT: "default", PbAction.APPEND: "append", PbAction.REPLACE: "replace"}
        return _REV.get(self, "default")


# ---------------------------------------------------------------------------
# 子结构
# ---------------------------------------------------------------------------

# elements 图层键的绘制顺序
ANIM_LAYER_ORDER: tuple[str, ...] = ("bg", "nose", "mouth", "eye_l", "eye_r", "extra")


@dataclass(frozen=True)
class PbServo:
    """一段头部双轴舵机动作。与 ``pb_servo_frame`` 对齐。

    ``xm`` / ``ym``：0 绝对，1 相对增量，2 本轴保持。
    """

    xm: int = 2
    ym: int = 2
    x: int = 0
    y: int = 0
    ms: int = 0

    def to_wire(self) -> dict[str, int]:
        return {"xm": self.xm, "ym": self.ym, "x": self.x, "y": self.y, "ms": self.ms}


@dataclass(frozen=True)
class PbAudio:
    """一段音频 binary 的元数据。与 ``pb_audio`` 对齐。

    ``sr`` / ``ch`` / ``fmt`` 仅在序列首包含音频的包中声明，
    后续分片省略时沿用首包值。

    Attributes:
        next_bin_len: 紧随 JSON 之后的 binary 字节数；0 表示无音频。
        frames: Opus batch 帧数；PCM 时为 0。
        sr: 采样率（仅首包含音频时声明）。
        ch: 声道数（仅首包含音频时声明）。
        fmt: 编码格式，``"opus"`` 或 ``"s16le"``（仅首包含音频时声明）。
    """

    next_bin_len: int = 0
    frames: int = 0
    sr: int = 0
    ch: int = 0
    fmt: str = ""

    def to_wire(self) -> dict[str, Any]:
        """仅输出 ``audio`` 子树（不含根级 ``sr`` / ``fmt`` / ``ch``）。"""
        out: dict[str, Any] = {"next_bin_len": self.next_bin_len}
        if self.frames > 0:
            out["frames"] = self.frames
        return out


@dataclass(frozen=True)
class PbAnim:
    """一个表情时间片。与 ``pb_anim_frame`` 对齐。

    ``elements`` 的键为图层名（``bg`` / ``nose`` / ``mouth`` / ``eye_l`` /
    ``eye_r`` / ``extra``），值为该层图元列表。每个图元为 dict，
    含 ``shape`` + 坐标字段 + ``c``（RGB565），格式见协议文档 §5。

    Attributes:
        elements: 图层 → 图元列表。
        ms: 该段子动画时长（ms，≥1）。
        phoneme: 可选，音素符号（调试用）。
    """

    elements: dict[str, Any] = field(default_factory=dict)
    ms: int = 1
    phoneme: str = ""

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"elements": self.elements, "ms": self.ms}
        if self.phoneme:
            out["phoneme"] = self.phoneme
        return out


# ---------------------------------------------------------------------------
# 顶层结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PbBlock:
    """单条 pb wire 消息。与 C++ ``pb_model`` 结构对齐。

    多片序列：``pb_start(idx=0)`` → ``pb_chunk(idx=1…N-2)`` → ``pb_end(idx=N-1)``；
    单片序列仅 ``pb_single``。

    Attributes:
        type: 消息类型。
        req: 序列 ID（hex 字符串）。
        idx: 分片序号（从 0 递增）。
        chunk_ms: 本片时长（ms）。
        anim: 表情动画帧列表。
        servo: 舵机动作列表。
        audio: 音频 binary 元数据（``None`` 表示本片无音频）。
        volume: 音量 0–100；``None`` 表示不指定（设备保持现状）。
        cam_fps: 相机目标帧率；``None`` 或 0 表示不指定。
        binaries: 紧随 JSON 之后的 binary 数据列表（如 Opus 音频）。
    """

    type: PbType = PbType.CANCEL
    req: str = ""
    idx: int = 0
    chunk_ms: int = 0
    anim: tuple[PbAnim, ...] = ()
    servo: tuple[PbServo, ...] = ()
    audio: PbAudio | None = None
    volume: int | None = None
    cam_fps: int | None = None
    binaries: tuple[bytes, ...] = ()

    @property
    def is_play(self) -> bool:
        """是否为可入队播放的类型（非 ``pb_cancel``）。"""
        return self.type.is_play

    def to_wire(self, *, level: int = 1, sr: int = 0, fmt: str = "", ch: int = 0) -> dict[str, Any]:
        """序列化为 pb wire JSON dict。

        ``level`` / ``sr`` / ``fmt`` / ``ch`` 为序列级属性，
        由 ``PbSeq.to_wire_pairs()`` 传入。
        """
        msg: dict[str, Any] = {
            "type": self.type.wire,
            "req": self.req,
            "idx": self.idx,
            "chunk_ms": self.chunk_ms,
            "pb_ver": 2,
            "level": level,
        }
        if self.anim:
            msg["anim"] = [a.to_wire() for a in self.anim]
        if self.servo:
            msg["servo"] = [s.to_wire() for s in self.servo]
        if self.audio is not None and self.audio.next_bin_len > 0:
            msg["audio"] = self.audio.to_wire()
            if sr > 0:
                msg["sr"] = sr
            if fmt:
                msg["fmt"] = fmt
            if ch > 0:
                msg["ch"] = ch
        if self.volume is not None:
            msg["volume"] = self.volume
        if self.cam_fps is not None and self.cam_fps > 0:
            msg["cam_fps"] = self.cam_fps
        return msg

    @classmethod
    def from_wire(cls, wire: dict[str, Any], binaries: tuple[bytes, ...] = ()) -> PbBlock:
        """从 pb wire JSON dict 反序列化为 ``PbBlock``。"""
        anim_raw = wire.get("anim")
        anim = tuple(
            PbAnim(elements=a.get("elements", {}), ms=a.get("ms", 1), phoneme=a.get("phoneme", ""))
            for a in anim_raw
        ) if isinstance(anim_raw, list) else ()

        servo_raw = wire.get("servo")
        servo = tuple(
            PbServo(xm=s.get("xm", 2), ym=s.get("ym", 2), x=s.get("x", 0), y=s.get("y", 0), ms=s.get("ms", 0))
            for s in servo_raw
        ) if isinstance(servo_raw, list) else ()

        audio_raw = wire.get("audio")
        audio: PbAudio | None = None
        if isinstance(audio_raw, dict):
            audio = PbAudio(
                next_bin_len=int(audio_raw.get("next_bin_len", 0)),
                frames=int(audio_raw.get("frames", 0)),
                sr=int(wire.get("sr", 0)),
                ch=int(wire.get("ch", 0)),
                fmt=str(wire.get("fmt", "")),
            )

        return cls(
            type=PbType.from_wire(wire.get("type", "")),
            req=str(wire.get("req", "")),
            idx=int(wire.get("idx", 0)),
            chunk_ms=int(wire.get("chunk_ms", 0)),
            anim=anim,
            servo=servo,
            audio=audio,
            volume=wire.get("volume"),
            cam_fps=wire.get("cam_fps"),
            binaries=binaries,
        )


@dataclass(frozen=True)
class PbSeq:
    """一条完整的 pb 播放序列。

    对应 wire 上 ``pb_start`` … ``pb_end``（多片）或 ``pb_single``（单片）。
    ``sr`` / ``fmt`` / ``ch`` 为序列级属性，仅在首包含音频的包中声明。

    Attributes:
        req: 序列 ID（hex 字符串）。
        entries: 按 ``idx`` 排序的分片列表。
        level: 优先级 0–3（0 idle，1 口播，2 紧急，3 调试）。
        action: 队列调度策略（序列级）。
        sr: 采样率（序列级）。
        ch: 声道数（序列级）。
        fmt: 编码格式（序列级，``"opus"`` 或 ``"s16le"``）。
    """

    req: str = ""
    entries: tuple[PbBlock, ...] = ()
    level: int = 1
    action: PbAction = PbAction.REPLACE
    sr: int = 24000
    ch: int = 1
    fmt: str = "opus"
    _done: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)

    @property
    def block_count(self) -> int:
        return len(self.entries)

    @property
    def is_single(self) -> bool:
        """是否为单片序列（``pb_single``）。"""
        return self.block_count == 1 and self.entries[0].type == PbType.SINGLE

    @property
    def total_chunk_ms(self) -> int:
        return sum(e.chunk_ms for e in self.entries)

    def compare(self, other: PbSeq) -> int:
        """比较两个 PbSeq 的优先级。

        Returns:
            1  → self 抢占 other（优先级更高，或同级 REPLACE）
            0  → 并存（同级 APPEND）
            -1 → self 让位 other（优先级更低，或同级 DEFAULT）
        """
        if self.level > other.level:
            return 1
        if self.level < other.level:
            return -1
        # 同 level
        if self.action == PbAction.REPLACE:
            return 1
        if self.action == PbAction.APPEND:
            return 0
        return -1

    def to_wire_pairs(self) -> list[tuple[dict[str, Any], list[bytes]]]:
        """序列化为 ``(pb_dict, binaries)`` 列表。"""
        pairs: list[tuple[dict[str, Any], list[bytes]]] = []
        for blk in self.entries:
            has_audio = blk.audio is not None and blk.audio.next_bin_len > 0
            sr = self.sr if has_audio else 0
            fmt = self.fmt if has_audio else ""
            ch = self.ch if has_audio else 0
            pairs.append((blk.to_wire(level=self.level, sr=sr, fmt=fmt, ch=ch), list(blk.binaries)))
        return pairs

    @classmethod
    def from_wire_pairs(
        cls,
        pairs: list[tuple[dict[str, Any], list[bytes]]],
        *,
        level: int = 1,
    ) -> PbSeq:
        """从 ``(wire_dict, binaries)`` 列表反序列化为 ``PbSeq``。

        ``sr`` / ``fmt`` / ``ch`` 从首个含音频的 block 中提取。
        """
        entries: list[PbBlock] = []
        sr = 0
        ch = 0
        fmt = ""
        req = ""
        action = PbAction.REPLACE
        for wire, bins in pairs:
            blk = PbBlock.from_wire(wire, binaries=tuple(bins))
            entries.append(blk)
            if not req and blk.req:
                req = blk.req
            if blk.audio is not None and blk.audio.next_bin_len > 0 and not sr:
                sr = blk.audio.sr
                ch = blk.audio.ch
                fmt = blk.audio.fmt
        # action 从 wire 顶层取（如果有），否则默认 REPLACE
        if pairs:
            action = PbAction.from_wire(pairs[0][0].get("action", ""))
        return cls(req=req, entries=tuple(entries), level=level, action=action, sr=sr or 24000, ch=ch or 1, fmt=fmt or "opus")
