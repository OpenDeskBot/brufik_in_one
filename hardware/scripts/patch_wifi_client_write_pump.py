"""WiFiClient::write() 等 socket 可写时 pump WS RX，便于 TCP ACK 推进发送窗口。"""
Import("env")
import os

FRAMEWORK_DIR = env.PioPlatform().get_package_dir("framework-arduinoespressif32")
WIFI_CLIENT_CPP = os.path.join(
    FRAMEWORK_DIR, "libraries", "WiFi", "src", "WiFiClient.cpp"
)

MARKER = "/* deskbot: write wait pump */"
DECL = (
    "\nextern \"C\" void deskbot_ws_uplink_write_pump(void); "
    "/* deskbot: write wait pump */\n"
)
OLD = """        if(FD_ISSET(socketFileDescriptor, &set)) {
            res = send(socketFileDescriptor, (void*) buf, bytesRemaining, MSG_DONTWAIT);"""
NEW = """        if(FD_ISSET(socketFileDescriptor, &set)) {
            res = send(socketFileDescriptor, (void*) buf, bytesRemaining, MSG_DONTWAIT);"""
INSERT_AFTER_SELECT = """        if(FD_ISSET(socketFileDescriptor, &set)) {
            res = send(socketFileDescriptor, (void*) buf, bytesRemaining, MSG_DONTWAIT);"""
ELSE_BLOCK = """        } else {
            deskbot_ws_uplink_write_pump();
        }
"""

# Insert else branch before closing of while(retry) - find pattern after send block
OLD_TAIL = """            else {
                // Try again
            }
        }
    }
    return totalBytesSent;
}"""

NEW_TAIL = """            else {
                // Try again
            }
        } else {
            deskbot_ws_uplink_write_pump();
        }
    }
    return totalBytesSent;
}"""

if not os.path.isfile(WIFI_CLIENT_CPP):
    print("==> WiFiClient.cpp not found, skip write pump patch: %s" % WIFI_CLIENT_CPP)
else:
    content = open(WIFI_CLIENT_CPP, "r", encoding="utf-8").read()
    if MARKER in content and "deskbot_ws_uplink_write_pump();" in content:
        print("==> WiFiClient.cpp write pump already patched")
    elif OLD_TAIL not in content:
        print("==> WiFiClient.cpp: expected write tail not found, skip pump patch")
    else:
        if MARKER not in content:
            anchor = "size_t WiFiClient::write(const uint8_t *buf, size_t size)"
            idx = content.find(anchor)
            if idx >= 0:
                content = content[:idx] + DECL + content[idx:]
        content = content.replace(OLD_TAIL, NEW_TAIL, 1)
        with open(WIFI_CLIENT_CPP, "w", encoding="utf-8") as f:
            f.write(content)
        print("==> Patched WiFiClient.cpp: deskbot_ws_uplink_write_pump() on write wait")
