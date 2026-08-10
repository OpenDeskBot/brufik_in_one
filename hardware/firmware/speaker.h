#pragma once

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
#define SPEAKER_QUEUE_DEPTH DESKBOT_PB_EXECUTOR_QUEUE_DEPTH
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
 * 打断：置 need_cancel，再队尾入队 type=cancel。
 * poll_cancel 丢弃 cancel 之前的旧任务并清 DMA；正在写的一小块 PCM 仍会写完。
 */
void speaker_abort();

/** xQueue 缓冲深度（供 pb 回压 / ack）。 */
unsigned speaker_input_queue_depth();
bool speaker_stream_pcm_active();
