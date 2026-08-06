"""NetworkClient::write() 缩短 select 等待，避免 TCP 缓冲区满时单次写入最长阻塞 10s。

Arduino 3.x：WiFiClient.cpp 已迁到 Network/NetworkClient.cpp。
将 WIFI_CLIENT_SELECT_TIMEOUT_US 从 1000000us（1s）改为 20000us（20ms）。
"""
Import("env")
import os

FRAMEWORK_DIR = env.PioPlatform().get_package_dir("framework-arduinoespressif32")
CANDIDATES = [
    os.path.join(FRAMEWORK_DIR, "libraries", "Network", "src", "NetworkClient.cpp"),
    os.path.join(FRAMEWORK_DIR, "libraries", "WiFi", "src", "WiFiClient.cpp"),
]

MARKER = "/* deskbot: reduced select timeout */"
OLD_TIMEOUT = "#define WIFI_CLIENT_SELECT_TIMEOUT_US   (1000000)"
# Arduino 2.x 旧对齐（多一个空格）
OLD_TIMEOUT_LEGACY = "#define WIFI_CLIENT_SELECT_TIMEOUT_US    (1000000)"
NEW_TIMEOUT = "#define WIFI_CLIENT_SELECT_TIMEOUT_US   (20000)  /* deskbot: reduced select timeout */"

target = next((p for p in CANDIDATES if os.path.isfile(p)), None)
if not target:
    print("==> NetworkClient/WiFiClient.cpp not found, skip patch")
else:
    content = open(target, "r", encoding="utf-8").read()
    if MARKER in content:
        print("==> %s select timeout already patched" % os.path.basename(target))
    elif OLD_TIMEOUT in content:
        content = content.replace(OLD_TIMEOUT, NEW_TIMEOUT)
        open(target, "w", encoding="utf-8").write(content)
        print("==> Patched %s: WIFI_CLIENT_SELECT_TIMEOUT_US = 20000us" % os.path.basename(target))
    elif OLD_TIMEOUT_LEGACY in content:
        content = content.replace(
            OLD_TIMEOUT_LEGACY,
            "#define WIFI_CLIENT_SELECT_TIMEOUT_US    (20000)  /* deskbot: reduced select timeout */",
        )
        open(target, "w", encoding="utf-8").write(content)
        print("==> Patched %s (legacy): WIFI_CLIENT_SELECT_TIMEOUT_US = 20000us" % os.path.basename(target))
    else:
        print("==> %s: expected WIFI_CLIENT_SELECT_TIMEOUT_US not found, skip patch" % os.path.basename(target))
