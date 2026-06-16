#ifndef DESKBOT_WIFI_PROVISION_H
#define DESKBOT_WIFI_PROVISION_H

/** 连接 WiFi：扫描匹配已保存凭证（最多 10 组）→ deskbot_config.h 默认 → 热点 Deskbot_Rom 配网。成功返回 true。 */
bool wifi_provision_connect();

/** 清除已保存 WiFi 并重启（串口 factory reset_wifi）。 */
void wifi_provision_reset();

#endif
