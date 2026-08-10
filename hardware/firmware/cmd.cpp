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
  if (cmd == "head_left") {
    head_left(20);
  } else if (cmd == "head_right") {
    head_right(20);
  } else if (cmd == "head_up") {
    head_up(20);
  } else if (cmd == "head_down") {
    head_down(20);
  } else if (cmd == "head_center") {
    head_center();
  } else if (cmd == "head_nod") {
    head_nod();
  } else if (cmd == "head_shake" || cmd == "shake" || cmd == "head_shake_3") {
    head_shake_async();
  } else if (cmd == "head_roll_left") {
    head_roll_left();
  } else if (cmd == "head_roll_right") {
    head_roll_right();
  } else if (cmd == "head_clear_pending") {
    head_clear_motor_pending();
  } else if (cmd == "delay") {
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
  } else if (cmd == "head_clear_pending") {
    head_clear_motor_pending();
    log_info("[CMD] head_clear_pending");
  } else if (cmd == "head_pos") {
    head_log_position();
  } else if (cmd.startsWith("head_move_abs_ex")) {
    int v[5]; int n = parse_int_args(cmd, v, 5);
    if (n < 3) { log_warn("[CMD] head_move_abs_ex x y step [hold_ms]"); }
    else { head_move_abs_ex(v[0], v[1], (uint8_t)constrain(v[2], 0, 255),
                           (n >= 4 && v[3] > 0) ? (uint16_t)v[3] : 0);
           log_info("[CMD] head_move_abs_ex %d %d step=%d hold=%u", v[0], v[1], v[2],
                    (n >= 4 && v[3] > 0) ? (unsigned)v[3] : 0u); }
  } else if (cmd.startsWith("head_move_abs")) {
    int v[2]; int n = parse_int_args(cmd, v, 2);
    if (n >= 2) { head_move_abs(v[0], v[1]); log_info("[CMD] head_move_abs %d %d", v[0], v[1]); }
  } else if (cmd.startsWith("head_move_ex")) {
    int v[5]; int n = parse_int_args(cmd, v, 5);
    if (n < 3) { log_warn("[CMD] head_move_ex dx dy step [hold_ms]"); }
    else { head_move_ex(v[0], v[1], (uint8_t)constrain(v[2], 0, 255),
                        (n >= 4 && v[3] > 0) ? (uint16_t)v[3] : 0);
           log_info("[CMD] head_move_ex %d %d step=%d hold=%u", v[0], v[1], v[2],
                    (n >= 4 && v[3] > 0) ? (unsigned)v[3] : 0u); }
  } else if (cmd.startsWith("head_move")) {
    int v[2]; int n = parse_int_args(cmd, v, 2);
    if (n >= 2) { head_move(v[0], v[1]); }
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