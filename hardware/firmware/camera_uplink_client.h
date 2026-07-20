#ifndef CAMERA_UPLINK_CLIENT_H
#define CAMERA_UPLINK_CLIENT_H

#include <Arduino.h>

/** 独立 /camera_uplink WebSocket；与 /asr_chat 分离，避免大帧阻塞语音/pb。 */
class CameraUplinkClient {
 public:
  void onLinkDown(const char* why = "wifi lost");
  void onLinkUp();

  /** WiFi 就绪后 pump + 连接维护 + 相机上行。 */
  void serviceLoop();

  /** TTS 期间暂停采集（camera_ws 回调）。 */
  bool isCapturePaused() const;

  /** write_pump 中驱动 TX 队列发送。 */
  void drainTx();

 private:
  bool connect();
  void maintainConnection();
  void registerHandlers();
  bool canUpload() const;
  bool tryUploadFrameIfDue();
  bool tryUploadFrame();
  void pump();
  void discardTxQueue();

  bool ready_ = false;
  bool needs_reconnect_ = false;
  unsigned long reconnect_backoff_ms_ = 2000;
  unsigned long last_reconnect_attempt_ms_ = 0;
  unsigned long last_uplink_ms_ = 0;
  unsigned long last_capture_ms_ = 0;
  unsigned long backoff_until_ms_ = 0;
  bool tx_active_ = false;
  bool handlers_registered_ = false;
  bool connect_in_progress_ = false;
  unsigned long connect_started_ms_ = 0;
};

extern CameraUplinkClient cameraUplinkClient;

void camera_uplink_write_pump(void);

/** 仅 pump 相机 WS 事件循环，不 drain TX（供 asr_chat 上行重试时交叉泵 TCP）。 */
void camera_uplink_pump_only(void);

#endif
