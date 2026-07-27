#ifndef Head_h
#define Head_h

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
int head_read_y_logic();
/** 串口打印 PWM 目标角、中位、限位与 attach 状态（非机械实测）。 */
void head_log_position();

/** 与 pb_servo_frame.xm / .ym 一致；motor 队列内 `MotorCmd` 使用同一编码。 */
constexpr uint8_t HEAD_SERVO_ABS = 0;
constexpr uint8_t HEAD_SERVO_REL = 1;
constexpr uint8_t HEAD_SERVO_HOLD = 2;

// Functions
/**
 * 相机 init 之前调用：GPIO 位bang 中位脉宽预归中（不 attach）。
 * 须在 setup_camera 之前；永久 attach 仍由 head_servo_boot_attach 完成。
 */
void setup_head();
/** 摄像头 init 之后调用：双轴永久 attach → 回中 (90/90)。 */
void head_servo_boot_attach();
/** 启动舵机 motor 队列与 motor_task（幂等）；enqueue 路径亦可兜底。 */
void task_setup_head();
void head_move(int x_offset = 0, int y_offset = 0);
/** 绝对角（度），双轴同时到位。 */
void head_move_abs(int x_deg, int y_deg);
/** 高级接口：step_deg=每拍最大转角(°)，0=默认1°；hold_ms=到位后停顿；async 的 ms 同 pb_servo_frame.ms（墙钟预算）。 */
void head_move_ex(int x_offset, int y_offset, uint8_t step_deg = 0, uint16_t hold_ms = 0);
void head_move_abs_ex(int x_deg, int y_deg, uint8_t step_deg = 0, uint16_t hold_ms = 0);
/** 与 pb_servo_frame 同形异步入队：xm/ym 为 HEAD_SERVO_*，ms 非 0 时为本段墙钟预算。 */
void head_servo_cmd_async(uint8_t xm, uint8_t ym, int x, int y, uint8_t step_deg, uint16_t ms);

/** 提交 pb_servo_frame[] chunk 所有权到 motor 队列（1 chunk = 1 队列项）。 */
void head_submit_pb_servo_chunk_owned(pb_servo_frame* frames, size_t count);

void head_center();
void head_right(int offset = 0);  
void head_left(int offset = 0);  
void head_down(int offset = 0);  
void head_up(int offset = 0);  
void head_nod();
/** 异步入队摇头。 */
void head_shake_async();
void head_roll_left();
void head_roll_right();
/**
 * 打断：置 need_cancel，再队尾入队 type=cancel。
 * 正在执行的斜坡在下一拍（约 SERVO_TICK_MS）经 poll_cancel 退出。
 */
void head_abort();
/** 同 head_abort（兼容旧名）。 */
void head_clear_motor_pending();

/** xQueue 缓冲深度（供 pb 回压）。 */
unsigned head_motor_input_queue_depth();

#endif