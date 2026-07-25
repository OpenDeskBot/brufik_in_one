#include "deskbot_state.h"

#include "deskbot_config.h"
#include "head.h"
#include "logger.h"
#include "speaker.h"
#include "ws_transport.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

static constexpr bool kStateUplinkEnabled = false;
static QueueHandle_t s_notify_q = nullptr;
static uint32_t s_seq = 0;
static unsigned long s_last_send_ms = 0;

static void deskbot_state_task(void*);

void task_setup_deskbot_state() {
  s_notify_q = xQueueCreate(1, sizeof(StateNotify));
  if (!s_notify_q) {
    log_error("[STATE] notify queue failed");
    return;
  }

  BaseType_t ok = xTaskCreatePinnedToCore(deskbot_state_task, "deskbot_state", 4096, nullptr, 1,
                                          nullptr, 0);
  if (ok != pdPASS) {
    log_error("[STATE] task create failed");
    return;
  }
  log_warn("[STATE] uplink task started (notify-queue gated) interval=%ums",
           (unsigned)DESKBOT_STATE_UPLINK_INTERVAL_MS);
}

void deskbot_state_notify(StateNotify n) {
  if (!s_notify_q) {
    return;
  }
  /* 单槽：先清空再放入最新通知。 */
  xQueueReset(s_notify_q);
  (void)xQueueSend(s_notify_q, &n, 0);
}

static bool enqueue_state_once() {
  const int servo_x = head_read_x();
  const int servo_y = head_read_y_logic();
  const int volume = speaker_get_volume();

  s_seq += 1;
  char msg[256];
  const int n = snprintf(
      msg, sizeof(msg),
      "{\"type\":\"device_state\",\"seq\":%u,\"volume\":%d,"
      "\"servo\":{\"x\":%d,\"y\":%d,\"x_min\":%d,\"x_max\":%d,\"y_min\":%d,\"y_max\":%d}}",
      (unsigned)s_seq, volume, servo_x, servo_y, X_MIN_LIMIT, X_MAX_LIMIT, Y_MIN_LIMIT,
      Y_MAX_LIMIT);
  if (n <= 0 || (size_t)n >= sizeof(msg)) {
    log_warn("[STATE] snprintf truncated");
    return false;
  }

  if (!ws_transport_enqueue_state(msg)) {
    return false;
  }
  s_last_send_ms = millis();
  if (s_seq <= 1u || s_seq % 30u == 0u) {
    log_warn("[STATE] enqueue seq=%u servo=(%d,%d) volume=%d", (unsigned)s_seq, servo_x, servo_y,
             volume);
  }
  return true;
}

/** GO 等待间隔；期间若入队 STOP / GO_NOW 则提前返回该通知（未超时则返回 false）。 */
static bool wait_go_interval_or_interrupt(StateNotify* out_interrupt) {
  unsigned long wait_ms = 0;
  if (s_last_send_ms != 0) {
    const unsigned long elapsed = millis() - s_last_send_ms;
    if (elapsed < (unsigned long)DESKBOT_STATE_UPLINK_INTERVAL_MS) {
      wait_ms = (unsigned long)DESKBOT_STATE_UPLINK_INTERVAL_MS - elapsed;
    }
  }
  if (wait_ms == 0) {
    return false;
  }

  const unsigned long deadline = millis() + wait_ms;
  for (;;) {
    const unsigned long now = millis();
    if ((long)(now - deadline) >= 0) {
      return false;
    }
    unsigned long slice = deadline - now;
    if (slice > 100UL) {
      slice = 100UL;
    }
    StateNotify peek;
    if (xQueueReceive(s_notify_q, &peek, pdMS_TO_TICKS(slice)) == pdTRUE) {
      if (peek == kStateStop || peek == kStateGoNow) {
        *out_interrupt = peek;
        return true;
      }
      /* 多余 GO 丢弃，继续等间隔 */
    }
  }
}

static void deskbot_state_task(void*) {
  for (;;) {
    StateNotify n;
    if (xQueueReceive(s_notify_q, &n, portMAX_DELAY) != pdTRUE) {
      continue;
    }

  handle_notify:
    if (n == kStateStop) {
      log_warn("[STATE] uplink stopped");
      continue;
    }

    if (n == kStateGo) {
      StateNotify interrupt = kStateStop;
      if (wait_go_interval_or_interrupt(&interrupt)) {
        n = interrupt;
        goto handle_notify;
      }

      StateNotify peek;
      if (xQueueReceive(s_notify_q, &peek, 0) == pdTRUE) {
        if (peek == kStateStop) {
          log_warn("[STATE] uplink stopped before send");
          continue;
        }
        if (peek == kStateGoNow) {
          n = kStateGoNow;
          goto handle_notify;
        }
        /* 多余 GO 丢弃 */
      }
    }

    /* GO / GO_NOW：发送一次。 */
    if (!enqueue_state_once()) {
      vTaskDelay(pdMS_TO_TICKS(100));
      deskbot_state_notify(n); /* 失败后按原意图重试（GO 或 GO_NOW） */
      continue;
    }

    if (!kStateUplinkEnabled) {
      continue;
    }

    /* GO：成功后继续周期上报；GO_NOW：只发一次。 */
    if (n == kStateGo) {
      deskbot_state_notify(kStateGo);
    }
  }
}
