"""Revert deskbot write-pump hooks from WiFiClient.cpp。"""
Import("env")
import os

FRAMEWORK_DIR = env.PioPlatform().get_package_dir("framework-arduinoespressif32")
WIFI_CLIENT_CPP = os.path.join(
    FRAMEWORK_DIR, "libraries", "WiFi", "src", "WiFiClient.cpp"
)

MARKER = "/* deskbot: write wait pump */"
DECL = (
    "\nextern \"C\" void deskbot_ws_transport_write_pump(void); "
    "/* deskbot: write wait pump */\n"
)
PATCHED_TAIL = """            else {
                // Try again
            }
        } else {
            deskbot_ws_transport_write_pump();
        }
    }
    return totalBytesSent;
}"""
ORIG_TAIL = """            else {
                // Try again
            }
        }
    }
    return totalBytesSent;
}"""

if not os.path.isfile(WIFI_CLIENT_CPP):
    print("==> WiFiClient.cpp not found, skip write pump unpatch: %s" % WIFI_CLIENT_CPP)
else:
    content = open(WIFI_CLIENT_CPP, "r", encoding="utf-8").read()
    changed = False
    if PATCHED_TAIL in content:
        content = content.replace(PATCHED_TAIL, ORIG_TAIL, 1)
        changed = True
    if DECL in content:
        content = content.replace(DECL, "", 1)
        changed = True
    if changed:
        with open(WIFI_CLIENT_CPP, "w", encoding="utf-8") as f:
            f.write(content)
        print("==> Reverted WiFiClient.cpp write pump hooks")
    elif MARKER in content or "deskbot_ws_transport_write_pump" in content:
        print("==> WiFiClient.cpp: partial pump hooks remain, manual check")
    else:
        print("==> WiFiClient.cpp: no write pump hooks")
