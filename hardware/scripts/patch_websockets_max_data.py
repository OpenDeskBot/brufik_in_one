"""Allow -DWEBSOCKETS_MAX_DATA_SIZE from platformio.ini to override library default (15KiB)."""
Import("env")
import os

PROJECT_DIR = env["PROJECT_DIR"]
PIOENV = env["PIOENV"]
WS_HEADER = os.path.join(
    PROJECT_DIR, ".pio", "libdeps", PIOENV, "WebSockets", "src", "WebSockets.h",
)

MARKER = "#ifndef WEBSOCKETS_MAX_DATA_SIZE\n#define WEBSOCKETS_MAX_DATA_SIZE (15 * 1024)\n#endif"
TARGET = "#define WEBSOCKETS_MAX_DATA_SIZE (15 * 1024)"

if not os.path.isfile(WS_HEADER):
    print("==> WebSockets.h not found, skip patch: %s" % WS_HEADER)
else:
    with open(WS_HEADER, "r", encoding="utf-8") as f:
        content = f.read()
    if MARKER in content:
        print("==> WebSockets.h already patched")
    elif TARGET not in content:
        print("==> WebSockets.h: expected define not found, skip patch")
    else:
        content = content.replace(TARGET, MARKER)
        with open(WS_HEADER, "w", encoding="utf-8") as f:
            f.write(content)
        print("==> Patched WebSockets.h: WEBSOCKETS_MAX_DATA_SIZE respects build_flags")
