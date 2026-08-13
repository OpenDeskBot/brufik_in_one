#include "cmd.h"
#include "logger.h"
#include "speaker.h"
#include "task_trace.h"

/** 解析 cmd 中从首个空格开始的空格分隔整数，写入 out[0..max_n-1]，返回实际解析个数。 */
static int parse_int_args(const String& cmd, int* out, int max_n) {
  int n = 0;
  int i = cmd.indexOf(' ');
  while (n < max_n && i >= 0) {
    if (i + 1 >= (int)cmd.length()) break;
    int j = cmd.indexOf(' ', i + 1);
    String tok = (j < 0) ? cmd.substring(i + 1) : cmd.substring(i + 1, j);
    tok.trim();
    if (tok.length() == 0) break;
    out[n++] = tok.toInt();
    if (j < 0) break;
    i = j;
  }
  return n;
}

void handle_cmd(String cmd) {
  if (Serial.available() > 0 && cmd == "") {
    cmd = Serial.readStringUntil('\n');
    cmd.trim();
  }

  if (!cmd.isEmpty()) {
    /* 纯文本模式：非 { 开头时，直接当 factory 命令处理（便于串口调试）。 */
    if (cmd[0] != '{') {
      executeFactoryCommand(cmd);
      return;
    }

    StaticJsonDocument<1024> doc;
    DeserializationError error = deserializeJson(doc, cmd);

    if (error) {
      log_error("[CMD] JSON parse failed: %s", error.c_str());
      return;
    }

    if (doc["actions"].is<JsonArray>()) {
      JsonArray actions = doc["actions"].as<JsonArray>();
      for (JsonVariant action : actions) {
        String actionCmd = action.as<String>();
        executeCommand(actionCmd);
      }
    }

    if (doc["factory"].is<String>()) {
      String factoryCmd = doc["factory"].as<String>();
      executeFactoryCommand(factoryCmd);
    }
  }
}

/* 调用约定：
 * - head_* 命令异步入队：motor_task 独立执行斜坡，不阻塞命令处理。
 * - 表情/显示动画由 asr_chat 下行 pb 矢量帧驱动，不再支持本地 eye_* / play_animation 等命令。
 * - "delay" 命令保留为调试用，原地阻塞 1s。
 */
void executeCommand(String cmd) {
  if (cmd == "delay") {
    delay(1000);
  } else {
    log_warn("[CMD] unknown action: %s", cmd.c_str());
    return;
  }
  log_info("[CMD] %s", cmd.c_str());
}

void executeFactoryCommand(String cmd) {
  if (cmd == "reboot" || cmd == "restart") {
    log_info("[CMD] Rebooting device...");
    ESP.restart();
  } else if (cmd == "reset_wifi") {
    wifi_provision_reset();
  } else if (cmd == "chat") {
    /* 主 loop 已持续泵 pb；mic 自治上行，无需再切会话。 */
    log_info("[CMD] chat: already running (serviceLoop + mic autonomous)");
  } else if (cmd == "task") {
    log_task_dump();
  } else if (cmd.startsWith("play_url")) {
    // {"factory":"play_url <url>"} —— 拉取 URL 指向的 WAV 并走 I2S 播放。
    // 典型用法：上位机合成 WAV、提供临时 URL，再经串口下发本命令由设备拉取播放。
    int firstSpaceIndex = cmd.indexOf(' ');
    if (firstSpaceIndex <= 0) {
      log_warn("[CMD] play_url: empty url");
      return;
    }
    String url = cmd.substring(firstSpaceIndex + 1);
    url.trim();
    if (url.isEmpty()) {
      log_warn("[CMD] play_url: empty url");
      return;
    }
    log_info("[CMD] play_url: %s", url.c_str());
    speaker_play_url(url.c_str());
  } else if (cmd.startsWith("asr_chat")) {
    /* mic_task 自治上行；无需再跑语音轮次。 */
    log_info("[CMD] asr_chat: mic uplink is autonomous (no voice round)");
  } else {
    log_warn("[CMD] Unknown factory command: %s", cmd.c_str());
    return;
  }
  log_info("[CMD] %s", cmd.c_str());
}