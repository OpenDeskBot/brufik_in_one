#pragma once

#include <stddef.h>
#include <stdint.h>

#include <WebSocketsClient.h>

#include "utils/utils.h"

/* ========== 开机引导 UI ========== */

/** 配网引导页。countdown_sec >= 0 显示剩余秒；-1 表示已连入、倒计时暂停。 */
void boot_guide_provision_show(const char* ap_ssid, const char* portal_url, int countdown_sec);

void boot_guide_wifi_connecting(const char* ssid);
void boot_guide_wifi_on_connected(const char* ssid, const char* ip);
void boot_guide_wifi_result(bool ok, const char* ssid, const char* detail);

void boot_guide_server_connecting(const char* server_url);
void boot_guide_server_result(bool ok, const char* detail);

/** WiFi 已连接且系统就绪：清屏，交给正常显示任务。 */
void boot_guide_show_ready();

/** 开机阶段等待 ASR WS 就绪（屏幕提示）。 */
bool boot_guide_wait_ws_ready(unsigned timeout_ms);

/* ========== 当前云服务器（builtin + NVS）========== */

void deskbot_ws_format_builtin_url(char* buf, size_t buf_sz);

void deskbot_ws_get_active(DeskbotWsTarget* out);

bool deskbot_ws_is_active_configured();

void deskbot_ws_build_service_path(char* buf, size_t buf_sz, const DeskbotWsTarget* target,
                                   const char* service_path);

void deskbot_ws_client_begin(WebSocketsClient& client, const char* path_and_query);

void deskbot_ws_format_active_url(char* buf, size_t buf_sz);
