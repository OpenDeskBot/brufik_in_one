#include "boot_guide.h"

#include "deskbot_config.h"
#include "display.h"
#include "display_text.h"
#include "logger.h"
#include "utils/deskbot_qrcode.h"
#include "utils/nvs_config_utils.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <Arduino.h>
#include <string.h>

namespace {

void draw_qr_modules(Adafruit_GFX* target, int16_t x0, int16_t y0, QRCode* qr, uint8_t scale,
                     uint16_t fg, uint16_t bg) {
  if (!target || !qr || scale == 0) {
    return;
  }
  const int16_t dim = (int16_t)qr->size * (int16_t)scale;
  target->fillRect(x0, y0, dim, dim, bg);
  for (uint8_t y = 0; y < qr->size; ++y) {
    for (uint8_t x = 0; x < qr->size; ++x) {
      if (!qrcode_getModule(qr, x, y)) {
        continue;
      }
      target->fillRect(x0 + (int16_t)x * (int16_t)scale, y0 + (int16_t)y * (int16_t)scale, scale,
                       scale, fg);
    }
  }
}

int16_t text_ascii_center_x(const char* text, uint8_t text_size) {
  if (!text) {
    return DESKBOT_DISPLAY_BOOT_SX;
  }
  const size_t len = strlen(text);
  return (DESKBOT_DRAW_W - (int16_t)(len * 6u * text_size)) / 2;
}

void draw_brufik_logo(Adafruit_GFX* target, int16_t* y) {
  constexpr uint8_t kLogoSize = 3;
  constexpr int16_t kLogoY = 9;
  constexpr int16_t kBodyGap = 4;
  *y = kLogoY;
  display_text_draw(target, text_ascii_center_x("brufik", kLogoSize), *y, "brufik", kLogoSize,
                    DESKBOT_DISPLAY_COLOR_YELLOW);
  *y += display_text_line_height(kLogoSize) + kBodyGap;
}

void boot_show_status(const char* status_line3, const char* status_line4) {
  char line1[48];
  char line2[40];
  snprintf(line1, sizeof(line1), "%s v%s", PRODUCT_NAME, VERSION);
  snprintf(line2, sizeof(line2), "device_id: %s", get_device_id());

  Adafruit_GFX* target = display_guide_target_begin(true);
  constexpr uint8_t kSize = DESKBOT_DISPLAY_BOOT_TEXT_SIZE;
  const int16_t x0 = DESKBOT_DISPLAY_BOOT_SX;
  const int16_t y0 = DESKBOT_DISPLAY_BOOT_SY0;
  const int16_t dy = display_text_line_height(kSize);
  int16_t row = 0;
  auto draw_row = [&](const char* text) {
    if (!text || text[0] == '\0') {
      return;
    }
    display_text_draw(target, x0, y0 + row * dy, text, kSize, DESKBOT_DISPLAY_COLOR_WHITE);
    row++;
  };
  draw_row(line1);
  draw_row(line2);
  draw_row(status_line3);
  draw_row(status_line4);
  display_guide_target_end();
}

void phase_draw_lines_n(const char* const* lines, size_t count) {
  Adafruit_GFX* target = display_guide_target_begin(true);
  constexpr uint8_t kBodySize = 1;
  constexpr int16_t kBodyGap = 4;
  const int16_t body_h = display_text_line_height(kBodySize);
  const int16_t x0 = DESKBOT_DISPLAY_BOOT_SX;
  int16_t y = 9;

  draw_brufik_logo(target, &y);
  for (size_t i = 0; i < count; ++i) {
    if (!lines[i] || lines[i][0] == '\0') {
      continue;
    }
    display_text_draw(target, x0, y, lines[i], kBodySize, DESKBOT_DISPLAY_COLOR_WHITE);
    y += body_h + kBodyGap;
  }
  display_guide_target_end();
}

void phase_draw_lines(const char* line1, const char* line2, const char* line3, const char* line4) {
  const char* lines[] = {line1, line2, line3, line4};
  phase_draw_lines_n(lines, 4);
}

int16_t s_phase_next_y = 0;
bool s_phase_active = false;

Adafruit_GFX* phase_target(bool clear_screen) {
  const bool restart = clear_screen || !s_phase_active;
  Adafruit_GFX* target = display_guide_target_begin(restart);
  if (restart) {
    s_phase_next_y = 9;
    draw_brufik_logo(target, &s_phase_next_y);
    s_phase_active = true;
  }
  return target;
}

void phase_append_line(Adafruit_GFX* target, const char* line,
                       uint16_t color = DESKBOT_DISPLAY_COLOR_WHITE) {
  if (!target || !line || line[0] == '\0') {
    return;
  }
  constexpr uint8_t kBodySize = 1;
  constexpr int16_t kBodyGap = 4;
  display_text_draw(target, DESKBOT_DISPLAY_BOOT_SX, s_phase_next_y, line, kBodySize, color);
  s_phase_next_y += display_text_line_height(kBodySize) + kBodyGap;
}

void phase_end() { s_phase_active = false; }

}  // namespace

void boot_guide_provision_show(const char* ap_ssid, const char* portal_url, int countdown_sec) {
  if (!ap_ssid || ap_ssid[0] == '\0') {
    return;
  }
  if (!portal_url || portal_url[0] == '\0') {
    portal_url = "http://192.168.4.1/";
  }

  QRCode qrcode;
  uint8_t qrcode_data[qrcode_getBufferSize(3)];
  if (qrcode_initText(&qrcode, qrcode_data, 3, ECC_LOW, portal_url) != 0) {
    boot_show_status("二维码生成失败", portal_url);
    return;
  }

  Adafruit_GFX* target = display_guide_target_begin(true);
  constexpr uint8_t kBodySize = 1;
  constexpr int16_t kBodyGap = 4;
  constexpr uint8_t kQrScale = 4;
  const int16_t body_h = display_text_line_height(kBodySize);
  const int16_t x0 = DESKBOT_DISPLAY_BOOT_SX;
  int16_t y = 0;

  draw_brufik_logo(target, &y);

  char line_buf[96];
  if (countdown_sec >= 0) {
    snprintf(line_buf, sizeof(line_buf), "进入启动模式，需要(%d):", countdown_sec);
  } else {
    snprintf(line_buf, sizeof(line_buf), "进入启动模式，需要(已连接，暂停):");
  }
  display_text_draw(target, x0, y, line_buf, kBodySize, DESKBOT_DISPLAY_COLOR_WHITE);
  y += body_h + kBodyGap;

  snprintf(line_buf, sizeof(line_buf), "(1)连接 WiFi：%s", ap_ssid);
  display_text_draw(target, x0, y, line_buf, kBodySize, DESKBOT_DISPLAY_COLOR_YELLOW);
  y += body_h + kBodyGap;

  snprintf(line_buf, sizeof(line_buf), "(2)访问后台：%s", portal_url);
  display_text_draw(target, x0, y, line_buf, kBodySize, DESKBOT_DISPLAY_COLOR_WHITE);
  y += body_h + kBodyGap;

  const int16_t qr_px = (int16_t)qrcode.size * (int16_t)kQrScale;
  const int16_t qr_x = (DESKBOT_DRAW_W - qr_px) / 2;
  draw_qr_modules(target, qr_x, y, &qrcode, kQrScale, DESKBOT_DISPLAY_COLOR_WHITE,
                  DESKBOT_DISPLAY_COLOR_BLACK);

  display_guide_target_end();
}

void boot_guide_wifi_connecting(const char* ssid) {
  Adafruit_GFX* target = phase_target(true);
  phase_append_line(target, "连接 WiFi...");
  if (ssid && ssid[0] != '\0') {
    char line[40];
    snprintf(line, sizeof(line), "SSID:%.22s", ssid);
    phase_append_line(target, line);
  }
  display_guide_target_end();
}

void boot_guide_wifi_on_connected(const char* ssid, const char* ip) {
  if (!s_phase_active) {
    boot_guide_wifi_result(true, ssid, ip);
    return;
  }

  Adafruit_GFX* target = phase_target(false);
  phase_append_line(target, "WiFi 已连接");
  if (ip && ip[0] != '\0') {
    char line[40];
    snprintf(line, sizeof(line), "IP:%s", ip);
    phase_append_line(target, line);
  }
  char id_line[48];
  snprintf(id_line, sizeof(id_line), "DeviceID:%s", get_device_id());
  phase_append_line(target, id_line);
  char ver_line[28];
  snprintf(ver_line, sizeof(ver_line), "Version:%s", VERSION);
  phase_append_line(target, ver_line, DESKBOT_DISPLAY_COLOR_YELLOW);
  display_guide_target_end();
}

void boot_guide_wifi_result(bool ok, const char* ssid, const char* detail) {
  if (ok) {
    phase_end();
    boot_guide_wifi_connecting(ssid);
    boot_guide_wifi_on_connected(ssid, detail);
    return;
  }

  phase_end();
  char line2[40];
  char line3[40];
  snprintf(line2, sizeof(line2), "WiFi 连接失败");
  if (detail && detail[0] != '\0') {
    snprintf(line3, sizeof(line3), "%s", detail);
  } else {
    line3[0] = '\0';
  }
  phase_draw_lines(line2, line3, nullptr, nullptr);
}

void boot_guide_server_connecting(const char* server_url) {
  phase_end();
  char line2[40];
  char line3[64];
  snprintf(line2, sizeof(line2), "连接云服务器...");
  if (server_url && server_url[0] != '\0') {
    snprintf(line3, sizeof(line3), "%.28s", server_url);
  } else {
    line3[0] = '\0';
  }
  phase_draw_lines(line2, line3, nullptr, nullptr);
}

void boot_guide_server_result(bool ok, const char* detail) {
  char line2[40];
  char line3[48];
  if (ok) {
    snprintf(line2, sizeof(line2), "云服务器已连接");
    if (detail && detail[0] != '\0') {
      snprintf(line3, sizeof(line3), "%.28s", detail);
    } else {
      line3[0] = '\0';
    }
  } else {
    snprintf(line2, sizeof(line2), "云服务器连接失败");
    if (detail && detail[0] != '\0') {
      snprintf(line3, sizeof(line3), "%s", detail);
    } else {
      line3[0] = '\0';
    }
  }
  phase_draw_lines(line2, line3, nullptr, nullptr);
}

void boot_guide_show_ready() {
  Adafruit_GFX* target = display_guide_target_begin(true);
  (void)target;
  display_guide_target_end();
}

bool boot_guide_wait_ws_ready(unsigned timeout_ms) {
  char ws_url[128];
  deskbot_ws_format_active_url(ws_url, sizeof(ws_url));

  if (!deskbot_ws_is_active_configured()) {
    boot_guide_server_result(false, "未配置服务器");
    delay(1200);
    return false;
  }

  boot_guide_server_connecting(ws_url);

  const unsigned long deadline_ms = millis() + timeout_ms;
  while (millis() < deadline_ms) {
    if (ws_transport_ready()) {
      boot_guide_server_result(true, ws_url);
      delay(800);
      return true;
    }
    delay(100);
  }

  boot_guide_server_result(false, "连接超时");
  delay(1200);
  return false;
}

void deskbot_ws_format_builtin_url(char* buf, size_t buf_sz) {
  if (buf == nullptr || buf_sz == 0) {
    return;
  }
  if (DESKBOT_WS_HOST[0] == '\0') {
    snprintf(buf, buf_sz, "(未配置)");
    return;
  }
  snprintf(buf, buf_sz, "ws://%s:%u", DESKBOT_WS_HOST, (unsigned)DESKBOT_WS_PORT);
}

void deskbot_ws_get_active(DeskbotWsTarget* out) {
  if (out == nullptr) {
    return;
  }
  memset(out, 0, sizeof(*out));

  const char* active = nvs_ws_get_active_id();
  if (strcmp(active, "builtin") == 0) {
    if (DESKBOT_WS_HOST[0] == '\0') {
      return;
    }
    strncpy(out->host, DESKBOT_WS_HOST, sizeof(out->host) - 1);
    out->port = DESKBOT_WS_PORT;
    out->use_ssl = false;
    out->path_prefix[0] = '\0';
    out->valid = true;
    return;
  }

  char url[128];
  if (!nvs_ws_get_custom_url(active, url, sizeof(url))) {
    return;
  }
  WsProto proto;
  if (!parse_ws_proto(url, proto)) {
    return;
  }
  out->use_ssl = proto.is_wss;
  strncpy(out->host, proto.host, sizeof(out->host) - 1);
  out->port = proto.port;
  if (proto.path[0] != '\0') {
    strncpy(out->path_prefix, proto.path, sizeof(out->path_prefix) - 1);
    out->path_prefix[sizeof(out->path_prefix) - 1] = '\0';
  }
  out->valid = true;
}

bool deskbot_ws_is_active_configured() {
  DeskbotWsTarget target;
  deskbot_ws_get_active(&target);
  return target.valid;
}

void deskbot_ws_build_service_path(char* buf, size_t buf_sz, const DeskbotWsTarget* target,
                                   const char* service_path) {
  if (buf == nullptr || buf_sz == 0 || target == nullptr || service_path == nullptr) {
    return;
  }
  if (target->path_prefix[0] == '\0') {
    snprintf(buf, buf_sz, "%s", service_path);
    return;
  }
  snprintf(buf, buf_sz, "%s%s", target->path_prefix, service_path);
}

void deskbot_ws_client_begin(WebSocketsClient& client, const char* path_and_query) {
  DeskbotWsTarget target;
  deskbot_ws_get_active(&target);
  if (!target.valid || path_and_query == nullptr) {
    return;
  }

  client.setReconnectInterval(500);
  if (target.use_ssl) {
    client.beginSSL(target.host, target.port, path_and_query);
  } else {
    client.begin(target.host, target.port, path_and_query);
  }
}

void deskbot_ws_format_active_url(char* buf, size_t buf_sz) {
  if (buf == nullptr || buf_sz == 0) {
    return;
  }
  if (strcmp(nvs_ws_get_active_id(), "builtin") == 0) {
    deskbot_ws_format_builtin_url(buf, buf_sz);
    return;
  }
  char url[128];
  if (!nvs_ws_get_custom_url(nvs_ws_get_active_id(), url, sizeof(url))) {
    deskbot_ws_format_builtin_url(buf, buf_sz);
    return;
  }
  strncpy(buf, url, buf_sz - 1);
  buf[buf_sz - 1] = '\0';
}
