#pragma once

#include <Arduino.h>
#include "pb_model.h"

/**
 * pb v2 下行：解析帧队列 → 直接分发到 speaker/display/head → ack 泵。
 * WS 生命周期 / 收发在 ws_transport；本模块只处理 pb 业务。
 * 无本地环形缓冲；流控由服务端按 ack 节奏驱动。
 */

/** 初始化 pb_runtime（须在 setup_ws_transport 之前）。 */
bool setup_pb_runtime(void);

/** 启动 pb 泵任务（消费下行帧队列 + 分发）；须在 setup_pb_runtime 之后。 */
bool task_setup_pb_runtime(void);

/**
 * ws_transport 将完整打包 BIN 帧移交到 pb 队列（成功则接管 data 所有权）。
 * 失败返回 false，调用方须 free(data)。
 */
bool pb_runtime_enqueue_frame(uint8_t* data, size_t length);

/** 清空待处理下行帧（new_session 等场景）。 */
void pb_runtime_discard_rx_queue(void);
