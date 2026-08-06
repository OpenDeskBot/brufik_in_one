## 说明

<!-- 改了什么、为什么 -->

## 测试

- [ ] `./flash_rom.sh build` 通过（Python ≥3.10 + pioarduino 55.03.39）
- [ ] 未回退到官方 PlatformIO `espressif32`（Arduino 2.x / IDF 4.4）
- [ ] 若改 `firmware/deskbot_config.h` 默认值：未提交内网 IP / WiFi 密码
- [ ] 未提交 `.pio/`、`.venv/`、`deskbot.local.env` 等本地文件
- [ ] mic/speaker 仍为 ESP_I2S（勿混用 legacy `driver/i2s.h`）
