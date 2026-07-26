#include "pb_model.h"

#include <limits.h>
#include <string.h>
#include <esp_heap_caps.h>

namespace {

int parse_type(JsonVariantConst value) {
  if (!value.is<String>()) return PB_MODEL_UNKNOWN;
  String raw = value.as<String>();
  raw.toLowerCase();
  if (raw == "pb_start") return PB_MODEL_START;
  if (raw == "pb_chunk") return PB_MODEL_CHUNK;
  if (raw == "pb_end") return PB_MODEL_END;
  if (raw == "pb_single") return PB_MODEL_SINGLE;
  if (raw == "pb_cancel") return PB_MODEL_CANCEL;
  return PB_MODEL_UNKNOWN;
}

int parse_action(JsonVariantConst value) {
  if (!value.is<String>()) return PB_MODEL_REPLACE;
  String raw = value.as<String>();
  raw.toLowerCase();
  if (raw == "append") return PB_MODEL_APPEND;
  if (raw == "default") return PB_MODEL_DEFAULT;
  return PB_MODEL_REPLACE;
}

bool parse_nonnegative_int(JsonVariantConst value, int& out) {
  if (value.isNull() || value.is<bool>() || value.is<const char*>()) return false;
  const double raw = value.as<double>();
  if (raw < 0 || raw > INT_MAX || raw != (double)(int)raw) return false;
  out = (int)raw;
  return true;
}

String serialize_subtree(JsonVariantConst value, const char* fallback) {
  if (value.isNull()) return String(fallback);
  String out;
  serializeJson(value, out);
  return out;
}

pb_anim_element_shape parse_shape(const char* shape) {
  if (!shape) return pb_anim_element_shape::none;
  if (!strcmp(shape, "rect") || !strcmp(shape, "fill_rect")) return pb_anim_element_shape::rect;
  if (!strcmp(shape, "rect_outline") || !strcmp(shape, "draw_rect")) return pb_anim_element_shape::rect_outline;
  if (!strcmp(shape, "circle") || !strcmp(shape, "fill_circle")) return pb_anim_element_shape::circle;
  if (!strcmp(shape, "circle_outline") || !strcmp(shape, "draw_circle")) return pb_anim_element_shape::circle_outline;
  if (!strcmp(shape, "line")) return pb_anim_element_shape::line;
  if (!strcmp(shape, "ellipse") || !strcmp(shape, "draw_ellipse")) return pb_anim_element_shape::ellipse;
  if (!strcmp(shape, "ellipse_fill") || !strcmp(shape, "fill_ellipse")) return pb_anim_element_shape::ellipse_fill;
  if (!strcmp(shape, "round_rect") || !strcmp(shape, "fill_round_rect")) return pb_anim_element_shape::round_rect;
  if (!strcmp(shape, "round_rect_outline") || !strcmp(shape, "draw_round_rect")) return pb_anim_element_shape::round_rect_outline;
  if (!strcmp(shape, "text") || !strcmp(shape, "print") || !strcmp(shape, "label")) return pb_anim_element_shape::text;
  return pb_anim_element_shape::none;
}

pb_anim_element_layer parse_layer(const char* layer) {
  if (layer && !strcmp(layer, "nose")) return pb_anim_element_layer::nose;
  if (layer && !strcmp(layer, "mouth")) return pb_anim_element_layer::mouth;
  if (layer && !strcmp(layer, "eye_l")) return pb_anim_element_layer::eye_l;
  if (layer && !strcmp(layer, "eye_r")) return pb_anim_element_layer::eye_r;
  if (layer && !strcmp(layer, "extra")) return pb_anim_element_layer::extra;
  return pb_anim_element_layer::bg;
}

const char* shape_name(pb_anim_element_shape shape) {
  switch (shape) {
    case pb_anim_element_shape::rect: return "rect";
    case pb_anim_element_shape::rect_outline: return "rect_outline";
    case pb_anim_element_shape::circle: return "circle";
    case pb_anim_element_shape::circle_outline: return "circle_outline";
    case pb_anim_element_shape::line: return "line";
    case pb_anim_element_shape::ellipse: return "ellipse";
    case pb_anim_element_shape::ellipse_fill: return "ellipse_fill";
    case pb_anim_element_shape::round_rect: return "round_rect";
    case pb_anim_element_shape::round_rect_outline: return "round_rect_outline";
    case pb_anim_element_shape::text: return "text";
    default: return "";
  }
}

const char* layer_name(pb_anim_element_layer layer) {
  switch (layer) {
    case pb_anim_element_layer::nose: return "nose";
    case pb_anim_element_layer::mouth: return "mouth";
    case pb_anim_element_layer::eye_l: return "eye_l";
    case pb_anim_element_layer::eye_r: return "eye_r";
    case pb_anim_element_layer::extra: return "extra";
    default: return "bg";
  }
}

int read_int(JsonVariantConst value, int fallback = 0) {
  if (value.isNull() || value.is<bool>() || value.is<const char*>()) return fallback;
  const double raw = value.as<double>();
  if (raw < INT_MIN || raw > INT_MAX || raw != (double)(int)raw) return fallback;
  return (int)raw;
}

void copy_string(char* dst, size_t cap, const String& src) {
  if (cap == 0) return;
  src.toCharArray(dst, cap);
}

bool parse_anim_frames(JsonVariantConst value, pb_model& out) {
  if (value.isNull()) return true;
  if (!value.is<JsonArrayConst>()) return false;
  const JsonArrayConst frames = value.as<JsonArrayConst>();
  if (frames.size() > PB_ANIM_FRAME_CAPACITY) return false;
  if (frames.size() == 0) return true;
  out.anim = static_cast<pb_anim_frame*>(
      heap_caps_malloc(frames.size() * sizeof(pb_anim_frame), MALLOC_CAP_SPIRAM));
  if (!out.anim) return false;
  memset(out.anim, 0, frames.size() * sizeof(pb_anim_frame));
  for (JsonObjectConst item : frames) {
    pb_anim_frame& frame = out.anim[out.anim_count++];
    if (!parse_nonnegative_int(item["ms"], frame.ms)) return false;
    if (item["phoneme"].is<String>()) copy_string(frame.phoneme, sizeof(frame.phoneme), item["phoneme"].as<String>());
    if (!item["elements"].is<JsonObjectConst>()) return false;
    size_t n = 0;
    for (JsonPairConst layer : item["elements"].as<JsonObjectConst>()) {
      if (!layer.value().is<JsonArrayConst>()) return false;
      n += layer.value().as<JsonArrayConst>().size();
    }
    if (n == 0) continue;
    frame.elements = static_cast<pb_anim_element*>(
        heap_caps_malloc(n * sizeof(pb_anim_element), MALLOC_CAP_SPIRAM));
    if (!frame.elements) return false;
    memset(frame.elements, 0, n * sizeof(pb_anim_element));
    for (JsonPairConst layer : item["elements"].as<JsonObjectConst>()) {
      for (JsonObjectConst prim : layer.value().as<JsonArrayConst>()) {
        pb_anim_element& out_prim = frame.elements[frame.element_count++];
        out_prim.layer = parse_layer(layer.key().c_str());
        out_prim.shape = parse_shape(prim["shape"] | "");
        out_prim.color = static_cast<uint16_t>(read_int(prim["c"], 0xFFFF));
        out_prim.x = read_int(prim["x"]);
        out_prim.y = read_int(prim["y"]);
        out_prim.w = read_int(prim["w"], read_int(prim["rw"]));
        out_prim.h = read_int(prim["h"], read_int(prim["rh"]));
        out_prim.r = read_int(prim["r"], read_int(prim["radius"]));
        out_prim.x1 = read_int(prim["x1"]);
        out_prim.y1 = read_int(prim["y1"]);
        out_prim.x2 = read_int(prim["x2"]);
        out_prim.y2 = read_int(prim["y2"]);
        out_prim.text_size = read_int(prim["size"], 1);
        if (prim["text"].is<String>()) copy_string(out_prim.text, sizeof(out_prim.text), prim["text"].as<String>());
      }
    }
  }
  return true;
}

bool parse_servo_frames(JsonVariantConst value, pb_model& out) {
  if (value.isNull()) return true;
  if (!value.is<JsonArrayConst>()) return false;
  const JsonArrayConst frames = value.as<JsonArrayConst>();
  if (frames.size() > PB_SERVO_FRAME_CAPACITY) return false;
  if (frames.size() == 0) return true;
  out.servo = static_cast<pb_servo_frame*>(
      heap_caps_malloc(frames.size() * sizeof(pb_servo_frame), MALLOC_CAP_SPIRAM));
  if (!out.servo) return false;
  memset(out.servo, 0, frames.size() * sizeof(pb_servo_frame));
  for (JsonObjectConst item : frames) {
    pb_servo_frame& frame = out.servo[out.servo_count++];
    frame.xm = item["xm"] | 2;
    frame.ym = item["ym"] | 2;
    frame.x = item["x"] | 0;
    frame.y = item["y"] | 0;
    frame.ms = item["ms"] | 0;
  }
  return true;
}

int parse_mic_hint(JsonVariantConst value) {
  if (!value.is<String>()) return PB_MIC_NONE;
  String raw = value.as<String>();
  raw.toLowerCase();
  if (raw == "open") return PB_MIC_OPEN;
  if (raw == "mute") return PB_MIC_MUTE;
  return PB_MIC_NONE;
}

}  // namespace

void pb_anim_frames_free(pb_anim_frame* frames, size_t frame_count) {
  if (!frames) return;
  for (size_t i = 0; i < frame_count; ++i) {
    heap_caps_free(frames[i].elements);
    frames[i].elements = nullptr;
    frames[i].element_count = 0;
  }
  heap_caps_free(frames);
}

void pb_servo_frames_free(pb_servo_frame* frames) {
  heap_caps_free(frames);
}

void pb_audio_free(pb_audio* audio) {
  if (!audio) {
    return;
  }
  heap_caps_free(audio->bin);
  audio->bin = nullptr;
  heap_caps_free(audio);
}

void pb_model_free(pb_model& model) {
  pb_anim_frames_free(model.anim, model.anim_count);
  pb_servo_frames_free(model.servo);
  pb_audio_free(model.audio);
  model = pb_model{};
}

bool pb_model_from_json(const JsonDocument& doc, const uint8_t* media, size_t media_len,
                        pb_model& out, const char*& err) {
  pb_model_free(out);
  err = nullptr;
  out.type = parse_type(doc["type"]);
  if (out.type == PB_MODEL_UNKNOWN) {
    err = "unknown pb type";
    return false;
  }

  if (!doc["req"].is<String>()) {
    err = "missing req";
    return false;
  }
  const String req = doc["req"].as<String>();
  if (req.isEmpty() || req.length() >= sizeof(out.req)) {
    err = req.isEmpty() ? "missing req" : "req too long";
    return false;
  }
  req.toCharArray(out.req, sizeof(out.req));

  if (out.type != PB_MODEL_CANCEL) {
    if (!parse_nonnegative_int(doc["idx"], out.idx) ||
        !parse_nonnegative_int(doc["chunk_ms"], out.chunk_ms)) {
      err = "invalid idx or chunk_ms";
      return false;
    }
    if (doc["level"].is<int>() || doc["level"].is<double>()) {
      out.level = constrain((int)doc["level"].as<double>(), 0, 3);
    }
    out.action = parse_action(doc["action"]);
    out.mic = parse_mic_hint(doc["mic"]);
    if (doc["sr"].is<uint32_t>()) out.sr = doc["sr"].as<uint32_t>();
    if (doc["ch"].is<int>()) out.ch = (uint8_t)doc["ch"].as<int>();
    else if (doc["ch"].is<double>()) out.ch = (uint8_t)doc["ch"].as<double>();
    else if (doc["ch"].is<uint8_t>()) out.ch = doc["ch"].as<uint8_t>();
    if (doc["fmt"].is<String>()) copy_string(out.fmt, sizeof(out.fmt), doc["fmt"].as<String>());
    if (doc["volume"].is<int>()) out.volume = constrain(doc["volume"].as<int>(), 0, 100);
    if (doc["cam_fps"].is<int>()) out.cam_fps = doc["cam_fps"].as<int>();
    if (!parse_anim_frames(doc["anim"], out)) {
      err = "invalid anim";
      pb_model_free(out);
      return false;
    }
    if (!parse_servo_frames(doc["servo"], out)) {
      err = "invalid servo";
      pb_model_free(out);
      return false;
    }
    if (!doc["audio"].isNull()) {
      if (!doc["audio"].is<JsonObjectConst>()) {
        err = "invalid audio";
        pb_model_free(out);
        return false;
      }
      out.audio = static_cast<pb_audio*>(heap_caps_malloc(sizeof(pb_audio), MALLOC_CAP_SPIRAM));
      if (!out.audio) {
        err = "audio psram alloc failed";
        pb_model_free(out);
        return false;
      }
      memset(out.audio, 0, sizeof(pb_audio));
      out.audio->sr = out.sr;
      out.audio->ch = out.ch;
      if (out.fmt[0] != '\0') {
        copy_string(out.audio->fmt, sizeof(out.audio->fmt), String(out.fmt));
      }
      if (!parse_nonnegative_int(doc["audio"]["next_bin_len"], out.audio->next_bin_len)) {
        err = "invalid audio.next_bin_len";
        pb_model_free(out);
        return false;
      }
      if (!doc["audio"]["frames"].isNull() &&
          !parse_nonnegative_int(doc["audio"]["frames"], out.audio->frames)) {
        err = "invalid audio.frames";
        pb_model_free(out);
        return false;
      }
      if (out.audio->next_bin_len > 0) {
        if (!media || media_len < (size_t)out.audio->next_bin_len) {
          err = "audio binary missing or short";
          pb_model_free(out);
          return false;
        }
        out.audio->bin = static_cast<int8_t*>(
            heap_caps_malloc((size_t)out.audio->next_bin_len, MALLOC_CAP_SPIRAM));
        if (!out.audio->bin) {
          err = "audio psram alloc failed";
          pb_model_free(out);
          return false;
        }
        memcpy(out.audio->bin, media, (size_t)out.audio->next_bin_len);
      }
    }
  }
  return true;
}

const char* pb_model_type_name(int type) {
  switch (type) {
    case PB_MODEL_START: return "pb_start";
    case PB_MODEL_CHUNK: return "pb_chunk";
    case PB_MODEL_END: return "pb_end";
    case PB_MODEL_SINGLE: return "pb_single";
    case PB_MODEL_CANCEL: return "pb_cancel";
    default: return "unknown";
  }
}

bool pb_model_is_play_type(int type) {
  return type >= PB_MODEL_START && type <= PB_MODEL_SINGLE;
}

bool pb_model_is_chain_head(const pb_model& model) {
  return pb_model_is_play_type(model.type) && model.idx == 0;
}
