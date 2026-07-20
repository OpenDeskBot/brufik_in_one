#pragma once

/** 初始化 OV2640（esp_camera）；失败返回 false，调用方可无摄像头继续启动。 */
bool setup_camera();
