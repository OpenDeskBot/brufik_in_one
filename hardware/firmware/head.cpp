#include "head.h"

#include <ESP32Servo.h>
#include <atomic>
#include <driver/gpio.h>
#include <soc/gpio_periph.h>
#include <soc/io_mux_reg.h>

#include "logger.h"

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

Servo servo_x;
Servo servo_y;

/** motor_task 维护的逻辑角；未 attach 时 head_read_* 返回此值。 */
static int s_logical_x = 90;
static int s_logical_y = 90;

/** 全生命周期只 attach 一次；head_servo_boot_attach 在 camera 之后执行。 */
static bool s_servos_attached    = false;
static bool s_servo_timers_ready = false;

static void head_sync_logical_pos(int x, int y);
static constexpr int kServoPulseMinUs = 1000;
static constexpr int kServoPulseMaxUs = 2000;

/** 把全部 4 个 LEDC 定时器标记为已占用，迫使 servo.attach() 走 MCPWM（避让 camera XCLK 的 LEDC timer0）。
 *  可在 setup_camera 之前调用（仅改 ESP32Servo 库记账；相机仍直接配 LEDC 硬件）。 */
static void head_servo_claim_mcpwm_once() {
  if (s_servo_timers_ready) {
    return;
  }
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  s_servo_timers_ready = true;
}

/** attach 单轴并立即 write，防止 attach 时输出默认脉冲导致舵机乱动。 */
static bool head_servo_attach_axis(Servo& servo, int pin, int deg, const char* label) {
  const int ch = servo.attach(pin, kServoPulseMinUs, kServoPulseMaxUs);
  if (!servo.attached()) {
    log_error("[SERVO] attach %s pin=%d failed (ch=%d)", label, pin, ch);
    return false;
  }
  servo.write(deg);
  log_info("[SERVO] attach %s pin=%d ok ch=%d deg=%d", label, pin, ch, deg);
  return true;
}

/** boot：双轴 attach 并立即写入已预归中的目标，避免库默认脉宽导致跳动。 */
static bool head_servo_boot_attach_pins() {
  if (s_servos_attached) return true;
  head_servo_claim_mcpwm_once();
  const int x = constrain(X_CENTER, X_MIN_LIMIT, X_MAX_LIMIT);
  const int y = constrain(Y_CENTER, Y_MIN_LIMIT, Y_MAX_LIMIT);
  if (!head_servo_attach_axis(servo_y, Y_PIN, y, "Y")) return false;
  if (!head_servo_attach_axis(servo_x, X_PIN, x, "X")) {
    servo_y.detach();
    return false;
  }
  head_sync_logical_pos(x, y);
  s_servos_attached = true;
  log_info("[SERVO] attach ok pos=(%d,%d)", x, y);
  return true;
}

/** 首次 attach；逐轴 attach+write，避免默认脉冲。 */
static bool head_servo_ensure_attached(int x_deg, int y_deg) {
  if (s_servos_attached) return true;
  x_deg = constrain(x_deg, X_MIN_LIMIT, X_MAX_LIMIT);
  y_deg = constrain(y_deg, Y_MIN_LIMIT, Y_MAX_LIMIT);
  head_servo_claim_mcpwm_once();
  if (!head_servo_attach_axis(servo_y, Y_PIN, y_deg, "Y")) return false;
  if (!head_servo_attach_axis(servo_x, X_PIN, x_deg, "X")) { servo_y.detach(); return false; }
  head_sync_logical_pos(x_deg, y_deg);
  s_servos_attached = true;
  log_info("[SERVO] attach ok pos=(%d,%d)", x_deg, y_deg);
  return true;
}

static void head_servo_write_x(int deg) {
  s_logical_x = constrain(deg, X_MIN_LIMIT, X_MAX_LIMIT);
  if (servo_x.attached()) servo_x.write(s_logical_x);
}

static void head_servo_write_y(int deg) {
  s_logical_y = constrain(deg, Y_MIN_LIMIT, Y_MAX_LIMIT);
  if (servo_y.attached()) servo_y.write(s_logical_y);
}


/* ---------------------------------------------------------------
 * Motor task：把舵机斜坡推进搬到独立 FreeRTOS 任务里。
 *
 * 目的：让"播放音频 / 录音 / 刷动画"不被头部动作的 delay() 阻塞。
 * 公开接口只负责异步入队；motor_task 是唯一执行舵机 PWM 的上下文。
 *
 * 队列由 task_setup_head 启动；pb/cmd 经 head_servo_cmd_async / enqueue_motion 入队。
 * ------------------------------------------------------------- */

static void head_sync_logical_pos(int x, int y) {
  s_logical_x = constrain(x, X_MIN_LIMIT, X_MAX_LIMIT);
  s_logical_y = constrain(y, Y_MIN_LIMIT, Y_MAX_LIMIT);
}

int head_read_x() { return s_logical_x; }

int head_read_y_logic() { return s_logical_y; }

void head_log_position() {
  log_info("[HEAD] pos=(%d,%d) center=(%d,%d) lim X[%d,%d] Y[%d,%d] pwm=%s",
           s_logical_x, s_logical_y, X_CENTER, Y_CENTER,
           X_MIN_LIMIT, X_MAX_LIMIT, Y_MIN_LIMIT, Y_MAX_LIMIT,
           s_servos_attached ? "attached" : "deferred");
}

namespace {

constexpr uint16_t   k_head_gesture_hold_ms = 15;
constexpr TickType_t k_tick_ticks           = pdMS_TO_TICKS(SERVO_TICK_MS);

/** 与 pb_servo_frame 同形（另含斜坡/同步字段）。xm/ym 见 head.h HEAD_SERVO_*。 */
struct MotorCmd {
  uint8_t xm, ym;
  int     x,  y;
  uint16_t ms;       /**< 非 0：本段墙钟总预算（ms）；此时忽略 hold_ms。 */
  uint16_t hold_ms;
  uint8_t  step_deg;
};

/** 执行器任务元素 type；cancel 走 xQueue 队尾，作旧/新任务分界。 */
enum class MotorJobType : uint8_t {
  kCancel = 0,
  kCmd = 1,
  kPbServoChunk = 2,
};

struct MotorJob {
  MotorJobType type = MotorJobType::kCmd;
  MotorCmd cmd{};
  pb_servo_frame* servo_frames = nullptr;
  size_t servo_count = 0;
};

QueueHandle_t s_motor_queue = nullptr;
TaskHandle_t  s_motor_task  = nullptr;
std::atomic<bool> s_need_cancel{false};

static void free_motor_job(MotorJob& job) {
  if (job.type == MotorJobType::kPbServoChunk) {
    pb_servo_frames_free(job.servo_frames);
    job.servo_frames = nullptr;
    job.servo_count = 0;
  }
}

/** 根据 xm/ym 模式将命令值转换为目标角度。 */
static int resolve_target(uint8_t mode, int cur, int val, int lo, int hi) {
  if (mode == HEAD_SERVO_ABS) return constrain(val,       lo, hi);
  if (mode == HEAD_SERVO_REL) return constrain(cur + val, lo, hi);
  return cur; /* HEAD_SERVO_HOLD 或非法值 */
}

/** 单步收敛：向 target 方向走最多 step，保证不越过目标（避免震荡）。 */
static int step_toward(int cur, int target, int step) {
  const int d = target - cur;
  return cur + (d > step ? step : d < -step ? -step : d);
}

/**
 * need_cancel==false → false。
 * 否则非阻塞丢弃旧任务，见到 cancel 则清 flag 并 return true（其后新任务保留）。
 * 队列空且未见 cancel → return true，保持 need_cancel。
 */
static bool poll_cancel() {
  if (!s_need_cancel.load(std::memory_order_acquire)) {
    return false;
  }
  MotorJob j{};
  while (xQueueReceive(s_motor_queue, &j, 0) == pdTRUE) {
    if (j.type == MotorJobType::kCancel) {
      s_need_cancel.store(false, std::memory_order_release);
      log_info("[HEAD] cancel");
      return true;
    }
    free_motor_job(j);
  }
  return true;
}

/** @return false 若中途 need_cancel。 */
static bool execute_motor_cmd(const MotorCmd& cmd) {
  int x = s_logical_x;
  int y = s_logical_y;
  if (!head_servo_ensure_attached(x, y)) {
    return true;
  }
  const int x_target = resolve_target(cmd.xm, x, cmd.x, X_MIN_LIMIT, X_MAX_LIMIT);
  const int y_target = resolve_target(cmd.ym, y, cmd.y, Y_MIN_LIMIT, Y_MAX_LIMIT);

  if (cmd.ms > 0) {
    const int total_ticks = (cmd.ms > SERVO_TICK_MS) ? (int)(cmd.ms / SERVO_TICK_MS) : 1;
    const int x_start = x, y_start = y;
    const int dx_total = x_target - x_start;
    const int dy_total = y_target - y_start;

    TickType_t last_wake = xTaskGetTickCount();
    for (int i = 1; i <= total_ticks; i++) {
      if (poll_cancel()) {
        head_sync_logical_pos(x, y);
        return false;
      }
      const int new_x = x_start + (long)dx_total * i / total_ticks;
      const int new_y = y_start + (long)dy_total * i / total_ticks;
      if (new_x != x) { x = new_x; head_servo_write_x(x); }
      if (new_y != y) { y = new_y; head_servo_write_y(y); }
      vTaskDelayUntil(&last_wake, k_tick_ticks);
    }
    const uint32_t used_ms = (uint32_t)total_ticks * SERVO_TICK_MS;
    if (used_ms < cmd.ms) {
      const uint32_t rem = cmd.ms - used_ms;
      TickType_t wake = xTaskGetTickCount();
      for (uint32_t left = rem; left > 0;) {
        if (poll_cancel()) {
          head_sync_logical_pos(x, y);
          return false;
        }
        const uint32_t slice = left > SERVO_TICK_MS ? SERVO_TICK_MS : left;
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(slice));
        left -= slice;
      }
    }

  } else {
    const int step = cmd.step_deg ? cmd.step_deg : 1;
    TickType_t last_wake = xTaskGetTickCount();
    while (x != x_target || y != y_target) {
      if (poll_cancel()) {
        head_sync_logical_pos(x, y);
        return false;
      }
      if (x != x_target) { x = step_toward(x, x_target, step); head_servo_write_x(x); }
      if (y != y_target) { y = step_toward(y, y_target, step); head_servo_write_y(y); }
      vTaskDelayUntil(&last_wake, k_tick_ticks);
    }
    if (cmd.hold_ms) {
      TickType_t wake = xTaskGetTickCount();
      for (uint32_t left = cmd.hold_ms; left > 0;) {
        if (poll_cancel()) {
          head_sync_logical_pos(x, y);
          return false;
        }
        const uint32_t slice = left > SERVO_TICK_MS ? SERVO_TICK_MS : left;
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(slice));
        left -= slice;
      }
    }
  }

  head_sync_logical_pos(x, y);
  return true;
}

static bool execute_pb_servo_frame(const pb_servo_frame& frame) {
  const uint16_t ms =
      frame.ms > 0 ? (uint16_t)constrain(frame.ms, 0, 65535) : (uint16_t)SERVO_TICK_MS;
  MotorCmd cmd{};
  cmd.xm = (uint8_t)constrain(frame.xm, 0, 2);
  cmd.ym = (uint8_t)constrain(frame.ym, 0, 2);
  cmd.x = frame.x;
  cmd.y = frame.y;
  cmd.ms = ms;
  return execute_motor_cmd(cmd);
}

static void execute_job(MotorJob& job) {
  if (job.type == MotorJobType::kPbServoChunk) {
    if (job.servo_frames && job.servo_count > 0) {
      for (size_t i = 0; i < job.servo_count; ++i) {
        if (!execute_pb_servo_frame(job.servo_frames[i])) {
          break;
        }
      }
    }
    free_motor_job(job);
    return;
  }
  (void)execute_motor_cmd(job.cmd);
}

/* ---- motor_task ---- */

static void motor_task(void* /*arg*/) {
  MotorJob job{};
  for (;;) {
    (void)poll_cancel();
    if (xQueueReceive(s_motor_queue, &job, portMAX_DELAY) != pdTRUE) {
      continue;
    }
    if (job.type == MotorJobType::kCancel) {
      /*
       * 空闲时 task 阻塞在 Receive，Cancel 会直接出队，不会经过 poll_cancel。
       * 若不在此清 flag，s_need_cancel 会永久为 true，后续舵机动作全被丢掉。
       */
      if (s_need_cancel.exchange(false, std::memory_order_acq_rel)) {
        log_info("[HEAD] cancel");
      }
      continue;
    }
    execute_job(job);
  }
}

/** 非阻塞入队。队列满时保留既有动作，丢弃新命令而不破坏正在排队的手势序列。 */
static bool enqueue_motor_job(MotorJob job) {
  if (!s_motor_queue) {
    free_motor_job(job);
    log_warn("[HEAD] motor queue not ready; drop command");
    return false;
  }
  if (xQueueSend(s_motor_queue, &job, 0) != pdTRUE) {
    free_motor_job(job);
    log_warn("[HEAD] motor queue full; drop new command");
    return false;
  }
  return true;
}

static bool enqueue_motion(uint8_t xm, int x, uint8_t ym, int y,
                           uint16_t hold_ms = 0, uint8_t step_deg = 0, uint16_t ms = 0) {
  MotorJob job{};
  job.type = MotorJobType::kCmd;
  job.cmd.xm = xm;
  job.cmd.x = x;
  job.cmd.ym = ym;
  job.cmd.y = y;
  job.cmd.ms = ms;
  job.cmd.hold_ms = hold_ms;
  job.cmd.step_deg = step_deg;
  return enqueue_motor_job(job);
}

}  // namespace

/* ================================================================
 * 公开接口实现
 * ================================================================ */

void task_setup_head() {
  if (s_motor_queue && s_motor_task) return;
  s_motor_queue       = xQueueCreate(HEAD_MOTOR_QUEUE_DEPTH, sizeof(MotorJob));
  if (!s_motor_queue) {
    log_error("[HEAD] motor queue create failed");
    return;
  }
  const BaseType_t rc =
      xTaskCreatePinnedToCore(motor_task, "motor", 8 * 1024, nullptr, 3, &s_motor_task, APP_CPU_NUM);
  if (rc != pdPASS) {
    log_error("[HEAD] motor task create rc=%d", (int)rc);
  } else {
    log_info("[HEAD] motor task started");
  }
}

void head_servo_cmd_async(uint8_t xm, uint8_t ym, int x, int y, uint8_t step_deg, uint16_t ms) {
  (void)enqueue_motion(xm, x, ym, y, /*hold_ms=*/0, step_deg, ms);
}

void head_submit_pb_servo_chunk_owned(pb_servo_frame* frames, size_t count) {
  if (!frames || count == 0) {
    if (frames) {
      pb_servo_frames_free(frames);
    }
    return;
  }
  MotorJob job{};
  job.type = MotorJobType::kPbServoChunk;
  job.servo_frames = frames;
  job.servo_count = count;
  if (enqueue_motor_job(job)) {
    log_info("[HEAD] pb servo[] submitted segs=%u", (unsigned)count);
  }
}

/* ---- 初始化 ---- */

void head_servo_boot_attach() {
  if (!head_servo_boot_attach_pins()) {
    log_error("[SERVO] boot: attach failed");
    return;
  }
  log_info("[SERVO] boot center (%d,%d)", X_CENTER, Y_CENTER);
}

/**
 * 角度 → 脉宽（µs）。约定 0°=1000 / 90°=1500 / 180°=2000，与常见模拟舵机一致。
 */
static int head_deg_to_pulse_us(int deg) {
  deg = constrain(deg, 0, 180);
  return kServoPulseMinUs + (deg * (kServoPulseMaxUs - kServoPulseMinUs)) / 180;
}

/**
 * GPIO 位bang 单轴中位脉冲串（不经过 Servo.attach）。
 *
 * 帧周期拉长到 period_ms（默认 60）只能降低指令刷新率，不能限制舵机转速：
 * 内部闭环在收到中位脉宽后仍会全速追位。两轴串行，降低同时堵转力矩。
 * setup_head 只做预归中；永久 PWM 由后续 head_servo_boot_attach 负责（须在 camera 之后）。
 */
static void head_gpio_soft_center_axis(int pin, int pulse_us, uint16_t period_ms, int pulses,
                                       const char* label) {
  const gpio_num_t gpio = (gpio_num_t)pin;
  PIN_FUNC_SELECT(GPIO_PIN_MUX_REG[pin], PIN_FUNC_GPIO);
  gpio_config_t cfg = {};
  cfg.pin_bit_mask  = 1ULL << pin;
  cfg.mode          = GPIO_MODE_OUTPUT;
  cfg.pull_up_en    = GPIO_PULLUP_DISABLE;
  cfg.pull_down_en  = GPIO_PULLDOWN_ENABLE;
  cfg.intr_type     = GPIO_INTR_DISABLE;
  gpio_config(&cfg);
  gpio_set_level(gpio, 0);

  /* 低电平段：帧周期 − 脉宽；period_ms 按「上升沿间隔」理解。 */
  const uint32_t low_us =
      (period_ms * 1000u > (uint32_t)pulse_us) ? (period_ms * 1000u - (uint32_t)pulse_us) : 0;

  log_info("[HEAD] gpio-center %s pin=%d pulse=%dus period=%ums n=%d", label, pin, pulse_us,
           (unsigned)period_ms, pulses);

  for (int i = 0; i < pulses; i++) {
    gpio_set_level(gpio, 1);
    delayMicroseconds(pulse_us);
    gpio_set_level(gpio, 0);
    if (low_us >= 1000) {
      delay(low_us / 1000);
      delayMicroseconds(low_us % 1000);
    } else if (low_us > 0) {
      delayMicroseconds(low_us);
    }
  }
  gpio_set_level(gpio, 0);
}

void setup_head() {
  /* 60ms/帧 ≈16.7Hz；每轴约 12 帧 ≈720ms，给机械到位留余量。 */
  static constexpr uint16_t kCenterPeriodMs = 60;
  static constexpr int      kCenterPulses   = 12;

  const int x_us = head_deg_to_pulse_us(constrain(X_CENTER, X_MIN_LIMIT, X_MAX_LIMIT));
  const int y_us = head_deg_to_pulse_us(constrain(Y_CENTER, Y_MIN_LIMIT, Y_MAX_LIMIT));

  log_info("[HEAD] gpio-center begin center=(%d,%d) us=(%d,%d) period=%ums pulses=%d/axis",
           X_CENTER, Y_CENTER, x_us, y_us, (unsigned)kCenterPeriodMs, kCenterPulses);

  head_gpio_soft_center_axis(Y_PIN, y_us, kCenterPeriodMs, kCenterPulses, "Y");
  head_gpio_soft_center_axis(X_PIN, x_us, kCenterPeriodMs, kCenterPulses, "X");

  s_logical_x = X_CENTER;
  s_logical_y = Y_CENTER;
  log_info("[HEAD] gpio-center done (await camera then permanent attach)");
}

/* ---- 运动接口 ---- */

void head_move(int x_offset, int y_offset) {
  (void)enqueue_motion(HEAD_SERVO_REL, x_offset, HEAD_SERVO_REL, y_offset);
}

void head_move_abs(int x_deg, int y_deg) {
  (void)enqueue_motion(HEAD_SERVO_ABS, x_deg, HEAD_SERVO_ABS, y_deg);
}

void head_move_ex(int x_offset, int y_offset, uint8_t step_deg, uint16_t hold_ms) {
  (void)enqueue_motion(HEAD_SERVO_REL, x_offset, HEAD_SERVO_REL, y_offset, hold_ms, step_deg);
}

void head_move_abs_ex(int x_deg, int y_deg, uint8_t step_deg, uint16_t hold_ms) {
  (void)enqueue_motion(HEAD_SERVO_ABS, x_deg, HEAD_SERVO_ABS, y_deg, hold_ms, step_deg);
}

void head_center()                     { head_move_abs(X_CENTER, Y_CENTER); }
void head_right(int offset)            { (void)enqueue_motion(HEAD_SERVO_REL,  offset, HEAD_SERVO_HOLD, 0); }
void head_left(int offset)             { (void)enqueue_motion(HEAD_SERVO_REL, -offset, HEAD_SERVO_HOLD, 0); }
void head_down(int offset)             { (void)enqueue_motion(HEAD_SERVO_HOLD, 0, HEAD_SERVO_REL,  offset); }
void head_up(int offset)               { (void)enqueue_motion(HEAD_SERVO_HOLD, 0, HEAD_SERVO_REL, -offset); }

void head_nod() {
  for (int i = 0; i < 2; i++) {
    (void)enqueue_motion(HEAD_SERVO_REL, 0, HEAD_SERVO_REL,  20, k_head_gesture_hold_ms);
    (void)enqueue_motion(HEAD_SERVO_REL, 0, HEAD_SERVO_REL, -20, k_head_gesture_hold_ms);
  }
}

void head_shake_async() {
  static const int8_t kSeq[] = {-10, 20, -20, 20, -10};
  for (int dx : kSeq)
    (void)enqueue_motion(HEAD_SERVO_REL, dx, HEAD_SERVO_HOLD, 0, k_head_gesture_hold_ms);
  log_info("[HEAD] head_shake_async queued (%u segments)",
           (unsigned)(sizeof(kSeq) / sizeof(kSeq[0])));
}

void head_roll_left() {
  /* 画圈幅度按硬限位行程比例，勿用已废弃的 Y_OFFSET(45)。 */
  static constexpr int k_dip = (Y_MAX_LIMIT - Y_MIN_LIMIT) / 4 + 5;
  static constexpr int k_x_half = (X_MAX_LIMIT - X_MIN_LIMIT) / 2;
  static constexpr int k_y_quarter = (Y_MAX_LIMIT - Y_MIN_LIMIT) / 4;
  head_center();
  head_down(k_dip);
  head_move(-k_x_half, -k_y_quarter);
  head_move( k_x_half, -k_y_quarter);
  head_move( k_x_half,  k_y_quarter);
  head_move(-k_x_half,  k_y_quarter);
  head_center();
}

void head_roll_right() {
  static constexpr int k_dip = (Y_MAX_LIMIT - Y_MIN_LIMIT) / 4 + 5;
  static constexpr int k_x_half = (X_MAX_LIMIT - X_MIN_LIMIT) / 2;
  static constexpr int k_y_quarter = (Y_MAX_LIMIT - Y_MIN_LIMIT) / 4;
  head_center();
  head_down(k_dip);
  head_move( k_x_half, -k_y_quarter);
  head_move(-k_x_half, -k_y_quarter);
  head_move(-k_x_half,  k_y_quarter);
  head_move( k_x_half,  k_y_quarter);
  head_center();
}

/* ---- 任务管理 ---- */

void head_abort() {
  if (!s_motor_queue) {
    return;
  }
  MotorJob job{};
  job.type = MotorJobType::kCancel;
  s_need_cancel.store(true, std::memory_order_release);
  (void)xQueueSend(s_motor_queue, &job, portMAX_DELAY);
}

void head_clear_motor_pending() { head_abort(); }

unsigned head_motor_input_queue_depth() {
  if (!s_motor_queue) {
    return 0;
  }
  return (unsigned)uxQueueMessagesWaiting(s_motor_queue);
}
