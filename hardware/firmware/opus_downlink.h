#ifndef OPUS_DOWNLINK_H
#define OPUS_DOWNLINK_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

/** pb 下行 Opus batch → PCM；``frames<=1`` 时整包单帧。返回 malloc 缓冲，调用方 heap_caps_free。 */
bool opus_downlink_decode(const uint8_t* payload, size_t len, int sample_rate, uint16_t frames,
                          int16_t** out_pcm, size_t* out_samples, uint32_t* out_free_caps);

bool opus_downlink_init(void);
void opus_downlink_reset(void);

#endif
