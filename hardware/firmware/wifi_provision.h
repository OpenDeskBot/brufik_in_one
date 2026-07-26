#ifndef DESKBOT_WIFI_PROVISION_H
#define DESKBOT_WIFI_PROVISION_H

typedef void (*WifiLinkHandler)(void);

/** 开机 AP 窗口：热点 SSID=设备 ID，屏幕二维码 + 倒计时（有客户端时暂停）。超时且无连接返回 false。 */
bool wifi_provision_ap_offer(unsigned timeout_ms);

/** 连接 WiFi（STA）：已保存凭证 → deskbot_config.h 默认；不进入配网热点。 */
bool wifi_provision_connect_sta();

/** 阻塞配网门户（http://192.168.4.1/），保存凭证后关闭 AP。 */
void wifi_provision_config_portal();

/** 连接 WiFi：ap_offer → STA → 失败则配网门户（兼容旧调用）。 */
bool wifi_provision_connect();

/** 注册链路回调：WiFi 断线 / 恢复时在主循环上下文触发（供 WS 等上层同步）。 */
void wifi_provision_set_link_handlers(WifiLinkHandler on_down, WifiLinkHandler on_up);

/** 当前 STA 是否已获 IP。 */
bool wifi_provision_is_connected();

/** 主循环调用：断线检测 + 自动重连（快恢非阻塞；全量扫凭证时可能短暂阻塞）。 */
void wifi_provision_maintain();

/** 清除已保存 WiFi 并重启（串口 factory reset_wifi）。 */
void wifi_provision_reset();

#endif
