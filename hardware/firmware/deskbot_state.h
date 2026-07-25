#pragma once

#include <stdint.h>

/** 状态上传任务通知：Stop=停发；Go=距上次满 10s 再发一次；GoNow=立刻发一次。 */
enum StateNotify : int8_t {
  kStateStop = 0,
  kStateGo = 1,
  kStateGoNow = 2,
};

/**
 * 启动状态上传任务：仅通过通知队列决定是否上报，不直接碰 WiFi/WebSocket。
 * 实际发送由 ws_transport owner 完成。
 */
void task_setup_deskbot_state();

/** 通知状态任务：先清空单槽队列再放入 n。 */
void deskbot_state_notify(StateNotify n);
