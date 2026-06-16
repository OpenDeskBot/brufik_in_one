Import("env")
import os
import subprocess
import sys

PROJECT_DIR = env["PROJECT_DIR"]
BOOT_PROJ = os.path.join(PROJECT_DIR, "bootloader_proj")
BOOT_BIN = os.path.join(BOOT_PROJ, ".pio", "build", "deskbot_bootloader", "bootloader.bin")

print("==> Building custom bootloader in bootloader_proj/")
subprocess.check_call(
    [env.subst("$PYTHONEXE"), "-m", "platformio", "run", "-t", "bootloader"],
    cwd=BOOT_PROJ,
)

if not os.path.isfile(BOOT_BIN):
    print("ERROR: missing %s" % BOOT_BIN, file=sys.stderr)
    env.Exit(1)

env.BoardConfig().update("build.arduino.custom_bootloader", BOOT_BIN)
print("==> Using custom bootloader: %s" % BOOT_BIN)
