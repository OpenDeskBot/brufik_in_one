#ifndef DESKBOT_WIFI_PROVISION_H
#define DESKBOT_WIFI_PROVISION_H

typedef void (*WifiLinkHandler)(void);

/** 连接 WiFi：扫描匹配已保存凭证（最多 10 组）→ deskbot_config.h 默认 → 热点 Deskbot_Rom 配网。成功返回 true。 */
bool wifi_provision_connect();

/** 注册链路回调：WiFi 断线 / 恢复时在主循环上下文触发（供 WS 等上层同步）。 */
void wifi_provision_set_link_handlers(WifiLinkHandler on_down, WifiLinkHandler on_up);

/** 当前 STA 是否已获 IP。 */
bool wifi_provision_is_connected();

/** 主循环调用：保活检测 + 断线自动重连（非阻塞，跨多次调用推进）。 */
void wifi_provision_maintain();

/** 清除已保存 WiFi 并重启（串口 factory reset_wifi）。 */
void wifi_provision_reset();

#endif
