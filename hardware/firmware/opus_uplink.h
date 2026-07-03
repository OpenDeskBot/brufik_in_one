#ifndef OPUS_UPLINK_H
#define OPUS_UPLINK_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

/** 16 kHz mono，20 ms 帧（320 样点）Opus 上行编码。 */
bool opus_uplink_init(void);
void opus_uplink_reset(void);
size_t opus_uplink_encode(const int16_t* pcm, size_t samples, uint8_t* out_buf, size_t out_cap);

#endif
