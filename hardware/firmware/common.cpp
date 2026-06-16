#include "common.h"

bool start_chat = false;

void setup_FFat() {
  if (!FFat.begin(true)) {
    log_warn("FFat unavailable (check partition deskbot_rom_8MB.csv); servo center not persisted");
    return;
  }
  log_info("FATFS is Ready.");
}

String get_mac_address() {
  uint8_t mac_address[6];
  WiFi.macAddress(mac_address);
  char mac_str[18];
  sprintf(mac_str, "%02X:%02X:%02X:%02X:%02X:%02X",
          mac_address[0], mac_address[1], mac_address[2],
          mac_address[3], mac_address[4], mac_address[5]);
  return String(mac_str);
}

const char* get_device_id() {
  static char id[32];
  static bool initialized = false;
  if (!initialized) {
    WiFi.mode(WIFI_STA);
    uint8_t mac[6];
    WiFi.macAddress(mac);
    snprintf(id, sizeof(id), "deskbot_%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3],
             mac[4], mac[5]);
    initialized = true;
  }
  return id;
}

String get_local_ip() {
  return WiFi.localIP().toString();
}
