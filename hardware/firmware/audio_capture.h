#ifndef AUDIO_CAPTURE_H
#define AUDIO_CAPTURE_H

#include <Arduino.h>
#include "freertos/FreeRTOS.h"

/* Step 2：麦克风独立采集任务。
 *
 * 背景：Arduino loop / runVoiceRound 在主任务里阻塞时（网络、舵机、gaze、部分 delay），
 * 若此时才调用 i2s_read，DMA 缓冲区会积压甚至溢出，收音时间轴与实际 wall clock 偏离。
 *
 * 模型：
 * - 独占 I2S_NUM_0 RX：仅此任务调用 i2s_read，恒定读 320 样本（20ms @ 16k 单声道）一帧，
 *   推入 FreeRTOS 队列；队列满时丢最旧的帧再放新帧（始终保持“最新音频”）。
 * - 所有业务侧取样统一走 mic_consumer_read()（由 audio_player.cpp::record 转发），内部用 mutex
 *   + stash 拼装任意长度的 PCM，保证同时只有一个消费者在拆帧（wake / Doubao ASR / asr_chat
 *   若在极端情况下并行，后来者阻塞等待）。
 *
 * flush：上行开始前倒掉队列里 Idle 积压的旧帧（runVoiceRound 起点调用）。*/

static constexpr size_t kMicCaptureFrameSamples = 320;

struct MicCaptureFrame {
  int16_t pcm[kMicCaptureFrameSamples];
};

/** 启动 mic_cap 任务（须在 setup_audio 装好 I2S0 之后）。 */
void task_setup_mic_capture();
void mic_capture_flush_queue();

/* 读取连续 mono int16 PCM，阻塞直到凑满 length 个采样或永远不返回（ticks=portMAX_DELAY）。
 * length 可为任意正整数（不必整除 320）。 */
void mic_consumer_read(int16_t* dst, size_t length, TickType_t first_frame_ticks);

#endif
