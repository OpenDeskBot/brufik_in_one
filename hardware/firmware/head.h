#pragma once

#include <stddef.h>
#include <ESP32Servo.h>
#include "deskbot_config.h"
#include "pb_model.h"

// Servo（见 deskbot_config.h）
#define X_PIN DESKBOT_ROM_X_PIN
#define Y_PIN DESKBOT_ROM_Y_PIN
/** 舵机物理极限（°）；所有运动均 constrain 于此。 */
#define X_MIN_LIMIT 0
#define X_MAX_LIMIT 180
#define Y_MIN_LIMIT 70
#define Y_MAX_LIMIT 110
/** 舵机 PWM 更新周期（ms）= 50Hz，motor_task 每拍间隔。 */
constexpr uint16_t SERVO_TICK_MS = 20;
constexpr size_t HEAD_MOTOR_QUEUE_DEPTH = DESKBOT_PB_EXECUTOR_QUEUE_DEPTH;

/** 固定逻辑中位（°）。 */
constexpr int X_CENTER = 90;
constexpr int Y_CENTER = 90;

extern Servo servo_x;
extern Servo servo_y;

/** 读 X 轴 PWM 目标角（逻辑角）；无物理反馈，不等于机械真实位置。 */
int head_read_x();
/** 读 Y 轴 PWM 目标角（逻辑角）；同上。 */
int head_read_y();

/** 与 pb_servo_frame.xm / .ym 一致；motor 队列内 `MotorCmd` 使用同一编码。 */
constexpr uint8_t HEAD_SERVO_ABS = 0;
constexpr uint8_t HEAD_SERVO_REL = 1;
constexpr uint8_t HEAD_SERVO_HOLD = 2;

// Functions
/** 双轴 MCPWM attach + 写中位（幂等），上电即可调用。 */
void setup_head();
/** 启动舵机 motor 队列与 motor_task（幂等）；enqueue 路径亦可兜底。 */
void task_setup_head();
/** 提交 pb_servo_frame[] chunk 所有权到 motor 队列（1 chunk = 1 队列项）。 */
void head_submit_pb_servo_chunk_owned(pb_servo_frame* frames, size_t count);
/** 打断：置 need_cancel，再队尾入队 type=cancel。 */
void head_abort();
/** xQueue 缓冲深度（供 pb 回压）。 */
unsigned head_motor_input_queue_depth();

