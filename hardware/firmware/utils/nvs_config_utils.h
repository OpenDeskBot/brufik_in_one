#ifndef DESKBOT_NVS_CONFIG_UTILS_H
#define DESKBOT_NVS_CONFIG_UTILS_H

#include <stddef.h>
#include <stdint.h>

#include <Arduino.h>

/** NVS 命名空间：deskbot_dev（pin / ap_offer / ws）、deskbot_wifi（凭证列表）。 */

/* ========== PIN / 开机 AP 等待 ========== */

/** 4 位数字 PIN（1000–9999）；NVS 无则生成并持久化。 */
const char* nvs_get_pin_code();

/** 重新生成 PIN 并持久化，返回新 PIN。 */
const char* nvs_reset_pin_code();

/** 开机 AP 配网窗口（秒），默认 20，范围 5–60。 */
unsigned nvs_get_ap_offer_timeout_sec();
unsigned nvs_get_ap_offer_timeout_ms();
bool nvs_set_ap_offer_timeout_sec(unsigned sec);
unsigned nvs_get_ap_offer_timeout_min_sec();
unsigned nvs_get_ap_offer_timeout_max_sec();

/** 重置 PIN 与启动时间至默认；不清理 WiFi / WS。 */
void nvs_device_factory_reset();

/* ========== 云服务器列表 ========== */

#ifndef NVS_MAX_CUSTOM_WS
#define NVS_MAX_CUSTOM_WS 5
#endif

struct NvsWsServerEntry {
  char id[8];
  char url[128];
};

/** 当前选中 id："builtin" 或 "c0"… */
const char* nvs_ws_get_active_id();

bool nvs_ws_set_active_id(const char* id);

/** 按 id 取自定义 URL；builtin 或不存在返回 false。 */
bool nvs_ws_get_custom_url(const char* id, char* out, size_t out_sz);

int nvs_ws_list_custom(NvsWsServerEntry* out, int max_out);

bool nvs_ws_add_custom(const char* url, char* out_id, size_t out_id_sz);

/** 修改已有自定义服务器 URL（id 如 "c0"）。 */
bool nvs_ws_update_custom(const char* id, const char* url);

bool nvs_ws_delete_custom(const char* id);

void nvs_ws_factory_reset();

/* ========== WiFi 凭证列表 ========== */

#ifndef NVS_MAX_SAVED_WIFI
#define NVS_MAX_SAVED_WIFI 10
#endif

struct NvsWifiCredential {
  String ssid;
  String password;
};

int nvs_wifi_list(NvsWifiCredential* out, int max_out);

/** 新增或更新（同名覆盖并置顶）。 */
bool nvs_wifi_upsert(const char* ssid, const char* password);

bool nvs_wifi_delete(const char* ssid);

void nvs_wifi_clear();

#endif
