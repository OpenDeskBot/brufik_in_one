#include "mic.h"

#include "speaker.h"
#include "deskbot_config.h"
#include "logger.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <atomic>
#include <driver/i2s.h>
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

i2s_config_t s_i2s_cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = i2s_bits_per_sample_t(16),
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = static_cast<int>(kMicFrameSamples),
};

const i2s_pin_config_t s_i2s_pins = {
    .bck_io_num = I2S_PIN_NO_CHANGE,
    .ws_io_num = PDM_MIC_CLK,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = PDM_MIC_DATA,
};

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

void mic_task(void* arg);

}  // namespace

bool setup_mic() {
  esp_err_t err = i2s_driver_install(I2S_NUM_0, &s_i2s_cfg, 0, NULL);
  if (err != ESP_OK) {
    log_error("[MIC] I2S0 PDM install failed err=%d", (int)err);
    return false;
  }
  i2s_set_pin(I2S_NUM_0, &s_i2s_pins);
#if SOC_I2S_SUPPORTS_PDM_RX
  i2s_set_pdm_rx_down_sample(I2S_NUM_0, I2S_PDM_DSR_8S);
#endif
  i2s_set_clk(I2S_NUM_0, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);

  int oerr = OPUS_OK;
  opus_encoder = opus_encoder_create(kOpusSr, kOpusChannels, OPUS_APPLICATION_VOIP, &oerr);
  if (oerr != OPUS_OK || opus_encoder == nullptr) {
    log_error("[MIC] Opus encoder create failed err=%d", oerr);
    opus_encoder = nullptr;
    return false;
  }
  opus_encoder_ctl(opus_encoder, OPUS_SET_COMPLEXITY(0));
  opus_encoder_ctl(opus_encoder, OPUS_SET_BITRATE(24000));

  log_info("[MIC] setup ok PDM CLK=%d DATA=%d %uHz opus_encoder ready", (int)PDM_MIC_CLK,
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
  if (ws_transport_tx_slots_free() == 0) {
    return false;
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
    static unsigned long s_enq_fail_log_ms = 0;
    static uint32_t s_enq_fail_n = 0;
    ++s_enq_fail_n;
    const unsigned long now = millis();
    if (s_enq_fail_log_ms == 0 || (now - s_enq_fail_log_ms) >= 1000UL) {
      log_warn("[MIC] ws_transport_enqueue_audio failed x%u bin_len=%u frames=%u flush=%d free=%u",
               (unsigned)s_enq_fail_n, (unsigned)s_batch_bin_len, (unsigned)s_batch_count,
               (int)flush, (unsigned)ws_transport_tx_slots_free());
      s_enq_fail_n = 0;
      s_enq_fail_log_ms = now;
    }
    return false;
  }
  s_samples_sent += (uint32_t)s_batch_count * (uint32_t)kMicFrameSamples;
  s_batch_bin_len = 0;
  s_batch_count = 0;
  return true;
}

/**
 * 编码 pcm（可空）入 batch；仅在 flush=true 或满 5 帧时发送。
 * flush=true：不足 5 帧也发，JSON 带 "flush":1；batch 空但本段已发过音时发 flush-only。
 */
bool send_to_ws(const int16_t* pcm, bool flush) {
  /* 上一批发不出去时 batch 仍满：先冲（同 flush 标志），本帧可丢。 */
  if (s_batch_count >= kUplinkBatchFrames) {
    if (!enqueue_batch(flush)) {
      return false;
    }
    if (flush) {
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
  }

  /* flush=true → 立刻发（可不足 5 帧，hdr 带 flush:1）。 */
  if (flush) {
    if (s_batch_count > 0) {
      if (!enqueue_batch(true)) {
        return false;
      }
      reset_segment_after_flush();
      return true;
    }
    if (s_samples_sent > 0) {
      if (ws_transport_tx_slots_free() == 0) {
        return false;
      }
      if (!ws_transport_enqueue_audio(
              "{\"type\":\"audio\",\"codec\":\"opus\",\"next_bin_len\":0,\"sr\":16000,\"ch\":1,"
              "\"frames\":0,\"flush\":1}",
              nullptr, 0)) {
        log_warn("[MIC] enqueue flush-only failed");
        return false;
      }
    }
    reset_segment_after_flush();
    return true;
  }

  /* 非 flush：仅满 5 帧才发。 */
  if (s_batch_count >= kUplinkBatchFrames) {
    return enqueue_batch(false);
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

  for (;;) {
    size_t bytes_read = 0;
    const esp_err_t err =
        i2s_read(I2S_NUM_0, frame.pcm, kMicFrameSamples * sizeof(int16_t), &bytes_read,
                 portMAX_DELAY);

    const MicSpeakerState spk = state_speaker.load(std::memory_order_relaxed);
    const MicWsState ws = state_ws.load(std::memory_order_relaxed);

    /* ws 不可用：清空积累 + 丢掉当次帧（条件靠前）。 */
    if (ws == kMicWsError) {
      discard_batch();
      s_samples_sent = 0;
      s_segment_start_ms = 0;
      was_open = false;
      prev_spk = spk;
      continue;
    }

    /* speak_start：边沿冲出不足 5 帧并 flush:1；之后各帧只丢弃当次、不再重复 flush。 */
    if (spk == kMicSpeakStart) {
      if (prev_spk == kMicSpeakEnd) {
        (void)send_to_ws(nullptr, /*flush=*/true);
      }
      was_open = false;
      s_segment_start_ms = 0;
      prev_spk = spk;
      continue;
    }
    prev_spk = spk;

    if (err != ESP_OK || bytes_read < kMicFrameSamples * sizeof(int16_t)) {
      continue;
    }

    /* speak_end && ws_ok：正常采上行。 */
    if (!was_open) {
      enhance_voice_reset();
      if (opus_encoder) {
        opus_encoder_ctl(opus_encoder, OPUS_RESET_STATE);
      }
      s_samples_sent = 0;
      s_segment_start_ms = millis();
      was_open = true;
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
  state_speaker.store(s, std::memory_order_relaxed);
}

void mic_set_ws_state(MicWsState s) {
  state_ws.store(s, std::memory_order_relaxed);
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
