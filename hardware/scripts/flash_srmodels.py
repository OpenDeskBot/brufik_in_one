Import("env")
import csv
import os
import subprocess


def find_partition_offset(csv_path, name_candidates=("model", "srmodels", "esp_sr")):
    with open(csv_path, newline="") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#"):
                continue
            name = row[0].strip()
            if name in name_candidates:
                off = row[3].strip()
                if not off:
                    raise RuntimeError("model partition offset missing in %s" % csv_path)
                return int(off, 0)
    raise RuntimeError("model partition not found in %s" % csv_path)


def after_upload(source, target, env):
    proj = env["PROJECT_DIR"]
    srmodels = os.path.join(proj, "srmodels.bin")
    if not os.path.isfile(srmodels):
        print("[srmodels] skip flash: %s not found (build should generate it)" % srmodels)
        return

    csv_path = env.BoardConfig().get("build.partitions", "partitions.csv")
    if not os.path.isabs(csv_path):
        csv_path = os.path.join(proj, csv_path)
    offset = find_partition_offset(csv_path)
    chip = env.BoardConfig().get("build.mcu", "esp32s3")
    port = env.subst("$UPLOAD_PORT")
    speed = env.subst("$UPLOAD_SPEED")
    esptool = os.path.join(env.PioPlatform().get_package_dir("tool-esptoolpy"), "esptool.py")
    cmd = [
        env.subst("$PYTHONEXE"),
        esptool,
        "--chip",
        chip,
        "--port",
        port,
        "--baud",
        speed,
        "write_flash",
        hex(offset),
        srmodels,
    ]
    print("[srmodels] flashing:", " ".join(cmd))
    ret = subprocess.call(cmd)
    if ret != 0:
        raise RuntimeError("esptool write_flash srmodels.bin failed rc=%d" % ret)


env.AddPostAction("upload", after_upload)
