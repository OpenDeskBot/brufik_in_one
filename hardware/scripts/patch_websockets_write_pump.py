"""WebSockets::write() 等待 TCP 可写时 pump WS RX，否则 lwIP 收不到 ACK，sendBIN 失败。"""
Import("env")
import os

PROJECT_DIR = env["PROJECT_DIR"]
PIOENV = env["PIOENV"]
WS_CPP = os.path.join(
    PROJECT_DIR, ".pio", "libdeps", PIOENV, "WebSockets", "src", "WebSockets.cpp",
)

MARKER = "/* deskbot: write pump hook */"
DECL = (
    "\nextern \"C\" void deskbot_ws_uplink_write_pump(void); "
    "/* deskbot: write pump hook */\n"
)
READCB_OLD = """        if(n > 0) {
            deskbot_ws_uplink_write_pump();
            WEBSOCKETS_YIELD();
        }
    }
    if(cb) {
        cb(client, true);
    }"""
READCB_NEW = """        if(n > 0) {
            WEBSOCKETS_YIELD();
        }
    }
    if(cb) {
        cb(client, true);
    }"""
WRITE_OLD_A = """        } else {
            DEBUG_WEBSOCKETS("WS write %d failed left %d!\\n", len, n);
        }
        if(n > 0) {
            WEBSOCKETS_YIELD();
        }"""
WRITE_NEW_A = """        } else {
            DEBUG_WEBSOCKETS("WS write %d failed left %d!\\n", len, n);
            deskbot_ws_uplink_write_pump();
        }
        if(n > 0) {
            deskbot_ws_uplink_write_pump();
            WEBSOCKETS_YIELD();
        }"""
WRITE_OLD_B = """        } else {
            DEBUG_WEBSOCKETS("WS write %d failed left %d!\\n", len, n);
        }
        if(n > 0) {
            deskbot_ws_uplink_write_pump();
            WEBSOCKETS_YIELD();
        }"""
WRITE_NEW_B = """        } else {
            DEBUG_WEBSOCKETS("WS write %d failed left %d!\\n", len, n);
            deskbot_ws_uplink_write_pump();
        }
        if(n > 0) {
            deskbot_ws_uplink_write_pump();
            WEBSOCKETS_YIELD();
        }"""

if not os.path.isfile(WS_CPP):
    print("==> WebSockets.cpp not found, skip write pump patch: %s" % WS_CPP)
else:
    content = open(WS_CPP, "r", encoding="utf-8").read()
    changed = False
    if READCB_OLD in content:
        content = content.replace(READCB_OLD, READCB_NEW, 1)
        changed = True
        print("==> WebSockets.cpp: reverted mistaken readCb pump patch")
    if WRITE_OLD_A in content:
        if MARKER not in content:
            anchor = "size_t WebSockets::write(WSclient_t * client, uint8_t * out, size_t n) {"
            idx = content.find(anchor)
            if idx >= 0:
                content = content[:idx] + DECL + content[idx:]
        content = content.replace(WRITE_OLD_A, WRITE_NEW_A, 1)
        changed = True
        print("==> Patched WebSockets.cpp: write pump in write()")
    elif WRITE_OLD_B in content:
        content = content.replace(WRITE_OLD_B, WRITE_NEW_B, 1)
        changed = True
        print("==> Patched WebSockets.cpp: write pump on zero-len too")
    elif "deskbot_ws_uplink_write_pump();" in content and MARKER in content:
        print("==> WebSockets.cpp write pump already patched")
    elif not changed:
        print("==> WebSockets.cpp: expected write() block not found, skip pump patch")
    if changed:
        with open(WS_CPP, "w", encoding="utf-8") as f:
            f.write(content)
