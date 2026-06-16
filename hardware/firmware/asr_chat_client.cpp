#include "asr_chat_client.h"

#include <ArduinoJson.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "camera_ws.h"
#include "cmd.h"
#include "oled.h"
#include "audio_capture.h"
#include "esp_heap_caps.h"
#include "head.h"
#include "task_trace.h"

static int pb_json_number_to_int(JsonVariantConst v, int defv) {
  if (v.isNull()) {
    return defv;
  }
  return (int)lround(v.as<double>());
}

/** pb 下行：audio.next_bin_len > 0 表示下一条 WS 为固定长度 PCM binary。 */
static size_t pb_read_audio_next_bin_len(const JsonDocument& doc) {
  if (!doc["audio"].is<JsonObjectConst>()) {
    return 0;
  }
  JsonVariantConst v = doc["audio"]["next_bin_len"];
  if (v.isNull()) {
    return 0;
  }
  const int n = pb_json_number_to_int(v, 0);
  return n > 0 ? (size_t)n : 0;
}

static size_t pb_read_asset_next_bin_len(JsonObjectConst asset) {
  if (asset.isNull()) {
    return 0;
  }
  JsonVariantConst v = asset["next_bin_len"];
  if (v.isNull()) {
    return 0;
  }
  const int n = pb_json_number_to_int(v, 0);
  return n > 0 ? (size_t)n : 0;
}

extern AsrChatClient asrChatClient;

bool deskbot_vision_uplink_paused(void) {
  return asrChatClient.isVisionUplinkPaused();
}

namespace {
/* 上一轮 TTS 刚结束就进入下一轮拾音时，喇叭尾音/房间反射易进麦克风 → ASR 连环触发
 *（短词如「老板」尤其明显）。无 AEC 时仅在播音/I2S 尾音窗口抑制上行，其余时间开麦。 */
/* I2S TX DMA 排空量级（与 audio_player stop_play 注释 ~340ms 同阶）+ 余量。 */
constexpr uint32_t kI2sDmaTailSuppressMs =
    (uint32_t)((DMA_BUF_COUNT * DMA_BUF_LEN * 1000UL) / (uint32_t)SAMPLE_RATE) + 200UL;

extern "C" void audio_yield_hook() {
  /* 严禁在此调用 loop()：loop() 内的 flush_deferred_pb_stream_end 会调
   * audio_stream_pcm16_end，该函数入队 kStreamPcm16End 后等 done_sem；
   * 但 done_sem 只有 audio_play_task 处理完 kStreamPcm16End 才会 give，
   * 而此处正运行在 audio_play_task 上——自我死锁。
   *
   * i2s_write(portMAX_DELAY) 在 DMA 满时会阻塞并交出 CPU，主循环得以
   * 调用 ws_.loop() 泵收新 pb_chunk，无需在这里主动推送。 */
  taskYIELD();
}

}  // namespace

AsrChatClient::PbEnqueueAction AsrChatClient::parsePbEnqueueAction(const JsonDocument& doc) {
  String a = doc["action"].is<String>() ? doc["action"].as<String>() : String("");
  a.toLowerCase();
  if (a == "append") {
    return PbEnqueueAction::kAppend;
  }
  if (a == "default") {
    return PbEnqueueAction::kDefault;
  }
  if (a == "opportunistic") {
    log_warn("[PB] deprecated action=opportunistic, treat as level=0 append");
    return PbEnqueueAction::kAppend;
  }
  return PbEnqueueAction::kReplace;
}

int8_t AsrChatClient::parsePbLevel(const JsonDocument& doc, bool legacy_opportunistic) {
  if (legacy_opportunistic) {
    return 0;
  }
  if (doc["level"].is<int>()) {
    return (int8_t)constrain(doc["level"].as<int>(), 0, 3);
  }
  if (doc["level"].is<double>()) {
    return (int8_t)constrain((int)lround(doc["level"].as<double>()), 0, 3);
  }
  return 1;
}

int AsrChatClient::pbCountHigherPrioritySeqs(int8_t level) const {
  int n = 0;
  for (size_t i = 0; i < pb_seq_level_count_; i++) {
    if (pb_seq_levels_[i] > level) {
      n++;
    }
  }
  return n;
}

AsrChatClient::PbQueueDecision AsrChatClient::pbDecideChainHead(int8_t level, PbEnqueueAction action) const {
  if (pb_inflight_seq_count_ == 0) {
    if (action == PbEnqueueAction::kReplace) {
      return PbQueueDecision::kClear;
    }
    return PbQueueDecision::kAppend;
  }
  const int8_t ql = pb_queue_level_;
  if (ql < 0 || level > ql) {
    return PbQueueDecision::kClear;
  }
  if (action == PbEnqueueAction::kAppend) {
    return PbQueueDecision::kAppend;
  }
  if (level == ql && action == PbEnqueueAction::kReplace) {
    return PbQueueDecision::kClear;
  }
  if (level <= ql && action == PbEnqueueAction::kDefault) {
    const int n_high = pbCountHigherPrioritySeqs(level);
    if (n_high <= 1) {
      return PbQueueDecision::kAppend;
    }
    return PbQueueDecision::kDrop;
  }
  if (level < ql && action == PbEnqueueAction::kReplace) {
    log_warn("[PB] drop chain head: level=%d < queue_level=%d action=replace", (int)level, (int)ql);
    return PbQueueDecision::kDrop;
  }
  log_warn("[PB] drop chain head: level=%d queue_level=%d action=%d", (int)level, (int)ql, (int)action);
  return PbQueueDecision::kDrop;
}

void AsrChatClient::pbSeqTrackPush(int8_t level) {
  if (pb_seq_level_count_ < kPbMaxTrackedSeqLevels) {
    pb_seq_levels_[pb_seq_level_count_++] = level;
  }
}

void AsrChatClient::pbSeqTrackPop() {
  if (pb_seq_level_count_ == 0) {
    return;
  }
  pb_seq_level_count_--;
  for (size_t i = 0; i < pb_seq_level_count_; i++) {
    pb_seq_levels_[i] = pb_seq_levels_[i + 1];
  }
}

void AsrChatClient::pbDrainWorkersForNewSequence(bool drain_motor) {
  if (!drain_motor) {
    /* 手势 pb_single：只停音频/OLED，不动 motor 队列。 */
    audio_play_reset();
    oled_render_reset();
  } else {
    audio_play_reset();
    oled_render_reset();
    head_clear_motor_pending();
  }
  pbReset(/*stop_audio=*/false);
  pb_audio_stream_started_ = false;
  pb_deferred_stream_end_pending_ = false;
}

void AsrChatClient::pbOnSequenceComplete() {
  if (pb_inflight_seq_count_ > 0) {
    pb_inflight_seq_count_--;
  }
  pbSeqTrackPop();
  if (pb_inflight_seq_count_ == 0) {
    pb_queue_level_ = -1;
    pb_active_ = false;
    pbSignalTtsRoundComplete();
  }
}

AsrChatClient::AsrChatClient() {}

void AsrChatClient::pbFreePendingAssets() {
  for (uint8_t i = 0; i < pb_asset_count_; i++) {
    if (pb_asset_bufs_[i]) {
      heap_caps_free(pb_asset_bufs_[i]);
      pb_asset_bufs_[i] = nullptr;
    }
    pb_asset_lens_[i] = 0;
  }
  pb_asset_count_ = 0;
}

void AsrChatClient::pbBuildPendingBinQueue(const JsonDocument& doc) {
  pb_pending_bin_count_ = 0;
  pb_pending_bin_cursor_ = 0;
  const size_t pcm_len = pb_read_audio_next_bin_len(doc);
  if (pcm_len > 0 && pb_pending_bin_count_ < kPbMaxBinsPerChunk) {
    pb_pending_bin_kinds_[pb_pending_bin_count_] = PbBinKind::kPcm;
    pb_pending_bin_lens_[pb_pending_bin_count_] = pcm_len;
    pb_pending_bin_count_++;
  }
  if (doc["assets"].is<JsonArrayConst>()) {
    for (JsonObjectConst asset : doc["assets"].as<JsonArrayConst>()) {
      const size_t asset_len = pb_read_asset_next_bin_len(asset);
      if (asset_len == 0) {
        continue;
      }
      if (pb_pending_bin_count_ >= kPbMaxBinsPerChunk) {
        log_warn("[PB] assets[] bin queue truncated at %u", (unsigned)kPbMaxBinsPerChunk);
        break;
      }
      pb_pending_bin_kinds_[pb_pending_bin_count_] = PbBinKind::kAsset;
      pb_pending_bin_lens_[pb_pending_bin_count_] = asset_len;
      pb_pending_bin_count_++;
    }
  }
}

void AsrChatClient::pbAdvanceBinQueue() {
  pb_pending_bin_cursor_++;
  if (pb_pending_bin_cursor_ < pb_pending_bin_count_) {
    pb_expect_bin_kind_ = pb_pending_bin_kinds_[pb_pending_bin_cursor_];
    pb_expect_bin_len_ = pb_pending_bin_lens_[pb_pending_bin_cursor_];
    pb_expect_bin_ = true;
    pb_expect_bin_since_ms_ = millis();
  } else {
    pb_expect_bin_ = false;
    pb_expect_bin_len_ = 0;
  }
}

void AsrChatClient::pbFreePendingAnim() {
  if (pb_pending_anim_buf_) {
    ::free(pb_pending_anim_buf_);
    pb_pending_anim_buf_ = nullptr;
  }
  pb_pending_anim_len_ = 0;
}

void AsrChatClient::pbReset(bool stop_audio) {
  const uint8_t pb_ch_before_reset = pb_ch_;
  pbFreePendingAssets();
  pb_pending_bin_count_ = 0;
  pb_pending_bin_cursor_ = 0;
  pb_active_ = false;
  pb_req_.remove(0);
  pb_next_idx_ = 0;
  pb_expect_bin_ = false;
  pb_expect_bin_len_ = 0;
  pb_expect_bin_since_ms_ = 0;
  pb_sr_ = 0;
  pb_ch_ = 0;
  pb_fmt_.remove(0);
  pb_pending_idx_ = 0;
  pb_pending_chunk_ms_ = 0;
  pbFreePendingAnim();
  pb_pending_servo_seg_count_ = 0;
  pb_ack_servo_report_valid_ = false;
  pb_ack_servo_report_x_ = 0;
  pb_ack_servo_report_y_ = 0;
  pb_last_ack_idx_ = 0;
  pb_bins_rx_count_ = 0;
  pb_pcm_bytes_rx_total_ = 0;
  pb_end_waiting_bin_ = false;
  pb_end_idx_ = 0;

  pb_ack_out_pending_ = false;
  pb_ack_out_req_.remove(0);
  pb_ack_out_idx_ = 0;
  pb_ack_out_buf_ms_ = 0;
  pb_last_pb_ack_sent_wall_ms_ = 0;
  pb_ack_bypass_throttle_ = false;
  pb_pending_enqueue_action_ = PbEnqueueAction::kReplace;
  pb_queue_level_ = -1;
  pb_inflight_seq_count_ = 0;
  pb_seq_level_count_ = 0;
  head_drain_pb_motor_ack_queue();

  pb_audio_buf_ms_est_ = 0;
  pb_last_buf_decay_ms_ = millis();
  if (stop_audio) {
    mic_suppress_until_ms_ = millis() + (unsigned long)kI2sDmaTailSuppressMs;
    if (pb_deferred_stream_end_pending_) {
      pb_deferred_stream_end_pending_ = false;
      audio_stream_pcm16_end(pb_deferred_stream_end_ch_);
    } else if (pb_audio_stream_started_) {
      const uint8_t ch_end = (pb_ch_before_reset == 0 || pb_ch_before_reset > 2) ? 1 : pb_ch_before_reset;
      audio_stream_pcm16_end(ch_end);
    }
    pb_audio_stream_started_ = false;
  }
}

void AsrChatClient::pbProtocolError(const char* why) {
  log_warn("[PB] protocol error: %s (req=%s expect_bin=%d expect_len=%u next_idx=%u)",
           why ? why : "?", pb_req_.c_str(), (int)pb_expect_bin_, (unsigned)pb_expect_bin_len_,
           (unsigned)pb_next_idx_);
  audio_play_emergency_flush();
  pbReset(/*stop_audio=*/true);
  /* 协议错位后兜底标记本轮 reply 结束，避免 runVoiceRound 等 30s 超时。 */
  pbSignalTtsRoundComplete();
}

void AsrChatClient::pbSubmitAnimIfAny() {
  if ((!pb_pending_anim_buf_ || pb_pending_anim_len_ == 0) && pb_asset_count_ == 0) {
    return;
  }
  oled_render_submit_pb_vector_json_owned(pb_pending_anim_buf_, pb_pending_anim_len_, pb_asset_bufs_,
                                          pb_asset_lens_, pb_asset_count_);
  pb_pending_anim_buf_ = nullptr;
  pb_pending_anim_len_ = 0;
  for (uint8_t i = 0; i < pb_asset_count_; i++) {
    pb_asset_bufs_[i] = nullptr;
    pb_asset_lens_[i] = 0;
  }
  pb_asset_count_ = 0;
}

/* pb 舵机：在 budget_ms 内按 50Hz(20ms/拍) 走完主行程。步进上限取经验值，过大易抖、过小易超时；
 * 尾段剩余 <20ms 时 motor_task 不再 write，由 MotorCmd.ms 对齐本条 servo[i].ms。 */
static constexpr uint8_t k_pb_servo_max_step_deg = 12;

static uint8_t compute_step_deg_for_ms(int dx_deg, int dy_deg, uint16_t ms) {
  int d = abs(dx_deg);
  if (abs(dy_deg) > d) d = abs(dy_deg);
  if (d <= 0) return 1;
  if (ms < 20) ms = 20;
  int ticks = (int)((ms + 19) / 20);  // motor_task 固定 20ms/tick
  if (ticks <= 0) ticks = 1;
  int step = (d + ticks - 1) / ticks;
  if (step < 1) step = 1;
  if (step > (int)k_pb_servo_max_step_deg) step = (int)k_pb_servo_max_step_deg;
  return (uint8_t)step;
}

static bool pb_enqueue_one_servo_seg(int xm, int ym, int x, int y, uint16_t seg_ms, uint32_t chunk_idx,
                                     size_t seg_i, size_t seg_n) {
  uint32_t budget_ms = seg_ms;
  if (budget_ms == 0) {
    budget_ms = 20;
  }

  int x_now = head_read_x();
  int y_now = head_read_y_logic();
  int x_target = x_now;
  int y_target = y_now;
  bool drive_x = true;
  bool drive_y = true;

  if (xm == (int)HEAD_SERVO_HOLD) {
    drive_x = false;
  } else if (xm == (int)HEAD_SERVO_ABS) {
    x_target = constrain(x, X_MIN_LIMIT, X_MAX_LIMIT);
  } else if (xm == (int)HEAD_SERVO_REL) {
    x_target = constrain(x_now + x, X_MIN_LIMIT, X_MAX_LIMIT);
  } else {
    drive_x = false;
  }

  if (ym == (int)HEAD_SERVO_HOLD) {
    drive_y = false;
  } else if (ym == (int)HEAD_SERVO_ABS) {
    y_target = constrain(y, Y_MIN_LIMIT, Y_MAX_LIMIT);
  } else if (ym == (int)HEAD_SERVO_REL) {
    y_target = constrain(y_now + y, Y_MIN_LIMIT, Y_MAX_LIMIT);
  } else {
    drive_y = false;
  }

  if (!drive_x && !drive_y) {
    return false;
  }

  const int dx = drive_x ? (x_target - x_now) : 0;
  const int dy = drive_y ? (y_target - y_now) : 0;
  if (dx == 0 && dy == 0) {
    return false;
  }

  bool move_x = (dx != 0);
  bool move_y = (dy != 0);
  /* 上看/下看/左看/右看：服务端 xm=ym=0 绝对角时，一轴在中位、一轴在限位 → 只动该轴（与 head_up/left 一致）。 */
  if (xm == (int)HEAD_SERVO_ABS && ym == (int)HEAD_SERVO_ABS) {
    const bool x_center = abs(x_target - X_CENTER) <= 3;
    const bool y_center = abs(y_target - Y_CENTER) <= 3;
    const bool x_limit = (x_target == X_MIN_LIMIT || x_target == X_MAX_LIMIT);
    const bool y_limit = (y_target == Y_MIN_LIMIT || y_target == Y_MAX_LIMIT);
    if (y_limit && x_center) {
      move_x = false;
    } else if (x_limit && y_center) {
      move_y = false;
    }
  }
  if (!move_x && !move_y) {
    return false;
  }

  const int eff_dx = move_x ? dx : 0;
  const int eff_dy = move_y ? dy : 0;
  const uint16_t ms_budget = (budget_ms > 65535u) ? 65535u : (uint16_t)budget_ms;
  const uint8_t step_deg = compute_step_deg_for_ms(eff_dx, eff_dy, ms_budget);
  const uint8_t qxm = move_x ? HEAD_SERVO_ABS : HEAD_SERVO_HOLD;
  const uint8_t qym = move_y ? HEAD_SERVO_ABS : HEAD_SERVO_HOLD;

  head_servo_cmd_async(qxm, qym, x_target, y_target, step_deg, ms_budget);
  return true;
}

bool AsrChatClient::pbApplyServoArrayIfAny(uint32_t chunk_idx) {
  if (pb_pending_servo_seg_count_ == 0) {
    return false;
  }
  const size_t n = pb_pending_servo_seg_count_;
  bool any = false;
  for (size_t i = 0; i < n; i++) {
    const PbServoSeg& seg = pb_pending_servo_segs_[i];
    if (pb_enqueue_one_servo_seg(seg.xm, seg.ym, seg.x, seg.y, seg.ms, chunk_idx, i, n)) {
      any = true;
      pb_ack_servo_report_valid_ = true;
      int x_now = head_read_x();
      int y_now = head_read_y_logic();
      if (seg.xm == (int)HEAD_SERVO_ABS) {
        pb_ack_servo_report_x_ = constrain(seg.x, X_MIN_LIMIT, X_MAX_LIMIT);
      } else if (seg.xm == (int)HEAD_SERVO_REL) {
        pb_ack_servo_report_x_ = constrain(x_now + seg.x, X_MIN_LIMIT, X_MAX_LIMIT);
      }
      if (seg.ym == (int)HEAD_SERVO_ABS) {
        pb_ack_servo_report_y_ = constrain(seg.y, Y_MIN_LIMIT, Y_MAX_LIMIT);
      } else if (seg.ym == (int)HEAD_SERVO_REL) {
        pb_ack_servo_report_y_ = constrain(y_now + seg.y, Y_MIN_LIMIT, Y_MAX_LIMIT);
      }
    }
  }
  return any;
}

void AsrChatClient::pbUpdateAudioBufDecayWall() {
  unsigned long now = millis();
  if (pb_last_buf_decay_ms_ == 0) {
    pb_last_buf_decay_ms_ = now;
  }
  /* 流已结束后仍按墙钟衰减，供半双工抑制覆盖播放队列排空前的估算缓冲。 */
  if (pb_audio_buf_ms_est_ > 0) {
    int32_t dec = (int32_t)(now - pb_last_buf_decay_ms_);
    if (dec > 0) {
      pb_audio_buf_ms_est_ -= dec;
      if (pb_audio_buf_ms_est_ < 0) {
        pb_audio_buf_ms_est_ = 0;
      }
    }
  }
  pb_last_buf_decay_ms_ = now;
}

void AsrChatClient::pbScheduleMotorAck(const char* req_cstr, uint32_t idx) {
  if (req_cstr == nullptr || req_cstr[0] == '\0') {
    return;
  }
  pbUpdateAudioBufDecayWall();
  pb_ack_out_req_ = req_cstr;
  pb_ack_out_idx_ = idx;
  pb_ack_out_buf_ms_ = pb_audio_buf_ms_est_;
  pb_ack_out_pending_ = true;
  pb_ack_bypass_throttle_ = true;
  if (idx > pb_last_ack_idx_) {
    pb_last_ack_idx_ = idx;
  }
}

void AsrChatClient::pbMaybeAck(uint32_t idx) {
  if (!pb_active_ || pb_req_.isEmpty()) {
    return;
  }

  pbUpdateAudioBufDecayWall();

  if (idx < pb_last_ack_idx_) {
    return;
  }
  pb_last_ack_idx_ = idx;
  pb_ack_out_req_ = pb_req_;
  pb_ack_out_idx_ = idx;
  /* 上报播放队列深度供服务端流水线；wall-clock 估算在排空时易长期为 0。 */
  const uint32_t chunk_ms = pb_pending_chunk_ms_ > 0 ? pb_pending_chunk_ms_ : 127u;
  const unsigned qd = audio_play_input_queue_depth();
  pb_ack_out_buf_ms_ = (int32_t)(qd * chunk_ms);
  if (pb_ack_out_buf_ms_ < pb_audio_buf_ms_est_) {
    pb_ack_out_buf_ms_ = pb_audio_buf_ms_est_;
  }
  pb_ack_out_pending_ = true;
}

void AsrChatClient::flushPendingPbAck() {
  if (!pb_ack_out_pending_) {
    return;
  }
  if (!ws_.isConnected()) {
    pb_ack_out_pending_ = false;
    return;
  }
  const unsigned long now_wall = millis();
  if (!pb_ack_bypass_throttle_ &&
      (pb_last_pb_ack_sent_wall_ms_ != 0) &&
      (now_wall - pb_last_pb_ack_sent_wall_ms_ < 80UL)) {
    return;
  }
  pb_ack_bypass_throttle_ = false;
  pb_last_pb_ack_sent_wall_ms_ = now_wall;
  pb_ack_out_pending_ = false;
  int servo_x_deg = head_read_x();
  int servo_y_deg = head_read_y_logic();
  if (pb_ack_servo_report_valid_) {
    servo_x_deg = pb_ack_servo_report_x_;
    servo_y_deg = pb_ack_servo_report_y_;
    pb_ack_servo_report_valid_ = false;
  }
  char msg[256];
  const int n = snprintf(msg, sizeof(msg),
                         "{\"type\":\"pb_ack\",\"req\":\"%s\",\"idx\":%u,\"audio_buf_ms\":%d,"
                         "\"servo\":{\"x\":%d,\"y\":%d,\"x_min\":%d,\"x_max\":%d,\"y_min\":%d,\"y_max\":%d}}",
                         pb_ack_out_req_.c_str(), (unsigned)pb_ack_out_idx_, (int)pb_ack_out_buf_ms_,
                         servo_x_deg, servo_y_deg, X_MIN_LIMIT, X_MAX_LIMIT, Y_MIN_LIMIT, Y_MAX_LIMIT);
  if (n <= 0 || (size_t)n >= sizeof(msg)) {
    log_warn("[PB] pb_ack snprintf truncated");
    return;
  }
  sendJson(msg);
}

void AsrChatClient::pbSignalTtsRoundComplete() {
  reply_done_ = true;
  tts_active_ = false;
}

void AsrChatClient::pbFinishChunkBins(uint32_t pending_idx_snap, bool closing_pb_end_bin,
                                    uint8_t ch_for_stream_end) {
  if (pb_next_idx_ <= pending_idx_snap) {
    pb_next_idx_ = pending_idx_snap + 1;
  }

  flushDeferredPbJson(/*pump_ws_after=*/false);

  (void)pbDispatchChunkPreamble(pending_idx_snap);
  pbMaybeAck(pending_idx_snap);
  pb_last_ack_idx_ = pending_idx_snap;
  pb_ack_bypass_throttle_ = true;

  pb_pending_chunk_ms_ = 0;
  pb_pending_servo_seg_count_ = 0;

  if (closing_pb_end_bin) {
    const bool stream_alive = pb_audio_stream_started_;
    pb_deferred_stream_end_ch_ =
        (ch_for_stream_end == 0 || ch_for_stream_end > 2) ? 1 : ch_for_stream_end;
    pb_deferred_stream_end_pending_ = stream_alive;
    pb_audio_stream_started_ = false;
    pb_end_waiting_bin_ = false;
    pb_end_idx_ = 0;
    log_info("[PB] complete end_bin req=%s pending_idx=%u inflight=%u (defer pcm16_end to loop)",
             pb_req_.c_str(), (unsigned)pb_pending_idx_, (unsigned)pb_inflight_seq_count_);
    pbMaybeAck(pending_idx_snap);
    pb_ack_bypass_throttle_ = true;
    pbOnSequenceComplete();
  }
}

bool AsrChatClient::pbDispatchChunkPreamble(uint32_t chunk_idx) {
  /* 音频 / OLED / 舵机异步入队；anim/servo 时长仅看各自 ms[]，与 chunk_ms（PCM）无关。 */
  pbSubmitAnimIfAny();
  return pbApplyServoArrayIfAny(chunk_idx);
}

void AsrChatClient::pbPumpWsWhileExpectBin(uint16_t max_pumps) {
  for (uint16_t i = 0; i < max_pumps && pb_expect_bin_ && ws_.isConnected(); i++) {
    ws_.loop();
    flushPendingPbAck();
    yield();
  }
}

bool AsrChatClient::pbDeferEnqueue(const uint8_t* payload, size_t length) {
  if (payload == nullptr || length == 0 || length > kPbDeferMaxBytes) {
    log_warn("[PB] defer reject len=%u", (unsigned)length);
    return false;
  }
  const uint8_t next_tail = static_cast<uint8_t>((pb_defer_tail_ + 1) % kPbDeferQueueDepth);
  if (next_tail == pb_defer_head_) {
    log_warn("[PB] defer queue full (depth=%u), drop len=%u", (unsigned)kPbDeferQueueDepth,
             (unsigned)length);
    return false;
  }
  uint8_t* buf = pb_defer_bufs_[pb_defer_tail_];
  if (buf == nullptr) {
    buf = static_cast<uint8_t*>(
        heap_caps_malloc(kPbDeferMaxBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (buf == nullptr) {
      buf = static_cast<uint8_t*>(malloc(kPbDeferMaxBytes));
    }
    if (buf == nullptr) {
      log_error("[PB] defer alloc failed %u bytes", (unsigned)kPbDeferMaxBytes);
      return false;
    }
    pb_defer_bufs_[pb_defer_tail_] = buf;
  }
  memcpy(buf, payload, length);
  pb_defer_lens_[pb_defer_tail_] = length;
  pb_defer_tail_ = next_tail;
  return true;
}

uint8_t AsrChatClient::pbDeferQueueDepth() const {
  return static_cast<uint8_t>((pb_defer_tail_ + kPbDeferQueueDepth - pb_defer_head_) %
                              kPbDeferQueueDepth);
}

void AsrChatClient::flushDeferredPbJson(bool pump_ws_after) {
  (void)pump_ws_after;
  /* 正在等 BIN 时不得再 parse 后续 pb_end JSON，否则覆盖 expect_len（表情+TTS 常见连发两帧 JSON）。 */
  if (pb_expect_bin_) {
    return;
  }
  if (pb_defer_head_ == pb_defer_tail_) {
    return;
  }

  const uint8_t slot = pb_defer_head_;
  const size_t len = pb_defer_lens_[slot];
  const uint8_t* const wire = pb_defer_bufs_[slot];
  pb_defer_head_ = static_cast<uint8_t>((pb_defer_head_ + 1) % kPbDeferQueueDepth);
  if (len == 0 || wire == nullptr) {
    flushDeferredPbJson(pump_ws_after);
    return;
  }

  JsonDocument doc;
  if (deserializeJson(doc, wire, len)) {
    log_warn("[PB] defer deserialize failed len=%u", (unsigned)len);
    flushDeferredPbJson(pump_ws_after);
    return;
  }
  (void)pbParseAndStage(doc);
  if (pb_expect_bin_) {
    return;
  }
  flushDeferredPbJson(false);
}

bool AsrChatClient::pbParseAndStage(const JsonDocument& doc) {
  String type = doc["type"].is<String>() ? doc["type"].as<String>() : String("");
  if (type != "pb_start" && type != "pb_chunk" && type != "pb_end" && type != "pb_single") {
    return false;
  }
  String req = doc["req"].is<String>() ? doc["req"].as<String>() : String("");
  if (req.isEmpty()) {
    pbProtocolError("missing req");
    return true;
  }

  uint32_t idx = doc["idx"].is<uint32_t>() ? doc["idx"].as<uint32_t>() : 0;

  if (!pb_suppress_tail_req_.isEmpty()) {
    if (req != pb_suppress_tail_req_) {
      pb_suppress_tail_req_.remove(0);
    } else if (!((type == "pb_start" || type == "pb_single") && idx == 0u)) {
      log_info("[PB] ignore stale pb after abort type=%s idx=%u req=%s", type.c_str(), (unsigned)idx,
               req.c_str());
      return true;
    } else {
      pb_suppress_tail_req_.remove(0);
    }
  }

  uint32_t chunk_ms = doc["chunk_ms"].is<uint32_t>() ? doc["chunk_ms"].as<uint32_t>() : 0;
  String action_raw = doc["action"].is<String>() ? doc["action"].as<String>() : String("");
  action_raw.toLowerCase();
  const bool legacy_opportunistic = (action_raw == "opportunistic");
  const PbEnqueueAction chunk_action = parsePbEnqueueAction(doc);
  const int8_t chunk_level = parsePbLevel(doc, legacy_opportunistic);

  const bool is_chain_head = (type == "pb_start" || type == "pb_single") && (idx == 0u);
  if (is_chain_head) {
    const PbQueueDecision qd = pbDecideChainHead(chunk_level, chunk_action);
    if (qd == PbQueueDecision::kDrop) {
      log_warn("[PB] drop chain head req=%s type=%s level=%d queue_level=%d n_high=%d",
               req.c_str(), type.c_str(), (int)chunk_level, (int)pb_queue_level_,
               pbCountHigherPrioritySeqs(chunk_level));
      return true;
    }
    const bool force_restart_same_req =
        pb_active_ && (req == pb_req_) && (chunk_action == PbEnqueueAction::kReplace);
    if (qd == PbQueueDecision::kClear || force_restart_same_req) {
      /* 服务端「摇头 N 次」常拆成多帧 pb_single（仅 servo、无音频）；replace 时勿清空 motor 队列。 */
      const size_t next_bin_len_head = pb_read_audio_next_bin_len(doc);
      /* 无 PCM 的 pb_single = 纯舵机手势（摇头/点头分多包）；禁止 replace 时 head_motor_reset。 */
      const bool servo_only_gesture = (type == "pb_single") && (next_bin_len_head == 0);
      const bool drain_motor = !servo_only_gesture;
      pbDrainWorkersForNewSequence(drain_motor);
      if (servo_only_gesture) {
        log_info("[PB] pb_single gesture: keep motor queue depth=%u",
                 (unsigned)head_motor_input_queue_depth());
      } else if (type == "pb_single") {
        log_warn("[PB] pb_single with audio bin: motor queue cleared");
      }
      pb_inflight_seq_count_ = 0;
      pb_seq_level_count_ = 0;
      pb_queue_level_ = chunk_level;
      const char* act_s = (chunk_action == PbEnqueueAction::kAppend)    ? "append"
                          : (chunk_action == PbEnqueueAction::kDefault) ? "default"
                                                                      : "replace";
      log_info("[PB] new sequence clear: req=%s type=%s idx=%u level=%d action=%s",
               req.c_str(), type.c_str(), (unsigned)idx, (int)chunk_level, act_s);
    } else {
      const char* act_s = (chunk_action == PbEnqueueAction::kDefault) ? "default" : "append";
      log_info("[PB] new sequence append: req=%s type=%s idx=%u level=%d queue_level=%d action=%s",
               req.c_str(), type.c_str(), (unsigned)idx, (int)chunk_level, (int)pb_queue_level_, act_s);
    }
    pb_inflight_seq_count_++;
    pbSeqTrackPush(chunk_level);
    pb_active_ = true;
    pb_req_ = req;
    pb_next_idx_ = 0;
    pb_bins_rx_count_ = 0;
    pb_pcm_bytes_rx_total_ = 0;
    /* pb_start：尽早打开半双工，避免首包 BIN 前上行真实 mic。 */
    server_started_reply_ = true;
    tts_active_ = true;
  } else if (!pb_active_ || req != pb_req_) {
    pbProtocolError("pb_chunk/pb_end without active matching req");
    return true;
  }

  if (!pb_active_ || req != pb_req_) {
    pbProtocolError("req mismatch after reset");
    return true;
  }

  if (idx != pb_next_idx_) {
    if (idx > pb_next_idx_) {
      if (pb_expect_bin_) {
        log_warn("[PB] idx gap while expect BIN: expected %u got %u (JSON should stay queued)",
                 (unsigned)pb_next_idx_, (unsigned)idx);
        return true;
      }
      log_warn("[PB] idx gap: expected %u got %u, resync (do not reset audio)",
               (unsigned)pb_next_idx_, (unsigned)idx);
      pb_next_idx_ = idx;
    } else {
      log_warn("[PB] duplicate idx %u (expected %u), skip",
               (unsigned)idx, (unsigned)pb_next_idx_);
      return true;
    }
  }

  if (doc["sr"].is<uint32_t>()) {
    pb_sr_ = doc["sr"].as<uint32_t>();
  }
  if (doc["ch"].is<int>()) {
    pb_ch_ = (uint8_t)doc["ch"].as<int>();
  } else if (doc["ch"].is<double>()) {
    pb_ch_ = (uint8_t)doc["ch"].as<double>();
  } else if (doc["ch"].is<uint8_t>()) {
    pb_ch_ = doc["ch"].as<uint8_t>();
  }
  if (doc["fmt"].is<String>()) {
    pb_fmt_ = doc["fmt"].as<String>();
  }

  /* volume（0–100）：有值则更新播放音量比例，省略则沿用上次值。 */
  if (doc["volume"].is<int>()) {
    const int vol = constrain(doc["volume"].as<int>(), 0, 100);
    pb_volume_ratio_ = vol / 100.0f;
    log_info("[PB] volume=%d ratio=%.2f", vol, pb_volume_ratio_);
  }

  /* cam_fps（>0）：有值则动态调整相机上行帧率，省略或 0 不改。 */
  if (doc["cam_fps"].is<int>()) {
    const int fps = doc["cam_fps"].as<int>();
    if (fps > 0) {
      camera_ws_set_fps((uint32_t)fps);
    }
  }

  if (doc["pb_ver"].is<int>() && doc["pb_ver"].as<int>() != 2) {
    log_warn("[PB] pb_ver=%d (expected 2)", doc["pb_ver"].as<int>());
  }

  pbFreePendingAnim();
  if (doc["anim"].is<JsonArrayConst>()) {
    JsonArrayConst anim_arr = doc["anim"].as<JsonArrayConst>();
    if (anim_arr.size() > 0) {
      const size_t need = measureJson(anim_arr);
      if (need > 0) {
        char* buf = static_cast<char*>(heap_caps_malloc(need + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (buf == nullptr) {
          buf = static_cast<char*>(malloc(need + 1));
        }
        if (buf != nullptr) {
          const size_t n = serializeJson(anim_arr, buf, need + 1);
          buf[n] = '\0';
          pb_pending_anim_buf_ = buf;
          pb_pending_anim_len_ = n;
          log_info("[PB] anim[] staged idx=%u segs=%u len=%u", (unsigned)idx, (unsigned)anim_arr.size(),
                   (unsigned)n);
        }
      }
    }
  } else if (!doc["anim"].isNull()) {
    log_warn("[PB] anim must be array (pb_ver 2); ignore idx=%u", (unsigned)idx);
  }

  pb_pending_servo_seg_count_ = 0;
  if (doc["servo"].is<JsonArrayConst>()) {
    JsonArrayConst servo_arr = doc["servo"].as<JsonArrayConst>();
    for (JsonObjectConst item : servo_arr) {
      if (pb_pending_servo_seg_count_ >= kPbMaxServoSegsPerChunk) {
        log_warn("[PB] servo[] truncated at %u", (unsigned)kPbMaxServoSegsPerChunk);
        break;
      }
      PbServoSeg& seg = pb_pending_servo_segs_[pb_pending_servo_seg_count_++];
      seg.xm = constrain(pb_json_number_to_int(item["xm"], 2), 0, 2);
      seg.ym = constrain(pb_json_number_to_int(item["ym"], 2), 0, 2);
      seg.x = pb_json_number_to_int(item["x"], 0);
      seg.y = pb_json_number_to_int(item["y"], 0);
      seg.ms = (uint16_t)constrain(pb_json_number_to_int(item["ms"], 0), 0, 65535);
    }
    if (pb_pending_servo_seg_count_ > 0) {
      log_info("[PB] servo[] staged idx=%u segs=%u", (unsigned)idx,
               (unsigned)pb_pending_servo_seg_count_);
    }
  } else if (!doc["servo"].isNull()) {
    log_warn("[PB] servo must be array (pb_ver 2); ignore idx=%u", (unsigned)idx);
  }

  pbFreePendingAssets();
  pbBuildPendingBinQueue(doc);
  const size_t next_bin_len = pb_read_audio_next_bin_len(doc);
  const bool expect_pcm_bin = (next_bin_len > 0);
  bool expect_asset_bins = false;
  if (doc["assets"].is<JsonArrayConst>()) {
    for (JsonObjectConst asset : doc["assets"].as<JsonArrayConst>()) {
      if (pb_read_asset_next_bin_len(asset) > 0) {
        expect_asset_bins = true;
        break;
      }
    }
  }
  const bool has_payload = (pb_pending_anim_buf_ != nullptr && pb_pending_anim_len_ > 0) ||
                           (pb_pending_servo_seg_count_ > 0) || expect_pcm_bin || expect_asset_bins;
  if (!has_payload) {
    pbProtocolError("R0: chunk needs anim[], servo[], audio or assets");
    return true;
  }

  if (pb_pending_bin_count_ > 0) {
    if (expect_pcm_bin) {
      if (pb_sr_ == 0 || pb_ch_ == 0 || pb_fmt_.isEmpty()) {
        pbProtocolError("audio.next_bin_len but missing sr/ch/fmt");
        return true;
      }
      if (pb_fmt_ != "s16le") {
        pbProtocolError("unsupported fmt (need s16le)");
        return true;
      }
      if ((next_bin_len & 1u) != 0u) {
        pbProtocolError("audio.next_bin_len must be even");
        return true;
      }
    }
    pb_expect_bin_ = true;
    pb_expect_bin_kind_ = pb_pending_bin_kinds_[0];
    pb_expect_bin_len_ = pb_pending_bin_lens_[0];
    pb_expect_bin_since_ms_ = millis();
    pb_pending_idx_ = idx;
    pb_pending_chunk_ms_ = chunk_ms;
    if (expect_pcm_bin && pb_pending_chunk_ms_ == 0 && pb_sr_ > 0) {
      const uint32_t ch = (pb_ch_ == 0) ? 1u : (uint32_t)pb_ch_;
      pb_pending_chunk_ms_ =
          (uint32_t)((uint64_t)next_bin_len * 1000ULL / ((uint64_t)pb_sr_ * ch * 2ULL));
    }
    log_info("[PB] expect BIN req=%s type=%s idx=%u bins=%u first_kind=%u first_len=%u chunk_ms=%u "
             "heap=%u",
             pb_req_.c_str(), type.c_str(), (unsigned)idx, (unsigned)pb_pending_bin_count_,
             (unsigned)pb_expect_bin_kind_, (unsigned)pb_expect_bin_len_,
             (unsigned)pb_pending_chunk_ms_, (unsigned)ESP.getFreeHeap());
    if (type == "pb_end" || type == "pb_single") {
      pb_end_waiting_bin_ = true;
      pb_end_idx_ = idx;
    } else {
      pb_end_waiting_bin_ = false;
    }
    pb_pending_enqueue_action_ = chunk_action;
  } else {
    const bool sequence_end = (type == "pb_end" || type == "pb_single");
    pbSubmitAnimIfAny();
    (void)pbApplyServoArrayIfAny(idx);
    pbMaybeAck(idx);
    pb_ack_bypass_throttle_ = true;
    if (sequence_end) {
      /* 允许“最后一片无音频”的情况：收尾释放 pipeline。pb_single 语义为单包整轮，与 pb_end 同收尾。 */
      log_info("[PB] complete end_no_bin req=%s type=%s idx=%u inflight=%u",
               pb_req_.c_str(), type.c_str(), (unsigned)idx, (unsigned)pb_inflight_seq_count_);
      if (pb_audio_stream_started_) {
        const uint8_t ch_end = (pb_ch_ == 0 || pb_ch_ > 2) ? 1 : pb_ch_;
        audio_stream_pcm16_end(ch_end);
        pb_audio_stream_started_ = false;
      }
      pb_end_waiting_bin_ = false;
      pb_end_idx_ = 0;
      pbOnSequenceComplete();
    }
  }

  /* 有 audio.next_bin_len 时须等 BIN 入队后再推进 idx，否则嵌套 ws_.loop() 可能先收到 pb_end 并覆盖 expect_len。 */
  if (!pb_expect_bin_) {
    pb_next_idx_ = idx + 1;
  }
  return true;
}

void AsrChatClient::forceWsReconnect(const char* why) {
  ready_ = false;
  if (ws_.isConnected()) {
    log_warn("[ASR_CHAT] force disconnect (%s)", why ? why : "?");
    ws_.disconnect();
  }
}

bool AsrChatClient::connect() {
  if (ws_.isConnected()) {
    if (ready_) {
      return true;
    }
    unsigned long start = millis();
    while (!ready_ && millis() - start < 3000) {
      ws_.loop();
      log_task_pump("wait_ready_late");
      taskYIELD();
    }
    if (ready_) {
      log_info("[ASR_CHAT] ready received (late on existing socket)");
      return true;
    }
    forceWsReconnect("connected but no ready");
  }
  if (kHost[0] == '\0') {
    log_error("[ASR_CHAT] ASR_CHAT_HOST not set; edit firmware/deskbot_config.h "
              "and set DESKBOT_WS_HOST");
    return false;
  }
  if (!deskbot_api_key_configured()) {
    log_error("[ASR_CHAT] DESKBOT_API_KEY not set; edit firmware/deskbot_config.h "
              "(odk_... or odk_free_... from server)");
    return false;
  }
  ready_ = false;
  reply_done_ = false;
  server_started_reply_ = false;

  char path[64];
  snprintf(path, sizeof(path), "/asr_chat?device_id=%s", get_device_id());
  char auth_header[96];
  snprintf(auth_header, sizeof(auth_header), "X-API-Key: %s", DESKBOT_API_KEY);
  ws_.setExtraHeaders(auth_header);
  log_info("[ASR_CHAT] connecting ws://%s:%u%s (X-API-Key)", kHost, (unsigned)kPort, path);
  ws_.begin(kHost, kPort, path);
  ws_.onEvent([this](WStype_t type, uint8_t* payload, size_t length) {
    this->onWebSocketEvent(type, payload, length);
  });
  ws_.setReconnectInterval(2000);

  unsigned long start = millis();
  while (!ws_.isConnected() && millis() - start < 4000) {
    ws_.loop();
    log_task_pump("tcp_handshake");
    taskYIELD();
  }
  if (!ws_.isConnected()) {
    log_error("[ASR_CHAT] connect timeout (check host/port, DESKBOT_API_KEY, network)");
    return false;
  }

  start = millis();
  while (!ready_ && millis() - start < 3000) {
    ws_.loop();
    log_task_pump("wait_ready");
    taskYIELD();
  }
  if (!ready_) {
    log_warn("[ASR_CHAT] no ready event, continue anyway");
  } else {
    log_info("[ASR_CHAT] ready received");
  }
  return true;
}

void AsrChatClient::loop() {
  /* 防御：loop() 不得从 audio_play_task 调用。
   * 该任务内调 audio_stream_pcm16_end/emergency_flush 等同步 audio API 会自我死锁；
   * 调 ws_.loop() 会在 WS 库内部重入（主循环可能已在 ws_.loop() 中途被 preempt）。
   * audio_yield_hook 已改为 taskYIELD()，正常不会走到这里；此处仅做兜底。 */
  if (audio_play_is_on_play_task()) {
    return;
  }

  auto flush_deferred_pb_stream_end = [this]() {
    if (!pb_deferred_stream_end_pending_) {
      return;
    }
    pb_deferred_stream_end_pending_ = false;
    audio_stream_pcm16_end(pb_deferred_stream_end_ch_);
  };
  flush_deferred_pb_stream_end();
  /* 先泵再 parse 队列：若已在 expect_bin，优先把 BIN 收进同一轮 ws_.loop() 栈外的主循环。 */
  if (pb_expect_bin_) {
    pbPumpWsWhileExpectBin(24);
  }
  flushDeferredPbJson();
  if (pb_expect_bin_) {
    pbPumpWsWhileExpectBin(8);
  }
  /* 等待下行 PCM 时须尽快泵 WS 收 BIN（仅在 loop 主上下文）。 */
  const int ws_pumps = pb_expect_bin_ ? 0 : ((pb_active_ || tts_active_) ? 6 : 1);
  for (int i = 0; i < ws_pumps; ++i) {
    ws_.loop();
    flushPendingPbAck();
    if (!pb_expect_bin_) {
      break;
    }
  }
  flush_deferred_pb_stream_end();
  /* 舵机 async ramp 完成后投递的 pb_ack（不阻塞 motor_task / WS 回调）。 */
  {
    char reqb[48];
    uint32_t midx = 0;
    while (head_take_pb_motor_ack_done(reqb, sizeof(reqb), &midx)) {
      pbScheduleMotorAck(reqb, midx);
    }
  }
  /* pb_ack 必须在 RX 回调之外发送，否则部分 WebSockets 实现会在 sendTXT 时死锁，表现为 pb_start 后卡住。 */
  flushPendingPbAck();
  if (ws_.isConnected() && millis() - last_ping_ms_ > 15000) {
    if (sendJson("{\"type\":\"ping\"}")) {
      last_ping_ms_ = millis();
    }
  }
  /* 空闲头部姿态步进：状态稳定时 if 判定即返回。 */
  if (!pb_active_) {
    updateAttentionDisplay();
  }

  if (ws_.isConnected() && !isVisionUplinkPaused()) {
    const uint8_t* jpeg_buf = nullptr;
    size_t jpeg_len = 0;
    uint32_t jpeg_seq = 0;
    if (camera_ws_take_frame(&jpeg_buf, &jpeg_len, &jpeg_seq)) {
      char cam_hdr[128];
      snprintf(cam_hdr, sizeof(cam_hdr),
               "{\"type\":\"camera_frame\",\"codec\":\"jpeg\",\"next_bin_len\":%u,\"seq\":%u}",
               (unsigned)jpeg_len, (unsigned)jpeg_seq);
      if (ws_.sendTXT(cam_hdr)) {
        if (!ws_.sendBIN(jpeg_buf, jpeg_len)) {
          log_warn("[CAM] camera_frame bin send failed");
          forceWsReconnect("camera_frame bin");
        }
      } else {
        log_warn("[CAM] camera_frame header send failed");
        forceWsReconnect("camera_frame header");
      }
      camera_ws_release_frame();
    }
  }
}

bool AsrChatClient::isVisionUplinkPaused() const {
  return tts_active_ || pb_expect_bin_ || pb_audio_stream_started_ || pb_deferred_stream_end_pending_ ||
         pb_active_;
}

void AsrChatClient::updateAttentionDisplay() {
  unsigned long now = millis();
  /* 唤醒窗口：pb_start 至 pb 序列收尾（tts_active_）。 */
  const bool should_wake = tts_active_;

  if (should_wake) {
    last_should_wake_ms_ = now;
    if (display_state_ != DISPLAY_WAKEUP) {
      log_info("[ATTENTION] -> WAKEUP (tts=%d)", (int)tts_active_);
      display_state_ = DISPLAY_WAKEUP;
      /* TTS 前抬头到中位，避免 SLEEP 低头后 PB 相对点头全夹在 Y_MIN_LIMIT。 */
      const int y_now = head_read_y_logic();
      if (y_now != Y_CENTER) {
        head_servo_cmd_async(HEAD_SERVO_HOLD, HEAD_SERVO_ABS, 0, Y_CENTER, /*step=*/0, /*ms=*/200);
        log_info("[ATTENTION] wake raise Y %d -> %d", y_now, Y_CENTER);
      }
    }
    return;
  }

  /* !should_wake：开机首次（last_should_wake_ms_==0）直接进 sleep；
   * 之前 wake 过 → 距上次 should_wake ≥ 2s 才进 sleep（抖动抑制）。 */
  bool first_time = (last_should_wake_ms_ == 0) && (display_state_ == DISPLAY_UNINIT);
  bool dwell_done = (last_should_wake_ms_ != 0) &&
                    (now - last_should_wake_ms_ >= kIdleEnterDelayMs);
  if (display_state_ != DISPLAY_SLEEP && (first_time || dwell_done)) {
    log_info("[ATTENTION] -> SLEEP (dwell=%lums first=%d)",
             last_should_wake_ms_ == 0 ? 0UL : (now - last_should_wake_ms_),
             (int)first_time);
    /* 沉默动作（低头）：只动 Y、保持 X 在当前位置（避免突兀横扫）。 */
    int idle_y_target = constrain(Y_CENTER + kSleepHeadDownDeg, Y_MIN_LIMIT, Y_MAX_LIMIT);
    int y_now = head_read_y_logic();
    int dy = idle_y_target - y_now;
    if (dy != 0) {
      head_move(0, dy);
    }
    display_state_ = DISPLAY_SLEEP;
  }
}

bool AsrChatClient::sendJson(const char* msg) {
  if (msg == nullptr || !ws_.isConnected()) {
    return false;
  }
  if (!ws_.sendTXT(msg)) {
    audio_play_emergency_flush();
    forceWsReconnect("sendTXT failed");
    return false;
  }
  return true;
}

bool AsrChatClient::sendJson(const String& msg) {
  return sendJson(msg.c_str());
}

bool AsrChatClient::shouldSuppressMicUplink() {
  const unsigned long now = millis();
  const bool playing =
      audio_play_stream_pcm_active() || (audio_play_input_queue_depth() > 0u);
  if (playing) {
    const unsigned long until = now + (unsigned long)kI2sDmaTailSuppressMs;
    if (until > mic_suppress_until_ms_) {
      mic_suppress_until_ms_ = until;
    }
    return true;
  }
  if (now < mic_suppress_until_ms_) {
    return true;
  }
  return false;
}

bool AsrChatClient::sendAudioJsonPcm16(const int16_t* pcm, size_t samples) {
  /* 新协议：JSON 头写 next_bin_len，下一条消息发裸 PCM binary（s16le mono 16kHz）。
   * 原 base64 data 字段已废弃。 */
  const uint8_t* bytes = reinterpret_cast<const uint8_t*>(pcm);
  const size_t byte_len = samples * sizeof(int16_t);
  char hdr[96];
  snprintf(hdr, sizeof(hdr),
           "{\"type\":\"audio\",\"codec\":\"pcm16\",\"next_bin_len\":%u,\"sr\":16000,\"ch\":1}",
           (unsigned)byte_len);
  if (!sendJson(hdr)) {
    return false;
  }
  if (!ws_.sendBIN(bytes, byte_len)) {
    audio_play_emergency_flush();
    forceWsReconnect("audio PCM bin send failed");
    return false;
  }
  return true;
}

bool AsrChatClient::runVoiceRound(uint16_t max_record_seconds) {
  round_id_++;
  char round_detail[24];
  snprintf(round_detail, sizeof(round_detail), "id=%u", (unsigned)round_id_);
  LogTaskScope round_scope("asr_round", round_detail);

  round_scope.phase("ws_connect");
  if (!connect()) {
    return false;
  }

  if (max_record_seconds == 0 || max_record_seconds > 60) {
    max_record_seconds = 10;
  }

  disconnect_abort_round_ = false;
  reply_done_ = false;
  server_started_reply_ = false;
  /* 每轮开头清掉上一轮 pb/PCM 残余；但若 WS 仍在推本轮 TTS（pb_active），此处 pbReset 会停流并清
   * expect_bin → 后续 BIN 变「unexpected」、idx 全错位（见 CHAT 重叠发起 runVoiceRound 与下行并行）。
   * 仅在没有进行中 pb 序列时才硬清。 */
  if (!pb_active_ && !pb_expect_bin_) {
    pbReset(/*stop_audio=*/true);
  } else {
    log_warn("[ASR_CHAT] round=%u start: skip initial pbReset (pb_active=%d expect_bin=%d req=%s next_idx=%u)",
             (unsigned)round_id_, (int)pb_active_, (int)pb_expect_bin_, pb_req_.c_str(), (unsigned)pb_next_idx_);
  }
  last_ping_ms_ = millis();

  const size_t total_samples = static_cast<size_t>(max_record_seconds) * SAMPLE_RATE;
  size_t samples_recorded = 0;  /* 本轮 mic 采样总样点（含未发送的 pre-roll），用于决定何时结束录音 */
  size_t samples_sent = 0;       /* 真正上行到服务端的样点数，仅用于日志/统计 */
  int16_t frame[kFrameSamples20ms];
  static int16_t silence_pcm[kFrameSamples20ms]; /* 半双工播音窗口上行用，全零 */
  bool voice_seen = false;
  unsigned long silence_start = 0;
  const unsigned long silence_end_ms = DESKBOT_PDM_SILENCE_END_MS;
  constexpr size_t kPreVoiceFrames = DESKBOT_PDM_PRE_VOICE_FRAMES;

  /* 「整轮基本静音就不上传」门控 + pre-roll 预滚缓冲：
   * - 触发有声（mean > SOUND_THRESHOLD）之前的帧不上行，仅滚动缓存最近 ~kPreVoiceFrames 帧；
   * - 一旦触发，按时间顺序把缓存全部排队上行，紧接着本帧及后续帧也上行；
   * - 整轮始终未触发：跳过 flush，直接结束本轮（不打扰服务端 ASR/LLM）。
   * 每帧 320 采样×2B = 640B；PDM 50 帧 ≈ 1s pre-roll，分配在 PSRAM。 */
  static int16_t* prebuf = nullptr;
  if (prebuf == nullptr) {
    const size_t bytes = kPreVoiceFrames * kFrameSamples20ms * sizeof(int16_t);
    prebuf = static_cast<int16_t*>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (prebuf == nullptr) {
      /* PSRAM 不可用时退到内部 RAM；仍失败就放弃 pre-roll，门控仍生效（首帧会丢点开头）。 */
      prebuf = static_cast<int16_t*>(malloc(bytes));
      if (prebuf == nullptr) {
        log_warn("[ASR_CHAT] pre-roll buffer alloc failed (%u bytes), no pre-roll", (unsigned)bytes);
      }
    }
  }
  size_t prebuf_head = 0;   /* 下一个要写入的 slot */
  size_t prebuf_count = 0;  /* 当前已缓存的帧数 (≤ kPreVoiceFrames) */

  log_info("[ASR_CHAT] round=%u start streaming up to %us @16k/mono/pcm16 (gated, pre-roll %ums)",
           (unsigned)round_id_, (unsigned)max_record_seconds,
           (unsigned)(prebuf ? kPreVoiceFrames * 20 : 0));
  /* 丢掉 Idle / 上一轮 tail 积压的帧，第一段上行从「现在开始」取样。*/
  mic_capture_flush_queue();

  round_scope.phase("pdm_calibrate");
  size_t voice_threshold = SOUND_THRESHOLD;
  size_t noise_ema = 0;
  uint8_t trigger_streak = 0;
  {
    size_t floor_sum = 0;
    size_t floor_max = 0;
    constexpr size_t kCalFrames = 20;
    for (size_t f = 0; f < kCalFrames; ++f) {
      record(frame, kFrameSamples20ms);
      enhanceVoice(frame, kFrameSamples20ms);
      const size_t m = calculate_mean(frame, kFrameSamples20ms);
      floor_sum += m;
      if (m > floor_max) {
        floor_max = m;
      }
    }
    noise_ema = floor_sum / kCalFrames;
    voice_threshold = deskbot_pdm_voice_trigger_thr(noise_ema);
    if (voice_threshold > DESKBOT_PDM_VOICE_THRESHOLD_MAX) {
      voice_threshold = DESKBOT_PDM_VOICE_THRESHOLD_MAX;
    }
    log_info("[ASR_CHAT] PDM calibrate avg=%u max=%u thr=%u hang=%u",
             (unsigned)noise_ema, (unsigned)floor_max, (unsigned)voice_threshold,
             (unsigned)deskbot_pdm_voice_hangover_thr(noise_ema));
  }

  unsigned long last_vad_dbg_ms = millis();
  size_t peak_mean_round = 0;
  size_t peak_mean_2s = 0;

  round_scope.phase("record");
  while (samples_recorded < total_samples) {
    record(frame, kFrameSamples20ms);
    const bool duplex_suppress = shouldSuppressMicUplink();
    /* VAD 始终做 enhance；半双工只禁上行，避免播音窗口内 mean 偏低导致漏触发 */
    enhanceVoice(frame, kFrameSamples20ms);
    size_t mean = calculate_mean(frame, kFrameSamples20ms);
    if (mean > peak_mean_round) {
      peak_mean_round = mean;
    }

    if (!voice_seen && !duplex_suppress) {
      const size_t quiet_cap =
          (noise_ema * DESKBOT_PDM_EMA_QUIET_RATIO_NUM) / DESKBOT_PDM_EMA_QUIET_RATIO_DEN;
      if (mean < quiet_cap) {
        noise_ema = (noise_ema * 15 + mean) / 16;
      }
      voice_threshold = deskbot_pdm_voice_trigger_thr(noise_ema);
      if (voice_threshold > DESKBOT_PDM_VOICE_THRESHOLD_MAX) {
        voice_threshold = DESKBOT_PDM_VOICE_THRESHOLD_MAX;
      }
      if (mean > peak_mean_2s) {
        peak_mean_2s = mean;
      }
      const unsigned long now_dbg = millis();
      if (now_dbg - last_vad_dbg_ms >= 2000) {
        last_vad_dbg_ms = now_dbg;
        log_info("[ASR_CHAT] PDM vad idle mean=%u peak2s=%u ema=%u thr=%u",
                 (unsigned)mean, (unsigned)peak_mean_2s, (unsigned)noise_ema,
                 (unsigned)voice_threshold);
        peak_mean_2s = 0;
      }
    }

    bool active = false;
    if (!voice_seen) {
      if (mean > voice_threshold) {
        if (trigger_streak < 255) {
          ++trigger_streak;
        }
      } else {
        trigger_streak = 0;
      }
      active = trigger_streak >= DESKBOT_PDM_VOICE_TRIGGER_FRAMES;
    } else {
      const size_t hang_thr = deskbot_pdm_voice_hangover_thr(noise_ema);
      active = mean > hang_thr;
    }

    if (active) {
      if (!voice_seen) {
        /* 首次触发：先把 pre-roll 按时间顺序排队上行，再走「本帧」上行分支。 */
        if (prebuf != nullptr && prebuf_count > 0) {
          size_t idx = (prebuf_head + (kPreVoiceFrames - prebuf_count)) % kPreVoiceFrames;
          for (size_t i = 0; i < prebuf_count; ++i) {
            int16_t* fp = &prebuf[idx * kFrameSamples20ms];
            const int16_t* uplink = duplex_suppress ? silence_pcm : fp;
            if (!sendAudioJsonPcm16(uplink, kFrameSamples20ms)) {
              log_error("[ASR_CHAT] send pre-roll frame failed");
              return false;
            }
            samples_sent += kFrameSamples20ms;
            idx = (idx + 1) % kPreVoiceFrames;
          }
          log_info("[ASR_CHAT] round=%u voice trigger (mean=%u thr=%u), flushed %u pre-roll frames",
                   (unsigned)round_id_, (unsigned)mean, (unsigned)voice_threshold,
                   (unsigned)prebuf_count);
          prebuf_count = 0;
          prebuf_head = 0;
        } else {
          log_info("[ASR_CHAT] round=%u voice trigger (mean=%u thr=%u, no pre-roll)",
                   (unsigned)round_id_, (unsigned)mean, (unsigned)voice_threshold);
        }
      }
      voice_seen = true;
      silence_start = 0;
    } else if (voice_seen) {
      if (silence_start == 0) {
        silence_start = millis();
      } else if (millis() - silence_start >= silence_end_ms) {
        break;
      }
    }

    if (voice_seen) {
      if (!duplex_suppress) {
        if (!sendAudioJsonPcm16(frame, kFrameSamples20ms)) {
          log_error("[ASR_CHAT] send audio frame failed");
          return false;
        }
        samples_sent += kFrameSamples20ms;
      }
    } else if (prebuf != nullptr) {
      /* 还没触发：写入 pre-roll 环形缓冲（满则覆盖最旧）。 */
      int16_t* slot = &prebuf[prebuf_head * kFrameSamples20ms];
      if (duplex_suppress) {
        memset(slot, 0, kFrameSamples20ms * sizeof(int16_t));
      } else {
        memcpy(slot, frame, kFrameSamples20ms * sizeof(int16_t));
      }
      prebuf_head = (prebuf_head + 1) % kPreVoiceFrames;
      if (prebuf_count < kPreVoiceFrames) {
        ++prebuf_count;
      }
    }

    samples_recorded += kFrameSamples20ms;
    handle_cmd();
    loop();
    round_scope.pump(voice_seen ? "uplink" : "listen");
    taskYIELD();
  }

  if (!voice_seen && !server_started_reply_) {
    /* 整轮基本是静音：不发 audio JSON、不发 flush、不进等下行循环。
     * 清一下 pb 状态与 mic 队列，跟正常路径退出时保持一致。 */
    log_info("[ASR_CHAT] round=%u skipped (no voice detected over %ums, peak_mean=%u thr=%u, %u prebuf discarded)",
             (unsigned)round_id_,
             (unsigned)(samples_recorded * 1000UL / SAMPLE_RATE),
             (unsigned)peak_mean_round, (unsigned)voice_threshold, (unsigned)prebuf_count);
    if (peak_mean_round + DESKBOT_PDM_VOICE_MARGIN >= voice_threshold) {
      log_warn("[ASR_CHAT] round=%u near-miss (peak=%u thr=%u delta_need>%u); speak louder",
               (unsigned)round_id_, (unsigned)peak_mean_round, (unsigned)voice_threshold,
               (unsigned)(voice_threshold - peak_mean_round));
    }
    pbReset(/*stop_audio=*/true);
    audio_stream_pcm16_stop();
    mic_capture_flush_queue();
    return true;
  }

  if (!server_started_reply_) {
    if (!sendJson("{\"type\":\"flush\"}")) {
      log_error("[ASR_CHAT] send flush failed");
      return false;
    }
    log_info("[ASR_CHAT] round=%u flush sent (uploaded %u samples), mic uplink continues until reply done",
             (unsigned)round_id_, (unsigned)samples_sent);
  } else {
    log_info("[ASR_CHAT] round=%u skip flush (server already replying, uploaded %u samples)",
             (unsigned)round_id_, (unsigned)samples_sent);
  }

  unsigned long wait_start = millis();
  unsigned long last_wait_log_ms = wait_start;
  bool logged_no_asr_reply = false;
  round_scope.phase("wait_reply");
  /* 与下行并行：flush 后仍按帧上行 PCM；无 AEC 时仅在喇叭播音/尾音窗口内暂停上行。 */
  while ((!reply_done_ || pb_audio_stream_started_ || pb_deferred_stream_end_pending_) &&
         millis() - wait_start < 30000) {
    if (!shouldSuppressMicUplink()) {
      record(frame, kFrameSamples20ms);
      enhanceVoice(frame, kFrameSamples20ms);
      if (!sendAudioJsonPcm16(frame, kFrameSamples20ms)) {
        log_error("[ASR_CHAT] post-flush uplink send failed");
        return false;
      }
    }
    for (int pump = 0; pump < 6; ++pump) {
      loop();
    }
    unsigned long now_w = millis();
    if (now_w - last_wait_log_ms >= 5000) {
      last_wait_log_ms = now_w;
      log_info("[ASR_CHAT] round=%u post-flush uplink... reply_done=%d pb_stream=%d defer_end=%d elapsed_ms=%lu ws_ok=%d",
               (unsigned)round_id_, (int)reply_done_, (int)pb_audio_stream_started_,
               (int)pb_deferred_stream_end_pending_, (unsigned long)(now_w - wait_start),
               (int)ws_.isConnected());
    }
    if (!logged_no_asr_reply && !server_started_reply_ && !reply_done_ &&
        (now_w - wait_start) >= 8000) {
      logged_no_asr_reply = true;
      log_warn("[ASR_CHAT] round=%u no pb after flush (%u samples) — check server %s:%u",
               (unsigned)round_id_, (unsigned)samples_sent, ASR_CHAT_HOST, (unsigned)ASR_CHAT_PORT);
    }
    round_scope.pump("post_flush");
    taskYIELD();
  }
  if (!reply_done_) {
    log_warn("[ASR_CHAT] round=%u reply wait timeout (reply_done=0)", (unsigned)round_id_);
    /* 30s 没等到下行结束：硬停 pb 流 + 清状态，避免 I2S/队列残留导致尾音循环；并结束本轮语义。 */
    tts_active_ = false;
    if (!pb_req_.isEmpty()) {
      pb_suppress_tail_req_ = pb_req_;
    }
    pbReset(/*stop_audio=*/true);
    audio_stream_pcm16_stop();
    pbSignalTtsRoundComplete();
    mic_capture_flush_queue();
  } else if (disconnect_abort_round_) {
    log_warn("[ASR_CHAT] round=%u reply phase ended (websocket disconnected)", (unsigned)round_id_);
    disconnect_abort_round_ = false;
  } else {
    log_info("[ASR_CHAT] round=%u reply completed", (unsigned)round_id_);
  }

  round_scope.phase("i2s_tail");
  while (millis() < mic_suppress_until_ms_) {
    loop();
    round_scope.pump();
    taskYIELD();
  }
  return true;
}

void AsrChatClient::onWebSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  /* 下行音频：pb v2（JSON 声明 audio.next_bin_len 后的裸 PCM）。不再支持 WAV / next_bin 旧字段。 */
  if (type == WStype_CONNECTED) {
    ready_ = false;
    log_info("[ASR_CHAT] connected (await ready)");
    return;
  }
  if (type == WStype_DISCONNECTED) {
    /* 固件从不主动 ws_.disconnect()；库传入 reason 字符串（如 HTTP 403、api_key_required）。 */
    const char* reason = (payload != nullptr && length > 0) ? reinterpret_cast<const char*>(payload)
                                                            : "";
    if (strstr(reason, "api_key_required") != nullptr) {
      log_error("[ASR_CHAT] auth rejected: API key missing or invalid (set DESKBOT_API_KEY)");
    } else if (strstr(reason, "quota_exhausted") != nullptr) {
      log_error("[ASR_CHAT] auth rejected: free key daily quota exhausted");
    }
    const unsigned long bin_wait_ms =
        (pb_expect_bin_ && pb_expect_bin_since_ms_ != 0) ? (millis() - pb_expect_bin_since_ms_) : 0UL;
    log_warn("[ASR_CHAT] disconnected (passive, no fw disconnect) reason=%s payload_len=%u "
             "pb_active=%d expect_bin=%d expect_len=%u bin_wait_ms=%lu pending_idx=%u "
             "bins_rx=%u pcm_rx=%u last_ws_bin_len=%u last_ack_idx=%u next_idx=%u heap=%u psram=%u",
             reason, (unsigned)length, (int)pb_active_, (int)pb_expect_bin_,
             (unsigned)pb_expect_bin_len_, (unsigned long)bin_wait_ms, (unsigned)pb_pending_idx_,
             (unsigned)pb_bins_rx_count_, (unsigned)pb_pcm_bytes_rx_total_,
             (unsigned)pb_last_ws_bin_len_, (unsigned)pb_last_ack_idx_, (unsigned)pb_next_idx_,
             (unsigned)ESP.getFreeHeap(), (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    if (pb_expect_bin_ && pb_last_ws_bin_len_ == 0) {
      log_warn("[PB] disconnected while expect BIN=%u but no WStype_BIN ever received "
               "(server likely closed before BIN; check server [pb TX] binary %u)",
               (unsigned)pb_expect_bin_len_, (unsigned)pb_expect_bin_len_);
    } else if (pb_expect_bin_ && pb_last_ws_bin_len_ != pb_expect_bin_len_) {
      log_warn("[PB] disconnected: last WStype_BIN len=%u != expect %u (delta=%d)",
               (unsigned)pb_last_ws_bin_len_, (unsigned)pb_expect_bin_len_,
               (int)pb_last_ws_bin_len_ - (int)pb_expect_bin_len_);
    }
    audio_play_emergency_flush();
    ready_ = false;
    /* pb 流未 end 时不收尾会卡「等下行结束」环；断线即结束本轮并清下行窗口。 */
    disconnect_abort_round_ = true;
    if (!pb_req_.isEmpty()) {
      pb_suppress_tail_req_ = pb_req_;
    }
    pbReset(/*stop_audio=*/true);
    pbSignalTtsRoundComplete();
    return;
  }
  if (type == WStype_TEXT) {
    JsonDocument doc;
    if (deserializeJson(doc, payload, length)) {
      String raw((const char*)payload, length);
      log_info("[ASR_CHAT] text(raw): %s", raw.c_str());
      return;
    }
    String t = doc["type"].is<String>() ? doc["type"].as<String>() : String("");

    /* pb v2：优先处理 pb_*。 */
    if (t.startsWith("pb_")) {
      if (t == "pb_cancel") {
        String req = doc["req"].is<String>() ? doc["req"].as<String>() : String("");
        if (req.isEmpty() || (!pb_req_.isEmpty() && req == pb_req_)) {
          pbReset(/*stop_audio=*/true);
        }
        return;
      }
      if (!pbDeferEnqueue(payload, length)) {
        log_warn("[PB] defer enqueue failed type=%s len=%u", t.c_str(), (unsigned)length);
      } else if (!pb_expect_bin_) {
        flushDeferredPbJson(/*pump_ws_after=*/false);
      }
      return;
    }
    if (t == "ready") {
      ready_ = true;
      log_info("[ASR_CHAT] event ready");
    } else if (t == "pong") {
      // no-op
    } else {
      String raw((const char*)payload, length);
      log_info("[ASR_CHAT] text: %s", raw.c_str());
    }
    return;
  }
  if (type == WStype_BIN) {
    pb_last_ws_bin_ms_ = millis();
    pb_last_ws_bin_len_ = length;
    if (payload == nullptr && length > 0) {
      log_error("[PB] ws BIN payload is null but length=%u", (unsigned)length);
      return;
    }
    if (pb_active_ && pb_expect_bin_) {
      if (length == 0u) {
        log_error("[PB] BIN reject: empty frame");
        pbProtocolError("binary length empty");
        return;
      }
      if (pb_expect_bin_len_ > 0 && length != pb_expect_bin_len_) {
        log_error("[PB] BIN reject: actual_len=%u expect=%u kind=%u req=%s idx=%u",
                  (unsigned)length, (unsigned)pb_expect_bin_len_, (unsigned)pb_expect_bin_kind_,
                  pb_req_.c_str(), (unsigned)pb_pending_idx_);
        pbProtocolError("binary length mismatch");
        return;
      }

      const bool closing_pb_end_bin = pb_end_waiting_bin_;
      const uint8_t ch_for_stream_end = pb_ch_;
      const uint32_t pending_idx_snap = pb_pending_idx_;
      const PbBinKind bin_kind = pb_expect_bin_kind_;

      if (bin_kind == PbBinKind::kPcm) {
        if ((length & 1u) != 0u) {
          pbProtocolError("PCM binary length not even");
          return;
        }
        if (pb_sr_ == 0 || pb_ch_ == 0 || pb_fmt_ != "s16le") {
          pbProtocolError("binary without valid audio params");
          return;
        }
        if (pb_bins_rx_count_ < 255) {
          pb_bins_rx_count_++;
        }
        pb_pcm_bytes_rx_total_ += length;
        uint8_t* pcm_bytes = (uint8_t*)heap_caps_malloc(length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        uint32_t free_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
        if (!pcm_bytes) {
          pcm_bytes = (uint8_t*)heap_caps_malloc(length, MALLOC_CAP_DEFAULT);
          free_caps = MALLOC_CAP_DEFAULT;
        }
        if (!pcm_bytes) {
          pbProtocolError("pcm alloc failed");
          return;
        }
        memcpy(pcm_bytes, payload, length);
        const size_t samples = length / 2;
        if (!pb_audio_stream_started_) {
          if (!audio_stream_pcm16_begin(pb_sr_, pb_ch_, pb_volume_ratio_)) {
            heap_caps_free(pcm_bytes);
            pbProtocolError("audio stream begin failed");
            return;
          }
          pb_audio_stream_started_ = true;
          pb_last_buf_decay_ms_ = millis();
          pb_audio_buf_ms_est_ = 0;
        }
        if (!audio_stream_pcm16_push_owned((int16_t*)pcm_bytes, samples, free_caps, pb_volume_ratio_)) {
          pbProtocolError("pcm push failed");
          return;
        }
        pb_audio_buf_ms_est_ += (int32_t)pb_pending_chunk_ms_;
      } else {
        if (pb_asset_count_ >= kPbMaxAssetsPerChunk) {
          pbProtocolError("too many asset binaries");
          return;
        }
        uint8_t* asset_buf = (uint8_t*)heap_caps_malloc(length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!asset_buf) {
          asset_buf = (uint8_t*)heap_caps_malloc(length, MALLOC_CAP_DEFAULT);
        }
        if (!asset_buf) {
          pbProtocolError("asset alloc failed");
          return;
        }
        memcpy(asset_buf, payload, length);
        pb_asset_bufs_[pb_asset_count_] = asset_buf;
        pb_asset_lens_[pb_asset_count_] = length;
        pb_asset_count_++;
      }

      pbAdvanceBinQueue();
      if (pb_expect_bin_) {
        flushDeferredPbJson(/*pump_ws_after=*/false);
        return;
      }

      pbFinishChunkBins(pending_idx_snap, closing_pb_end_bin, ch_for_stream_end);
      return;
    }

    if (pb_active_) {
      log_warn("[PB] BIN ignore: pb_active expect_bin=%d expect_len=%u actual_len=%u "
               "(expected JSON preamble before next BIN)",
               (int)pb_expect_bin_, (unsigned)pb_expect_bin_len_, (unsigned)length);
      pbProtocolError("unexpected BIN (expect JSON per pb v2)");
    } else if (!pb_suppress_tail_req_.isEmpty()) {
      log_info("[PB] BIN drop stale tail len=%u", (unsigned)length);
    } else {
      log_warn("[ASR_CHAT] drop unexpected BIN len=%u", (unsigned)length);
    }
    return;
  }
  if (type == WStype_FRAGMENT_BIN_START || type == WStype_FRAGMENT || type == WStype_FRAGMENT_FIN) {
    pb_last_ws_bin_ms_ = millis();
    pb_last_ws_bin_len_ = length;
    log_warn("[PB] ws FRAGMENT type=%d chunk_len=%u expect_bin=%d expect_len=%u wait_ms=%lu — "
             "firmware only handles single WStype_BIN; fragmented PCM will never match next_bin_len",
             (int)type, (unsigned)length, (int)pb_expect_bin_, (unsigned)pb_expect_bin_len_,
             (pb_expect_bin_ && pb_expect_bin_since_ms_ != 0)
                 ? (unsigned long)(millis() - pb_expect_bin_since_ms_)
                 : 0UL);
    return;
  }
}
