#include "deskbot_uplink_state.h"

#include "mic.h"

namespace {

volatile bool s_ws_ready = false;
volatile bool s_ws_uplink_allowed = false;
volatile uint32_t s_ws_generation = 0;

}  // namespace

void deskbot_uplink_set_ws_ready(bool ready) {
  s_ws_ready = ready;
  s_ws_uplink_allowed = ready;
  mic_set_ws_state(ready ? kMicWsOk : kMicWsError);
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
  mic_set_ws_state(kMicWsError);
}

bool deskbot_uplink_capture_allowed(void) {
  return mic_capture_allowed();
}
