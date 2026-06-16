#include "task_trace.h"

#include "logger.h"

#include <stdio.h>
#include <string.h>

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
