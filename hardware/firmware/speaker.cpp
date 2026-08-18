#include "speaker.h"

#include "mic.h"
#include "pb_model.h"
#include "utils/opus_codec.h"
#include "utils/utils.h"
#include "logger.h"

#include <ESP_I2S.h>
#include <atomic>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "freertos/queue.h"
#include "freertos/task.h"

namespace {

constexpr int kDmaBufCount = 8;
constexpr int kDmaBufLen = 1024;
/** opus_decode 栈开销与 mic 端 encode 同量级；8KB 会触发 stack canary。 */
constexpr uint32_t kSpeakerTaskStack = 28 * 1024;
/** 正常收尾：等待约 N 个 DMA frame 播完，再 zero_dma（不再写静音，避免写完立刻被清掉）。 */
constexpr size_t kIdleDrainDmaBufs = 2;
/** play 分块写，便于中途响应 need_cancel。 */
constexpr size_t kPlayBlockSamples = 1024;

static std::atomic<bool> s_is_speaking{false};
static std::atomic<float> s_volume{DESKBOT_AUDIO_PLAY_VOLUME};
/** 流式会话：仅 task_loop_speaker 写；pb 经 speaker_stream_pcm_active() 跨任务读 → 保持 atomic。 */
static std::atomic<bool> s_stream_active{false};
/** 跨任务请求取消当前 i2s 写出（abort 置位，见到 cancel 后清位）。 */
static std::atomic<bool> s_need_cancel{false};
/** pb_runtime 查询：当前任务是否已执行完毕（入队时 false，见到 kEndOfTask 后 true）。 */
static std::atomic<bool> s_task_done{true};
/** 仅 task_loop_speaker 访问。 */
static bool s_mic_speak_held = false;
static uint32_t s_i2s_rate = SAMPLE_RATE;
static QueueHandle_t s_queue = nullptr;
static TaskHandle_t s_task = nullptr;

/* 与 mic 同用新 I2S 驱动（不可与 legacy driver/i2s.h 混用）。 */
I2SClass s_i2s(I2S_NUM_1);

static void speaker_set_clk(uint32_t rate, uint8_t channels) {
  const uint32_t r = rate ? rate : SAMPLE_RATE;
  const i2s_slot_mode_t ch = (channels == 2) ? I2S_SLOT_MODE_STEREO : I2S_SLOT_MODE_MONO;
  if (!s_i2s.configureTX(r, I2S_DATA_BIT_WIDTH_16BIT, ch)) {
    log_warn("[SPEAKER] configureTX failed rate=%u ch=%u", (unsigned)r, (unsigned)channels);
  }
  s_i2s_rate = r;
}


enum class HeapFree : uint8_t { kMalloc = 0, kHeapCaps = 1 };

/** 执行器任务元素 type；cancel 走 xQueue 队尾，作旧/新任务分界。 */
enum class JobType : uint8_t {
  kCancel = 0,
  kWav = 1,
  kBegin = 2,
  kChunk = 3,
  kEnd = 4,
  kPbAudio = 5,
  kEndOfTask = 6,
};

struct Job {
  JobType type = JobType::kWav;
  HeapFree free_mode = HeapFree::kMalloc;
  uint8_t channels = 1;
  uint32_t rate = SAMPLE_RATE;
  pb_audio* pb_audio_ptr = nullptr;
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
  if (j.type == JobType::kWav) {
    free_ptr(j.wav.ptr, j.free_mode);
    j.wav.ptr = nullptr;
  } else if (j.type == JobType::kChunk) {
    free_ptr(j.pcm.ptr, j.free_mode);
    j.pcm.ptr = nullptr;
  } else if (j.type == JobType::kPbAudio) {
    pb_audio_free(j.pb_audio_ptr);
    j.pb_audio_ptr = nullptr;
  }
}

static HeapFree caps_to_mode(uint32_t caps) {
  return (caps == 0) ? HeapFree::kMalloc : HeapFree::kHeapCaps;
}

static bool enqueue(Job& j) {
  /* 满则失败（不从队列偷包，避免与 task_loop_speaker 双消费者竞态）。 */
  if (j.type != JobType::kCancel && j.type != JobType::kEndOfTask) {
    s_task_done.store(false, std::memory_order_release);
  }
  if (j.type == JobType::kChunk || j.type == JobType::kPbAudio) {
    return xQueueSend(s_queue, &j, 0) == pdTRUE;
  }
  return xQueueSend(s_queue, &j, portMAX_DELAY) == pdTRUE;
}

/** 停流并放麦；force 时即使未 begin 也清 I2S/麦（WAV 收尾/abort）。 */
static void stop_output(bool graceful, uint8_t channels, bool force);

static void finish_cancel() {
  stop_output(/*graceful=*/false, 1, /*force=*/true);
  log_info("[SPEAKER] cancel");
}

/**
 * need_cancel==false → false。
 * 否则非阻塞丢弃旧任务，见到 cancel 则清 flag、收尾并 return true（其后新任务保留）。
 * 队列空且未见 cancel → return true，保持 need_cancel。
 */
static bool poll_cancel() {
  if (!s_need_cancel.load(std::memory_order_acquire)) {
    return false;
  }
  Job j{};
  while (xQueueReceive(s_queue, &j, 0) == pdTRUE) {
    if (j.type == JobType::kCancel) {
      s_need_cancel.store(false, std::memory_order_release);
      finish_cancel();
      return true;
    }
    free_job(j);
  }
  return true;
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
 * 分块写出以便响应 need_cancel。返回 false 表示中途被取消。
 */
static bool play(int16_t* data, size_t length) {
  if (!data || length == 0) {
    return true;
  }
  if (poll_cancel()) {
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
    if (poll_cancel()) {
      return false;
    }
    size_t n = length - off;
    if (n > kPlayBlockSamples) {
      n = kPlayBlockSamples;
    }
    const size_t want = n * sizeof(int16_t);
    const size_t bw =
        s_i2s.write(reinterpret_cast<const uint8_t*>(data + off), want);
    if (bw != want) {
      log_warn("[SPEAKER] i2s_write short bw=%u want=%u", (unsigned)bw, (unsigned)want);
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
  speaker_set_clk(SAMPLE_RATE, 1);
}

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

  speaker_set_clk(rate, static_cast<uint8_t>(channels));
  const bool ok = play(reinterpret_cast<int16_t*>(data + data_off), data_size / 2);
  stop_output(/*graceful=*/ok, static_cast<uint8_t>(channels), /*force=*/true);
  return ok;
}

static bool play_pb_audio_owned(pb_audio* audio) {
  if (!audio || !audio->bin || audio->next_bin_len <= 0) {
    return false;
  }
  if (audio->sr == 0 || audio->ch == 0 || audio->fmt[0] == '\0') {
    log_warn("[SPEAKER] pb_audio missing sr/ch/fmt");
    return false;
  }
  if (strcmp(audio->fmt, "s16le") != 0 && strcmp(audio->fmt, "opus") != 0) {
    log_warn("[SPEAKER] unsupported fmt=%s", audio->fmt);
    return false;
  }

  const uint8_t* payload = reinterpret_cast<const uint8_t*>(audio->bin);
  const size_t length = (size_t)audio->next_bin_len;
  int16_t* pcm_owned = nullptr;
  size_t samples = 0;
  HeapFree free_mode = HeapFree::kHeapCaps;

  if (strcmp(audio->fmt, "opus") == 0) {
    const uint16_t opus_frames = audio->frames > 0 ? (uint16_t)audio->frames : (uint16_t)1;
    const size_t cap = opus_codec_decode_out_cap((int)audio->sr, opus_frames);
    pcm_owned = (int16_t*)psram_malloc(cap * sizeof(int16_t));
    if (!pcm_owned) {
      return false;
    }
    free_mode = HeapFree::kMalloc;
    samples = opus_frames > 1 ? opus_codec_decode_batch(payload, length, (int)audio->sr, opus_frames,
                                                        pcm_owned, cap)
                              : opus_codec_decode(payload, length, (int)audio->sr, pcm_owned, cap);
    if (samples == 0) {
      free_ptr(pcm_owned, free_mode);
      return false;
    }
  } else {
    if ((length & 1u) != 0u) {
      return false;
    }
    pcm_owned = (int16_t*)psram_malloc(length);
    if (!pcm_owned) {
      return false;
    }
    free_mode = HeapFree::kMalloc;
    memcpy(pcm_owned, payload, length);
    samples = length / 2;
  }

  if (!s_stream_active.load(std::memory_order_relaxed)) {
    if (audio->ch != 1 && audio->ch != 2) {
      free_ptr(pcm_owned, free_mode);
      return false;
    }
    speaker_set_clk(audio->sr, audio->ch);
    s_stream_active.store(true, std::memory_order_release);
  }

  const bool ok = play(pcm_owned, samples);
  free_ptr(pcm_owned, free_mode);
  return ok;
}

static void execute_job(Job& job) {
  switch (job.type) {
    case JobType::kWav:
      if (s_stream_active.load(std::memory_order_relaxed)) {
        log_warn("[SPEAKER] wav dropped (stream active)");
      } else if (job.wav.ptr) {
        (void)play_wav(job.wav.ptr, job.wav.len);
      }
      free_job(job);
      break;
    case JobType::kBegin:
      if (s_stream_active.load(std::memory_order_relaxed)) {
        log_warn("[SPEAKER] begin while active -> force end");
        end_stream(/*graceful=*/false, 1);
      }
      if (job.channels != 1 && job.channels != 2) {
        log_warn("[SPEAKER] bad channels=%u", (unsigned)job.channels);
      } else if (job.rate == 0) {
        log_warn("[SPEAKER] bad rate=0");
      } else {
        speaker_set_clk(job.rate, job.channels);
        s_stream_active.store(true, std::memory_order_release);
      }
      break;
    case JobType::kChunk:
      if (!s_stream_active.load(std::memory_order_relaxed)) {
        log_warn("[SPEAKER] chunk dropped (no begin)");
      } else if (job.pcm.ptr && job.pcm.samples > 0) {
        (void)play(job.pcm.ptr, job.pcm.samples);
      }
      free_job(job);
      break;
    case JobType::kPbAudio:
      if (job.pb_audio_ptr) {
        (void)play_pb_audio_owned(job.pb_audio_ptr);
      }
      free_job(job);
      break;
    case JobType::kEnd:
      end_stream(/*graceful=*/true, job.channels);
      break;
    case JobType::kCancel:
      break;
  }
}

static void task_loop_speaker(void*) {
  Job job{};
  constexpr TickType_t kIdleTimeout = pdMS_TO_TICKS(2000);
  for (;;) {
    (void)poll_cancel();
    if (xQueueReceive(s_queue, &job, kIdleTimeout) != pdTRUE) {
      /* 空闲超时：队列已空。若 mic gate 仍被持有（缺失 kEnd / 异常中断），
       * 主动释放，避免麦克风永久死锁。 */
      if (s_mic_speak_held) {
        log_warn("[SPEAKER] idle timeout, force releasing mic gate");
        release_mic(/*immediate=*/false);
      }
      continue;
    }
    if (job.type == JobType::kCancel) {
      /*
       * 空闲时 task 阻塞在 Receive，Cancel 会直接出队，不会经过 poll_cancel。
       * 若不在此清 flag，s_need_cancel 会永久为 true，后续 TTS 全被 play() 丢掉。
       */
      if (s_need_cancel.exchange(false, std::memory_order_acq_rel)) {
        finish_cancel();
      }
      continue;
    }
    if (job.type == JobType::kEndOfTask) {
      s_task_done.store(true, std::memory_order_release);
      continue;
    }
    execute_job(job);
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
  s_i2s.setPins((int8_t)MAX98357_BCLK, (int8_t)MAX98357_LRC, (int8_t)MAX98357_DIN);
  if (!s_i2s.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    log_error("[SPEAKER] ESP_I2S STD begin failed");
    return;
  }
  s_i2s_rate = SAMPLE_RATE;
  if (!s_queue) {
    s_queue = xQueueCreate(SPEAKER_QUEUE_DEPTH, sizeof(Job));
    if (!s_queue) {
      log_error("[SPEAKER] queue create failed");
      return;
    }
  }
  log_info("[SPEAKER] ready ESP_I2S DIN=%d vol=%.2f depth=%d", (int)MAX98357_DIN,
           (double)s_volume.load(std::memory_order_relaxed), (int)SPEAKER_QUEUE_DEPTH);
}

void task_setup_speaker() {
  if (s_task) {
    return;
  }
  const BaseType_t rc = utils_task_create_pinned(task_loop_speaker, "speaker", kSpeakerTaskStack,
                                                 nullptr, 7, &s_task, APP_CPU_NUM);
  if (rc != pdPASS) {
    log_error("[SPEAKER] task create rc=%d", (int)rc);
  } else {
    log_info("[SPEAKER] task started stack=%u", (unsigned)kSpeakerTaskStack);
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
  return (unsigned)uxQueueMessagesWaiting(s_queue);
}

bool speaker_stream_pcm16_begin(uint32_t sample_rate, uint8_t channels) {
  if ((channels != 1 && channels != 2) || sample_rate == 0) {
    return false;
  }
  Job j{};
  j.type = JobType::kBegin;
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
  j.type = JobType::kChunk;
  j.pcm.ptr = samples;
  j.pcm.samples = num_samples;
  j.free_mode = caps_to_mode(caps_for_heap_caps_free);
  if (!enqueue(j)) {
    free_ptr(samples, j.free_mode);
    return false;
  }
  return true;
}

bool speaker_submit_pb_audio_owned(pb_audio* audio) {
  if (!audio) {
    return false;
  }
  Job j{};
  j.type = JobType::kPbAudio;
  j.pb_audio_ptr = audio;
  if (!enqueue(j)) {
    pb_audio_free(audio);
    return false;
  }
  return true;
}

bool speaker_stream_pcm16_end(uint8_t channels) {
  if (channels != 1 && channels != 2) {
    return false;
  }
  Job j{};
  j.type = JobType::kEnd;
  j.channels = channels;
  return enqueue(j);
}

void speaker_abort() {
  Job j{};
  j.type = JobType::kCancel;
  s_need_cancel.store(true, std::memory_order_release);
  (void)xQueueSend(s_queue, &j, portMAX_DELAY);
}

bool speaker_task_done() {
  return s_task_done.load(std::memory_order_acquire);
}

void speaker_signal_task_done() {
  Job j{};
  j.type = JobType::kEndOfTask;
  (void)xQueueSend(s_queue, &j, portMAX_DELAY);
}

void speaker_set_task_done_flag() {
  s_task_done.store(true, std::memory_order_release);
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
  j.type = JobType::kWav;
  j.wav.ptr = buf;
  j.wav.len = len;
  j.free_mode = HeapFree::kHeapCaps;
  if (!enqueue(j)) {
    heap_caps_free(buf);
    return false;
  }
  return true;
}
