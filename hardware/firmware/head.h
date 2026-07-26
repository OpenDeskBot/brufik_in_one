#ifndef Head_h
#define Head_h

#include <stddef.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>
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
constexpr size_t HEAD_MOTOR_QUEUE_DEPTH = 5;

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

/** 与下行 JSON `servo.xm` / `servo.ym` 一致；motor 队列内 `MotorCmd` 使用同一编码。 */
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
/** 高级接口：step_deg=每拍最大转角(°)，0=默认1°；hold_ms=到位后停顿；async 的 ms 同 JSON `servo.ms`（墙钟预算）。 */
void head_move_ex(int x_offset, int y_offset, uint8_t step_deg = 0, uint16_t hold_ms = 0);
void head_move_abs_ex(int x_deg, int y_deg, uint8_t step_deg = 0, uint16_t hold_ms = 0);
/** 与 `servo` JSON 同形异步入队：xm/ym 为 HEAD_SERVO_*，ms 非 0 时为本段墙钟预算。 */
void head_servo_cmd_async(uint8_t xm, uint8_t ym, int x, int y, uint8_t step_deg, uint16_t ms);

/**
 * pb 下行 servo[]：解析到 head 暂存（覆盖未 flush 的旧暂存）。
 * 非数组 / null 则清空暂存；非法字段忽略。
 */
void head_stage_pb_servo(JsonVariantConst servo_field);
/** 提交 pb_servo_frame[] chunk 所有权到 motor 队列（1 chunk = 1 队列项）。 */
void head_submit_pb_servo_chunk_owned(pb_servo_frame* frames, size_t count);
/** @deprecated 兼容旧 JSON 路径。 */
void head_stage_pb_servo_json(const String& servo_json);
/** 将暂存段异步入队 motor；返回是否入队了至少一段。 */
bool head_flush_pb_servo();
void head_clear_pb_servo_pending();
size_t head_pb_servo_pending_count();

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
/** 非阻塞排空 motor 的 FreeRTOS 输入队列；当前正在执行的 ramp 不受影响。 */
void head_clear_motor_pending();
/** 丢弃最早尚未执行的 motor 命令；用于 PB 调度器的队列腾挪。 */
bool head_drop_oldest_motor_pending();

unsigned head_motor_input_queue_depth();

#endif