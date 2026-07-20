#include "audio_player.h"
#include "deskbot_uplink_state.h"

/* I2S1 TX：仅在本文件的 audio_play 任务内执行（static play / wav_impl / pcm16_impl）。
 * 对外只通过 audio_play_* → post_play_job_and_wait 入队。 */

#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <math.h>
#include <string.h>

#include "audio_capture.h"
#include "esp_heap_caps.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

/* XIAO ESP32S3 Sense 板载 PDM 麦：CLK=42 DATA=41（Seeed Wiki） */
i2s_config_t i2sIn_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = i2s_bits_per_sample_t(16),
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = DMA_BUF_COUNT,
    .dma_buf_len = DMA_BUF_LEN
};

const i2s_pin_config_t i2sIn_pin_config = {
    .bck_io_num = I2S_PIN_NO_CHANGE,
    .ws_io_num = PDM_MIC_CLK,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = PDM_MIC_DATA
};

i2s_config_t i2sOut_config = {
    .mode = i2s_mode_t(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = i2s_bits_per_sample_t(16),
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = DMA_BUF_COUNT,
    .dma_buf_len = DMA_BUF_LEN
};

const i2s_pin_config_t i2sOut_pin_config = {
    .bck_io_num = MAX98357_BCLK,
    .ws_io_num = MAX98357_LRC,
    .data_out_num = MAX98357_DIN,
    .data_in_num = -1
};

void setup_audio() {
  if (MAX98357_GAIN >= 0) {
    pinMode(MAX98357_GAIN, INPUT);
  }
  if (MAX98357_SD >= 0) {
    pinMode(MAX98357_SD, OUTPUT);
    digitalWrite(MAX98357_SD, HIGH);
  }

  bool mic_ok = true;
  bool spk_ok = true;

  esp_err_t err = i2s_driver_install(I2S_NUM_0, &i2sIn_config, 0, NULL);
  if (err != ESP_OK) {
    log_error("[AUDIO] mic I2S0 PDM install failed err=%d", (int)err);
    mic_ok = false;
  } else {
    i2s_set_pin(I2S_NUM_0, &i2sIn_pin_config);
#if SOC_I2S_SUPPORTS_PDM_RX
    i2s_set_pdm_rx_down_sample(I2S_NUM_0, I2S_PDM_DSR_8S);
#endif
    i2s_set_clk(I2S_NUM_0, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
  }

  err = i2s_driver_install(I2S_NUM_1, &i2sOut_config, 0, NULL);
  if (err != ESP_OK) {
    log_error("[AUDIO] speaker I2S1 install failed err=%d", (int)err);
    spk_ok = false;
  } else {
    i2s_set_pin(I2S_NUM_1, &i2sOut_pin_config);
  }

  if (!mic_ok || !spk_ok) {
    log_error("[AUDIO] setup incomplete mic_ok=%d spk_ok=%d", mic_ok, spk_ok);
    return;
  }
  log_info("[AUDIO] ready PDM mic CLK=%d DATA=%d, MAX98357 DIN=%d play_vol=%.2f",
           (int)PDM_MIC_CLK, (int)PDM_MIC_DATA, (int)MAX98357_DIN,
           (double)DESKBOT_AUDIO_PLAY_VOLUME);
}

void record(int16_t* data, size_t length) {
  mic_consumer_read(data, length, portMAX_DELAY);
}

namespace {

int16_t s_hpf_prev_in = 0;
float s_hpf_prev_out = 0.0f;

}  // namespace

void enhanceVoice_reset(void) {
  s_hpf_prev_in = 0;
  s_hpf_prev_out = 0.0f;
}

void enhanceVoice(int16_t* data, size_t length) {
  /* 一阶高通（RC）：去掉 PDM 大 DC 偏置；fc≈80Hz @16kHz → alpha≈0.969 */
  constexpr float kAlpha = 0.969f;
  constexpr int kGain = 5;

  if (data == nullptr || length == 0) {
    return;
  }

  for (size_t i = 0; i < length; ++i) {
    const int16_t x = data[i];
    const float y =
        kAlpha * (s_hpf_prev_out + static_cast<float>(x) - static_cast<float>(s_hpf_prev_in));
    s_hpf_prev_in = x;
    s_hpf_prev_out = y;
    data[i] = static_cast<int16_t>(constrain(static_cast<int>(lroundf(y * static_cast<float>(kGain))),
                                             -32768, 32767));
  }
}

/* audio_yield_hook 默认空实现，asr_chat_client.cpp 提供强定义（taskYIELD）。
   weak 符号：Step 3 后实际在 audio_play_task 上下文里周期性调用，
   hook 仍可 pump ws + 推舵机——主 loop 可同时跑不再被整段 WAV 卡住。 */
extern "C" __attribute__((weak)) void audio_yield_hook() {}

size_t calculate_mean(const int16_t* data, size_t length);

static bool s_i2s_play_in_progress = false;
static bool s_audible_play_in_progress = false;

static bool pcm_chunk_audible(const int16_t* data, size_t length, float volume_ratio) {
  if (data == nullptr || length == 0) {
    return false;
  }
  const size_t mean = calculate_mean(data, length);
  const size_t effective =
      static_cast<size_t>(static_cast<float>(mean) * ((volume_ratio < 0.0f) ? 0.0f : volume_ratio));
  return effective >= (size_t)DESKBOT_SPEAKER_AUDIBLE_MEAN_ABS;
}

static void play(const int16_t* data, size_t length, float volume_ratio, bool yield_after_chunk = false) {
  if (!data || length == 0) {
    return;
  }
  const bool audible = pcm_chunk_audible(data, length, volume_ratio);
  s_i2s_play_in_progress = true;
  if (audible) {
    s_audible_play_in_progress = true;
    /* 首段可听 PCM 再挡麦；stream begin 时不置位，避免 pb JSON 先到、TTS 未播就停录音。 */
    deskbot_uplink_set_speaker_active(true);
  }
  constexpr size_t kBlock = 256;
  int16_t scratch[kBlock];
  size_t off = 0;
  while (off < length) {
    size_t n = length - off;
    if (n > kBlock) {
      n = kBlock;
    }
    const int16_t* out = data + off;
    if (volume_ratio != 1.0f) {
      for (size_t j = 0; j < n; j++) {
        scratch[j] =
            static_cast<int16_t>(static_cast<float>(data[off + j]) * volume_ratio);
      }
      out = scratch;
    }
    size_t bytes_written = 0;
    i2s_write(I2S_NUM_1, out, n * sizeof(int16_t), &bytes_written, portMAX_DELAY);
    off += n;
  }
  s_i2s_play_in_progress = false;
  if (audible) {
    s_audible_play_in_progress = false;
  }
  if (yield_after_chunk) {
    audio_yield_hook();
  }
}

static void stop_play() {
  i2s_zero_dma_buffer(I2S_NUM_1);
}

size_t calculate_mean(const int16_t* data, size_t length) {
  /* 历史实现写的是 i += 1000，对 20ms / 320 采样帧而言 count 退化为 1，等价于只看 data[0]，
   * 噪声大、漏判多。改为整帧绝对值平均（mean-abs），与 doc/zh/硬件与传感器.md 中"50 → 1000+"
   * 的描述一致；CPU 开销在 16kHz 下可忽略。 */
  if (data == nullptr || length == 0) {
    return 0;
  }
  uint64_t sum = 0;
  for (size_t i = 0; i < length; ++i) {
    sum += static_cast<uint32_t>(abs(data[i]));
  }
  return static_cast<size_t>(sum / length);
}

/* WAV 解析 + i2s 输出：仅在 audio_play 任务上下文执行。
 * I2S1 TX 只允许经本文件入队路径；业务侧禁止对 I2S_NUM_1 直写样点。
 */
static bool audio_play_wav_impl(const uint8_t* data, size_t len, float volume_ratio) {
  if (data == nullptr || len < 44 || memcmp(data, "RIFF", 4) != 0 || memcmp(data + 8, "WAVE", 4) != 0) {
    log_error("[Audio] bad WAV header (len=%u)", (unsigned)len);
    return false;
  }

  uint16_t channels = static_cast<uint16_t>(data[22]) | (static_cast<uint16_t>(data[23]) << 8);
  uint32_t rate = static_cast<uint32_t>(data[24]) | (static_cast<uint32_t>(data[25]) << 8) |
                  (static_cast<uint32_t>(data[26]) << 16) | (static_cast<uint32_t>(data[27]) << 24);
  uint16_t bits = static_cast<uint16_t>(data[34]) | (static_cast<uint16_t>(data[35]) << 8);

  size_t off = 12;
  uint32_t data_size = 0;
  size_t data_off = 0;
  while (off + 8 <= len) {
    uint32_t csize = static_cast<uint32_t>(data[off + 4]) | (static_cast<uint32_t>(data[off + 5]) << 8) |
                     (static_cast<uint32_t>(data[off + 6]) << 16) | (static_cast<uint32_t>(data[off + 7]) << 24);
    if (memcmp(data + off, "data", 4) == 0) {
      data_off = off + 8;
      data_size = csize;
      break;
    }
    off += 8 + csize;
  }

  if (data_off == 0 || data_size == 0 || data_off + data_size > len) {
    log_error("[Audio] WAV data chunk invalid doff=%u dsize=%u len=%u", (unsigned)data_off,
              (unsigned)data_size, (unsigned)len);
    return false;
  }

  if (bits != 16) {
    log_error("[Audio] unsupported WAV bits=%u (need 16)", (unsigned)bits);
    return false;
  }

  log_info("[Audio] WAV rate=%u ch=%u bits=%u pcm=%uB", (unsigned)rate, (unsigned)channels, (unsigned)bits,
           (unsigned)data_size);

  i2s_set_clk(I2S_NUM_1, rate, I2S_BITS_PER_SAMPLE_16BIT, channels == 2 ? I2S_CHANNEL_STEREO : I2S_CHANNEL_MONO);

  const int16_t* pcm = reinterpret_cast<const int16_t*>(data + data_off);
  size_t samples = data_size / 2;
  play(pcm, samples, volume_ratio);

  {
    const size_t tail_samples =
        static_cast<size_t>(DMA_BUF_COUNT) * static_cast<size_t>(DMA_BUF_LEN) * (channels == 2 ? 2u : 1u);
    static const int16_t s_zeros[256] = {};
    size_t written = 0;
    while (written < tail_samples) {
      size_t n = tail_samples - written;
      if (n > 256u) n = 256u;
      size_t bw = 0;
      i2s_write(I2S_NUM_1, s_zeros, n * sizeof(int16_t), &bw, portMAX_DELAY);
      written += n;
    }
  }
  i2s_set_clk(I2S_NUM_1, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
  stop_play();
  return true;
}

namespace {

/* 播放任务队列。free_mode≠None 时在任务内释放 heap_ptr。
 * ok_out：仅对需同步的 job 非空；流式 chunk 不唤醒 done_sem。
 */
enum class AudioHeapFree : uint8_t {
  kMalloc = 0,
  kHeapCaps = 1,
};

struct AudioPlayJob {
  enum class Kind : uint8_t {
    kWav = 0,
    kStreamPcm16Begin = 1,
    kStreamPcm16Chunk = 2,
    kStreamPcm16End = 3,
    kEmergencyFlush = 4,
    /* 打断 reset：drain 已由 caller 完成（drain_audio_play_queue_drop_and_notify），
     * task 在外层 for 循环 receive 到本 job 时已经"完成当前 chunk"。处理：
     * 若流式 active 则停流（stop_play + zero DMA）+ 释放 pipeline 互斥，等待新的 begin。 */
    kReset = 5,
  };

  Kind kind = Kind::kWav;
  AudioHeapFree free_mode = AudioHeapFree::kMalloc;

  uint8_t* heap_ptr = nullptr;
  size_t len = 0;

  int16_t* pcm_heap = nullptr;
  size_t pcm_samples = 0;
  uint32_t pcm_rate = SAMPLE_RATE;
  uint8_t pcm_channels = 1;

  float volume = 1.0f;
  bool* ok_out = nullptr;
};

QueueHandle_t     s_audio_play_q           = nullptr;
TaskHandle_t      s_audio_play_task        = nullptr;
SemaphoreHandle_t s_audio_play_done_sem    = nullptr;
SemaphoreHandle_t s_audio_play_mutex       = nullptr;
SemaphoreHandle_t s_pipeline_mutex         = nullptr;

static bool s_stream_pcm_active = false;
static float s_stream_vol = 1.0f;
static bool s_stream_client_session = false;

/* 丢弃队列中尚未播放的任务并释放缓冲；对带 ok_out 的同步 job 置失败并唤醒，避免调用方永久阻塞。 */
static void ensure_audio_play_task();

static void free_audio_play_job_pcm(AudioPlayJob& d) {
  if (d.kind != AudioPlayJob::Kind::kStreamPcm16Chunk || d.pcm_heap == nullptr) {
    return;
  }
  if (d.free_mode == AudioHeapFree::kMalloc) {
    ::free(d.pcm_heap);
  } else if (d.free_mode == AudioHeapFree::kHeapCaps) {
    heap_caps_free(d.pcm_heap);
  }
  d.pcm_heap = nullptr;
}

/* 播放队列满时丢弃最旧的一块 PCM chunk，避免在 WS 收包回调里 portMAX_DELAY 卡死、收不到后续 pb_chunk。 */
static bool enqueue_audio_play_job(AudioPlayJob& j) {
  ensure_audio_play_task();
  static bool s_logged_drop = false;
  for (unsigned attempt = 0; attempt < 32; ++attempt) {
    if (xQueueSend(s_audio_play_q, &j, 0) == pdTRUE) {
      s_logged_drop = false;
      return true;
    }
    AudioPlayJob oldest{};
    if (xQueueReceive(s_audio_play_q, &oldest, 0) != pdTRUE) {
      audio_yield_hook();
      vTaskDelay(1);
      continue;
    }
    if (oldest.kind == AudioPlayJob::Kind::kStreamPcm16Chunk) {
      free_audio_play_job_pcm(oldest);
      if (!s_logged_drop) {
        s_logged_drop = true;
        log_warn("[AUDIO] play queue full: dropping oldest pcm chunk");
      }
      continue;
    }
    (void)xQueueSendToFront(s_audio_play_q, &oldest, 0);
    audio_yield_hook();
    vTaskDelay(1);
  }
  return false;
}

static void drain_audio_play_queue_drop_and_notify() {
  AudioPlayJob d{};
  while (xQueueReceive(s_audio_play_q, &d, 0) == pdTRUE) {
    switch (d.kind) {
      case AudioPlayJob::Kind::kWav:
        if (d.heap_ptr) {
          if (d.free_mode == AudioHeapFree::kMalloc) {
            ::free(d.heap_ptr);
          } else if (d.free_mode == AudioHeapFree::kHeapCaps) {
            heap_caps_free(d.heap_ptr);
          }
        }
        if (d.ok_out) {
          *d.ok_out = false;
          xSemaphoreGive(s_audio_play_done_sem);
        }
        break;
      case AudioPlayJob::Kind::kStreamPcm16Begin:
        if (d.ok_out) {
          *d.ok_out = false;
          xSemaphoreGive(s_audio_play_done_sem);
        }
        break;
      case AudioPlayJob::Kind::kStreamPcm16Chunk:
        if (d.pcm_heap) {
          if (d.free_mode == AudioHeapFree::kMalloc) {
            ::free(d.pcm_heap);
          } else if (d.free_mode == AudioHeapFree::kHeapCaps) {
            heap_caps_free(d.pcm_heap);
          }
        }
        break;
      case AudioPlayJob::Kind::kStreamPcm16End:
        if (d.ok_out) {
          *d.ok_out = false;
          xSemaphoreGive(s_audio_play_done_sem);
        }
        break;
      case AudioPlayJob::Kind::kEmergencyFlush:
        break;
      case AudioPlayJob::Kind::kReset:
        break;
    }
  }
}

static void stream_write_tail_and_restore_i2s(uint8_t channels) {
  const size_t tail_samples =
      static_cast<size_t>(DMA_BUF_COUNT) * static_cast<size_t>(DMA_BUF_LEN) * (channels == 2 ? 2u : 1u);
  static int16_t s_zero_block[256];
  memset(s_zero_block, 0, sizeof(s_zero_block));
  size_t written = 0;
  while (written < tail_samples) {
    size_t n = tail_samples - written;
    if (n > 256u) {
      n = 256u;
    }
    size_t bytes_written = 0;
    i2s_write(I2S_NUM_1, s_zero_block, n * sizeof(int16_t), &bytes_written, portMAX_DELAY);
    written += n;
  }
  i2s_set_clk(I2S_NUM_1, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
  stop_play();
}

void audio_play_task_main(void* /*arg*/) {
  AudioPlayJob job{};
  for (;;) {
    if (xQueueReceive(s_audio_play_q, &job, portMAX_DELAY) != pdTRUE) {
      continue;
    }
    bool wake_caller = false;

    switch (job.kind) {
      case AudioPlayJob::Kind::kWav: {
        wake_caller = true;
        if (job.ok_out != nullptr) {
          bool playback_ok = false;
          if (job.heap_ptr != nullptr) {
            playback_ok = audio_play_wav_impl(job.heap_ptr, job.len, job.volume);
          }
          *job.ok_out = playback_ok;
        }
        if (job.free_mode == AudioHeapFree::kMalloc && job.heap_ptr) {
          ::free(job.heap_ptr);
        } else if (job.free_mode == AudioHeapFree::kHeapCaps && job.heap_ptr) {
          heap_caps_free(job.heap_ptr);
        }
        break;
      }
      case AudioPlayJob::Kind::kStreamPcm16Begin: {
        wake_caller = true;
        if (job.ok_out) {
          if (s_stream_pcm_active) {
            log_warn("[AUDIO] stream begin while active");
            *job.ok_out = false;
          } else if (job.pcm_channels != 1 && job.pcm_channels != 2) {
            *job.ok_out = false;
          } else if (job.pcm_rate == 0) {
            *job.ok_out = false;
          } else {
            /* pipeline 互斥必须在「本播放任务」内 take/give：begin 可能从 WS 回调进（甚至经
             * audio_yield_hook），end 常在 loop 任务；跨任务 Give 互斥会损坏 RTOS 状态 →
             * I2S/队列异常、尾音循环。流式会话期间一直由本任务持有直到 kStreamPcm16End。 */
            xSemaphoreTake(s_pipeline_mutex, portMAX_DELAY);
            i2s_set_clk(I2S_NUM_1, job.pcm_rate, I2S_BITS_PER_SAMPLE_16BIT,
                        job.pcm_channels == 2 ? I2S_CHANNEL_STEREO : I2S_CHANNEL_MONO);
            s_stream_pcm_active = true;
            s_stream_vol = job.volume;
            *job.ok_out = true;
          }
        }
        break;
      }
      case AudioPlayJob::Kind::kStreamPcm16Chunk: {
        wake_caller = false;
        if (!s_stream_pcm_active) {
          log_warn("[AUDIO] stream chunk dropped (no begin)");
          if (job.free_mode == AudioHeapFree::kMalloc && job.pcm_heap) {
            ::free(job.pcm_heap);
          } else if (job.free_mode == AudioHeapFree::kHeapCaps && job.pcm_heap) {
            heap_caps_free(job.pcm_heap);
          }
        } else if (job.pcm_heap != nullptr && job.pcm_samples > 0) {
          /* 每块播完再 yield 一次（泵 WS），避免逐样本 i2s_write + 高频 hook 导致队列积压、只播前几段。 */
          play(job.pcm_heap, job.pcm_samples, s_stream_vol, /*yield_after_chunk=*/true);
          if (job.free_mode == AudioHeapFree::kMalloc && job.pcm_heap) {
            ::free(job.pcm_heap);
          } else if (job.free_mode == AudioHeapFree::kHeapCaps && job.pcm_heap) {
            heap_caps_free(job.pcm_heap);
          }
        } else {
          if (job.free_mode == AudioHeapFree::kMalloc && job.pcm_heap) {
            ::free(job.pcm_heap);
          } else if (job.free_mode == AudioHeapFree::kHeapCaps && job.pcm_heap) {
            heap_caps_free(job.pcm_heap);
          }
        }
        break;
      }
      case AudioPlayJob::Kind::kStreamPcm16End: {
        wake_caller = true;
        if (job.ok_out) {
          if (!s_stream_pcm_active) {
            *job.ok_out = false;
          } else {
            stream_write_tail_and_restore_i2s(job.pcm_channels);
            s_stream_pcm_active = false;
            deskbot_uplink_set_speaker_active(false);
            xSemaphoreGive(s_pipeline_mutex);
            *job.ok_out = true;
          }
        } else {
          if (s_stream_pcm_active) {
            stream_write_tail_and_restore_i2s(job.pcm_channels);
            s_stream_pcm_active = false;
            deskbot_uplink_set_speaker_active(false);
            xSemaphoreGive(s_pipeline_mutex);
          }
        }
        break;
      }
      case AudioPlayJob::Kind::kEmergencyFlush: {
        wake_caller = true;
        drain_audio_play_queue_drop_and_notify();
        if (s_stream_pcm_active) {
          i2s_set_clk(I2S_NUM_1, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
          stop_play();
          s_stream_pcm_active = false;
          deskbot_uplink_set_speaker_active(false);
          xSemaphoreGive(s_pipeline_mutex);
        }
        s_stream_client_session = false;
        log_warn("[AUDIO] emergency flush: queue drained, stream stopped (e.g. connection reset)");
        break;
      }
      case AudioPlayJob::Kind::kReset: {
        /* 打断收尾：当前 chunk 已在外层 receive 之前播完。
         * 立即停流式：stop_play 写 i2s_zero_dma_buffer 抹掉 DMA 残留 ~340ms，避免新 begin
         * 切采样率前喇叭还在循环旧数据。释放 pipeline 互斥，让下一段 begin 可立即 take。 */
        wake_caller = false;
        if (s_stream_pcm_active) {
          i2s_set_clk(I2S_NUM_1, SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
          stop_play();
          s_stream_pcm_active = false;
          deskbot_uplink_set_speaker_active(false);
          xSemaphoreGive(s_pipeline_mutex);
        }
        s_stream_client_session = false;
        log_info("[AUDIO] reset: stream stopped, ready for next pb sequence");
        break;
      }
    }

    if (wake_caller) {
      xSemaphoreGive(s_audio_play_done_sem);
    }
  }
}

void ensure_audio_play_task() {
  if (s_audio_play_q && s_audio_play_task && s_audio_play_done_sem && s_audio_play_mutex && s_pipeline_mutex) {
    return;
  }
  if (!s_audio_play_q) {
    s_audio_play_q = xQueueCreate(AUDIO_PLAY_QUEUE_DEPTH, sizeof(AudioPlayJob));
  }
  if (!s_audio_play_done_sem) {
    s_audio_play_done_sem = xSemaphoreCreateBinary();
  }
  if (!s_audio_play_mutex) {
    s_audio_play_mutex = xSemaphoreCreateMutex();
  }
  if (!s_pipeline_mutex) {
    s_pipeline_mutex = xSemaphoreCreateMutex();
  }
  if (!s_audio_play_task) {
    BaseType_t rc =
        xTaskCreatePinnedToCore(audio_play_task_main, "audio_play", 8 * 1024, nullptr, 7, &s_audio_play_task, APP_CPU_NUM);
    if (rc != pdPASS) {
      log_error("[AUDIO] audio_play_task rc=%d", (int)rc);
    } else {
      log_info("[AUDIO] play task started prio=7 depth=%d", (int)AUDIO_PLAY_QUEUE_DEPTH);
    }
  }
}

bool post_play_job_and_wait(AudioPlayJob job_in) {
  ensure_audio_play_task();
  bool ok = false;
  job_in.ok_out = &ok;

  xSemaphoreTake(s_pipeline_mutex, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_mutex, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, 0);
  xQueueSend(s_audio_play_q, &job_in, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, portMAX_DELAY);
  xSemaphoreGive(s_audio_play_mutex);
  xSemaphoreGive(s_pipeline_mutex);

  return ok;
}

}  // namespace

/* 极简流式：固定 16k/mono。第一次 push 时自动 begin（占用 pipeline），stop 时 end（释放 pipeline）。 */
static bool ensure_stream_started(float volume_ratio) {
  if (s_stream_client_session) {
    s_stream_vol = volume_ratio;
    return true;
  }
  ensure_audio_play_task();
  bool ok = false;
  AudioPlayJob j{};
  j.kind = AudioPlayJob::Kind::kStreamPcm16Begin;
  j.pcm_rate = SAMPLE_RATE;
  j.pcm_channels = 1;
  j.volume = volume_ratio;
  j.ok_out = &ok;
  xSemaphoreTake(s_audio_play_mutex, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, 0);
  xQueueSend(s_audio_play_q, &j, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, portMAX_DELAY);
  xSemaphoreGive(s_audio_play_mutex);
  if (!ok) {
    return false;
  }
  s_stream_client_session = true;
  s_stream_vol = volume_ratio;
  return true;
}

bool audio_stream_pcm16_begin(uint32_t sample_rate, uint8_t channels, float volume_ratio) {
  if (channels != 1 && channels != 2) {
    return false;
  }
  if (sample_rate == 0) {
    return false;
  }
  ensure_audio_play_task();
  bool ok = false;
  AudioPlayJob j{};
  j.kind = AudioPlayJob::Kind::kStreamPcm16Begin;
  j.pcm_rate = sample_rate;
  j.pcm_channels = channels;
  j.volume = volume_ratio;
  j.ok_out = &ok;
  xSemaphoreTake(s_audio_play_mutex, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, 0);
  xQueueSend(s_audio_play_q, &j, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, portMAX_DELAY);
  xSemaphoreGive(s_audio_play_mutex);
  if (!ok) {
    return false;
  }
  return true;
}

bool audio_stream_pcm16_end(uint8_t channels) {
  if (channels != 1 && channels != 2) {
    return false;
  }
  ensure_audio_play_task();
  bool ok = false;
  AudioPlayJob j{};
  j.kind = AudioPlayJob::Kind::kStreamPcm16End;
  j.pcm_channels = channels;
  j.ok_out = &ok;
  xSemaphoreTake(s_audio_play_mutex, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, 0);
  xQueueSend(s_audio_play_q, &j, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, portMAX_DELAY);
  xSemaphoreGive(s_audio_play_mutex);
  return ok;
}

bool audio_stream_pcm16_push_owned(int16_t* samples, size_t num_samples, uint32_t caps_for_heap_caps_free,
                                   float volume_ratio) {
  if (!samples || num_samples == 0) {
    return false;
  }
  /* pb 路径先调 audio_stream_pcm16_begin()：s_stream_pcm_active==true，pipeline 互斥在播放任务内。
   * 若再走 ensure_stream_started 会二次 Begin 自锁——故 active 时只调音量。 */
  if (s_stream_pcm_active) {
    s_stream_vol = volume_ratio;
  } else if (!ensure_stream_started(volume_ratio)) {
    return false;
  }
  AudioPlayJob j{};
  j.kind = AudioPlayJob::Kind::kStreamPcm16Chunk;
  j.pcm_samples = num_samples;
  j.ok_out = nullptr;
  j.free_mode = (caps_for_heap_caps_free == 0) ? AudioHeapFree::kMalloc : AudioHeapFree::kHeapCaps;
  j.pcm_heap = samples;
  if (!enqueue_audio_play_job(j)) {
    free_audio_play_job_pcm(j);
    return false;
  }
  return true;
}

void audio_stream_pcm16_stop() {
  /* pb 流式仅设 s_stream_pcm_active，不设 s_stream_client_session；仍须能排队 End 冲刷尾零。 */
  ensure_audio_play_task();
  bool ok = false;
  AudioPlayJob j{};
  j.kind = AudioPlayJob::Kind::kStreamPcm16End;
  j.pcm_channels = 1;
  j.ok_out = &ok;
  xSemaphoreTake(s_audio_play_mutex, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, 0);
  xQueueSend(s_audio_play_q, &j, portMAX_DELAY);
  xSemaphoreTake(s_audio_play_done_sem, portMAX_DELAY);
  xSemaphoreGive(s_audio_play_mutex);
  (void)ok;

  s_stream_client_session = false;
}

void audio_play_emergency_flush() {
  ensure_audio_play_task();
  if (!s_audio_play_task) {
    return;
  }
  const bool on_play_task = (xTaskGetCurrentTaskHandle() == s_audio_play_task);
  if (!on_play_task) {
    xSemaphoreTake(s_audio_play_mutex, portMAX_DELAY);
  }
  xSemaphoreTake(s_audio_play_done_sem, 0);
  AudioPlayJob j{};
  j.kind = AudioPlayJob::Kind::kEmergencyFlush;
  xQueueSendToFront(s_audio_play_q, &j, portMAX_DELAY);
  if (!on_play_task) {
    xSemaphoreTake(s_audio_play_done_sem, portMAX_DELAY);
    xSemaphoreGive(s_audio_play_mutex);
  }
}

unsigned audio_play_input_queue_depth() {
  ensure_audio_play_task();
  if (!s_audio_play_q) {
    return 0u;
  }
  return (unsigned)uxQueueMessagesWaiting(s_audio_play_q);
}

bool audio_play_stream_pcm_active() {
  ensure_audio_play_task();
  return s_stream_pcm_active;
}

/** 扬声器是否正在输出可听 PCM（chunk mean-abs×volume ≥ DESKBOT_SPEAKER_AUDIBLE_MEAN_ABS）。
 * 不含：stream begin、队列里仅有 Begin/End job、静音 tail flush、stream_open 空窗。 */
bool audio_play_speaker_busy() {
  return s_audible_play_in_progress;
}

bool audio_play_i2s_in_progress() {
  return s_i2s_play_in_progress;
}

bool audio_play_is_on_play_task() {
  return s_audio_play_task != nullptr &&
         xTaskGetCurrentTaskHandle() == s_audio_play_task;
}

void audio_play_reset() {
  ensure_audio_play_task();
  if (!s_audio_play_task) {
    return;
  }
  const bool on_play_task = (xTaskGetCurrentTaskHandle() == s_audio_play_task);
  if (!on_play_task) {
    xSemaphoreTake(s_audio_play_mutex, portMAX_DELAY);
  }
  /* 1. drain 队列里所有未执行 job：释放堆 + 唤醒 sync caller 防永久阻塞。
   *    drain 必须在入队 kReset 之前，否则 kReset 会被同一轮 drain 误吃。 */
  drain_audio_play_queue_drop_and_notify();
  /* 2. 入队 kReset 到队尾（非抢占）：task 完成"当前正在执行的 chunk"后 receive 到，
   *    再做停流收尾。这样保留当前样点完整播完，避免突然咔哒声。 */
  AudioPlayJob j{};
  j.kind = AudioPlayJob::Kind::kReset;
  xQueueSend(s_audio_play_q, &j, portMAX_DELAY);
  if (!on_play_task) {
    xSemaphoreGive(s_audio_play_mutex);
  }
}

void task_setup_audio_play() {
  ensure_audio_play_task();
}

/* 缓冲区 heap_ptr：播完后在播放任务内按 kind 释放。失败也会释放以防泄漏。*/
bool audio_play_wav_owned(uint8_t* heap_ptr, size_t len, float volume_ratio, uint32_t caps_for_heap_caps_free) {
  AudioPlayJob j{};
  j.kind = AudioPlayJob::Kind::kWav;
  j.heap_ptr = heap_ptr;
  j.len = len;
  j.volume = volume_ratio;
  j.free_mode = (caps_for_heap_caps_free == 0) ? AudioHeapFree::kMalloc : AudioHeapFree::kHeapCaps;
  return post_play_job_and_wait(j);
}

bool audio_play_url(const char* url, float volume_ratio) {
  if (url == nullptr || url[0] == 0) {
    log_error("[Audio] play_url: empty url");
    return false;
  }

  log_info("[Audio] play_url GET %s", url);

  HTTPClient http;
  bool is_https = (strncmp(url, "https://", 8) == 0);
  WiFiClientSecure secure_client;
  bool begin_ok;
  if (is_https) {
    secure_client.setInsecure();
    begin_ok = http.begin(secure_client, url);
  } else {
    begin_ok = http.begin(url);
  }
  if (!begin_ok) {
    log_error("[Audio] http.begin failed");
    return false;
  }
  http.setTimeout(60000);

  int code = http.GET();
  if (code != 200) {
    log_error("[Audio] HTTP %d", code);
    http.end();
    return false;
  }

  int clen = http.getSize();
  size_t cap = (clen > 0 ? (size_t)clen : (size_t)(512 * 1024)) + 16;
  uint8_t* buf = (uint8_t*)heap_caps_malloc(cap, MALLOC_CAP_SPIRAM);
  if (!buf) {
    log_error("[Audio] PSRAM alloc %u failed (free=%u)", (unsigned)cap,
              (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    http.end();
    return false;
  }

  WiFiClient* stream = http.getStreamPtr();
  size_t got = 0;
  unsigned long t0 = millis();
  const unsigned long READ_TOTAL_MS = 60000;
  while (true) {
    if (clen > 0 && got >= (size_t)clen) break;
    size_t room = cap - 1 - got;
    if (room == 0) break;
    int n = stream->available();
    if (n > 0) {
      int r = stream->readBytes(reinterpret_cast<char*>(buf + got), (n > (int)room) ? (int)room : n);
      if (r > 0) {
        got += (size_t)r;
        t0 = millis();
        continue;
      }
    }
    if (clen < 0 && !stream->connected() && stream->available() == 0) break;
    if (millis() - t0 > READ_TOTAL_MS) {
      log_error("[Audio] read timeout got=%u clen=%d", (unsigned)got, clen);
      break;
    }
    delay(5);
  }
  http.end();
  log_info("[Audio] body read=%uB (clen=%d)", (unsigned)got, clen);

  if (got < 44) {
    log_error("[Audio] body too short for WAV");
    heap_caps_free(buf);
    return false;
  }
  return audio_play_wav_owned(buf, got, volume_ratio, MALLOC_CAP_SPIRAM);
}

