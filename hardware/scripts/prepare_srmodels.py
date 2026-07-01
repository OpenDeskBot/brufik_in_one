Import("env")
import csv
import math
import os
import shutil
import subprocess
import sys

PROJECT_DIR = env["PROJECT_DIR"]
PIOENV = env["PIOENV"]
SDKCONFIG_DEFAULTS = os.path.join(PROJECT_DIR, "firmware", "sdkconfig.defaults")
SRMODELS_OUT = os.path.join(PROJECT_DIR, "srmodels.bin")


def find_esp_sr_root():
    component = os.path.join(PROJECT_DIR, "components", "esp-sr")
    if os.path.isdir(component):
        return component
    libdeps = os.path.join(PROJECT_DIR, ".pio", "libdeps", PIOENV)
    if not os.path.isdir(libdeps):
        return None
    for name in sorted(os.listdir(libdeps)):
        if "esp-sr" in name.lower():
            return os.path.join(libdeps, name)
    return None


def find_model_partition_kb(csv_path):
    with open(csv_path, newline="") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#"):
                continue
            if row[0].strip() == "model":
                size = row[4].strip().upper()
                if size.endswith("K"):
                    return int(size[:-1])
                if size.endswith("M"):
                    return int(size[:-1]) * 1024
    return 0


def generate_srmodels(source, target, env):
    esp_sr = find_esp_sr_root()
    if not esp_sr:
        print("[srmodels] esp-sr lib not resolved yet; skip generation")
        return

    movemodel = os.path.join(esp_sr, "model", "movemodel.py")
    if not os.path.isfile(movemodel):
        print("[srmodels] missing movemodel.py in %s" % esp_sr)
        env.Exit(1)

    build_dir = os.path.join(PROJECT_DIR, ".pio", "build", PIOENV)
    os.makedirs(build_dir, exist_ok=True)

    cmd = [
        env.subst("$PYTHONEXE"),
        movemodel,
        "-d1",
        SDKCONFIG_DEFAULTS,
        "-d2",
        esp_sr,
        "-d3",
        build_dir,
    ]
    print("[srmodels] generating:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=PROJECT_DIR)

    generated = os.path.join(build_dir, "srmodels", "srmodels.bin")
    if not os.path.isfile(generated):
        print("[srmodels] expected output missing:", generated)
        env.Exit(1)

    shutil.copy2(generated, SRMODELS_OUT)
    size_kb = math.ceil(os.path.getsize(SRMODELS_OUT) / 1024.0)
    part_rel = env.BoardConfig().get("build.partitions", "partitions.csv")
    part_csv = part_rel if os.path.isabs(part_rel) else os.path.join(PROJECT_DIR, part_rel)
    part_kb = find_model_partition_kb(part_csv)
    print("[srmodels] wrote %s (%u KB); model partition=%u KB" % (SRMODELS_OUT, size_kb, part_kb))
    if part_kb and size_kb > part_kb:
        print("[srmodels] ERROR: srmodels.bin larger than model partition")
        env.Exit(1)


env.AddPreAction("buildprog", generate_srmodels)
