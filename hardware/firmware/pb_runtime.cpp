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
    servo["y"] = head_read_y();
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

void PbRuntime::onChainHead(pb_model& model) {
  if (model.action == PB_MODEL_REPLACE) {
    speaker_abort();
    display_abort();
    head_abort();
    endAudioStreamIfNeeded();
    log_info("[PB] chain head replace drain req=%s type=%s", model.req,
             pb_model_type_name(model.type));
  }

  safe_copy(pb_req_, sizeof(pb_req_), model.req);
  tts_active_ = true;
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
}

namespace {

PbRuntime s_runtime;
bool s_setup_ok = false;
TaskHandle_t s_task = nullptr;
QueueHandle_t s_frame_q = nullptr;

struct PbRxFrame {
  uint8_t* data = nullptr;
  size_t len = 0;
};

constexpr UBaseType_t kPbFrameQDepth = 64;
constexpr uint32_t kPbRuntimeStack = 24 * 1024;
constexpr UBaseType_t kPbRuntimePrio = 5;
constexpr size_t kMaxPackedFrame = 1024 * 1024;
constexpr size_t kPbModelRingCapacity = DESKBOT_PB_MODEL_RING_CAPACITY;

pb_model s_model_ring[kPbModelRingCapacity]{};
size_t s_model_head = 0;
size_t s_model_count = 0;
/** 当前已 dispatch、仍在播放中的优先级（不在 ring 内）。 */
int s_playing_level = -1;

size_t model_ring_at(size_t offset) {
  return (s_model_head + offset) % kPbModelRingCapacity;
}

void model_slot_clear(pb_model& m) {
  pb_model_free(m);
}

void model_slot_move(pb_model& dst, pb_model& src) {
  if (&dst == &src) return;
  model_slot_clear(dst);
  dst = src;
  src = pb_model{};
}

void model_ring_clear() {
  for (size_t i = 0; i < s_model_count; ++i) {
    model_slot_clear(s_model_ring[model_ring_at(i)]);
  }
  s_model_head = 0;
  s_model_count = 0;
  s_playing_level = -1;
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
    const int level = s_model_ring[model_ring_at(i)].level;
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
    if (s_model_ring[model_ring_at(off)].level != level) {
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
    if (s_model_ring[model_ring_at(off)].level >= level) {
      continue;
    }
    model_ring_remove(off);
    ++dropped;
  }
  return dropped;
}

static int current_priority_level() {
  const int qmax = model_ring_max_level();
  return qmax > s_playing_level ? qmax : s_playing_level;
}


bool model_ring_push(pb_model& incoming) {
  const pb_model& model = incoming;
  const bool chain_head = pb_model_is_chain_head(model);
  bool preempt_playing = false;

  if (chain_head) {
    if (model.action == PB_MODEL_REPLACE) {
      const size_t dropped = model_ring_drop_level(model.level);
      if (dropped) {
        log_info("[PB_SCHED] replace level=%d removed=%u same-level buffered models", model.level,
                 (unsigned)dropped);
      }
    }
    const size_t dropped_lower = model_ring_drop_below_level(model.level);
    if (dropped_lower) {
      log_info("[PB_SCHED] preempt level=%d removed=%u lower-priority buffered models", model.level,
               (unsigned)dropped_lower);
      speaker_abort();
      display_abort();
      head_abort();
    }
    const int max_level = model_ring_max_level();
    if (max_level >= 0 && model.level < max_level) {
      log_info("[PB_SCHED] drop lower priority req=%s level=%d queue_max_level=%d", model.req,
               model.level, max_level);
      return false;
    }
    if (s_playing_level >= 0) {
      if (model.level > s_playing_level ||
          (model.action == PB_MODEL_REPLACE && model.level >= s_playing_level)) {
        preempt_playing = true;
      }
    }
  }
  if (s_model_count >= kPbModelRingCapacity) {
    if (s_model_count > 0 &&
        incoming.level >= s_model_ring[model_ring_at(s_model_count - 1)].level) {
      log_warn("[PB_SCHED] ring full; drop oldest req=%s level=%d",
               incoming.req, incoming.level);
      model_ring_remove(0);
    } else {
      log_warn("[PB_SCHED] ring full; drop req=%s level=%d",
               incoming.req, incoming.level);
      return false;
    }
  }
  const size_t tail = model_ring_at(s_model_count++);
  model_slot_move(s_model_ring[tail], incoming);
  log_info("[PB_SCHED] buffered req=%s idx=%d level=%d depth=%u",
           s_model_ring[tail].req, s_model_ring[tail].idx,
           s_model_ring[tail].level, (unsigned)s_model_count);
  if (preempt_playing) {
    log_info("[PB_SCHED] preempt: level=%d > playing=%d",
             model.level, s_playing_level);
    /* 不在此 abortRound：口播中的 realtime servo replace 只清舵机，由 onChainHead 决定。 */
  }
  return true;
}

bool model_slot_from_frame(const PbRxFrame& item, pb_model& out, uint32_t* parse_ms) {
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
  if (!pb_model_from_json(frame.doc, frame.bin, media_len, out, err)) {
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
  static uint32_t s_last_block_log_ms = 0;
  while (s_model_count > 0) {
    const char* blocked_by = nullptr;
    if (!executors_accepting(&blocked_by)) {
      if (s_last_block_log_ms == 0 || (now - s_last_block_log_ms) >= 500u) {
        log_warn("[PB_LAT] blocked by=%s spk=%u head=%u disp=%u ring=%u",
                 blocked_by ? blocked_by : "?",
                 (unsigned)speaker_input_queue_depth(),
                 (unsigned)head_motor_input_queue_depth(),
                 (unsigned)display_render_input_queue_depth(),
                 (unsigned)s_model_count);
        s_last_block_log_ms = now;
      }
      break;
    }
    pb_model& slot = s_model_ring[s_model_head];
    const int dispatch_level = slot.level;
    const int idx = slot.idx;
    char req_snap[37];
    safe_copy(req_snap, sizeof(req_snap), slot.req);
    const uint32_t t_disp = millis();
    s_runtime.dispatchModel(slot);
    const uint32_t disp_ms = millis() - t_disp;
    slot = pb_model{};
    s_playing_level = dispatch_level;
    log_info("[PB_LAT] dispatched req=%s idx=%d level=%d ring=%u dispatch_ms=%u",
             req_snap, idx, dispatch_level,
             (unsigned)(s_model_count - 1), (unsigned)disp_ms);
    model_ring_remove(0);
  }
}

void task_loop_pb_runtime(void* /*arg*/) {
  constexpr TickType_t kIdleWaitTicks = pdMS_TO_TICKS(2);

  for (;;) {
    PbRxFrame item{};
    if (xQueueReceive(s_frame_q, &item, kIdleWaitTicks) == pdTRUE) {
      struct FrameGuard {
        uint8_t* p;
        ~FrameGuard() { free(p); }
      } guard{item.data};

      uint32_t parse_ms = 0;
      pb_model incoming{};
      if (model_slot_from_frame(item, incoming, &parse_ms)) {
        log_info("[PB_LAT] parsed type=%s req=%s idx=%d chunk_ms=%d anim=%u servo=%u "
                 "audio=%d len=%u parse_ms=%u ring=%u q=%u",
                 pb_model_type_name(incoming.type), incoming.req,
                 incoming.idx, incoming.chunk_ms,
                 (unsigned)incoming.anim_count, (unsigned)incoming.servo_count,
                 incoming.audio ? incoming.audio->next_bin_len : 0,
                 (unsigned)item.len, (unsigned)parse_ms,
                 (unsigned)s_model_count,
                 (unsigned)uxQueueMessagesWaiting(s_frame_q));
        if (incoming.type == PB_MODEL_CANCEL) {
          log_info("[PB_SCHED] cancel req=%s; clear %u buffered models",
                   incoming.req, (unsigned)s_model_count);
          model_ring_clear();
          s_runtime.handleCancel(incoming);
          model_slot_clear(incoming);
        } else if (!model_ring_push(incoming)) {
          model_slot_clear(incoming);
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
  PbRxFrame item{};
  item.data = data;
  item.len = length;
  if (xQueueSend(s_frame_q, &item, 0) != pdTRUE) {
    /* 队列满：丢弃最旧帧，为新帧腾位（保证设备处理最新数据）。 */
    PbRxFrame dropped{};
    if (xQueueReceive(s_frame_q, &dropped, 0) == pdTRUE) {
      free(dropped.data);
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

void pb_runtime_discard_rx_queue(void) {
  if (!s_frame_q) {
    return;
  }
  PbRxFrame item{};
  while (xQueueReceive(s_frame_q, &item, 0) == pdTRUE) {
    free(item.data);
  }
}
