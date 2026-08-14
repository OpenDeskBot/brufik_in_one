#pragma once

#include <Arduino.h>
#include <WebSocketsClient.h>
#include <stddef.h>
#include <stdint.h>

#include "utils/utils.h"

/* 单帧 WS 入站上限：platformio.ini WEBSOCKETS_MAX_DATA_SIZE（默认 1MiB）；须大于 PB PCM chunk。 */
#if !defined(WEBSOCKETS_MAX_DATA_SIZE) || WEBSOCKETS_MAX_DATA_SIZE < (200 * 1024)
#error WEBSOCKETS_MAX_DATA_SIZE must be >= 200KiB; set -DWEBSOCKETS_MAX_DATA_SIZE in platformio.ini
#endif

/** TX 类型：state / audio / camera 经 FIFO 发送。 */
enum class WsTxType : uint8_t {
  kState = 0,   // pb_ack / boot_connect / audio_cancel
  kAudio = 1,   // Opus batch / flush
  kCamera = 2,  // JPEG frame (已打包 u32be+json+bin)
};

/** setup 时从 NVS 解析的服务器地址。 */
extern WsProto server_ws_proto;
/** setup 时拼好的 begin 路径：``[path_prefix]/asr_chat?device_id=&version=``。 */
extern String server_ws_path;

/** WS 链路状态：0=disconnected，1=connecting，2=connected。 */
enum class WsState : int {
  kDisconnected = 0,
  kConnecting = 1,
  kConnected = 2,
};

/**
 * 初始化：解析 server_ws_proto / server_ws_path，创建 TX 队列、绑定 WS 回调。
 * 须在 setup_pb_runtime 之后调用。
 */
bool setup_ws_transport(void);

/** 创建 FreeRTOS 任务：每轮 ensure_connected → loop → drain_tx。 */
bool task_setup_ws_transport(void);

/** 当前链路状态。 */
WsState ws_transport_state(void);

/** 是否已 WS connected。 */
bool ws_transport_ok(void);

/** 是否已收到服务端 ready（可业务上行）。 */
bool ws_transport_ready(void);

/** 未连时 disconnect→begin；已连则直接返回。 */
void ws_transport_ensure_connected(void);

/** 非 connected 返回 false；否则 sendBIN，失败则置 disconnected。 */
bool ws_transport_send(const uint8_t* data, size_t len);

/** WiFi 断开 / 恢复：打断或允许重连。 */
void ws_transport_on_link_down(const char* why = nullptr);
void ws_transport_on_link_up(void);

/** 递增 session，并通知 PB 链路断开。 */
void ws_transport_new_session(void);

/** 尽量发完 TX 队列；仅 ws_transport_task 调用。 */
bool ws_transport_drain_tx(void);

bool ws_transport_enqueue_state(const char* json);
bool ws_transport_enqueue_audio(const char* json, const uint8_t* bin, size_t bin_len);
/**
 * 入队已打包的 camera 帧（u32be+json+bin）。
 * 接管 packed 所有权：成功由 TX 队列释放；失败立即 free。
 */
bool ws_transport_enqueue_camera(uint8_t* packed, size_t packed_len);
