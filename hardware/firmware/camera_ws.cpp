#include "camera_ws.h"

#include "camera.h"
#include "deskbot_config.h"
#include "boot_guide.h"
#include "logger.h"
#include "utils/nvs_config_utils.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <Arduino.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

WebSocketsClient camera_ws;
static std::atomic<int> g_camera_ws_state{-1};
static bool s_handlers_registered = false;
static bool s_registered_with_uplink = false;
static unsigned long s_reconnect_backoff_ms = 2000;
static unsigned long s_last_reconnect_ms = 0;
static unsigned long s_connected_at_ms = 0;
static unsigned long s_connect_attempt_started_ms = 0;

static void set_state(int v) {
  g_camera_ws_state.store(v, std::memory_order_release);
}

static void mark_disconnected_internal(const char* why) {
  set_state(-1);
  s_connected_at_ms = 0;
  s_connect_attempt_started_ms = 0;
  camera_notify_capture(kCamStop);
  if (why && why[0]) {
    log_warn("[CAMERA_WS] state=-1 (%s)", why);
  }
}

static void register_handlers() {
  if (s_handlers_registered) {
    return;
  }
  s_handlers_registered = true;
  camera_ws.onEvent([](WStype_t type, uint8_t* payload, size_t length) {
    if (type == WStype_CONNECTED) {
      set_state(-1);
      s_connected_at_ms = millis();
      return;
    }
    if (type == WStype_DISCONNECTED) {
      mark_disconnected_internal("disconnected");
      return;
    }
    if (type == WStype_ERROR) {
      mark_disconnected_internal("ws error");
      return;
    }
    if (type == WStype_BIN && payload && length >= 5) {
      PackedFrame frame;
      if (parse_packed_frame(payload, length, frame) && frame.doc["type"] == "ready") {
        camera_ws.setReconnectInterval(7UL * 24UL * 3600UL * 1000UL);
        s_reconnect_backoff_ms = 2000;
        s_connect_attempt_started_ms = 0;
        set_state(0);
        log_warn("[CAMERA_WS] ready state=0 → capture_go");
        camera_notify_capture(kCamGo);
      }
    }
  });
}

static void disconnect_ws() {
  camera_ws.disconnect();
  set_state(-1);
  s_connected_at_ms = 0;
  s_connect_attempt_started_ms = 0;
  camera_notify_capture(kCamStop);
}

static bool camera_ws_enabled() {
  return deskbot_camera_uplink_enabled();
}

static void ensure_connected_owner() {
  if (WiFi.status() != WL_CONNECTED || !camera_ws_enabled()) {
    if (g_camera_ws_state.load(std::memory_order_acquire) != -1) {
      set_state(-1);
      camera_notify_capture(kCamStop);
    }
    return;
  }
  if (!deskbot_ws_is_active_configured()) {
    return;
  }

  register_handlers();
  camera_ws.loop();

  const int st = g_camera_ws_state.load(std::memory_order_acquire);

  if (camera_ws.isConnected() && st == 0) {
    s_connect_attempt_started_ms = 0;
    return;
  }

  if (camera_ws.isConnected()) {
    if (st != 0 && s_connected_at_ms != 0 &&
        (millis() - s_connected_at_ms) > 3000UL) {
      log_warn("[CAMERA_WS] no ready JSON, force state=0 → capture_go");
      camera_ws.setReconnectInterval(7UL * 24UL * 3600UL * 1000UL);
      s_reconnect_backoff_ms = 2000;
      s_connect_attempt_started_ms = 0;
      set_state(0);
      camera_notify_capture(kCamGo);
      return;
    }
    return;
  }

  if (st != -1) {
    set_state(-1);
    camera_notify_capture(kCamStop);
  }

  const unsigned long now = millis();
  if (s_connect_attempt_started_ms != 0) {
    if ((now - s_connect_attempt_started_ms) > (unsigned long)DESKBOT_WS_CONNECT_TIMEOUT_MS) {
      log_warn("[CAMERA_WS] connect timeout, backoff then retry");
      disconnect_ws();
      s_connect_attempt_started_ms = 0;
      if (s_reconnect_backoff_ms < 30000UL) {
        s_reconnect_backoff_ms *= 2;
        if (s_reconnect_backoff_ms > 30000UL) {
          s_reconnect_backoff_ms = 30000UL;
        }
      }
    }
    return;
  }

  if (s_last_reconnect_ms != 0 && (now - s_last_reconnect_ms) < s_reconnect_backoff_ms) {
    return;
  }
  s_last_reconnect_ms = now;
  s_connect_attempt_started_ms = now;

  camera_ws.disconnect();
  set_state(-1);
  s_connected_at_ms = 0;

  DeskbotWsTarget target;
  deskbot_ws_get_active(&target);
  char service_path[48];
  char path[112];
  deskbot_ws_build_service_path(service_path, sizeof(service_path), &target, DESKBOT_CAMERA_WS_PATH);
  snprintf(path, sizeof(path), "%s?device_id=%s&pin_code=%s", service_path, get_device_id(),
           nvs_get_pin_code());
  const char* scheme = target.use_ssl ? "wss" : "ws";
  log_warn("[CAMERA_WS] reconnecting %s://%s:%u%s", scheme, target.host, (unsigned)target.port,
           path);
  deskbot_ws_client_begin(camera_ws, path);
}

int camera_ws_state(void) {
  return g_camera_ws_state.load(std::memory_order_acquire);
}

bool camera_ws_try_begin_send(void) {
  int expected = 0;
  return g_camera_ws_state.compare_exchange_strong(expected, 1, std::memory_order_acq_rel,
                                                   std::memory_order_acquire);
}

void camera_ws_end_send_ok(void) {
  int expected = 1;
  (void)g_camera_ws_state.compare_exchange_strong(expected, 0, std::memory_order_acq_rel,
                                                  std::memory_order_acquire);
  camera_notify_capture(kCamGo);
}

void camera_ws_mark_disconnected(void) {
  mark_disconnected_internal("send fail");
}

void camera_ws_on_image_finished(void) {
  /* 成功发送走 end_send_ok；此处用于「扔掉/跳过」仍放行下一帧。 */
  if (g_camera_ws_state.load(std::memory_order_acquire) == 1) {
    int expected = 1;
    (void)g_camera_ws_state.compare_exchange_strong(expected, 0, std::memory_order_acq_rel,
                                                    std::memory_order_acquire);
  }
  if (g_camera_ws_state.load(std::memory_order_acquire) == 0) {
    camera_notify_capture(kCamGo);
  }
}

WebSocketsClient* camera_ws_client(void) {
  return &camera_ws;
}

void ws_camera_auto_reconnect(void) {
  if (!camera_ws_enabled()) {
    return;
  }
  if (!s_registered_with_uplink) {
    ws_transport_set_camera_client(&camera_ws);
    s_registered_with_uplink = true;
    set_state(-1);
  }
  ensure_connected_owner();
  if (camera_ws.isConnected()) {
    camera_ws.loop();
  }
}
