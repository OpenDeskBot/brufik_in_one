#include "deskbot_uplink_state.h"

#include "deskbot_config.h"

#include <Arduino.h>

namespace {

volatile bool s_speaker_audible = false;
volatile unsigned long s_speaker_silent_until_ms = 0;
volatile bool s_ws_ready = false;
volatile bool s_ws_uplink_allowed = false;
volatile uint32_t s_ws_generation = 0;

}  // namespace

void deskbot_uplink_set_speaker_active(bool active) {
  s_speaker_audible = active;
  if (!active) {
    const unsigned long until = millis() + (unsigned long)DESKBOT_TAIL_SUPPRESS_MS;
    if (until > s_speaker_silent_until_ms) {
      s_speaker_silent_until_ms = until;
    }
  }
}

bool deskbot_uplink_speaker_audible(void) {
  return s_speaker_audible;
}

bool deskbot_uplink_in_tail_suppress(void) {
  if (s_speaker_audible) {
    return true;
  }
  return millis() < s_speaker_silent_until_ms;
}

unsigned long deskbot_uplink_tail_ms_remaining(void) {
  if (s_speaker_audible) {
    return (unsigned long)DESKBOT_TAIL_SUPPRESS_MS;
  }
  const unsigned long now = millis();
  if (now >= s_speaker_silent_until_ms) {
    return 0;
  }
  return s_speaker_silent_until_ms - now;
}

void deskbot_uplink_set_ws_ready(bool ready) {
  s_ws_ready = ready;
  s_ws_uplink_allowed = ready;
}

bool deskbot_uplink_ws_ready(void) {
  return s_ws_ready;
}

bool deskbot_uplink_ws_uplink_allowed(void) {
  return s_ws_uplink_allowed;
}

uint32_t deskbot_uplink_ws_generation(void) {
  return s_ws_generation;
}

void deskbot_uplink_bump_ws_generation(void) {
  ++s_ws_generation;
  s_ws_ready = false;
  s_ws_uplink_allowed = false;
}

bool deskbot_uplink_capture_allowed(void) {
  if (!s_ws_uplink_allowed) {
    return false;
  }
  if (s_speaker_audible) {
    return false;
  }
  if (millis() < s_speaker_silent_until_ms) {
    return false;
  }
  return true;
}
