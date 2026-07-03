#include "opus_downlink.h"

#include "esp_heap_caps.h"
#include "logger.h"
#include "opus.h"

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include <stdlib.h>
#include <string.h>

namespace {

enum JobKind : uint8_t {
  kJobReset = 1,
  kJobDecode = 2,
};

static OpusDecoder* s_dec = nullptr;
static int s_dec_sr = 0;
static TaskHandle_t s_task = nullptr;
static SemaphoreHandle_t s_done = nullptr;

/* silk_decode_core 栈深；勿在 loopTask 上直接 opus_decode。 */
static constexpr uint32_t kDecodeTaskStackBytes = 32768;

static int frame_samples_for_sr(int sr) {
  return sr > 0 ? (sr / 50) : 480;
}

struct DecodeJob {
  JobKind kind = kJobReset;
  uint8_t* payload = nullptr;
  size_t len = 0;
  int sample_rate = 0;
  uint16_t frames = 0;
  int16_t* pcm_out = nullptr;
  size_t out_samples = 0;
  uint32_t pcm_free_caps = MALLOC_CAP_DEFAULT;
  bool ok = false;
};

static DecodeJob s_job;

static bool ensure_decoder_in_task(int sample_rate) {
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
    log_error("[OPUS] downlink decoder create failed sr=%d err=%d", sample_rate, err);
    return false;
  }
  s_dec_sr = sample_rate;
  log_info("[OPUS] downlink decoder ready sr=%d", sample_rate);
  return true;
}

static bool decode_batch_in_task(void) {
  const int frame_samples = frame_samples_for_sr(s_job.sample_rate);
  const size_t max_frames = s_job.frames > 0 ? s_job.frames : 1;
  const size_t cap_samples = max_frames * (size_t)frame_samples + (size_t)frame_samples;
  uint32_t free_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
  int16_t* pcm =
      (int16_t*)heap_caps_malloc(cap_samples * sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (pcm == nullptr) {
    pcm = (int16_t*)heap_caps_malloc(cap_samples * sizeof(int16_t), MALLOC_CAP_DEFAULT);
    free_caps = MALLOC_CAP_DEFAULT;
  }
  if (pcm == nullptr) {
    log_error("[OPUS] downlink pcm alloc fail cap=%u", (unsigned)cap_samples);
    return false;
  }

  size_t wrote = 0;
  const uint8_t* payload = s_job.payload;
  const size_t len = s_job.len;
  const uint16_t frames = s_job.frames;

  if (frames <= 1) {
    const int n = opus_decode(s_dec, payload, (opus_int32)len, pcm + wrote,
                                (int)(cap_samples - wrote), 0);
    if (n < 0) {
      log_warn("[OPUS] downlink decode fail: %s", opus_strerror(n));
      heap_caps_free(pcm);
      return false;
    }
    wrote += (size_t)n;
  } else {
    size_t offset = 0;
    for (uint16_t i = 0; i < frames; ++i) {
      if (offset + 2 > len) {
        log_warn("[OPUS] downlink batch frame %u missing hdr", (unsigned)i);
        heap_caps_free(pcm);
        return false;
      }
      const uint16_t flen = (uint16_t)((payload[offset] << 8) | payload[offset + 1]);
      offset += 2;
      if (flen == 0 || offset + flen > len) {
        log_warn("[OPUS] downlink batch frame %u bad len=%u", (unsigned)i, (unsigned)flen);
        heap_caps_free(pcm);
        return false;
      }
      const int n = opus_decode(s_dec, payload + offset, (opus_int32)flen, pcm + wrote,
                                (int)(cap_samples - wrote), 0);
      offset += flen;
      if (n < 0) {
        log_warn("[OPUS] downlink batch decode fail frame=%u: %s", (unsigned)i, opus_strerror(n));
        heap_caps_free(pcm);
        return false;
      }
      wrote += (size_t)n;
    }
    if (offset != len) {
      log_warn("[OPUS] downlink batch trailing=%u", (unsigned)(len - offset));
    }
  }

  s_job.pcm_out = pcm;
  s_job.out_samples = wrote;
  s_job.pcm_free_caps = free_caps;
  return wrote > 0;
}

static void run_job_in_task(void) {
  s_job.ok = false;
  s_job.pcm_out = nullptr;
  s_job.out_samples = 0;

  if (s_job.kind == kJobReset) {
    if (s_dec != nullptr) {
      opus_decoder_destroy(s_dec);
      s_dec = nullptr;
      s_dec_sr = 0;
    }
    s_job.ok = true;
    if (s_job.payload != nullptr) {
      heap_caps_free(s_job.payload);
      s_job.payload = nullptr;
    }
    return;
  }

  if (s_job.kind != kJobDecode || s_job.payload == nullptr || s_job.len == 0) {
    if (s_job.payload != nullptr) {
      heap_caps_free(s_job.payload);
      s_job.payload = nullptr;
    }
    return;
  }

  if (!ensure_decoder_in_task(s_job.sample_rate)) {
    heap_caps_free(s_job.payload);
    s_job.payload = nullptr;
    return;
  }

  s_job.ok = decode_batch_in_task();
  heap_caps_free(s_job.payload);
  s_job.payload = nullptr;
}

static void decode_task_main(void* /*arg*/) {
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    run_job_in_task();
    xSemaphoreGive(s_done);
  }
}

static bool ensure_decode_task(void) {
  if (s_task != nullptr) {
    return true;
  }
  s_done = xSemaphoreCreateBinary();
  if (s_done == nullptr) {
    log_error("[OPUS] downlink done sem alloc failed");
    return false;
  }
  const uint32_t stack_words = kDecodeTaskStackBytes / sizeof(StackType_t);
  const BaseType_t created = xTaskCreatePinnedToCore(
      decode_task_main, "opus_dec", stack_words, nullptr, 5, &s_task, 0);
  if (created != pdPASS || s_task == nullptr) {
    log_error("[OPUS] downlink task create failed stack_words=%u", (unsigned)stack_words);
    s_task = nullptr;
    return false;
  }
  log_warn("[OPUS] downlink decode task started stack=%uB", (unsigned)kDecodeTaskStackBytes);
  return true;
}

static bool dispatch_decode_job(unsigned long wait_ms) {
  xTaskNotifyGive(s_task);
  if (xSemaphoreTake(s_done, pdMS_TO_TICKS(wait_ms)) != pdTRUE) {
    log_warn("[OPUS] downlink decode task timeout");
    return false;
  }
  return s_job.ok;
}

}  // namespace

bool opus_downlink_init(void) {
  return ensure_decode_task();
}

void opus_downlink_reset(void) {
  if (s_task == nullptr) {
    if (s_dec != nullptr) {
      opus_decoder_destroy(s_dec);
      s_dec = nullptr;
      s_dec_sr = 0;
    }
    return;
  }
  s_job.kind = kJobReset;
  s_job.payload = nullptr;
  (void)dispatch_decode_job(500);
}

bool opus_downlink_decode(const uint8_t* payload, size_t len, int sample_rate, uint16_t frames,
                          int16_t** out_pcm, size_t* out_samples, uint32_t* out_free_caps) {
  if (payload == nullptr || len == 0 || out_pcm == nullptr || out_samples == nullptr) {
    return false;
  }
  if (!ensure_decode_task()) {
    return false;
  }

  uint8_t* copy =
      (uint8_t*)heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (copy == nullptr) {
    copy = (uint8_t*)heap_caps_malloc(len, MALLOC_CAP_DEFAULT);
  }
  if (copy == nullptr) {
    log_error("[OPUS] downlink payload copy fail len=%u", (unsigned)len);
    return false;
  }
  memcpy(copy, payload, len);

  s_job.kind = kJobDecode;
  s_job.payload = copy;
  s_job.len = len;
  s_job.sample_rate = sample_rate;
  s_job.frames = frames;
  s_job.pcm_out = nullptr;
  s_job.out_samples = 0;
  s_job.ok = false;

  if (!dispatch_decode_job(8000)) {
    return false;
  }

  *out_pcm = s_job.pcm_out;
  *out_samples = s_job.out_samples;
  if (out_free_caps != nullptr) {
    *out_free_caps = s_job.pcm_free_caps;
  }
  return s_job.out_samples > 0;
}
