#include "head.h"

#include <ESP32Servo.h>
#include <cstring>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

int X_CENTER = 90;
int Y_CENTER = 90;

Servo servo_x;
Servo servo_y;

/** motor_task 维护的逻辑角；未 attach 时 head_read_* 返回此值。 */
static int s_logical_x = 90;
static int s_logical_y = 90;

/** 全生命周期只 attach 一次；head_servo_boot_attach 在 camera 之后执行。 */
static bool s_servos_attached    = false;
static bool s_servo_timers_ready = false;

static void head_sync_logical_pos(int x, int y);

/** 把全部 4 个 LEDC 定时器标记为已占用，迫使 servo.attach() 走 MCPWM 路径。
 *  必须在 setup_camera()（已占用 timer 0 作为 XCLK）之后调用。 */
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
  const int ch = servo.attach(pin);
  if (ch == 0) {
    log_warn("[SERVO] attach %s pin=%d failed (ch=0)", label, pin);
    return false;
  }
  servo.write(deg);
  log_info("[SERVO] attach %s pin=%d ok ch=%d deg=%d", label, pin, ch, deg);
  return true;
}

/** attach 单轴，defer write（boot 双轴 attach 完再统一回中）。 */
static bool head_servo_attach_axis_defer_write(Servo& servo, int pin, const char* label) {
  const int ch = servo.attach(pin);
  if (ch == 0) {
    log_warn("[SERVO] attach %s pin=%d failed (ch=0)", label, pin);
    return false;
  }
  log_info("[SERVO] attach %s pin=%d ok ch=%d", label, pin, ch);
  return true;
}

/** boot：双轴 attach，不在 attach 时写 PWM。 */
static bool head_servo_boot_attach_pins() {
  if (s_servos_attached) return true;
  head_servo_claim_mcpwm_once();
  if (!head_servo_attach_axis_defer_write(servo_y, Y_PIN, "Y")) return false;
  if (!head_servo_attach_axis_defer_write(servo_x, X_PIN, "X")) {
    servo_y.detach();
    return false;
  }
  s_servos_attached = true;
  log_info("[SERVO] attach ok (await center)");
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
 * 目的：让"播放音频 / 录音 / 刷动画"在等待头部动作完成期间不再被 delay()
 * 占满 CPU。head_move 等公开接口仍然是"同步语义"（返回时动作已完成），
 * 等待是 binary semaphore，调度器会把 CPU 让给音频等高优先级 task。
 *
 * 与 asr_chat / pb 触发的 head_* 仍可能并发写舵机；head_*
 * 命令期间退让，命令结束后逻辑角由 motor_task 维护。
 * ------------------------------------------------------------- */

struct HeadPbMotorAckMsg {
  char req[40];
  uint32_t idx;
};
static QueueHandle_t s_pb_motor_ack_q = nullptr;

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

/** 与下行 JSON `servo` 同形（另含斜坡/同步字段）。xm/ym 见 head.h HEAD_SERVO_*。 */
struct MotorCmd {
  uint8_t xm, ym;
  int     x,  y;
  uint16_t ms;       /**< 非 0：本段墙钟总预算（ms）；此时忽略 hold_ms。 */
  uint16_t hold_ms;
  uint8_t  step_deg;
  SemaphoreHandle_t notify_sem;
  bool     pb_ack_after_done;
  uint32_t pb_ack_idx;
  char     pb_ack_req[40];
};

QueueHandle_t     s_motor_queue       = nullptr;
TaskHandle_t      s_motor_task        = nullptr;
SemaphoreHandle_t s_motor_done_sem    = nullptr;
SemaphoreHandle_t s_motor_caller_lock = nullptr;

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

/* ---- motor_task ---- */

void motor_task(void* /*arg*/) {
  MotorCmd cmd{};
  for (;;) {
    if (xQueueReceive(s_motor_queue, &cmd, portMAX_DELAY) != pdTRUE) continue;

    int x = s_logical_x;
    int y = s_logical_y;
    if (!head_servo_ensure_attached(x, y)) {
      if (cmd.notify_sem) {
        xSemaphoreGive(cmd.notify_sem);
      }
      continue;
    }
    const int x_target = resolve_target(cmd.xm, x, cmd.x, X_MIN_LIMIT, X_MAX_LIMIT);
    const int y_target = resolve_target(cmd.ym, y, cmd.y, Y_MIN_LIMIT, Y_MAX_LIMIT);

    if (cmd.ms > 0) {
      /* ms 模式：Bresenham 整数线性插值，保证 ms 时间内均匀到达目标。 */
      const int total_ticks = (cmd.ms > SERVO_TICK_MS) ? (int)(cmd.ms / SERVO_TICK_MS) : 1;
      const int x_start = x, y_start = y;
      const int dx_total = x_target - x_start;
      const int dy_total = y_target - y_start;

      TickType_t last_wake = xTaskGetTickCount();
      for (int i = 1; i <= total_ticks; i++) {
        const int new_x = x_start + (long)dx_total * i / total_ticks;
        const int new_y = y_start + (long)dy_total * i / total_ticks;
        if (new_x != x) { x = new_x; head_servo_write_x(x); }
        if (new_y != y) { y = new_y; head_servo_write_y(y); }
        vTaskDelayUntil(&last_wake, k_tick_ticks);
      }
      const uint32_t used_ms = (uint32_t)total_ticks * SERVO_TICK_MS;
      if (used_ms < cmd.ms) vTaskDelay(pdMS_TO_TICKS(cmd.ms - used_ms));

    } else {
      /* 普通模式：以 step_deg 步进直到到达目标。 */
      const int step = cmd.step_deg ? cmd.step_deg : 1;
      TickType_t last_wake = xTaskGetTickCount();
      while (x != x_target || y != y_target) {
        if (x != x_target) { x = step_toward(x, x_target, step); head_servo_write_x(x); }
        if (y != y_target) { y = step_toward(y, y_target, step); head_servo_write_y(y); }
        vTaskDelayUntil(&last_wake, k_tick_ticks);
      }
      if (cmd.hold_ms) vTaskDelay(pdMS_TO_TICKS(cmd.hold_ms));
    }

    head_sync_logical_pos(x, y);

    if (cmd.notify_sem) {
      xSemaphoreGive(cmd.notify_sem);
    } else if (cmd.pb_ack_after_done && cmd.pb_ack_req[0] && s_pb_motor_ack_q) {
      HeadPbMotorAckMsg out{};
      strncpy(out.req, cmd.pb_ack_req, sizeof(out.req) - 1);
      out.idx = cmd.pb_ack_idx;
      (void)xQueueSend(s_pb_motor_ack_q, &out, 0);
    }
  }
}

/** 丢弃队列中尚未被 motor_task 取走的命令；对 sync cmd 补 give，防止调用方永久阻塞。 */
void drain_motor_queue_nonblocking() {
  if (!s_motor_queue) return;
  MotorCmd dropped{};
  while (xQueueReceive(s_motor_queue, &dropped, 0) == pdTRUE)
    if (dropped.notify_sem) xSemaphoreGive(dropped.notify_sem);
}

void ensure_motor_task() {
  if (!s_pb_motor_ack_q)
    s_pb_motor_ack_q = xQueueCreate(8, sizeof(HeadPbMotorAckMsg));
  /* 快速路径：三个句柄均已就绪。 */
  if (s_motor_queue && s_motor_task && s_motor_done_sem && s_motor_caller_lock) return;
  if (!s_motor_queue)       s_motor_queue       = xQueueCreate(32, sizeof(MotorCmd));
  if (!s_motor_done_sem)    s_motor_done_sem    = xSemaphoreCreateBinary();
  if (!s_motor_caller_lock) s_motor_caller_lock = xSemaphoreCreateMutex();
  if (!s_motor_task)
    /* core 1 (APP_CPU)，优先级 3：高于 act/anim，低于音频（5），确保 I2S 不被抢占。
     * 栈 8KB：servo.write + FreeRTOS 帧；FFat 已移出本任务。 */
    xTaskCreatePinnedToCore(motor_task, "motor", 8 * 1024, nullptr, 3, &s_motor_task, APP_CPU_NUM);
}

/** 队列满则丢最旧 async 命令，避免在 WS 回调里 portMAX_DELAY 卡死。 */
static void enqueue_motor_async_drop_oldest(const MotorCmd& cmd) {
  if (xQueueSend(s_motor_queue, &cmd, 0) == pdTRUE) return;
  MotorCmd dropped{};
  if (xQueueReceive(s_motor_queue, &dropped, 0) == pdTRUE && dropped.notify_sem)
    xSemaphoreGive(dropped.notify_sem);
  (void)xQueueSend(s_motor_queue, &cmd, 0);
}

/** 提交一条 MotorCmd；sync=true 时阻塞等待执行完成。 */
void submit_motor(uint8_t xm, int x, uint8_t ym, int y,
                  uint16_t hold_ms, uint8_t step_deg, uint16_t ms,
                  bool sync,
                  bool pb_ack = false, uint32_t pb_idx = 0, const char* pb_req = nullptr) {
  ensure_motor_task();
  MotorCmd cmd{};
  cmd.xm = xm; cmd.x = x; cmd.ym = ym; cmd.y = y;
  cmd.ms = ms; cmd.hold_ms = hold_ms; cmd.step_deg = step_deg;
  if (pb_ack && pb_req && pb_req[0]) {
    cmd.pb_ack_after_done = true;
    cmd.pb_ack_idx = pb_idx;
    strncpy(cmd.pb_ack_req, pb_req, sizeof(cmd.pb_ack_req) - 1);
  }
  if (sync) {
    xSemaphoreTake(s_motor_caller_lock, portMAX_DELAY);
    xSemaphoreTake(s_motor_done_sem, 0); /* 清理可能残留的旧 give */
    cmd.notify_sem = s_motor_done_sem;
    xQueueSend(s_motor_queue, &cmd, portMAX_DELAY);
    xSemaphoreTake(s_motor_done_sem, portMAX_DELAY);
    xSemaphoreGive(s_motor_caller_lock);
  } else {
    enqueue_motor_async_drop_oldest(cmd);
  }
}

}  // namespace

/* ================================================================
 * 公开接口实现
 * ================================================================ */

void head_servo_cmd_async(uint8_t xm, uint8_t ym, int x, int y, uint8_t step_deg, uint16_t ms,
                          bool pb_ack_after_done, uint32_t pb_ack_idx, const char* pb_ack_req) {
  submit_motor(xm, x, ym, y, /*hold_ms=*/0, step_deg, ms, /*sync=*/false,
               pb_ack_after_done, pb_ack_idx, pb_ack_req);
}

/* ---- 中位（固定 90/90，仅内存；factory 命令可临时 offset） ---- */

void adjust_x_center(int offset) {
  X_CENTER = constrain(X_CENTER + offset, X_MIN_LIMIT, X_MAX_LIMIT);
  log_info("[Factory] X_CENTER=%d (runtime only)", X_CENTER);
}

void adjust_y_center(int offset) {
  Y_CENTER = constrain(Y_CENTER + offset, Y_MIN_LIMIT, Y_MAX_LIMIT);
  log_info("[Factory] Y_CENTER=%d (runtime only)", Y_CENTER);
}

/* ---- 初始化 ---- */

void head_servo_boot_attach() {
  if (!head_servo_boot_attach_pins()) {
    log_warn("[SERVO] boot: attach failed, motor_task starts without servo");
    ensure_motor_task();
    return;
  }
  ensure_motor_task();
  /* 双轴 attach 完成后统一回中：优先 motor_task 缓动；逻辑角已在中位时直接写 PWM。 */
  const int lx = s_logical_x;
  const int ly = s_logical_y;
  submit_motor(HEAD_SERVO_ABS, X_CENTER, HEAD_SERVO_ABS, Y_CENTER, 0, 0, 0, true);
  if (lx == X_CENTER && ly == Y_CENTER) {
    head_servo_write_y(Y_CENTER);
    head_servo_write_x(X_CENTER);
    head_sync_logical_pos(X_CENTER, Y_CENTER);
  }
  log_info("[SERVO] boot center (%d,%d)", X_CENTER, Y_CENTER);
}

void setup_head() {
  log_info("[HEAD] ready center=(%d,%d) lim X[%d,%d] Y[%d,%d]",
           X_CENTER, Y_CENTER, X_MIN_LIMIT, X_MAX_LIMIT, Y_MIN_LIMIT, Y_MAX_LIMIT);
}

/* ---- 运动接口 ---- */

void head_move(int x_offset, int y_offset, int /*servo_delay*/) {
  submit_motor(HEAD_SERVO_REL, x_offset, HEAD_SERVO_REL, y_offset, 0, 0, 0, true);
}

void head_move_abs(int x_deg, int y_deg, int /*servo_delay*/) {
  submit_motor(HEAD_SERVO_ABS, x_deg, HEAD_SERVO_ABS, y_deg, 0, 0, 0, true);
}

void head_move_ex(int x_offset, int y_offset, uint8_t step_deg, uint16_t hold_ms) {
  submit_motor(HEAD_SERVO_REL, x_offset, HEAD_SERVO_REL, y_offset, hold_ms, step_deg, 0, true);
}

void head_move_abs_ex(int x_deg, int y_deg, uint8_t step_deg, uint16_t hold_ms) {
  submit_motor(HEAD_SERVO_ABS, x_deg, HEAD_SERVO_ABS, y_deg, hold_ms, step_deg, 0, true);
}

void head_center(int /*servo_delay*/)  { head_move_abs(X_CENTER, Y_CENTER); }
void head_right(int offset)            { submit_motor(HEAD_SERVO_REL,  offset, HEAD_SERVO_HOLD, 0, 0, 0, 0, true); }
void head_left(int offset)             { submit_motor(HEAD_SERVO_REL, -offset, HEAD_SERVO_HOLD, 0, 0, 0, 0, true); }
void head_down(int offset)             { submit_motor(HEAD_SERVO_HOLD, 0, HEAD_SERVO_REL,  offset, 0, 0, 0, true); }
void head_up(int offset)               { submit_motor(HEAD_SERVO_HOLD, 0, HEAD_SERVO_REL, -offset, 0, 0, 0, true); }

void head_nod(int /*servo_delay*/) {
  for (int i = 0; i < 2; i++) {
    submit_motor(HEAD_SERVO_REL, 0, HEAD_SERVO_REL,  20, k_head_gesture_hold_ms, 0, 0, true);
    submit_motor(HEAD_SERVO_REL, 0, HEAD_SERVO_REL, -20, k_head_gesture_hold_ms, 0, 0, true);
  }
}

void head_shake_async() {
  ensure_motor_task();
  static const int8_t kSeq[] = {-10, 20, -20, 20, -10};
  MotorCmd cmd{};
  cmd.xm = HEAD_SERVO_REL; cmd.ym = HEAD_SERVO_HOLD;
  cmd.hold_ms = k_head_gesture_hold_ms;
  for (int dx : kSeq) { cmd.x = dx; enqueue_motor_async_drop_oldest(cmd); }
  log_info("[HEAD] head_shake_async queued (%zu segments)", sizeof(kSeq));
}

void head_roll_left(int /*servo_delay*/) {
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

void head_roll_right(int /*servo_delay*/) {
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

void head_clear_motor_pending() {
  ensure_motor_task();
  drain_motor_queue_nonblocking();
}

bool head_take_pb_motor_ack_done(char* req_out, size_t req_cap, uint32_t* idx_out) {
  if (!s_pb_motor_ack_q || !req_out || req_cap < 2 || !idx_out) return false;
  HeadPbMotorAckMsg m{};
  if (xQueueReceive(s_pb_motor_ack_q, &m, 0) != pdTRUE) return false;
  strncpy(req_out, m.req, req_cap - 1);
  req_out[req_cap - 1] = '\0';
  *idx_out = m.idx;
  return true;
}

void head_drain_pb_motor_ack_queue() {
  if (!s_pb_motor_ack_q) return;
  HeadPbMotorAckMsg m{};
  while (xQueueReceive(s_pb_motor_ack_q, &m, 0) == pdTRUE) {}
}

unsigned head_motor_input_queue_depth() {
  ensure_motor_task();
  return s_motor_queue ? (unsigned)uxQueueMessagesWaiting(s_motor_queue) : 0u;
}
