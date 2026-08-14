#include "utils.h"

#include "deskbot_config.h"
#include "logger.h"
#include "nvs_config_utils.h"

#include <FFat.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_mac.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace {
constexpr size_t kMaxPackedJsonLen = DESKBOT_MAX_PACKED_JSON_LEN;
}

bool parse_packed_frame(uint8_t* data, size_t length, PackedFrame& out) {
  out.bin = nullptr;
  out.bin_len = 0;
  if (data == nullptr || length < 4) {
    log_warn("[UTILS] packed frame too short len=%u", (unsigned)length);
    return false;
  }
  const size_t json_len =
      ((size_t)data[0] << 24) | ((size_t)data[1] << 16) | ((size_t)data[2] << 8) | (size_t)data[3];
  if (json_len == 0 || json_len > kMaxPackedJsonLen || 4 + json_len > length) {
    log_warn("[UTILS] packed json_len invalid %u total=%u", (unsigned)json_len, (unsigned)length);
    return false;
  }
  if (data[4] != '{') {
    log_warn("[UTILS] packed frame json does not start with '{'");
    return false;
  }
  const DeserializationError jerr = deserializeJson(out.doc, data + 4, json_len);
  if (jerr) {
    log_warn("[UTILS] packed deserialize failed len=%u err=%s", (unsigned)json_len, jerr.c_str());
    return false;
  }
  out.bin = data + 4 + json_len;
  out.bin_len = static_cast<int>(length - 4 - json_len);
  return true;
}

uint8_t* new_packed_bin(const char* json, const uint8_t* bin, size_t bin_len, size_t* out_len) {
  if (out_len) {
    *out_len = 0;
  }
  if (!json) {
    return nullptr;
  }
  const size_t json_len = strlen(json);
  if (json_len == 0 || json_len > kMaxPackedJsonLen) {
    log_warn("[UTILS] new_packed_bin bad json_len=%u", (unsigned)json_len);
    return nullptr;
  }
  if (bin_len > 0 && bin == nullptr) {
    return nullptr;
  }
  const size_t total = 4u + json_len + bin_len;
  uint8_t* frame = (uint8_t*)heap_caps_malloc(total, MALLOC_CAP_SPIRAM);
  if (!frame) {
    log_warn("[UTILS] new_packed_bin alloc fail total=%u", (unsigned)total);
    return nullptr;
  }
  frame[0] = (uint8_t)((json_len >> 24) & 0xFFu);
  frame[1] = (uint8_t)((json_len >> 16) & 0xFFu);
  frame[2] = (uint8_t)((json_len >> 8) & 0xFFu);
  frame[3] = (uint8_t)(json_len & 0xFFu);
  memcpy(frame + 4, json, json_len);
  if (bin_len > 0) {
    memcpy(frame + 4 + json_len, bin, bin_len);
  }
  if (out_len) {
    *out_len = total;
  }
  return frame;
}

void setup_FFat() {
  if (!FFat.begin(true)) {
    log_error("[FFAT] begin failed (check partition deskbot_rom_8MB.csv); FS unavailable");
    return;
  }
  log_info("[FFAT] ready");
}

const char* get_device_id() {
  static char id[32];
  static bool initialized = false;
  if (!initialized) {
    uint8_t mac[6] = {0};
    (void)esp_read_mac(mac, ESP_MAC_WIFI_STA);
    /* 格式：brfk_ + mac(12位hex) + 随机4位数字，后缀存 NVS 保持重启不变 */
    uint32_t suffix = nvs_get_device_suffix();
    snprintf(id, sizeof(id), "brfk_%02x%02x%02x%02x%02x%02x%04u", mac[0], mac[1], mac[2], mac[3],
             mac[4], mac[5], suffix);
    initialized = true;
  }
  return id;
}

String json_escape(const String& raw) {
  String out;
  out.reserve(raw.length() + 8);
  for (size_t i = 0; i < raw.length(); ++i) {
    const char c = raw.charAt(i);
    switch (c) {
      case '\\':
        out += "\\\\";
        break;
      case '"':
        out += "\\\"";
        break;
      case '\b':
        out += "\\b";
        break;
      case '\f':
        out += "\\f";
        break;
      case '\n':
        out += "\\n";
        break;
      case '\r':
        out += "\\r";
        break;
      case '\t':
        out += "\\t";
        break;
      default:
        if ((uint8_t)c < 0x20) {
          char buf[7];
          snprintf(buf, sizeof(buf), "\\u%04x", (unsigned)c);
          out += buf;
        } else {
          out += c;
        }
        break;
    }
  }
  return out;
}

const char* get_server_ws_url() {
  static char url[192];
  url[0] = '\0';

  char base[128];
  base[0] = '\0';
  const char* active = nvs_ws_get_active_id();
  if (strcmp(active, "builtin") == 0) {
    if (DESKBOT_WS_HOST[0] == '\0') {
      return url;
    }
    snprintf(base, sizeof(base), "ws://%s:%u", DESKBOT_WS_HOST, (unsigned)DESKBOT_WS_PORT);
  } else if (!nvs_ws_get_custom_url(active, base, sizeof(base)) || base[0] == '\0') {
    return url;
  }

  /* 去掉 base 末尾多余 '/'，再拼服务路径与鉴权 query。 */
  size_t base_len = strlen(base);
  while (base_len > 0 && base[base_len - 1] == '/') {
    base[--base_len] = '\0';
  }
  snprintf(url, sizeof(url), "%s/asr_chat?device_id=%s&version=%s", base, get_device_id(), VERSION);
  return url;
}

namespace {

bool ws_host_char_valid(char c) {
  return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.'
         || c == '-';
}

}  // namespace

bool parse_ws_proto(const char* str, WsProto& out) {
  out = {};
  if (str == nullptr) {
    return false;
  }

  const char* p = str;
  while (*p == ' ') {
    ++p;
  }
  if (strncmp(p, "wss://", 6) == 0) {
    out.is_wss = true;
    p += 6;
  } else if (strncmp(p, "ws://", 5) == 0) {
    out.is_wss = false;
    p += 5;
  } else {
    return false;
  }

  const char* host_start = p;
  const char* colon = strchr(p, ':');
  const char* slash = strchr(p, '/');
  const char* host_end = nullptr;
  const uint16_t default_port = out.is_wss ? 443 : 80;

  if (colon != nullptr && (slash == nullptr || colon < slash)) {
    host_end = colon;
    const char* port_start = colon + 1;
    char* port_end = nullptr;
    long port = 0;
    if (*port_start >= '0' && *port_start <= '9') {
      port = strtol(port_start, &port_end, 10);
      p = port_end ? port_end : port_start;
    } else {
      p = port_start;
    }
    out.port = (port > 0 && port <= 65535) ? (uint16_t)port : default_port;
    if (slash != nullptr) {
      p = slash;
    }
  } else {
    host_end = slash != nullptr ? slash : (p + strlen(p));
    out.port = default_port;
    p = slash != nullptr ? slash : host_end;
  }

  if (out.port == 0) {
    out.port = default_port;
  }

  const size_t host_len = (size_t)(host_end - host_start);
  if (host_len == 0 || host_len >= sizeof(out.host)) {
    return false;
  }
  for (size_t i = 0; i < host_len; ++i) {
    if (!ws_host_char_valid(host_start[i])) {
      return false;
    }
  }
  memcpy(out.host, host_start, host_len);
  out.host[host_len] = '\0';

  if (*p == '/') {
    const char* path_start = p;
    const char* path_end = path_start + strlen(path_start);
    while (path_end > path_start && path_end[-1] == '/') {
      --path_end;
    }
    const size_t path_len = (size_t)(path_end - path_start);
    if (path_len >= sizeof(out.path)) {
      return false;
    }
    if (path_len > 0) {
      memcpy(out.path, path_start, path_len);
      out.path[path_len] = '\0';
    }
  }

  return out.host[0] != '\0';
}

bool utils_http_get_binary(const char* url, uint8_t** out_buf, size_t* out_len) {
  if (out_buf) {
    *out_buf = nullptr;
  }
  if (out_len) {
    *out_len = 0;
  }
  if (url == nullptr || url[0] == 0 || out_buf == nullptr || out_len == nullptr) {
    log_error("[UTILS] http_get: bad args");
    return false;
  }

  log_info("[UTILS] HTTP GET %s", url);

  HTTPClient http;
  const bool is_https = (strncmp(url, "https://", 8) == 0);
  WiFiClientSecure secure_client;
  bool begin_ok;
  if (is_https) {
    secure_client.setInsecure();
    begin_ok = http.begin(secure_client, url);
  } else {
    begin_ok = http.begin(url);
  }
  if (!begin_ok) {
    log_error("[UTILS] http.begin failed");
    return false;
  }
  http.setTimeout(60000);

  const int code = http.GET();
  if (code != 200) {
    log_error("[UTILS] HTTP %d", code);
    http.end();
    return false;
  }

  const int clen = http.getSize();
  const size_t cap = (clen > 0 ? (size_t)clen : (size_t)(512 * 1024)) + 16;
  uint8_t* buf = (uint8_t*)heap_caps_malloc(cap, MALLOC_CAP_SPIRAM);
  if (!buf) {
    log_error("[UTILS] PSRAM alloc %u failed (free=%u)", (unsigned)cap,
              (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    http.end();
    return false;
  }

  WiFiClient* stream = http.getStreamPtr();
  size_t got = 0;
  unsigned long t0 = millis();
  constexpr unsigned long kReadTotalMs = 60000;
  while (true) {
    if (clen > 0 && got >= (size_t)clen) {
      break;
    }
    const size_t room = cap - 1 - got;
    if (room == 0) {
      break;
    }
    const int n = stream->available();
    if (n > 0) {
      const int r =
          stream->readBytes(reinterpret_cast<char*>(buf + got), (n > (int)room) ? (int)room : n);
      if (r > 0) {
        got += (size_t)r;
        t0 = millis();
        continue;
      }
    }
    if (clen < 0 && !stream->connected() && stream->available() == 0) {
      break;
    }
    if (millis() - t0 > kReadTotalMs) {
      log_error("[UTILS] read timeout got=%u clen=%d", (unsigned)got, clen);
      break;
    }
    delay(5);
  }
  http.end();
  log_info("[UTILS] body read=%uB (clen=%d)", (unsigned)got, clen);

  if (got == 0) {
    heap_caps_free(buf);
    return false;
  }

  *out_buf = buf;
  *out_len = got;
  return true;
}

BaseType_t utils_task_create_pinned(TaskFunction_t fn, const char* name, uint32_t stack_bytes,
                                    void* arg, UBaseType_t prio, TaskHandle_t* out_handle,
                                    BaseType_t core_id) {
  if (!fn || !name || stack_bytes < 1024) {
    return pdFAIL;
  }
  /*
   * Arduino 3.x / IDF 5.5：CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM=y，大栈放 PSRAM，
   * 避免 mic/speaker/ws/display 吃光内部 SRAM 后 pb_runtime/camera 创建失败。
   * TCB 仍须在内部 RAM。
   */
  StackType_t* stack = static_cast<StackType_t*>(
      heap_caps_malloc(stack_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  StaticTask_t* tcb = static_cast<StaticTask_t*>(
      heap_caps_malloc(sizeof(StaticTask_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  if (stack && tcb) {
    TaskHandle_t handle =
        xTaskCreateStaticPinnedToCore(fn, name, stack_bytes, arg, prio, stack, tcb, core_id);
    if (handle) {
      if (out_handle) {
        *out_handle = handle;
      }
      return pdPASS;
    }
  }
  if (stack) {
    heap_caps_free(stack);
  }
  if (tcb) {
    heap_caps_free(tcb);
  }
  /* 回落动态创建（可能仍走内部堆）。 */
  return xTaskCreatePinnedToCore(fn, name, stack_bytes, arg, prio, out_handle, core_id);
}

void* psram_malloc(size_t sz) {
  void* p = heap_caps_malloc(sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  return p ? p : malloc(sz);
}
