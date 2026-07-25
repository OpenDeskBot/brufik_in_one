#include "speaker.h"

#include "mic.h"
#include "utils/utils.h"
#include "logger.h"

#include <atomic>
#include <driver/i2s.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "freertos/queue.h"
#include "freertos/task.h"

namespace {

constexpr int kDmaBufCount = 8;
constexpr int kDmaBufLen = 1024;
/** 正常收尾：等待约 N 个 DMA frame 播完，再 zero_dma（不再写静音，避免写完立刻被清掉）。 */
constexpr size_t kIdleDrainDmaBufs = 2;
/** play 分块写，便于中途响应 s_cancel。 */
constexpr size_t kPlayBlockSamples = 1024;

static std::atomic<bool> s_is_speaking{false};
static std::atomic<float> s_volume{DESKBOT_AUDIO_PLAY_VOLUME};
/** 流式会话：仅 speaker_task 写；pb 经 speaker_stream_pcm_active() 跨任务读 → 保持 atomic。 */
static std::atomic<bool> s_stream_active{false};
/** 跨任务请求取消当前 i2s 写出（abort 置位，task 清位）。 */
static std::atomic<bool> s_cancel{false};
/** 仅 speaker_task 访问。 */
static bool s_mic_speak_held = false;
static uint32_t s_i2s_rate = SAMPLE_RATE;
static QueueHandle_t s_q = nullptr;
static TaskHandle_t s_task = nullptr;

static i2s_config_t s_i2s_cfg = {
    .mode = i2s_mode_t(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = i2s_bits_per_sample_t(16),
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = kDmaBufCount,
    .dma_buf_len = kDmaBufLen,
};

static const i2s_pin_config_t s_i2s_pins = {
    .bck_io_num = MAX98357_BCLK,
    .ws_io_num = MAX98357_LRC,
    .data_out_num = MAX98357_DIN,
    .data_in_num = -1,
};

enum class HeapFree : uint8_t { kMalloc = 0, kHeapCaps = 1 };

enum class JobKind : uint8_t {
  kWav = 0,
  kBegin = 1,
  kChunk = 2,
  kEnd = 3,
  kAbort = 4,
};

struct Job {
  JobKind kind = JobKind::kWav;
  HeapFree free_mode = HeapFree::kMalloc;
  uint8_t channels = 1;
  uint32_t rate = SAMPLE_RATE;
  union {
    struct {
      uint8_t* ptr;
      size_t len;
    } wav;
    struct {
      int16_t* ptr;
      size_t samples;
    } pcm;
  };
};

static void free_ptr(void* p, HeapFree mode) {
  if (!p) {
    return;
  }
  if (mode == HeapFree::kMalloc) {
    ::free(p);
  } else {
    heap_caps_free(p);
  }
}

static void free_job(Job& j) {
  if (j.kind == JobKind::kWav) {
    free_ptr(j.wav.ptr, j.free_mode);
    j.wav.ptr = nullptr;
  } else if (j.kind == JobKind::kChunk) {
    free_ptr(j.pcm.ptr, j.free_mode);
    j.pcm.ptr = nullptr;
  }
}

static HeapFree caps_to_mode(uint32_t caps) {
  return (caps == 0) ? HeapFree::kMalloc : HeapFree::kHeapCaps;
}

static bool enqueue(Job& j, bool front = false) {
  if (!s_q) {
    return false;
  }
  if (front) {
    return xQueueSendToFront(s_q, &j, portMAX_DELAY) == pdTRUE;
  }
  /* 满则失败（不从队列偷包，避免与 speaker_task 双消费者竞态）。 */
  if (j.kind == JobKind::kChunk) {
    if (xQueueSend(s_q, &j, 0) == pdTRUE) {
      return true;
    }
    return false;
  }
  return xQueueSend(s_q, &j, portMAX_DELAY) == pdTRUE;
}

static void drain_drop() {
  Job j{};
  while (xQueueReceive(s_q, &j, 0) == pdTRUE) {
    free_job(j);
  }
}

static size_t mean_abs(const int16_t* data, size_t length) {
  if (!data || length == 0) {
    return 0;
  }
  uint64_t sum = 0;
  for (size_t i = 0; i < length; ++i) {
    sum += static_cast<uint32_t>(abs(data[i]));
  }
  return static_cast<size_t>(sum / length);
}

static bool audible(const int16_t* data, size_t length, float vol) {
  const size_t m = mean_abs(data, length);
  return static_cast<size_t>(static_cast<float>(m) * vol) >= (size_t)DESKBOT_SPEAKER_AUDIBLE_MEAN_ABS;
}

static void release_mic(bool immediate) {
  if (!s_mic_speak_held) {
    s_is_speaking.store(false, std::memory_order_release);
    return;
  }
  if (!immediate) {
    vTaskDelay(pdMS_TO_TICKS(DESKBOT_TAIL_SUPPRESS_MS));
  }
  mic_set_speaker_state(kMicSpeakEnd);
  s_mic_speak_held = false;
  s_is_speaking.store(false, std::memory_order_release);
}

/**
 * 写 I2S：按 s_volume 缩放（可就地改 PCM）；可听则挡麦（SpeakStart）。
 * 分块写出以便响应 s_cancel。返回 false 表示中途被 abort。
 */
static bool play(int16_t* data, size_t length) {
  if (!data || length == 0) {
    return true;
  }
  if (s_cancel.load(std::memory_order_acquire)) {
    return false;
  }
  const float vol = s_volume.load(std::memory_order_relaxed);

  /* 已挡麦则跳过 mean_abs；半双工：首段可听再 SpeakStart。 */
  if (!s_mic_speak_held && audible(data, length, vol)) {
    s_is_speaking.store(true, std::memory_order_release);
    mic_set_speaker_state(kMicSpeakStart);
    s_mic_speak_held = true;
  }

  if (vol != 1.0f) {
    /* Q15 定点乘，避免每样本 float。speaker_set_volume 已夹到 [0,1]。 */
    const int32_t g = static_cast<int32_t>(vol * 32768.0f + 0.5f);
    for (size_t i = 0; i < length; ++i) {
      data[i] = static_cast<int16_t>((static_cast<int32_t>(data[i]) * g) >> 15);
    }
  }

  for (size_t off = 0; off < length;) {
    if (s_cancel.load(std::memory_order_acquire)) {
      return false;
    }
    size_t n = length - off;
    if (n > kPlayBlockSamples) {
      n = kPlayBlockSamples;
    }
    size_t bw = 0;
    const esp_err_t err =
        i2s_write(I2S_NUM_1, data + off, n * sizeof(int16_t), &bw, portMAX_DELAY);
    if (err != ESP_OK) {
      log_warn("[SPEAKER] i2s_write err=%d", (int)err);
      return false;
    }
    off += n;
  }
  return true;
}

/**
 * drain：等 DMA 里残留 PCM 大致播完；然后恢复 16k mono 并 zero_dma。
 * channels 仅保留接口兼容（delay 按 frame 计，与声道无关）。
 */
static void i2s_idle(bool drain, uint8_t /*channels*/) {
  if (drain) {
    const uint32_t rate = s_i2s_rate ? s_i2s_rate : SAMPLE_RATE;
    const uint32_t ms =
        (kIdleDrainDmaBufs * static_cast<uint32_t>(kDmaBufLen) * 1000u + rate - 1u) / rate;
    if (ms > 0) {
      vTaskDelay(pdMS_TO_TICKS(ms));
    }
  }
  i2s_set_clk(I2S_NUM_1, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
  s_i2s_rate = SAMPLE_RATE;
  i2s_zero_dma_buffer(I2S_NUM_1);
}

/** 停流并放麦；force 时即使未 begin 也清 I2S/麦（WAV 收尾/abort）。 */
static void stop_output(bool graceful, uint8_t channels, bool force) {
  if (!s_stream_active.load(std::memory_order_relaxed) && !force) {
    return;
  }
  i2s_idle(/*drain=*/graceful, channels);
  s_stream_active.store(false, std::memory_order_release);
  release_mic(/*immediate=*/!graceful);
}

static void end_stream(bool graceful, uint8_t channels) {
  stop_output(graceful, channels, /*force=*/false);
}

static bool play_wav(uint8_t* data, size_t len) {
  if (!data || len < 44 || memcmp(data, "RIFF", 4) != 0 || memcmp(data + 8, "WAVE", 4) != 0) {
    log_error("[SPEAKER] bad WAV header len=%u", (unsigned)len);
    return false;
  }
  const uint16_t channels =
      static_cast<uint16_t>(data[22]) | (static_cast<uint16_t>(data[23]) << 8);
  const uint32_t rate = static_cast<uint32_t>(data[24]) | (static_cast<uint32_t>(data[25]) << 8) |
                        (static_cast<uint32_t>(data[26]) << 16) |
                        (static_cast<uint32_t>(data[27]) << 24);
  const uint16_t bits =
      static_cast<uint16_t>(data[34]) | (static_cast<uint16_t>(data[35]) << 8);
  if (bits != 16) {
    log_error("[SPEAKER] unsupported bits=%u", (unsigned)bits);
    return false;
  }
  if (channels != 1 && channels != 2) {
    log_error("[SPEAKER] unsupported channels=%u", (unsigned)channels);
    return false;
  }

  size_t off = 12;
  uint32_t data_size = 0;
  size_t data_off = 0;
  while (off + 8 <= len) {
    const uint32_t csize =
        static_cast<uint32_t>(data[off + 4]) | (static_cast<uint32_t>(data[off + 5]) << 8) |
        (static_cast<uint32_t>(data[off + 6]) << 16) | (static_cast<uint32_t>(data[off + 7]) << 24);
    if (memcmp(data + off, "data", 4) == 0) {
      data_off = off + 8;
      data_size = csize;
      break;
    }
    off += 8 + csize;
  }
  if (data_off == 0 || data_size == 0 || data_off + data_size > len) {
    log_error("[SPEAKER] WAV data chunk invalid");
    return false;
  }

  i2s_set_clk(I2S_NUM_1, rate, I2S_BITS_PER_SAMPLE_16BIT,
              channels == 2 ? I2S_CHANNEL_STEREO : I2S_CHANNEL_MONO);
  s_i2s_rate = rate ? rate : SAMPLE_RATE;
  const bool ok = play(reinterpret_cast<int16_t*>(data + data_off), data_size / 2);
  stop_output(/*graceful=*/ok, static_cast<uint8_t>(channels), /*force=*/true);
  return ok;
}

static void speaker_task(void*) {
  Job job{};
  for (;;) {
    if (xQueueReceive(s_q, &job, portMAX_DELAY) != pdTRUE) {
      continue;
    }
    switch (job.kind) {
      case JobKind::kWav:
        if (s_stream_active.load(std::memory_order_relaxed)) {
          log_warn("[SPEAKER] wav dropped (stream active)");
        } else if (job.wav.ptr) {
          (void)play_wav(job.wav.ptr, job.wav.len);
        }
        free_job(job);
        break;
      case JobKind::kBegin:
        if (s_stream_active.load(std::memory_order_relaxed)) {
          log_warn("[SPEAKER] begin while active -> force end");
          end_stream(/*graceful=*/false, 1);
        }
        if (job.channels != 1 && job.channels != 2) {
          log_warn("[SPEAKER] bad channels=%u", (unsigned)job.channels);
        } else if (job.rate == 0) {
          log_warn("[SPEAKER] bad rate=0");
        } else {
          s_cancel.store(false, std::memory_order_release);
          i2s_set_clk(I2S_NUM_1, job.rate, I2S_BITS_PER_SAMPLE_16BIT,
                      job.channels == 2 ? I2S_CHANNEL_STEREO : I2S_CHANNEL_MONO);
          s_i2s_rate = job.rate;
          s_stream_active.store(true, std::memory_order_release);
        }
        break;
      case JobKind::kChunk:
        if (!s_stream_active.load(std::memory_order_relaxed)) {
          log_warn("[SPEAKER] chunk dropped (no begin)");
        } else if (job.pcm.ptr && job.pcm.samples > 0) {
          /* 若被 abort，play 提前返回；收尾交给随后的 kAbort。 */
          (void)play(job.pcm.ptr, job.pcm.samples);
        }
        free_job(job);
        break;
      case JobKind::kEnd:
        end_stream(/*graceful=*/true, job.channels);
        break;
      case JobKind::kAbort:
        drain_drop();
        /* force：流式与 WAV 都清 DMA/麦。 */
        stop_output(/*graceful=*/false, 1, /*force=*/true);
        s_cancel.store(false, std::memory_order_release);
        log_info("[SPEAKER] abort");
        break;
    }
  }
}

}  // namespace

void setup_speaker() {
  if (MAX98357_GAIN >= 0) {
    pinMode(MAX98357_GAIN, INPUT);
  }
  if (MAX98357_SD >= 0) {
    pinMode(MAX98357_SD, OUTPUT);
    digitalWrite(MAX98357_SD, HIGH);
  }
  const esp_err_t err = i2s_driver_install(I2S_NUM_1, &s_i2s_cfg, 0, NULL);
  if (err != ESP_OK) {
    log_error("[SPEAKER] I2S1 install failed err=%d", (int)err);
    return;
  }
  i2s_set_pin(I2S_NUM_1, &s_i2s_pins);
  log_info("[SPEAKER] ready DIN=%d vol=%.2f", (int)MAX98357_DIN,
           (double)s_volume.load(std::memory_order_relaxed));
}

void task_setup_speaker() {
  if (s_q && s_task) {
    return;
  }
  if (!s_q) {
    s_q = xQueueCreate(SPEAKER_QUEUE_DEPTH, sizeof(Job));
  }
  if (!s_task) {
    const BaseType_t rc =
        xTaskCreatePinnedToCore(speaker_task, "speaker", 8 * 1024, nullptr, 7, &s_task, APP_CPU_NUM);
    if (rc != pdPASS) {
      log_error("[SPEAKER] task create rc=%d", (int)rc);
    } else {
      log_info("[SPEAKER] task started depth=%d", (int)SPEAKER_QUEUE_DEPTH);
    }
  }
}

void speaker_set_volume(int vol_0_100) {
  if (vol_0_100 < 0) {
    vol_0_100 = 0;
  } else if (vol_0_100 > 100) {
    vol_0_100 = 100;
  }
  s_volume.store(vol_0_100 / 100.0f, std::memory_order_release);
}

int speaker_get_volume(void) {
  return (int)(s_volume.load(std::memory_order_acquire) * 100.0f + 0.5f);
}

bool speaker_is_speaking() {
  return s_is_speaking.load(std::memory_order_acquire);
}

bool speaker_stream_pcm_active() {
  return s_stream_active.load(std::memory_order_acquire);
}

unsigned speaker_input_queue_depth() {
  return s_q ? (unsigned)uxQueueMessagesWaiting(s_q) : 0u;
}

bool speaker_drop_oldest_pending() {
  if (!s_q) return false;
  Job dropped{};
  if (xQueueReceive(s_q, &dropped, 0) != pdTRUE) return false;
  free_job(dropped);
  log_warn("[SPEAKER] drop oldest pending job for PB scheduler");
  return true;
}

bool speaker_stream_pcm16_begin(uint32_t sample_rate, uint8_t channels) {
  if ((channels != 1 && channels != 2) || sample_rate == 0) {
    return false;
  }
  Job j{};
  j.kind = JobKind::kBegin;
  j.rate = sample_rate;
  j.channels = channels;
  return enqueue(j);
}

bool speaker_stream_pcm16_chunk(int16_t* samples, size_t num_samples,
                                uint32_t caps_for_heap_caps_free) {
  if (!samples || num_samples == 0) {
    return false;
  }
  Job j{};
  j.kind = JobKind::kChunk;
  j.pcm.ptr = samples;
  j.pcm.samples = num_samples;
  j.free_mode = caps_to_mode(caps_for_heap_caps_free);
  if (!enqueue(j)) {
    free_ptr(samples, j.free_mode);
    return false;
  }
  return true;
}

bool speaker_stream_pcm16_end(uint8_t channels) {
  if (channels != 1 && channels != 2) {
    return false;
  }
  Job j{};
  j.kind = JobKind::kEnd;
  j.channels = channels;
  return enqueue(j);
}

void speaker_abort() {
  s_cancel.store(true, std::memory_order_release);
  Job j{};
  j.kind = JobKind::kAbort;
  (void)enqueue(j, /*front=*/true);
}

bool speaker_play_url(const char* url) {
  uint8_t* buf = nullptr;
  size_t len = 0;
  if (!utils_http_get_binary(url, &buf, &len)) {
    return false;
  }
  if (len < 44) {
    log_error("[SPEAKER] body too short for WAV (%u)", (unsigned)len);
    heap_caps_free(buf);
    return false;
  }
  Job j{};
  j.kind = JobKind::kWav;
  j.wav.ptr = buf;
  j.wav.len = len;
  j.free_mode = HeapFree::kHeapCaps;
  if (!enqueue(j)) {
    heap_caps_free(buf);
    return false;
  }
  return true;
}
