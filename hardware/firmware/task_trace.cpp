#include "task_trace.h"

#include "logger.h"

#include <esp_freertos_hooks.h>
#include <freertos/FreeRTOS.h>
#include <freertos/portmacro.h>
#include <freertos/task.h>
#include <stdio.h>
#include <string.h>

#ifndef DESKBOT_CPU_STATS_INTERVAL_MS
#define DESKBOT_CPU_STATS_INTERVAL_MS 5000
#endif
#ifndef DESKBOT_CPU_STATS_MAX_TASKS
#define DESKBOT_CPU_STATS_MAX_TASKS 48
#endif

namespace {

struct TaskTraceState {
  bool active = false;
  char task[24];
  char phase[24];
  char detail[64];
  unsigned long task_start_ms = 0;
  unsigned long phase_start_ms = 0;
  unsigned long last_heartbeat_ms = 0;
  unsigned long last_stall_log_ms = 0;
};

TaskTraceState s;

void copy_field(char* dst, size_t cap, const char* src) {
  if (src == nullptr || src[0] == '\0') {
    dst[0] = '\0';
    return;
  }
  snprintf(dst, cap, "%s", src);
}

unsigned long elapsed_ms(unsigned long start_ms) {
  return millis() - start_ms;
}

void log_alive_if_due() {
  const unsigned long now = millis();
  if (now - s.last_heartbeat_ms < DESKBOT_TASK_HEARTBEAT_MS) {
    return;
  }
  s.last_heartbeat_ms = now;
  log_info("[TASK] ALIVE task=%s phase=%s task_ms=%lu phase_ms=%lu%s%s",
           s.task, s.phase, (unsigned long)elapsed_ms(s.task_start_ms),
           (unsigned long)elapsed_ms(s.phase_start_ms),
           s.detail[0] ? " detail=" : "", s.detail[0] ? s.detail : "");
}

/*
 * Arduino-ESP32 2.x 预编译 FreeRTOS 未打开 CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS，
 * vTaskGetRunTimeStats 无法链接。用双核 tick hook 采样（1kHz/核）得到等价 CPU 占比。
 */
struct CpuHit {
  TaskHandle_t handle = nullptr;
  uint32_t hits = 0;
};

CpuHit s_cpu_hits[DESKBOT_CPU_STATS_MAX_TASKS];
size_t s_cpu_hit_count = 0;
uint32_t s_cpu_total_samples = 0;
uint32_t s_cpu_overflow = 0;
portMUX_TYPE s_cpu_mux = portMUX_INITIALIZER_UNLOCKED;

void IRAM_ATTR cpu_tick_hook() {
  TaskHandle_t h = xTaskGetCurrentTaskHandle();
  portENTER_CRITICAL_ISR(&s_cpu_mux);
  s_cpu_total_samples++;
  for (size_t i = 0; i < s_cpu_hit_count; ++i) {
    if (s_cpu_hits[i].handle == h) {
      s_cpu_hits[i].hits++;
      portEXIT_CRITICAL_ISR(&s_cpu_mux);
      return;
    }
  }
  if (s_cpu_hit_count < DESKBOT_CPU_STATS_MAX_TASKS) {
    s_cpu_hits[s_cpu_hit_count].handle = h;
    s_cpu_hits[s_cpu_hit_count].hits = 1;
    s_cpu_hit_count++;
  } else {
    s_cpu_overflow++;
  }
  portEXIT_CRITICAL_ISR(&s_cpu_mux);
}

void dump_cpu_runtime_stats() {
  CpuHit snap[DESKBOT_CPU_STATS_MAX_TASKS];
  size_t n = 0;
  uint32_t total = 0;
  uint32_t overflow = 0;

  portENTER_CRITICAL(&s_cpu_mux);
  n = s_cpu_hit_count;
  if (n > DESKBOT_CPU_STATS_MAX_TASKS) {
    n = DESKBOT_CPU_STATS_MAX_TASKS;
  }
  memcpy(snap, s_cpu_hits, n * sizeof(CpuHit));
  total = s_cpu_total_samples;
  overflow = s_cpu_overflow;
  s_cpu_hit_count = 0;
  s_cpu_total_samples = 0;
  s_cpu_overflow = 0;
  memset(s_cpu_hits, 0, sizeof(s_cpu_hits));
  portEXIT_CRITICAL(&s_cpu_mux);

  /* 按 hits 降序，便于扫一眼看热点任务。 */
  for (size_t i = 1; i < n; ++i) {
    CpuHit key = snap[i];
    size_t j = i;
    while (j > 0 && snap[j - 1].hits < key.hits) {
      snap[j] = snap[j - 1];
      --j;
    }
    snap[j] = key;
  }

  /* % 相对单核满载：双核各 1kHz tick，窗口内 per_core ≈ interval_ms。 */
  const uint32_t per_core =
      (portNUM_PROCESSORS > 0) ? (total / (uint32_t)portNUM_PROCESSORS) : total;

  log_warn("[CPU] ===== task CPU stats (tick-sample, last %ums) =====",
           (unsigned)DESKBOT_CPU_STATS_INTERVAL_MS);
  log_warn("[CPU] %-16s %10s %6s", "Task", "AbsTime", "%CPU");
  for (size_t i = 0; i < n; ++i) {
    if (snap[i].handle == nullptr || snap[i].hits == 0) {
      continue;
    }
    const char* name = pcTaskGetName(snap[i].handle);
    if (name == nullptr || name[0] == '\0') {
      name = "?";
    }
    uint32_t pct = 0;
    if (per_core > 0) {
      pct = (snap[i].hits * 100u) / per_core;
    }
    if (pct == 0 && snap[i].hits > 0) {
      log_warn("[CPU] %-16s %10u   <1%%", name, (unsigned)snap[i].hits);
    } else {
      log_warn("[CPU] %-16s %10u %5u%%", name, (unsigned)snap[i].hits, (unsigned)pct);
    }
  }
  if (overflow > 0) {
    log_warn("[CPU] (overflow samples=%u, raise DESKBOT_CPU_STATS_MAX_TASKS)",
             (unsigned)overflow);
  }
  log_warn("[CPU] total_samples=%u cores=%d ===== end =====", (unsigned)total,
           (int)portNUM_PROCESSORS);
}

void cpu_runtime_stats_task(void* /*arg*/) {
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(DESKBOT_CPU_STATS_INTERVAL_MS));
    dump_cpu_runtime_stats();
  }
}

}  // namespace

void log_task_begin(const char* task, const char* detail) {
  if (s.active) {
    log_warn("[TASK] begin(%s) while task=%s phase=%s still active — auto end previous",
             task ? task : "?", s.task, s.phase);
    log_task_end("superseded");
  }
  copy_field(s.task, sizeof(s.task), task ? task : "?");
  copy_field(s.phase, sizeof(s.phase), "init");
  copy_field(s.detail, sizeof(s.detail), detail);
  const unsigned long now = millis();
  s.task_start_ms = now;
  s.phase_start_ms = now;
  s.last_heartbeat_ms = now;
  s.last_stall_log_ms = 0;
  s.active = true;
  log_info("[TASK] START task=%s%s%s", s.task, s.detail[0] ? " detail=" : "",
           s.detail[0] ? s.detail : "");
}

void log_task_end(const char* result) {
  if (!s.active) {
    return;
  }
  log_info("[TASK] END task=%s phase=%s task_ms=%lu%s%s", s.task, s.phase,
           (unsigned long)elapsed_ms(s.task_start_ms), result ? " result=" : "",
           result ? result : "");
  s.active = false;
  s.task[0] = '\0';
  s.phase[0] = '\0';
  s.detail[0] = '\0';
}

void log_task_phase(const char* phase, const char* detail) {
  if (!s.active) {
    log_task_begin("unknown", "phase-without-begin");
  }
  copy_field(s.phase, sizeof(s.phase), phase ? phase : "?");
  if (detail != nullptr) {
    copy_field(s.detail, sizeof(s.detail), detail);
  }
  const unsigned long now = millis();
  s.phase_start_ms = now;
  s.last_heartbeat_ms = now;
  s.last_stall_log_ms = 0;
  log_info("[TASK] PHASE task=%s phase=%s%s%s", s.task, s.phase,
           s.detail[0] ? " detail=" : "", s.detail[0] ? s.detail : "");
}

void log_task_pump(const char* detail) {
  if (!s.active) {
    return;
  }
  if (detail != nullptr) {
    copy_field(s.detail, sizeof(s.detail), detail);
  }
  log_alive_if_due();
}

void log_task_tick() {
  if (!s.active) {
    return;
  }
  const unsigned long phase_ms = elapsed_ms(s.phase_start_ms);
  if (phase_ms < DESKBOT_TASK_STALL_MS) {
    return;
  }
  const unsigned long now = millis();
  if (s.last_stall_log_ms != 0 &&
      (now - s.last_stall_log_ms) < DESKBOT_TASK_STALL_MS) {
    return;
  }
  s.last_stall_log_ms = now;
  log_warn("[TASK] STALL task=%s phase=%s task_ms=%lu phase_ms=%lu%s%s",
           s.task, s.phase, (unsigned long)elapsed_ms(s.task_start_ms),
           (unsigned long)phase_ms, s.detail[0] ? " detail=" : "",
           s.detail[0] ? s.detail : "");
}

void log_task_dump() {
  if (!s.active) {
    log_info("[TASK] idle (no active task)");
    return;
  }
  log_info("[TASK] NOW task=%s phase=%s task_ms=%lu phase_ms=%lu%s%s", s.task, s.phase,
           (unsigned long)elapsed_ms(s.task_start_ms),
           (unsigned long)elapsed_ms(s.phase_start_ms), s.detail[0] ? " detail=" : "",
           s.detail[0] ? s.detail : "");
}

LogTaskScope::LogTaskScope(const char* task, const char* detail) { log_task_begin(task, detail); }

LogTaskScope::~LogTaskScope() { log_task_end(nullptr); }

void task_setup_cpu_runtime_stats() {
  esp_err_t e0 = esp_register_freertos_tick_hook_for_cpu(cpu_tick_hook, 0);
  esp_err_t e1 = esp_register_freertos_tick_hook_for_cpu(cpu_tick_hook, 1);
  if (e0 != ESP_OK || e1 != ESP_OK) {
    log_error("[CPU] tick hook register failed e0=%d e1=%d", (int)e0, (int)e1);
    return;
  }

  const BaseType_t ok = xTaskCreatePinnedToCore(
      cpu_runtime_stats_task, "cpu_stats", 4 * 1024, nullptr, 1, nullptr, tskNO_AFFINITY);
  if (ok != pdPASS) {
    log_error("[CPU] failed to start cpu_stats task");
    return;
  }
  log_info("[CPU] cpu_stats started (tick-sample, interval=%ums)",
           (unsigned)DESKBOT_CPU_STATS_INTERVAL_MS);
}
