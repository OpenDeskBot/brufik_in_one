#include "pb_runtime.h"

#include <stdlib.h>
#include "display.h"
#include "head.h"
#include "logger.h"
#include "speaker.h"
#include "utils/utils.h"
#include "ws_transport.h"

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

/* ── 常量 ── */

static constexpr int kAckBatchSize = 10;
static constexpr char kAckChunk[] = "pb_chunk";
static constexpr char kAckEnd[] = "pb_end";

/* ── 状态 ── */

static String s_req;
static int s_dispatched_since_ack = 0;
static String s_ack_req;

/* ── 帧队列 ── */

struct PbRxFrame {
  uint8_t* data = nullptr;
  size_t len = 0;
};

static constexpr UBaseType_t kPbFrameQDepth = 64;
static constexpr uint32_t kPbRuntimeStack = 24 * 1024;
static constexpr UBaseType_t kPbRuntimePrio = 5;

static bool s_setup_ok = false;
static TaskHandle_t s_task = nullptr;
static QueueHandle_t s_frame_q = nullptr;

/* ── 内部函数 ── */

static unsigned computeMinExecutorSpace() {
  const unsigned spk = SPEAKER_QUEUE_DEPTH - speaker_input_queue_depth();
  const unsigned hd = HEAD_MOTOR_QUEUE_DEPTH - head_motor_input_queue_depth();
  const unsigned disp = DESKBOT_PB_EXECUTOR_QUEUE_DEPTH - display_render_input_queue_depth();
  unsigned m = spk;
  if (hd < m) m = hd;
  if (disp < m) m = disp;
  return m;
}

static void sendAck(const char* ack_type) {
  const unsigned space = computeMinExecutorSpace();
  JsonDocument ack;
  ack["type"] = "pb_ack";
  ack["req"] = s_ack_req;
  ack["ack_type"] = ack_type;
  ack["space"] = space;
  String msg;
  if (serializeJson(ack, msg) == 0) {
    log_warn("[PB] pb_ack serialize failed");
    return;
  }
  ws_transport_enqueue_state(msg.c_str());
  log_info("[PB_ACK] %s req=%s space=%u", ack_type, s_ack_req.c_str(), space);
  s_dispatched_since_ack = 0;
}

static void abortRound() {
  speaker_abort();
  display_abort();
  head_abort();
  s_req = "";
  speaker_set_task_done_flag();
  head_set_task_done_flag();
  display_set_task_done_flag();
  s_dispatched_since_ack = 0;
  s_ack_req = "";
}


static bool model_slot_from_frame(const PbRxFrame& item, pb_model& out) {
  PackedFrame frame;
  if (!parse_packed_frame(item.data, item.len, frame)) {
    log_warn("[PB] packed frame parse failed");
    return false;
  }
  const char* err = nullptr;
  const size_t media_len = frame.bin_len > 0 ? static_cast<size_t>(frame.bin_len) : 0;
  if (!pb_model_from_json(frame.doc, frame.bin, media_len, out, err)) {
    log_warn("[PB] model parse rejected: %s", err ? err : "unknown");
    return false;
  }
  return true;
}

static void task_loop_pb_runtime(void* /*arg*/) {
  constexpr TickType_t kIdleWaitTicks = pdMS_TO_TICKS(2);

  for (;;) {
    PbRxFrame item{};
    if (xQueueReceive(s_frame_q, &item, kIdleWaitTicks) != pdTRUE) {
      continue;
    }
    MemGuard frame_guard{item.data};

    pb_model incoming{};
    if (!model_slot_from_frame(item, incoming)) {
      continue;
    }

    if (incoming.type == PB_MODEL_CANCEL) {
      log_info("[PB] cancel req=%s active_req=%s", incoming.req, s_req.c_str());
      if (incoming.req[0] == '\0' || s_req.isEmpty() || s_req.equals(incoming.req)) {
        abortRound();
      }
      pb_model_free(incoming);
      continue;
    }

    const int incoming_type = incoming.type;

    /* ── dispatch (was dispatchModel) ── */
    log_info("[PB] dispatch req=%s type=%s idx=%d level=%d anim=%u servo=%u audio=%d",
             incoming.req, pb_model_type_name(incoming.type), incoming.idx, incoming.level,
             (unsigned)incoming.anim_count, (unsigned)incoming.servo_count,
             incoming.audio ? incoming.audio->next_bin_len : 0);

    if (pb_model_is_chain_head(incoming)) {
      s_req = incoming.req;
      s_dispatched_since_ack = 0;
    }

    if (incoming.anim && incoming.anim_count > 0) {
      display_render_submit_pb_anim_frames_owned(incoming.anim, incoming.anim_count);
      incoming.anim = nullptr;
      incoming.anim_count = 0;
    }
    if (incoming.servo && incoming.servo_count > 0) {
      head_submit_pb_servo_chunk_owned(incoming.servo, incoming.servo_count);
      incoming.servo = nullptr;
      incoming.servo_count = 0;
    }
    if (incoming.audio && incoming.audio->bin && incoming.audio->next_bin_len > 0) {
      if (!speaker_submit_pb_audio_owned(incoming.audio)) {
        log_warn("[PB] audio dispatch failed req=%s idx=%d", incoming.req, incoming.idx);
      } else {
        incoming.audio = nullptr;
      }
    }

    s_ack_req = incoming.req;
    ++s_dispatched_since_ack;

    if (incoming_type == PB_MODEL_END || incoming_type == PB_MODEL_SINGLE) {
      log_info("[PB] complete req=%s idx=%d type=%s", incoming.req, incoming.idx,
               pb_model_type_name(incoming.type));
      speaker_stream_pcm16_end(incoming.ch > 0 ? incoming.ch : 1);
    }

    pb_model_free(incoming);

    if (s_dispatched_since_ack >= kAckBatchSize) {
      while (computeMinExecutorSpace() < (unsigned)kAckBatchSize) {
        vTaskDelay(pdMS_TO_TICKS(5));
      }
      sendAck(kAckChunk);
    }

    if (incoming_type == PB_MODEL_END || incoming_type == PB_MODEL_SINGLE) {
      speaker_signal_task_done();
      head_signal_task_done();
      display_signal_task_done();
      while (!(speaker_task_done() && head_task_done() && display_task_done())) {
        vTaskDelay(pdMS_TO_TICKS(5));
      }
      sendAck(kAckEnd);
    }
  }
}

/* ── 公开接口 ── */

bool setup_pb_runtime(void) {
  if (!s_frame_q) {
    s_frame_q = xQueueCreate(kPbFrameQDepth, sizeof(PbRxFrame));
    if (!s_frame_q) {
      log_error("[PB_RUNTIME] frame queue create failed");
      s_setup_ok = false;
      return false;
    }
  }
  s_req.reserve(37);
  s_ack_req.reserve(37);
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

bool pb_runtime_enqueue_frame(uint8_t* data, size_t length) {
  PbRxFrame item{};
  item.data = data;
  item.len = length;
  if (xQueueSend(s_frame_q, &item, 0) != pdTRUE) {
    PbRxFrame dropped{};
    if (xQueueReceive(s_frame_q, &dropped, 0) == pdTRUE) {
      free(dropped.data);
    }
    if (xQueueSend(s_frame_q, &item, 0) != pdTRUE) {
      log_warn("[PB_RUNTIME] frame queue full after drop; free new len=%u", (unsigned)length);
      return false;
    }
  }
  log_info("[PB_ENQ] pb_q_in len=%u q=%u", (unsigned)length,
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
