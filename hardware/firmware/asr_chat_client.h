#ifndef ASR_CHAT_CLIENT_H
#define ASR_CHAT_CLIENT_H

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WebSocketsClient.h>
/* 单帧 WS 入站上限：platformio.ini WEBSOCKETS_MAX_DATA_SIZE（默认 1MiB）；须大于 PB PCM chunk。 */
#if !defined(WEBSOCKETS_MAX_DATA_SIZE) || WEBSOCKETS_MAX_DATA_SIZE < (200 * 1024)
#error WEBSOCKETS_MAX_DATA_SIZE must be >= 200KiB; set -DWEBSOCKETS_MAX_DATA_SIZE in platformio.ini
#endif
#include "audio_player.h"
#include "common.h"
#include "deskbot_config.h"

class AsrChatClient {
public:
  AsrChatClient();

  bool connect();
  void loop();
  bool runVoiceRound(uint16_t max_record_seconds = 10);

  /** TTS/pb 播音或半双工抑制窗口内为 true，camera_ws 应暂停上传。 */
  bool isVisionUplinkPaused() const;

  /* 空闲时头部姿态：TTS 结束后延迟低头；TTS 期间不重复触发。 */
  void updateAttentionDisplay();

private:
  static constexpr const char* kHost = ASR_CHAT_HOST;
  static constexpr uint16_t kPort = ASR_CHAT_PORT;
  static constexpr size_t kFrameSamples20ms = 320;  // 16kHz * 0.02s

  WebSocketsClient ws_;
  bool ready_ = false;
  /* 本轮下行收尾标志：pbSignalTtsRoundComplete() 触发，等价于「pb 序列已完整结束」。 */
  bool reply_done_ = false;
  bool server_started_reply_ = false;
  /* tts_active_：pb_start 置 true、pb 序列收尾 / pbSignalTtsRoundComplete 置 false。
   * 半双工抑制、摄像头暂停、attention display 共用此窗口。 */
  bool tts_active_ = false;
  unsigned long last_ping_ms_ = 0;
  uint32_t round_id_ = 0;
  /* WebSocket 断在本轮内时置位，用于结束等待并区分日志（避免打成「reply completed」）。 */
  bool disconnect_abort_round_ = false;

  /* -----------------------------------------------------------------------
   * pb v2 下行播放序列（JSON + 紧随 binary PCM）：
   * - 维护当前 req、idx、期望下一帧 binary、queue_level 与在途序列计数。
   * - pb_start / pb_single（链首）按 level + action（replace|append|default）做入队决策（§2.1/§2.2）。
   * - append 跨 req 不清 worker 队列，分片顺序入队；高 level 或 replace 同级时 drain+reset。
   * - v1 action=opportunistic 降级为 level=0 + append。
   * ----------------------------------------------------------------------- */
  enum class PbEnqueueAction : uint8_t { kReplace = 0, kAppend = 1, kDefault = 2 };
  enum class PbQueueDecision : uint8_t { kDrop = 0, kClear = 1, kAppend = 2 };
  static PbEnqueueAction parsePbEnqueueAction(const JsonDocument& doc);
  static int8_t parsePbLevel(const JsonDocument& doc, bool legacy_opportunistic);
  PbQueueDecision pbDecideChainHead(int8_t level, PbEnqueueAction action) const;
  int pbCountHigherPrioritySeqs(int8_t level) const;
  void pbSeqTrackPush(int8_t level);
  void pbSeqTrackPop();
  /** drain_motor=false：多帧 pb_single 舵机手势（如摇头分步）保留 motor 队列，避免 replace 清掉未执行的步。 */
  void pbDrainWorkersForNewSequence(bool drain_motor = true);
  void pbOnSequenceComplete();
  PbEnqueueAction pb_pending_enqueue_action_ = PbEnqueueAction::kReplace;
  int8_t pb_queue_level_ = -1;
  uint8_t pb_inflight_seq_count_ = 0;
  static constexpr size_t kPbMaxTrackedSeqLevels = 16;
  int8_t pb_seq_levels_[kPbMaxTrackedSeqLevels]{};
  size_t pb_seq_level_count_ = 0;

  bool pb_active_ = false;
  String pb_req_;
  /* 断线 / pbReset 后服务端仍可能送达同一 req 的 pb_chunk+BIN；若已 pbReset 则 next_idx
   * 归零，会误触发 idx not monotonic。记录被中止的 req：丢弃尾帧，直至同 req 的 pb_start|pb_single
   * idx==0（合法新流）或收到不同 req。 */
  String pb_suppress_tail_req_;
  uint32_t pb_next_idx_ = 0;
  bool pb_expect_bin_ = false;
  size_t pb_expect_bin_len_ = 0;
  /** 进入 expect_bin 的时刻；用于对照 BIN 是否在断线前到达。 */
  unsigned long pb_expect_bin_since_ms_ = 0;
  /** 最近一次 WStype_BIN/FRAGMENT 回调里的 length（含被拒绝的帧）。 */
  size_t pb_last_ws_bin_len_ = 0;
  unsigned long pb_last_ws_bin_ms_ = 0;
  uint32_t pb_sr_ = 0;
  uint8_t pb_ch_ = 0;
  String pb_fmt_;
  uint32_t pb_pending_idx_ = 0;
  uint32_t pb_pending_chunk_ms_ = 0;
  char* pb_pending_anim_buf_ = nullptr;
  size_t pb_pending_anim_len_ = 0;
  void pbFreePendingAnim();
  struct PbServoSeg {
    int xm = 2;
    int ym = 2;
    int x = 0;
    int y = 0;
    uint16_t ms = 0;
  };
  static constexpr size_t kPbMaxServoSegsPerChunk = 32;
  PbServoSeg pb_pending_servo_segs_[kPbMaxServoSegsPerChunk]{};
  size_t pb_pending_servo_seg_count_ = 0;
  /** 本 chunk 舵机目标（logic Y）；pb_ack 上报供服务端相对运动衔接，不等待 ramp 完成。 */
  bool pb_ack_servo_report_valid_ = false;
  int pb_ack_servo_report_x_ = 0;
  int pb_ack_servo_report_y_ = 0;
  /** pb JSON 中 volume 字段（0–100）换算的播放音量比例；省略时保持上次值（初始为编译期默认）。 */
  float pb_volume_ratio_ = DESKBOT_AUDIO_PLAY_VOLUME;
  bool pb_audio_stream_started_ = false;
  unsigned long pb_last_buf_decay_ms_ = 0;
  int32_t pb_audio_buf_ms_est_ = 0;
  uint32_t pb_last_ack_idx_ = 0;
  /** 本轮 req 已成功入队的 PCM BIN 包数 / 累计字节（供与服务端对照是否收全）。 */
  uint8_t pb_bins_rx_count_ = 0;
  size_t pb_pcm_bytes_rx_total_ = 0;
  bool pb_end_waiting_bin_ = false;
  uint32_t pb_end_idx_ = 0;

  enum class PbBinKind : uint8_t { kPcm = 0, kAsset = 1 };
  static constexpr uint8_t kPbMaxAssetsPerChunk = 8;
  static constexpr uint8_t kPbMaxBinsPerChunk = 1 + kPbMaxAssetsPerChunk;
  uint8_t pb_pending_bin_count_ = 0;
  uint8_t pb_pending_bin_cursor_ = 0;
  PbBinKind pb_pending_bin_kinds_[kPbMaxBinsPerChunk]{};
  size_t pb_pending_bin_lens_[kPbMaxBinsPerChunk]{};
  PbBinKind pb_expect_bin_kind_ = PbBinKind::kPcm;
  uint8_t* pb_asset_bufs_[kPbMaxAssetsPerChunk]{};
  size_t pb_asset_lens_[kPbMaxAssetsPerChunk]{};
  uint8_t pb_asset_count_ = 0;

  void pbFreePendingAssets();
  void pbBuildPendingBinQueue(const JsonDocument& doc);
  void pbAdvanceBinQueue();
  void pbFinishChunkBins(uint32_t pending_idx_snap, bool closing_pb_end_bin, uint8_t ch_for_stream_end);
  /* pb_end 最后一包若在 WS 回调里调 audio_stream_pcm16_end 会长时间占住回调线程，主循环仍
   * 在跑 → runVoiceRound 看到 reply_done=0 且 pb_stream=1。将 end 推迟到 loop() 主上下文执行。 */
  bool pb_deferred_stream_end_pending_ = false;
  uint8_t pb_deferred_stream_end_ch_ = 1;

  /* pb_ack 不得在 WebSocket onEvent 回调里 sendTXT（部分库会死锁），只入队到 loop() 再发。 */
  bool pb_ack_out_pending_ = false;
  String pb_ack_out_req_;
  uint32_t pb_ack_out_idx_ = 0;
  int32_t pb_ack_out_buf_ms_ = 0;
  /** 纯音频 pb_ack 节流（ms）；舵机完成 ack 走 pb_ack_bypass_throttle_ 立即发。 */
  unsigned long pb_last_pb_ack_sent_wall_ms_ = 0;
  bool pb_ack_bypass_throttle_ = false;

  /* 空闲头部姿态：updateAttentionDisplay() 状态机。
   *   UNINIT：开机后未驱动过；首次进入若 should_wake=false 立刻低头（不等 2s）。
   *   WAKEUP：tts_active_ 为真（pb 下行窗口）。
   *   SLEEP：持续 ≥2s 无 pb 下行时低头到 Y_CENTER+kSleepHeadDownDeg。 */
  enum DisplayState : uint8_t {
    DISPLAY_UNINIT = 0,
    DISPLAY_WAKEUP = 1,
    DISPLAY_SLEEP = 2,
  };
  DisplayState display_state_ = DISPLAY_UNINIT;
  /* 上一次 should_wake=true 的时间戳。!should_wake 时拿这个判 dwell ≥ 2s 才切 sleep。
   * 0 = 自上电以来从未 wake 过 → 首次进 sleep 不等 2s（开机直接 sleep 是合理初值）。 */
  unsigned long last_should_wake_ms_ = 0;
  static constexpr unsigned long kIdleEnterDelayMs = 2000;
  /* 沉默低头幅度（相对 Y_CENTER）：30° 表达"放空"姿态又不顶到 Y_MAX 极限。 */
  /* 睡眠低头偏移量（逻辑角，相对 Y_CENTER）。
   * 负值 → y_logic 减小 → PWM 减小 → 物理向下（本硬件 D6 正装）。
   * 0 = 禁用睡眠低头。 */
  static constexpr int kSleepHeadDownDeg = -30;

  void onWebSocketEvent(WStype_t type, uint8_t* payload, size_t length);
  bool sendAudioJsonPcm16(const int16_t* pcm, size_t samples);
  bool sendJson(const char* msg);
  bool sendJson(const String& msg);
  /** sendTXT/BIN 失败或半开连接：断开 WS，下一轮 connect() 会 ws_.begin 重连。 */
  void forceWsReconnect(const char* why);

  /* 无 AEC：仅喇叭播音/播放队列/I2S DMA 尾音窗口内不上行真实 mic（录音环仍读麦排空队列）。 */
  bool shouldSuppressMicUplink();

  /** 播音结束后 I2S 尾音抑制截止时刻（毫秒墙钟）。 */
  unsigned long mic_suppress_until_ms_ = 0;

  void pbReset(bool stop_audio);
  void pbProtocolError(const char* why);
  bool pbApplyServoArrayIfAny(uint32_t chunk_idx);
  void pbSubmitAnimIfAny();
  void pbUpdateAudioBufDecayWall();
  void pbScheduleMotorAck(const char* req_cstr, uint32_t idx);
  void pbMaybeAck(uint32_t idx);
  void flushPendingPbAck();
  /* pb 序列播完（或仅动画无流）时置本轮下行完成，解除 runVoiceRound 等待。 */
  void pbSignalTtsRoundComplete();
  bool pbParseAndStage(const JsonDocument& doc);
  /** 仅 loop() 主上下文泵 WS；禁止在 onWebSocketEvent 内调用。 */
  void pbPumpWsWhileExpectBin(uint16_t max_pumps = 24);
  static constexpr size_t kPbDeferMaxBytes = 65536;
  static constexpr uint8_t kPbDeferQueueDepth = 4;
  uint8_t* pb_defer_bufs_[kPbDeferQueueDepth]{};
  size_t pb_defer_lens_[kPbDeferQueueDepth]{};
  uint8_t pb_defer_head_ = 0;
  uint8_t pb_defer_tail_ = 0;
  bool pbDeferEnqueue(const uint8_t* payload, size_t length);
  uint8_t pbDeferQueueDepth() const;
  void flushDeferredPbJson(bool pump_ws_after = true);
  /* audio.next_bin_len：pb_ack 仅在 loop() 发送；舵机 async 不再阻塞 ack（与音频解耦）。 */
  bool pbDispatchChunkPreamble(uint32_t chunk_idx);
};

#endif
