#pragma once

#include <Arduino.h>
#include "pb_model.h"
#include "speaker.h"
#include "deskbot_config.h"

/**
 * pb v2 下行：模型缓存调度、分发到 speaker/display/head、ack / attention 泵。
 * WS 生命周期 / 收发在 ws_transport；本类只处理 pb 业务。
 */
class PbRuntime {
public:
  PbRuntime();

  /** 主循环泵 pb（ack / attention）。由 pb_runtime 任务调用。 */
  void serviceLoop();

  /* 空闲时头部姿态：TTS 结束后延迟低头；TTS 期间不重复触发。 */
  void updateAttentionDisplay();

  /** 仅由 pb_runtime_task 消费断线控制事件后调用。 */
  void onLinkDown();

  /** 处理 pb_cancel：中止当前播放并清空执行器队列。 */
  void handleCancel(const pb_model& model);

  /**
   * 分发一条已解析 pb_model 到 speaker/head/display；接管 model 内 PSRAM 资源所有权并在返回前释放。
   * 由 pb_runtime_task 环形缓存到期时调用。
   */
  void dispatchModel(pb_model& model);

  /**
   * 纯舵机叠层：只改 head，不碰 speaker/display，不改 playing_level / tts_active。
   * 用于注视/跟随等低优先级舵机，避免取消更高优先级口播。
   */
  void dispatchServoOverlay(pb_model& model);

private:
  void applySideEffects(const pb_model& model);
  bool tryMicOnlySingle(pb_model& model);
  void onChainHead(pb_model& model);
  void dispatchAnim(pb_model& model);
  void dispatchServo(pb_model& model);
  bool dispatchAudio(pb_model& model);
  void onSequenceEnd(const pb_model& model);
  void maybeAck(const pb_model& model);
  void enqueueAck(const char* req, uint32_t idx, int32_t audio_buf_ms, bool include_servo);
  void flushPendingPbAck();
  void signalTtsRoundComplete();
  void endAudioStreamIfNeeded();
  void abortRound(bool abort_speaker);
  static uint8_t normalizeAudioCh(uint8_t ch);
  void updateAudioBufDecayWall();

  bool tts_active_ = false;
  char pb_req_[37]{};
  uint32_t pb_sr_ = 0;
  uint8_t pb_ch_ = 0;
  char pb_fmt_[PB_MODEL_FMT_CAPACITY]{};
  bool pb_audio_stream_started_ = false;
  int32_t pb_audio_buf_ms_est_ = 0;
  unsigned long pb_last_buf_decay_ms_ = 0;

  bool pb_ack_out_pending_ = false;
  char pb_ack_out_req_[37]{};
  uint32_t pb_ack_out_idx_ = 0;
  int32_t pb_ack_out_buf_ms_ = 0;
  unsigned long pb_last_pb_ack_sent_wall_ms_ = 0;
  bool pb_ack_bypass_throttle_ = false;

  enum DisplayState : uint8_t {
    DISPLAY_UNINIT = 0,
    DISPLAY_WAKEUP = 1,
    DISPLAY_SLEEP = 2,
  };
  DisplayState display_state_ = DISPLAY_UNINIT;
  unsigned long last_should_wake_ms_ = 0;
  static constexpr unsigned long kIdleEnterDelayMs = 2000;
  static constexpr int kSleepHeadDownDeg = -30;
};

/** 初始化 PbRuntime 单例（须在 setup_ws_transport 之前）。 */
bool setup_pb_runtime(void);

/** 启动 pb 泵任务（消费下行帧队列 + serviceLoop）；须在 setup_pb_runtime 之后。 */
bool task_setup_pb_runtime(void);

/** 单例（须先 setup_pb_runtime）。 */
PbRuntime* pb_runtime(void);

/**
 * ws_transport 将完整打包 BIN 帧移交到 pb 队列（成功则接管 data 所有权）。
 * 失败返回 false，调用方须 free(data)。
 */
bool pb_runtime_enqueue_frame(uint8_t* data, size_t length);

/**
 * ws_transport 通知 ASR 链路已断开。实际 PB 状态清理由 pb_runtime_task 串行执行。
 */
void pb_runtime_notify_link_down(void);

/** 清空待处理下行帧（断线 / new_session）。 */
void pb_runtime_discard_rx_queue(void);
