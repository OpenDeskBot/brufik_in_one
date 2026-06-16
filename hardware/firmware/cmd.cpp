#include "cmd.h"
#include "audio_player.h"
#include "asr_chat_client.h"
#include "task_trace.h"

extern AsrChatClient asrChatClient;

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
      log_error("JSON parse failed: %s", error.c_str());
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
 * - head_* 命令同步：head.cpp::motor_task 内部斜坡推进在独立任务，head_move 等通过 sync semaphore 等待完成。
 * - 表情/OLED 动画由 asr_chat 下行 pb 矢量帧驱动，不再支持本地 eye_* / play_animation 等命令。
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
    head_nod(3);
  } else if (cmd == "head_shake" || cmd == "shake" || cmd == "head_shake_3") {
    head_shake_async();
  } else if (cmd == "head_roll_left") {
    head_roll_left(10);
  } else if (cmd == "head_roll_right") {
    head_roll_right(10);
  } else if (cmd == "head_clear_pending") {
    head_clear_motor_pending();
  } else if (cmd == "delay") {
    delay(1000);
  } else {
    log_warn("Unknown action command: %s", cmd.c_str());
    return;
  }
  log_info("%s", cmd.c_str());
}

void executeFactoryCommand(String cmd) {
  if (cmd == "reboot" || cmd == "restart") {
    log_info("[Factory] Rebooting device...");
    ESP.restart();
  } else if (cmd.startsWith("adjust_x")) {
    int firstSpaceIndex = cmd.indexOf(' ');
    if (firstSpaceIndex > 0) {
      String offsetString = cmd.substring(firstSpaceIndex + 1);
      int offset = offsetString.toInt();
      adjust_x_center(offset);
      log_info("[Factory] X_CENTER=%d (servo attach unchanged)", X_CENTER);
    }
  } else if (cmd.startsWith("adjust_y")) {
    int firstSpaceIndex = cmd.indexOf(' ');
    if (firstSpaceIndex > 0) {
      String offsetString = cmd.substring(firstSpaceIndex + 1);
      int offset = offsetString.toInt();
      adjust_y_center(offset);
      log_info("[Factory] Y_CENTER=%d (servo attach unchanged)", Y_CENTER);
    }
  } else if (cmd == "head_clear_pending") {
    head_clear_motor_pending();
    log_info("[Factory] head_clear_pending");
  } else if (cmd == "head_pos") {
    head_log_position();
  } else if (cmd.startsWith("head_move_abs_ex")) {
    // head_move_abs_ex <x_deg> <y_deg> <step_deg> [hold_ms]
    int v[5] = {0};
    int n = 0;
    int i = cmd.indexOf(' ');
    while (n < 5 && i >= 0) {
      int j = cmd.indexOf(' ', i + 1);
      if (i + 1 >= (int)cmd.length()) {
        break;
      }
      String tok = (j < 0) ? cmd.substring(i + 1) : cmd.substring(i + 1, j);
      tok.trim();
      if (tok.length() == 0) {
        break;
      }
      v[n++] = tok.toInt();
      if (j < 0) {
        break;
      }
      i = j;
    }
    if (n < 3) {
      log_warn("[Factory] head_move_abs_ex x y step [hold_ms]");
    } else {
      int x = v[0];
      int y = v[1];
      int step = constrain(v[2], 0, 255);
      uint16_t hold_ms = (n >= 4 && v[3] > 0) ? (uint16_t)v[3] : 0;
      head_move_abs_ex(x, y, (uint8_t)step, hold_ms);
      log_info("[Factory] head_move_abs_ex %d %d step=%d hold=%u", x, y, step, (unsigned)hold_ms);
    }
  } else if (cmd.startsWith("head_move_abs")) {
    int firstSpaceIndex = cmd.indexOf(' ');
    if (firstSpaceIndex > 0) {
      int secondSpaceIndex = cmd.indexOf(' ', firstSpaceIndex + 1);
      if (secondSpaceIndex > 0) {
        int x = cmd.substring(firstSpaceIndex + 1, secondSpaceIndex).toInt();
        int y = cmd.substring(secondSpaceIndex + 1).toInt();
        head_move_abs(x, y);
        log_info("[Factory] head_move_abs %d %d", x, y);
      }
    }
  } else if (cmd.startsWith("head_move_ex")) {
    // head_move_ex <dx> <dy> <step_deg> [hold_ms]
    int v[5] = {0};
    int n = 0;
    int i = cmd.indexOf(' ');
    while (n < 5 && i >= 0) {
      int j = cmd.indexOf(' ', i + 1);
      if (i + 1 >= (int)cmd.length()) {
        break;
      }
      String tok = (j < 0) ? cmd.substring(i + 1) : cmd.substring(i + 1, j);
      tok.trim();
      if (tok.length() == 0) {
        break;
      }
      v[n++] = tok.toInt();
      if (j < 0) {
        break;
      }
      i = j;
    }
    if (n < 3) {
      log_warn("[Factory] head_move_ex dx dy step [hold_ms]");
    } else {
      int dx = v[0];
      int dy = v[1];
      int step = constrain(v[2], 0, 255);
      uint16_t hold_ms = (n >= 4 && v[3] > 0) ? (uint16_t)v[3] : 0;
      head_move_ex(dx, dy, (uint8_t)step, hold_ms);
      log_info("[Factory] head_move_ex %d %d step=%d hold=%u", dx, dy, step, (unsigned)hold_ms);
    }
  } else if (cmd.startsWith("head_move")) {
    int firstSpaceIndex = cmd.indexOf(' ');
    if (firstSpaceIndex > 0) {
      int secondSpaceIndex = cmd.indexOf(' ', firstSpaceIndex + 1);
      if (secondSpaceIndex > 0) {
        int thirdSpaceIndex = cmd.indexOf(' ', secondSpaceIndex + 1);
        if (thirdSpaceIndex > 0) {
          String xString = cmd.substring(firstSpaceIndex + 1, secondSpaceIndex);
          String yString = cmd.substring(secondSpaceIndex + 1, thirdSpaceIndex);
          String delayString = cmd.substring(thirdSpaceIndex + 1);
          int x_offset = xString.toInt();
          int y_offset = yString.toInt();
          int servo_delay = delayString.toInt();
          head_move(x_offset, y_offset, servo_delay);
        }
      }
    }
  } else if (cmd == "reset_wifi") {
    wifi_provision_reset();
  } else if (cmd == "chat") {
    /* {"factory":"chat"}：置位后进入主 loop 里的 asr_chat 长会话（服务端 ASR+LLM+TTS）。 */
    start_chat = true;
    log_info("[Factory] chat triggered");
  } else if (cmd == "task") {
    log_task_dump();
  } else if (cmd.startsWith("play_url")) {
    // {"factory":"play_url <url>"} —— 拉取 URL 指向的 WAV 并走 I2S 播放。
    // 典型用法：上位机合成 WAV、提供临时 URL，再经串口下发本命令由设备拉取播放。
    int firstSpaceIndex = cmd.indexOf(' ');
    if (firstSpaceIndex <= 0) {
      log_warn("[Factory] play_url: empty url");
      return;
    }
    String url = cmd.substring(firstSpaceIndex + 1);
    url.trim();
    if (url.isEmpty()) {
      log_warn("[Factory] play_url: empty url");
      return;
    }
    log_info("[Factory] play_url: %s", url.c_str());
    audio_play_url(url.c_str(), DESKBOT_AUDIO_PLAY_VOLUME);
  } else if (cmd.startsWith("asr_chat")) {
    // {"factory":"asr_chat"} 或 {"factory":"asr_chat 12"}：走 /asr_chat 长连接语音轮次。
    uint16_t max_sec = 10;
    int firstSpaceIndex = cmd.indexOf(' ');
    if (firstSpaceIndex > 0) {
      String sec = cmd.substring(firstSpaceIndex + 1);
      sec.trim();
      if (!sec.isEmpty()) {
        int v = sec.toInt();
        if (v > 0) {
          max_sec = (uint16_t)v;
        }
      }
    }
    log_info("[Factory] asr_chat max_sec=%u", (unsigned)max_sec);
    asrChatClient.runVoiceRound(max_sec);
  } else {
    log_warn("[Factory] Unknown factory command: %s", cmd.c_str());
    return;
  }
  log_info("%s", cmd.c_str());
}