#include "utils.h"

#include "logger.h"

#include <FFat.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
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
    WiFi.mode(WIFI_STA);
    uint8_t mac[6];
    WiFi.macAddress(mac);
    snprintf(id, sizeof(id), "deskbot_%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3],
             mac[4], mac[5]);
    initialized = true;
  }
  return id;
}

namespace {

bool ws_host_char_valid(char c) {
  return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.'
         || c == '-';
}

}  // namespace

bool utils_parse_ws_url(const char* url, DeskbotWsTarget* out) {
  if (url == nullptr || out == nullptr) {
    return false;
  }
  memset(out, 0, sizeof(*out));

  const char* p = url;
  while (*p == ' ') {
    ++p;
  }
  if (strncmp(p, "wss://", 6) == 0) {
    out->use_ssl = true;
    p += 6;
  } else if (strncmp(p, "ws://", 5) == 0) {
    out->use_ssl = false;
    p += 5;
  } else {
    return false;
  }

  const char* host_start = p;
  const char* colon = strchr(p, ':');
  const char* slash = strchr(p, '/');
  const char* host_end = nullptr;

  if (colon != nullptr && (slash == nullptr || colon < slash)) {
    host_end = colon;
    const char* port_start = colon + 1;
    if (*port_start < '0' || *port_start > '9') {
      return false;
    }
    const long port = strtol(port_start, const_cast<char**>(&p), 10);
    if (port <= 0 || port > 65535) {
      return false;
    }
    out->port = (uint16_t)port;
    if (slash != nullptr && p > slash) {
      p = slash;
    }
  } else {
    host_end = slash != nullptr ? slash : (p + strlen(p));
    out->port = out->use_ssl ? 443 : 80;
    if (slash != nullptr) {
      p = slash;
    } else {
      p = host_end;
    }
  }

  const size_t host_len = (size_t)(host_end - host_start);
  if (host_len == 0 || host_len >= sizeof(out->host)) {
    return false;
  }
  for (size_t i = 0; i < host_len; ++i) {
    if (!ws_host_char_valid(host_start[i])) {
      return false;
    }
  }
  memcpy(out->host, host_start, host_len);
  out->host[host_len] = '\0';

  if (*p == '/') {
    const char* path_start = p;
    const char* path_end = path_start + strlen(path_start);
    while (path_end > path_start && path_end[-1] == '/') {
      --path_end;
    }
    const size_t prefix_len = (size_t)(path_end - path_start);
    if (prefix_len >= sizeof(out->path_prefix)) {
      return false;
    }
    if (prefix_len > 0) {
      memcpy(out->path_prefix, path_start, prefix_len);
      out->path_prefix[prefix_len] = '\0';
    }
  }

  out->valid = out->host[0] != '\0' && out->port != 0;
  return out->valid;
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
   * Arduino-ESP32 预编译 FreeRTOS 的 xPortcheckValidStackMem 拒绝 PSRAM 栈
   * （即便 sdkconfig.defaults 写了 SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY，链接的仍是旧配置），
   * Static+PSRAM 会直接 assert 重启，不能当 fallback 用。只走内部 RAM。
   */
  return xTaskCreatePinnedToCore(fn, name, stack_bytes, arg, prio, out_handle, core_id);
}
