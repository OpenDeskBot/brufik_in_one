"""音素对齐 TTS 的 PCM 切分与 frontend 辅助（纯逻辑，便于单测）。"""

from __future__ import annotations

import base64
import re
from typing import Any, Dict, List

import numpy as np

_ELLIPSIS_RE = re.compile(r"\.{2,}|…+")
_TILDE_RE = re.compile(r"[~～]+")
_PUNCT_RUN_RE = re.compile(r"[,，.。!！?？;；:：、]{2,}")
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _strip_surrogates(text: str) -> str:
    return _SURROGATE_RE.sub("", text)


def sanitize_zh_tts_text(text: str) -> str:
    """避免 ``嗯...`` 等导致 zh_frontend tone_sandhi 空 finals / IndexError。"""
    s = str(text or "").strip()
    if not s:
        return s
    s = _strip_surrogates(s).strip()
    if not s:
        return "。"
    s = _ELLIPSIS_RE.sub("，", s)
    s = _TILDE_RE.sub("", s)
    s = _PUNCT_RUN_RE.sub("，", s)
    s = re.sub(r"\s+", "", s)
    s = s.strip("，。,. ")
    return s if s else "。"


def sanitize_mix_tts_text(text: str) -> str:
    """中英混合：保留英文空格；仅做标点/装饰符清洗。"""
    s = str(text or "").strip()
    if not s:
        return s
    s = _strip_surrogates(s).strip()
    if not s:
        return "。"
    s = _ELLIPSIS_RE.sub("，", s)
    s = _TILDE_RE.sub("", s)
    s = _PUNCT_RUN_RE.sub("，", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip() or "。"


def _flatten_phone_tensors(phone_ids: list) -> List[int]:
    out: List[int] = []
    for t in phone_ids:
        if hasattr(t, "numpy"):
            arr = t.numpy()
        else:
            arr = np.asarray(t)
        out.extend(np.asarray(arr, dtype=np.int64).flatten().tolist())
    return [int(x) for x in out]


def flatten_phone_ids_from_frontend(handler: Any, text: str) -> List[int]:
    fe = handler.executor.frontend
    lang = handler.config.lang
    if lang == "zh":
        input_ids = fe.get_input_ids(
            text, merge_sentences=False, get_tone_ids=False
        )
    elif lang == "en":
        input_ids = fe.get_input_ids(text, merge_sentences=False)
    elif lang == "mix":
        input_ids = fe.get_input_ids(
            text, merge_sentences=False, get_tone_ids=False
        )
    else:
        raise ValueError(f"unsupported TTS lang: {lang}")
    return _flatten_phone_tensors(input_ids.get("phone_ids") or [])


def flatten_phone_ids(handler: Any, text: str) -> List[int]:
    return flatten_phone_ids_from_frontend(handler, text)


def id_to_symbol_map_from_frontend(handler: Any) -> Dict[int, str]:
    fe = handler.executor.frontend
    if hasattr(fe, "zh_frontend"):
        vocab = fe.zh_frontend.vocab_phones
    else:
        vocab = fe.vocab_phones
    return {int(v): str(k) for k, v in vocab.items()}


def id_to_symbol_map(handler: Any) -> Dict[int, str]:
    return id_to_symbol_map_from_frontend(handler)


def collect_pcm_int16_from_wav(
    executor: Any, text: str, spk_id: int, lang: str, am: str
) -> np.ndarray:
    executor.infer_onnx(text=text, lang=lang, am=am, spk_id=spk_id)
    wav = executor._outputs.get("wav")
    if wav is None:
        return np.array([], dtype=np.int16)
    arr = np.asarray(wav, dtype=np.float32).flatten()
    if arr.size == 0:
        return np.array([], dtype=np.int16)
    peak = float(np.max(np.abs(arr))) or 1.0
    if peak > 1.0:
        arr = arr / peak
    return (np.clip(arr, -1.0, 1.0) * 32767.0).astype(np.int16)


def collect_pcm_int16(handler: Any, text: str, spk_id: int) -> np.ndarray:
    chunks: List[np.ndarray] = []
    for wav_b64 in handler.run(sentence=text, spk_id=spk_id):
        raw = base64.b64decode(wav_b64)
        chunks.append(np.frombuffer(raw, dtype=np.int16))
    if not chunks:
        return np.array([], dtype=np.int16)
    return np.concatenate(chunks, axis=0)


def split_pcm_by_phonemes(
    pcm: np.ndarray,
    phone_ids: List[int],
    sample_rate: int,
    id_to_sym: Dict[int, str],
) -> List[Dict[str, Any]]:
    n = len(phone_ids)
    if n == 0:
        return []
    total = int(pcm.shape[0])
    segs: List[Dict[str, Any]] = []
    for i, pid in enumerate(phone_ids):
        start = total * i // n
        end = total * (i + 1) // n
        if i == n - 1:
            end = total
        chunk = pcm[start:end]
        # 与 ``chunk_ms * sr // 1000 * 2`` 字节对齐（均分边界可能多几个 int16，设备会按 chunk_ms 算 expect_len）
        if sample_rate > 0 and chunk.size > 0:
            ns = int(chunk.size)
            ms_floor = ns * 1000 // sample_rate
            exp_samples = ms_floor * sample_rate // 1000
            if exp_samples == 0:
                exp_samples = ns
            elif exp_samples < ns:
                chunk = chunk[:exp_samples]
        ms = (
            max(1, int(chunk.size * 1000 // sample_rate))
            if sample_rate and chunk.size > 0
            else 0
        )
        sym = id_to_sym.get(int(pid), str(int(pid)))
        b64 = base64.b64encode(chunk.tobytes()).decode("ascii")
        segs.append(
            {
                "audio": b64,
                "phoneme_id": int(pid),
                "phoneme": sym,
                "ms": ms,
            }
        )
    return segs
