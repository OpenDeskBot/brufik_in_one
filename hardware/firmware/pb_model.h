#ifndef PB_MODEL_H
#define PB_MODEL_H

#include <Arduino.h>
#include <ArduinoJson.h>

/**
 * 设备端 PB 领域模型。
 *
 * wire 格式仍兼容服务端当前 JSON：type/action/req 在外层为字符串，anim/servo/audio
 * 是 JSON 子树。本模型把语义字段归一化为整数，并把子树保存为 JSON String，避免业务层
 * 依赖 ArduinoJson 文档的生命周期。req 保留服务端字符串 ID，使用定长 char 数组存储。
 */
enum PbModelType : int {
  PB_MODEL_START = 0,
  PB_MODEL_CHUNK = 1,
  PB_MODEL_END = 2,
  PB_MODEL_SINGLE = 3,
  PB_MODEL_CANCEL = 4,
  PB_MODEL_UNKNOWN = -1,
};

enum PbModelAction : int {
  PB_MODEL_REPLACE = 0,
  PB_MODEL_APPEND = 1,
  PB_MODEL_DEFAULT = 2,
};

enum PbModelMicHint : int {
  PB_MIC_NONE = 0,
  PB_MIC_OPEN = 1,
  PB_MIC_MUTE = 2,
};

constexpr size_t PB_MODEL_FMT_CAPACITY = 8;
constexpr size_t PB_ASSET_CAPACITY = 8;

constexpr size_t PB_ANIM_PHONEME_CAPACITY = 16;
constexpr size_t PB_ANIM_TEXT_CAPACITY = 129;  // 与 display.cpp 的单个文本图元上限一致。
constexpr size_t PB_ANIM_FRAME_CAPACITY = 64;
constexpr size_t PB_SERVO_FRAME_CAPACITY = 32;

/** 与显示端当前支持的 PB 图元类型一一对应。 */
enum class pb_anim_element_shape : int {
  none = 0,
  rect,
  rect_outline,
  circle,
  circle_outline,
  line,
  ellipse,
  ellipse_fill,
  round_rect,
  round_rect_outline,
  text,
  image,
};

enum class pb_anim_element_layer : int {
  bg = 0,
  nose,
  mouth,
  eye_l,
  eye_r,
  extra,
};

/** 一段头部双轴舵机动作；xm/ym：0 绝对、1 相对、2 保持。 */
struct pb_servo_frame {
  int xm = 2;
  int ym = 2;
  int x = 0;
  int y = 0;
  int ms = 0;
};

/** 一段音频 binary 的描述。格式、采样率和声道在 pb_model 顶层。 */
struct pb_audio {
  int8_t* bin = nullptr;
  int next_bin_len = 0;
  int frames = 0;  // Opus batch 帧数；PCM 时为 0。
};

/** anim[] 引用的 JPEG 附件。 */
struct pb_asset {
  int8_t* bin = nullptr;
  int next_bin_len = 0;
};

/**
 * 一个表情图层内的图元。字段覆盖当前显示端支持的 rect/circle/line/ellipse/
 * round_rect/text/image；无关字段保持默认值。
 */
struct pb_anim_element {
  pb_anim_element_layer layer = pb_anim_element_layer::bg;
  pb_anim_element_shape shape = pb_anim_element_shape::none;
  uint16_t color = 0xFFFF;
  int x = 0;
  int y = 0;
  int w = 0;
  int h = 0;
  int r = 0;
  int x1 = 0;
  int y1 = 0;
  int x2 = 0;
  int y2 = 0;
  int text_size = 1;
  char text[PB_ANIM_TEXT_CAPACITY]{};
  int asset_index = -1;
};

/** 一个表情时间片；elements 保存原始图层 JSON。 */
struct pb_anim_frame {
  pb_anim_element* elements = nullptr;
  size_t element_count = 0;
  int ms = 0;
  char phoneme[PB_ANIM_PHONEME_CAPACITY]{};
};

struct pb_model {
  int type = PB_MODEL_UNKNOWN;
  char req[37]{};
  int idx = 0;
  int chunk_ms = 0;
  int action = PB_MODEL_REPLACE;
  int level = 1;
  uint32_t sr = 0;
  uint8_t ch = 0;
  char fmt[PB_MODEL_FMT_CAPACITY]{};
  int volume = -1;   // 0–100；-1 表示未指定。
  int cam_fps = 0;   // >0 时调整相机帧率。
  int mic = PB_MIC_NONE;
  pb_anim_frame* anim = nullptr;
  size_t anim_count = 0;
  pb_servo_frame* servo = nullptr;
  size_t servo_count = 0;
  pb_audio audio;
  pb_asset* assets = nullptr;
  size_t asset_count = 0;
};

/** 回收 PSRAM 中的动画帧及其图元；可传空指针。 */
void pb_anim_frames_free(pb_anim_frame* frames, size_t frame_count);
/** 回收 PSRAM 中的舵机帧；可传空指针。 */
void pb_servo_frames_free(pb_servo_frame* frames);
/** 释放 ``pb_model_from_json`` 分配的帧、图元与音频内存；可重复调用。 */
void pb_model_free(pb_model& model);

/** 将服务端 PB JSON 解析为设备端领域模型；失败时返回 false 并写入 err。 */
bool pb_model_from_json(const JsonDocument& doc, const uint8_t* media, size_t media_len,
                        pb_model& out, const char*& err);
const char* pb_model_type_name(int type);
bool pb_model_is_play_type(int type);

#endif
