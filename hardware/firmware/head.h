#ifndef Head_h
#define Head_h

#include <stddef.h>
#include <ESP32Servo.h>
#include "common.h"
#include "deskbot_config.h"

// Servo（见 deskbot_config.h）
#define X_PIN DESKBOT_ROM_X_PIN
#define Y_PIN DESKBOT_ROM_Y_PIN
/* servo_delay 参数已被 motor_task 斜坡机制替代，仅保留以兼容旧调用方签名，实现层忽略。 */
#define SERVO_DELAY 5

/** 舵机物理极限（°）；所有运动均 constrain 于此。 */
#define X_MIN_LIMIT 0
#define X_MAX_LIMIT 180
#define Y_MIN_LIMIT 70
#define Y_MAX_LIMIT 110
/** 舵机 PWM 更新周期（ms）= 50Hz，motor_task 每拍间隔。 */
constexpr uint16_t SERVO_TICK_MS = 20;

/** 逻辑中位（90/90），运行时可通过 adjust_*_center 临时偏移，不持久化。 */
extern int X_CENTER;
extern int Y_CENTER;

extern Servo servo_x;
extern Servo servo_y;

/** Y 轴逻辑角与 PWM 角一致（大舵机 D6 正装）。 */
inline int head_y_logic_to_pwm(int y_logic) { return y_logic; }
inline int head_y_pwm_to_logic(int y_pwm) { return y_pwm; }

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
/** 初始化中位/限位日志；不 attach，须在 setup_camera 之前调用。 */
void setup_head();
/** 摄像头 init 之后调用：双轴 attach → 统一回中 (X_CENTER/Y_CENTER)，启动 motor_task。 */
void head_servo_boot_attach();
void adjust_x_center(int offset);
void adjust_y_center(int offset);
void head_move(int x_offset = 0, int y_offset = 0, int servo_delay = SERVO_DELAY);
/** 绝对角（度），双轴同时到位。 */
void head_move_abs(int x_deg, int y_deg, int servo_delay = SERVO_DELAY);
/** 高级接口：step_deg=每拍最大转角(°)，0=默认1°；hold_ms=到位后停顿；async 的 ms 同 JSON `servo.ms`（墙钟预算）。 */
void head_move_ex(int x_offset, int y_offset, uint8_t step_deg = 0, uint16_t hold_ms = 0);
void head_move_abs_ex(int x_deg, int y_deg, uint8_t step_deg = 0, uint16_t hold_ms = 0);
/** 与 `servo` JSON 同形入队（仅 async）：xm/ym 为 HEAD_SERVO_*，ms 非 0 时为本段墙钟预算。
 *  pb_ack_after_done 为真且 pb_ack_req 非空时，motor_task 在本段 ramp 结束后投递一条 ack（主循环取）。 */
void head_servo_cmd_async(uint8_t xm, uint8_t ym, int x, int y, uint8_t step_deg, uint16_t ms,
                          bool pb_ack_after_done = false, uint32_t pb_ack_idx = 0, const char* pb_ack_req = nullptr);
/** 非阻塞取一条「舵机 ramp 完成」触发的 pb_ack 元数据；无消息返回 false。 */
bool head_take_pb_motor_ack_done(char* req_out, size_t req_cap, uint32_t* idx_out);
void head_drain_pb_motor_ack_queue();
void head_center(int servo_delay = SERVO_DELAY);  
void head_right(int offset = 0);  
void head_left(int offset = 0);  
void head_down(int offset = 0);  
void head_up(int offset = 0);  
void head_nod(int servo_delay = 1);
/** 异步入队摇头（避免 sync submit_motor 阻塞调用方）。 */
void head_shake_async();
void head_roll_left(int servo_delay = SERVO_DELAY);  
void head_roll_right(int servo_delay = SERVO_DELAY);  
/** 非阻塞排空 motor 的 FreeRTOS 输入队列（尚未被 motor_task 取走的 cmd）；对丢弃的 sync cmd
 *  give done_sem，避免调用方永久阻塞。当前已在执行的 ramp 不受影响。 */
void head_clear_motor_pending();

unsigned head_motor_input_queue_depth();

#endif