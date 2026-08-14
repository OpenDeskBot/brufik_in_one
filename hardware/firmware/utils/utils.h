#pragma once

#include <stddef.h>
#include <stdint.h>

#include <Arduino.h>
#include <ArduinoJson.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#define VERSION "0.0.5"
#define PRODUCT_NAME "Deskbot"

/** 打包帧：u32be(json_len) + json_utf8 + optional_binary。 */
struct PackedFrame {
  JsonDocument doc;
  int bin_len = 0;
  const uint8_t* bin = nullptr;  // 指向输入 buffer 内 media 段，生命周期随 data
};

/** 单帧 JSON 上限（与服务端 ``_MAX_PACKED_JSON_LEN`` / ``PB_MAX_WIRE_JSON_BYTES`` 对齐）。 */
#ifndef DESKBOT_MAX_PACKED_JSON_LEN
#define DESKBOT_MAX_PACKED_JSON_LEN (64 * 1024)
#endif

/**
 * 解析打包 BIN 为 PackedFrame。
 * 成功填充 out 并返回 true；失败返回 false（不依赖 out 内容）。
 */
bool parse_packed_frame(uint8_t* data, size_t length, PackedFrame& out);

/**
 * 分配并组装打包帧：``u32be(json_len) + json_utf8 + optional_bin``。
 * 成功返回堆缓冲（优先 PSRAM），``*out_len`` 为总字节数；失败返回 nullptr。
 * 调用方负责 ``free()``。
 */
uint8_t* new_packed_bin(const char* json, const uint8_t* bin, size_t bin_len, size_t* out_len);

void setup_FFat();
/** 设备唯一 ID，格式 deskbot_<mac>（基于 WiFi STA MAC） */
const char* get_device_id();

/** JSON 字符串转义（``"`` ``\`` 控制字符等）。 */
String json_escape(const String& raw);

/**
 * 当前云服务器完整 WS URL（NVS active / builtin + ``/asr_chat?device_id=&version=``）。
 * 未配置时返回空串。返回值指向静态缓冲，下次调用会覆盖。
 */
const char* get_server_ws_url();

/** PIN / AP 等待 / WiFi / 云服务器 NVS：见 ``nvs_config_utils.h``。 */

/** 解析后的 WebSocket 目标（不含 query）。 */
struct DeskbotWsTarget {
  bool valid = false;
  bool use_ssl = false;
  char host[64] = {};
  uint16_t port = 0;
  /** 可选路径前缀，如 "/api"；空表示根路径。 */
  char path_prefix[48] = {};
};

/** ``ws://`` / ``wss://`` URL 解析结果。 */
struct WsProto {
  bool is_wss = false;
  char host[64] = {};
  uint16_t port = 0;
  /** 可选路径前缀，如 "/api"；无路径则为空串。 */
  char path[64] = {};
};

/** 解析 ``ws://host[:port][/path]`` 或 ``wss://...`` 到 ``out``。 */
bool parse_ws_proto(const char* str, WsProto& out);

/**
 * HTTP(S) GET 整包下载到 PSRAM（失败回落内部已释放）。
 * 成功时 *out_buf 由调用方负责 heap_caps_free（caps=MALLOC_CAP_SPIRAM）。
 * @return true 且 *out_len > 0
 */
bool utils_http_get_binary(const char* url, uint8_t** out_buf, size_t* out_len);

/**
 * 创建 pinned 任务。Arduino 3.x / IDF 5.5 下优先 Static+PSRAM 栈（TCB 仍在内部 SRAM）。
 * ``stack_bytes`` 与 ESP-IDF ``xTaskCreate*`` 一致（字节）。
 */
BaseType_t utils_task_create_pinned(TaskFunction_t fn, const char* name, uint32_t stack_bytes,
                                    void* arg, UBaseType_t prio, TaskHandle_t* out_handle,
                                    BaseType_t core_id);

/** PSRAM 优先分配，失败回落内部堆。调用方 free() 或 heap_caps_free() 均可。 */
void* psram_malloc(size_t sz);
