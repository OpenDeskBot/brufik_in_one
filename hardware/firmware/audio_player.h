#ifndef AUDIO_PLAYER_H
#define AUDIO_PLAYER_H

#include <Arduino.h>
#include <driver/i2s.h>
#include <FFat.h>
#include "common.h"
#include "deskbot_config.h"

#define PDM_MIC_CLK DESKBOT_PDM_MIC_CLK
#define PDM_MIC_DATA DESKBOT_PDM_MIC_DATA

#define MAX98357_LRC DESKBOT_ROM_MAX98357_LRC
#define MAX98357_BCLK DESKBOT_ROM_MAX98357_BCLK
#define MAX98357_DIN DESKBOT_ROM_MAX98357_DIN
#define MAX98357_SD DESKBOT_ROM_MAX98357_SD
#define MAX98357_GAIN DESKBOT_ROM_MAX98357_GAIN

// parameters
#define SAMPLE_RATE 16000
#define DMA_BUF_COUNT 8
#define DMA_BUF_LEN 1024
#define SOUND_THRESHOLD 180

// i2s
extern i2s_config_t i2sIn_config;
extern i2s_config_t i2sOut_config;
extern const i2s_pin_config_t i2sIn_pin_config;
extern const i2s_pin_config_t i2sOut_pin_config;

void setup_audio();
/* 从麦克风采集队列取 mono int16 PCM（见 audio_capture：独立任务恒时 i2s_read 20ms 一帧）。
 * 不得与 mic_capture_task 并行对 I2S_NUM_0 再调 i2s_read。 */
void record(int16_t *data, size_t length = DMA_BUF_LEN);
void enhanceVoice(int16_t *data, size_t length = DMA_BUF_LEN);
void enhanceVoice_reset(void);
size_t calculate_mean(const int16_t *data, size_t length);

/*
 * 播放（I2S1 TX）约定：
 * - 所有扬声器输出必须经下方 API：内部入队到 FreeRTOS 队列，由独立 audio_play 任务串行执行
 *   i2s_set_clk / i2s_write；业务代码不要对 I2S_NUM_1 再写样点。
 * - audio_play_wav_owned：投递后 **阻塞直到该段播完**（pipeline 互斥 + done 信号量）。
 * - audio_stream_pcm16_*：小块连续入队；队列满则 xQueueSend 阻塞等待（持续推送、用法简单）。
 *   流式播放占用 pipeline（直到流自然结束/被显式停止），期间其他 `audio_play_*` 会阻塞等待。
 * - 流式队列深度见 AUDIO_PLAY_QUEUE_DEPTH；单块大小由调用方决定（如 20ms 一帧）。
 * - I2S DMA 缓冲见 DMA_BUF_COUNT / DMA_BUF_LEN。
 */
#ifndef AUDIO_PLAY_QUEUE_DEPTH
#define AUDIO_PLAY_QUEUE_DEPTH 96
#endif

/* 将 WAV 缓冲交给播放任务：播完后在任务内释放。
 *   caps_for_heap_caps_free == 0  → ::free(heap_ptr)
 *   != 0                          → heap_caps_free(heap_ptr)（如 MALLOC_CAP_SPIRAM 下载缓冲）
 * 失败时同样会释放，避免泄漏。*/
bool audio_play_wav_owned(uint8_t* heap_ptr, size_t len, float volume_ratio, uint32_t caps_for_heap_caps_free);

/* 启动 I2S 播放任务（setup 阶段在 setup_audio + mic_capture 之后调用一次）。 */
void audio_play_task_setup();

/* 流式 PCM16：调用方持续 push，小块会按顺序播放。
 * - 默认固定 16k/mono（与系统 SAMPLE_RATE 一致），因此不需要 begin/end。
 * - caps：0 → ::free；非 0 → heap_caps_free（如 MALLOC_CAP_SPIRAM）。
 * - push_* 在队列满时会阻塞等待（调用方线程会被背压）。 */
bool audio_stream_pcm16_push_owned(int16_t* samples, size_t num_samples, uint32_t caps_for_heap_caps_free,
                                   float volume_ratio = 1.0f);
/* 强制排队一次流式 End：含 pb begin 路径（仅 s_stream_pcm_active）与极简 push 会话。 */
void audio_stream_pcm16_stop();

/* WebSocket 断线 / write 失败（如 errno 104）时调用：在播放任务内排空队列、停流式、清 I2S DMA，
 * 避免已入队的短 PCM 反复播。非播放任务上会阻塞到冲刷完成；若在 audio_play 任务内调用则仅入队、不等待。 */
void audio_play_emergency_flush();

/* 打断播放：drain 队列里所有未执行 chunk（释放堆 + 唤醒 sync caller 防永久阻塞），
 * 再排队一个 reset job 到队尾。audio_play 任务完成当前正在执行的 chunk 后立即收到
 * reset → 停流式 + i2s_zero_dma_buffer + 释放 pipeline 互斥，让下一段 begin 立即生效。
 * 与 emergency_flush 的区别：
 *  - emergency_flush 走 SendToFront 抢占式，意图"立刻在 task 内排空一切"，用于断线兜底；
 *  - audio_play_reset 走 SendToBack，让当前 chunk 放完再 reset，用于"打断旧 pb 序列、迎接
 *    新 pb_start"——保持当前样点完整播完避免突然咔哒声。 */
void audio_play_reset();

/** audio_play 任务输入队列中待处理消息数（含未播 chunk）。 */
unsigned audio_play_input_queue_depth();

/** 流式 PCM（pb/TTS）是否仍占用 I2S 播放管线（含尾音 flush 写入）。
 * 用于 ASR 半双工：pbSignalTtsRoundComplete 之后队列里可能仍有 PCM，tts_active_ 已为 false 时仍需参考本标志。 */
bool audio_play_stream_pcm_active();

/** 扬声器是否在播可听 PCM（见 DESKBOT_SPEAKER_AUDIBLE_MEAN_ABS）。 */
bool audio_play_speaker_busy();

/** 调试：play() 内 i2s_write 是否进行中（含静音 chunk / tail flush 以外的路径）。 */
bool audio_play_i2s_in_progress();

/* pb 播放序列用：显式 begin/push/end，支持任意采样率/声道（仍仅支持 16bit PCM）。
 * - begin 成功后占用 pipeline（互斥在播放任务内 take，end 时 give），直到 end/stop。
 * - push_*：满了就阻塞等待（天然回压）；音频任务内按顺序播放。
 * - 若你只需要 16k/mono 的“极简流式”，继续用 audio_stream_pcm16_push_* 即可。 */
bool audio_stream_pcm16_begin(uint32_t sample_rate, uint8_t channels, float volume_ratio = 1.0f);
bool audio_stream_pcm16_end(uint8_t channels);

/* HTTP GET 拉取一段 WAV 并播放，用于上位机经 HTTP 推送 TTS 等音频给 ESP32。
   url 可是 http:// 或 https://（后者会切换到 WiFiClientSecure + setInsecure）。
   返回 true 表示下载 + 播放都成功。 */
bool audio_play_url(const char* url, float volume_ratio = 1.0);

/* 音频任务在 i2s_write 节拍中调用；上层提供强定义（如 asr_chat_client）可按间隔泵 ws、
 * 驱动舵机微调。钩子跑在音频任务上下文，不要做长耗时工作以免卡顿。默认弱符号空实现。
 *
 * ⚠️  audio_yield_hook 运行于 audio_play_task 上下文，严禁在钩子内调用任何
 *    同步 audio API（audio_stream_pcm16_end / audio_play_emergency_flush 等），
 *    否则任务等待自身处理队列任务，造成死锁。 */
extern "C" void audio_yield_hook();

/** 当前调用者是否运行于 audio_play_task 内。
 *  用于 loop() 等跨任务函数防止从播放任务上下文调用同步 audio API 而死锁。 */
bool audio_play_is_on_play_task();

#endif
