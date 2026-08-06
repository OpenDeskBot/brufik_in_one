"""Revert deskbot write-pump hooks from NetworkClient.cpp / WiFiClient.cpp。"""
Import("env")
import os

FRAMEWORK_DIR = env.PioPlatform().get_package_dir("framework-arduinoespressif32")
CANDIDATES = [
    os.path.join(FRAMEWORK_DIR, "libraries", "Network", "src", "NetworkClient.cpp"),
    os.path.join(FRAMEWORK_DIR, "libraries", "WiFi", "src", "WiFiClient.cpp"),
]

MARKER = "/* deskbot: write wait pump */"
DECL = (
    '\nextern "C" void deskbot_ws_transport_write_pump(void); '
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

target = next((p for p in CANDIDATES if os.path.isfile(p)), None)
if not target:
    print("==> NetworkClient/WiFiClient.cpp not found, skip write pump unpatch")
else:
    content = open(target, "r", encoding="utf-8").read()
    changed = False
    if PATCHED_TAIL in content:
        content = content.replace(PATCHED_TAIL, ORIG_TAIL, 1)
        changed = True
    if DECL in content:
        content = content.replace(DECL, "", 1)
        changed = True
    if changed:
        open(target, "w", encoding="utf-8").write(content)
        print("==> Reverted %s write pump hooks" % os.path.basename(target))
    elif MARKER in content or "deskbot_ws_transport_write_pump" in content:
        print("==> %s: partial pump hooks remain, manual check" % os.path.basename(target))
    else:
        print("==> %s: no write pump hooks" % os.path.basename(target))
