#ifndef DESKBOT_UPLINK_STATE_H
#define DESKBOT_UPLINK_STATE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** WS ready 且 generation 有效；ws 任务 / 连接逻辑写入。 */
void deskbot_uplink_set_ws_ready(bool ready);
bool deskbot_uplink_ws_ready(void);
bool deskbot_uplink_ws_uplink_allowed(void);

/** 断线 / 错误时递增；消费者见变化则丢弃 batch / 清环。 */
uint32_t deskbot_uplink_ws_generation(void);
void deskbot_uplink_bump_ws_generation(void);

/** mic 入队总开关：见 mic_capture_allowed（speak_end && ws_ok）。 */
bool deskbot_uplink_capture_allowed(void);

#ifdef __cplusplus
}
#endif

#endif
