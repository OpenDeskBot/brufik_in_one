#ifndef WS_TRANSPORT_H
#define WS_TRANSPORT_H

#include <WebSocketsClient.h>
#include <stddef.h>
#include <stdint.h>

/** TX 类型：统一 FIFO；drain 按 type 选 asr / camera socket。 */
enum class WsTxType : uint8_t {
  kState = 0,  // pb_ack / boot_connect / audio_cancel / deskbot_state → /asr_chat
  kAudio = 1,  // Opus batch / flush → /asr_chat
  kImage = 2,  // camera_frame + JPEG → /camera_uplink
};

/**
 * 初始化：创建 RX/TX 队列与 owner mutex。
 * asr/camera socket 由 asr_ws / camera_ws 自行登记。
 * CONNECTED/DISCONNECTED/ready 在 drain_rx 处理；业务打包 BIN 移交 pb_runtime 帧队列。
 * 须在 setup_pb_runtime 之后调用。
 */
bool setup_ws_transport(void);

/**
 * 创建 FreeRTOS 任务：每轮 asr/camera 重连 → 1×RX → 1×TX。
 * WS loop/send 仅此任务；其它模块只 enqueue。
 */
bool task_setup_ws_transport(void);

void ws_transport_set_asr_client(WebSocketsClient* asr_ws);
void ws_transport_set_camera_client(WebSocketsClient* cam_ws);

/** asr_ws onEvent → RX 队列（拷贝 payload）。 */
void ws_transport_enqueue_rx(WStype_t type, uint8_t* payload, size_t length);

/** 递增 session：旧 RX 在 drain_rx 时丢弃。 */
void ws_transport_new_session(void);

/** 处理 1 条 RX / 1 条 TX；仅 ws_transport_task 调用。 */
bool ws_transport_drain_rx(void);
bool ws_transport_drain_tx(void);

bool ws_transport_enqueue_state(const char* json);
bool ws_transport_enqueue_text(const char* json);  // 兼容旧名 → enqueue_state
bool ws_transport_enqueue_audio(const char* json, const uint8_t* bin, size_t bin_len);

/**
 * image：json 拷贝；bin 借用。发完/丢弃时调 releaser。
 * 入队成功后调用方不得再使用 bin。
 */
bool ws_transport_enqueue_image_borrow(const char* json, uint8_t* bin, size_t bin_len,
                                       void (*releaser)(void* ctx), void* release_ctx);

void ws_transport_discard_tx_queue(void);
void ws_transport_discard_audio_tx(void);
/** 仅清 /asr_chat 相关 TX（audio+state），保留 camera JPEG，避免断线时误伤视觉上行。 */
void ws_transport_discard_asr_tx(void);

/** TX 队列空位数（mic 背压）。 */
uint32_t ws_transport_tx_slots_free(void);

#endif
