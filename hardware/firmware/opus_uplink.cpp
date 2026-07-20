#include "opus_uplink.h"

#include "audio_player.h"
#include "esp_heap_caps.h"
#include "logger.h"
#include "opus.h"

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

namespace {

enum JobKind : uint8_t {
  kJobReset = 1,
  kJobEncode = 2,
};

static OpusEncoder* s_enc = nullptr;
static TaskHandle_t s_task = nullptr;
static SemaphoreHandle_t s_done = nullptr;

static constexpr int kSampleRate = SAMPLE_RATE;
static constexpr int kChannels = 1;
static constexpr int kFrameSamples = 320;  /* 20 ms @ 16 kHz */
/* Opus encode 实测需 ~24KB；启用 SPIRAM_ALLOW_STACK_EXTERNAL 后由 FreeRTOS 分配。 */
static constexpr uint32_t kEncodeTaskStackBytes = 24576;

struct EncodeJob {
  JobKind kind = kJobReset;
  const int16_t* pcm = nullptr;
  uint8_t* out_buf = nullptr;
  size_t out_cap = 0;
  size_t out_len = 0;
  bool ok = false;
};

static EncodeJob s_job;

static bool ensure_encoder_in_task(void) {
  if (s_enc != nullptr) {
    return true;
  }
  int err = OPUS_OK;
  s_enc = opus_encoder_create(kSampleRate, kChannels, OPUS_APPLICATION_VOIP, &err);
  if (err != OPUS_OK || s_enc == nullptr) {
    log_error("[OPUS] encoder create failed err=%d", err);
    return false;
  }
  opus_encoder_ctl(s_enc, OPUS_SET_COMPLEXITY(0));
  opus_encoder_ctl(s_enc, OPUS_SET_BITRATE(24000));
  log_info("[OPUS] uplink encoder ready sr=%d ch=%d frame=%dms",
           kSampleRate, kChannels, kFrameSamples * 1000 / kSampleRate);
  return true;
}

static void run_job_in_task(void) {
  s_job.ok = false;
  s_job.out_len = 0;

  if (s_job.kind == kJobReset) {
    if (s_enc != nullptr) {
      opus_encoder_ctl(s_enc, OPUS_RESET_STATE);
    }
    s_job.ok = true;
    return;
  }

  if (s_job.kind != kJobEncode) {
    return;
  }
  if (s_job.pcm == nullptr || s_job.out_buf == nullptr || s_job.out_cap == 0) {
    return;
  }
  if (!ensure_encoder_in_task()) {
    return;
  }
  const opus_int32 n =
      opus_encode(s_enc, s_job.pcm, kFrameSamples, s_job.out_buf,
                  static_cast<opus_int32>(s_job.out_cap));
  if (n < 0) {
    log_warn("[OPUS] encode failed: %s", opus_strerror(n));
    return;
  }
  s_job.out_len = static_cast<size_t>(n);
  s_job.ok = true;
}

static void encode_task_main(void* /*arg*/) {
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    run_job_in_task();
    xSemaphoreGive(s_done);
  }
}

static bool ensure_encode_task(void) {
  if (s_task != nullptr) {
    return true;
  }
  s_done = xSemaphoreCreateBinary();
  if (s_done == nullptr) {
    log_error("[OPUS] done sem alloc failed");
    return false;
  }
  const uint32_t stack_words = kEncodeTaskStackBytes / sizeof(StackType_t);
  const BaseType_t created = xTaskCreatePinnedToCore(
      encode_task_main, "opus_enc", stack_words, nullptr, 5, &s_task, 0);
  if (created != pdPASS || s_task == nullptr) {
    log_error("[OPUS] encode task create failed stack_words=%u free_int=%u free_psram=%u",
              (unsigned)stack_words, (unsigned)ESP.getFreeHeap(),
              (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    s_task = nullptr;
    return false;
  }
  log_warn("[OPUS] encode task started stack=%uB free_int=%u free_psram=%u",
           (unsigned)kEncodeTaskStackBytes, (unsigned)ESP.getFreeHeap(),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
  return true;
}

static bool dispatch_job(JobKind kind, unsigned long wait_ms) {
  if (!ensure_encode_task()) {
    return false;
  }
  s_job.kind = kind;
  s_job.pcm = nullptr;
  s_job.out_buf = nullptr;
  s_job.out_cap = 0;
  s_job.out_len = 0;
  s_job.ok = false;
  xTaskNotifyGive(s_task);
  if (xSemaphoreTake(s_done, pdMS_TO_TICKS(wait_ms)) != pdTRUE) {
    log_warn("[OPUS] encode task timeout kind=%u", (unsigned)kind);
    return false;
  }
  return s_job.ok;
}

}  // namespace

bool opus_uplink_init(void) {
  return ensure_encode_task();
}

void opus_uplink_reset(void) {
  if (s_task == nullptr) {
    return;
  }
  (void)dispatch_job(kJobReset, 500);
}

size_t opus_uplink_encode(const int16_t* pcm, size_t samples, uint8_t* out_buf, size_t out_cap) {
  if (pcm == nullptr || out_buf == nullptr || out_cap == 0) {
    return 0;
  }
  if (samples != static_cast<size_t>(kFrameSamples)) {
    log_warn("[OPUS] unexpected frame samples=%u (need %d)", (unsigned)samples, kFrameSamples);
    return 0;
  }
  if (!ensure_encode_task()) {
    return 0;
  }
  s_job.kind = kJobEncode;
  s_job.pcm = pcm;
  s_job.out_buf = out_buf;
  s_job.out_cap = out_cap;
  s_job.out_len = 0;
  s_job.ok = false;
  xTaskNotifyGive(s_task);
  if (xSemaphoreTake(s_done, pdMS_TO_TICKS(1000)) != pdTRUE) {
    log_warn("[OPUS] encode timeout");
    return 0;
  }
  if (!s_job.ok || s_job.out_len == 0) {
    return 0;
  }
  return s_job.out_len;
}
