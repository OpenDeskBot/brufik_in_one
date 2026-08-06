#include "mic.h"

#include "speaker.h"
#include "deskbot_config.h"
#include "logger.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <ESP_I2S.h>
#include <atomic>
#include <esp_heap_caps.h>
#include <math.h>
#include <opus.h>
#include <string.h>

#include "freertos/task.h"

namespace {

constexpr size_t kUplinkBatchFrames = 5;
constexpr size_t kUplinkBatchMaxBin = kUplinkBatchFrames * (2 + 256);
constexpr int kOpusSr = SAMPLE_RATE;
constexpr int kOpusChannels = 1;
constexpr uint32_t kMicTaskStack = 28 * 1024; /* opus_encode alloca；complexity=0 仍建议 ≥32–40KB */
constexpr unsigned long kSegmentFlushMs =
    (unsigned long)DESKBOT_UPLINK_MAX_SEC * 1000UL; /* 默认 30s */

/* Arduino 3.x：legacy driver/i2s.h PDM 在 IDF5 上常读到 0 字节；改走 ESP_I2S。 */
I2SClass s_i2s(I2S_NUM_0);

TaskHandle_t s_task = nullptr;

std::atomic<MicSpeakerState> state_speaker{kMicSpeakEnd};
std::atomic<MicWsState> state_ws{kMicWsError};

uint8_t s_batch_bin[kUplinkBatchMaxBin];
size_t s_batch_bin_len = 0;
uint8_t s_batch_count = 0;
volatile uint32_t s_samples_sent = 0;

OpusEncoder* opus_encoder = nullptr;

int16_t s_hpf_prev_in = 0;
float s_hpf_prev_out = 0.0f;

/* 门控打开后的段计时：≥30s 自动 flush 一轮。 */
unsigned long s_segment_start_ms = 0;

/* 1s 诊断：证明编码/入队是否在持续跑。 */
uint32_t s_stat_encode_ok = 0;
uint32_t s_stat_enq_ok = 0;
uint32_t s_stat_enq_fail = 0;
uint32_t s_stat_gate_ws = 0;
uint32_t s_stat_gate_spk = 0;
uint32_t s_stat_batch_drop = 0;
unsigned long s_stat_log_ms = 0;

void mic_task(void* arg);

}  // namespace

bool setup_mic() {
  s_i2s.setPinsPdmRx((int8_t)PDM_MIC_CLK, (int8_t)PDM_MIC_DATA);
  if (!s_i2s.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    log_error("[MIC] ESP_I2S PDM_RX begin failed CLK=%d DATA=%d", (int)PDM_MIC_CLK,
              (int)PDM_MIC_DATA);
    return false;
  }
  s_i2s.setTimeout(100);

  int oerr = OPUS_OK;
  opus_encoder = opus_encoder_create(kOpusSr, kOpusChannels, OPUS_APPLICATION_VOIP, &oerr);
  if (oerr != OPUS_OK || opus_encoder == nullptr) {
    log_error("[MIC] Opus encoder create failed err=%d", oerr);
    opus_encoder = nullptr;
    s_i2s.end();
    return false;
  }
  opus_encoder_ctl(opus_encoder, OPUS_SET_COMPLEXITY(0));
  opus_encoder_ctl(opus_encoder, OPUS_SET_BITRATE(24000));

  log_info("[MIC] setup ok ESP_I2S PDM CLK=%d DATA=%d %uHz opus_encoder ready", (int)PDM_MIC_CLK,
           (int)PDM_MIC_DATA, (unsigned)SAMPLE_RATE);
  return true;
}

void task_setup_mic() {
  if (s_task) {
    return;
  }
  /* 40KB：opus_encode 栈；须在 display 之前创建，并靠缩小 loopTask 腾出内部 RAM。 */
  BaseType_t rc =
      utils_task_create_pinned(mic_task, "mic", kMicTaskStack, nullptr, 6, &s_task, APP_CPU_NUM);
  if (rc != pdPASS) {
    log_error("[MIC] task create failed rc=%d (internal free=%u psram free=%u)", (int)rc,
              (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
              (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    s_task = nullptr;
  } else {
    log_info("[MIC] task OK stack=%u batch=%u segment_flush=%lus", (unsigned)kMicTaskStack,
             (unsigned)kUplinkBatchFrames, (unsigned long)(kSegmentFlushMs / 1000UL));
  }
}

namespace {

size_t opus_encode_frame(const int16_t* pcm, uint8_t* out_buf, size_t out_cap) {
  if (!pcm || !out_buf || out_cap == 0 || !opus_encoder) {
    return 0;
  }
  const opus_int32 n =
      opus_encode(opus_encoder, pcm, (int)kMicFrameSamples, out_buf, (opus_int32)out_cap);
  if (n < 0) {
    log_warn("[MIC] opus_encode failed: %s", opus_strerror(n));
    return 0;
  }
  return (size_t)n;
}

void reset_segment_after_flush() {
  s_samples_sent = 0;
  s_segment_start_ms = millis();
  if (opus_encoder) {
    opus_encoder_ctl(opus_encoder, OPUS_RESET_STATE);
  }
  enhance_voice_reset();
}

/** 将当前 batch 入队；flush=true 时 hdr 带 "flush":1。成功后清空 batch。 */
bool enqueue_batch(bool flush) {
  if (s_batch_count == 0 || s_batch_bin_len == 0) {
    return true;
  }

  char hdr[160];
  if (flush) {
    snprintf(hdr, sizeof(hdr),
             "{\"type\":\"audio\",\"codec\":\"opus\",\"next_bin_len\":%u,\"sr\":16000,\"ch\":1,"
             "\"frames\":%u,\"flush\":1}",
             (unsigned)s_batch_bin_len, (unsigned)s_batch_count);
  } else {
    snprintf(hdr, sizeof(hdr),
             "{\"type\":\"audio\",\"codec\":\"opus\",\"next_bin_len\":%u,\"sr\":16000,\"ch\":1,"
             "\"frames\":%u}",
             (unsigned)s_batch_bin_len, (unsigned)s_batch_count);
  }
  if (!ws_transport_enqueue_audio(hdr, s_batch_bin, s_batch_bin_len)) {
    ++s_stat_enq_fail;
    return false;
  }
  ++s_stat_enq_ok;
  s_samples_sent += (uint32_t)s_batch_count * (uint32_t)kMicFrameSamples;
  s_batch_bin_len = 0;
  s_batch_count = 0;
  return true;
}

/**
 * TX 暂不可用时丢掉卡住的满 batch，让编码链路继续跑（保活、偏向最新音频）。
 * 旧逻辑会 return false 且不编码新帧 → 整条 mic 上行停转，直到偶然腾出 TX 槽。
 */
void drop_stuck_batch_for_live(const char* why) {
  if (s_batch_count == 0 && s_batch_bin_len == 0) {
    return;
  }
  ++s_stat_batch_drop;
  s_batch_bin_len = 0;
  s_batch_count = 0;
  (void)why;
}

/**
 * 编码 pcm（可空）入 batch；仅在 flush=true 或满 5 帧时发送。
 * flush=true：不足 5 帧也发，JSON 带 "flush":1；batch 空但本段已发过音时发 flush-only。
 */
bool send_to_ws(const int16_t* pcm, bool flush) {
  /* 满 batch 发不出去时：丢掉旧 batch，继续编当前帧（持续上行，不卡死编码）。 */
  if (s_batch_count >= kUplinkBatchFrames) {
    if (!enqueue_batch(flush)) {
      drop_stuck_batch_for_live(flush ? "flush" : "live");
      if (flush) {
        /* flush 仍发不出去：段结束，避免卡在满 batch。 */
        reset_segment_after_flush();
        return false;
      }
      /* 非 flush：清空后落入下方，编码本帧。 */
    } else if (flush) {
      reset_segment_after_flush();
      return true;
    }
  }

  if (pcm != nullptr) {
    uint8_t opus_buf[256];
    const size_t opus_len = opus_encode_frame(pcm, opus_buf, sizeof(opus_buf));
    if (opus_len == 0) {
      return false;
    }
    if (opus_len > 65535U || s_batch_bin_len + 2U + opus_len > kUplinkBatchMaxBin) {
      log_warn("[MIC] Opus batch overflow len=%u bin=%u", (unsigned)opus_len,
               (unsigned)s_batch_bin_len);
      return false;
    }
    s_batch_bin[s_batch_bin_len++] = static_cast<uint8_t>((opus_len >> 8) & 0xFF);
    s_batch_bin[s_batch_bin_len++] = static_cast<uint8_t>(opus_len & 0xFF);
    memcpy(s_batch_bin + s_batch_bin_len, opus_buf, opus_len);
    s_batch_bin_len += opus_len;
    s_batch_count++;
    ++s_stat_encode_ok;
  }

  /* flush=true → 立刻发（可不足 5 帧，hdr 带 flush:1）。 */
  if (flush) {
    if (s_batch_count > 0) {
      if (!enqueue_batch(true)) {
        drop_stuck_batch_for_live("flush_tail");
        reset_segment_after_flush();
        return false;
      }
      reset_segment_after_flush();
      return true;
    }
    if (s_samples_sent > 0) {
      if (!ws_transport_enqueue_audio(
              "{\"type\":\"audio\",\"codec\":\"opus\",\"next_bin_len\":0,\"sr\":16000,\"ch\":1,"
              "\"frames\":0,\"flush\":1}",
              nullptr, 0)) {
        ++s_stat_enq_fail;
        log_warn("[MIC] enqueue flush-only failed");
        reset_segment_after_flush();
        return false;
      }
      ++s_stat_enq_ok;
    }
    reset_segment_after_flush();
    return true;
  }

  /* 非 flush：仅满 5 帧才发。 */
  if (s_batch_count >= kUplinkBatchFrames) {
    if (!enqueue_batch(false)) {
      drop_stuck_batch_for_live("full");
      return false;
    }
  }
  return true;
}

void discard_batch() {
  s_batch_bin_len = 0;
  s_batch_count = 0;
  enhance_voice_reset();
}

void mic_task(void* /*arg*/) {
  MicFrame frame;
  /* 上一帧是否在采：用于开门沿重置 encoder/段计时，避免每帧都 reset。 */
  bool was_open = false;
  MicSpeakerState prev_spk = state_speaker.load(std::memory_order_relaxed);
  MicWsState prev_ws = state_ws.load(std::memory_order_relaxed);

  for (;;) {
    size_t bytes_read =
        s_i2s.readBytes(reinterpret_cast<char*>(frame.pcm), kMicFrameSamples * sizeof(int16_t));
    if (bytes_read < kMicFrameSamples * sizeof(int16_t)) {
      static uint32_t s_last_i2s_err_ms = 0;
      const uint32_t now_err = millis();
      if (s_last_i2s_err_ms == 0 || (uint32_t)(now_err - s_last_i2s_err_ms) >= 2000u) {
        s_last_i2s_err_ms = now_err;
        log_warn("[MIC] i2s_read short bytes=%u need=%u", (unsigned)bytes_read,
                 (unsigned)(kMicFrameSamples * sizeof(int16_t)));
      }
      continue;
    }

    const MicSpeakerState spk = state_speaker.load(std::memory_order_acquire);
    const MicWsState ws = state_ws.load(std::memory_order_acquire);

    if (ws != prev_ws) {
      log_warn("[MIC] gate ws %s → %s", prev_ws == kMicWsOk ? "ok" : "error",
               ws == kMicWsOk ? "ok" : "error");
      prev_ws = ws;
    }
    if (spk != prev_spk && ws == kMicWsOk) {
      log_warn("[MIC] gate speak %s → %s", prev_spk == kMicSpeakEnd ? "end" : "start",
               spk == kMicSpeakEnd ? "end" : "start");
    }

    const unsigned long now = millis();
    if (s_stat_log_ms == 0) {
      s_stat_log_ms = now;
    } else if ((now - s_stat_log_ms) >= 1000UL) {
      log_warn("[MIC/1s] enc=%u enq_ok=%u enq_fail=%u batch_drop=%u gate_ws=%u gate_spk=%u "
               "ws=%s speak=%s batch=%u",
               (unsigned)s_stat_encode_ok, (unsigned)s_stat_enq_ok, (unsigned)s_stat_enq_fail,
               (unsigned)s_stat_batch_drop, (unsigned)s_stat_gate_ws, (unsigned)s_stat_gate_spk,
               ws == kMicWsOk ? "ok" : "err", spk == kMicSpeakEnd ? "end" : "start",
               (unsigned)s_batch_count);
      s_stat_encode_ok = 0;
      s_stat_enq_ok = 0;
      s_stat_enq_fail = 0;
      s_stat_batch_drop = 0;
      s_stat_gate_ws = 0;
      s_stat_gate_spk = 0;
      s_stat_log_ms = now;
    }

    /* ws 不可用：清空积累 + 丢掉当次帧（条件靠前）。 */
    if (ws == kMicWsError) {
      ++s_stat_gate_ws;
      discard_batch();
      s_samples_sent = 0;
      s_segment_start_ms = 0;
      was_open = false;
      prev_spk = spk;
      continue;
    }

    /* speak_start：边沿冲出不足 5 帧并 flush:1；之后各帧只丢弃当次、不再重复 flush。 */
    if (spk == kMicSpeakStart) {
      ++s_stat_gate_spk;
      if (prev_spk == kMicSpeakEnd) {
        (void)send_to_ws(nullptr, /*flush=*/true);
      }
      was_open = false;
      s_segment_start_ms = 0;
      prev_spk = spk;
      continue;
    }
    prev_spk = spk;

    /* speak_end && ws_ok：正常采上行。 */
    if (!was_open) {
      enhance_voice_reset();
      if (opus_encoder) {
        opus_encoder_ctl(opus_encoder, OPUS_RESET_STATE);
      }
      s_samples_sent = 0;
      s_segment_start_ms = millis();
      was_open = true;
      log_warn("[MIC] uplink open (ws_ok, speak_end)");
    }

    enhance_voice(frame.pcm, kMicFrameSamples);
    (void)send_to_ws(frame.pcm, /*flush=*/false);

    if (s_segment_start_ms != 0 && (millis() - s_segment_start_ms) >= kSegmentFlushMs) {
      (void)send_to_ws(nullptr, /*flush=*/true);
    }
  }
}

}  // namespace

void mic_set_speaker_state(MicSpeakerState s) {
  state_speaker.store(s, std::memory_order_release);
}

void mic_set_ws_state(MicWsState s) {
  state_ws.store(s, std::memory_order_release);
}

uint32_t mic_uplink_samples_sent(void) {
  return s_samples_sent;
}

bool mic_capture_allowed(void) {
  return state_speaker.load(std::memory_order_relaxed) == kMicSpeakEnd &&
         state_ws.load(std::memory_order_relaxed) == kMicWsOk;
}

void enhance_voice_reset(void) {
  s_hpf_prev_in = 0;
  s_hpf_prev_out = 0.0f;
}

void enhance_voice(int16_t* data, size_t length) {
  constexpr float kAlpha = 0.969f;
  constexpr int kGain = 5;
  if (data == nullptr || length == 0) {
    return;
  }
  for (size_t i = 0; i < length; ++i) {
    const int16_t x = data[i];
    const float y =
        kAlpha * (s_hpf_prev_out + static_cast<float>(x) - static_cast<float>(s_hpf_prev_in));
    s_hpf_prev_in = x;
    s_hpf_prev_out = y;
    data[i] = static_cast<int16_t>(
        constrain(static_cast<int>(lroundf(y * static_cast<float>(kGain))), -32768, 32767));
  }
}
