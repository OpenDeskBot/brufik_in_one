#include "nvs_config_utils.h"

#include <Preferences.h>
#include <esp_random.h>
#include <string.h>

namespace {

constexpr char kDevPrefsNs[] = "deskbot_dev";
constexpr char kWifiPrefsNs[] = "deskbot_wifi";

constexpr char kPinKey[] = "pin";
constexpr char kApOfferSecKey[] = "ap_offer_sec";
constexpr unsigned kApOfferSecDefault = 20;
constexpr unsigned kApOfferSecMin = 5;
constexpr unsigned kApOfferSecMax = 60;

constexpr char kWsActiveKey[] = "ws_active";
constexpr char kWsCountKey[] = "ws_cnt";

constexpr char kWifiCountKey[] = "cnt";
constexpr char kLegacySsidKey[] = "ssid";
constexpr char kLegacyPassKey[] = "pass";

char s_pin[5] = {};
bool s_pin_ready = false;

char s_ws_active_id[8] = "builtin";
bool s_ws_active_cached = false;

bool pin_digits_valid(const char* p) {
  if (p == nullptr || strlen(p) != 4) {
    return false;
  }
  for (int i = 0; i < 4; ++i) {
    if (p[i] < '0' || p[i] > '9') {
      return false;
    }
  }
  return p[0] != '0';
}

void pin_generate_and_persist() {
  const unsigned value = (unsigned)(esp_random() % 9000u + 1000u);
  snprintf(s_pin, sizeof(s_pin), "%04u", value);
  Preferences prefs;
  if (prefs.begin(kDevPrefsNs, false)) {
    prefs.putString(kPinKey, s_pin);
    prefs.end();
  }
  s_pin_ready = true;
}

void ws_id_for_index(int index, char* out, size_t out_sz) {
  snprintf(out, out_sz, "c%d", index);
}

bool ws_custom_url_key(int index, char* out, size_t out_sz) {
  if (index < 0 || index >= NVS_MAX_CUSTOM_WS) {
    return false;
  }
  snprintf(out, out_sz, "ws_u%d", index);
  return true;
}

int ws_load_count(Preferences& prefs) {
  int count = prefs.getUChar(kWsCountKey, 0);
  if (count < 0) {
    count = 0;
  }
  if (count > NVS_MAX_CUSTOM_WS) {
    count = NVS_MAX_CUSTOM_WS;
  }
  return count;
}

bool ws_find_custom_index_by_id(Preferences& prefs, const char* id, int* out_index) {
  if (id == nullptr || id[0] == '\0' || out_index == nullptr) {
    return false;
  }
  const int count = ws_load_count(prefs);
  for (int i = 0; i < count; ++i) {
    char expect[8];
    ws_id_for_index(i, expect, sizeof(expect));
    if (strcmp(expect, id) == 0) {
      *out_index = i;
      return true;
    }
  }
  return false;
}

void ws_cache_active_id(const char* id) {
  if (id == nullptr || id[0] == '\0') {
    strncpy(s_ws_active_id, "builtin", sizeof(s_ws_active_id) - 1);
  } else {
    strncpy(s_ws_active_id, id, sizeof(s_ws_active_id) - 1);
  }
  s_ws_active_id[sizeof(s_ws_active_id) - 1] = '\0';
  s_ws_active_cached = true;
}

void ws_refresh_active_cache() {
  Preferences prefs;
  if (!prefs.begin(kDevPrefsNs, true)) {
    ws_cache_active_id("builtin");
    return;
  }
  String active = prefs.getString(kWsActiveKey, "builtin");
  active.trim();
  prefs.end();
  if (active.length() == 0) {
    ws_cache_active_id("builtin");
    return;
  }
  ws_cache_active_id(active.c_str());
}

void migrate_legacy_wifi_prefs(Preferences& prefs) {
  if (!prefs.isKey(kLegacySsidKey)) {
    return;
  }
  String old_ssid = prefs.getString(kLegacySsidKey, "");
  String old_pass = prefs.getString(kLegacyPassKey, "");
  old_ssid.trim();
  if (old_ssid.length() > 0 && prefs.getUChar(kWifiCountKey, 0) == 0) {
    prefs.putString("s0", old_ssid);
    prefs.putString("p0", old_pass);
    prefs.putUChar(kWifiCountKey, 1);
  }
  prefs.remove(kLegacySsidKey);
  prefs.remove(kLegacyPassKey);
}

bool persist_wifi_list(const NvsWifiCredential* list, int count) {
  if (list == nullptr || count < 0) {
    return false;
  }
  if (count > NVS_MAX_SAVED_WIFI) {
    count = NVS_MAX_SAVED_WIFI;
  }

  Preferences prefs;
  if (!prefs.begin(kWifiPrefsNs, false)) {
    return false;
  }
  migrate_legacy_wifi_prefs(prefs);
  prefs.putUChar(kWifiCountKey, (uint8_t)count);
  for (int i = 0; i < count; ++i) {
    char key_s[4];
    char key_p[4];
    snprintf(key_s, sizeof(key_s), "s%d", i);
    snprintf(key_p, sizeof(key_p), "p%d", i);
    prefs.putString(key_s, list[i].ssid);
    prefs.putString(key_p, list[i].password);
  }
  for (int i = count; i < NVS_MAX_SAVED_WIFI; ++i) {
    char key_s[4];
    char key_p[4];
    snprintf(key_s, sizeof(key_s), "s%d", i);
    snprintf(key_p, sizeof(key_p), "p%d", i);
    prefs.remove(key_s);
    prefs.remove(key_p);
  }
  prefs.end();
  return true;
}

}  // namespace

const char* nvs_get_pin_code() {
  if (s_pin_ready) {
    return s_pin;
  }

  Preferences prefs;
  if (prefs.begin(kDevPrefsNs, true)) {
    String stored = prefs.getString(kPinKey, "");
    stored.trim();
    if (pin_digits_valid(stored.c_str())) {
      memcpy(s_pin, stored.c_str(), 4);
      s_pin[4] = '\0';
      prefs.end();
      s_pin_ready = true;
      return s_pin;
    }
    prefs.end();
  }

  pin_generate_and_persist();
  return s_pin;
}

const char* nvs_reset_pin_code() {
  s_pin_ready = false;
  Preferences prefs;
  if (prefs.begin(kDevPrefsNs, false)) {
    prefs.remove(kPinKey);
    prefs.end();
  }
  return nvs_get_pin_code();
}

unsigned nvs_get_ap_offer_timeout_min_sec() { return kApOfferSecMin; }

unsigned nvs_get_ap_offer_timeout_max_sec() { return kApOfferSecMax; }

unsigned nvs_get_ap_offer_timeout_sec() {
  Preferences prefs;
  if (prefs.begin(kDevPrefsNs, true)) {
    const unsigned sec = prefs.getUChar(kApOfferSecKey, (uint8_t)kApOfferSecDefault);
    prefs.end();
    if (sec >= kApOfferSecMin && sec <= kApOfferSecMax) {
      return sec;
    }
  }
  return kApOfferSecDefault;
}

unsigned nvs_get_ap_offer_timeout_ms() { return nvs_get_ap_offer_timeout_sec() * 1000U; }

bool nvs_set_ap_offer_timeout_sec(unsigned sec) {
  if (sec < kApOfferSecMin || sec > kApOfferSecMax) {
    return false;
  }
  Preferences prefs;
  if (!prefs.begin(kDevPrefsNs, false)) {
    return false;
  }
  prefs.putUChar(kApOfferSecKey, (uint8_t)sec);
  prefs.end();
  return true;
}

void nvs_device_factory_reset() {
  Preferences prefs;
  if (prefs.begin(kDevPrefsNs, false)) {
    prefs.remove(kPinKey);
    prefs.putUChar(kApOfferSecKey, (uint8_t)kApOfferSecDefault);
    prefs.end();
  }
  s_pin_ready = false;
  (void)nvs_get_pin_code();
}

const char* nvs_ws_get_active_id() {
  if (!s_ws_active_cached) {
    ws_refresh_active_cache();
  }
  return s_ws_active_id;
}

bool nvs_ws_get_custom_url(const char* id, char* out, size_t out_sz) {
  if (id == nullptr || out == nullptr || out_sz == 0) {
    return false;
  }
  Preferences prefs;
  if (!prefs.begin(kDevPrefsNs, true)) {
    return false;
  }
  int index = -1;
  if (!ws_find_custom_index_by_id(prefs, id, &index)) {
    prefs.end();
    return false;
  }
  char key[8];
  if (!ws_custom_url_key(index, key, sizeof(key))) {
    prefs.end();
    return false;
  }
  String url = prefs.getString(key, "");
  url.trim();
  prefs.end();
  if (url.length() == 0 || url.length() >= out_sz) {
    return false;
  }
  strncpy(out, url.c_str(), out_sz - 1);
  out[out_sz - 1] = '\0';
  return true;
}

bool nvs_ws_set_active_id(const char* id) {
  if (id == nullptr || id[0] == '\0') {
    return false;
  }
  if (strcmp(id, "builtin") != 0) {
    char url[128];
    if (!nvs_ws_get_custom_url(id, url, sizeof(url))) {
      return false;
    }
  }

  Preferences prefs;
  if (!prefs.begin(kDevPrefsNs, false)) {
    return false;
  }
  prefs.putString(kWsActiveKey, id);
  prefs.end();
  ws_cache_active_id(id);
  return true;
}

int nvs_ws_list_custom(NvsWsServerEntry* out, int max_out) {
  if (out == nullptr || max_out <= 0) {
    return 0;
  }

  Preferences prefs;
  if (!prefs.begin(kDevPrefsNs, true)) {
    return 0;
  }
  const int count = ws_load_count(prefs);
  int written = 0;
  for (int i = 0; i < count && written < max_out; ++i) {
    char key[8];
    if (!ws_custom_url_key(i, key, sizeof(key))) {
      continue;
    }
    String url = prefs.getString(key, "");
    url.trim();
    if (url.length() == 0) {
      continue;
    }
    memset(&out[written], 0, sizeof(out[written]));
    ws_id_for_index(i, out[written].id, sizeof(out[written].id));
    strncpy(out[written].url, url.c_str(), sizeof(out[written].url) - 1);
    ++written;
  }
  prefs.end();
  return written;
}

bool nvs_ws_add_custom(const char* url, char* out_id, size_t out_id_sz) {
  if (url == nullptr || url[0] == '\0') {
    return false;
  }

  Preferences prefs;
  if (!prefs.begin(kDevPrefsNs, false)) {
    return false;
  }
  const int count = ws_load_count(prefs);
  if (count >= NVS_MAX_CUSTOM_WS) {
    prefs.end();
    return false;
  }

  char key[8];
  if (!ws_custom_url_key(count, key, sizeof(key))) {
    prefs.end();
    return false;
  }
  if (!prefs.putString(key, url)) {
    prefs.end();
    return false;
  }
  prefs.putUChar(kWsCountKey, (uint8_t)(count + 1));
  prefs.end();

  if (out_id != nullptr && out_id_sz > 0) {
    ws_id_for_index(count, out_id, out_id_sz);
  }
  return true;
}

bool nvs_ws_update_custom(const char* id, const char* url) {
  if (id == nullptr || url == nullptr || url[0] == '\0' || strcmp(id, "builtin") == 0) {
    return false;
  }

  Preferences prefs;
  if (!prefs.begin(kDevPrefsNs, false)) {
    return false;
  }
  int index = -1;
  if (!ws_find_custom_index_by_id(prefs, id, &index)) {
    prefs.end();
    return false;
  }
  char key[8];
  if (!ws_custom_url_key(index, key, sizeof(key))) {
    prefs.end();
    return false;
  }
  const bool ok = prefs.putString(key, url) != 0;
  prefs.end();
  return ok;
}

bool nvs_ws_delete_custom(const char* id) {
  if (id == nullptr || strcmp(id, "builtin") == 0) {
    return false;
  }

  Preferences prefs;
  if (!prefs.begin(kDevPrefsNs, false)) {
    return false;
  }

  int index = -1;
  if (!ws_find_custom_index_by_id(prefs, id, &index)) {
    prefs.end();
    return false;
  }

  const int count = ws_load_count(prefs);
  NvsWsServerEntry remaining[NVS_MAX_CUSTOM_WS];
  int remaining_count = 0;
  for (int i = 0; i < count; ++i) {
    if (i == index) {
      continue;
    }
    char key[8];
    if (!ws_custom_url_key(i, key, sizeof(key))) {
      continue;
    }
    String url = prefs.getString(key, "");
    url.trim();
    if (url.length() == 0) {
      continue;
    }
    memset(&remaining[remaining_count], 0, sizeof(remaining[remaining_count]));
    strncpy(remaining[remaining_count].url, url.c_str(), sizeof(remaining[remaining_count].url) - 1);
    ++remaining_count;
  }

  for (int i = 0; i < NVS_MAX_CUSTOM_WS; ++i) {
    char key[8];
    ws_custom_url_key(i, key, sizeof(key));
    prefs.remove(key);
  }
  for (int i = 0; i < remaining_count; ++i) {
    char key[8];
    ws_custom_url_key(i, key, sizeof(key));
    prefs.putString(key, remaining[i].url);
  }
  prefs.putUChar(kWsCountKey, (uint8_t)remaining_count);

  String active = prefs.getString(kWsActiveKey, "builtin");
  active.trim();
  if (active == id) {
    prefs.putString(kWsActiveKey, "builtin");
    ws_cache_active_id("builtin");
  }
  prefs.end();
  return true;
}

void nvs_ws_factory_reset() {
  Preferences prefs;
  if (prefs.begin(kDevPrefsNs, false)) {
    prefs.remove(kWsActiveKey);
    prefs.remove(kWsCountKey);
    for (int i = 0; i < NVS_MAX_CUSTOM_WS; ++i) {
      char key[8];
      ws_custom_url_key(i, key, sizeof(key));
      prefs.remove(key);
    }
    prefs.end();
  }
  ws_cache_active_id("builtin");
}

int nvs_wifi_list(NvsWifiCredential* out, int max_out) {
  if (out == nullptr || max_out <= 0) {
    return 0;
  }
  Preferences prefs;
  if (!prefs.begin(kWifiPrefsNs, false)) {
    return 0;
  }
  migrate_legacy_wifi_prefs(prefs);
  int n = prefs.getUChar(kWifiCountKey, 0);
  if (n > NVS_MAX_SAVED_WIFI) {
    n = NVS_MAX_SAVED_WIFI;
  }
  int count = 0;
  for (int i = 0; i < n && count < max_out; ++i) {
    char key_s[4];
    char key_p[4];
    snprintf(key_s, sizeof(key_s), "s%d", i);
    snprintf(key_p, sizeof(key_p), "p%d", i);
    String saved_ssid = prefs.getString(key_s, "");
    saved_ssid.trim();
    if (saved_ssid.length() == 0) {
      continue;
    }
    out[count].ssid = saved_ssid;
    out[count].password = prefs.getString(key_p, "");
    count++;
  }
  prefs.end();
  return count;
}

bool nvs_wifi_upsert(const char* ssid, const char* password) {
  if (ssid == nullptr || ssid[0] == '\0') {
    return false;
  }
  const String new_ssid(ssid);
  const String new_password(password ? password : "");

  NvsWifiCredential existing[NVS_MAX_SAVED_WIFI];
  const int existing_count = nvs_wifi_list(existing, NVS_MAX_SAVED_WIFI);

  NvsWifiCredential merged[NVS_MAX_SAVED_WIFI];
  int merged_count = 0;
  merged[merged_count].ssid = new_ssid;
  merged[merged_count].password = new_password;
  merged_count++;

  for (int i = 0; i < existing_count && merged_count < NVS_MAX_SAVED_WIFI; ++i) {
    if (existing[i].ssid == new_ssid) {
      continue;
    }
    merged[merged_count++] = existing[i];
  }

  return persist_wifi_list(merged, merged_count);
}

bool nvs_wifi_delete(const char* ssid) {
  if (ssid == nullptr || ssid[0] == '\0') {
    return false;
  }
  const String target(ssid);

  NvsWifiCredential existing[NVS_MAX_SAVED_WIFI];
  const int existing_count = nvs_wifi_list(existing, NVS_MAX_SAVED_WIFI);
  NvsWifiCredential kept[NVS_MAX_SAVED_WIFI];
  int kept_count = 0;
  bool found = false;
  for (int i = 0; i < existing_count; ++i) {
    if (existing[i].ssid == target) {
      found = true;
      continue;
    }
    if (kept_count < NVS_MAX_SAVED_WIFI) {
      kept[kept_count++] = existing[i];
    }
  }
  if (!found) {
    return false;
  }
  return persist_wifi_list(kept, kept_count);
}

void nvs_wifi_clear() {
  Preferences prefs;
  if (prefs.begin(kWifiPrefsNs, false)) {
    prefs.clear();
    prefs.end();
  }
}
