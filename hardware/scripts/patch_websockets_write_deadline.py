"""WebSockets write() 使用绝对截止时间，避免 TCP 慢写时滑动重置导致单次 send 阻塞数十秒。"""
Import("env")
import os

PROJECT_DIR = env["PROJECT_DIR"]
PIOENV = env["PIOENV"]
WS_CPP = os.path.join(
    PROJECT_DIR, ".pio", "libdeps", PIOENV, "WebSockets", "src", "WebSockets.cpp",
)

MARKER = "/* deskbot: absolute write deadline */"
OLD = """    unsigned long t = millis();
    size_t len      = 0;
    size_t total    = 0;
    DEBUG_WEBSOCKETS("[write] n: %zu t: %lu\\n", n, t);
    while(n > 0) {
        if(client->tcp == NULL) {
            DEBUG_WEBSOCKETS("[write] tcp is null!\\n");
            break;
        }

        if(!client->tcp->connected()) {
            DEBUG_WEBSOCKETS("[write] not connected!\\n");
            break;
        }

        if((millis() - t) > WEBSOCKETS_TCP_TIMEOUT) {
            DEBUG_WEBSOCKETS("[write] write TIMEOUT! %lu\\n", (millis() - t));
            break;
        }

        len = client->tcp->write((const uint8_t *)out, n);
        if(len) {
            t = millis();"""

NEW = """    unsigned long deadline = millis() + WEBSOCKETS_TCP_TIMEOUT; /* deskbot: absolute write deadline */
    size_t len      = 0;
    size_t total    = 0;
    DEBUG_WEBSOCKETS("[write] n: %zu deadline: %lu\\n", n, deadline);
    while(n > 0) {
        if(client->tcp == NULL) {
            DEBUG_WEBSOCKETS("[write] tcp is null!\\n");
            break;
        }

        if(!client->tcp->connected()) {
            DEBUG_WEBSOCKETS("[write] not connected!\\n");
            break;
        }

        if((long)(millis() - deadline) >= 0) {
            DEBUG_WEBSOCKETS("[write] write TIMEOUT! elapsed=%lu\\n", WEBSOCKETS_TCP_TIMEOUT);
            break;
        }

        len = client->tcp->write((const uint8_t *)out, n);
        if(len) {"""

if not os.path.isfile(WS_CPP):
    print("==> WebSockets.cpp not found, skip patch: %s" % WS_CPP)
elif MARKER in open(WS_CPP, "r", encoding="utf-8").read():
    print("==> WebSockets.cpp write deadline already patched")
elif OLD not in open(WS_CPP, "r", encoding="utf-8").read():
    print("==> WebSockets.cpp: expected write() block not found, skip patch")
else:
    with open(WS_CPP, "r", encoding="utf-8") as f:
        content = f.read()
    content = content.replace(OLD, NEW)
    with open(WS_CPP, "w", encoding="utf-8") as f:
        f.write(content)
    print("==> Patched WebSockets.cpp: write() uses absolute deadline")
