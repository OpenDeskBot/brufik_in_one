#include "audio_capture.h"

#include "audio_player.h"
#include "deskbot_uplink_state.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/queue.h"

#include <driver/i2s.h>
#include <string.h>

namespace {

QueueHandle_t     s_mic_q           = nullptr;
TaskHandle_t      s_mic_task        = nullptr;
SemaphoreHandle_t s_consumer_mutex = nullptr;

int16_t   s_partial[kMicCaptureFrameSamples];
size_t    s_partial_len = 0;

void mic_enqueue_drop_oldest(const MicCaptureFrame& f) {
  if (!s_mic_q) {
    return;
  }
  while (xQueueSend(s_mic_q, &f, 0) != pdTRUE) {
    MicCaptureFrame discarded{};
    if (xQueueReceive(s_mic_q, &discarded, 0) != pdTRUE) {
      break;
    }
  }
}

void mic_capture_task(void* /*arg*/) {
  MicCaptureFrame frame{};
  for (;;) {
    size_t bytes_read = 0;
    i2s_read(I2S_NUM_0,
               frame.pcm,
               kMicCaptureFrameSamples * sizeof(int16_t),
               &bytes_read,
               portMAX_DELAY);
    /* 容错：极少数情况下对齐不足，读满下一轮补 */
    if (bytes_read < kMicCaptureFrameSamples * sizeof(int16_t)) {
      continue;
    }
    if (deskbot_uplink_capture_allowed()) {
      mic_enqueue_drop_oldest(frame);
    }
  }
}

}  // namespace

void mic_capture_setup() {
  if (s_mic_q && s_mic_task && s_consumer_mutex) {
    return;
  }
  if (!s_mic_q) {
    /* 与 VADNet 10s ring 同量级；burst 改流式后仍留余量防 WS 发送偏慢时丢帧。 */
    constexpr UBaseType_t kDepth = 600;
    s_mic_q = xQueueCreate(kDepth, sizeof(MicCaptureFrame));
  }
  if (!s_consumer_mutex) {
    s_consumer_mutex = xSemaphoreCreateMutex();
  }
  if (!s_mic_task) {
    /* 优先级 6：高于 display(2)/motor(3)，尽快把 I2S DMA 搬进 RAM */
    BaseType_t rc = xTaskCreatePinnedToCore(
        mic_capture_task,
        "mic_cap",
        4096,
        nullptr,
        6,
        &s_mic_task,
        APP_CPU_NUM);
    if (rc != pdPASS) {
      log_error("[MIC] mic_capture_task create failed rc=%d", (int)rc);
    } else {
      log_info("[MIC] PDM capture task OK %uHz %u samp/frame queue_depth=600",
               (unsigned)SAMPLE_RATE, (unsigned)kMicCaptureFrameSamples);
    }
  }
}

void mic_capture_flush_queue() {
  if (!s_mic_q || !s_consumer_mutex) {
    return;
  }
  xSemaphoreTake(s_consumer_mutex, portMAX_DELAY);
  s_partial_len = 0;
  MicCaptureFrame tmp{};
  while (xQueueReceive(s_mic_q, &tmp, 0) == pdTRUE) {
  }
  xSemaphoreGive(s_consumer_mutex);
}

void mic_consumer_read(int16_t* dst, size_t length, TickType_t first_frame_ticks) {
  if (!dst || length == 0) {
    return;
  }
  if (!s_mic_q || !s_consumer_mutex) {
    /* 兜底：未启动捕获任务沿用直接 I2S（不应发生——setup 顺序保证）。 */
    size_t bytes_read = 0;
    i2s_read(I2S_NUM_0, dst, length * sizeof(int16_t), &bytes_read, portMAX_DELAY);
    return;
  }

  xSemaphoreTake(s_consumer_mutex, portMAX_DELAY);

  size_t out = 0;
  TickType_t wait_ticks = first_frame_ticks;

  while (out < length) {
    if (s_partial_len > 0) {
      size_t take = s_partial_len;
      if (take > length - out) {
        take = length - out;
      }
      memcpy(dst + out, s_partial, take * sizeof(int16_t));
      out += take;
      s_partial_len -= take;
      if (s_partial_len > 0) {
        memmove(s_partial, s_partial + take, s_partial_len * sizeof(int16_t));
      }
      wait_ticks = 0;
      continue;
    }

    MicCaptureFrame frame{};
    if (xQueueReceive(s_mic_q, &frame, wait_ticks) != pdTRUE) {
      break;
    }
    wait_ticks = 0;

    size_t frame_remain = kMicCaptureFrameSamples;
    int16_t* fp = frame.pcm;
    while (frame_remain > 0 && out < length) {
      size_t take = frame_remain;
      if (take > length - out) {
        take = length - out;
      }
      memcpy(dst + out, fp, take * sizeof(int16_t));
      out += take;
      fp += take;
      frame_remain -= take;
    }
    if (frame_remain > 0) {
      memcpy(s_partial, fp, frame_remain * sizeof(int16_t));
      s_partial_len = frame_remain;
    }
  }

  xSemaphoreGive(s_consumer_mutex);
}
