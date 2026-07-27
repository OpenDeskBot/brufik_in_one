#include "asr_ws.h"

#include "boot_guide.h"
#include "camera.h"
#include "deskbot_config.h"
#include "logger.h"
#include "mic.h"
#include "utils/nvs_config_utils.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <Arduino.h>
#include <WiFi.h>
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <string.h>

WebSocketsClient asr_ws;
static std::atomic<int> g_asr_ws_state{-1};
static volatile bool s_ws_uplink_allowed = false;
static bool s_handlers_registered = false;
static bool s_registered_with_transport = false;
static unsigned long s_reconnect_backoff_ms = 2000;
static unsigned long s_last_reconnect_ms = 0;
static unsigned long s_connected_at_ms = 0;
static unsigned long s_connect_attempt_started_ms = 0;
static uint8_t s_send_fail_streak = 0;
static uint8_t s_connect_fail_streak = 0;
static bool s_disconnect_initiated = false;
static constexpr uint8_t kSendFailReconnectThreshold = 3;
static constexpr unsigned long kReadyTimeoutMs = 15000UL;

static void set_state(int v) {
  g_asr_ws_state.store(v, std::memory_order_release);
}

static void set_ws_uplink_allowed(bool allowed) {
  s_ws_uplink_allowed = allowed;
  mic_set_ws_state(allowed ? kMicWsOk : kMicWsError);
}

static void log_net_context(const char* tag) {
  DeskbotWsTarget target;
  deskbot_ws_get_active(&target);
  const char* scheme = target.use_ssl ? "wss" : "ws";
  if (WiFi.status() == WL_CONNECTED) {
    log_warn("[ASR_WS] net %s wifi=%s ip=%s rssi=%d server=%s://%s:%u",
             tag ? tag : "?",
             WiFi.SSID().c_str(),
             WiFi.localIP().toString().c_str(),
             (int)WiFi.RSSI(),
             scheme,
             target.valid ? target.host : "?",
             target.valid ? (unsigned)target.port : 0U);
  } else {
    log_warn("[ASR_WS] net %s wifi=DISCONNECTED server=%s://%s:%u",
             tag ? tag : "?",
             scheme,
             target.valid ? target.host : "?",
             target.valid ? (unsigned)target.port : 0U);
  }
}

static void mark_disconnected_internal(const char* why) {
  set_state(-1);
  s_connected_at_ms = 0;
  s_connect_attempt_started_ms = 0;
  set_ws_uplink_allowed(false);
  camera_notify_capture(kCamStop);
  if (why && why[0]) {
    log_warn("[ASR_WS] state=-1 (%s)", why);
  }
}

/* TCP 握手阶段失败时，WebSocketsClient 会报 "TCP connection cleanup"。
 * 此时此前的逻辑不递增失败计数，导致固定 5 秒无限重连，耗尽/卡住 lwIP socket。
 * 连续失败后重建 Wi‑Fi 链路，让 TCP PCB 和路由状态一起恢复。 */
static void note_pre_ready_connect_failure(const char* why) {
  if (s_connect_fail_streak < 255) {
    ++s_connect_fail_streak;
  }
  if (s_reconnect_backoff_ms < 30000UL) {
    s_reconnect_backoff_ms *= 2;
    if (s_reconnect_backoff_ms > 30000UL) {
      s_reconnect_backoff_ms = 30000UL;
    }
  }
  log_warn("[ASR_WS] pre-ready failure x%u (%s); retry backoff=%lums",
           (unsigned)s_connect_fail_streak, why ? why : "?", s_reconnect_backoff_ms);
  if (s_connect_fail_streak >= 3) {
    log_warn("[ASR_WS] rebuilding WiFi after %u pre-ready failures",
             (unsigned)s_connect_fail_streak);
    s_connect_fail_streak = 0;
    WiFi.reconnect();
  }
}

static void register_handlers() {
  if (s_handlers_registered) {
    return;
  }
  s_handlers_registered = true;
  asr_ws.onEvent([](WStype_t type, uint8_t* payload, size_t length) {
    if (type == WStype_CONNECTED) {
      set_state(-1);
      s_connected_at_ms = millis();
      set_ws_uplink_allowed(false);
      log_warn("[ASR_WS] TCP connected; awaiting packed ready");
    } else if (type == WStype_DISCONNECTED) {
      const bool was_ready = g_asr_ws_state.load(std::memory_order_acquire) == 0;
      const bool intentional = s_disconnect_initiated;
      s_disconnect_initiated = false;
      const char* reason = (payload != nullptr && length > 0)
                               ? reinterpret_cast<const char*>(payload)
                               : "";
      log_warn("[ASR_WS] disconnected reason_len=%u reason=%.*s", (unsigned)length,
               (int)length, reason);
      const String reason_text(reason, length);
      if (reason_text.indexOf("pin_code_required") >= 0) {
        log_error("[ASR_WS] auth rejected: pin_code missing or invalid");
      } else if (reason_text.indexOf("device_id_required") >= 0) {
        log_error("[ASR_WS] auth rejected: device_id missing");
      } else if (reason_text.indexOf("api_key_required") >= 0) {
        log_error("[ASR_WS] auth rejected: API key missing or invalid (legacy server?)");
      } else if (reason_text.indexOf("quota_exhausted") >= 0) {
        log_error("[ASR_WS] auth rejected: free key daily quota exhausted");
      }
      if (!was_ready && !intentional) {
        note_pre_ready_connect_failure(reason_text.c_str());
      }
      mark_disconnected_internal("disconnected");
      ws_transport_discard_asr_tx();
    } else if (type == WStype_ERROR) {
      const bool was_ready = g_asr_ws_state.load(std::memory_order_acquire) == 0;
      const bool intentional = s_disconnect_initiated;
      s_disconnect_initiated = false;
      log_warn("[ASR_WS] websocket error len=%u detail=%.*s", (unsigned)length, (int)length,
               payload ? reinterpret_cast<const char*>(payload) : "");
      if (!was_ready && !intentional) {
        note_pre_ready_connect_failure("websocket error");
      }
      mark_disconnected_internal("ws error");
      ws_transport_discard_asr_tx();
    } else if (type == WStype_BIN && g_asr_ws_state.load(std::memory_order_acquire) != 0) {
      log_warn("[ASR_WS] RX BIN len=%u (queued for ready/pb parse)", (unsigned)length);
    }
    /* 业务帧（打包 BIN）入 RX 队列；ready 在 ws_transport_drain_rx 处理。 */
    ws_transport_enqueue_rx(type, payload, length);
  });
}

static void disconnect_ws(const char* why) {
  asr_ws.disconnect();
  mark_disconnected_internal(why ? why : "disconnect");
}

void asr_ws_force_reconnect(const char* why) {
  s_send_fail_streak = 0;
  ws_transport_discard_asr_tx();
  log_warn("[ASR_WS] force disconnect (%s)", why ? why : "?");
  log_net_context("force_disconnect");
  s_disconnect_initiated = true;
  asr_ws.disconnect();
  mark_disconnected_internal(why ? why : "force");
  /* 切换 session：drain 丢弃旧 DISCONNECTED，避免风暴。不在此阻塞 drain（可能持有 transport 锁）。 */
  ws_transport_new_session();
  s_last_reconnect_ms = 0;
  s_connect_attempt_started_ms = 0;
}

static void ensure_connected_owner() {
  if (WiFi.status() != WL_CONNECTED) {
    if (g_asr_ws_state.load(std::memory_order_acquire) != -1) {
      mark_disconnected_internal("wifi down");
    }
    return;
  }
  if (!deskbot_ws_is_active_configured()) {
    return;
  }

  register_handlers();
  asr_ws.loop();

  const int st = g_asr_ws_state.load(std::memory_order_acquire);

  if (asr_ws.isConnected() && st == 0) {
    s_connect_attempt_started_ms = 0;
    return;
  }

  if (asr_ws.isConnected()) {
    /*
     * 已连上但未 ready：必须等服务端 ready。
     * 旧逻辑 3s 强制 state=0 会误开 mic/uplink，半开连接上继续灌 TX → sendBIN 失败。
     * 超时则断开重试（不清 force-ready）。
     */
    if (st != 0 && s_connected_at_ms != 0 && (millis() - s_connected_at_ms) > kReadyTimeoutMs) {
      log_warn("[ASR_WS] no ready within %lus, reconnect",
               (unsigned long)(kReadyTimeoutMs / 1000UL));
      log_net_context("no_ready");
      note_pre_ready_connect_failure("ready timeout");
      disconnect_ws("no ready");
      s_connect_attempt_started_ms = 0;
      return;
    }
    return;
  }

  if (st != -1) {
    mark_disconnected_internal("socket closed");
  }

  const unsigned long now = millis();
  if (s_connect_attempt_started_ms != 0) {
    if ((now - s_connect_attempt_started_ms) > (unsigned long)DESKBOT_WS_CONNECT_TIMEOUT_MS) {
      log_warn("[ASR_WS] connect timeout, backoff then retry");
      log_net_context("connect_timeout");
      note_pre_ready_connect_failure("connect timeout");
      disconnect_ws("connect timeout");
      s_connect_attempt_started_ms = 0;
    }
    return;
  }

  if (s_last_reconnect_ms != 0 && (now - s_last_reconnect_ms) < s_reconnect_backoff_ms) {
    return;
  }
  s_last_reconnect_ms = now;
  s_connect_attempt_started_ms = now;

  s_disconnect_initiated = true;
  asr_ws.disconnect();
  set_state(-1);
  s_connected_at_ms = 0;
  set_ws_uplink_allowed(false);
  ws_transport_new_session();

  DeskbotWsTarget target;
  deskbot_ws_get_active(&target);
  char service_path[48];
  char path[96];
  deskbot_ws_build_service_path(service_path, sizeof(service_path), &target, "/asr_chat");
  snprintf(path, sizeof(path), "%s?device_id=%s&pin_code=%s", service_path, get_device_id(),
           nvs_get_pin_code());
  const char* scheme = target.use_ssl ? "wss" : "ws";
  log_warn("[ASR_WS] reconnecting %s://%s:%u%s", scheme, target.host, (unsigned)target.port, path);
  log_net_context("connecting");
  deskbot_ws_client_begin(asr_ws, path);
}

int asr_ws_state(void) {
  return g_asr_ws_state.load(std::memory_order_acquire);
}

bool asr_ws_can_send(void) {
  return asr_ws.isConnected() && asr_ws_state() == 0 && s_ws_uplink_allowed;
}

WebSocketsClient* asr_ws_client(void) {
  return &asr_ws;
}

void asr_ws_on_link_down(const char* why) {
  set_ws_uplink_allowed(false);
  asr_ws_force_reconnect(why ? why : "wifi lost");
}

void asr_ws_on_link_up(void) {
  s_reconnect_backoff_ms = 2000;
  s_last_reconnect_ms = 0;
  s_send_fail_streak = 0;
  s_connect_fail_streak = 0;
  mark_disconnected_internal("link up, need reconnect");
}

void asr_ws_note_send_ok(void) {
  s_send_fail_streak = 0;
}

void asr_ws_note_ready(void) {
  asr_ws.setReconnectInterval(7UL * 24UL * 3600UL * 1000UL);
  s_reconnect_backoff_ms = 2000;
  s_connect_attempt_started_ms = 0;
  s_send_fail_streak = 0;
  s_connect_fail_streak = 0;
  set_state(0);
  set_ws_uplink_allowed(true);
  log_warn("[ASR_WS] ready state=0 → capture_go");
  camera_notify_capture(kCamGo);
}

void asr_ws_note_send_fail(const char* what) {
  if (s_send_fail_streak < 255) {
    ++s_send_fail_streak;
  }
  log_warn("[ASR_WS] send fail %u/%u (%s)", (unsigned)s_send_fail_streak,
           (unsigned)kSendFailReconnectThreshold, what ? what : "?");
  if (s_send_fail_streak >= kSendFailReconnectThreshold) {
    asr_ws_force_reconnect(what ? what : "send streak");
  }
}

void asr_ws_bind_transport(void) {
  register_handlers();
  if (!s_registered_with_transport) {
    ws_transport_set_asr_client(&asr_ws);
    s_registered_with_transport = true;
    set_state(-1);
  }
}

void ws_asr_auto_reconnect(void) {
  if (!s_registered_with_transport) {
    asr_ws_bind_transport();
  }
  ensure_connected_owner();
  if (asr_ws.isConnected()) {
    asr_ws.loop();
  }
}
