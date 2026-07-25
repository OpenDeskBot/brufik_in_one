#include "opus_codec.h"

#include "speaker.h"
#include "logger.h"
#include "opus.h"

/*
 * 下行 decode（loopTask / ws_transport_drain_rx）。
 * 上行 encode 已迁至 mic.cpp（mic 任务内 opus_encode + batch enqueue）。
 *
 * s_dec 非线程安全：仅 loopTask 使用。
 */

namespace {

static OpusDecoder* s_dec = nullptr;
static int s_dec_sr = 0;

static int frame_samples_for_sr(int sr) {
  return sr > 0 ? (sr / 50) : 480;
}

static bool ensure_decoder(int sample_rate) {
  if (s_dec != nullptr && s_dec_sr == sample_rate) {
    return true;
  }
  if (s_dec != nullptr) {
    opus_decoder_destroy(s_dec);
    s_dec = nullptr;
    s_dec_sr = 0;
  }
  int err = OPUS_OK;
  s_dec = opus_decoder_create(sample_rate, 1, &err);
  if (err != OPUS_OK || s_dec == nullptr) {
    log_error("[OPUS] decoder create failed sr=%d err=%d", sample_rate, err);
    return false;
  }
  s_dec_sr = sample_rate;
  log_info("[OPUS] decoder ready sr=%d", sample_rate);
  return true;
}

}  // namespace

bool opus_codec_decode_init(void) {
  return true;
}

void opus_codec_decode_reset(void) {
  if (s_dec != nullptr) {
    opus_decoder_destroy(s_dec);
    s_dec = nullptr;
    s_dec_sr = 0;
  }
}

size_t opus_codec_frame_samples(int sample_rate) {
  return (size_t)frame_samples_for_sr(sample_rate);
}

size_t opus_codec_decode_out_cap(int sample_rate, uint16_t frames) {
  const size_t frame_samples = opus_codec_frame_samples(sample_rate);
  const size_t max_frames = frames > 0 ? frames : 1;
  return max_frames * frame_samples + frame_samples;
}

size_t opus_codec_decode(const uint8_t* payload, size_t len, int sample_rate,
                         int16_t* out_pcm, size_t out_cap_samples) {
  if (payload == nullptr || len == 0 || out_pcm == nullptr || out_cap_samples == 0) {
    return 0;
  }
  if (!ensure_decoder(sample_rate)) {
    return 0;
  }
  const int n =
      opus_decode(s_dec, payload, (opus_int32)len, out_pcm, (int)out_cap_samples, 0);
  if (n < 0) {
    log_warn("[OPUS] decode fail: %s", opus_strerror(n));
    return 0;
  }
  return (size_t)n;
}

size_t opus_codec_decode_batch(const uint8_t* payload, size_t len, int sample_rate,
                               uint16_t frames, int16_t* out_pcm, size_t out_cap_samples) {
  if (payload == nullptr || len == 0 || frames == 0 || out_pcm == nullptr ||
      out_cap_samples == 0) {
    return 0;
  }
  if (!ensure_decoder(sample_rate)) {
    return 0;
  }

  size_t wrote = 0;
  size_t offset = 0;
  for (uint16_t i = 0; i < frames; ++i) {
    if (offset + 2 > len) {
      log_warn("[OPUS] batch frame %u missing hdr", (unsigned)i);
      return 0;
    }
    const uint16_t flen = (uint16_t)((payload[offset] << 8) | payload[offset + 1]);
    offset += 2;
    if (flen == 0 || offset + flen > len) {
      log_warn("[OPUS] batch frame %u bad len=%u", (unsigned)i, (unsigned)flen);
      return 0;
    }
    if (wrote >= out_cap_samples) {
      log_warn("[OPUS] batch out full frame=%u", (unsigned)i);
      return 0;
    }
    const int n = opus_decode(s_dec, payload + offset, (opus_int32)flen, out_pcm + wrote,
                              (int)(out_cap_samples - wrote), 0);
    offset += flen;
    if (n < 0) {
      log_warn("[OPUS] batch decode fail frame=%u: %s", (unsigned)i, opus_strerror(n));
      return 0;
    }
    wrote += (size_t)n;
  }
  if (offset != len) {
    log_warn("[OPUS] batch trailing=%u", (unsigned)(len - offset));
  }
  return wrote;
}
