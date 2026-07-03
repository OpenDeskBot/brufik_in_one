#ifndef DESKBOT_UPLINK_STATE_H
#define DESKBOT_UPLINK_STATE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** 扬声器正在输出可听 PCM（stream 或 chunk 播放）。audio_play 路径写入。 */
void deskbot_uplink_set_speaker_active(bool active);

/** 播音结束时刻 + DESKBOT_TAIL_SUPPRESS_MS；active=false 时设置。 */
bool deskbot_uplink_speaker_audible(void);
bool deskbot_uplink_in_tail_suppress(void);
/** 尾音抑制剩余 ms；不在抑制期返回 0。 */
unsigned long deskbot_uplink_tail_ms_remaining(void);

/** WS ready 且 generation 有效；ws 任务 / 连接逻辑写入。 */
void deskbot_uplink_set_ws_ready(bool ready);
bool deskbot_uplink_ws_ready(void);
bool deskbot_uplink_ws_uplink_allowed(void);

/** 断线 / 错误时递增；消费者见变化则丢弃 batch / 清环。 */
uint32_t deskbot_uplink_ws_generation(void);
void deskbot_uplink_bump_ws_generation(void);

/** mic 入队 + record 总开关：网络 OK && !speaking && !尾音抑制。 */
bool deskbot_uplink_capture_allowed(void);

#ifdef __cplusplus
}
#endif

#endif
