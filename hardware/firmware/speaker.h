#ifndef SPEAKER_H
#define SPEAKER_H

#include <Arduino.h>
#include <stdint.h>
#include "deskbot_config.h"
#include "pb_model.h"

#define MAX98357_LRC DESKBOT_ROM_MAX98357_LRC
#define MAX98357_BCLK DESKBOT_ROM_MAX98357_BCLK
#define MAX98357_DIN DESKBOT_ROM_MAX98357_DIN
#define MAX98357_SD DESKBOT_ROM_MAX98357_SD
#define MAX98357_GAIN DESKBOT_ROM_MAX98357_GAIN

#define SAMPLE_RATE 16000

#ifndef SPEAKER_QUEUE_DEPTH
#define SPEAKER_QUEUE_DEPTH 5
#endif

void setup_speaker();
void task_setup_speaker();

bool speaker_is_speaking();
void speaker_set_volume(int vol_0_100);
int speaker_get_volume(void);

/** HTTP 拉 WAV 并入队播放。 */
bool speaker_play_url(const char* url);

/** 流式：按 FIFO 顺序 begin → chunk* → end。勿并行开多会话。 */
bool speaker_stream_pcm16_begin(uint32_t sample_rate, uint8_t channels);
/** 入队一块 PCM；成功则所有权交播放任务。caps=0 用 free，否则 heap_caps_free。 */
bool speaker_stream_pcm16_chunk(int16_t* samples, size_t num_samples,
                                uint32_t caps_for_heap_caps_free);
bool speaker_stream_pcm16_end(uint8_t channels);

/** 入队一块 pb_audio（opus/s16le）；成功则所有权交 speaker_task。 */
bool speaker_submit_pb_audio_owned(pb_audio* audio);

/**
 * 打断：置 cancel 并插队 abort，排空未播队列、清 DMA（流式与 WAV 均可打断）。
 * 当前正在写的一小块 PCM 仍会写完，随后停止。
 */
void speaker_abort();

unsigned speaker_input_queue_depth();
/** 丢弃最早尚未播放的输入任务；用于 PB 调度器为新分片腾出队列槽位。 */
bool speaker_drop_oldest_pending();
bool speaker_stream_pcm_active();

#endif
