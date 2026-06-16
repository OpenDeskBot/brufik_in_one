"""音素 PCM 均分逻辑的单元测试（不依赖 PaddleSpeech 运行时）。"""

from __future__ import annotations

import base64

import numpy as np

from paddlespeech_server.phoneme import split_pcm_by_phonemes


def test_split_even_phonemes():
    sr = 24000
    pcm = np.arange(240, dtype=np.int16)  # 10 ms @ 24 kHz
    phone_ids = [1, 2, 3]
    id_to_sym = {1: "a", 2: "b", 3: "c"}

    segs = split_pcm_by_phonemes(pcm, phone_ids, sample_rate=sr, id_to_sym=id_to_sym)

    assert len(segs) == 3
    assert segs[0]["phoneme_id"] == 1
    assert segs[0]["phoneme"] == "a"
    assert segs[1]["phoneme"] == "b"
    assert segs[2]["phoneme"] == "c"

    total_samples = sum(
        len(np.frombuffer(base64.b64decode(s["audio"]), dtype=np.int16)) for s in segs
    )
    assert total_samples <= len(pcm)
    assert total_samples > 0
    assert all(s["ms"] >= 1 for s in segs)


def test_split_empty_phone_ids():
    pcm = np.array([1, 2, 3], dtype=np.int16)
    assert split_pcm_by_phonemes(pcm, [], sample_rate=24000, id_to_sym={}) == []


def test_split_unknown_phoneme_id():
    sr = 16000
    pcm = np.zeros(160, dtype=np.int16)
    segs = split_pcm_by_phonemes(pcm, [99], sample_rate=sr, id_to_sym={})
    assert len(segs) == 1
    assert segs[0]["phoneme"] == "99"
    assert segs[0]["ms"] >= 1
