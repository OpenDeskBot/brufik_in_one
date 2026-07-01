Import("env")
import os
import subprocess
import sys

PROJECT_DIR = env["PROJECT_DIR"]
ESP_SR = os.path.join(PROJECT_DIR, "components", "esp-sr")
TAG = "v2.0.0"
REPO = "https://github.com/espressif/esp-sr.git"

if not os.path.isdir(ESP_SR):
    print("==> Fetching esp-sr %s into components/" % TAG)
    os.makedirs(os.path.join(PROJECT_DIR, "components"), exist_ok=True)
    subprocess.check_call(
        ["git", "clone", "--depth", "1", "--branch", TAG, REPO, ESP_SR],
        cwd=PROJECT_DIR,
    )
    print("==> esp-sr ready")
