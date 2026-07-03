"""为 WebSockets.h 添加 WEBSOCKETS_TCP_WRITE_TIMEOUT 回退定义。"""
Import("env")
import os

PROJECT_DIR = env["PROJECT_DIR"]
PIOENV = env["PIOENV"]
WS_HEADER = os.path.join(
    PROJECT_DIR, ".pio", "libdeps", PIOENV, "WebSockets", "src", "WebSockets.h",
)

INSERT = (
    "\n#ifndef WEBSOCKETS_TCP_WRITE_TIMEOUT\n"
    "#define WEBSOCKETS_TCP_WRITE_TIMEOUT WEBSOCKETS_TCP_TIMEOUT\n"
    "#endif\n"
)

if not os.path.isfile(WS_HEADER):
    print("==> WebSockets.h not found, skip write timeout define patch")
else:
    with open(WS_HEADER, "r", encoding="utf-8") as f:
        content = f.read()
    if "WEBSOCKETS_TCP_WRITE_TIMEOUT" in content:
        print("==> WebSockets.h write timeout define already present")
    elif "#define WEBSOCKETS_TCP_TIMEOUT" not in content:
        print("==> WebSockets.h: WEBSOCKETS_TCP_TIMEOUT not found, skip")
    else:
        marker = "#endif\n\nnamespace websockets"
        if marker in content:
            content = content.replace(
                marker,
                "#endif" + INSERT + "\nnamespace websockets",
                1,
            )
        else:
            content = content.replace(
                "#define WEBSOCKETS_TCP_TIMEOUT (5000)",
                "#define WEBSOCKETS_TCP_TIMEOUT (5000)" + INSERT,
                1,
            )
        with open(WS_HEADER, "w", encoding="utf-8") as f:
            f.write(content)
        print("==> Patched WebSockets.h: WEBSOCKETS_TCP_WRITE_TIMEOUT fallback")
