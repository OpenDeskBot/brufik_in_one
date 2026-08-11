#include "pb_runtime.h"

#include <stdlib.h>
#include <string.h>
#include "camera.h"
#include "display.h"
#include "mic.h"
#include "head.h"
#include "logger.h"
#include "speaker.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

static void safe_copy(char* dst, size_t cap, const char* src) {
  strncpy(dst, src, cap);
  dst[cap - 1] = '\0';
}

PbRuntime::PbRuntime() {}

uint8_t PbRuntime::normalizeAudioCh(uint8_t ch) {
  return (ch == 0 || ch > 2) ? 1 : ch;
}

void PbRuntime::endAudioStreamIfNeeded() {
  if (pb_audio_stream_started_) {
    speaker_stream_pcm16_end(normalizeAudioCh(pb_ch_));
    pb_audio_stream_started_ = false;
  }
}

void PbRuntime::signalTtsRoundComplete() {
  tts_active_ = false;
}

void PbRuntime::abortRound(bool abort_speaker) {
  if (abort_speaker) {
    speaker_abort();
  }
  display_abort();
  head_abort();
  endAudioStreamIfNeeded();
  pb_req_[0] = '\0';
  pb_sr_ = 0;
  pb_ch_ = 0;
  pb_fmt_[0] = '\0';
  pb_audio_buf_ms_est_ = 0;
  pb_last_buf_decay_ms_ = millis();
  pb_ack_out_pending_ = false;
  pb_ack_out_req_[0] = '\0';
  pb_ack_out_idx_ = 0;
  pb_ack_out_buf_ms_ = 0;
  pb_last_pb_ack_sent_wall_ms_ = 0;
  pb_ack_bypass_throttle_ = false;
  signalTtsRoundComplete();
}

void PbRuntime::updateAudioBufDecayWall() {
  const unsigned long now = millis();
  if (pb_last_buf_decay_ms_ == 0) {
    pb_last_buf_decay_ms_ = now;
  }
  if (pb_audio_buf_ms_est_ > 0) {
    const int32_t dec = (int32_t)(now - pb_last_buf_decay_ms_);
    if (dec > 0) {
      pb_audio_buf_ms_est_ -= dec;
      if (pb_audio_buf_ms_est_ < 0) {
        pb_audio_buf_ms_est_ = 0;
      }
    }
  }
  pb_last_buf_decay_ms_ = now;
}

void PbRuntime::enqueueAck(const char* req, uint32_t idx, int32_t audio_buf_ms, bool include_servo) {
  JsonDocument ack;
  ack["type"] = "pb_ack";
  ack["req"] = req;
  ack["idx"] = idx;
  ack["audio_buf_ms"] = audio_buf_ms;
  if (include_servo) {
    JsonObject servo = ack["servo"].to<JsonObject>();
    servo["x"] = head_read_x();
    servo["y"] = head_read_y_logic();
    servo["x_min"] = X_MIN_LIMIT;
    servo["x_max"] = X_MAX_LIMIT;
    servo["y_min"] = Y_MIN_LIMIT;
    servo["y_max"] = Y_MAX_LIMIT;
  }
  String msg;
  if (serializeJson(ack, msg) == 0) {
    log_warn("[PB] pb_ack serialize failed");
    return;
  }
  const bool ok = ws_transport_enqueue_state(msg.c_str());
  log_warn("[PB_LAT] ack_enqueue req=%s idx=%u ok=%d", req ? req : "?", (unsigned)idx, (int)ok);
}

void PbRuntime::flushPendingPbAck() {
  if (!pb_ack_out_pending_) {
    return;
  }
  const unsigned long now_wall = millis();
  if (!pb_ack_bypass_throttle_ && pb_last_pb_ack_sent_wall_ms_ != 0 &&
      (now_wall - pb_last_pb_ack_sent_wall_ms_ < 80UL)) {
    return;
  }
  enqueueAck(pb_ack_out_req_, pb_ack_out_idx_, pb_ack_out_buf_ms_, /*include_servo=*/true);
  pb_ack_bypass_throttle_ = false;
  pb_last_pb_ack_sent_wall_ms_ = now_wall;
  pb_ack_out_pending_ = false;
}

void PbRuntime::maybeAck(const pb_model& model) {
  if (model.req[0] == '\0') {
    return;
  }
  updateAudioBufDecayWall();
  safe_copy(pb_ack_out_req_, sizeof(pb_ack_out_req_), model.req);
  pb_ack_out_idx_ = (uint32_t)model.idx;
  const uint32_t chunk_ms = model.chunk_ms > 0 ? (uint32_t)model.chunk_ms : 127u;
  const unsigned qd = speaker_input_queue_depth();
  pb_ack_out_buf_ms_ = (int32_t)(qd * chunk_ms);
  if (pb_ack_out_buf_ms_ < pb_audio_buf_ms_est_) {
    pb_ack_out_buf_ms_ = pb_audio_buf_ms_est_;
  }
  pb_ack_out_pending_ = true;
}

void PbRuntime::applySideEffects(const pb_model& model) {
  if (model.volume >= 0) {
    speaker_set_volume(model.volume);
    log_info("[PB] volume=%d", model.volume);
  }
  if (model.cam_fps > 0) {
    camera_set_fps((uint32_t)model.cam_fps);
  }
  if (model.sr > 0) {
    pb_sr_ = model.sr;
  }
  if (model.ch > 0) {
    pb_ch_ = model.ch;
  }
  if (model.fmt[0] != '\0') {
    safe_copy(pb_fmt_, sizeof(pb_fmt_), model.fmt);
  }
}

static bool model_has_payload(const pb_model& model) {
  return model.anim_count > 0 || model.servo_count > 0 ||
         (model.audio && model.audio->next_bin_len > 0) || model.mic != PB_MIC_NONE;
}

static bool model_is_servo_only_gesture(const pb_model& model) {
  return model.anim_count == 0 && (!model.audio || model.audio->next_bin_len == 0) &&
         model.servo_count > 0 && model.mic == PB_MIC_NONE;
}

static bool model_is_servo_only_replace_head(const pb_model& model) {
  return pb_model_is_chain_head(model) && model.action == PB_MODEL_REPLACE &&
         model_is_servo_only_gesture(model);
}

bool PbRuntime::tryMicOnlySingle(pb_model& model) {
  if (model.type != PB_MODEL_SINGLE || model.idx != 0 || model.mic == PB_MIC_NONE) {
    return false;
  }
  if (model.anim_count > 0 || model.servo_count > 0 ||
      (model.audio && model.audio->next_bin_len > 0)) {
    return false;
  }
  if (model.mic == PB_MIC_OPEN) {
    signalTtsRoundComplete();
  }
  enqueueAck(model.req, 0, 0, /*include_servo=*/false);
  log_info("[PB] mic hint pb_single req=%s mic=%s", model.req,
           model.mic == PB_MIC_OPEN ? "open" : "mute");
  pb_model_free(model);
  return true;
}

void PbRuntime::dispatchServoOverlay(pb_model& model) {
  log_info("[PB] servo overlay req=%s level=%d servo=%u (keep audio/anim)", model.req, model.level,
           (unsigned)model.servo_count);
  head_abort();
  applySideEffects(model);
  dispatchServo(model);
  maybeAck(model);
  pb_ack_bypass_throttle_ = true;
  pb_model_free(model);
}

void PbRuntime::onChainHead(pb_model& model) {
  const bool servo_only = model_is_servo_only_gesture(model);
  const bool voice_servo_only = mic_capture_allowed() && servo_only;
  /* level>=3 纯舵机通常走 dispatchServoOverlay；若仍入环则只清电机。 */
  const bool replace_realtime_servo =
      servo_only && model.action == PB_MODEL_REPLACE && model.level >= 3;

  if (model.action == PB_MODEL_REPLACE) {
    if (replace_realtime_servo) {
      head_abort();
      log_info("[PB] realtime servo replace: abort motor");
    } else if (!voice_servo_only) {
      speaker_abort();
      display_abort();
      head_abort();
      endAudioStreamIfNeeded();
      log_info("[PB] chain head replace drain req=%s type=%s", model.req,
               pb_model_type_name(model.type));
    } else {
      log_info("[PB] servo-only during voice: skip audio drain req=%s", model.req);
    }
  }

  safe_copy(pb_req_, sizeof(pb_req_), model.req);
  if (!voice_servo_only) {
    tts_active_ = true;
  }
}

void PbRuntime::dispatchAnim(pb_model& model) {
  if (!model.anim || model.anim_count == 0) {
    return;
  }
  display_render_submit_pb_anim_frames_owned(model.anim, model.anim_count);
  model.anim = nullptr;
  model.anim_count = 0;
}

void PbRuntime::dispatchServo(pb_model& model) {
  if (!model.servo || model.servo_count == 0) {
    return;
  }
  head_submit_pb_servo_chunk_owned(model.servo, model.servo_count);
  model.servo = nullptr;
  model.servo_count = 0;
}

bool PbRuntime::dispatchAudio(pb_model& model) {
  pb_audio* audio = model.audio;
  if (!audio || !audio->bin || audio->next_bin_len <= 0) {
    return true;
  }
  if (audio->sr == 0 && pb_sr_ > 0) {
    audio->sr = pb_sr_;
  }
  if (audio->ch == 0 && pb_ch_ > 0) {
    audio->ch = pb_ch_;
  }
  if (audio->fmt[0] == '\0' && pb_fmt_[0] != '\0') {
    safe_copy(audio->fmt, sizeof(audio->fmt), pb_fmt_);
  }
  if (!speaker_submit_pb_audio_owned(audio)) {
    return false;
  }
  model.audio = nullptr;
  if (!pb_audio_stream_started_) {
    pb_audio_stream_started_ = true;
    pb_last_buf_decay_ms_ = millis();
    pb_audio_buf_ms_est_ = 0;
  }
  pb_audio_buf_ms_est_ += (int32_t)(model.chunk_ms > 0 ? model.chunk_ms : 127);
  return true;
}

void PbRuntime::onSequenceEnd(const pb_model& model) {
  endAudioStreamIfNeeded();
  log_info("[PB] complete req=%s idx=%d type=%s", model.req, model.idx,
           pb_model_type_name(model.type));
  signalTtsRoundComplete();
}

void PbRuntime::handleCancel(const pb_model& model) {
  log_info("[PB] cancel req=%s active_req=%s", model.req, pb_req_);
  if (model.req[0] == '\0' || pb_req_[0] == '\0' || strcmp(model.req, pb_req_) == 0) {
    abortRound(/*abort_speaker=*/true);
  }
}

void PbRuntime::dispatchModel(pb_model& model) {
  if (model.type == PB_MODEL_CANCEL) {
    handleCancel(model);
    pb_model_free(model);
    return;
  }
  if (!pb_model_is_play_type(model.type)) {
    pb_model_free(model);
    return;
  }

  log_info("[PB] dispatch req=%s type=%s idx=%d level=%d anim=%u servo=%u audio=%d",
           model.req, pb_model_type_name(model.type), model.idx, model.level,
           (unsigned)model.anim_count, (unsigned)model.servo_count,
           model.audio ? model.audio->next_bin_len : 0);

  if (tryMicOnlySingle(model)) {
    return;
  }

  const bool is_chain_head = pb_model_is_chain_head(model);
  if (is_chain_head) {
    onChainHead(model);
  }

  applySideEffects(model);

  if (!model_has_payload(model)) {
    log_warn("[PB] skip empty chunk req=%s idx=%d", model.req, model.idx);
    pb_model_free(model);
    return;
  }

  dispatchAnim(model);
  dispatchServo(model);
  if (!dispatchAudio(model)) {
    log_warn("[PB] audio dispatch failed req=%s idx=%d", model.req, model.idx);
  }

  maybeAck(model);
  pb_ack_bypass_throttle_ = true;

  if (model.type == PB_MODEL_END || model.type == PB_MODEL_SINGLE) {
    onSequenceEnd(model);
  }

  pb_model_free(model);
}

void PbRuntime::serviceLoop() {
  flushPendingPbAck();
  if (!tts_active_) {
    updateAttentionDisplay();
  }
}

void PbRuntime::updateAttentionDisplay() {
  const unsigned long now = millis();
  const bool should_wake = tts_active_;

  if (should_wake) {
    last_should_wake_ms_ = now;
    if (display_state_ != DISPLAY_WAKEUP) {
      log_info("[ATTENTION] -> WAKEUP (tts=%d)", (int)tts_active_);
      display_state_ = DISPLAY_WAKEUP;
      const int y_now = head_read_y_logic();
      if (y_now != Y_CENTER) {
        head_servo_cmd_async(HEAD_SERVO_HOLD, HEAD_SERVO_ABS, 0, Y_CENTER, /*step=*/0, /*ms=*/200);
        log_info("[ATTENTION] wake raise Y %d -> %d", y_now, Y_CENTER);
      }
    }
    return;
  }

  const bool first_time = (last_should_wake_ms_ == 0) && (display_state_ == DISPLAY_UNINIT);
  const bool dwell_done =
      (last_should_wake_ms_ != 0) && (now - last_should_wake_ms_ >= kIdleEnterDelayMs);
  if (display_state_ != DISPLAY_SLEEP && (first_time || dwell_done)) {
    log_info("[ATTENTION] -> SLEEP (dwell=%lums first=%d)",
             last_should_wake_ms_ == 0 ? 0UL : (now - last_should_wake_ms_), (int)first_time);
    const int idle_y_target = constrain(Y_CENTER + kSleepHeadDownDeg, Y_MIN_LIMIT, Y_MAX_LIMIT);
    const int y_now = head_read_y_logic();
    if (y_now != idle_y_target) {
      head_servo_cmd_async(HEAD_SERVO_HOLD, HEAD_SERVO_ABS, 0, idle_y_target, /*step=*/0, /*ms=*/200);
      log_info("[ATTENTION] sleep lower Y %d -> %d", y_now, idle_y_target);
    }
    display_state_ = DISPLAY_SLEEP;
  }
}

void PbRuntime::onLinkDown() {
  log_warn("[PB_RUNTIME] link down req=%s tts=%d heap=%u psram=%u", pb_req_, (int)tts_active_,
           (unsigned)ESP.getFreeHeap(), (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
  abortRound(/*abort_speaker=*/true);
  pb_runtime_discard_rx_queue();
}

namespace {

PbRuntime s_runtime;
bool s_setup_ok = false;
TaskHandle_t s_task = nullptr;
QueueHandle_t s_frame_q = nullptr;

struct PbRxFrame {
  enum class Kind : uint8_t {
    kPacked = 0,
    kLinkDown = 1,
  };
  Kind kind = Kind::kPacked;
  uint8_t* data = nullptr;
  size_t len = 0;
};

constexpr UBaseType_t kPbFrameQDepth = 64;
constexpr uint32_t kPbRuntimeStack = 24 * 1024;
constexpr UBaseType_t kPbRuntimePrio = 5;
constexpr size_t kMaxPackedFrame = 1024 * 1024;
constexpr size_t kPbModelRingCapacity = DESKBOT_PB_MODEL_RING_CAPACITY;
constexpr int32_t kPbPrefetchTargetMs = (int32_t)DESKBOT_PB_PREFETCH_TARGET_MS;
constexpr uint32_t kPbCreditTickMs = DESKBOT_PB_CREDIT_TICK_MS;

struct PbModelSlot {
  pb_model model{};
};

PbModelSlot s_model_ring[kPbModelRingCapacity]{};
size_t s_model_head = 0;
size_t s_model_count = 0;
/** 已下发给执行器、尚未按墙钟消耗完的 chunk_ms 信用。 */
int32_t s_dispatch_credit_ms = 0;
uint32_t s_credit_wall_ms = 0;
bool s_has_dispatched_model = false;
/** 当前已 dispatch、仍在 chunk_ms 节拍中的优先级（不在 ring 内）。 */
int s_playing_level = -1;

static void reset_credit() {
  s_dispatch_credit_ms = 0;
  s_credit_wall_ms = millis();
}

size_t model_ring_at(size_t offset) {
  return (s_model_head + offset) % kPbModelRingCapacity;
}

void model_slot_clear(PbModelSlot& slot) {
  pb_model_free(slot.model);
}

void model_slot_move(PbModelSlot& dst, PbModelSlot& src) {
  if (&dst == &src) return;
  model_slot_clear(dst);
  dst.model = src.model;
  src.model = pb_model{};
}

void model_ring_clear() {
  for (size_t i = 0; i < s_model_count; ++i) {
    model_slot_clear(s_model_ring[model_ring_at(i)]);
  }
  s_model_head = 0;
  s_model_count = 0;
  s_has_dispatched_model = false;
  s_playing_level = -1;
  reset_credit();
}

void dispatch_credit_decay(uint32_t now) {
  if (s_credit_wall_ms == 0) {
    s_credit_wall_ms = now;
    return;
  }
  const int32_t elapsed = (int32_t)(now - s_credit_wall_ms);
  if (elapsed <= 0) {
    return;
  }
  s_dispatch_credit_ms -= elapsed;
  if (s_dispatch_credit_ms < 0) {
    s_dispatch_credit_ms = 0;
  }
  s_credit_wall_ms = now;
}

bool executors_accepting(const char** blocked_by = nullptr) {
  /* xQueue 通常被执行器抽空；用 ring+queue 合计深度做回压。 */
  const unsigned spk = speaker_input_queue_depth();
  const unsigned head = head_motor_input_queue_depth();
  const unsigned disp = display_render_input_queue_depth();
  if (spk >= (unsigned)(SPEAKER_QUEUE_DEPTH - 1)) {
    if (blocked_by) {
      *blocked_by = "speaker";
    }
    return false;
  }
  if (head >= (unsigned)(HEAD_MOTOR_QUEUE_DEPTH - 1)) {
    if (blocked_by) {
      *blocked_by = "head";
    }
    return false;
  }
  if (disp >= (unsigned)(DESKBOT_PB_EXECUTOR_QUEUE_DEPTH - 1)) {
    if (blocked_by) {
      *blocked_by = "display";
    }
    return false;
  }
  return true;
}

void model_ring_remove(size_t offset) {
  if (offset >= s_model_count) return;
  for (size_t i = offset; i + 1 < s_model_count; ++i) {
    model_slot_move(s_model_ring[model_ring_at(i)], s_model_ring[model_ring_at(i + 1)]);
  }
  const size_t last = model_ring_at(s_model_count - 1);
  model_slot_clear(s_model_ring[last]);
  --s_model_count;
}

int model_ring_max_level() {
  int max_level = -1;
  for (size_t i = 0; i < s_model_count; ++i) {
    const int level = s_model_ring[model_ring_at(i)].model.level;
    if (level > max_level) {
      max_level = level;
    }
  }
  return max_level;
}

size_t model_ring_drop_level(int level) {
  size_t dropped = 0;
  size_t off = s_model_count;
  while (off > 0) {
    --off;
    if (s_model_ring[model_ring_at(off)].model.level != level) {
      continue;
    }
    model_ring_remove(off);
    ++dropped;
  }
  return dropped;
}

size_t model_ring_drop_below_level(int level) {
  size_t dropped = 0;
  size_t off = s_model_count;
  while (off > 0) {
    --off;
    if (s_model_ring[model_ring_at(off)].model.level >= level) {
      continue;
    }
    model_ring_remove(off);
    ++dropped;
  }
  return dropped;
}

static int current_priority_level() {
  const int qmax = model_ring_max_level();
  const int playing = s_has_dispatched_model ? s_playing_level : -1;
  return qmax > playing ? qmax : playing;
}

/** 纯舵机 replace：叠到电机上，绝不因 level 清掉口播缓冲。 */
static bool should_overlay_servo_only(const pb_model& model) {
  if (!model_is_servo_only_replace_head(model)) {
    return false;
  }
  /* level>=3：旧 realtime follow 约定；低 level 在更高优先级口播期间同样叠层。 */
  if (model.level >= 3) {
    return true;
  }
  const int cur = current_priority_level();
  return cur >= 0 && model.level < cur;
}

bool model_ring_push(PbModelSlot& incoming) {
  const pb_model& model = incoming.model;
  const bool chain_head = pb_model_is_chain_head(model);
  bool preempt_playing = false;

  if (chain_head) {
    /*
     * 纯舵机 realtime/低优先级叠层不走 ring（见 should_overlay_servo_only）。
     * 若误入：仍禁止 drop-below + speaker_abort，否则会掐死口播。
     */
    const bool servo_overlay = model_is_servo_only_replace_head(model) &&
                               (model.level >= 3 || model.level < current_priority_level());
    if (model.action == PB_MODEL_REPLACE) {
      const size_t dropped = model_ring_drop_level(model.level);
      if (dropped) {
        log_info("[PB_SCHED] replace level=%d removed=%u same-level buffered models", model.level,
                 (unsigned)dropped);
      }
      /* replace 链头：清信用以便立刻派发；执行器打断由随后 onChainHead 负责。 */
      reset_credit();
    }
    if (!servo_overlay) {
      const size_t dropped_lower = model_ring_drop_below_level(model.level);
      if (dropped_lower) {
        log_info("[PB_SCHED] preempt level=%d removed=%u lower-priority buffered models", model.level,
                 (unsigned)dropped_lower);
        /* 环形队列因更高 level 清空低优先级缓冲：同步打断执行器已预取内容。 */
        speaker_abort();
        display_abort();
        head_abort();
        reset_credit();
      }
    }
    const int max_level = model_ring_max_level();
    if (max_level >= 0 && model.level < max_level) {
      log_info("[PB_SCHED] drop lower priority req=%s level=%d queue_max_level=%d", model.req,
               model.level, max_level);
      return false;
    }
    /* 协议：更高 level（或同级 replace）应立即执行，不能被当前 chunk_ms 节拍卡住。 */
    if (s_has_dispatched_model && s_playing_level >= 0) {
      if (model.level > s_playing_level) {
        preempt_playing = true;
      } else if (model.action == PB_MODEL_REPLACE && model.level >= s_playing_level) {
        preempt_playing = true;
      }
    }
  }
  if (s_model_count >= kPbModelRingCapacity) {
    if (s_model_count > 0 &&
        incoming.model.level >= s_model_ring[model_ring_at(s_model_count - 1)].model.level) {
      log_warn("[PB_SCHED] model ring full; drop oldest for req=%s level=%d", incoming.model.req,
               incoming.model.level);
      model_ring_remove(0);
    } else {
      log_warn("[PB_SCHED] model ring full; drop req=%s level=%d", incoming.model.req,
               incoming.model.level);
      return false;
    }
  }
  const int incoming_level = model.level;
  const size_t tail = model_ring_at(s_model_count++);
  model_slot_move(s_model_ring[tail], incoming);
  log_info("[PB_SCHED] buffered req=%s idx=%d level=%d depth=%u", s_model_ring[tail].model.req,
           s_model_ring[tail].model.idx, s_model_ring[tail].model.level, (unsigned)s_model_count);
  if (preempt_playing) {
    log_info("[PB_SCHED] preempt now: incoming level=%d playing level=%d (flush credit)",
             incoming_level, s_playing_level);
    /* 不在此 abortRound：口播中的 realtime servo replace 只清舵机，由 onChainHead 决定。 */
    reset_credit();
  }
  return true;
}

bool model_slot_from_frame(const PbRxFrame& item, PbModelSlot& out, uint32_t* parse_ms) {
  const uint32_t t0 = millis();
  PackedFrame frame;
  if (!parse_packed_frame(item.data, item.len, frame)) {
    log_warn("[PB_SCHED] packed frame parse failed");
    if (parse_ms) {
      *parse_ms = millis() - t0;
    }
    return false;
  }
  const char* err = nullptr;
  const size_t media_len = frame.bin_len > 0 ? static_cast<size_t>(frame.bin_len) : 0;
  if (!pb_model_from_json(frame.doc, frame.bin, media_len, out.model, err)) {
    log_warn("[PB_SCHED] model parse rejected: %s", err ? err : "unknown");
    if (parse_ms) {
      *parse_ms = millis() - t0;
    }
    return false;
  }
  if (parse_ms) {
    *parse_ms = millis() - t0;
  }
  return true;
}

void model_ring_dispatch_due(uint32_t now) {
  dispatch_credit_decay(now);
  static uint32_t s_last_block_log_ms = 0;
  while (s_model_count > 0 && s_dispatch_credit_ms < kPbPrefetchTargetMs) {
    const char* blocked_by = nullptr;
    if (!executors_accepting(&blocked_by)) {
      if (s_last_block_log_ms == 0 || (now - s_last_block_log_ms) >= 500u) {
        log_warn("[PB_LAT] dispatch_blocked by=%s spk=%u head=%u disp=%u ring=%u credit=%d",
                 blocked_by ? blocked_by : "?", (unsigned)speaker_input_queue_depth(),
                 (unsigned)head_motor_input_queue_depth(),
                 (unsigned)display_render_input_queue_depth(), (unsigned)s_model_count,
                 (int)s_dispatch_credit_ms);
        s_last_block_log_ms = now;
      }
      break;
    }
    PbModelSlot& slot = s_model_ring[s_model_head];
    const int chunk_ms = slot.model.chunk_ms > 0 ? slot.model.chunk_ms : 1;
    const int dispatch_level = slot.model.level;
    const int idx = slot.model.idx;
    char req_snap[37];
    safe_copy(req_snap, sizeof(req_snap), slot.model.req);
    const uint32_t t_disp = millis();
    s_runtime.dispatchModel(slot.model);
    const uint32_t disp_ms = millis() - t_disp;
    slot.model = pb_model{};
    s_dispatch_credit_ms += chunk_ms;
    s_has_dispatched_model = true;
    s_playing_level = dispatch_level;
    log_warn("[PB_LAT] dispatched req=%s idx=%d chunk_ms=%d level=%d credit=%d ring=%u "
             "dispatch_call_ms=%u",
             req_snap, idx, chunk_ms, dispatch_level, (int)s_dispatch_credit_ms,
             (unsigned)(s_model_count - 1), (unsigned)disp_ms);
    model_ring_remove(0);
  }
  if (s_model_count > 0 && s_dispatch_credit_ms >= kPbPrefetchTargetMs) {
    static uint32_t s_last_credit_log_ms = 0;
    if (s_last_credit_log_ms == 0 || (now - s_last_credit_log_ms) >= 1000u) {
      log_warn("[PB_LAT] credit_wait credit=%d target=%d ring=%u", (int)s_dispatch_credit_ms,
               (int)kPbPrefetchTargetMs, (unsigned)s_model_count);
      s_last_credit_log_ms = now;
    }
  }
}

void task_loop_pb_runtime(void* /*arg*/) {
  for (;;) {
    /* 信用未满时短等收帧并尽量派发；已满则按墙钟 tick 等待再扣减。 */
    dispatch_credit_decay(millis());
    const bool credit_full =
        s_dispatch_credit_ms >= kPbPrefetchTargetMs && s_model_count > 0;
    const TickType_t wait_ticks =
        pdMS_TO_TICKS(credit_full ? kPbCreditTickMs : 2);

    PbRxFrame item{};
    if (xQueueReceive(s_frame_q, &item, wait_ticks) == pdTRUE) {
      if (item.kind == PbRxFrame::Kind::kLinkDown) {
        model_ring_clear();
        s_runtime.onLinkDown();
        continue;
      }
      struct FrameGuard {
        uint8_t* p;
        ~FrameGuard() {
          free(p);
        }
      } guard{item.data};

      const uint32_t t_rx = millis();
      uint32_t parse_ms = 0;
      PbModelSlot incoming{};
      if (model_slot_from_frame(item, incoming, &parse_ms)) {
        log_warn("[PB_LAT] frame_parsed type=%s req=%s idx=%d chunk_ms=%d anim=%u servo=%u "
                 "audio=%d len=%u parse_ms=%u ring=%u credit=%d q=%u",
                 pb_model_type_name(incoming.model.type), incoming.model.req, incoming.model.idx,
                 incoming.model.chunk_ms, (unsigned)incoming.model.anim_count,
                 (unsigned)incoming.model.servo_count,
                 incoming.model.audio ? incoming.model.audio->next_bin_len : 0, (unsigned)item.len,
                 (unsigned)parse_ms, (unsigned)s_model_count, (int)s_dispatch_credit_ms,
                 (unsigned)uxQueueMessagesWaiting(s_frame_q));
        if (incoming.model.type == PB_MODEL_CANCEL) {
          log_info("[PB_SCHED] cancel req=%s; clear %u buffered models", incoming.model.req,
                   (unsigned)s_model_count);
          model_ring_clear();
          s_runtime.handleCancel(incoming.model);
          model_slot_clear(incoming);
        } else if (should_overlay_servo_only(incoming.model)) {
          s_runtime.dispatchServoOverlay(incoming.model);
          incoming.model = pb_model{};
        } else if (!model_ring_push(incoming)) {
          model_slot_clear(incoming);
        } else {
          log_warn("[PB_LAT] buffered(+%ums since dequeue) ring=%u", (unsigned)(millis() - t_rx),
                   (unsigned)s_model_count);
        }
      }
    }
    model_ring_dispatch_due(millis());
    s_runtime.serviceLoop();
  }
}

}  // namespace

bool setup_pb_runtime(void) {
  if (!s_frame_q) {
    s_frame_q = xQueueCreate(kPbFrameQDepth, sizeof(PbRxFrame));
    if (!s_frame_q) {
      log_error("[PB_RUNTIME] frame queue create failed");
      s_setup_ok = false;
      return false;
    }
  }
  s_setup_ok = true;
  log_info("[PB_RUNTIME] setup ok frame_q=%u", (unsigned)kPbFrameQDepth);
  return true;
}

bool task_setup_pb_runtime(void) {
  if (!s_setup_ok) {
    log_error("[PB_RUNTIME] task_setup skipped (setup not ok)");
    return false;
  }
  if (s_task) {
    return true;
  }
  BaseType_t rc = utils_task_create_pinned(task_loop_pb_runtime, "pb_runtime", kPbRuntimeStack, nullptr,
                                           kPbRuntimePrio, &s_task, APP_CPU_NUM);
  if (rc != pdPASS) {
    log_error("[PB_RUNTIME] task create failed rc=%d (internal free=%u)", (int)rc,
              (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    s_task = nullptr;
    return false;
  }
  log_info("[PB_RUNTIME] task OK stack=%u prio=%u", (unsigned)kPbRuntimeStack,
           (unsigned)kPbRuntimePrio);
  return true;
}

PbRuntime* pb_runtime(void) {
  return &s_runtime;
}

bool pb_runtime_enqueue_frame(uint8_t* data, size_t length) {
  if (!s_setup_ok || !s_frame_q) {
    return false;
  }
  if (!data || length == 0 || length > kMaxPackedFrame) {
    return false;
  }
  PbRxFrame item{};
  item.kind = PbRxFrame::Kind::kPacked;
  item.data = data;
  item.len = length;
  if (xQueueSend(s_frame_q, &item, 0) != pdTRUE) {
    /* 队列满：丢弃最旧帧，为新帧腾位（保证设备处理最新数据）。 */
    PbRxFrame dropped{};
    if (xQueueReceive(s_frame_q, &dropped, 0) == pdTRUE) {
      if (dropped.kind == PbRxFrame::Kind::kPacked && dropped.data) {
        free(dropped.data);
      }
    }
    if (xQueueSend(s_frame_q, &item, 0) != pdTRUE) {
      log_warn("[PB_RUNTIME] frame queue full after drop; free new len=%u", (unsigned)length);
      return false;
    }
  }
  log_warn("[PB_LAT] pb_q_in len=%u q=%u", (unsigned)length,
           (unsigned)uxQueueMessagesWaiting(s_frame_q));
  return true;
}

void pb_runtime_notify_link_down(void) {
  if (!s_frame_q) {
    return;
  }
  PbRxFrame item{};
  item.kind = PbRxFrame::Kind::kLinkDown;
  (void)xQueueSend(s_frame_q, &item, 0);
}

void pb_runtime_discard_rx_queue(void) {
  if (!s_frame_q) {
    return;
  }
  PbRxFrame item{};
  while (xQueueReceive(s_frame_q, &item, 0) == pdTRUE) {
    if (item.kind == PbRxFrame::Kind::kPacked && item.data) {
      free(item.data);
    }
  }
}
