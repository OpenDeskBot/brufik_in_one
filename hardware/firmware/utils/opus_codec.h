#ifndef OPUS_CODEC_H
#define OPUS_CODEC_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * 下行 Opus 解码（loopTask）。上行 encode 见 mic.cpp。
 */
bool opus_codec_decode_init(void);
void opus_codec_decode_reset(void);

/** 一帧 PCM 样点数（20 ms @ sample_rate）。 */
size_t opus_codec_frame_samples(int sample_rate);
/** 解码缓冲容量：frames 帧 + 1 帧余量。单帧传 frames=1。 */
size_t opus_codec_decode_out_cap(int sample_rate, uint16_t frames);

/** 单帧：整包 payload 为一帧 Opus → PCM。返回样点数，失败 0。 */
size_t opus_codec_decode(const uint8_t* payload, size_t len, int sample_rate,
                         int16_t* out_pcm, size_t out_cap_samples);
/** 多帧 batch：重复 [u16be len][opus bytes]。返回总样点数，失败 0。 */
size_t opus_codec_decode_batch(const uint8_t* payload, size_t len, int sample_rate,
                               uint16_t frames, int16_t* out_pcm, size_t out_cap_samples);

#ifdef __cplusplus
}
#endif

#endif
