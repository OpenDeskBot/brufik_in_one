"""pb mic 字段与 ASR 无效开麦信号。"""

from __future__ import annotations

from deskbot_server.pb.mic_signal import build_mic_signal_pb, parse_pb_mic


def test_parse_pb_mic():
    assert parse_pb_mic(None) is None
    assert parse_pb_mic("hold") == "hold"
    assert parse_pb_mic("OPEN") == "open"
    assert parse_pb_mic("mute") == "mute"
    assert parse_pb_mic("bad") is None


def test_build_mic_signal_pb_open():
    msg = build_mic_signal_pb(mic="open", req="abc123")
    assert msg["type"] == "pb_single"
    assert msg["req"] == "abc123"
    assert msg["mic"] == "open"
    assert "anim" not in msg
    assert "servo" not in msg
    assert "audio" not in msg
    assert "volume" not in msg


def test_build_mic_signal_pb_hold_omits_field():
    msg = build_mic_signal_pb(mic="hold")
    assert "mic" not in msg
