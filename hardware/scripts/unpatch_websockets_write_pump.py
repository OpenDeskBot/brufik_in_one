"""Revert deskbot write-pump hooks from WebSockets.cpp (loop 改由 asr_ws 负责)。"""
Import("env")
import os

PROJECT_DIR = env["PROJECT_DIR"]
PIOENV = env["PIOENV"]
WS_CPP = os.path.join(
    PROJECT_DIR, ".pio", "libdeps", PIOENV, "WebSockets", "src", "WebSockets.cpp",
)

MARKER = "/* deskbot: write pump hook */"
DECL = (
    "\nextern \"C\" void deskbot_ws_transport_write_pump(void); "
    "/* deskbot: write pump hook */\n"
)
WRITE_PATCHED_A = """        } else {
            DEBUG_WEBSOCKETS("WS write %d failed left %d!\\n", len, n);
            deskbot_ws_transport_write_pump();
        }
        if(n > 0) {
            deskbot_ws_transport_write_pump();
            WEBSOCKETS_YIELD();
        }"""
WRITE_ORIG_A = """        } else {
            DEBUG_WEBSOCKETS("WS write %d failed left %d!\\n", len, n);
        }
        if(n > 0) {
            WEBSOCKETS_YIELD();
        }"""
WRITE_PATCHED_B = """        } else {
            DEBUG_WEBSOCKETS("WS write %d failed left %d!\\n", len, n);
            deskbot_ws_transport_write_pump();
        }
        if(n > 0) {
            deskbot_ws_transport_write_pump();
            WEBSOCKETS_YIELD();
        }"""

if not os.path.isfile(WS_CPP):
    print("==> WebSockets.cpp not found, skip write pump unpatch: %s" % WS_CPP)
else:
    content = open(WS_CPP, "r", encoding="utf-8").read()
    changed = False
    if WRITE_PATCHED_A in content:
        content = content.replace(WRITE_PATCHED_A, WRITE_ORIG_A, 1)
        changed = True
    elif WRITE_PATCHED_B in content:
        content = content.replace(WRITE_PATCHED_B, WRITE_ORIG_A, 1)
        changed = True
    if DECL in content:
        content = content.replace(DECL, "", 1)
        changed = True
    if "deskbot_ws_transport_write_pump();" in content:
        content = content.replace("            deskbot_ws_transport_write_pump();\n", "")
        changed = True
    if changed:
        with open(WS_CPP, "w", encoding="utf-8") as f:
            f.write(content)
        print("==> Reverted WebSockets.cpp write pump hooks")
    elif MARKER in content or "deskbot_ws_transport_write_pump" in content:
        print("==> WebSockets.cpp: partial pump hooks remain, manual check")
    else:
        print("==> WebSockets.cpp: no write pump hooks")
