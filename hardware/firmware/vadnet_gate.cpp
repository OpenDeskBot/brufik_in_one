#include "vadnet_gate.h"

#include "deskbot_config.h"
#include "logger.h"

#include "esp_afe_config.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "model_path.h"

#include "esp_heap_caps.h"
#include <stdlib.h>
#include <string.h>

namespace {

const esp_afe_sr_iface_t* s_afe_handle = nullptr;
esp_afe_sr_data_t* s_afe_data = nullptr;
srmodel_list_t* s_models = nullptr;

int s_feed_chunk = 0;
int s_feed_nch = 1;
int16_t* s_feed_acc = nullptr;
size_t s_feed_acc_len = 0;
size_t s_feed_acc_cap = 0;
bool s_feed_acc_psram = false;

bool s_ready = false;

void vadnet_gate_teardown() {
  if (s_afe_handle && s_afe_data) {
    s_afe_handle->destroy(s_afe_data);
  }
  s_afe_data = nullptr;
  s_afe_handle = nullptr;
  if (s_models) {
    esp_srmodel_deinit(s_models);
  }
  s_models = nullptr;
  if (s_feed_acc) {
    if (s_feed_acc_psram) {
      heap_caps_free(s_feed_acc);
    } else {
      free(s_feed_acc);
    }
    s_feed_acc = nullptr;
  }
  s_feed_acc_len = 0;
  s_feed_acc_cap = 0;
  s_feed_acc_psram = false;
  s_ready = false;
}

void apply_fetch_result(const afe_fetch_result_t* res, VadnetSpeechPulse* out) {
  if (!res || !out) {
    return;
  }
  out->speech = (res->vad_state == VAD_SPEECH);
  if (res->vad_cache_size > 0 && res->vad_cache != nullptr) {
    out->cache_ready = true;
    out->cache_pcm = res->vad_cache;
    out->cache_samples = static_cast<size_t>(res->vad_cache_size) / sizeof(int16_t);
  }
}

void drain_fetch(VadnetSpeechPulse* out) {
  if (!s_afe_handle || !s_afe_data || !out) {
    return;
  }
  for (;;) {
    afe_fetch_result_t* res = s_afe_handle->fetch_with_delay(s_afe_data, 0);
    if (!res || res->ret_value != ESP_OK) {
      break;
    }
    apply_fetch_result(res, out);
  }
}

void feed_full_chunk(VadnetSpeechPulse* out) {
  s_afe_handle->feed(s_afe_data, s_feed_acc);
  s_feed_acc_len = 0;
  drain_fetch(out);
}

}  // namespace

bool vadnet_gate_available() {
  return s_ready;
}

bool vadnet_gate_setup() {
  if (s_ready) {
    return true;
  }

  s_models = esp_srmodel_init("model");
  if (s_models == nullptr) {
    log_error("[VADNET] esp_srmodel_init(model) failed — check srmodels.bin / model partition");
    return false;
  }

  afe_config_t* cfg = afe_config_init("M", s_models, AFE_TYPE_SR, AFE_MODE_LOW_COST);
  if (cfg == nullptr) {
    log_error("[VADNET] afe_config_init failed");
    vadnet_gate_teardown();
    return false;
  }

  cfg->aec_init = false;
  cfg->se_init = false;
  cfg->ns_init = false;
  cfg->wakenet_init = false;
  cfg->agc_init = false;
  cfg->vad_init = true;
  cfg->vad_mode = static_cast<vad_mode_t>(DESKBOT_VADNET_MODE);
  cfg->vad_min_speech_ms = DESKBOT_VADNET_MIN_SPEECH_MS;
  cfg->vad_min_noise_ms = DESKBOT_VADNET_MIN_NOISE_MS;
  cfg->vad_delay_ms = DESKBOT_VADNET_DELAY_MS;
  cfg->memory_alloc_mode = AFE_MEMORY_ALLOC_MORE_PSRAM;

  s_afe_handle = esp_afe_handle_from_config(cfg);
  if (s_afe_handle == nullptr) {
    log_error("[VADNET] esp_afe_handle_from_config failed");
    afe_config_free(cfg);
    vadnet_gate_teardown();
    return false;
  }

  s_afe_data = s_afe_handle->create_from_config(cfg);
  afe_config_free(cfg);
  if (s_afe_data == nullptr) {
    log_error("[VADNET] create_from_config failed");
    vadnet_gate_teardown();
    return false;
  }

  s_feed_chunk = s_afe_handle->get_feed_chunksize(s_afe_data);
  s_feed_nch = s_afe_handle->get_feed_channel_num(s_afe_data);
  if (s_feed_chunk <= 0 || s_feed_nch <= 0) {
    log_error("[VADNET] invalid feed chunk=%d nch=%d", s_feed_chunk, s_feed_nch);
    vadnet_gate_teardown();
    return false;
  }

  s_feed_acc_cap = static_cast<size_t>(s_feed_chunk) * static_cast<size_t>(s_feed_nch);
  s_feed_acc = static_cast<int16_t*>(
      heap_caps_malloc(s_feed_acc_cap * sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (s_feed_acc != nullptr) {
    s_feed_acc_psram = true;
  } else {
    s_feed_acc = static_cast<int16_t*>(malloc(s_feed_acc_cap * sizeof(int16_t)));
    s_feed_acc_psram = false;
  }
  if (s_feed_acc == nullptr) {
    log_error("[VADNET] feed buffer alloc failed (%u samples)", (unsigned)s_feed_acc_cap);
    vadnet_gate_teardown();
    return false;
  }
  s_feed_acc_len = 0;
  s_ready = true;

  log_info("[VADNET] ready feed=%d x nch=%d fetch=%d sr=%d",
           s_feed_chunk, s_feed_nch, s_afe_handle->get_fetch_chunksize(s_afe_data),
           s_afe_handle->get_samp_rate(s_afe_data));
  if (s_afe_handle->print_pipeline) {
    s_afe_handle->print_pipeline(s_afe_data);
  }
  return true;
}

void vadnet_gate_reset_round() {
  if (!s_ready) {
    return;
  }
  s_feed_acc_len = 0;
  if (s_afe_handle->reset_buffer) {
    s_afe_handle->reset_buffer(s_afe_data);
  }
  if (s_afe_handle->reset_vad) {
    s_afe_handle->reset_vad(s_afe_data);
  }
}

bool vadnet_gate_process(const int16_t* pcm, size_t samples, VadnetSpeechPulse* out) {
  if (!s_ready || pcm == nullptr || samples == 0 || out == nullptr) {
    return false;
  }

  memset(out, 0, sizeof(*out));

  for (size_t i = 0; i < samples; ++i) {
    s_feed_acc[s_feed_acc_len++] = pcm[i];
    if (s_feed_acc_len >= s_feed_acc_cap) {
      feed_full_chunk(out);
    }
  }
  return true;
}
