/* camera_http.cpp — Deskbot 本地摄像头 HTTP（GPL-3.0）
 * 仅 MJPEG 流与抓拍，替代 Espressif CameraWebServer 示例以统一许可证。
 */
#include "camera_http.h"

#include "common.h"

#include <Arduino.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_timer.h"
#include "img_converters.h"

namespace {

constexpr char kBoundary[] = "deskbot_cam_stream_boundary";
constexpr char kStreamType[] = "multipart/x-mixed-replace;boundary=deskbot_cam_stream_boundary";

httpd_handle_t s_httpd = nullptr;
httpd_handle_t s_stream_httpd = nullptr;

static esp_err_t index_handler(httpd_req_t* req) {
  static const char kHtml[] =
      "<!DOCTYPE html><html><head><meta charset=utf-8><title>Deskbot</title></head>"
      "<body><h3>Deskbot camera</h3>"
      "<p><a href=\"/stream\">MJPEG stream</a> &middot; <a href=\"/capture\">snapshot</a></p>"
      "<img src=\"/stream\" style=\"max-width:100%%\"></body></html>";
  httpd_resp_set_type(req, "text/html");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, kHtml, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t capture_handler(httpd_req_t* req) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    return httpd_resp_send_500(req);
  }

  uint8_t* jpg_buf = fb->buf;
  size_t jpg_len = fb->len;
  uint8_t* temp = nullptr;

  if (fb->format != PIXFORMAT_JPEG) {
    if (!frame2jpg(fb, 80, &temp, &jpg_len)) {
      esp_camera_fb_return(fb);
      return httpd_resp_send_500(req);
    }
    jpg_buf = temp;
    esp_camera_fb_return(fb);
    fb = nullptr;
  }

  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t res = httpd_resp_send(req, (const char*)jpg_buf, jpg_len);

  if (temp) {
    free(temp);
  } else if (fb) {
    esp_camera_fb_return(fb);
  }
  return res;
}

static esp_err_t stream_handler(httpd_req_t* req) {
  esp_err_t res = httpd_resp_set_type(req, kStreamType);
  if (res != ESP_OK) {
    return res;
  }
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  char part_hdr[128];
  const char boundary_prefix[] = "\r\n--deskbot_cam_stream_boundary\r\n";

  while (true) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      log_error("[CAM_HTTP] stream: fb_get failed");
      return ESP_FAIL;
    }

    const time_t ts_sec = fb->timestamp.tv_sec;
    const suseconds_t ts_usec = fb->timestamp.tv_usec;

    uint8_t* jpg_buf = fb->buf;
    size_t jpg_len = fb->len;
    uint8_t* temp = nullptr;

    if (fb->format != PIXFORMAT_JPEG) {
      if (!frame2jpg(fb, 80, &temp, &jpg_len)) {
        esp_camera_fb_return(fb);
        return ESP_FAIL;
      }
      jpg_buf = temp;
      esp_camera_fb_return(fb);
    }

    const int hlen = snprintf(part_hdr, sizeof(part_hdr),
                            "Content-Type: image/jpeg\r\nContent-Length: %u\r\n"
                            "X-Timestamp: %ld.%06ld\r\n\r\n",
                            (unsigned)jpg_len, (long)ts_sec, (long)ts_usec);

    if (httpd_resp_send_chunk(req, boundary_prefix, strlen(boundary_prefix)) != ESP_OK ||
        httpd_resp_send_chunk(req, part_hdr, (size_t)hlen) != ESP_OK ||
        httpd_resp_send_chunk(req, (const char*)jpg_buf, jpg_len) != ESP_OK) {
      if (temp) {
        free(temp);
      } else {
        esp_camera_fb_return(fb);
      }
      return ESP_FAIL;
    }

    if (temp) {
      free(temp);
    } else {
      esp_camera_fb_return(fb);
    }
  }
}

}  // namespace

void startCameraServer(void) {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.max_uri_handlers = 8;

  if (httpd_start(&s_httpd, &config) == ESP_OK) {
    httpd_uri_t index_uri = {.uri = "/", .method = HTTP_GET, .handler = index_handler};
    httpd_uri_t capture_uri = {.uri = "/capture", .method = HTTP_GET, .handler = capture_handler};
    httpd_register_uri_handler(s_httpd, &index_uri);
    httpd_register_uri_handler(s_httpd, &capture_uri);
    log_info("[CAM_HTTP] http://<ip>/  capture /capture");
  } else {
    log_error("[CAM_HTTP] main server start failed");
  }

  config.server_port += 1;
  config.ctrl_port += 1;
  if (httpd_start(&s_stream_httpd, &config) == ESP_OK) {
    httpd_uri_t stream_uri = {.uri = "/stream", .method = HTTP_GET, .handler = stream_handler};
    httpd_register_uri_handler(s_stream_httpd, &stream_uri);
    log_info("[CAM_HTTP] stream http://<ip>:%u/stream", (unsigned)config.server_port);
  } else {
    log_error("[CAM_HTTP] stream server start failed");
  }
}
