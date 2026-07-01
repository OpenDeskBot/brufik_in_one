#ifndef VADNET_GATE_H
#define VADNET_GATE_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

/** VADNet 单帧检测结果（含 AFE vad_cache，用于避免首字截断）。 */
struct VadnetSpeechPulse {
  bool speech = false;
  bool cache_ready = false;
  const int16_t* cache_pcm = nullptr;
  size_t cache_samples = 0;
};

/** 初始化 AFE + VADNet（需已烧录 model 分区 srmodels.bin）。 */
bool vadnet_gate_setup();

bool vadnet_gate_available();

/** 每轮 ASR 开始前重置 VAD 状态与 feed 缓冲。 */
void vadnet_gate_reset_round();

/** 送入 16k/mono PCM；内部按 AFE feed_chunksize 聚合后 fetch VAD 状态。 */
bool vadnet_gate_process(const int16_t* pcm, size_t samples, VadnetSpeechPulse* out);

#endif
