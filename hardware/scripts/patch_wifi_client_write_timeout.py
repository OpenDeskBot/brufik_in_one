"""WiFiClient::write() 使用绝对截止时间，避免 TCP 缓冲区满时 select() 每次等待1秒导致单次写入最长阻塞10秒。
将 WIFI_CLIENT_SELECT_TIMEOUT_US 从 1000000us（1秒）改为 20000us（20ms），
10次重试上限 × 20ms = 200ms 最大单次写入阻塞，与 WEBSOCKETS_TCP_TIMEOUT=400ms 配合生效。
"""
Import("env")
import os

# WiFiClient.cpp 在 framework 包目录，与 libdeps 不同
FRAMEWORK_DIR = env.PioPlatform().get_package_dir("framework-arduinoespressif32")
WIFI_CLIENT_CPP = os.path.join(
    FRAMEWORK_DIR, "libraries", "WiFi", "src", "WiFiClient.cpp"
)

MARKER = "/* deskbot: reduced select timeout */"
OLD_TIMEOUT = "#define WIFI_CLIENT_SELECT_TIMEOUT_US    (1000000)"
NEW_TIMEOUT = "#define WIFI_CLIENT_SELECT_TIMEOUT_US    (20000)  /* deskbot: reduced select timeout */"

if not os.path.isfile(WIFI_CLIENT_CPP):
    print("==> WiFiClient.cpp not found, skip patch: %s" % WIFI_CLIENT_CPP)
elif MARKER in open(WIFI_CLIENT_CPP, "r", encoding="utf-8").read():
    print("==> WiFiClient.cpp select timeout already patched")
elif OLD_TIMEOUT not in open(WIFI_CLIENT_CPP, "r", encoding="utf-8").read():
    print("==> WiFiClient.cpp: expected WIFI_CLIENT_SELECT_TIMEOUT_US not found, skip patch")
else:
    with open(WIFI_CLIENT_CPP, "r", encoding="utf-8") as f:
        content = f.read()
    content = content.replace(OLD_TIMEOUT, NEW_TIMEOUT)
    with open(WIFI_CLIENT_CPP, "w", encoding="utf-8") as f:
        f.write(content)
    print("==> Patched WiFiClient.cpp: WIFI_CLIENT_SELECT_TIMEOUT_US = 20000us (was 1000000us)")
